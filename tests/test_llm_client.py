"""Tests for the LLM client module.

Covers:
- Provider/model routing
- Tool calling fallback (streaming → non-streaming → no-tools)
- Text-embedded tool call detection
- Circuit breaker behavior
- Cost lookup edge cases
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deepresearch.llm.client import (
    LLMClient,
    CircuitBreaker,
    CircuitBreakerOpenError,
    _lookup_cost,
)


# ── Helper factories ─────────────────────────────────────────────────────


def _mock_nonstreaming_response(
    content: str = "test response",
    tool_calls: list | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 20,
) -> MagicMock:
    """Build a mock LiteLLM non-streaming response.

    Mimics the structure of ``litellm.ModelResponse`` that
    ``generate_with_tools`` and ``generate`` parse after a
    non-streaming ``acompletion`` call.
    """
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    choice = MagicMock()
    choice.message = msg
    choice.index = 0
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


def _mock_streaming_chunks(*texts: str):
    """Build an async generator of streaming chunks.

    Each string in ``texts`` is returned as one chunk's
    ``choices[0].delta.content`` content.
    """
    async def _gen():
        for text in texts:
            delta = MagicMock()
            delta.content = text
            delta.tool_calls = None
            choice = MagicMock()
            choice.delta = delta
            choice.index = 0
            chunk = MagicMock()
            chunk.choices = [choice]
            yield chunk
    return _gen()


# ── Model Routing Tests ──────────────────────────────────────────────────


class TestModelRouting:
    """Provider detection and routing configuration."""

    def test_ollama_resolves_provider(self) -> None:
        """``ollama/qwen3:8b`` → provider = ``ollama`` (local backend)."""
        client = LLMClient(model="ollama/qwen3:8b")
        assert client.provider == "ollama"
        assert client.actual_model == "ollama/qwen3:8b"

    def test_gpt4o_no_routing(self) -> None:
        """``gpt-4o`` has no known prefix → provider is ``None``."""
        client = LLMClient(model="gpt-4o")
        assert client.provider is None
        assert client.api_base is None

    def test_opencode_endpoint_routed(self) -> None:
        """``opencode/go/deepseek-v4-flash`` is endpoint-routed with correct api_base."""
        client = LLMClient(model="opencode/go/deepseek-v4-flash")
        assert client.provider == "opencode"
        assert client.endpoint == "go"
        assert client.actual_model == "deepseek-v4-flash"
        assert client.api_base == "https://opencode.ai/zen/go/v1"


# ── Tool Calling Fallback Tests ──────────────────────────────────────────


class TestToolCallingFallback:
    """Fallback chain within ``generate_with_tools``.

    The expected fallback order is:
        streaming (with tools) → non-streaming (with tools) → no-tools
    """

    async def test_empty_tools_falls_back_to_generate_stream(self) -> None:
        """``tools=None`` → ``generate_stream`` is called directly."""
        client = LLMClient(model="test-model", timeout=5)
        with patch.object(client, "generate_stream", new_callable=AsyncMock) as mock_stream:
            mock_stream.return_value = "stream result"
            result = await client.generate_with_tools(
                system_prompt="test",
                user_prompt="test",
                tools=None,
            )
            assert result == "stream result"
            mock_stream.assert_called_once()

    async def test_streaming_fails_nonstreaming_succeeds(self) -> None:
        """Non-local backend: streaming fails → falls back to non-streaming."""
        client = LLMClient(model="test-model", timeout=5)
        nonstream_response = _mock_nonstreaming_response(
            content="fallback result", tool_calls=None
        )

        call_count: list[int] = [0]

        async def mock_acompletion(**kwargs: object) -> MagicMock:
            call_count[0] += 1
            if kwargs.get("stream", False):
                raise Exception("Streaming transport error")
            return nonstream_response

        with patch("litellm.acompletion", side_effect=mock_acompletion):
            result = await client.generate_with_tools(
                system_prompt="test",
                user_prompt="test",
                tools=[{"type": "function", "function": {"name": "web_search"}}],
                temperature=0.7,
            )
            assert result == "fallback result"
            # Call 1: streaming (fails), Call 2: non-streaming (succeeds)
            assert call_count[0] == 2

    async def test_both_streaming_and_nonstreaming_fail_falls_back_to_no_tools(
        self,
    ) -> None:
        """Both streaming and non-streaming fail → falls back to ``generate_stream`` (no tools)."""
        client = LLMClient(model="test-model", timeout=5)

        call_count: list[int] = [0]

        async def mock_acompletion(**kwargs: object) -> MagicMock:
            call_count[0] += 1
            raise Exception("API unreachable")

        with (
            patch("litellm.acompletion", side_effect=mock_acompletion),
            patch.object(
                client, "generate_stream", new_callable=AsyncMock
            ) as mock_gen_stream,
        ):
            mock_gen_stream.return_value = "fallback without tools"
            result = await client.generate_with_tools(
                system_prompt="test",
                user_prompt="test",
                tools=[{"type": "function", "function": {"name": "web_search"}}],
                temperature=0.7,
            )
            assert result == "fallback without tools"
            # Call 1: streaming (fails), Call 2: non-streaming (fails)
            assert call_count[0] == 2
            mock_gen_stream.assert_called_once()


# ── Text-Embedded Tool Call Detection ────────────────────────────────────


class TestTextEmbeddedToolCallDetection:
    """Detection of ``{"name": "web_search", "arguments": {...}}`` in plain text responses.

    Some models (especially local backends) do not support native function
    calling but may emit tool-call JSON as regular text content.
    """

    async def test_detects_text_embedded_tool_call(self) -> None:
        """Response content with web_search JSON → detected and executed."""
        client = LLMClient(model="test-model", timeout=5)

        tool_call_json = (
            '{"name": "web_search", "arguments": {"query": "test query", "max_results": 3}}'
        )
        round1 = _mock_nonstreaming_response(
            content=tool_call_json, tool_calls=None
        )
        round2 = _mock_nonstreaming_response(
            content="Final answer after search.", tool_calls=None
        )

        responses: list[MagicMock] = [round1, round2]
        idx: list[int] = [0]

        async def mock_acompletion(**kwargs: object) -> MagicMock:
            if kwargs.get("stream", False):
                raise Exception("Streaming failed")
            val = responses[idx[0]]
            idx[0] += 1
            return val

        with (
            patch("litellm.acompletion", side_effect=mock_acompletion),
            patch(
                "deepresearch.tools.web_search.web_search", new_callable=AsyncMock
            ) as mock_web_search,
        ):
            mock_web_search.return_value = [
                {"title": "Result 1", "url": "https://example.com"}
            ]
            result = await client.generate_with_tools(
                system_prompt="You are a search assistant.",
                user_prompt="Search for test query",
                tools=[{"type": "function", "function": {"name": "web_search"}}],
                temperature=0.7,
            )
            assert result == "Final answer after search."
            mock_web_search.assert_called_once_with("test query", 3)

    async def test_regular_text_not_detected(self) -> None:
        """Plain text without tool-call JSON → NOT misidentified as a tool call."""
        client = LLMClient(model="test-model", timeout=5)

        regular_text = "Here is a summary of findings based on available data."
        nonstream_response = _mock_nonstreaming_response(
            content=regular_text, tool_calls=None
        )

        call_count: list[int] = [0]

        async def mock_acompletion(**kwargs: object) -> MagicMock:
            call_count[0] += 1
            if kwargs.get("stream", False):
                raise Exception("Streaming failed")
            return nonstream_response

        with patch("litellm.acompletion", side_effect=mock_acompletion):
            result = await client.generate_with_tools(
                system_prompt="test",
                user_prompt="test",
                tools=[{"type": "function", "function": {"name": "web_search"}}],
                temperature=0.7,
            )
            # Regular text passes through unchanged
            assert result == regular_text
            # Call 1: streaming (fails), Call 2: non-streaming (succeeds, no tools)
            assert call_count[0] == 2


# ── Circuit Breaker Tests ────────────────────────────────────────────────


class TestCircuitBreaker:
    """Per-model circuit breaker (``CircuitBreaker`` class)."""

    def test_starts_closed(self) -> None:
        """Fresh breaker is not open."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=60.0)
        assert not cb.is_open
        assert cb._failures == 0

    def test_failure_threshold_opens_breaker(self) -> None:
        """After ``failure_threshold`` failures the breaker opens."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=60.0)
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert cb.is_open

    def test_success_resets_breaker(self) -> None:
        """``record_success()`` clears failures and closes the breaker."""
        cb = CircuitBreaker(failure_threshold=3, reset_timeout=60.0)
        # Push to open
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open
        # Record success — should reset
        cb.record_success()
        assert not cb.is_open
        assert cb._failures == 0

    async def test_generate_stream_raises_circuit_breaker_open_error(self) -> None:
        """``generate_stream`` raises ``CircuitBreakerOpenError`` when breaker is open."""
        # Use a unique model name to avoid class-level state leaking from other tests.
        model = "test-breaker-open"
        LLMClient._breakers.pop(model, None)  # start fresh
        client = LLMClient(model=model, timeout=5)
        breaker = client._get_breaker()
        breaker._failures = 3
        breaker._opened_at = time.monotonic()

        with pytest.raises(CircuitBreakerOpenError, match=f"Circuit breaker open for {model}"):
            await client.generate_stream(
                system_prompt="test",
                user_prompt="test",
            )

    async def test_successful_streaming_call_resets_breaker(self) -> None:
        """A successful streaming call calls ``record_success``, resetting the breaker."""
        # Use a unique model name to avoid class-level state leaking from other tests.
        model = "test-breaker-reset"
        LLMClient._breakers.pop(model, None)  # start fresh
        client = LLMClient(model=model, timeout=5)
        breaker = client._get_breaker()
        breaker._failures = 2  # Close to threshold

        async def mock_acompletion(**kwargs: object) -> object:
            return _mock_streaming_chunks("hello ", "world")

        with patch("litellm.acompletion", side_effect=mock_acompletion):
            result = await client.generate_stream(
                system_prompt="test",
                user_prompt="test",
            )
            assert result == "hello world"
            assert breaker._failures == 0  # Reset by record_success


# ── Cost Lookup Tests ────────────────────────────────────────────────────


class TestCostLookup:
    """``_lookup_cost`` edge cases."""

    def test_known_model(self) -> None:
        """Known model rates are used correctly."""
        cost = _lookup_cost("gpt-4o", input_tokens=1000, output_tokens=500)
        # input: 1K × 0.0025 = 0.0025, output: 0.5K × 0.01 = 0.005
        assert cost == pytest.approx(0.0075)

    def test_unknown_model_falls_back_to_default(self) -> None:
        """Unknown model uses the fallback rate (0.0025 USD / 1K)."""
        cost = _lookup_cost("unknown-model", input_tokens=1000, output_tokens=1000)
        # Both use default 0.0025: 1 × 0.0025 + 1 × 0.0025 = 0.005
        assert cost == pytest.approx(0.005)

    def test_free_model_zero_cost(self) -> None:
        """Free models (ollama/llama3.1) return 0.0 cost."""
        cost = _lookup_cost("ollama/llama3.1", input_tokens=10_000, output_tokens=5_000)
        assert cost == pytest.approx(0.0)

    def test_zero_tokens_returns_zero(self) -> None:
        """Zero input and output tokens → cost is 0.0."""
        cost = _lookup_cost("gpt-4o", input_tokens=0, output_tokens=0)
        assert cost == pytest.approx(0.0)
