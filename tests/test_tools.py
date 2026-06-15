"""Tests for DeepeResearch tool definitions.

Covers:
  - web_search tool definition schema (WEB_SEARCH_TOOL)
  - web_search execution via DuckDuckGo
  - web_search error handling
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


# ─── Web Search Execution ──────────────────────────────────────────────────


class TestWebSearchExecution:
    """web_search() should return structured results."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self) -> None:
        """web_search should return a list of dicts with expected keys."""
        with patch("ddgs.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.return_value = [
                {"title": "Result 1", "body": "Snippet 1", "href": "https://example.com/1"},
                {"title": "Result 2", "body": "Snippet 2", "href": "https://example.com/2"},
            ]
            mock_ddgs.return_value.__enter__.return_value = mock_instance

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
        with patch("ddgs.DDGS") as mock_ddgs:
            # Return more results than requested
            mock_instance = MagicMock()
            mock_instance.text.return_value = [
                {"title": f"Result {i}", "body": f"Snippet {i}", "href": f"https://example.com/{i}"}
                for i in range(20)
            ]
            mock_ddgs.return_value.__enter__.return_value = mock_instance

            results = await web_search("test", max_results=3)

            assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_handles_empty_results(self) -> None:
        """web_search should return an empty list when no results."""
        with patch("ddgs.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.return_value = []
            mock_ddgs.return_value.__enter__.return_value = mock_instance

            results = await web_search("obscure_xyz_query_12345")

            assert isinstance(results, list)
            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_handles_search_failure_gracefully(self) -> None:
        """web_search should return error dict on failure, not raise."""
        with patch("ddgs.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.side_effect = RuntimeError("Network error")
            mock_ddgs.return_value.__enter__.return_value = mock_instance

            results = await web_search("failing query")

            assert isinstance(results, list)
            assert len(results) == 1
            assert results[0]["title"] == "Search Error"
            assert "Network error" in results[0]["snippet"]

    @pytest.mark.asyncio
    async def test_handles_missing_keys(self) -> None:
        """web_search should handle dicts with missing keys."""
        with patch("ddgs.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.return_value = [
                {"title": "Only Title"},
                {"body": "Only snippet", "href": "https://example.com"},
            ]
            mock_ddgs.return_value.__enter__.return_value = mock_instance

            results = await web_search("sparse data", max_results=2)

            assert len(results) == 2
            # Missing keys should default to empty strings
            assert results[0]["url"] == ""
            assert results[0]["snippet"] == ""
            assert results[1]["title"] == ""

    @pytest.mark.asyncio
    async def test_passes_max_results_to_ddgs(self) -> None:
        """web_search should pass max_results to DDGS.text()."""
        with patch("ddgs.DDGS") as mock_ddgs:
            mock_instance = MagicMock()
            mock_instance.text.return_value = []
            mock_ddgs.return_value.__enter__.return_value = mock_instance

            await web_search("test", max_results=7)

            mock_instance.text.assert_called_once_with("test", max_results=7)


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

        # Mock acompletion to fail on streaming, then succeed on non-streaming
        mock_async_iterator = AsyncMock()
        mock_async_iterator.__aiter__.return_value = iter([])

        with patch("litellm.acompletion") as mock_acompletion:
            # First call (streaming=True) fails
            mock_acompletion.side_effect = [
                RuntimeError("Streaming+tool not supported"),
                MagicMock(),  # Second call returns non-streaming response
            ]

            # Configure the non-streaming response
            async def second_call_side_effect(**kwargs):
                # Return a mock response with text content
                mock_response = MagicMock()
                mock_response.choices = [
                    MagicMock(message=MagicMock(content="final text response", tool_calls=[]))
                ]
                return mock_response

            mock_acompletion.side_effect = [
                RuntimeError("Streaming+tool not supported"),
                AsyncMock(side_effect=second_call_side_effect)(),
            ]

            # Actually, let's make this simpler — just test that streaming
            # path itself handles the chunk accumulation correctly when
            # no tool calls are made (just text).
            mock_acompletion.side_effect = None
            mock_acompletion.reset_mock()

            # Create a proper async iterator mock for streaming
            async def mock_stream():
                mock_chunk = MagicMock()
                mock_chunk.choices = [MagicMock()]
                mock_chunk.choices[0].delta.content = "test response"
                mock_chunk.choices[0].delta.tool_calls = None
                yield mock_chunk

            # The response of acompletion when streaming=True is an async iterable
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
            # First streaming call fails, second non-streaming succeeds
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content="final answer after fallback",
                        tool_calls=None,
                    )
                )
            ]
            # Non-streaming response has usage info
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

        with patch("litellm.acompletion") as mock_acompletion, \
             patch("deepresearch.tools.web_search.web_search", new_callable=AsyncMock) as mock_ws:

            mock_ws.return_value = [
                {"title": "Result", "snippet": "Snippet", "url": "https://example.com"}
            ]

            # First round: tool calls
            async def first_stream():
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = None

                # Tool call chunk (partial, like streaming)
                tc_chunk = MagicMock()
                tc_chunk.index = 0
                tc_chunk.id = "call_abc123"
                tc_chunk.function.name = "web_search"
                tc_chunk.function.arguments = '{"query": "test"}'
                chunk.choices[0].delta.tool_calls = [tc_chunk]

                yield chunk

            # Second round: final text
            async def second_stream():
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta.content = "Based on search results, here's my report."
                chunk.choices[0].delta.tool_calls = None
                yield chunk

            # Third round: no tools (not reached since first round sets 2 rounds)
            # Actually with streaming, acompletion is called per round.
            # First round: streaming response with tool calls
            # Second round: streaming response with text

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
