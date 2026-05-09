"""
Technical indicator calculation and scoring engine.

接收 twse_client 回傳的 DataFrame，計算五項技術指標並加總評分，
回傳 AnalysisResult dataclass 供 llm.py 和 main.py 使用。

評分總覽（滿分 100）：
    MA Crossover  25 — 領先指標，5日MA 是否站上 20日MA
    Volume Surge  25 — 領先指標，近5日均量是否放大
    Price Trend   20 — 同步指標，近30日漲跌幅
    RSI           20 — 同步指標，動能是否在健康區間
    Stability     10 — 風險指標，價格波動是否穩定
"""

import logging

import pandas as pd

from app.models import AnalysisResult, IndicatorScores

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring thresholds — centralized so they're easy to tune
# ---------------------------------------------------------------------------

# MA Crossover
MA_SHORT = 5
MA_LONG = 20

# Volume Surge: 近5日均量 / 近20日均量
VOLUME_HIGH_RATIO = 1.5   # → 25 分
VOLUME_MID_RATIO = 1.2    # → 15 分

# Price Trend: 近30日漲跌幅
PRICE_HIGH_PCT = 0.05     # >5%  → 20 分
PRICE_MID_PCT = 0.0       # >0%  → 10 分

# RSI 14日
RSI_PERIOD = 14
RSI_IDEAL_LOW = 45
RSI_IDEAL_HIGH = 65
RSI_OUTER_LOW = 35
RSI_OUTER_HIGH = 75

# Stability: 標準差 / 均價（變異係數）
STABILITY_THRESHOLD = 0.05   # < 0.05 → 10 分

# Verdict threshold
VERDICT_THRESHOLD = 60


# ---------------------------------------------------------------------------
# Indicator calculators
# ---------------------------------------------------------------------------

def _calc_ma_crossover(closes: pd.Series) -> tuple[float, float, int, list[float], list[float]]:
    """計算 5日MA 與 20日MA 完整序列，並判斷最新一日是否黃金交叉。

    Args:
        closes: 收盤價序列（由舊到新）

    Returns:
        (ma5, ma20, score, ma5_series, ma20_series)
        score: 25 if ma5 > ma20 else 0
        ma5_series/ma20_series: 完整 MA 序列，窗口不足的位置為 None
    """
    ma5_full = closes.rolling(MA_SHORT).mean()
    ma20_full = closes.rolling(MA_LONG).mean()
    ma5 = float(ma5_full.iloc[-1])
    ma20 = float(ma20_full.iloc[-1])
    score = 25 if ma5 > ma20 else 0
    ma5_series = [round(v, 2) if not pd.isna(v) else None for v in ma5_full]
    ma20_series = [round(v, 2) if not pd.isna(v) else None for v in ma20_full]
    return ma5, ma20, score, ma5_series, ma20_series


def _calc_volume_surge(volumes: pd.Series) -> tuple[float, int]:
    """計算近5日均量相對近20日均量的放大倍數。

    Args:
        volumes: 成交量序列（由舊到新）

    Returns:
        (ratio, score)
        ratio: 近5日均量 / 近20日均量，四捨五入到小數後兩位
        score: 25 / 15 / 0
    """
    avg_5 = volumes.tail(5).mean()
    avg_20 = volumes.tail(20).mean()

    # 避免除以零（理論上不會發生，但防禦性處理）
    if avg_20 == 0:
        return 0.0, 0

    ratio = avg_5 / avg_20

    if ratio >= VOLUME_HIGH_RATIO:
        score = 25
    elif ratio >= VOLUME_MID_RATIO:
        score = 15
    else:
        score = 0

    return round(float(ratio), 2), score


def _calc_price_trend(closes: pd.Series) -> tuple[float, int]:
    """計算近30日漲跌幅（以最舊一筆為基準）。

    Args:
        closes: 收盤價序列（由舊到新），需至少 30 筆

    Returns:
        (pct, score)
        pct: 漲跌幅，例如 0.08 代表 +8%，四捨五入到小數後四位
        score: 20 / 10 / 0
    """
    window = closes.tail(30)
    pct = (window.iloc[-1] - window.iloc[0]) / window.iloc[0]

    if pct > PRICE_HIGH_PCT:
        score = 20
    elif pct > PRICE_MID_PCT:
        score = 10
    else:
        score = 0

    return round(float(pct), 4), score


def _calc_rsi(closes: pd.Series, period: int = RSI_PERIOD) -> tuple[float, int]:
    """計算 RSI（相對強弱指數），使用 Wilder's EWM 平滑法。

    標準 RSI 計算步驟：
      1. 計算每日價格變動 delta
      2. 分離漲幅（gain）與跌幅（loss）
      3. 用指數加權移動平均（EWM, alpha=1/period）取得平均漲跌幅
      4. RS = avg_gain / avg_loss，RSI = 100 - 100 / (1 + RS)

    Args:
        closes: 收盤價序列（由舊到新）
        period: RSI 計算週期，預設 14 日

    Returns:
        (rsi, score)
        rsi: RSI 數值（0–100），四捨五入到小數後兩位
        score: 20 / 10 / 0
    """
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    # EWM with adjust=False 對應 Wilder's smoothing（等同 alpha = 1/period）
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]

    if avg_loss == 0:
        rsi = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

    if RSI_IDEAL_LOW <= rsi <= RSI_IDEAL_HIGH:
        score = 20
    elif RSI_OUTER_LOW <= rsi <= RSI_OUTER_HIGH:
        score = 10
    else:
        score = 0

    return round(float(rsi), 2), score


def _calc_stability(closes: pd.Series) -> tuple[float, int]:
    """計算收盤價的變異係數（CV = 標準差 / 均價）來衡量價格穩定性。

    CV 越小代表波動越小、價格越穩定。

    Args:
        closes: 收盤價序列（由舊到新）

    Returns:
        (cv, score)
        cv: 變異係數，四捨五入到小數後四位
        score: 10 if cv < STABILITY_THRESHOLD else 0
    """
    mean_price = closes.mean()
    if mean_price == 0:
        return 0.0, 0

    cv = closes.std() / mean_price
    score = 10 if cv < STABILITY_THRESHOLD else 0

    return round(float(cv), 4), score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(df: pd.DataFrame, stock_no: str) -> AnalysisResult:
    """對 TWSE DataFrame 執行完整技術分析，回傳結構化結果。

    Args:
        df: twse_client.fetch_stock_data() 回傳的 DataFrame，
            欄位：date (datetime), close (float), volume (float)，由舊到新排序。
        stock_no: 股票代號，用於填入回傳結果（不影響計算邏輯）。

    Returns:
        AnalysisResult，包含各指標數值、各項得分、總分與結論。
    """
    closes = df["close"]
    volumes = df["volume"]

    # --- 計算各指標 ---
    ma5, ma20, ma_score, ma5_series, ma20_series = _calc_ma_crossover(closes)
    vol_ratio, vol_score = _calc_volume_surge(volumes)
    price_pct, price_score = _calc_price_trend(closes)
    rsi, rsi_score = _calc_rsi(closes)
    cv, stability_score = _calc_stability(closes)

    total_score = ma_score + vol_score + price_score + rsi_score + stability_score
    verdict = "值得關注" if total_score >= VERDICT_THRESHOLD else "暫不關注"

    logger.info(
        "Analysis for %s: score=%d (%s) | MA=%d Vol=%d Price=%d RSI=%d Stab=%d",
        stock_no, total_score, verdict,
        ma_score, vol_score, price_score, rsi_score, stability_score,
    )

    return AnalysisResult(
        stock_no=stock_no,
        start_date=df["date"].iloc[0].strftime("%Y-%m-%d"),
        end_date=df["date"].iloc[-1].strftime("%Y-%m-%d"),
        scores=IndicatorScores(
            ma_crossover_score=ma_score,
            volume_surge_ratio=vol_ratio,
            volume_surge_score=vol_score,
            price_trend_pct=price_pct,
            price_trend_score=price_score,
            rsi=rsi,
            rsi_score=rsi_score,
            stability_cv=cv,
            stability_score=stability_score,
        ),
        total_score=total_score,
        verdict=verdict,
        ma5=round(ma5, 2),
        ma20=round(ma20, 2),
        closes=closes.tolist(),
        volumes=volumes.tolist(),
        dates=[d.strftime("%Y-%m-%d") for d in df["date"]],
        ma5_series=ma5_series,
        ma20_series=ma20_series,
    )
