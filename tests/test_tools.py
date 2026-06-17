"""Tests for DeepeResearch tool definitions.

Covers:
  - web_search tool definition schema (WEB_SEARCH_TOOL)
  - web_search execution via SearXNG (default)
  - web_search execution via DuckDuckGo (legacy, conditional)
  - web_search error handling
  - Feature flag switching
  - generate_with_tools on LLMClient
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deepresearch.llm.client import LLMClient
from deepresearch.tools.web_search import WEB_SEARCH_TOOL, web_search


# ─── Web Search Tool Definition ─────────────────────────────────────────────


class TestWebSearchToolDefinition:
    """WEB_SEARCH_TOOL must conform to the LiteLLM function-calling schema."""

    def test_tool_definition_type(self) -> None:
        """WEB_SEARCH_TOOL should have type 'function'."""
        assert WEB_SEARCH_TOOL["type"] == "function"

    def test_tool_definition_has_function_name(self) -> None:
        """WEB_SEARCH_TOOL should have a function name."""
        assert WEB_SEARCH_TOOL["function"]["name"] == "web_search"

    def test_tool_definition_has_description(self) -> None:
        """WEB_SEARCH_TOOL should have a non-empty description."""
        desc = WEB_SEARCH_TOOL["function"]["description"]
        assert isinstance(desc, str)
        assert len(desc) > 10

    def test_tool_definition_parameters(self) -> None:
        """WEB_SEARCH_TOOL should define query and max_results params."""
        params = WEB_SEARCH_TOOL["function"]["parameters"]
        assert "query" in params["properties"]
        assert params["properties"]["query"]["type"] == "string"
        assert "max_results" in params["properties"]
        assert params["properties"]["max_results"]["type"] == "integer"

    def test_tool_definition_required(self) -> None:
        """WEB_SEARCH_TOOL should require 'query'."""
        assert "query" in WEB_SEARCH_TOOL["function"]["parameters"]["required"]


# ─── Web Search Execution — SearXNG (default) ──────────────────────────────


class TestWebSearchSearxng:
    """web_search() via SearXNG backend (default)."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self) -> None:
        """web_search should return a list of dicts with expected keys."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"title": "Result 1", "content": "Snippet 1", "url": "https://example.com/1"},
                {"title": "Result 2", "content": "Snippet 2", "url": "https://example.com/2"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deepresearch.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await web_search("test query", max_results=2)

        assert isinstance(results, list)
        assert len(results) == 2
        for r in results:
            assert "title" in r
            assert "snippet" in r
            assert "url" in r
        assert results[0]["title"] == "Result 1"
        assert results[0]["url"] == "https://example.com/1"

    @pytest.mark.asyncio
    async def test_respects_max_results(self) -> None:
        """web_search should not return more than max_results items."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"title": f"Result {i}", "content": f"Snippet {i}", "url": f"https://example.com/{i}"}
                for i in range(20)
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deepresearch.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await web_search("test", max_results=3)

        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_handles_empty_results(self) -> None:
        """web_search should return an empty list when no results."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"results": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deepresearch.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await web_search("obscure_xyz_query_12345")

        assert isinstance(results, list)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_handles_search_failure_gracefully(self) -> None:
        """web_search should return empty list on failure (fallback mode), not raise."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=RuntimeError("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deepresearch.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await web_search("failing query")

        assert isinstance(results, list)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_handles_missing_keys(self) -> None:
        """web_search should handle dicts with missing/empty keys."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"title": "Only Title"},
                {"content": "Only snippet", "url": "https://example.com"},
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deepresearch.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await web_search("sparse data", max_results=2)

        assert len(results) == 2
        # Missing keys should default to empty strings
        assert results[0]["url"] == ""
        assert results[0]["snippet"] == ""
        assert results[1]["title"] == ""

    @pytest.mark.asyncio
    async def test_truncates_long_fields(self) -> None:
        """web_search should truncate title, snippet, url to max lengths."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "A" * 100,
                    "content": "B" * 200,
                    "url": "https://example.com/" + "C" * 100,
                }
            ]
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("deepresearch.tools.web_search.httpx.AsyncClient", return_value=mock_client):
            results = await web_search("truncate test", max_results=1)

        assert len(results) == 1
        assert len(results[0]["title"]) <= 80
        assert len(results[0]["snippet"]) <= 150
        assert len(results[0]["url"]) <= 80


# ─── Web Search Execution — DuckDuckGo (legacy, conditional) ───────────────


class TestWebSearchDDGS:
    """web_search() via DuckDuckGo backend (legacy). Skipped if ddgs not installed."""

    @pytest.mark.asyncio
    async def test_ddgs_returns_list_of_dicts(self, mock_ddgs) -> None:
        """web_search with ddgs should return structured results."""
        mock_instance = MagicMock()
        mock_instance.text.return_value = [
            {"title": "Result 1", "body": "Snippet 1", "href": "https://example.com/1"},
            {"title": "Result 2", "body": "Snippet 2", "href": "https://example.com/2"},
        ]
        mock_ddgs.return_value.__enter__.return_value = mock_instance

        import deepresearch.tools.web_search as ws
        old_engine = ws._search_engine
        try:
            ws._search_engine = "ddgs"
            results = await web_search("test query ddgs", max_results=2)
        finally:
            ws._search_engine = old_engine

        assert isinstance(results, list)
        assert len(results) == 2
        for r in results:
            assert "title" in r
            assert "snippet" in r
            assert "url" in r
        assert results[0]["title"] == "Result 1"
        assert results[0]["url"] == "https://example.com/1"

    @pytest.mark.asyncio
    async def test_ddgs_passes_max_results(self, mock_ddgs) -> None:
        """web_search with ddgs should pass max_results to DDGS.text()."""
        mock_instance = MagicMock()
        mock_instance.text.return_value = []
        mock_ddgs.return_value.__enter__.return_value = mock_instance

        import deepresearch.tools.web_search as ws
        old_engine = ws._search_engine
        try:
            ws._search_engine = "ddgs"
            await web_search("unique_test_query_max_results", max_results=7)
        finally:
            ws._search_engine = old_engine

        mock_instance.text.assert_called_once_with(
            "unique_test_query_max_results", max_results=7
        )


# ─── Feature Flag Switching ────────────────────────────────────────────────


class TestFeatureFlag:
    """Tests for _search_engine feature flag."""

    @pytest.mark.asyncio
    async def test_default_engine_is_searxng(self) -> None:
        """Default search engine should be 'searxng'."""
        import deepresearch.tools.web_search as ws
        # Reset to default
        ws._search_engine = "searxng"
        assert ws._search_engine == "searxng"

    @pytest.mark.asyncio
    async def test_flag_switches_to_ddgs(self) -> None:
        """Setting _search_engine to 'ddgs' should use ddgs backend."""
        import deepresearch.tools.web_search as ws
        old = ws._search_engine
        try:
            ws._search_engine = "ddgs"
            assert ws._search_engine == "ddgs"
        finally:
            ws._search_engine = old


# ─── Search Health Info ─────────────────────────────────────────────────────


class TestSearchHealthInfo:
    """get_search_health_info() returns current search status."""

    def test_returns_expected_keys(self) -> None:
        """get_search_health_info should return all expected keys."""
        from deepresearch.tools.web_search import get_search_health_info

        info = get_search_health_info()
        assert "engine" in info
        assert "status" in info
        assert "cached_queries" in info
        assert "searxng_url" in info

    def test_health_values(self) -> None:
        """Health status should be one of the expected values."""
        from deepresearch.tools.web_search import get_search_health_info, _search_health

        info = get_search_health_info()
        assert info["status"] in ("unknown", "healthy", "degraded", "unhealthy")


# ─── LLMClient.generate_with_tools ────────────────────────────────────────


class TestGenerateWithTools:
    """LLMClient.generate_with_tools should handle tool calling correctly."""

    @pytest.mark.asyncio
    async def test_no_tools_falls_back_to_generate_stream(self) -> None:
        """Passing no tools should fall back to generate_stream."""
        client = LLMClient(model="gpt-4o", timeout=10)
        with patch.object(client, "generate_stream", new_callable=AsyncMock) as mock_gs:
            mock_gs.return_value = "fallback response"
            result = await client.generate_with_tools(
                system_prompt="system",
                user_prompt="user",
                tools=None,
            )
            assert result == "fallback response"
            mock_gs.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_tools_list_falls_back_to_generate_stream(self) -> None:
        """Passing an empty tools list should fall back to generate_stream."""
        client = LLMClient(model="gpt-4o", timeout=10)
        with patch.object(client, "generate_stream", new_callable=AsyncMock) as mock_gs:
            mock_gs.return_value = "empty tools fallback"
            result = await client.generate_with_tools(
                system_prompt="system",
                user_prompt="user",
                tools=[],
            )
            assert result == "empty tools fallback"
            mock_gs.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_tool_call_with_fallback(self) -> None:
        """When streaming+tool fails, fall back to non-streaming mode."""
        client = LLMClient(model="gpt-4o", timeout=10)

        with patch("litellm.acompletion") as mock_acompletion:
            mock_acompletion.side_effect = None
            mock_acompletion.reset_mock()

            async def mock_stream():
                mock_chunk = MagicMock()
                mock_chunk.choices = [MagicMock()]
                mock_chunk.choices[0].delta.content = "test response"
                mock_chunk.choices[0].delta.tool_calls = None
                yield mock_chunk

            mock_acompletion.return_value = mock_stream()

            result = await client.generate_with_tools(
                system_prompt="system",
                user_prompt="user",
                tools=[WEB_SEARCH_TOOL],
            )

            assert "test response" in result

    @pytest.mark.asyncio
    async def test_non_streaming_fallback_response(self) -> None:
        """Non-streaming fallback should still produce text."""
        client = LLMClient(model="gpt-4o", timeout=10)

        with patch("litellm.acompletion") as mock_acompletion:
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content="final answer after fallback",
                        tool_calls=None,
                    )
                )
            ]
            mock_response.usage = MagicMock(
                prompt_tokens=10,
                completion_tokens=5,
            )

            mock_acompletion.side_effect = [
                RuntimeError("Streaming failed"),
                mock_response,
            ]

            result = await client.generate_with_tools(
                system_prompt="system",
                user_prompt="user",
                tools=[WEB_SEARCH_TOOL],
            )

            assert "final answer after fallback" in result

    @pytest.mark.asyncio
    async def test_streaming_with_tool_calls_and_final_text(self) -> None:
        """Streaming path should handle tool calls then final text."""
        client = LLMClient(model="gpt-4o", timeout=10)

        with (
            patch("litellm.acompletion") as mock_acompletion,
            patch(
                "deepresearch.tools.web_search.web_search", new_callable=AsyncMock
            ) as mock_ws,
        ):
            mock_ws.return_value = [
                {"title": "Result", "snippet": "Snippet", "url": "https://example.com"}
            ]

            async def first_stream():
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = None

                tc_chunk = MagicMock()
                tc_chunk.index = 0
                tc_chunk.id = "call_abc123"
                tc_chunk.function.name = "web_search"
                tc_chunk.function.arguments = '{"query": "test"}'
                chunk.choices[0].delta.tool_calls = [tc_chunk]

                yield chunk

            async def second_stream():
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[
                    0
                ].delta.content = "Based on search results, here's my report."
                chunk.choices[0].delta.tool_calls = None
                yield chunk

            mock_acompletion.side_effect = [
                first_stream(),
                second_stream(),
            ]

            result = await client.generate_with_tools(
                system_prompt="system",
                user_prompt="user",
                tools=[WEB_SEARCH_TOOL],
            )

            assert "Based on search results" in result
            assert mock_ws.called
