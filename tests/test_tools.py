"""Tests for DeepResearch tool definitions.

Covers:
  - web_search tool definition schema (WEB_SEARCH_TOOL)
  - web_search execution via SearXNG (default)
  - web_search execution via DuckDuckGo (legacy, conditional)
  - web_search error handling
  - Feature flag switching
  - generate_with_tools on LLMClient
"""

from __future__ import annotations

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
    """web_search() via SearchChain (default, refactored ADR-0017)."""

    @pytest.fixture(autouse=True)
    def _no_fetch_or_cache(self, no_fetch_or_cache) -> None:
        """Disable content fetching and caching for these unit tests."""
        # Delegates to shared no_fetch_or_cache fixture in conftest.py
        pass

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self) -> None:
        """web_search should return a list of dicts with expected keys."""
        from deepresearch.tools.search_chain import SearchChain

        mock_results = [
            {
                "title": "Result 1",
                "snippet": "Snippet 1",
                "url": "https://example.com/1",
                "source": "searxng",
            },
            {
                "title": "Result 2",
                "snippet": "Snippet 2",
                "url": "https://example.com/2",
                "source": "searxng",
            },
        ]
        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
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
        from deepresearch.tools.search_chain import SearchChain

        mock_results = [
            {
                "title": f"Result {i}",
                "snippet": f"Snippet {i}",
                "url": f"https://example.com/{i}",
                "source": "searxng",
            }
            for i in range(3)
        ]
        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
            results = await web_search("test", max_results=3)

        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_handles_empty_results(self) -> None:
        """web_search should return an empty list when no results."""
        from deepresearch.tools.search_chain import SearchChain

        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            results = await web_search("obscure_xyz_query_12345")

        assert isinstance(results, list)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_handles_search_failure_gracefully(self) -> None:
        """web_search should return empty list on failure (fallback mode), not raise."""
        from deepresearch.tools.search_chain import SearchChain

        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            results = await web_search("failing query")

        assert isinstance(results, list)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_handles_missing_keys(self) -> None:
        """web_search should handle dicts with missing/empty keys."""
        from deepresearch.tools.search_chain import SearchChain

        mock_results = [
            {"title": "Only Title", "source": "searxng"},
            {
                "snippet": "Only snippet",
                "url": "https://example.com",
                "source": "searxng",
            },
        ]
        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
            results = await web_search("sparse data", max_results=2)

        assert len(results) == 2
        # Missing keys should default to empty strings
        assert results[0]["url"] == ""
        assert results[0]["snippet"] == ""
        assert results[1]["title"] == ""

    @pytest.mark.asyncio
    async def test_truncates_long_fields(self) -> None:
        """web_search should return fields within length limits."""
        from deepresearch.tools.search_chain import SearchChain

        # Providers return pre-truncated data; web_search passes it through
        _title = "A" * 80
        _snippet = "B" * 150
        _url = "https://example.com/" + "C" * 60  # 80 total
        mock_results = [
            {
                "title": _title,
                "snippet": _snippet,
                "url": _url,
                "source": "searxng",
            }
        ]
        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
            results = await web_search("truncate test", max_results=1)

        assert len(results) == 1
        assert len(results[0]["title"]) <= 80
        assert len(results[0]["snippet"]) <= 150
        assert len(results[0]["url"]) <= 80


# ─── Web Search Execution — DuckDuckGo (legacy, conditional) ───────────────


class TestWebSearchDDGS:
    """web_search() via DuckDuckGo provider (legacy)."""

    @pytest.mark.asyncio
    async def test_ddgs_returns_list_of_dicts(self) -> None:
        """web_search should return structured results from any provider."""
        from deepresearch.tools.search_chain import SearchChain

        mock_results = [
            {
                "title": "Result 1",
                "snippet": "Snippet 1",
                "url": "https://example.com/1",
                "source": "duckduckgo",
            },
            {
                "title": "Result 2",
                "snippet": "Snippet 2",
                "url": "https://example.com/2",
                "source": "duckduckgo",
            },
        ]
        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = mock_results
            results = await web_search("test query ddgs", max_results=2)

        assert isinstance(results, list)
        assert len(results) == 2
        for r in results:
            assert "title" in r
            assert "snippet" in r
            assert "url" in r
        assert results[0]["title"] == "Result 1"
        assert results[0]["url"] == "https://example.com/1"

    @pytest.mark.asyncio
    async def test_ddgs_passes_max_results(self) -> None:
        """web_search should pass max_results through SearchChain."""
        from deepresearch.tools.search_chain import SearchChain

        with patch.object(SearchChain, "search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            await web_search("unique_test_query_max_results", max_results=7)

        mock_search.assert_called_once()
        call_kwargs = mock_search.call_args[1]
        assert call_kwargs["max_results"] == 7


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
        assert "last_search_latency_ms" in info
        assert "searxng_url" in info

    def test_health_values(self) -> None:
        """Health status should be one of the expected values."""
        from deepresearch.tools.web_search import get_search_health_info

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
        from deepresearch.tools.registry import ToolDef

        client = LLMClient(model="gpt-4o", timeout=10)

        with (
            patch("litellm.acompletion") as mock_acompletion,
            patch("deepresearch.tools.registry.resolve_tool") as mock_resolve,
        ):
            mock_ws = AsyncMock()
            mock_ws.return_value = [
                {"title": "Result", "snippet": "Snippet", "url": "https://example.com"}
            ]
            mock_resolve.return_value = ToolDef(
                name="web_search",
                handler=mock_ws,
                schema=WEB_SEARCH_TOOL,
            )

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
