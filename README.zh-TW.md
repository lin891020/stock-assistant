# 台股關注度分析 Assistant

輸入股票代號，系統從 TWSE OpenAPI 抓取最近 60 筆交易日資料，
計算五項技術指標並產生量化評分（0–100），再由 Claude AI 生成結構化說明。

## 系統架構

```
使用者輸入股票代號
        │
        ▼
┌───────────────────┐
│  FastAPI / Streamlit  │  ← 兩個獨立入口，互不依賴
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  twse_client.py   │  ← 非同步抓取 TWSE API（含 retry 指數退避）
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  analyzer.py      │  ← 技術指標計算 + 評分（純 Python/pandas）
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  llm.py           │  ← Claude 生成結構化 JSON + 中文說明
└───────────────────┘
        │
        ▼
  JSON response / Streamlit UI
```

**重要設計決策：** Streamlit 直接 import `app/` 模組，不透過 FastAPI HTTP，避免多一層網路延遲。

## 評分邏輯

量化指標評分制（0–100 分），**≥ 60 分**判定為「值得關注」。

| 指標 | 權重 | 分類 | 判斷邏輯 |
|------|------|------|---------|
| MA Crossover | 25 | 領先指標 | 5日MA > 20日MA → 25分 |
| Volume Surge | 25 | 領先指標 | 近5日均量/近20日均量 ≥1.5 → 25分；≥1.2 → 15分 |
| Price Trend | 20 | 同步指標 | 30日漲幅 >5% → 20分；>0% → 10分 |
| RSI (14日) | 20 | 同步指標 | RSI 45–65 → 20分；35–45 或 65–75 → 10分 |
| Stability | 10 | 風險指標 | 價格標準差/均價 <0.05 → 10分 |

**權重設計理由：** 領先指標（MA + 量能）權重較高，目標是「提早發現」而非「確認已發生的事」。各門檻值為 domain knowledge 主觀設定，可根據回測結果調整。

## LLM 角色設計

Claude 接收結構化指標摘要，回傳 JSON 格式說明。**決策由 Python 邏輯負責，LLM 只做解釋與敘事。**

```json
{
  "verdict": "值得關注",
  "confidence": "高",
  "key_signals": ["成交量放大 1.8 倍", "短期均線上穿長期均線"],
  "risks": ["RSI 偏高，注意追高風險"],
  "summary": "約 150–200 字的中文說明..."
}
```

System prompt 啟用 **Prompt Caching**，相同 prompt 在 5 分鐘內不重複計算 token 費用。

## 安裝

**前置條件：** Python 3.9+，Anthropic API Key

```bash
# 1. 進入專案目錄
cd stock-assistant

# 2. 安裝套件
pip install -r requirements.txt

# 3. 設定環境變數
cp .env.example .env
# 編輯 .env，填入 ANTHROPIC_API_KEY=你的金鑰
```

## 啟動方式

**FastAPI（REST API）**
```bash
uvicorn app.main:app --reload
# API 文件：http://localhost:8000/docs
# 範例請求：GET http://localhost:8000/analyze/2330
```

**Streamlit（互動介面）**
```bash
streamlit run streamlit_app.py
# 開啟 http://localhost:8501
```

## API 回應格式

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

## 錯誤處理

| 情境 | HTTP 狀態 | 說明 |
|------|-----------|------|
| 股票代號不存在 | 404 | 查無此股票代號 |
| 資料不足 | 422 | 資料不足 20 個交易日，無法計算完整指標 |
| TWSE API 無法存取 | 503 | 重試 3 次（指數退避）後仍失敗 |

## 執行測試

```bash
pytest tests/
```

- `test_analyzer.py`：各指標計算與評分邏輯的完整單元測試
- `test_twse_client.py`：資料解析 mock 測試（千分位處理、民國年轉換）
- `llm.py` 不測試（非確定性輸出）

## 假設與限制

- TWSE OpenAPI 僅涵蓋上市股票，不含上櫃（OTC）
- 資料在盤後才更新，盤中無法取得當日資料
- 技術指標僅反映價格行為，不含基本面或新聞資訊
- LLM 說明為輔助參考，**非投資建議**
