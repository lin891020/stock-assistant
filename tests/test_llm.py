"""Tests for agentic loop functions in app/llm.py."""

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("LLM_PROVIDER", "github")
os.environ.setdefault("GITHUB_TOKEN", "fake-token")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-key")

from app.llm import _agentic_loop_anthropic, _agentic_loop_github
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
        assert len(tool_trace) == 1

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

        assert final_text == ""
        assert len(tool_trace) == 1  # only unique query counted


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
