# Agentic News Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace passive news pre-fetching with an agentic tool_use loop where the LLM picks its own search queries, then display the trace in a collapsible expander with per-step timing.

**Architecture:** `news_fetcher.py` gains a free-form query function; `llm.py` gains two provider-specific agentic loops (`_agentic_loop_github`, `_agentic_loop_anthropic`) and a public `run_agentic_analysis` coordinator; `台股分析.py` swaps `stream_analysis` for `asyncio.run(run_agentic_analysis(...))` and renders an expander.

**Tech Stack:** Python asyncio, httpx (RSS), anthropic SDK (tool_use), openai SDK (function calling), Streamlit st.expander, pytest-asyncio + unittest.mock

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/news_fetcher.py` | Modify | Add `search_news_by_query(query, max_items)` |
| `app/llm.py` | Modify | Add tool constants, `_build_agentic_user_prompt`, `_agentic_loop_github`, `_agentic_loop_anthropic`, `run_agentic_analysis` |
| `台股分析.py` | Modify | Remove news pre-fetch; replace streaming with spinner + expander |
| `tests/test_news_fetcher.py` | Create | Tests for `search_news_by_query` |
| `tests/test_llm.py` | Create | Tests for agentic loop functions |

---

## Task 1: `search_news_by_query` in `news_fetcher.py`

**Files:**
- Modify: `app/news_fetcher.py`
- Create: `tests/test_news_fetcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_news_fetcher.py`:

```python
"""Tests for app/news_fetcher.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.news_fetcher import search_news_by_query


_FAKE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss><channel>
  <item>
    <title>台積電法說會：AI 需求強勁</title>
    <source>經濟日報</source>
    <pubDate>Tue, 13 May 2026 08:00:00 GMT</pubDate>
    <link>https://example.com/news/1</link>
  </item>
  <item>
    <title>外資加碼台積電</title>
    <source>工商時報</source>
    <pubDate>Mon, 12 May 2026 10:00:00 GMT</pubDate>
    <link>https://example.com/news/2</link>
  </item>
</channel></rss>"""


def _make_mock_client(text: str):
    mock_resp = MagicMock()
    mock_resp.text = text
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)
    return mock_client


@pytest.mark.asyncio
class TestSearchNewsByQuery:
    async def test_returns_list_of_dicts(self):
        """回傳格式應為 list of dict，含 title/source/published/url。"""
        with patch("app.news_fetcher.httpx.AsyncClient", return_value=_make_mock_client(_FAKE_RSS)):
            result = await search_news_by_query("台積電 法說會")
        assert isinstance(result, list)
        assert all({"title", "source", "published", "url"} <= item.keys() for item in result)

    async def test_uses_query_directly(self):
        """傳入的 query 應直接 URL-encode 後放入請求 URL，不再附加固定字串。"""
        import urllib.parse
        captured_url = []

        async def fake_get(url, **kwargs):
            captured_url.append(url)
            mock_resp = MagicMock()
            mock_resp.text = "<rss><channel></channel></rss>"
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = fake_get

        with patch("app.news_fetcher.httpx.AsyncClient", return_value=mock_client):
            await search_news_by_query("台積電 CoWoS")

        assert len(captured_url) == 1
        assert urllib.parse.quote("台積電 CoWoS") in captured_url[0]

    async def test_max_items_respected(self):
        """max_items 應限制回傳筆數。"""
        with patch("app.news_fetcher.httpx.AsyncClient", return_value=_make_mock_client(_FAKE_RSS)):
            result = await search_news_by_query("台積電", max_items=1)
        assert len(result) == 1

    async def test_network_error_returns_empty_list(self):
        """網路錯誤應靜默回傳空 list，不拋例外。"""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))

        with patch("app.news_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await search_news_by_query("台積電")
        assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "/Users/lin1020/Projects/Stock Assistant/stock-assistant"
pytest tests/test_news_fetcher.py -v
```

Expected: `ImportError` or `AttributeError` — `search_news_by_query` does not exist yet.

- [ ] **Step 3: Implement `search_news_by_query`**

Add to `app/news_fetcher.py`, after the existing `fetch_recent_news` function:

```python
async def search_news_by_query(query: str, max_items: int = 5) -> list[dict]:
    """從 Google News RSS 搜尋新聞，接受自由關鍵字字串。

    供 LLM agentic loop 使用；query 由 LLM 自行決定，不再固定格式。
    任何錯誤靜默回傳空 list，不影響主流程。

    Args:
        query: 搜尋關鍵字，例如 "台積電 CoWoS 法說會"
        max_items: 最多回傳幾則

    Returns:
        list of {"title": str, "source": str, "published": str, "url": str}
    """
    url = _RSS_URL.format(query=urllib.parse.quote(query))
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch news for query '%s': %s", query, exc)
        return []
    return _parse_rss(resp.text, max_items)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_news_fetcher.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/news_fetcher.py tests/test_news_fetcher.py
git commit -m "feat: add search_news_by_query to news_fetcher"
```

---

## Task 2: Tool constants and prompt builder in `llm.py`

**Files:**
- Modify: `app/llm.py`

No tests needed — these are pure data constants and a string formatter.

- [ ] **Step 1: Add constants after the existing provider config block**

In `app/llm.py`, after the line `GITHUB_BASE_URL = "https://models.inference.ai.azure.com"`, add:

```python
MAX_TOOL_ROUNDS = 3  # agentic loop upper bound

_SEARCH_NEWS_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": "搜尋台股相關新聞標題，用於輔助技術分析。可呼叫多次使用不同關鍵字。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵字，例如：「台積電 CoWoS 法說會」",
                },
                "max_items": {
                    "type": "integer",
                    "description": "最多回傳幾則新聞，預設 5，最大 8",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}

_SEARCH_NEWS_TOOL_ANTHROPIC = {
    "name": "search_news",
    "description": "搜尋台股相關新聞標題，用於輔助技術分析。可呼叫多次使用不同關鍵字。",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜尋關鍵字，例如：「台積電 CoWoS 法說會」",
            },
            "max_items": {
                "type": "integer",
                "description": "最多回傳幾則新聞，預設 5，最大 8",
            },
        },
        "required": ["query"],
    },
}
```

- [ ] **Step 2: Add `import time` and `import json` at top of `llm.py`**

`llm.py` already imports `json`. Add `import time` after the existing imports block (after `import os`):

```python
import time
```

- [ ] **Step 3: Add `_build_agentic_user_prompt` after the existing `_build_user_prompt` function**

```python
def _build_agentic_user_prompt(result: AnalysisResult) -> str:
    """User prompt for agentic loop — same as _build_user_prompt but instructs tool use.

    Does not pre-include news; the model fetches news via search_news tool calls.
    """
    base = _build_user_prompt(result, news_items=None)
    return (
        base
        + "\n\n【工具使用說明】\n"
        "你有 search_news 工具可以主動搜尋新聞。"
        "請先搜尋 1–2 次與此股票近期相關的新聞，再生成分析結果。"
    )
```

- [ ] **Step 4: Add `search_news_by_query` import at top of `llm.py`**

After the existing `from app.models import AnalysisResult, LLMOutput` line, add:

```python
from app.news_fetcher import search_news_by_query
```

- [ ] **Step 5: Verify imports work**

```bash
cd "/Users/lin1020/Projects/Stock Assistant/stock-assistant"
python -c "from app.llm import _build_agentic_user_prompt; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/llm.py
git commit -m "feat: add tool constants and agentic prompt builder to llm"
```

---

## Task 3: `_agentic_loop_github` in `llm.py`

**Files:**
- Modify: `app/llm.py`
- Create: `tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_llm.py`:

```python
"""Tests for agentic loop functions in app/llm.py."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure github provider is used for these tests
os.environ.setdefault("LLM_PROVIDER", "github")
os.environ.setdefault("GITHUB_TOKEN", "fake-token")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-key")

from app.llm import _agentic_loop_github
from app.models import AnalysisResult, IndicatorScores


def _fake_result() -> AnalysisResult:
    scores = IndicatorScores(
        ma_crossover_score=25,
        volume_surge_ratio=1.5,
        volume_surge_score=15,
        price_trend_pct=0.05,
        price_trend_score=10,
        rsi=55.0,
        rsi_score=20,
        stability_cv=0.03,
        stability_score=10,
    )
    return AnalysisResult(
        stock_no="2330",
        start_date="2024-01-02",
        end_date="2024-03-15",
        scores=scores,
        total_score=80,
        verdict="值得關注",
        ma5=580.0,
        ma20=560.0,
        closes=[550.0, 560.0, 568.0],
        volumes=[10_000_000.0, 12_000_000.0, 11_000_000.0],
        dates=["2024-01-02", "2024-01-03", "2024-01-04"],
        ma5_series=[552.0, 558.0, 562.0],
        ma20_series=[540.0, 545.0, 550.0],
    )


def _make_openai_tool_call_response(query: str, call_id: str = "call_abc"):
    """Build a mock OpenAI response that requests a tool call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = "search_news"
    tc.function.arguments = json.dumps({"query": query, "max_items": 5})

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message.content = None
    choice.message.tool_calls = [tc]

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_openai_text_response(text: str):
    """Build a mock OpenAI response with final text (no tool calls)."""
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.content = text
    choice.message.tool_calls = None

    resp = MagicMock()
    resp.choices = [choice]
    return resp


_VALID_JSON = json.dumps({
    "verdict": "值得關注",
    "confidence": "高",
    "key_signals": ["MA 黃金交叉"],
    "risks": ["成交量略低"],
    "summary": "測試摘要",
    "next_actions": ["觀察成交量變化"],
})


@pytest.mark.asyncio
class TestAgenticLoopGithub:
    async def test_one_tool_call_then_final_answer(self):
        """Model 呼叫一次 search_news 後回傳最終文字，trace 應有一筆記錄。"""
        tool_resp = _make_openai_tool_call_response("台積電 法說會", call_id="call_1")
        final_resp = _make_openai_text_response(_VALID_JSON)

        mock_create = MagicMock(side_effect=[tool_resp, final_resp])
        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        with (
            patch("app.llm.OpenAI", return_value=mock_client),
            patch("app.llm.search_news_by_query", new_callable=AsyncMock, return_value=[{"title": "test"}]),
        ):
            final_text, tool_trace = await _agentic_loop_github("test prompt")

        assert final_text == _VALID_JSON
        assert len(tool_trace) == 1
        assert tool_trace[0]["query"] == "台積電 法說會"
        assert tool_trace[0]["count"] == 1

    async def test_duplicate_query_skipped(self):
        """同一 query 重複呼叫時，search_news_by_query 只執行一次。"""
        tool_resp1 = _make_openai_tool_call_response("台積電", call_id="call_1")
        tool_resp2 = _make_openai_tool_call_response("台積電", call_id="call_2")
        final_resp = _make_openai_text_response(_VALID_JSON)

        mock_create = MagicMock(side_effect=[tool_resp1, tool_resp2, final_resp])
        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        mock_search = AsyncMock(return_value=[])
        with (
            patch("app.llm.OpenAI", return_value=mock_client),
            patch("app.llm.search_news_by_query", mock_search),
        ):
            _, tool_trace = await _agentic_loop_github("test prompt")

        assert mock_search.call_count == 1
        assert len(tool_trace) == 1  # only the first unique query

    async def test_max_rounds_returns_last_text(self):
        """超過 MAX_TOOL_ROUNDS 輪後，回傳最後一次有效文字（即使 tool_calls）。"""
        tool_resp = _make_openai_tool_call_response("台積電", call_id="call_x")

        mock_create = MagicMock(side_effect=[tool_resp, tool_resp, tool_resp])
        mock_client = MagicMock()
        mock_client.chat.completions.create = mock_create

        with (
            patch("app.llm.OpenAI", return_value=mock_client),
            patch("app.llm.search_news_by_query", new_callable=AsyncMock, return_value=[]),
        ):
            final_text, tool_trace = await _agentic_loop_github("test prompt")

        assert final_text == ""  # no text was ever returned
        assert len(tool_trace) == 1  # only unique query counted
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd "/Users/lin1020/Projects/Stock Assistant/stock-assistant"
pytest tests/test_llm.py -v
```

Expected: `ImportError` — `_agentic_loop_github` not yet defined.

- [ ] **Step 3: Implement `_agentic_loop_github` in `llm.py`**

Add after `_stream_github`, before the `# Public API` section:

```python
async def _agentic_loop_github(user_prompt: str) -> tuple[str, list[dict]]:
    """GitHub Models agentic loop using OpenAI function calling.

    Returns:
        (final_text, tool_trace)
        tool_trace entries: {"query": str, "count": int, "elapsed_s": float}
    """
    client = OpenAI(api_key=os.environ["GITHUB_TOKEN"], base_url=GITHUB_BASE_URL)
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    tool_trace: list[dict] = []
    seen_queries: set[str] = set()
    loop_start = time.time()
    last_text = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(
            model=GITHUB_MODEL,
            max_tokens=1024,
            messages=messages,
            tools=[_SEARCH_NEWS_TOOL_OPENAI],
            tool_choice="auto",
        )
        choice = response.choices[0]

        if choice.finish_reason != "tool_calls":
            last_text = choice.message.content or ""
            return last_text, tool_trace

        # Append assistant turn with tool calls
        messages.append({
            "role": "assistant",
            "content": choice.message.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ],
        })

        # Execute each tool call
        for tc in choice.message.tool_calls:
            args = json.loads(tc.function.arguments)
            query: str = args["query"]
            max_items: int = min(args.get("max_items", 5), 8)

            if query not in seen_queries:
                seen_queries.add(query)
                elapsed = time.time() - loop_start
                news_items = await search_news_by_query(query, max_items)
                tool_trace.append({
                    "query": query,
                    "count": len(news_items),
                    "elapsed_s": round(elapsed, 1),
                })
                result_content = json.dumps(news_items, ensure_ascii=False)
            else:
                result_content = "[]"

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result_content,
            })

    return last_text, tool_trace
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_llm.py::TestAgenticLoopGithub -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/llm.py tests/test_llm.py
git commit -m "feat: add _agentic_loop_github with tool_use and dedup"
```

---

## Task 4: `_agentic_loop_anthropic` in `llm.py`

**Files:**
- Modify: `app/llm.py`
- Modify: `tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm.py`:

```python
from app.llm import _agentic_loop_anthropic


def _make_anthropic_tool_use_response(query: str, block_id: str = "block_abc"):
    """Build a mock Anthropic response that requests a tool_use call."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = block_id
    tool_block.name = "search_news"
    tool_block.input = {"query": query, "max_items": 5}

    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [tool_block]
    return resp


def _make_anthropic_text_response(text: str):
    """Build a mock Anthropic response with final text."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [text_block]
    return resp


@pytest.mark.asyncio
class TestAgenticLoopAnthropic:
    async def test_one_tool_call_then_final_answer(self):
        """Model 呼叫一次 search_news 後回傳最終文字，trace 應有一筆記錄。"""
        tool_resp = _make_anthropic_tool_use_response("台積電 CoWoS", block_id="b1")
        final_resp = _make_anthropic_text_response(_VALID_JSON)

        mock_create = MagicMock(side_effect=[tool_resp, final_resp])
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        with (
            patch("app.llm.anthropic.Anthropic", return_value=mock_client),
            patch("app.llm.search_news_by_query", new_callable=AsyncMock, return_value=[{"title": "t"}]),
        ):
            final_text, tool_trace = await _agentic_loop_anthropic("test prompt")

        assert final_text == _VALID_JSON
        assert len(tool_trace) == 1
        assert tool_trace[0]["query"] == "台積電 CoWoS"
        assert tool_trace[0]["count"] == 1

    async def test_duplicate_query_skipped(self):
        """同一 query 重複呼叫時，search_news_by_query 只執行一次。"""
        tool_resp1 = _make_anthropic_tool_use_response("台積電", block_id="b1")
        tool_resp2 = _make_anthropic_tool_use_response("台積電", block_id="b2")
        final_resp = _make_anthropic_text_response(_VALID_JSON)

        mock_create = MagicMock(side_effect=[tool_resp1, tool_resp2, final_resp])
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        mock_search = AsyncMock(return_value=[])
        with (
            patch("app.llm.anthropic.Anthropic", return_value=mock_client),
            patch("app.llm.search_news_by_query", mock_search),
        ):
            _, tool_trace = await _agentic_loop_anthropic("test prompt")

        assert mock_search.call_count == 1
        assert len(tool_trace) == 1

    async def test_max_rounds_returns_empty_string(self):
        """超過 MAX_TOOL_ROUNDS 後回傳空字串（讓呼叫方走 fallback）。"""
        tool_resp = _make_anthropic_tool_use_response("台積電", block_id="bx")

        mock_create = MagicMock(side_effect=[tool_resp, tool_resp, tool_resp])
        mock_client = MagicMock()
        mock_client.messages.create = mock_create

        with (
            patch("app.llm.anthropic.Anthropic", return_value=mock_client),
            patch("app.llm.search_news_by_query", new_callable=AsyncMock, return_value=[]),
        ):
            final_text, _ = await _agentic_loop_anthropic("test prompt")

        assert final_text == ""
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_llm.py::TestAgenticLoopAnthropic -v
```

Expected: `ImportError` — `_agentic_loop_anthropic` not yet defined.

- [ ] **Step 3: Implement `_agentic_loop_anthropic` in `llm.py`**

Add immediately after `_agentic_loop_github`:

```python
async def _agentic_loop_anthropic(user_prompt: str) -> tuple[str, list[dict]]:
    """Anthropic Claude agentic loop using tool_use.

    Returns:
        (final_text, tool_trace)
        tool_trace entries: {"query": str, "count": int, "elapsed_s": float}
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    messages: list[dict] = [{"role": "user", "content": user_prompt}]
    tool_trace: list[dict] = []
    seen_queries: set[str] = set()
    loop_start = time.time()
    last_text = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=messages,
            tools=[_SEARCH_NEWS_TOOL_ANTHROPIC],
        )

        if response.stop_reason != "tool_use":
            last_text = next(
                (b.text for b in response.content if b.type == "text"), ""
            )
            return last_text, tool_trace

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # Execute tool calls, collect results for one user turn
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            query: str = block.input["query"]
            max_items: int = min(block.input.get("max_items", 5), 8)

            if query not in seen_queries:
                seen_queries.add(query)
                elapsed = time.time() - loop_start
                news_items = await search_news_by_query(query, max_items)
                tool_trace.append({
                    "query": query,
                    "count": len(news_items),
                    "elapsed_s": round(elapsed, 1),
                })
                result_content = json.dumps(news_items, ensure_ascii=False)
            else:
                result_content = "[]"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_content,
            })

        messages.append({"role": "user", "content": tool_results})

    return last_text, tool_trace
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_llm.py::TestAgenticLoopAnthropic -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/llm.py tests/test_llm.py
git commit -m "feat: add _agentic_loop_anthropic with tool_use and dedup"
```

---

## Task 5: `run_agentic_analysis` (public API) in `llm.py`

**Files:**
- Modify: `app/llm.py`
- Modify: `tests/test_llm.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm.py`:

```python
from app.llm import run_agentic_analysis
from app.models import LLMOutput


@pytest.mark.asyncio
class TestRunAgenticAnalysis:
    async def test_routes_to_github_when_provider_github(self):
        """LLM_PROVIDER=github 時應呼叫 _agentic_loop_github。"""
        fake_output = LLMOutput(
            verdict="值得關注",
            confidence="高",
            key_signals=["MA 黃金交叉"],
            risks=["成交量略低"],
            summary="測試",
            next_actions=[],
        )
        fake_trace = [{"query": "台積電", "count": 2, "elapsed_s": 1.0}]

        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "github"}),
            patch("app.llm._agentic_loop_github", new_callable=AsyncMock,
                  return_value=(_VALID_JSON, fake_trace)) as mock_gh,
            patch("app.llm._agentic_loop_anthropic", new_callable=AsyncMock) as mock_an,
        ):
            llm_output, trace = await run_agentic_analysis(_fake_result())

        mock_gh.assert_called_once()
        mock_an.assert_not_called()
        assert isinstance(llm_output, LLMOutput)
        assert llm_output.verdict == "值得關注"
        assert trace == fake_trace

    async def test_routes_to_anthropic_when_provider_anthropic(self):
        """LLM_PROVIDER=anthropic 時應呼叫 _agentic_loop_anthropic。"""
        fake_trace: list[dict] = []

        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "anthropic"}),
            patch("app.llm._agentic_loop_github", new_callable=AsyncMock) as mock_gh,
            patch("app.llm._agentic_loop_anthropic", new_callable=AsyncMock,
                  return_value=(_VALID_JSON, fake_trace)) as mock_an,
        ):
            llm_output, trace = await run_agentic_analysis(_fake_result())

        mock_an.assert_called_once()
        mock_gh.assert_not_called()
        assert isinstance(llm_output, LLMOutput)

    async def test_fallback_on_empty_final_text(self):
        """LLM 回傳空字串（max rounds 耗盡）時，應回傳 fallback LLMOutput。"""
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "github"}),
            patch("app.llm._agentic_loop_github", new_callable=AsyncMock,
                  return_value=("", [])),
        ):
            llm_output, _ = await run_agentic_analysis(_fake_result())

        assert isinstance(llm_output, LLMOutput)
        assert llm_output.confidence == "低"  # fallback always sets confidence to 低
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_llm.py::TestRunAgenticAnalysis -v
```

Expected: `ImportError` — `run_agentic_analysis` not yet defined.

- [ ] **Step 3: Implement `run_agentic_analysis` in `llm.py`**

Add in the `# Public API` section, after `stream_analysis`:

```python
async def run_agentic_analysis(
    result: AnalysisResult,
) -> tuple[LLMOutput, list[dict]]:
    """Agentic analysis: LLM calls search_news tool, returns structured output + trace.

    Args:
        result: analyzer.analyze() output (technical indicator data)

    Returns:
        (LLMOutput, tool_trace)
        tool_trace: list of {"query": str, "count": int, "elapsed_s": float}
    """
    provider = _get_provider()
    user_prompt = _build_agentic_user_prompt(result)

    logger.info("Running agentic analysis via provider: %s", provider)

    if provider == "github":
        final_text, tool_trace = await _agentic_loop_github(user_prompt)
    else:
        final_text, tool_trace = await _agentic_loop_anthropic(user_prompt)

    return _parse_llm_output(final_text, result), tool_trace
```

- [ ] **Step 4: Run all tests to verify nothing regressed**

```bash
pytest tests/ -v
```

Expected: all tests pass (including existing 33 tests).

- [ ] **Step 5: Commit**

```bash
git add app/llm.py tests/test_llm.py
git commit -m "feat: add run_agentic_analysis public API"
```

---

## Task 6: Update `台股分析.py` UI

**Files:**
- Modify: `台股分析.py`

No unit tests — Streamlit UI is verified manually.

- [ ] **Step 1: Update imports at the top of `台股分析.py`**

Remove these imports:
```python
import json
from app.llm import stream_analysis
from app.models import LLMOutput
from app.news_fetcher import fetch_recent_news
```

Add / keep:
```python
import time  # add this line after "import asyncio"
from app.llm import run_agentic_analysis  # replaces stream_analysis
```

The final imports block should look like:

```python
import asyncio
import os
import time

import plotly.graph_objects as go
import streamlit as st
from dotenv import load_dotenv
from streamlit_searchbox import st_searchbox

from app import analyzer
from app.llm import run_agentic_analysis
from app.twse_client import (
    InsufficientDataError,
    StockNotFoundError,
    TWSEUnavailableError,
    fetch_stock_data,
    fetch_stock_list,
    fetch_stock_name,
)
```

- [ ] **Step 2: Simplify `_fetch_data_and_name`**

Replace the entire `_fetch_data_and_name` function with:

```python
async def _fetch_data_and_name(stock_no: str):
    """並發抓取股票資料與名稱。"""
    async def safe_fetch_name(sno: str) -> str:
        try:
            return await fetch_stock_name(sno)
        except Exception:
            return ""

    df, name = await asyncio.gather(
        fetch_stock_data(stock_no),
        safe_fetch_name(stock_no),
    )
    return df, name
```

- [ ] **Step 3: Update the call site in `main()`**

Find this block:

```python
    with st.spinner("正在從 TWSE 抓取資料..."):
        try:
            df, stock_name, news_items = asyncio.run(_fetch_data_and_name(stock_no))
```

Replace with:

```python
    with st.spinner("正在從 TWSE 抓取資料..."):
        try:
            df, stock_name = asyncio.run(_fetch_data_and_name(stock_no))
```

- [ ] **Step 4: Replace the LLM streaming section in `main()`**

Find and remove this entire block:

```python
    stream_placeholder = st.empty()
    chunks: list[str] = []
    for chunk in stream_analysis(result, news_items or None):
        chunks.append(chunk)
        stream_placeholder.markdown("".join(chunks) + "▌")
    raw_output = "".join(chunks)
    stream_placeholder.empty()

    try:
        llm = LLMOutput(**json.loads(raw_output))
    except Exception:
        st.markdown(raw_output)
        return
```

Replace with:

```python
    loop_start = time.time()
    with st.spinner("AI 正在分析中..."):
        llm, tool_trace = asyncio.run(run_agentic_analysis(result))
    total_elapsed = time.time() - loop_start

    if tool_trace:
        with st.expander(f"🔍 AI 搜尋過程（點擊展開）　⏱ 總耗時 {total_elapsed:.1f} 秒"):
            for trace in tool_trace:
                st.caption(
                    f"[{trace['elapsed_s']:.1f}s] 搜尋：「{trace['query']}」"
                    f"→ 找到 {trace['count']} 則新聞"
                )
```

- [ ] **Step 5: Remove news expander from `tab_retail`**

In `tab_retail`, find and remove this entire block (news are now shown in the agentic expander above):

```python
        if news_items:
            with st.expander("📰 參考新聞來源"):
                for news in news_items:
                    url = news.get("url", "")
                    title = news["title"]
                    source = news.get("source", "")
                    published = news.get("published", "")
                    label = f"{title}　{source}　{published}"
                    if url:
                        st.markdown(f"- [{label}]({url})")
                    else:
                        st.markdown(f"- {label}")
```

- [ ] **Step 6: Verify syntax is valid**

```bash
cd "/Users/lin1020/Projects/Stock Assistant/stock-assistant"
python -m py_compile 台股分析.py && echo "OK"
```

Expected: `OK`

- [ ] **Step 7: Run full test suite to confirm no regressions**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add 台股分析.py
git commit -m "feat: replace stream_analysis with run_agentic_analysis in Streamlit UI"
```

---

## Task 7: Manual smoke test

**No code changes** — verify the end-to-end flow works with the real app.

- [ ] **Step 1: Set env vars for GitHub Models (free tier)**

Ensure `.env` contains:
```
LLM_PROVIDER=github
GITHUB_TOKEN=<your token>
```

- [ ] **Step 2: Launch the app**

```bash
cd "/Users/lin1020/Projects/Stock Assistant/stock-assistant"
streamlit run 台股分析.py
```

- [ ] **Step 3: Test golden path**

1. Search for `2330` (台積電)
2. Select it from the dropdown
3. Observe spinner: "AI 正在分析中..."
4. After completion, verify:
   - `🔍 AI 搜尋過程（點擊展開）` expander appears with timing
   - Clicking expander shows search queries and news counts
   - `⏱ 總耗時 X.X 秒` appears in expander header
   - Both 散戶模式 / 法人模式 tabs render correctly

- [ ] **Step 4: Test fallback (no tool calls)**

If the model returns final JSON without calling any tool, verify:
- Expander does NOT appear (tool_trace is empty)
- Analysis still renders correctly

- [ ] **Step 5: Final commit with version tag**

```bash
git add -p  # review any remaining changes
git commit -m "chore: verify agentic news search end-to-end"
```
