"""
Claude API integration — generates structured analysis from indicator data.

支援兩個 LLM provider，透過環境變數 LLM_PROVIDER 切換：
  - "anthropic"（預設）：使用 Claude，需要 ANTHROPIC_API_KEY
  - "github"：使用 GitHub Models（OpenAI-compatible），需要 GITHUB_TOKEN
    免費模型，適合開發測試階段

流程：
  1. 將 AnalysisResult 組裝成結構化的 user prompt
  2. 依 provider 呼叫對應 API
  3. 解析回傳的 JSON → LLMOutput；解析失敗時產生 fallback
  4. 支援 streaming 模式（供 Streamlit st.write_stream() 使用）
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Generator

import anthropic
from dotenv import load_dotenv
from openai import OpenAI

from app.models import AnalysisResult, LLMOutput

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

# GitHub Models 使用 Azure OpenAI-compatible endpoint
GITHUB_MODEL = "gpt-4o-mini"
GITHUB_BASE_URL = "https://models.inference.ai.azure.com"


def _get_provider() -> str:
    """從環境變數讀取 provider，預設為 anthropic。

    Returns:
        "anthropic" or "github"

    Raises:
        ValueError: LLM_PROVIDER 設定了不支援的值。
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    if provider not in ("anthropic", "github"):
        raise ValueError(
            f"Unsupported LLM_PROVIDER='{provider}'. Use 'anthropic' or 'github'."
        )
    return provider


# ---------------------------------------------------------------------------
# System prompt — shared across both providers
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """你是一位台股技術分析助理。你的工作是根據使用者提供的量化指標與近期新聞，
用繁體中文撰寫清楚、有依據的分析說明。

【限制】
- 只能根據給定的量化指標和提供的新聞標題進行說明，不能加入任何其他外部資訊
- 不能給予明確的買賣建議，僅說明技術面與新聞面訊號
- 不能引用任何不在輸入資料中的數字
- 嚴禁出現中文、英文以外的語言（包括西班牙文、日文等）

【輸出格式】
請嚴格輸出以下 JSON，不要加入任何其他文字或 markdown 符號：
{
  "verdict": "值得關注 或 暫不關注",
  "confidence": "高 或 中 或 低",
  "key_signals": ["訊號1", "訊號2", ...],
  "risks": ["風險1", ...],
  "summary": "約 400-500 字的中文說明",
  "next_actions": ["建議觀察點1", "建議觀察點2", ...]
}

【next_actions 撰寫要求】
- 1–3 點具體可執行的後續觀察建議，幫助使用者知道接下來要關注什麼
- 必須根據當前指標數值給出有針對性的建議（例如：RSI 接近 70 → 觀察是否回落）
- 不能給買賣建議，只能建議「觀察什麼」、「注意什麼訊號」

【信心程度判斷標準】
- 高：≥ 3 項指標分數達到滿分
- 中：1–2 項指標分數達到滿分
- 低：所有指標均未達滿分，或有衝突訊號

【summary 撰寫要求】
- 說明各指標數值代表的意義
- 說明支撐結論的主要理由
- 提醒主要風險
- 約 400–500 字，語氣客觀
- 如果有提供新聞標題，summary 中必須至少明確引用一則，格式：「根據《新聞標題》...」"""


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_user_prompt(result: AnalysisResult, news_items: list[dict] | None = None) -> str:
    """將 AnalysisResult 轉換為結構化的 user prompt 文字。

    Args:
        result: analyzer.analyze() 回傳的分析結果
        news_items: 近期新聞列表（可選），來自 news_fetcher.fetch_recent_news()

    Returns:
        結構化的 prompt 字串
    """
    s = result.scores
    lines = [
        f"股票代號：{result.stock_no}",
        f"資料區間：{result.start_date} 至 {result.end_date}",
        f"總評分：{result.total_score} / 100（門檻 60 分）",
        f"系統結論：{result.verdict}",
        "",
        "【各指標明細】",
        f"1. MA 黃金交叉（25分）：得分 {s.ma_crossover_score}",
        f"   - 5日MA = {result.ma5}，20日MA = {result.ma20}",
        f"2. 成交量放大（25分）：得分 {s.volume_surge_score}",
        f"   - 近5日均量 / 近20日均量 = {s.volume_surge_ratio:.2f} 倍",
        f"3. 價格趨勢（20分）：得分 {s.price_trend_score}",
        f"   - 近30日漲跌幅 = {s.price_trend_pct * 100:.2f}%",
        f"4. RSI 14日（20分）：得分 {s.rsi_score}",
        f"   - RSI = {s.rsi}（健康區間 45–65）",
        f"5. 價格穩定性（10分）：得分 {s.stability_score}",
        f"   - 變異係數 = {s.stability_cv:.4f}（門檻 < 0.05）",
    ]

    if news_items:
        lines += ["", "【近期相關新聞】（僅供參考，請結合技術指標綜合說明）"]
        for i, news in enumerate(news_items, 1):
            lines.append(f"{i}. {news['title']}（{news['source']}，{news['published']}）")

    lines += ["", "請依照系統 prompt 指定的 JSON 格式輸出分析結果。"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Output parser
# ---------------------------------------------------------------------------

def _parse_llm_output(raw_text: str, result: AnalysisResult) -> LLMOutput:
    """解析 LLM 回傳的 JSON 文字，失敗時回傳 fallback。

    Args:
        raw_text: LLM 完整回傳文字
        result: 原始分析結果（供 fallback 使用）

    Returns:
        LLMOutput，無論解析成功或失敗都保證回傳有效物件。
    """
    try:
        data = json.loads(raw_text.strip())
        return LLMOutput(**data)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse LLM JSON output, using fallback: %s", exc)
        return _fallback_output(result)


def _fallback_output(result: AnalysisResult) -> LLMOutput:
    """當 LLM JSON 解析失敗時，由 Python 直接產生最小可用輸出。"""
    return LLMOutput(
        verdict=result.verdict,
        confidence="低",
        key_signals=["（LLM 輸出解析失敗，以下為系統自動產生）"],
        risks=["建議重新查詢，或直接參考上方量化指標"],
        summary=(
            f"股票 {result.stock_no} 的量化評分為 {result.total_score} 分，"
            f"系統判定為「{result.verdict}」。"
            "LLM 說明產生失敗，請參考各指標得分明細。"
        ),
        next_actions=[],
    )


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

def _generate_anthropic(user_prompt: str) -> str:
    """呼叫 Anthropic Claude API，回傳完整文字。

    System prompt 加上 cache_control，在 5 分鐘內相同 prompt 不重複計費。
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
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
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


def _stream_anthropic(user_prompt: str) -> Generator[str, None, None]:
    """呼叫 Anthropic Claude API（streaming），yield 文字片段。"""
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    with client.messages.stream(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_prompt}],
    ) as stream:
        for text in stream.text_stream:
            yield text


def _generate_github(user_prompt: str) -> str:
    """呼叫 GitHub Models API（OpenAI-compatible），回傳完整文字。

    使用 GITHUB_TOKEN 作為 API key，base_url 指向 Azure inference endpoint。
    """
    client = OpenAI(
        api_key=os.environ["GITHUB_TOKEN"],
        base_url=GITHUB_BASE_URL,
    )
    response = client.chat.completions.create(
        model=GITHUB_MODEL,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content


def _stream_github(user_prompt: str) -> Generator[str, None, None]:
    """呼叫 GitHub Models API（streaming），yield 文字片段。"""
    client = OpenAI(
        api_key=os.environ["GITHUB_TOKEN"],
        base_url=GITHUB_BASE_URL,
    )
    stream = client.chat.completions.create(
        model=GITHUB_MODEL,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        stream=True,
    )
    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_analysis(result: AnalysisResult, news_items: list[dict] | None = None) -> LLMOutput:
    """呼叫 LLM，回傳結構化分析結果（非串流版本，供 FastAPI 使用）。

    Args:
        result: analyzer.analyze() 回傳的分析結果
        news_items: 近期新聞列表（可選）

    Returns:
        LLMOutput，解析失敗時回傳 fallback。
    """
    provider = _get_provider()
    user_prompt = _build_user_prompt(result, news_items)

    logger.info("Generating analysis via provider: %s", provider)

    if provider == "anthropic":
        raw_text = _generate_anthropic(user_prompt)
    else:
        raw_text = _generate_github(user_prompt)

    return _parse_llm_output(raw_text, result)


def stream_analysis(result: AnalysisResult, news_items: list[dict] | None = None) -> Generator[str, None, None]:
    """呼叫 LLM 並以串流方式 yield 文字片段（供 Streamlit 使用）。

    Args:
        result: analyzer.analyze() 回傳的分析結果
        news_items: 近期新聞列表（可選）

    Yields:
        LLM 回傳的文字片段（str）
    """
    provider = _get_provider()
    user_prompt = _build_user_prompt(result, news_items)

    logger.info("Streaming analysis via provider: %s", provider)

    if provider == "anthropic":
        yield from _stream_anthropic(user_prompt)
    else:
        yield from _stream_github(user_prompt)
