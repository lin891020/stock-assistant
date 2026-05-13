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
