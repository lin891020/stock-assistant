"""
FastAPI application — exposes GET /analyze/{stock_no}.

職責：
  1. 接收 HTTP 請求，驗證股票代號格式
  2. 呼叫 twse_client → analyzer → llm，組裝完整結果
  3. 將各層的自訂例外對應到正確的 HTTP 狀態碼
  4. 回傳 AnalyzeResponse JSON

錯誤對應：
  StockNotFoundError    → 404
  InsufficientDataError → 422
  TWSEUnavailableError  → 503
  其他未預期例外        → 500
"""

import logging

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Path
from fastapi.responses import JSONResponse

from app import analyzer, twse_client
from app.llm import generate_analysis
from app.models import AnalyzeResponse, IndicatorsOut, PeriodOut
from app.twse_client import (
    InsufficientDataError,
    StockNotFoundError,
    TWSEUnavailableError,
)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Stock Assistant API",
    description="台股關注度分析 API — 輸入股票代號，回傳技術指標評分與 LLM 說明。",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get(
    "/analyze/{stock_no}",
    response_model=AnalyzeResponse,
    summary="分析指定股票",
    description=(
        "從 TWSE 抓取最近 60 筆交易日資料，計算技術指標評分（0–100），"
        "並由 Claude 生成結構化說明。評分 ≥ 60 判定為「值得關注」。"
    ),
)
async def analyze_stock(
    stock_no: str = Path(
        ...,
        min_length=4,
        max_length=6,
        pattern=r"^\d+$",
        description="股票代號，例如 2330",
        examples=["2330"],
    ),
) -> AnalyzeResponse:
    """分析單一股票的技術面，回傳評分與 LLM 說明。

    Args:
        stock_no: URL path 中的股票代號，由 FastAPI 自動驗證格式。

    Returns:
        AnalyzeResponse，包含資料區間、總分、各指標明細、LLM 輸出。

    Raises:
        HTTPException 404: 查無此股票代號。
        HTTPException 422: 資料筆數不足（< 20 個交易日）。
        HTTPException 503: TWSE API 無法存取。
        HTTPException 500: 其他未預期錯誤。
    """
    logger.info("Received analysis request for stock: %s", stock_no)

    # --- Step 1: Fetch raw data from TWSE ---
    try:
        df = await twse_client.fetch_stock_data(stock_no)
    except StockNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InsufficientDataError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TWSEUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # --- Step 2: Calculate technical indicators ---
    result = analyzer.analyze(df, stock_no)

    # --- Step 3: Generate LLM explanation ---
    try:
        llm_output = generate_analysis(result)
    except Exception as exc:
        # LLM 失敗不應讓整個 API 掛掉；記錄錯誤後繼續，
        # generate_analysis 內部已有 fallback，這層只捕捉更外層的例外
        logger.error("Unexpected LLM error for %s: %s", stock_no, exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="LLM analysis failed unexpectedly. Please retry.",
        ) from exc

    # --- Step 4: Assemble response ---
    s = result.scores
    return AnalyzeResponse(
        stock_no=result.stock_no,
        period=PeriodOut(start=result.start_date, end=result.end_date),
        score=result.total_score,
        verdict=result.verdict,
        indicators=IndicatorsOut(
            ma_crossover_score=s.ma_crossover_score,
            volume_surge_ratio=s.volume_surge_ratio,
            volume_surge_score=s.volume_surge_score,
            price_trend_pct=s.price_trend_pct,
            price_trend_score=s.price_trend_score,
            rsi=s.rsi,
            rsi_score=s.rsi_score,
            stability_cv=s.stability_cv,
            stability_score=s.stability_score,
        ),
        llm_output=llm_output,
    )


# ---------------------------------------------------------------------------
# Health check — useful for deployment and smoke testing
# ---------------------------------------------------------------------------

@app.get("/health", include_in_schema=False)
async def health_check() -> JSONResponse:
    """簡單的 liveness check，確認 API server 有在跑。"""
    return JSONResponse(content={"status": "ok"})
