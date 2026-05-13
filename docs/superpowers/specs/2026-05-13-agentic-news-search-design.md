# Agentic News Search — Design Spec

**Date:** 2026-05-13  
**Status:** Approved  
**Scope:** `llm.py`, `news_fetcher.py`, `台股分析.py`

---

## Problem

Current LLM analysis is passive: the app pre-fetches 5 fixed news items using a hardcoded query `"{stock_no} {stock_name} 股票"` and hands them to Claude. Claude cannot influence what it searches for or how many results it needs.

## Goal

Let Claude actively call a `search_news` tool during analysis — deciding its own queries, number of results, and whether to search at all — then display the tool call trace in a collapsible expander so users can see what the AI searched and how long each step took.

---

## Architecture

### Data Flow (after)

```
Streamlit
  → analyzer.analyze(df)           # unchanged
  → run_agentic_analysis(result)   # NEW: agentic loop in llm.py
      ├─ send prompt + tool definition to model
      ├─ model calls search_news(query, max_items)
      │     └─ search_news_by_query() in news_fetcher.py
      ├─ (repeat, max 3 rounds, deduplicated queries)
      └─ model outputs final JSON
  → (LLMOutput, tool_trace)        # returned to Streamlit
  → st.expander shows trace
  → tabs show LLMOutput
```

### What changes

| File | Change |
|---|---|
| `app/news_fetcher.py` | Add `search_news_by_query(query, max_items)` — accepts free-form query string |
| `app/llm.py` | Add `async run_agentic_analysis(result) -> tuple[LLMOutput, list[dict]]` |
| `台股分析.py` | Replace `stream_analysis` + pre-fetch news with `asyncio.run(run_agentic_analysis(result))` + expander UI |

### What does NOT change

- `app/analyzer.py` — technical indicator calculation
- `app/twse_client.py` — TWSE data fetching
- `app/models.py` — data structures
- `app/main.py` — FastAPI endpoint (keeps existing `generate_analysis`)
- `pages/比較.py` — multi-stock comparison page (does not use LLM)
- Existing `news_fetcher.fetch_recent_news` — kept for backward compatibility

---

## Tool Definition

Single tool, shared across both providers:

```python
SEARCH_NEWS_TOOL = {
    "name": "search_news",
    "description": "搜尋台股相關新聞標題，用於輔助技術分析。可呼叫多次使用不同關鍵字。",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜尋關鍵字，例如：「台積電 CoWoS 法說會」"
            },
            "max_items": {
                "type": "integer",
                "description": "最多回傳幾則新聞，預設 5，最大 8",
                "default": 5
            }
        },
        "required": ["query"]
    }
}
```

---

## Agentic Loop (`llm.py`)

### Signature

```python
async def run_agentic_analysis(
    result: AnalysisResult,
) -> tuple[LLMOutput, list[dict]]:
    """
    Returns:
        LLMOutput: structured analysis
        list[dict]: tool trace for UI display
            each entry: {"query": str, "count": int, "elapsed_s": float}
    """
```

### Loop logic

1. Build user prompt from `AnalysisResult` (same as `_build_user_prompt`, minus news section)
2. Send to model with `SEARCH_NEWS_TOOL` defined
3. If model returns a tool call:
   - Check if query already searched (deduplicate) — skip if duplicate
   - Execute `search_news_by_query(query, max_items)`
   - Record `{query, count, elapsed_s}` in trace
   - Append tool result to message history
   - Continue loop
4. If model returns final text: parse with `_parse_llm_output`, return
5. **Max 3 tool call rounds** — if limit reached, force-parse whatever the model last returned; fall back to `_fallback_output` if unparseable

### Provider handling

Two private functions handle provider-specific message formats:

- `_run_agentic_anthropic(messages, tools) -> (content, tool_calls)`
- `_run_agentic_github(messages, tools) -> (content, tool_calls)`

`run_agentic_analysis` calls the right one based on `_get_provider()`, keeping the loop logic provider-agnostic.

---

## Duplicate Query Guard

```python
seen_queries: set[str] = set()

# before executing a tool call:
if query in seen_queries:
    # skip, inject empty result message and continue
    continue
seen_queries.add(query)
```

---

## Streamlit UI (`台股分析.py`)

### Replace streaming section with:

```python
loop_start = time.time()
with st.spinner("AI 正在分析中..."):
    llm_output, tool_trace = asyncio.run(run_agentic_analysis(result))
total_elapsed = time.time() - loop_start

if tool_trace:
    with st.expander(f"🔍 AI 搜尋過程（點擊展開）　⏱ 總耗時 {total_elapsed:.1f} 秒"):
        for trace in tool_trace:
            st.caption(
                f"[{trace['elapsed_s']:.1f}s] 搜尋：「{trace['query']}」"
                f"→ 找到 {trace['count']} 則新聞"
            )
```

### Remove from `_fetch_data_and_name`:
- `fetch_recent_news` call (news is now fetched inside agentic loop)
- Return signature changes from `(df, name, news_items)` to `(df, name)`
- All call sites updated accordingly (only one: the `asyncio.run(...)` block in `main()`)

---

## Error Handling

| Situation | Handling |
|---|---|
| `search_news_by_query` network error | Return empty list, record `count=0` in trace, continue loop |
| Max 3 rounds reached without final answer | Force-parse last model response; fall back to `_fallback_output` |
| Model returns malformed JSON | Existing `_parse_llm_output` fallback handles it |
| `比較.py` unaffected | Does not call LLM; no changes needed |

---

## Timing

- Each `tool_trace` entry records `elapsed_s` relative to loop start
- Total elapsed time shown in expander header
- No live timer (UI is blocked during `asyncio.run`) — post-completion display is sufficient

---

## Out of Scope

- MCP protocol (we use Claude's native tool_use / OpenAI function calling directly)
- Adding other tools (financial data, institutional trades) — extensible later by adding to tool list
- Streaming during agentic loop — accepted tradeoff; expander trace compensates UX
- `pages/比較.py` LLM integration — separate future work
