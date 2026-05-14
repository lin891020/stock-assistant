[English](README.md) | [繁體中文](README.zh-TW.md)

# 台股關注度分析 Assistant

輸入股票代號，系統從 TWSE OpenAPI 抓取近 60 筆交易日資料，計算五項技術指標並產生量化評分（0–100），再透過 **Agentic 新聞搜尋** — LLM 主動決定要搜尋什麼、呼叫 `search_news` 工具從 Google News RSS 抓取相關新聞，最後生成結構化分析說明。

## Demo

<!-- 錄製完成後，將 Demo 影片或 GIF 放在這裡 -->
> https://github.com/user-attachments/assets/ad171fa2-13f3-4130-9f67-de9353b8c279

## 功能

| 功能 | 說明 |
|------|------|
| 單股分析 | 技術指標評分 + Agentic 新聞搜尋 + AI 說明（散戶 / 法人兩種模式） |
| 多股比較 | 最多 4 支股票，標準化相對漲跌幅（%）疊圖 + 技術指標評分比較表 |
| AI 搜尋過程透明化 | 可展開面板顯示每步 AI 思考與新聞搜尋，含各步驟耗時 |
| 雙 LLM Provider | 透過 `LLM_PROVIDER` 環境變數切換 Anthropic（Claude）或 GitHub Models（gpt-4o-mini） |

## 系統架構

![Architecture](docs/architecture.svg)

**架構重點：**
- 兩個入口：Streamlit 互動介面與 FastAPI REST endpoint，兩者都直接 import `app/`，中間沒有 HTTP 層。
- 股價資料來自 TWSE OpenAPI；新聞脈絡透過 Agentic tool-use loop，以 best-effort 方式從 Google News RSS 抓取。
- 評分邏輯（`analyzer.py`）完全確定性，不依賴 LLM — 即使 LLM 呼叫失敗，圖表與分數仍正常顯示。
- LLM 只負責說明、摘要與風險解讀；它透過 `search_news` 工具主動決定要搜尋什麼，而非被動接收預先格式化的新聞清單。
- 最終輸出為定型 JSON，渲染成兩種 UI 模式：散戶模式（敘事摘要）與法人模式（數字明細 + 訊號）。

## 設計決策

- **直接 Import** — Streamlit 直接 import `app/` 模組，不走 FastAPI HTTP。省去不必要的網路開銷；兩個 entry point 共用同一份核心，互不耦合。
- **確定性評分** — 所有指標計算與結論邏輯都在 `analyzer.py`（純 Python/pandas）。LLM 不碰評分。就算 LLM 完全失敗，圖表和分數仍正常顯示。
- **LLM 只負責解釋** — LLM 收到的是結構化摘要（評分 + 已搜尋新聞），不是原始股價資料。System prompt 明確禁止引用外部資訊，把幻覺風險框限在說明文字層。
- **Agentic 新聞搜尋** — 不再預先抓取固定新聞清單傳給 LLM，而是讓 LLM 主動決定要搜尋什麼。LLM 呼叫 `search_news` 工具（最多 3 輪），自行選擇查詢關鍵字，並把搜尋結果整合進分析。這模擬了分析師「按需查資料」的流程。

## LLM 使用方式與 Prompt 策略

Claude（或 gpt-4o-mini）執行 Agentic tool-use loop，回傳定型 JSON：

```json
{
  "verdict": "值得關注",
  "confidence": "高",
  "key_signals": ["成交量放大 1.8 倍", "短期均線上穿長期均線"],
  "risks": ["RSI 偏高，注意追高風險"],
  "summary": "...",
  "next_actions": ["觀察成交量是否持續放大"]
}
```

**Agentic loop 流程：**
```
LLM 收到：技術指標評分 + 結論 + 日期區間
    → LLM 決定查詢關鍵字（例：「台積電 法說會」）
    → tool call: search_news_by_query(query)
    → News Fetcher → Google News RSS
    → 搜尋結果注入對話
    → LLM 可再次搜尋（最多 3 輪）
    → LLM 生成最終 JSON
    → _linkify_news_citations()：將《標題》→ [標題](url)
```

**Provider 支援：**
- `LLM_PROVIDER=anthropic` — Claude 透過 `anthropic` SDK，原生 `tool_use` / `end_turn` 協定
- `LLM_PROVIDER=github` — gpt-4o-mini 透過 OpenAI 相容 SDK，`function_calling` / `finish_reason` 協定

**UI 透明化：**
- 可展開的「AI 搜尋過程」面板顯示每一步與耗時
- 💭 LLM 思考步驟（例：「AI 思考中　4.0s」）
- 🔍 搜尋步驟（例：「搜尋：台積電 法說會 → 5 則　0.9s」）
- 所有步驟耗時加總等於顯示的總耗時

**Fallback 機制：**
- 若 agentic loop 耗盡輪次或 JSON 解析失敗，回傳信心「低」的 fallback `LLMOutput`，圖表和評分不受影響

## TWSE API 串接與例外處理

使用 `STOCK_DAY` endpoint — 每支股票打 3 次（每月一次，共約 60 個交易日）。

**三種明確的例外情境：**

| 例外 | 原因 | 處理方式 |
|------|------|----------|
| `StockNotFoundError` | 股票代號不在 TWSE 上市名單 | 顯示錯誤訊息，early return |
| `InsufficientDataError` | 回傳交易日數 < 20 | 顯示警告訊息，early return |
| `TWSEUnavailableError` | 指數退避 retry 3 次後仍失敗 | 顯示錯誤訊息，early return |

**限流處理：** 多股比較改用 sequential fetch，每支股票間隔 0.5 秒，搭配 `st.cache_data(ttl=600)` 避免重複請求。（原本用 `asyncio.gather` 並發，觸發 HiNetCDN WAF 回傳 HTTP 307 無 `Location` header — 透過 `curl` 診斷確認後改成同步依序抓取。）

## 評分邏輯

總分 ≥ 60 → 判定「值得關注」。所有門檻值為人工設定，未做回測驗證。

| 指標 | 權重 | 分類 | 判斷邏輯 |
|------|------|------|----------|
| MA 黃金交叉 | 25 | 領先指標 | 5日MA > 20日MA → 25分 |
| 成交量放大 | 25 | 領先指標 | 近5日均量/近20日均量 ≥1.5 → 25分；≥1.2 → 15分 |
| 價格趨勢 | 20 | 同步指標 | 近30日漲幅 >5% → 20分；>0% → 10分 |
| RSI（14日）| 20 | 同步指標 | RSI 45–65 → 20分；35–45 或 65–75 → 10分 |
| 價格穩定性 | 10 | 風險指標 | 變異係數 < 0.05 → 10分 |

領先指標（MA + 量能）權重較高，目標是「提早發現」而非「確認已發生的事」。

## 安裝與啟動

**前置條件：** Python 3.9+，以下擇一：Anthropic API Key 或 GitHub Models Token

```bash
cd stock-assistant
pip install -r requirements.txt
cp .env.example .env
```

**.env 選項：**
```bash
# 選項 A：Anthropic（Claude）
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...

# 選項 B：GitHub Models（gpt-4o-mini，有免費額度）
LLM_PROVIDER=github
GITHUB_TOKEN=ghp_...
```

**本機啟動**
```bash
streamlit run 台股分析.py      # UI → http://localhost:8501
uvicorn app.main:app --reload  # REST API → http://localhost:8000/docs
```

**Docker 啟動**
```bash
docker compose up
```

## 測試

```bash
pytest tests/
```

46 個單元測試，涵蓋三個模組：
- `test_analyzer.py` — 各指標計算與評分邊界條件
- `test_twse_client.py` — 資料解析 mock（千分位處理、民國年轉換）
- `test_llm.py` — Agentic loop 邏輯（tool call 路由、重複 query 去重、max rounds fallback、provider 切換）

## 重要假設、限制與風險

| 項目 | 說明 |
|------|------|
| 資料範圍 | 僅限 TWSE 上市股票，不含上櫃、ETF、權證 |
| 資料即時性 | 僅提供盤後完整資料；當日資料約於收盤後 14:00 起才能取得 |
| 新聞可靠性 | Google News RSS 為 best-effort，失敗時靜默降級為無新聞模式 |
| LLM 準確性 | LLM 有幻覺風險；透過結構化 prompt 範圍限制 + UI 顯示可點擊新聞來源連結來降低影響 |
| TWSE 穩定性 | HiNetCDN WAF 在高頻請求下可能暫時封鎖 IP |
| 評分門檻 | 人工設定，未經回測驗證 |
