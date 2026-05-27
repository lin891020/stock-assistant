# Telegram Investment Bot — Design Spec

**Date:** 2026-05-27
**Status:** Approved

## Overview

A standalone Telegram Bot that provides two core capabilities for a beginner investor:

1. **Stock Analysis** — 7 Wall Street analyst prompts for Taiwan and US stocks, delivered as PDF reports
2. **Personal Finance Coach** — guided dialogue to help allocate salary and build an investment plan

The bot is independent from the existing Stock Assistant (Streamlit app), though it may reuse TWSE client logic. Deployed 24/7 on Render.com, restricted to a single user via Telegram ID whitelist.

---

## Architecture

### Project Structure

```
stock-telegram-bot/
├── bot/
│   ├── main.py              # Bot entry point, registers all handlers
│   ├── handlers/
│   │   ├── analyze.py       # /analyze stock analysis conversation flow
│   │   ├── finance.py       # /finance personal finance coach flow
│   │   └── learn.py         # /learn educational content
│   ├── services/
│   │   ├── stock.py         # TWSE API + yfinance data fetching
│   │   ├── llm.py           # Claude / gpt-4o-mini integration
│   │   ├── pdf.py           # PDF report generation
│   │   └── github_store.py  # Read/write user profile JSON via GitHub API
│   └── content/
│       └── lessons.json     # Pre-written educational content
├── requirements.txt
└── render.yaml              # Render deployment config
```

### Data Flow

```
User → Telegram
  → State machine determines current state
  → Corresponding handler processes input
  → Calls services (stock / llm / pdf / github_store)
  → Returns message or PDF file
```

### State Machine

Three top-level conversation flows:

| State | Flow |
|-------|------|
| `ANALYZING` | Single-turn: ask for ticker → run analysis → send PDF |
| `FINANCE_ONBOARDING` → `FINANCE_GOALS` → `FINANCE_RESULT` | Multi-turn: financial profile → goals → personalized plan |
| `LEARNING` | Single-turn: keyword lookup → return lesson content |

---

## Feature 1: Stock Analysis

### Triggers

```
/analyze 2330         ← Taiwan stock (4-digit number)
/analyze TSLA         ← US stock (alphabetic)
「幫我分析台積電」      ← Natural language (resolved to /analyze)
```

### Flow

1. Identify stock code (Taiwan: 4-digit / US: alphabetic ticker)
2. Fetch data:
   - Taiwan → TWSE API (price + technical indicators)
   - US → yfinance (financial data)
3. Present 7 analysis type buttons
4. Claude executes selected prompt → generates structured report
5. reportlab generates PDF
6. Telegram sends PDF file to user

### 7 Analysis Prompts (Button Labels)

| Button | Prompt Focus |
|--------|-------------|
| 完整分析 | Full Wall Street analysis: business model, moat, industry trends, financials, risks, valuation, 12–24 month outlook |
| 財務健康 | 5-year financial data: revenue growth, net income, FCF, margins, debt, ROE — strengthening or weakening? |
| 競爭護城河 | Moat evaluation: brand, network effects, switching costs, cost advantage, patents — scored 1–10 vs competitors |
| 估值分析 | Valuation: P/E vs peers, DCF, industry averages — undervalued or overvalued? |
| 成長潛力 | Growth potential: market size, industry growth, expansion, new products, AI/tech edge — 5–10 year outlook |
| 多空辯論 | Bull vs bear analyst debate — both sides with data-backed arguments, neutral conclusion |
| 投資建議 | Buy / hold / avoid: short-term (1yr), long-term (5yr+), key catalysts, key risks, final verdict |

### LLM Data Strategy

- Claude uses its training knowledge as the primary source for analysis
- yfinance supplements with real financial figures where available
- Taiwan stocks: TWSE API provides price/volume data; technical indicators calculated locally

---

## Feature 2: Personal Finance Coach

### Trigger

```
/finance
「我想學資產配置」  ← Natural language
```

### Three-Stage Dialogue Flow

**Stage 1 — Financial Profile (FINANCE_ONBOARDING)**

Bot asks sequentially:
1. 每月稅後收入大約多少？
2. 每月固定支出大約多少？（房租／水電／交通）
3. 目前有多少存款？
4. 有任何貸款或負債嗎？

**Stage 2 — Goal Setting (FINANCE_GOALS)**

Bot presents buttons:
- [緊急備用金] [開始投資ETF] [存第一桶金] [買房計畫] [退休規劃]

**Stage 3 — Personalized Plan (FINANCE_RESULT)**

Claude generates:
- Salary allocation ratio (50/30/20 or customized based on actual numbers)
- Concrete first action steps
- Estimated timeline to reach stated goal

Profile saved to GitHub JSON. On subsequent `/finance` calls, bot loads existing profile and asks if user wants to update or continue.

---

## Feature 3: Educational Content (/learn)

### Trigger

```
/learn ETF
/learn 緊急備用金
/learn 什麼是護城河   ← Not in pre-written list → Claude generates
```

### Pre-written Topics (Initial Set)

ETF、指數基金、緊急備用金、50/30/20法則、複利、資產配置、股票 vs 債券、定期定額、本益比、股息

Topics not in the list are answered by Claude in real time.

---

## Data Storage

### GitHub Private Repo JSON (User Profile)

```json
{
  "user_profile": {
    "monthly_income": 50000,
    "monthly_expenses": 20000,
    "savings": 100000,
    "debt": 0,
    "goal": "緊急備用金",
    "updated_at": "2026-05-27"
  }
}
```

Read and written via GitHub API using a personal access token. Single-user, no database required.

### PDF Reports

Generated locally on the Render server → sent directly to user via Telegram file message. Not persisted server-side after sending.

---

## Deployment

### Render Configuration

```yaml
# render.yaml
services:
  - type: worker
    name: stock-bot
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: python bot/main.py
```

Polling mode (not webhook) — simpler setup, no public URL required for the bot itself.

### Environment Variables

```
TELEGRAM_BOT_TOKEN       # From @BotFather
ANTHROPIC_API_KEY        # Claude API
GITHUB_TOKEN             # Personal access token for profile JSON repo
GITHUB_REPO              # e.g. username/stock-bot-data (private)
LLM_PROVIDER             # anthropic | github (default: anthropic)
ALLOWED_TELEGRAM_ID      # Your Telegram user ID (whitelist)
```

---

## Security

- All incoming messages checked against `ALLOWED_TELEGRAM_ID` before any processing
- Unknown users receive no response (silent rejection)

---

## Backlog (Not in Scope)

- OneDrive API automatic PDF upload (Microsoft Graph)
- Multi-user whitelist
- Push notifications (e.g. daily market alerts)
- Portfolio tracking

---

## Tech Stack

| Component | Library |
|-----------|---------|
| Telegram Bot | `python-telegram-bot` v20+ |
| Taiwan stock data | TWSE OpenAPI (reused from existing app) |
| US stock data | `yfinance` |
| LLM | `anthropic` SDK + OpenAI-compatible SDK |
| PDF generation | `reportlab` |
| User data | GitHub REST API via `requests` |
| Deployment | Render.com (worker service) |
