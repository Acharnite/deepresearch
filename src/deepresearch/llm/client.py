"""LiteLLM client wrapper for async LLM interactions.

Provides retry logic, timeout enforcement, token tracking,
cost estimation, structured output parsing, circuit breaker,
and a shared connection pool.
"""

import asyncio
import json
import logging
import re
import time
from collections.abc import Awaitable
from typing import Any, Callable, ClassVar

import httpx
import litellm

from deepresearch.llm.tracker import TokenTracker
from deepresearch.observability.tracing import tracer

logger = logging.getLogger(__name__)

# ── Auto-detection cache for llama-cpp port probing ──────────────────────
_llamacpp_detected_url: str | None = None
_llamacpp_detected_at: float = 0.0
_LLAMACPP_PROBE_TIMEOUT: float = 1.5
_LLAMACPP_CACHE_TTL: float = 60.0
_LLAMACPP_PROBE_PORTS: list[int] = [8080, 7501, 8000, 1234]


def _detect_llamacpp_address() -> str | None:
    """Probe common ports for a running llama-server, return ``http://localhost:{port}/v1``.

    Checks ``/health`` on each candidate port.  The configured default port
    (from ``PROVIDER_ROUTES``) is tried first, followed by common llama.cpp /
    local-backend ports.  Results are cached for ``_LLAMACPP_CACHE_TTL``
    seconds to avoid repeated probes.
    """
    global _llamacpp_detected_url, _llamacpp_detected_at

    now = time.monotonic()
    if (
        _llamacpp_detected_url is not None
        and (now - _llamacpp_detected_at) < _LLAMACPP_CACHE_TTL
    ):
        return _llamacpp_detected_url

    # Build candidate list: configured default port first, then others
    default_port = PROVIDER_ROUTES.get("llama-cpp", {}).get("local_backend_port", 8080)
    candidate_ports = [default_port]
    for p in _LLAMACPP_PROBE_PORTS:
        if p not in candidate_ports:
            candidate_ports.append(p)

    for port in candidate_ports:
        try:
            resp = httpx.get(
                f"http://localhost:{port}/health",
                timeout=_LLAMACPP_PROBE_TIMEOUT,
            )
            if resp.status_code == 200:
                url = f"http://localhost:{port}/v1"
                _llamacpp_detected_url = url
                _llamacpp_detected_at = now
                logger.info("Auto-detected llama-server on port %d", port)
                return url
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            continue

    # Nothing found — clear cache so next call re-probes
    _llamacpp_detected_url = None
    _llamacpp_detected_at = now
    return None


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
        "openai_compatible": True,
    },
    "anthropic": {
        "api_base": "https://api.anthropic.com",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "ollama": {
        "api_base": "http://localhost:11434",
        "api_key_env": None,
        "local_backend": True,
    },
    "llama-cpp": {
        "api_base": None,
        "api_key_env": None,
        "local_backend": True,
        "local_backend_port": 8080,
    },
    "vllm": {
        "api_base": None,
        "api_key_env": None,
        "local_backend": True,
        "local_backend_port": 8000,
    },
    "lm-studio": {
        "api_base": None,
        "api_key_env": None,
        "local_backend": True,
        "local_backend_port": 1234,
    },
    "local-ai": {
        "api_base": None,
        "api_key_env": None,
        "local_backend": True,
        "local_backend_port": 8080,
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


class CircuitBreakerOpenError(LLMError):
    """Raised when the circuit breaker is open for a model."""


class CircuitBreaker:
    """Per-model circuit breaker to avoid hammering failing APIs.

    Tracks consecutive failures. Once ``failure_threshold`` is reached
    the breaker opens and refuses requests for ``reset_timeout`` seconds.
    After the timeout a single request is allowed through (half-open) to
    probe whether the service has recovered.
    """

    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 60.0) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at > self._reset_timeout:
            self._opened_at = None
            self._failures = 0
            return False  # Half-open — allow one request through
        return True

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._failure_threshold:
            self._opened_at = time.monotonic()

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None


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
    ``llama-cpp/``           ``http://localhost:8080/v1``             *(none — local)*  (custom address via Settings)
    ``vllm/``                ``http://localhost:8000/v1``             *(none — local)*  (custom address via Settings)
    ``lm-studio/``           ``http://localhost:1234/v1``             *(none — local)*  (custom address via Settings)
    ``local-ai/``            ``http://localhost:8080/v1``             *(none — local)*  (custom address via Settings)
    ``ollama/``              ``http://localhost:11434``               *(none — local)*  (custom address via Settings)
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

    # ── Shared connection pool (class-level, reused across instances) ──
    _pool: ClassVar[httpx.AsyncClient | None] = None
    _breakers: ClassVar[dict[str, CircuitBreaker]] = {}
    _pool_lock: ClassVar[asyncio.Lock | None] = None

    @classmethod
    def _get_pool(cls) -> httpx.AsyncClient:
        """Return the shared ``httpx.AsyncClient`` pool (lazy-initialised)."""
        if cls._pool is None:
            cls._pool = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0),
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=20,
                ),
            )
        return cls._pool

    def _get_breaker(self) -> CircuitBreaker:
        """Return the per-model circuit breaker for ``self.model``."""
        if self.model not in self._breakers:
            # Local backends need higher failure thresholds — timeouts are
            # often transient (model loading, context pressure).
            is_local = self._is_local_backend()
            self._breakers[self.model] = CircuitBreaker(
                failure_threshold=5 if is_local else 3,
                reset_timeout=30.0 if is_local else 60.0,
            )
        return self._breakers[self.model]

    def __init__(
        self,
        model: str = "gpt-4o",
        timeout: int = 60,
        provider: str | None = None,
        api_base: str | None = None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        max_tokens: int | None = None,
        tracker: TokenTracker | None = None,
        force_text_parsing: bool = False,
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
            force_text_parsing: When ``True``, skip native tool calling
                and parse tool calls from text output instead (useful for
                models without reliable native function calling).
        """
        self.model = model
        self.timeout = timeout
        self.provider = provider or self._detect_provider(model)

        # Local backends (especially quantized models) need more time for
        # complex prompts.  Bump timeout to 180s if the caller used the
        # default 60s and this is a local backend.
        if (
            self.timeout == 60
            and self.provider
            and PROVIDER_ROUTES.get(self.provider, {}).get("local_backend")
        ):
            self.timeout = 180

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
        self.force_text_parsing: bool = force_text_parsing
        self.tracker: TokenTracker | None = tracker
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost: float = 0.0
        self.call_count: int = 0
        self.cancel_event: asyncio.Event | None = None
        self.last_tool_results: list[dict[str, Any]] = []

    def _is_local_backend(self) -> bool:
        """Return True if the current provider is a local backend (ollama, llama-cpp, etc.)."""
        if not self.provider or self.provider not in PROVIDER_ROUTES:
            return False
        return bool(PROVIDER_ROUTES[self.provider].get("local_backend"))

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

        For **local backends** (e.g. ``ollama``, ``llama-cpp``, ``vllm``,
        ``lm-studio``, ``local-ai``), the address is resolved dynamically:
        1. If a custom address was set via ``local_backend_manager``, use that.
        2. Otherwise fall back to ``http://localhost:{port}/v1``.
        """
        if provider and provider in PROVIDER_ROUTES:
            route = PROVIDER_ROUTES[provider]
            if isinstance(route, dict) and route.get("type") == "endpoint_routed":
                return None

            # Handle local backends with dynamic address resolution
            if route.get("local_backend"):
                # Ollama does NOT use /v1 prefix (its API is at /api/chat).
                if provider == "ollama":
                    port = route.get("local_backend_port")
                    try:
                        from deepresearch.web.settings_manager import (
                            local_backend_manager,
                        )

                        custom_addr = local_backend_manager.get_address(provider)
                        if custom_addr:
                            return f"http://{custom_addr}"
                    except ImportError:
                        pass
                    return f"http://localhost:{port}" if port else route.get("api_base")

                port = route.get("local_backend_port")
                try:
                    from deepresearch.web.settings_manager import local_backend_manager

                    custom_addr = local_backend_manager.get_address(provider)
                    if custom_addr:
                        return f"http://{custom_addr}/v1"
                except ImportError:
                    pass  # Fall through to auto-detect

                # Auto-detect: probe common ports for a running llama-server
                detected = _detect_llamacpp_address()
                if detected:
                    try:
                        from deepresearch.web.settings_manager import (
                            local_backend_manager,
                        )

                        # Persist so future calls skip the probe
                        local_backend_manager.set_address(
                            provider,
                            detected.replace("http://", "").removesuffix("/v1"),
                        )
                    except ImportError:
                        pass
                    return detected

                # Fall back to standard port
                if port:
                    return f"http://localhost:{port}/v1"

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

        with tracer.start_as_current_span(
            f"llm.{self.model}",
            attributes={
                "llm.model": self.model,
                "llm.provider": self.provider or "unknown",
            },
        ) as llm_span:
            for attempt in range(3):  # initial + 2 retries
                # Check cancellation before each attempt.
                if _cancel and _cancel.is_set():
                    raise LLMError("Session cancelled")

                # Circuit breaker check — fast-fail if the model is on cooldown.
                breaker = self._get_breaker()
                if breaker.is_open:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker open for {self.model}"
                    )

                try:
                    response = await asyncio.wait_for(
                        self._acompletion(messages, temperature, effective_max_tokens),
                        timeout=self.timeout,
                    )
                    self.call_count += 1
                    self._track_usage(response)
                    # Record span attributes from usage
                    try:
                        if hasattr(response, "usage") and response.usage:
                            usage = response.usage
                            prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                            completion_tokens = (
                                getattr(usage, "completion_tokens", 0) or 0
                            )
                            llm_span.set_attribute("llm.prompt_tokens", prompt_tokens)
                            llm_span.set_attribute(
                                "llm.completion_tokens", completion_tokens
                            )
                            cost = _lookup_cost(
                                self.actual_model, prompt_tokens, completion_tokens
                            )
                            llm_span.set_attribute("llm.cost", cost)
                    except Exception:
                        pass
                    breaker.record_success()
                    return self._extract_content(response)

                except asyncio.TimeoutError:
                    breaker.record_failure()
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
                    breaker.record_failure()
                    raise LLMError(
                        f"LLM resource exhausted (model={self.model}): {e}"
                    ) from e

                except CircuitBreakerOpenError:
                    # Already checked above; re-raise without recording again.
                    raise

                except Exception as e:
                    breaker.record_failure()
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
        *,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the keyword-arguments dict for ``litellm.acompletion``.

        Shared by :meth:`_acompletion` (non-streaming) and
        :meth:`generate_stream` (streaming) so that both paths use the
        same provider routing and model prefix logic.

        Args:
            response_schema: Optional JSON schema for structured output.
                When provided and the model supports ``structured_outputs``,
                uses ``response_format: json_schema`` instead of prompt-based
                JSON instructions.
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

        # Disable thinking/reasoning for DeepSeek models — JSON output goes
        # into reasoning field instead of content when reasoning is enabled
        # (vllm #41132).  Always disable for JSON output tasks.
        if "deepseek" in self.model:
            kwargs["reasoning"] = {"effort": "none"}

        # Structured output mode: when a response_schema is provided and the
        # model is known to support structured_outputs, use json_schema format.
        if response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": response_schema,
                },
            }

        return kwargs

    async def _local_backend_request(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        stream: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Make a direct HTTP request to a local backend API.

        Handles both Ollama (/api/chat) and OpenAI-compatible backends (/v1/chat/completions).
        Returns the raw JSON response dict.
        """
        is_ollama = self.provider == "ollama"
        model_name = self.actual_model
        if "/" in model_name and not model_name.startswith("http"):
            _, _, maybe = model_name.partition("/")
            if maybe:
                model_name = maybe

        # Separate system message from chat history
        chat_messages: list[dict] = []
        system_prompt: str | None = None
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                chat_messages.append(msg)

        req_messages = chat_messages[:]
        if system_prompt:
            req_messages.insert(0, {"role": "system", "content": system_prompt})

        payload: dict = {
            "model": model_name,
            "messages": req_messages,
            "stream": stream,
        }
        if max_tokens is not None:
            if is_ollama:
                payload["options"] = {"num_predict": max_tokens}
            else:
                payload["max_tokens"] = max_tokens

        # Add tools if provided (for generate_with_tools)
        if tools:
            if is_ollama:
                ollama_tools = []
                for t in tools:
                    fn = t.get("function", {})
                    ollama_tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": fn.get("name", ""),
                                "description": fn.get("description", ""),
                                "parameters": fn.get("parameters", {}),
                            },
                        }
                    )
                payload["tools"] = ollama_tools
            else:
                payload["tools"] = tools

        # Suppress reasoning for llama-cpp backends — Qwen3 reasoning mode
        # puts all output into reasoning_content, leaving content empty and
        # making JSON parsing fail. chat_template_kwargs tells the Qwen3
        # template to skip thinking.
        if not is_ollama:
            if "Qwen" in model_name and "3" in model_name:
                payload["chat_template_kwargs"] = {"enable_thinking": False}
            else:
                payload["chat_template_kwargs"] = {}

        # Build URL
        base = self.api_base.rstrip("/")
        if is_ollama and base.endswith("/v1"):
            base = base[:-3]
        if is_ollama:
            url = f"{base}/api/chat"
        else:
            if not base.endswith("/v1"):
                url = f"{base}/v1/chat/completions"
            else:
                url = f"{base}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self.timeout or 120) as hc:
                resp = await hc.post(url, json=payload)
                if resp.status_code != 200:
                    raise LLMError(
                        f"Local backend returned HTTP {resp.status_code} "
                        f"({url}): {resp.text[:200]}"
                    )
                if stream:
                    # For streaming, return the raw response for SSE parsing
                    return {"_raw_response": resp, "_is_ollama": is_ollama}
                return resp.json()
        except httpx.TimeoutException:
            raise LLMError(f"Local backend {self.provider} timed out ({self.timeout}s)")
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot connect to {self.provider} at {self.api_base}: {e}")

    async def _local_backend_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> str:
        """Stream a response from a local backend via httpx SSE.

        Parses SSE chunks from the streaming response and yields text via callback.
        Falls back to reasoning_content for reasoning models.
        """
        is_ollama = self.provider == "ollama"
        model_name = self.actual_model
        if "/" in model_name and not model_name.startswith("http"):
            _, _, maybe = model_name.partition("/")
            if maybe:
                model_name = maybe

        chat_messages: list[dict] = []
        system_prompt: str | None = None
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt = msg.get("content", "")
            else:
                chat_messages.append(msg)

        req_messages = chat_messages[:]
        if system_prompt:
            req_messages.insert(0, {"role": "system", "content": system_prompt})

        payload: dict = {
            "model": model_name,
            "messages": req_messages,
            "stream": True,
        }
        if max_tokens is not None:
            if is_ollama:
                payload["options"] = {"num_predict": max_tokens}
            else:
                payload["max_tokens"] = max_tokens

        base = self.api_base.rstrip("/")
        if is_ollama and base.endswith("/v1"):
            base = base[:-3]
        if is_ollama:
            url = f"{base}/api/chat"
        else:
            url = (
                f"{base}/v1/chat/completions"
                if not base.endswith("/v1")
                else f"{base}/chat/completions"
            )

        full_text = ""
        try:
            async with httpx.AsyncClient(timeout=self.timeout or 120) as hc:
                async with hc.stream("POST", url, json=payload) as resp:
                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise LLMError(
                            f"Local backend returned HTTP {resp.status_code} "
                            f"({url}): {body[:200]}"
                        )
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if is_ollama:
                            delta = chunk.get("message", {})
                            text = delta.get("content", "")
                        else:
                            choices = chunk.get("choices", [{}])
                            delta = choices[0].get("delta", {}) if choices else {}
                            text = delta.get("content", "") or ""
                            # Reasoning model fallback
                            if not text:
                                text = delta.get("reasoning_content", "")

                        if text:
                            full_text += text
                            if event_callback:
                                await event_callback({"type": "stream", "text": text})
        except httpx.TimeoutException:
            raise LLMError(f"Local backend {self.provider} timed out ({self.timeout}s)")
        except httpx.ConnectError as e:
            raise LLMError(f"Cannot connect to {self.provider} at {self.api_base}: {e}")

        return full_text

    async def _acompletion(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
    ) -> Any:
        """Perform the actual completion call (non-streaming).

        For local backends, calls the provider's API directly via httpx.
        For all other providers, falls back to LiteLLM's acompletion.
        """
        if self._is_local_backend() and self.api_base:
            data = await self._local_backend_request(
                messages, temperature, max_tokens, stream=False
            )

            is_ollama = self.provider == "ollama"
            if is_ollama:
                msg = data.get("message", {})
                content = msg.get("content", "")
            else:
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {})
                content = msg.get("content", "") or ""
                # Reasoning model fallback: check reasoning_content if content is empty
                if not content:
                    content = msg.get("reasoning_content", "")

            # Build a fake LiteLLM ModelResponse so the rest of the code works
            from litellm.types.utils import ModelResponse, Choices, Message

            choice_obj = Choices(
                finish_reason="stop",
                message=Message(content=content, role="assistant"),
                index=0,
            )
            return ModelResponse(
                id=data.get("id", f"local-{self.actual_model}"),
                choices=[choice_obj],
                model=self.actual_model,
                usage=None,
            )

        # Fallback: use LiteLLM's acompletion for non-local providers
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

        # Circuit breaker check — fast-fail if the model is on cooldown.
        breaker = self._get_breaker()
        if breaker.is_open:
            raise CircuitBreakerOpenError(f"Circuit breaker open for {self.model}")

        try:
            if self._is_local_backend() and self.api_base:
                result = await self._local_backend_stream(
                    messages, temperature, effective_max_tokens, self.event_callback
                )
                breaker.record_success()
                return result

            from litellm import acompletion

            kwargs = self._build_acompletion_kwargs(
                messages, temperature, effective_max_tokens
            )
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

        except CircuitBreakerOpenError:
            raise

        except Exception as e:
            breaker.record_failure()
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

        breaker.record_success()
        return full_text

    async def generate_with_tools(
        self,
        system_prompt: str,
        user_prompt: str,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        cancel_event: asyncio.Event | None = None,
        response_schema: dict[str, Any] | None = None,
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
            response_schema: Optional JSON schema for structured output mode.

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
        collected_tool_results: list[dict[str, Any]] = []

        logger.warning(
            "generate_with_tools: starting with %d tools", len(tools) if tools else 0
        )
        for tool_round in range(max_tool_rounds):
            # Check cancellation before each tool round.
            if _cancel and _cancel.is_set():
                raise LLMError("Session cancelled")

            kwargs = self._build_acompletion_kwargs(
                messages,
                temperature,
                effective_max_tokens,
                response_schema=response_schema,
            )
            kwargs["tools"] = tools

            from litellm import acompletion

            # Tool calls accumulator: list of (id, name, args_json)
            tool_calls: list[tuple[str, str, str]] = []
            # Reset full_text for each round — only keep final non-tool output
            _round_text = ""

            # ── If force_text_parsing is set, skip native tools ────────
            # and fall through to text-based tool call parsing below.
            use_text_parsing = self.force_text_parsing

            # ── Local backends don't support streaming tool calls ──────
            # (they hang silently with no output). Use direct HTTP to
            # avoid LiteLLM bugs with Ollama and other local providers.
            if not use_text_parsing and self._is_local_backend():
                logger.warning(
                    "generate_with_tools: using direct HTTP for %s", self.provider
                )

                data = await self._local_backend_request(
                    messages,
                    temperature,
                    effective_max_tokens,
                    stream=False,
                    tools=tools,
                )

                is_ollama = self.provider == "ollama"
                if is_ollama:
                    msg = data.get("message", {})
                    text_content = msg.get("content", "") or ""
                    _round_text = text_content
                    if text_content and self.event_callback:
                        await self.event_callback(
                            {"type": "stream", "text": text_content}
                        )
                    raw_tc = msg.get("tool_calls", [])
                    for idx, tc in enumerate(raw_tc):
                        while len(tool_calls) <= idx:
                            tool_calls.append(("", "", ""))
                        fn_info = tc.get("function", {})
                        tool_calls[idx] = (
                            f"call_{idx}",
                            fn_info.get("name", ""),
                            json.dumps(fn_info.get("arguments", {})),
                        )
                else:
                    choice = data.get("choices", [{}])[0]
                    msg = choice.get("message", {})
                    text_content = msg.get("content", "") or ""
                    # Reasoning model fallback
                    if not text_content:
                        text_content = msg.get("reasoning_content", "") or ""
                    _round_text = text_content
                    if text_content and self.event_callback:
                        await self.event_callback(
                            {"type": "stream", "text": text_content}
                        )
                    raw_tc = msg.get("tool_calls", [])
                    for idx, tc in enumerate(raw_tc):
                        while len(tool_calls) <= idx:
                            tool_calls.append(("", "", ""))
                        fn_info = tc.get("function", {})
                        tool_calls[idx] = (
                            tc.get("id", f"call_{idx}"),
                            fn_info.get("name", ""),
                            fn_info.get("arguments", ""),
                        )
            elif use_text_parsing:
                # Force text parsing mode: call the LLM without native tools
                # and let the text-based parser extract tool calls from output.
                text_kwargs = {k: v for k, v in kwargs.items() if k != "tools"}
                text_kwargs["stream"] = True
                try:
                    response = await acompletion(**text_kwargs)
                    async for chunk in response:
                        delta = chunk.choices[0].delta if chunk.choices else None
                        if delta and delta.content:
                            _round_text += delta.content
                            if self.event_callback:
                                await self.event_callback(
                                    {"type": "stream", "text": delta.content}
                                )
                except Exception as e:
                    logger.warning(
                        "Text-parsing stream failed (round %d): %s. "
                        "Falling back to non-streaming.",
                        tool_round,
                        e,
                    )
                    text_kwargs["stream"] = False
                    response = await acompletion(**text_kwargs)
                    text_content = response.choices[0].message.content or ""
                    _round_text = text_content
                    if text_content and self.event_callback:
                        await self.event_callback(
                            {"type": "stream", "text": text_content}
                        )
                    self._track_usage(response)
            else:
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
                    try:
                        response = await acompletion(**kwargs)
                    except Exception as e2:
                        logger.warning(
                            "Non-streaming tool call also failed (model=%s): %s. "
                            "Retrying without tools.",
                            self.model,
                            e2,
                        )
                        # Remove tools and fall back to plain generate_stream
                        return await self.generate_stream(
                            system_prompt=system_prompt,
                            user_prompt=user_prompt,
                            temperature=temperature,
                            max_tokens=effective_max_tokens,
                            cancel_event=_cancel,
                        )

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

            # Handle models that output tool calls as text content
            # (e.g. local models without native function calling support)
            if not tool_calls and _round_text:
                from deepresearch.tools.parser import ToolCallParser
                from deepresearch.tools.registry import resolve_tool

                parser = ToolCallParser()
                parsed = parser.parse(_round_text)
                if parsed:
                    for pc in parsed:
                        tool_def = resolve_tool(pc.name)
                        if tool_def:
                            logger.debug(
                                "Detected text-embedded tool call '%s' "
                                "for model without native function calling (format=%s)",
                                pc.name,
                                pc.source,
                            )
                            args_str = json.dumps(pc.arguments, ensure_ascii=False)
                            tool_calls.append((pc.call_id, pc.name, args_str))
                    _round_text = ""  # Not the final response — execute tool

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
            from deepresearch.tools.registry import resolve_tool

            for tool_id, tool_name, tool_args_str in tool_calls:
                args = json.loads(tool_args_str) if tool_args_str else {}

                tool_def = resolve_tool(tool_name)
                if tool_def is not None and tool_def.handler is not None:
                    logger.debug(
                        "Executing tool '%s' (round %d, args=%s)",
                        tool_name,
                        tool_round,
                        args,
                    )

                    # If the tool is web_search, specifically handle
                    # query/max_results parameter extraction for stream display
                    is_search = tool_def.name == "web_search" if tool_def else False

                    if is_search:
                        query = args.get("query", "")
                        max_res = args.get("max_results", 5)
                        results = await tool_def.handler(query, max_res)
                    else:
                        results = await tool_def.handler(**args)

                    result_text = json.dumps(results, ensure_ascii=False)

                    # Collect search results for source attribution
                    collected_tool_results.extend(results)

                    # Stream search activity to the output panel
                    if is_search and self.event_callback and results:
                        q = args.get("query", "")
                        search_summary = f'\n[🔍 Web Search] Query: "{q}"\n'

                        # Show time_filter from first result if available
                        tf = results[0].get("time_filter") if results else None
                        if tf:
                            search_summary += f"   Time filter: {tf}\n"
                        # Show provider source + number of results
                        sources = set(
                            r.get("source", "") for r in results if r.get("source")
                        )
                        if sources:
                            search_summary += (
                                f"   Providers: {', '.join(sorted(sources))}\n"
                            )

                        # Show tl_dr if available (enriched search - ADR-0017)
                        tl_dr = results[0].get("tl_dr") if results else None
                        if tl_dr:
                            search_summary += f"   TL;DR: {tl_dr[:200]}\n"

                        # Show content stats
                        content_count = sum(1 for r in results if r.get("content"))
                        if content_count > 0:
                            total_chars = sum(
                                len(r.get("content", "") or "") for r in results
                            )
                            search_summary += f"   Fetched content: {total_chars:,} chars from {content_count} pages\n"

                        # Show result titles
                        for r in results[:5]:
                            title = (r.get("title", "") or "")[:80]
                            source = r.get("source", "")
                            src_tag = f" [{source}]" if source else ""
                            search_summary += f"  • {title}{src_tag}\n"

                        if len(results) > 5:
                            search_summary += (
                                f"  ... and {len(results) - 5} more results\n"
                            )

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
                    logger.warning(
                        "Unknown tool call: %s in round %s", tool_name, tool_round
                    )
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

        self.last_tool_results = collected_tool_results
        return full_text

    def _track_usage(self, response: Any) -> None:
        """Track token usage and cost from the LLM response."""
        try:
            if hasattr(response, "usage") and response.usage:
                usage = response.usage
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                cost = _lookup_cost(self.actual_model, prompt_tokens, completion_tokens)
                self.total_input_tokens += prompt_tokens
                self.total_output_tokens += completion_tokens
                self.total_cost += cost
                if self.tracker is not None:
                    self.tracker.record(
                        model=self.model,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        cost=cost,
                    )
        except Exception:
            logger.debug("Failed to track token usage", exc_info=True)

    def _extract_content(self, response: Any) -> str:
        """Extract the text content from the LLM response.

        For reasoning models, falls back to reasoning_content if content is empty.
        """
        try:
            content = response.choices[0].message.content or ""
            if not content:
                # Reasoning model fallback
                content = (
                    getattr(response.choices[0].message, "reasoning_content", "") or ""
                )
            return content
        except (AttributeError, IndexError, KeyError) as e:
            raise LLMError(f"Failed to extract content from response: {e}") from e

    @staticmethod
    def _strip_tool_output(response: str) -> str:
        """Remove tool-related prefixes and output from LLM response text.

        Strips patterns like [🔍 Web Search], [Tool], bullet-point tool
        results, numbered list items, and non-JSON lines that may leak
        into the response from generate_with_tools().
        """
        import re

        # Remove [🔍 Web Search], [Tool], etc. block patterns
        cleaned = re.sub(
            r"\[[^\]]*\]\s*Query:.*?(?=\n\n|\n[^ []|$)", "", response, flags=re.DOTALL
        )
        # Remove bullet-point tool result lines
        cleaned = re.sub(r"^\s*[•\-]\s+.*$", "", cleaned, flags=re.MULTILINE)
        # Remove numbered list items from search results (e.g. "1. Some title")
        cleaned = re.sub(r"^\s*\d+\.\s+.*$", "", cleaned, flags=re.MULTILINE)
        # Remove lines that don't contain any JSON (non-JSON noise lines)
        cleaned = re.sub(r"^(?!.*\{).*$", "", cleaned, flags=re.MULTILINE)
        # Collapse multiple blank lines
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def parse_json_response(self, response: str) -> dict[str, Any]:
        """Parse a JSON response from the LLM.

        Tries extracting from markdown code blocks first, then direct
        parsing, then strips tool output as a last resort.

        Args:
            response: The raw text response from the LLM.

        Returns:
            Parsed dictionary.

        Raises:
            LLMError: If the response cannot be parsed as JSON.
        """
        json_pattern = r"```(?:json)?\s*\n?(.*?)```"

        # 1. Try extracting from markdown code block FIRST (preserves JSON)
        match = re.search(json_pattern, response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 2. Try direct parsing
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 3. Last resort: strip tool output and try again
        cleaned = self._strip_tool_output(response)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # 4. Try extracting from cleaned response
        match = re.search(json_pattern, cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 5. Try stripping non-JSON text before first { and after last }
        try:
            start = cleaned.index("{")
            end = cleaned.rindex("}")
            return json.loads(cleaned[start : end + 1])
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
