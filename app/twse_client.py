"""
TWSE client — fetches daily trading data for a given stock.

流程：
  1. 計算當月 + 前兩個月共三個查詢日期
  2. 對每個月各打一次 TWSE API（非同步並發）
  3. 合併、清洗資料，取最近 60 筆交易日
  4. 回傳 pandas DataFrame，欄位皆已轉為正確型別

TWSE endpoint:
  GET https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY
      ?stockNo={stock_no}&date={YYYYMMDD}&response=json
  回傳該月所有交易資料，格式為 {"stat": "OK", "fields": [...], "data": [[...], ...]}。

注意：原本計畫使用 openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY，
但該 endpoint 已被 TWSE 下架（回傳 302 → 404），改用此新端點。
"""

import asyncio
import logging
import re
from datetime import date, timedelta

import httpx
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"

# 最多抓三個月份、取最近這幾筆
MONTHS_TO_FETCH = 3
MAX_ROWS = 60
MIN_ROWS = 20  # 低於此筆數無法計算完整指標

# Retry 設定：最多重試 3 次，初始等待 1 秒，每次翻倍（1s → 2s → 4s）
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class TWSEError(Exception):
    """TWSE API 相關錯誤的基底類別。"""


class StockNotFoundError(TWSEError):
    """查無此股票代號（API 回傳空資料）。"""


class InsufficientDataError(TWSEError):
    """資料筆數不足以計算完整指標（< MIN_ROWS 筆）。"""


class TWSEUnavailableError(TWSEError):
    """TWSE API 無法存取（重試三次後仍失敗）。"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _months_to_query(reference_date: date) -> list[date]:
    """回傳要查詢的月份起始日列表（當月 + 前兩個月）。

    TWSE API 以「date 所在月份」決定回傳哪個月的資料，
    所以只需要傳各月的任意一天，這裡統一傳當月 1 號。

    Args:
        reference_date: 基準日期，通常是 date.today()

    Returns:
        由新到舊排列的 date list，長度為 MONTHS_TO_FETCH。
    """
    result = []
    current = reference_date.replace(day=1)
    for _ in range(MONTHS_TO_FETCH):
        result.append(current)
        # 往前推一個月：先退一天到上個月最後一天，再取 1 號
        current = (current - timedelta(days=1)).replace(day=1)
    return result


def _parse_monthly_data(raw: list[list]) -> pd.DataFrame:
    """將 TWSE API 回傳的單月原始資料解析為 DataFrame。

    新版 API 回傳 list of lists，欄位順序固定：
      index 0: 日期（民國年）, 1: 成交股數, 6: 收盤價

    TWSE 的數字欄位含千分位逗號（例如 "1,234.56"），需 strip 後才能轉 float。

    Args:
        raw: TWSE API 回傳的 data array（list of list）

    Returns:
        欄位為 [date, close, volume] 的 DataFrame；
        date 為 datetime，close/volume 為 float。
        若 raw 為空，回傳空 DataFrame。
    """
    if not raw:
        return pd.DataFrame()

    # 欄位索引：0=日期, 1=成交股數, 6=收盤價
    DATE_IDX, VOLUME_IDX, CLOSE_IDX = 0, 1, 6

    df = pd.DataFrame(raw)
    df = df[[DATE_IDX, VOLUME_IDX, CLOSE_IDX]].copy()
    df.columns = ["date", "volume", "close"]

    # 民國年 → 西元年，格式 "113/01/02" → datetime
    df["date"] = pd.to_datetime(
        df["date"].apply(_roc_to_ad_date),
        format="%Y/%m/%d",
    )

    # 移除千分位逗號後轉 float；無效值（例如 "--"）設為 NaN
    for col in ["close", "volume"]:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(",", "", regex=False)
            .pipe(pd.to_numeric, errors="coerce")
        )

    # 丟掉收盤價或成交量缺值的列（停牌日等特殊情況）
    df = df.dropna(subset=["close", "volume"])

    return df.reset_index(drop=True)


def _roc_to_ad_date(roc_date: str) -> str:
    """將民國年日期字串轉換為西元年格式。

    Args:
        roc_date: 格式 "113/01/02"（民國年/月/日）

    Returns:
        格式 "2024/01/02"（西元年/月/日）
    """
    parts = roc_date.split("/")
    ad_year = int(parts[0]) + 1911
    return f"{ad_year}/{parts[1]}/{parts[2]}"


async def _fetch_month(
    client: httpx.AsyncClient,
    stock_no: str,
    query_date: date,
) -> list[list]:
    """對單一月份打一次 TWSE API，含指數退避重試。

    Args:
        client: 共用的 httpx AsyncClient
        stock_no: 股票代號，例如 "2330"
        query_date: 查詢日期（TWSE 以此日期所在月份回傳資料）

    Returns:
        TWSE 回傳的 data array（list of list）；查無資料時回傳空 list。

    Raises:
        TWSEUnavailableError: 重試三次後仍無法連線或非 200 回應。
    """
    params = {
        "stockNo": stock_no,
        "date": query_date.strftime("%Y%m%d"),
        "response": "json",
    }

    for attempt in range(MAX_RETRIES):
        try:
            response = await client.get(TWSE_URL, params=params, timeout=10.0)
            response.raise_for_status()
            data = response.json()
            # stat != "OK" 代表查無資料（股票代號不存在或該月無資料）
            if data.get("stat") == "OK":
                return data.get("data", [])
            return []

        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            if attempt == MAX_RETRIES - 1:
                raise TWSEUnavailableError(
                    f"TWSE API unavailable after {MAX_RETRIES} attempts: {exc}"
                ) from exc

            delay = RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "TWSE API error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1, MAX_RETRIES, delay, exc,
            )
            await asyncio.sleep(delay)

    return []  # unreachable, but satisfies type checker


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def fetch_stock_data(stock_no: str) -> pd.DataFrame:
    """從 TWSE 取得指定股票最近 60 筆交易日資料。

    並發抓取當月與前兩個月資料，合併後取最新 MAX_ROWS 筆，
    確保資料量達到 MIN_ROWS 才回傳。

    Args:
        stock_no: 股票代號，例如 "2330"

    Returns:
        DataFrame，欄位：
            - date (datetime64): 交易日期
            - close (float): 收盤價
            - volume (float): 成交股數
        由舊到新排序，最多 MAX_ROWS 筆。

    Raises:
        StockNotFoundError: 三個月份均查無資料（代號不存在）。
        InsufficientDataError: 合併後資料筆數 < MIN_ROWS。
        TWSEUnavailableError: TWSE API 連線失敗。
    """
    query_dates = _months_to_query(date.today())

    async with httpx.AsyncClient() as client:
        # 三個月份並發請求，加速整體查詢時間
        tasks = [
            _fetch_month(client, stock_no, d)
            for d in query_dates
        ]
        monthly_results = await asyncio.gather(*tasks)

    # 合併三個月的原始資料，解析並去重
    all_raw: list[list] = []
    for raw in monthly_results:
        all_raw.extend(raw)

    if not all_raw:
        raise StockNotFoundError(f"No data found for stock '{stock_no}'")

    df = _parse_monthly_data(all_raw)

    # 去除重複日期（不同月份查詢可能有重疊），保留最新的一筆
    df = df.drop_duplicates(subset=["date"]).sort_values("date")

    # 取最近 MAX_ROWS 筆
    df = df.tail(MAX_ROWS).reset_index(drop=True)

    if len(df) < MIN_ROWS:
        raise InsufficientDataError(
            f"Only {len(df)} trading days available for '{stock_no}' "
            f"(minimum {MIN_ROWS} required)"
        )

    logger.info(
        "Fetched %d trading days for %s (%s to %s)",
        len(df),
        stock_no,
        df["date"].iloc[0].strftime("%Y-%m-%d"),
        df["date"].iloc[-1].strftime("%Y-%m-%d"),
    )

    return df


def fetch_stock_list() -> list[tuple[str, str]]:
    """從 TWSE ISIN 頁面抓取所有上市股票清單。

    Returns:
        [(stock_code, stock_name), ...] 只含純數字代號的股票。
    """
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    resp = httpx.get(url, timeout=10.0)
    html = resp.content.decode("big5", errors="ignore")

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    result = []
    for row in rows[2:]:  # 跳過 header 和 "股票" 分類列
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)
        if not cells:
            continue
        cell0 = re.sub(r"<[^>]+>", "", cells[0]).strip()
        parts = cell0.split("　")  # 全形空白分隔代號與名稱
        if len(parts) < 2:
            continue
        code, name = parts[0].strip(), parts[1].strip()
        if code.isdigit():
            result.append((code, name))
    return result


async def fetch_stock_name(stock_no: str) -> str:
    """從 TWSE 取得股票中文名稱，查無時回傳空字串。"""
    url = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_AVG"
    params = {"stockNo": stock_no, "response": "json"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, params=params, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            # title 格式："{民國年月} {股票代號} {股票名稱} {說明}"
            # 例："115年05月 2330 台積電 日收盤價及月平均收盤價"
            title: str = data.get("title", "")
            parts = title.split()
            for i, part in enumerate(parts):
                if part == stock_no and i + 1 < len(parts):
                    return parts[i + 1]
            return ""
    except Exception:
        return ""
