[English](README.md) | [繁體中文](README.zh-TW.md)

# Stock Assistant

A quantitative screening tool for Taiwan listed stocks. The system fetches 60 trading days of TWSE data, scores five technical indicators (0–100), augments with recent news headlines via Google News RSS, then generates a structured AI explanation via Claude.

## Demo

<!-- Replace with actual demo video / GIF after recording -->
> 🎬 *Demo video — coming soon*

## Features

| Feature | Description |
|---------|-------------|
| Single-stock analysis | Technical scoring + RAG news augmentation + Claude AI explanation in retail / institutional modes |
| Multi-stock comparison | Up to 4 stocks, normalized % return overlay chart + side-by-side indicator score table |

## Architecture & Design

```
User Input
    │
    ▼
┌──────────────────────────────┐
│  Streamlit UI                │  ← imports app/ directly (no FastAPI HTTP hop)
└──────────────────────────────┘
    │
    ├── twse_client.py    ← async TWSE fetch, exponential backoff retry
    ├── analyzer.py       ← deterministic scoring (pure Python / pandas)
    ├── news_fetcher.py   ← Google News RSS, gracefully degraded
    └── llm.py            ← Claude: explain only, never decide
```

**Key design decisions:**
- Streamlit calls `app/` modules directly — no FastAPI middleman, no extra network hop for a single-user demo
- All scoring is deterministic Python; LLM only translates numbers into language
- LLM verdict is decorative — `analyzer.py` always makes the authoritative call
- RAG news augmentation is non-blocking: if the RSS fetch fails, LLM analysis continues with technical indicators only

## LLM Strategy

Claude receives a structured summary (scores + news headlines), not raw price data, and returns a typed JSON object:

```json
{
  "verdict": "值得關注",
  "confidence": "高",
  "key_signals": ["成交量放大 1.8 倍", "短期均線上穿長期均線"],
  "risks": ["RSI 偏高，注意追高風險"],
  "summary": "..."
}
```

**Prompt design:**
- System prompt is fixed and cache-controlled (Anthropic Prompt Caching — reused within 5-minute TTL to reduce token cost)
- LLM is explicitly instructed to use only the provided indicators and news headlines — no external knowledge
- Streaming output via `messages.stream()` for responsive UI
- Two views rendered from the same JSON: **retail mode** (narrative summary) and **institutional mode** (raw scores + signals)
- If JSON parsing fails, raw streamed text is shown as fallback; quantitative scores are unaffected

**RAG flow:**
```
Google News RSS (stock code + name)
    → fetch_recent_news()           ← any exception → return []
    → headlines appended to prompt
    → LLM cites headlines in summary
    → UI shows source expander for user verification
```

## TWSE API Integration

`STOCK_DAY` endpoint — 3 calls per stock (one per month, ~60 trading days total).

**Three distinct error types:**

| Error | Cause | Handling |
|-------|-------|----------|
| `StockNotFoundError` | Ticker absent from TWSE listed stocks | User-facing error, early return |
| `InsufficientDataError` | Fewer than 20 trading days returned | User-facing warning, early return |
| `TWSEUnavailableError` | API unreachable after 3 retries (exponential backoff) | User-facing error, early return |

**Rate limiting:** Multi-stock comparison fetches sequentially with 0.5 s delay between tickers, plus `st.cache_data(ttl=600)` to avoid redundant requests. (Concurrent fetching with `asyncio.gather` triggered HiNetCDN WAF — HTTP 307 with no `Location` header — diagnosed via `curl`; fixed by switching to sequential fetch.)

## Scoring Logic

Score ≥ 60 → "Worth Watching". All thresholds are manually calibrated domain knowledge; not backtested.

| Indicator | Weight | Type | Logic |
|-----------|--------|------|-------|
| MA Crossover | 25 | Leading | 5-day MA > 20-day MA → 25 pts |
| Volume Surge | 25 | Leading | 5-day avg vol / 20-day avg vol ≥ 1.5 → 25 pts; ≥ 1.2 → 15 pts |
| Price Trend | 20 | Coincident | 30-day return > 5% → 20 pts; > 0% → 10 pts |
| RSI (14-day) | 20 | Coincident | RSI 45–65 → 20 pts; 35–45 or 65–75 → 10 pts |
| Stability | 10 | Risk | Price CV < 0.05 → 10 pts |

Leading indicators (MA + Volume) carry higher weight because the goal is early detection, not confirmation of events already past.

## Setup

**Prerequisites:** Python 3.9+, Anthropic API key

```bash
cd stock-assistant
pip install -r requirements.txt
cp .env.example .env   # set ANTHROPIC_API_KEY
```

**Run locally**
```bash
streamlit run 台股分析.py      # UI → http://localhost:8501
uvicorn app.main:app --reload  # REST API → http://localhost:8000/docs
```

**Run with Docker**
```bash
docker compose up
```

## Testing

```bash
pytest tests/
```

29 unit tests across two modules:
- `test_analyzer.py` — indicator calculation and scoring edge cases
- `test_twse_client.py` — data parsing mocks (comma stripping, ROC-to-AD date conversion)

`llm.py` is intentionally untested — non-deterministic output.

## Assumptions, Limitations & Risks

| Item | Detail |
|------|--------|
| Data scope | TWSE listed stocks only — no OTC, ETFs, or warrants |
| Data freshness | End-of-day only; current trading day unavailable until ~14:00 after market close |
| News reliability | Google News RSS is best-effort; failures silently degrade to no-news mode |
| LLM accuracy | Hallucination risk exists; mitigated by structured prompt scope and user-visible news source links |
| TWSE stability | HiNetCDN WAF may temporarily block IPs under high request frequency |
| Scoring thresholds | Manually calibrated — not validated against historical returns |
