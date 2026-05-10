"""
Google News RSS 抓取模組。

Retrieve 層（RAG pattern）：根據股票代號與名稱，抓取近期財經新聞標題。
不需要 API key，使用 Google News RSS + Python 內建 xml 解析。
任何錯誤靜默回傳空 list，不影響主流程。
"""

import logging
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

logger = logging.getLogger(__name__)

_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
_TIMEOUT = 5.0


async def fetch_recent_news(
    stock_no: str,
    stock_name: str = "",
    max_items: int = 5,
) -> list[dict]:
    """從 Google News RSS 抓取股票相關新聞。

    Args:
        stock_no: 股票代號，例如 "2330"
        stock_name: 股票名稱，例如 "台積電"（可空白）
        max_items: 最多回傳幾則新聞

    Returns:
        list of {"title": str, "source": str, "published": str, "url": str}
        抓取失敗時回傳空 list。
    """
    query = f"{stock_no} {stock_name} 股票".strip()
    url = _RSS_URL.format(query=urllib.parse.quote(query))

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception as exc:
        logger.warning("Failed to fetch news for %s: %s", stock_no, exc)
        return []

    return _parse_rss(resp.text, max_items)


def _parse_rss(xml_text: str, max_items: int) -> list[dict]:
    """解析 Google News RSS XML，回傳新聞列表。"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("RSS XML parse error: %s", exc)
        return []

    items = []
    for item in root.findall(".//item")[:max_items]:
        title = _text(item, "title")
        source = _text(item, "source")
        published = _text(item, "pubDate")
        link = _text(item, "link")

        if title:
            items.append({
                "title": title,
                "source": source,
                "published": published,
                "url": link,
            })

    return items


def _text(element: ET.Element, tag: str) -> str:
    child = element.find(tag)
    return child.text.strip() if child is not None and child.text else ""
