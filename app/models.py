"""
Pydantic models for request/response validation and internal data structures.

分三層：
1. 內部計算結果（AnalysisResult）— analyzer.py 輸出
2. LLM 輸出（LLMOutput）— llm.py 輸出
3. API Response（AnalyzeResponse）— FastAPI endpoint 回傳給呼叫方
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Layer 1: Internal — analyzer.py output
# ---------------------------------------------------------------------------

@dataclass
class IndicatorScores:
    """每項技術指標的原始數值與對應得分。

    Attributes:
        ma_crossover_score: MA 黃金交叉得分（0 or 25）
        volume_surge_ratio: 近5日均量 / 近20日均量
        volume_surge_score: 爆量得分（0, 15, or 25）
        price_trend_pct: 近30日漲跌幅，例如 0.08 代表 +8%
        price_trend_score: 價格趨勢得分（0, 10, or 20）
        rsi: RSI 14日數值
        rsi_score: RSI 得分（0, 10, or 20）
        stability_cv: 價格變異係數（標準差 / 均價）
        stability_score: 穩定性得分（0 or 10）
    """
    ma_crossover_score: int
    volume_surge_ratio: float
    volume_surge_score: int
    price_trend_pct: float
    price_trend_score: int
    rsi: float
    rsi_score: int
    stability_cv: float
    stability_score: int


@dataclass
class AnalysisResult:
    """analyzer.py 對單一股票的完整分析結果。

    Attributes:
        stock_no: 股票代號，例如 "2330"
        start_date: 實際使用資料的起始日期，格式 "YYYY-MM-DD"
        end_date: 實際使用資料的結束日期，格式 "YYYY-MM-DD"
        scores: 各指標得分明細
        total_score: 加總總分（0–100）
        verdict: "值得關注" or "暫不關注"
        ma5: 最新一日的 5 日移動平均價
        ma20: 最新一日的 20 日移動平均價
        closes: 用於繪圖的收盤價序列（由舊到新）
        volumes: 用於繪圖的成交量序列（由舊到新）
        dates: 對應 closes/volumes 的日期序列，格式 "YYYY-MM-DD"
    """
    stock_no: str
    start_date: str
    end_date: str
    scores: IndicatorScores
    total_score: int
    verdict: str
    ma5: float
    ma20: float
    closes: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    ma5_series: list[float] = field(default_factory=list)
    ma20_series: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Layer 2: LLM output — llm.py output
# ---------------------------------------------------------------------------

class LLMOutput(BaseModel):
    """Claude 回傳的結構化分析結果。

    用 Pydantic 解析並驗證 LLM 輸出的 JSON，確保欄位齊全、型別正確。
    解析失敗時由 llm.py 產生 fallback，這個 model 只負責「成功路徑」。
    """

    verdict: str = Field(
        description="最終結論，'值得關注' or '暫不關注'"
    )
    confidence: str = Field(
        description="信心程度，'高' / '中' / '低'"
    )
    key_signals: list[str] = Field(
        description="支撐結論的關鍵訊號，2–4 點"
    )
    risks: list[str] = Field(
        description="主要風險提示，1–3 點"
    )
    summary: str = Field(
        description="約 150–200 字的中文說明"
    )


# ---------------------------------------------------------------------------
# Layer 3: API Response — FastAPI endpoint output
# ---------------------------------------------------------------------------

class IndicatorsOut(BaseModel):
    """API response 中的指標明細欄位，供前端或呼叫方使用。"""

    ma_crossover_score: int
    volume_surge_ratio: float
    volume_surge_score: int
    price_trend_pct: float
    price_trend_score: int
    rsi: float
    rsi_score: int
    stability_cv: float
    stability_score: int


class PeriodOut(BaseModel):
    """資料實際涵蓋的日期區間。"""

    start: str = Field(description="最早一筆資料的日期，YYYY-MM-DD")
    end: str = Field(description="最新一筆資料的日期，YYYY-MM-DD")


class AnalyzeResponse(BaseModel):
    """GET /analyze/{stock_no} 的完整回應格式。"""

    stock_no: str
    period: PeriodOut
    score: int = Field(ge=0, le=100, description="總評分 0–100，≥60 為值得關注")
    verdict: str
    indicators: IndicatorsOut
    llm_output: LLMOutput
