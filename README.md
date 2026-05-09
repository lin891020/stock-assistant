# Stock Assistant

A Taiwan stock market attention analysis tool. Input a stock ticker and the system fetches the latest 60 trading days of data from TWSE, calculates five technical indicators, produces a quantitative score (0–100), and generates a structured explanation via Claude AI.

## Architecture

```
User Input (stock ticker)
        │
        ▼
┌───────────────────┐
│  FastAPI / Streamlit  │  ← two independent entry points
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  twse_client.py   │  ← fetch 3 months from TWSE OpenAPI (async, retry)
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  analyzer.py      │  ← calculate indicators + score (pure Python/pandas)
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  llm.py           │  ← Claude generates structured JSON + summary
└───────────────────┘
        │
        ▼
  JSON response / Streamlit UI
```

**Key design decision:** Streamlit imports `app/` modules directly — it does not go through FastAPI over HTTP. This avoids unnecessary network overhead.

## Scoring Logic

A quantitative scoring system (0–100). Score ≥ 60 → "Worth Watching".

| Indicator | Weight | Type | Logic |
|-----------|--------|------|-------|
| MA Crossover | 25 | Leading | 5-day MA > 20-day MA → 25 pts |
| Volume Surge | 25 | Leading | 5-day avg vol / 20-day avg vol ≥ 1.5 → 25 pts; ≥ 1.2 → 15 pts |
| Price Trend | 20 | Coincident | 30-day return > 5% → 20 pts; > 0% → 10 pts |
| RSI (14-day) | 20 | Coincident | RSI 45–65 → 20 pts; 35–45 or 65–75 → 10 pts |
| Stability | 10 | Risk | Price CV < 0.05 → 10 pts |

Leading indicators (MA + Volume) carry higher weight because the goal is early detection, not confirmation of events already in the past.

## LLM Role

Claude receives structured indicator data and returns a JSON object — it does **not** make the verdict decision. All decisions are made deterministically by `analyzer.py`.

```json
{
  "verdict": "Worth Watching",
  "confidence": "Medium",
  "key_signals": ["Volume surged 1.8x", "Short MA crossed above long MA"],
  "risks": ["RSI elevated, chasing risk"],
  "summary": "..."
}
```

Prompt caching is enabled on the system prompt to reduce token costs on repeated queries.

## Setup

**Prerequisites:** Python 3.9+, an Anthropic API key.

```bash
# 1. Clone and enter the project
cd stock-assistant

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=your_key_here
```

## Running

**FastAPI (REST API)**
```bash
uvicorn app.main:app --reload
# API docs: http://localhost:8000/docs
# Example: GET http://localhost:8000/analyze/2330
```

**Streamlit (UI)**
```bash
streamlit run streamlit_app.py
# Opens http://localhost:8501
```

## API Response

```json
{
  "stock_no": "2330",
  "period": { "start": "2025-03-10", "end": "2025-05-09" },
  "score": 75,
  "verdict": "值得關注",
  "indicators": {
    "ma_crossover_score": 25,
    "volume_surge_ratio": 1.62,
    "volume_surge_score": 25,
    "price_trend_pct": 0.0821,
    "price_trend_score": 20,
    "rsi": 58.3,
    "rsi_score": 20,
    "stability_cv": 0.042,
    "stability_score": 10
  },
  "llm_output": {
    "verdict": "值得關注",
    "confidence": "高",
    "key_signals": ["..."],
    "risks": ["..."],
    "summary": "..."
  }
}
```

## Error Handling

| Scenario | HTTP Status | Message |
|----------|-------------|---------|
| Ticker not found | 404 | No data found for stock |
| Insufficient data | 422 | Fewer than 20 trading days available |
| TWSE API unavailable | 503 | API unavailable after 3 retries (exponential backoff) |

## Testing

```bash
pytest tests/
```

- `test_analyzer.py` — full unit tests for each indicator and scoring logic
- `test_twse_client.py` — mock tests for data parsing (comma stripping, ROC date conversion)
- `llm.py` is not tested (non-deterministic output)

## Limitations

- TWSE OpenAPI covers listed stocks only (not OTC/emerging market boards)
- Data updates after market close; intraday data is not available
- Technical indicators reflect price behavior only — no fundamentals or news
- LLM output is for reference only, not investment advice
