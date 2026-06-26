"""Tests for fetch_page_content — parallel page content fetching."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _import_fetcher():
    """Lazy-import content_fetcher module."""
    from deepresearch.tools.content_fetcher import (
        _strip_html,
        _extract_title,
        fetch_page_content,
    )

    return {
        "fetch_page_content": fetch_page_content,
        "_strip_html": _strip_html,
        "_extract_title": _extract_title,
    }


# ponytail: inline httpx mocks for readability — each test owns its response data.
# Ceiling: 14+ repetitions. Upgrade path: extract to conftest fixture when 20+.
# NOTE: Mocking httpx.AsyncClient.get (class-level) works because Python binds
# `self` as the first arg, which the side_effect functions ignore.


class TestContentFetcher:
    """fetch_page_content — parallel page content fetching."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self) -> None:
        F = _import_fetcher()
        mock_response = MagicMock()
        mock_response.text = (
            "<html><title>Test</title><body><p>Hello world</p></body></html>"
        )
        mock_response.raise_for_status.return_value = None

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            results = await F["fetch_page_content"](["https://example.com"])

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["url"] == "https://example.com"
        assert results[0]["title"] == "Test"
        assert "Hello world" in results[0]["content"]
        assert results[0]["error"] is None

    @pytest.mark.asyncio
    async def test_failed_url_returns_error(self) -> None:
        F = _import_fetcher()
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found", request=MagicMock(), response=MagicMock()
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            results = await F["fetch_page_content"](["https://example.com/404"])

        assert len(results) == 1
        assert results[0]["url"] == "https://example.com/404"
        assert results[0]["error"] is not None
        assert "HTTPStatusError" in results[0]["error"]
        assert results[0]["title"] is None
        assert results[0]["content"] is None

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self) -> None:
        F = _import_fetcher()
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Connection timed out")
            results = await F["fetch_page_content"](["https://slow.example.com"])

        assert len(results) == 1
        assert results[0]["error"] is not None
        assert "TimeoutException" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self) -> None:
        F = _import_fetcher()
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.ConnectError("Connection refused")
            results = await F["fetch_page_content"](["https://unreachable.example.com"])

        assert len(results) == 1
        assert results[0]["error"] is not None
        assert "ConnectError" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_content_truncation(self) -> None:
        F = _import_fetcher()
        long_content = "Hello world " * 500
        mock_response = MagicMock()
        mock_response.text = f"<html><body><p>{long_content}</p></body></html>"
        mock_response.raise_for_status.return_value = None

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response
            results = await F["fetch_page_content"](
                ["https://example.com"], max_chars=100
            )

        assert len(results) == 1
        assert results[0]["content"] is not None
        assert len(results[0]["content"]) <= 100

    @pytest.mark.asyncio
    async def test_empty_urls_list(self) -> None:
        F = _import_fetcher()
        results = await F["fetch_page_content"]([])
        assert results == []

    @pytest.mark.asyncio
    async def test_mixed_success_failure(self) -> None:
        F = _import_fetcher()

        async def mock_get_side_effect(url, **kwargs):
            if "good" in str(url):
                mock_resp = MagicMock()
                mock_resp.text = (
                    "<html><title>Good</title><body><p>OK</p></body></html>"
                )
                mock_resp.raise_for_status.return_value = None
                return mock_resp
            else:
                raise httpx.HTTPStatusError(
                    "500", request=MagicMock(), response=MagicMock()
                )

        from httpx import AsyncClient

        with patch.object(AsyncClient, "get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = mock_get_side_effect
            results = await F["fetch_page_content"](
                ["https://good.example.com", "https://bad.example.com"]
            )

        assert len(results) == 2
        success = [r for r in results if r["error"] is None]
        failures = [r for r in results if r["error"] is not None]
        assert len(success) == 1
        assert len(failures) == 1
        assert success[0]["url"] == "https://good.example.com"
        assert failures[0]["url"] == "https://bad.example.com"

    @pytest.mark.asyncio
    async def test_html_stripping(self) -> None:
        F = _import_fetcher()
        html = "<html><body><h1>Title</h1><p>Some <b>bold</b> text</p></body></html>"
        text = F["_strip_html"](html)
        assert "Title" in text
        assert "Some bold text" in text
        assert "<h1>" not in text
        assert "<b>" not in text

    @pytest.mark.asyncio
    async def test_html_strip_scripts_and_styles(self) -> None:
        F = _import_fetcher()
        html = (
            "<html><head><style>.cls{color:red}</style></head>"
            "<body><script>alert('hi')</script><p>Content</p></body></html>"
        )
        text = F["_strip_html"](html)
        assert "Content" in text
        assert "alert" not in text
        assert "color:red" not in text

    @pytest.mark.asyncio
    async def test_title_extraction(self) -> None:
        F = _import_fetcher()
        html = "<html><title>My Page</title><body><p>Content</p></body></html>"
        title = F["_extract_title"](html)
        assert title == "My Page"

    @pytest.mark.asyncio
    async def test_title_extraction_no_title(self) -> None:
        F = _import_fetcher()
        html = "<html><body><p>No title here</p></body></html>"
        title = F["_extract_title"](html)
        assert title is None

    @pytest.mark.asyncio
    async def test_semaphore_limiting(self) -> None:
        """Semaphore should limit concurrent requests to max_concurrent."""
        F = _import_fetcher()

        semaphore_lock = asyncio.Lock()
        active_count = 0
        max_active = 0

        async def slow_get(url, **kwargs):
            nonlocal active_count, max_active
            async with semaphore_lock:
                active_count += 1
                max_active = max(max_active, active_count)
            await asyncio.sleep(0.05)
            async with semaphore_lock:
                active_count -= 1
            mock_resp = MagicMock()
            mock_resp.text = (
                f"<html><title>Page</title><body><p>{url}</p></body></html>"
            )
            mock_resp.raise_for_status.return_value = None
            return mock_resp

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = slow_get
            await F["fetch_page_content"](
                [f"https://example.com/{i}" for i in range(10)],
                max_concurrent=3,
            )

        assert max_active <= 3, f"Max concurrent was {max_active}, expected <= 3"
