"""
Mock tests for app/twse_client.py.

只測「資料解析邏輯」，不打真實 TWSE API：
  1. 千分位逗號是否被正確 strip
  2. 民國年日期是否被正確轉換為西元年
  3. 缺值列（停牌日）是否被正確丟棄
  4. fetch_stock_list 能正確解析 TWSE ISIN HTML（mock HTTP）

網路 retry / 503 錯誤依賴 TWSE 服務本身，不列入測試範圍。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.twse_client import _parse_monthly_data, _roc_to_ad_date, fetch_stock_list


# ---------------------------------------------------------------------------
# _roc_to_ad_date
# ---------------------------------------------------------------------------

class TestRocToAdDate:
    def test_converts_correctly(self):
        """民國年 113 → 西元年 2024。"""
        assert _roc_to_ad_date("113/01/02") == "2024/01/02"

    def test_early_roc_year(self):
        """民國年 90 → 西元年 2001。"""
        assert _roc_to_ad_date("90/06/15") == "2001/06/15"


# ---------------------------------------------------------------------------
# _parse_monthly_data
# ---------------------------------------------------------------------------

class TestParseMonthlyData:
    def _sample_raw(self) -> list[list]:
        """模擬 TWSE 新版 API 回傳的 data array（list of lists）。

        欄位順序：[日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, ...]
        index:      0     1         2         3       4       5       6
        """
        return [
            ["113/01/02", "12,345,678", "6,987,654,321", "560.00", "570.00", "558.00", "568.00", "+8.00", "12345", ""],
            ["113/01/03", "9,876,543",  "1,234,567,890", "560.00", "580.00", "555.00", "1,234.50", "+0.50", "9876", ""],
        ]

    def test_comma_stripped_from_closing_price(self):
        """收盤價的千分位逗號應被正確移除並轉為 float。"""
        df = _parse_monthly_data(self._sample_raw())
        assert df.loc[1, "close"] == pytest.approx(1234.50)

    def test_comma_stripped_from_volume(self):
        """成交量的千分位逗號應被正確移除並轉為 float。"""
        df = _parse_monthly_data(self._sample_raw())
        assert df.loc[0, "volume"] == pytest.approx(12_345_678.0)

    def test_date_converted_to_datetime(self):
        """日期應轉換為 datetime64 型別。"""
        df = _parse_monthly_data(self._sample_raw())
        assert pd.api.types.is_datetime64_any_dtype(df["date"])
        assert df.loc[0, "date"] == pd.Timestamp("2024-01-02")

    def test_invalid_closing_price_row_is_dropped(self):
        """收盤價為 '--'（停牌日）的列應被丟棄。"""
        raw = self._sample_raw()
        raw.append(["113/01/04", "0", "0", "--", "--", "--", "--", "0", "0", ""])
        df = _parse_monthly_data(raw)
        assert len(df) == 2            # 只保留前兩筆有效資料

    def test_empty_input_returns_empty_dataframe(self):
        """空輸入應回傳空 DataFrame 而非拋例外。"""
        df = _parse_monthly_data([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


# ---------------------------------------------------------------------------
# fetch_stock_list
# ---------------------------------------------------------------------------

# 模擬 TWSE ISIN 頁面的 HTML 片段（big5 encode → bytes）
_FAKE_ISIN_HTML = """
<html><body><table>
<tr><th>有價證券代號及名稱</th></tr>
<tr><td>股票</td></tr>
<tr><td>2330　台積電</td><td>TW0002330008</td></tr>
<tr><td>2317　鴻海</td><td>TW0002317005</td></tr>
<tr><td>ABC　非數字代號</td><td>INVALID</td></tr>
<tr><td></td></tr>
</table></body></html>
""".encode("big5", errors="ignore")


def _make_mock_client(content: bytes):
    """建立一個模擬 httpx.AsyncClient，get() 回傳指定內容。"""
    mock_resp = MagicMock()
    mock_resp.content = content

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return mock_client


@pytest.mark.asyncio
class TestFetchStockList:
    async def test_returns_only_numeric_codes(self):
        """只回傳代號為純數字的股票，非數字代號應被過濾掉。"""
        with patch("app.twse_client.httpx.AsyncClient", return_value=_make_mock_client(_FAKE_ISIN_HTML)):
            result = await fetch_stock_list()
        codes = [code for code, _ in result]
        assert "2330" in codes
        assert "2317" in codes
        assert "ABC" not in codes

    async def test_returns_correct_names(self):
        """股票名稱應正確解析（全形空白分隔後的第二欄）。"""
        with patch("app.twse_client.httpx.AsyncClient", return_value=_make_mock_client(_FAKE_ISIN_HTML)):
            result = await fetch_stock_list()
        result_dict = dict(result)
        assert result_dict["2330"] == "台積電"
        assert result_dict["2317"] == "鴻海"

    async def test_returns_list_of_tuples(self):
        """回傳型別應為 list of (str, str) tuples。"""
        with patch("app.twse_client.httpx.AsyncClient", return_value=_make_mock_client(_FAKE_ISIN_HTML)):
            result = await fetch_stock_list()
        assert isinstance(result, list)
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)

    async def test_empty_html_returns_empty_list(self):
        """HTML 無有效資料列時，應回傳空 list 而非拋例外。"""
        empty_html = "<html><body><table><tr><th>header</th></tr></table></body></html>".encode("big5")
        with patch("app.twse_client.httpx.AsyncClient", return_value=_make_mock_client(empty_html)):
            result = await fetch_stock_list()
        assert result == []
