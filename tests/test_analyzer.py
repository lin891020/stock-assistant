"""
Unit tests for app/analyzer.py.

測試策略：
  - 每個指標函數單獨測試（正常值、邊界值、零值）
  - 最後測 analyze() 整合：確認 dataclass 欄位齊全、verdict 邏輯正確
  - 不 mock 任何東西，analyzer.py 是純 Python/pandas，可完全離線測試
"""

from typing import Optional

import pandas as pd
import pytest

from app.analyzer import (
    _calc_ma_crossover,
    _calc_price_trend,
    _calc_rsi,
    _calc_stability,
    _calc_volume_surge,
    analyze,
)


# ---------------------------------------------------------------------------
# Fixtures — reusable test DataFrames
# ---------------------------------------------------------------------------

def _make_df(closes: list[float], volumes: Optional[list[float]] = None) -> pd.DataFrame:
    """建立測試用 DataFrame，日期從 2024-01-01 起連續遞增。

    Args:
        closes: 收盤價序列
        volumes: 成交量序列；若為 None 則全部填 1_000_000

    Returns:
        欄位為 [date, close, volume] 的 DataFrame，由舊到新排序。
    """
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000.0] * n
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="B"),
        "close": closes,
        "volume": volumes,
    })


# ---------------------------------------------------------------------------
# MA Crossover
# ---------------------------------------------------------------------------

class TestMACrossover:
    def test_bullish_crossover_returns_25(self):
        """5日MA > 20日MA 時應得 25 分。"""
        # 前20筆低價，後5筆高價 → ma5 > ma20
        closes = [100.0] * 20 + [200.0] * 5
        df = _make_df(closes)
        _, _, score, _, _ = _calc_ma_crossover(df["close"])
        assert score == 25

    def test_bearish_crossover_returns_0(self):
        """5日MA < 20日MA 時應得 0 分。"""
        # 前20筆高價，後5筆低價 → ma5 < ma20
        closes = [200.0] * 20 + [100.0] * 5
        df = _make_df(closes)
        _, _, score, _, _ = _calc_ma_crossover(df["close"])
        assert score == 0

    def test_flat_price_ma_values_equal_close(self):
        """股價完全持平時，ma5 與 ma20 應等於收盤價本身。"""
        closes = [150.0] * 25
        df = _make_df(closes)
        ma5, ma20, _, _, _ = _calc_ma_crossover(df["close"])
        assert ma5 == pytest.approx(150.0)
        assert ma20 == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# Volume Surge
# ---------------------------------------------------------------------------

class TestVolumeSurge:
    def test_high_surge_returns_25(self):
        """近5日均量 / 近20日均量 ≥ 1.5 時應得 25 分。"""
        # 前15筆低量，後5筆高量（高出 3 倍）→ ratio ≥ 1.5
        volumes = [100_000.0] * 15 + [300_000.0] * 5
        df = _make_df([1.0] * 20, volumes)
        _, score = _calc_volume_surge(df["volume"])
        assert score == 25

    def test_mid_surge_returns_15(self):
        """1.2 ≤ ratio < 1.5 時應得 15 分。"""
        volumes = [100_000.0] * 15 + [130_000.0] * 5
        df = _make_df([1.0] * 20, volumes)
        ratio, score = _calc_volume_surge(df["volume"])
        assert 1.2 <= ratio < 1.5
        assert score == 15

    def test_no_surge_returns_0(self):
        """均量無明顯放大時應得 0 分。"""
        volumes = [100_000.0] * 20
        df = _make_df([1.0] * 20, volumes)
        _, score = _calc_volume_surge(df["volume"])
        assert score == 0

    def test_zero_volume_returns_0(self):
        """成交量全為零時不應拋例外，score 應為 0。"""
        volumes = [0.0] * 20
        df = _make_df([1.0] * 20, volumes)
        ratio, score = _calc_volume_surge(df["volume"])
        assert ratio == 0.0
        assert score == 0


# ---------------------------------------------------------------------------
# Price Trend
# ---------------------------------------------------------------------------

class TestPriceTrend:
    def test_strong_uptrend_returns_20(self):
        """近30日漲幅 > 5% 時應得 20 分。"""
        # 從 100 漲到 110（+10%）
        closes = list(range(100, 131))  # 31 筆，漲 30%
        df = _make_df(closes)
        pct, score = _calc_price_trend(df["close"])
        assert pct > 0.05
        assert score == 20

    def test_mild_uptrend_returns_10(self):
        """0% < 漲幅 ≤ 5% 時應得 10 分。"""
        closes = [100.0] * 29 + [103.0]  # +3%
        df = _make_df(closes)
        pct, score = _calc_price_trend(df["close"])
        assert 0.0 < pct <= 0.05
        assert score == 10

    def test_downtrend_returns_0(self):
        """下跌時應得 0 分。"""
        closes = list(range(130, 99, -1))  # 31 筆，跌幅
        df = _make_df(closes)
        pct, score = _calc_price_trend(df["close"])
        assert pct < 0.0
        assert score == 0

    def test_flat_returns_0(self):
        """完全持平（0%）不超過門檻，應得 0 分。"""
        closes = [100.0] * 30
        df = _make_df(closes)
        pct, score = _calc_price_trend(df["close"])
        assert pct == pytest.approx(0.0)
        assert score == 0


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

class TestRSI:
    def test_ideal_rsi_returns_20(self):
        """RSI 落在 45–65 健康區間時應得 20 分。"""
        # 漲跌交替但整體微升 → 漲跌天數接近，RSI 落在中段
        closes = [100.0 + (1.0 if i % 2 == 0 else -0.8) * (i // 2 + 1) * 0.1
                  for i in range(30)]
        df = _make_df(closes)
        rsi, score = _calc_rsi(df["close"])
        assert 45 <= rsi <= 65, f"Expected RSI in 45–65, got {rsi}"
        assert score == 20

    def test_overbought_rsi_returns_10_or_0(self):
        """強勁連漲 → RSI 偏高，應得 10 分（65–75）或 0 分（>75）。"""
        closes = [float(100 + i * 5) for i in range(30)]
        df = _make_df(closes)
        rsi, score = _calc_rsi(df["close"])
        assert score in (0, 10)

    def test_all_gains_rsi_equals_100(self):
        """每日都上漲（無跌幅）時，avg_loss = 0，RSI 應為 100。"""
        closes = list(range(100, 130))
        df = _make_df(closes)
        rsi, _ = _calc_rsi(df["close"])
        assert rsi == pytest.approx(100.0)

    def test_all_losses_rsi_near_0(self):
        """每日都下跌（無漲幅）時，RSI 應接近 0。"""
        closes = list(range(130, 100, -1))
        df = _make_df(closes)
        rsi, _ = _calc_rsi(df["close"])
        assert rsi < 10


# ---------------------------------------------------------------------------
# Stability
# ---------------------------------------------------------------------------

class TestStability:
    def test_stable_price_returns_10(self):
        """變異係數 < 0.05 時應得 10 分。"""
        # 極小波動：均價 100，波動 ±0.1
        closes = [100.0 + (i % 3) * 0.1 for i in range(30)]
        df = _make_df(closes)
        cv, score = _calc_stability(df["close"])
        assert cv < 0.05
        assert score == 10

    def test_volatile_price_returns_0(self):
        """變異係數 ≥ 0.05 時應得 0 分。"""
        # 劇烈波動：50 和 150 交替
        closes = [50.0 if i % 2 == 0 else 150.0 for i in range(30)]
        df = _make_df(closes)
        cv, score = _calc_stability(df["close"])
        assert cv >= 0.05
        assert score == 0

    def test_zero_mean_no_crash(self):
        """均價為零時不應拋例外。"""
        closes = [0.0] * 30
        df = _make_df(closes)
        cv, score = _calc_stability(df["close"])
        assert cv == 0.0
        assert score == 0


# ---------------------------------------------------------------------------
# Integration — analyze()
# ---------------------------------------------------------------------------

class TestAnalyze:
    def _make_bullish_df(self) -> pd.DataFrame:
        """建立一個各指標都偏正面的測試 DataFrame（60 筆）。"""
        # 溫和上漲 + 近5日放量
        base_closes = [float(100 + i * 0.3) for i in range(60)]
        base_volumes = [500_000.0] * 55 + [800_000.0] * 5
        return _make_df(base_closes, base_volumes)

    def test_returns_analysis_result_with_all_fields(self):
        """analyze() 回傳的 AnalysisResult 所有欄位都不為 None。"""
        df = self._make_bullish_df()
        result = analyze(df, "2330")

        assert result.stock_no == "2330"
        assert result.start_date is not None
        assert result.end_date is not None
        assert result.total_score is not None
        assert result.verdict in ("值得關注", "暫不關注")
        assert len(result.closes) == 60
        assert len(result.volumes) == 60
        assert len(result.dates) == 60

    def test_total_score_equals_sum_of_individual_scores(self):
        """total_score 應精確等於各項得分加總。"""
        df = self._make_bullish_df()
        result = analyze(df, "2330")
        s = result.scores
        expected = (
            s.ma_crossover_score
            + s.volume_surge_score
            + s.price_trend_score
            + s.rsi_score
            + s.stability_score
        )
        assert result.total_score == expected

    def test_verdict_threshold_at_60(self):
        """總分 ≥ 60 → 值得關注；< 60 → 暫不關注。"""
        df = self._make_bullish_df()
        result = analyze(df, "2330")

        if result.total_score >= 60:
            assert result.verdict == "值得關注"
        else:
            assert result.verdict == "暫不關注"

    def test_score_within_valid_range(self):
        """總分必須在 0–100 之間。"""
        df = self._make_bullish_df()
        result = analyze(df, "2330")
        assert 0 <= result.total_score <= 100
