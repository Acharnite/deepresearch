"""LiteLLM client wrapper for async LLM interactions.

Provides retry logic, timeout enforcement, token tracking,
cost estimation, and structured output parsing.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable
from typing import Any, Callable

import litellm

logger = logging.getLogger(__name__)


# Per-model cost tables (USD per 1K tokens).
# Source: provider pricing pages as of 2026-06.
MODEL_COST_PER_1K_INPUT: dict[str, float] = {
    "gpt-4o": 0.0025,
    "gpt-4o-mini": 0.00015,
    "claude-sonnet-4-20250514": 0.003,
    "claude-3-5-haiku-20241022": 0.0008,
    "openrouter/opencode/go": 0.0,  # OpenRouter — free tier
    "openrouter/opencode/zen": 0.0,  # OpenRouter — free tier
    "ollama/llama3.1": 0.0,  # Local (Ollama)
    "ollama/mixtral": 0.0,  # Local (Ollama)
}

MODEL_COST_PER_1K_OUTPUT: dict[str, float] = {
    "gpt-4o": 0.01,
    "gpt-4o-mini": 0.0006,
    "claude-sonnet-4-20250514": 0.015,
    "claude-3-5-haiku-20241022": 0.004,
    "openrouter/opencode/go": 0.0,  # OpenRouter — free tier
    "openrouter/opencode/zen": 0.0,  # OpenRouter — free tier
    "ollama/llama3.1": 0.0,  # Local (Ollama)
    "ollama/mixtral": 0.0,  # Local (Ollama)
}


# ── Provider routing — maps model prefix → API base + env var ──────────
# When a model ID starts with a known prefix, LLMClient automatically
# sets the correct api_base and api_key for that provider.
PROVIDER_ROUTES: dict[str, dict[str, Any]] = {
    "opencode": {
        "type": "endpoint_routed",
        "api_key_env": "OPENCODE_API_KEY",
        "openai_compatible": True,
        "endpoints": {
            "go": "https://opencode.ai/zen/go/v1",
            "zen": "https://opencode.ai/zen/v1",
        },
    },
    "openrouter": {
        "api_base": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
    },
    "groq": {
        "api_base": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
    },
    "together": {
        "api_base": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
    },
    "deepseek": {
        "api_base": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "cohere": {
        "api_base": "https://api.cohere.ai/v1",
        "api_key_env": "COHERE_API_KEY",
    },
    "gemini": {
        "api_base": "https://generativelanguage.googleapis.com",
        "api_key_env": "GEMINI_API_KEY",
    },
    "anthropic": {
        "api_base": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "ollama": {
        "api_base": "http://localhost:11434",
        "api_key_env": None,
    },
}


def _lookup_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> float:
    """Calculate cost for a given model and token counts.

    Falls back to the model's input rate for both input and output
    if the output rate is not explicitly listed.
    """
    input_rate = MODEL_COST_PER_1K_INPUT.get(model, 0.0025)
    output_rate = MODEL_COST_PER_1K_OUTPUT.get(model, input_rate)
    cost = (input_tokens / 1000) * input_rate + (output_tokens / 1000) * output_rate
    return round(cost, 6)


class LLMError(Exception):
    """Raised when an LLM call fails after all retries or times out."""


class LLMClient:
    """Async wrapper around LiteLLM's acompletion.

    Features:
    - Async completion with configurable timeout
    - Automatic retry with exponential backoff (2 retries)
    - Token usage tracking (input/output counts and cost)
    - Cost estimation before making a call
    - Structured output parsing (JSON responses)
    - Auto-routing: models are routed to the correct provider
      API base and API key based on the model prefix.

    Provider routing
    ----------------
    ``LLMClient`` automatically detects the provider from the model prefix
    and sets the correct ``api_base`` and ``api_key`` for the LLM call.
    This means you never need to configure LiteLLM's proxy — just set the
    environment variable for the provider you want to use.

    ``opencode`` is a special **endpoint-routed** provider — the model
    ID uses a 3-part format ``opencode/{endpoint}/{model-name}`` which
    determines both the endpoint and the actual model name passed to
    LiteLLM.

    ======================== ======================================== =====================
    Model prefix / format     API base                                 Env var
    ======================== ======================================== =====================
    ``opencode/go/{name}``   ``https://opencode.ai/zen/go/v1``        ``OPENCODE_API_KEY``
    ``opencode/zen/{name}``  ``https://opencode.ai/zen/v1``           ``OPENCODE_API_KEY``   OpenAI-compatible (uses ``openai/`` prefix)
    ``openrouter/``          ``https://openrouter.ai/api/v1``         ``OPENROUTER_API_KEY``
    ``groq/``                ``https://api.groq.com/openai/v1``       ``GROQ_API_KEY``
    ``together/``            ``https://api.together.xyz/v1``          ``TOGETHER_API_KEY``
    ``deepseek/``            ``https://api.deepseek.com/v1``          ``DEEPSEEK_API_KEY``
    ``cohere/``              ``https://api.cohere.ai/v1``             ``COHERE_API_KEY``
    ``gemini/``              ``https://generativelanguageapi…``       ``GEMINI_API_KEY``
    ``anthropic/``           ``https://api.anthropic.com``            ``ANTHROPIC_API_KEY``
    ``ollama/``              ``http://localhost:11434``               *(none — local)*
    ======================== ======================================== =====================

    Models without a recognized prefix (e.g. ``gpt-4o``) are passed
    directly to LiteLLM, which auto-detects the provider from the model
    name.

    Usage:
        # Opencode AI model via Zen endpoint
        client = LLMClient(model="opencode/zen/claude-sonnet-4", timeout=60)

        # Opencode AI model via Go endpoint
        client = LLMClient(model="opencode/go/deepseek-v4-flash", timeout=60)

        # Explicit provider override (bypasses auto-detection)
        client = LLMClient(model="opencode/zen/claude-sonnet-4", provider="openrouter")

        # Custom API base (bypasses auto-detection)
        client = LLMClient(model="my-model", api_base="https://my-proxy.example.com/v1")
    """

    def __init__(
        self,
        model: str = "gpt-4o",
        timeout: int = 60,
        provider: str | None = None,
        api_base: str | None = None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Initialize the LLM client.

        Args:
            model: LiteLLM model identifier (e.g.,
                ``"gpt-4o"``, ``"opencode/zen/claude-sonnet-4"``,
                ``"gpt-4o"``).
            timeout: Maximum time in seconds to wait for a response.
            provider: Explicit provider name (e.g. ``"openrouter"``).
                Overrides auto-detection from the model prefix.
            api_base: Explicit API base URL. Overrides auto-detection.
            event_callback: Async callable invoked with each streamed chunk
                ``{"type": "stream", "text": "..."}`` during
                ``generate_stream``.  ``None`` disables streaming callbacks.
            max_tokens: Default max output tokens per LLM call. Used as
                fallback when ``generate()``, ``generate_stream()``, or
                ``generate_with_tools()`` are called with ``max_tokens=None``.
        """
        self.model = model
        self.timeout = timeout
        self.provider = provider or self._detect_provider(model)
        self.api_base: str | None = None
        self.endpoint: str | None = None
        self.actual_model: str = model
        self.event_callback = event_callback

        # ── Parse endpoint-routed providers (e.g., opencode/go/deepseek-v4-flash) ──
        if self.provider and self.provider in PROVIDER_ROUTES:
            route = PROVIDER_ROUTES[self.provider]
            if isinstance(route, dict) and route.get("type") == "endpoint_routed":
                parts = model.split("/", 2)  # ["opencode", "go", "deepseek-v4-flash"]
                if len(parts) >= 3:
                    self.endpoint = parts[1]
                    self.actual_model = parts[2]
                    endpoints = route.get("endpoints", {})
                    if self.endpoint in endpoints:
                        self.api_base = endpoints[self.endpoint]

        # ── Check if provider needs "openai/" prefix for LiteLLM ──
        self.openai_compatible = False
        if self.provider and self.provider in PROVIDER_ROUTES:
            route = PROVIDER_ROUTES[self.provider]
            if isinstance(route, dict) and route.get("openai_compatible"):
                self.openai_compatible = True

        # ── For non-endpoint-routed providers, use standard resolution ──
        if self.api_base is None:
            self.api_base = api_base or self._resolve_api_base(self.provider)

        self.api_key = self._resolve_api_key(self.provider) if self.provider else None
        self.max_tokens: int | None = max_tokens
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost: float = 0.0
        self.call_count: int = 0
        self.cancel_event: asyncio.Event | None = None

    # ── Provider routing helpers ───────────────────────────────────────

    @staticmethod
    def _detect_provider(model: str) -> str | None:
        """Extract provider prefix, e.g. ``'opencode/zen/claude-sonnet-4'`` → ``'opencode'``."""
        if "/" in model:
            parts = model.split("/")
            if parts[0] in PROVIDER_ROUTES:
                return parts[0]
        return None

    @staticmethod
    def _resolve_api_base(provider: str | None) -> str | None:
        """Return the API base URL for the given provider, or ``None``.

        For endpoint-routed providers (e.g. ``opencode``), returns ``None``
        since the API base is determined by the specific endpoint parsed from
        the model ID (handled in ``__init__``).
        """
        if provider and provider in PROVIDER_ROUTES:
            route = PROVIDER_ROUTES[provider]
            if isinstance(route, dict) and route.get("type") == "endpoint_routed":
                return None
            return route.get("api_base")  # type: ignore
        return None

    @staticmethod
    def _resolve_api_key(provider: str | None) -> str | None:
        """Read the API key for the given provider from the environment."""
        if provider and provider in PROVIDER_ROUTES:
            env_var = PROVIDER_ROUTES[provider]["api_key_env"]
            if env_var:
                import os

                return os.environ.get(env_var)
        return None

    @staticmethod
    def _build_messages(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
        """Build the messages list for a LiteLLM completion call."""
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Send a completion request to the LLM.

        Retries up to 2 times with exponential backoff on failure.
        Enforces timeout via asyncio.wait_for.

        Args:
            system_prompt: The system-level instruction prompt.
            user_prompt: The user-specific prompt.
            temperature: Sampling temperature (0.0–1.0).
            max_tokens: Maximum tokens in the response (None = model default).
            cancel_event: Optional cancellation event — checked before each
                retry attempt.  Falls back to ``self.cancel_event`` if None.

        Returns:
            The generated text response.

        Raises:
            LLMError: If all retries fail or the request times out.
        """
        _cancel = cancel_event or self.cancel_event
        # Fall back to client-level max_tokens when not specified per call.
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        messages = self._build_messages(system_prompt, user_prompt)

        last_exception: Exception | None = None

        for attempt in range(3):  # initial + 2 retries
            # Check cancellation before each attempt.
            if _cancel and _cancel.is_set():
                raise LLMError("Session cancelled")

            try:
                response = await asyncio.wait_for(
                    self._acompletion(messages, temperature, effective_max_tokens),
                    timeout=self.timeout,
                )
                self.call_count += 1
                self._track_usage(response)
                return self._extract_content(response)

            except asyncio.TimeoutError:
                last_exception = LLMError(
                    f"LLM request timed out after {self.timeout}s "
                    f"(model={self.model}, attempt={attempt + 1})"
                )
                logger.warning(str(last_exception))

            except (
                litellm.BudgetExceededError,
                litellm.ContextWindowExceededError,
                litellm.RateLimitError,
            ) as e:
                # Token/rate errors are NOT retryable — fail immediately
                raise LLMError(
                    f"LLM resource exhausted (model={self.model}): {e}"
                ) from e

            except Exception as e:
                last_exception = e
                logger.warning(
                    "LLM request failed (model=%s, attempt=%d): %s",
                    self.model,
                    attempt + 1,
                    e,
                )

            if attempt < 2:
                # Check cancellation before sleeping for retry.
                if _cancel and _cancel.is_set():
                    raise LLMError("Session cancelled")
                backoff = 2 ** (attempt + 1)  # 2s, 4s
                logger.info("Retrying in %ds...", backoff)
                await asyncio.sleep(backoff)

        raise LLMError(
            f"LLM request failed after all retries (model={self.model}): "
            f"{last_exception}"
        )

    def _build_acompletion_kwargs(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        """Build the keyword-arguments dict for ``litellm.acompletion``.

        Shared by :meth:`_acompletion` (non-streaming) and
        :meth:`generate_stream` (streaming) so that both paths use the
        same provider routing and model prefix logic.
        """
        # OpenAI-compatible providers (e.g., opencode.ai) need "openai/" prefix
        if self.openai_compatible:
            litellm_model = f"openai/{self.actual_model}"
        else:
            litellm_model = self.actual_model

        kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        return kwargs

    async def _acompletion(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> Any:
        """Perform the actual LiteLLM acompletion call (non-streaming).

        Imported lazily to avoid import errors if litellm is not installed.
        """
        try:
            from litellm import acompletion
        except ImportError as e:
            raise LLMError(
                "LiteLLM is not installed. Install it with: pip install litellm"
            ) from e

        kwargs = self._build_acompletion_kwargs(messages, temperature, max_tokens)
        return await acompletion(**kwargs)

    async def generate_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Generate a response with streaming.

        Each text chunk is sent via ``self.event_callback`` as
        ``{"type": "stream", "text": chunk}``.  Falls back to
        non-streaming :meth:`generate` if the streaming call fails.

        Args:
            system_prompt: The system-level instruction prompt.
            user_prompt: The user-specific prompt.
            temperature: Sampling temperature (0.0–1.0).
            max_tokens: Maximum tokens in the response (``None`` = model default).
            cancel_event: Optional cancellation event — checked before the
                LLM call.  Falls back to ``self.cancel_event`` if None.

        Returns:
            The full generated text.
        """
        _cancel = cancel_event or self.cancel_event
        # Check cancellation before the LLM call.
        if _cancel and _cancel.is_set():
            raise LLMError("Session cancelled")

        # Fall back to client-level max_tokens when not specified per call.
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        messages = self._build_messages(system_prompt, user_prompt)
        full_text = ""
        _stream_buffer: list[str] = []

        try:
            from litellm import acompletion

            kwargs = self._build_acompletion_kwargs(messages, temperature, effective_max_tokens)
            kwargs["stream"] = True

            response = await acompletion(**kwargs)

            async for chunk in response:
                delta = ""
                if hasattr(chunk, "choices") and chunk.choices:
                    delta = chunk.choices[0].delta.content or ""
                full_text += delta

                if self.event_callback and delta:
                    # Buffer tokens and flush in chunks (5 tokens or sentence boundary).
                    _stream_buffer.append(delta)
                    if len(_stream_buffer) >= 5 or delta in (". ", "\n", ".\n"):
                        chunk_text = "".join(_stream_buffer)
                        _stream_buffer.clear()
                        await self.event_callback(
                            {"type": "stream", "text": chunk_text}
                        )

        except Exception as e:
            # Flush remaining buffer before falling back.
            if _stream_buffer and self.event_callback:
                chunk_text = "".join(_stream_buffer)
                _stream_buffer.clear()
                await self.event_callback({"type": "stream", "text": chunk_text})
            logger.warning("Streaming failed, falling back to non-streaming: %s", e)
            result = await self.generate(
                system_prompt,
                user_prompt,
                temperature,
                effective_max_tokens,
                cancel_event=_cancel,
            )
            if self.event_callback and result:
                await self.event_callback({"type": "stream", "text": result})
            return result

        # Flush remaining buffered tokens.
        if _stream_buffer and self.event_callback:
            chunk_text = "".join(_stream_buffer)
            _stream_buffer.clear()
            await self.event_callback({"type": "stream", "text": chunk_text})

        return full_text

    async def generate_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> str:
        """Generate a response with tool calling support.

        If the LLM calls a tool, the tool is executed and the result
        is fed back, continuing until a final text response is given.

        Currently supports: web_search

        Args:
            system_prompt: System-level prompt.
            user_prompt: User prompt.
            tools: List of LiteLLM tool definitions. If None, falls back to
                :meth:`generate_stream`.
            temperature: Sampling temperature.
            max_tokens: Max output tokens.
            cancel_event: Optional cancellation event — checked before each
                tool round.  Falls back to ``self.cancel_event`` if None.

        Returns:
            Final response text.
        """
        _cancel = cancel_event or self.cancel_event
        # Fall back to client-level max_tokens when not specified per call.
        effective_max_tokens = max_tokens if max_tokens is not None else self.max_tokens
        if not tools:
            return await self.generate_stream(
                system_prompt,
                user_prompt,
                temperature,
                effective_max_tokens,
                cancel_event=_cancel,
            )

        messages = self._build_messages(system_prompt, user_prompt)
        full_text = ""
        max_tool_rounds = 5  # Prevent infinite loops

        for tool_round in range(max_tool_rounds):
            # Check cancellation before each tool round.
            if _cancel and _cancel.is_set():
                raise LLMError("Session cancelled")

            kwargs = self._build_acompletion_kwargs(messages, temperature, effective_max_tokens)
            kwargs["tools"] = tools

            from litellm import acompletion

            # Tool calls accumulator: list of (id, name, args_json)
            tool_calls: list[tuple[str, str, str]] = []
            # Reset full_text for each round — only keep final non-tool output
            _round_text = ""

            try:
                kwargs["stream"] = True
                response = await acompletion(**kwargs)

                async for chunk in response:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Text content
                    if delta.content:
                        _round_text += delta.content
                        if self.event_callback:
                            await self.event_callback(
                                {"type": "stream", "text": delta.content}
                            )

                    # Tool calls
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index if tc.index is not None else 0
                            while len(tool_calls) <= idx:
                                tool_calls.append(("", "", ""))

                            tid, tname, targs = tool_calls[idx]
                            if tc.id:
                                tid = tc.id
                            if tc.function and tc.function.name:
                                tname = tc.function.name
                            if tc.function and tc.function.arguments:
                                targs += tc.function.arguments
                            tool_calls[idx] = (tid, tname, targs)

            except (
                litellm.BudgetExceededError,
                litellm.ContextWindowExceededError,
                litellm.RateLimitError,
            ) as e:
                raise LLMError(
                    f"LLM resource exhausted (model={self.model}): {e}"
                ) from e

            except Exception as e:
                logger.warning(
                    "Tool calling with streaming failed (tool_round=%d, tools=%s): %s. "
                    "Retrying without stream.",
                    tool_round,
                    [t.get("function", {}).get("name", "?") for t in (tools or [])],
                    e,
                    exc_info=True,
                )
                # Fallback: non-streaming
                kwargs["stream"] = False
                response = await acompletion(**kwargs)

                text_content = response.choices[0].message.content or ""
                _round_text = text_content
                if text_content:
                    if self.event_callback:
                        await self.event_callback(
                            {"type": "stream", "text": text_content}
                        )

                raw_tool_calls = response.choices[0].message.tool_calls or []
                for idx, tc in enumerate(raw_tool_calls):
                    while len(tool_calls) <= idx:
                        tool_calls.append(("", "", ""))
                    args_str = tc.function.arguments if tc.function else ""
                    tool_calls[idx] = (
                        tc.id,
                        tc.function.name if tc.function else "",
                        args_str,
                    )

                # Track usage from non-streaming response
                self._track_usage(response)

            if not tool_calls:
                # No tool calls — this is the final response, keep the text
                if _round_text:
                    full_text = _round_text
                # If _round_text is empty, keep existing full_text (non-streaming fallback)
                break
            # Discard intermediate text from rounds with tool calls
            # (it's just "Let me search..." chatter, not the final JSON)

            # Build assistant message with tool calls (required by API spec)
            assistant_tool_calls = [
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": tc_name, "arguments": tc_args},
                }
                for tc_id, tc_name, tc_args in tool_calls
            ]
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": assistant_tool_calls,
                }
            )

            # Execute tool calls
            import json

            from deepresearch.tools.web_search import (
                web_search as _web_search,
            )

            for tool_id, tool_name, tool_args_str in tool_calls:
                args = json.loads(tool_args_str) if tool_args_str else {}

                logger.debug(
                    "Executing tool '%s' (round %d, query='%s')",
                    tool_name,
                    tool_round,
                    args.get("query", ""),
                )
                if tool_name == "web_search":
                    query = args.get("query", "")
                    max_res = args.get("max_results", 5)
                    results = await _web_search(query, max_res)
                    result_text = json.dumps(results, ensure_ascii=False)

                    # Stream search activity to the output panel
                    if self.event_callback and results:
                        search_summary = f'\n[🔍 Web Search] Query: "{query}"\n'
                        for r in results[:3]:
                            title = (r.get("title", "") or "")[:60]
                            search_summary += f"  • {title}\n"
                        await self.event_callback(
                            {"type": "stream", "text": search_summary}
                        )

                    # Add tool result to messages
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": result_text,
                        }
                    )
                else:
                    logger.warning("Unknown tool call: %s", tool_name)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_id,
                            "content": f"Error: Unknown tool '{tool_name}'",
                        }
                    )

        if self.event_callback and full_text:
            await self.event_callback(
                {"type": "stream", "text": "\n\n[Final response complete]"}
            )

        return full_text

    def _track_usage(self, response: Any) -> None:
        """Track token usage and cost from the LLM response."""
        try:
            if hasattr(response, "usage") and response.usage:
                usage = response.usage
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                self.total_input_tokens += prompt_tokens
                self.total_output_tokens += completion_tokens
                self.total_cost += _lookup_cost(
                    self.actual_model,
                    prompt_tokens,
                    completion_tokens,
                )
        except Exception:
            logger.debug("Failed to track token usage", exc_info=True)

    def _extract_content(self, response: Any) -> str:
        """Extract the text content from the LLM response."""
        try:
            return response.choices[0].message.content or ""
        except (AttributeError, IndexError, KeyError) as e:
            raise LLMError(f"Failed to extract content from response: {e}") from e

    @staticmethod
    def _strip_tool_output(response: str) -> str:
        """Remove tool-related prefixes and output from LLM response text.

        Strips patterns like [🔍 Web Search], [Tool], bullet-point tool
        results, and query/result lines that may leak into the response.
        """
        import re

        # Remove [🔍 Web Search], [Tool], etc. block patterns
        cleaned = re.sub(
            r'\[[^\]]*\]\s*Query:.*?(?=\n\n|\n[^ []|$)', '', response, flags=re.DOTALL
        )
        # Remove bullet-point tool result lines
        cleaned = re.sub(r'^\s*[•\-]\s+.*$', '', cleaned, flags=re.MULTILINE)
        # Collapse multiple blank lines
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        return cleaned.strip()

    def parse_json_response(self, response: str) -> dict[str, Any]:
        """Parse a JSON response from the LLM.

        Attempts direct JSON parsing first. Falls back to extracting
        JSON from code blocks (```json ... ```).

        Args:
            response: The raw text response from the LLM.

        Returns:
            Parsed dictionary.

        Raises:
            LLMError: If the response cannot be parsed as JSON.
        """
        # Strip tool output that may pollute the response
        response = self._strip_tool_output(response)

        # Try direct parsing first
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code block
        json_pattern = r"```(?:json)?\s*\n?(.*?)```"
        match = re.search(json_pattern, response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Try stripping non-JSON text before first { and after last }
        try:
            start = response.index("{")
            end = response.rindex("}")
            return json.loads(response[start : end + 1])
        except (ValueError, json.JSONDecodeError):
            pass

        raise LLMError("Could not parse JSON from LLM response")

    def get_usage_stats(self) -> dict[str, int | float]:
        """Return current usage statistics.

        Returns:
            Dict with ``total_input_tokens``, ``total_output_tokens``,
            ``total_cost``, and ``call_count``.
        """
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            "call_count": self.call_count,
        }

    def estimate_cost(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> float:
        """Estimate cost before making a call.

        Uses a rough token count (word_count * 1.3) and the model's
        per-1K input rate.  Output cost is estimated assuming the same
        number of tokens as input (conservative).

        Args:
            system_prompt: The system prompt text.
            user_prompt: The user prompt text.

        Returns:
            Estimated USD cost (float).
        """
        total_text = f"{system_prompt}\n\n{user_prompt}"
        approx_tokens = int(len(total_text.split()) * 1.3)
        # Assume output is ~50% of input length.
        output_tokens = max(1, approx_tokens // 2)
        return _lookup_cost(self.actual_model, approx_tokens, output_tokens)

    def reset_stats(self) -> None:
        """Reset all counters (tokens, cost, call count)."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.call_count = 0
