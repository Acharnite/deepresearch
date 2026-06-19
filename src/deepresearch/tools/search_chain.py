"""Multi-provider search chain with ordered fallback.

Runs search providers in a configured order until one returns results.
Each provider has independent retry, timeout, and rate limiting.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Callable

from deepresearch.observability.tracing import tracer

logger = logging.getLogger(__name__)

# Sentinel to distinguish "not passed" from "passed as None"
_UNSET = object()

# ── Type alias ─────────────────────────────────────────────────────────────

SearchProvider = Callable[..., list[dict[str, Any]]]

# ── Default provider order ─────────────────────────────────────────────────

_DEFAULT_ORDER = ["searxng", "duckduckgo", "brave", "google_pse", "tavily", "serper"]

# ── Provider module lookup (lazy-imported) ─────────────────────────────────

_PROVIDER_MODULES: dict[str, SearchProvider] = {}


def _get_provider(name: str) -> SearchProvider | None:
    """Lazy-import and return the search function for a provider name.

    Returns ``None`` if the provider module cannot be loaded (unlikely
    unless the package is malformed).
    """
    if name in _PROVIDER_MODULES:
        return _PROVIDER_MODULES[name]

    _imports = {
        "searxng": "deepresearch.tools.providers.searxng",
        "duckduckgo": "deepresearch.tools.providers.duckduckgo",
        "brave": "deepresearch.tools.providers.brave",
        "google_pse": "deepresearch.tools.providers.google_pse",
        "tavily": "deepresearch.tools.providers.tavily",
        "serper": "deepresearch.tools.providers.serper",
    }

    module_path = _imports.get(name)
    if module_path is None:
        logger.warning("Unknown search provider '%s'", name)
        return None

    try:
        import importlib

        mod = importlib.import_module(module_path)
        provider_fn = getattr(mod, "search", None)
        if provider_fn is None:
            logger.warning("Provider '%s' has no 'search' function", name)
            return None
        _PROVIDER_MODULES[name] = provider_fn
        return provider_fn
    except Exception as e:
        logger.warning("Failed to load provider '%s': %s", name, e)
        return None


# ── Provider readiness check ───────────────────────────────────────────────


def _is_provider_configured(name: str) -> bool:
    """Check if a provider has the required API keys configured.

    Free providers (SearXNG, DuckDuckGo) are always available.
    """
    required_env = {
        "brave": "BRAVE_API_KEY",
        "google_pse": "GOOGLE_PSE_API_KEY",
        "tavily": "TAVILY_API_KEY",
        "serper": "SERPER_API_KEY",
    }
    env_var = required_env.get(name)
    if env_var is not None and not os.environ.get(env_var):
        logger.debug("Provider '%s' skipped — %s not set", name, env_var)
        return False
    # Google PSE also needs CX
    if name == "google_pse" and not os.environ.get("GOOGLE_PSE_CX"):
        logger.debug("Provider 'google_pse' skipped — GOOGLE_PSE_CX not set")
        return False
    return True


# ── SearchChain ────────────────────────────────────────────────────────────


class SearchChain:
    """Multi-provider fallback chain with per-provider retry and timeout.

    Providers are run in order.  If a provider returns empty results or
    raises an exception, the next provider in the chain is tried.

    Usage::

        chain = SearchChain()
        results = await chain.search("latest AI news")
    """

    def __init__(
        self,
        provider_order: list[str] | None = _UNSET,
        per_provider_timeout: int = 15,
        max_retries: int = 1,
        semaphore_size: int = 3,
    ) -> None:
        """Initialize the search chain.

        Args:
            provider_order: Ordered list of provider names.  Defaults to
                ``SEARCH_PROVIDER_ORDER`` env var (comma-separated) or the
                built-in default ``[searxng, duckduckgo, brave, google_pse,
                tavily, serper]``.
            per_provider_timeout: Max seconds for each provider call.
            max_retries: Number of retries per provider on failure.
            semaphore_size: Max concurrent searches per provider.
        """
        # Only read env var when provider_order is NOT explicitly passed
        if provider_order is _UNSET:
            order_env = os.environ.get("SEARCH_PROVIDER_ORDER")
            if order_env:
                provider_order = [p.strip() for p in order_env.split(",") if p.strip()]
                logger.debug("Using SEARCH_PROVIDER_ORDER from env: %s", provider_order)
        self._provider_order = provider_order or _DEFAULT_ORDER
        self._per_provider_timeout = per_provider_timeout
        self._max_retries = max_retries
        # Per-provider semaphore for independent rate limiting
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._semaphore_size = semaphore_size

    def _get_semaphore(self, name: str) -> asyncio.Semaphore:
        """Return the per-provider semaphore (lazy-initialised)."""
        if name not in self._semaphores:
            self._semaphores[name] = asyncio.Semaphore(self._semaphore_size)
        return self._semaphores[name]

    async def search(
        self,
        query: str,
        max_results: int = 5,
        time_filter: str | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> list[dict[str, Any]]:
        """Run providers in order until one returns results.

        Args:
            query: The search query.
            max_results: Max results to return.
            time_filter: Unified time filter (day/week/month/year).
            cancel_event: Optional cancellation event.

        Returns:
            List of result dicts, or empty list if all providers fail.
        """
        with tracer.start_as_current_span(
            "search_chain",
            attributes={
                "search.query": query[:100],
                "search.max_results": max_results,
                "search.providers": ",".join(self._provider_order),
            },
        ) as span:
            for provider_name in self._provider_order:
                if cancel_event and cancel_event.is_set():
                    logger.debug("Search chain cancelled")
                    return []

                # Skip unconfigured providers
                if not _is_provider_configured(provider_name):
                    continue

                provider_fn = _get_provider(provider_name)
                if provider_fn is None:
                    continue

                provider = provider_name
                sem = self._get_semaphore(provider)

                for attempt in range(self._max_retries + 1):
                    if cancel_event and cancel_event.is_set():
                        return []

                    async with sem:
                        try:
                            results = await asyncio.wait_for(
                                provider_fn(
                                    query=query,
                                    max_results=max_results,
                                    time_filter=time_filter,
                                    cancel_event=cancel_event,
                                ),
                                timeout=self._per_provider_timeout,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                "Provider '%s' timed out after %ds (attempt %d/%d)",
                                provider,
                                self._per_provider_timeout,
                                attempt + 1,
                                self._max_retries + 1,
                            )
                            continue
                        except Exception as e:
                            logger.warning(
                                "Provider '%s' failed (attempt %d/%d): %s",
                                provider,
                                attempt + 1,
                                self._max_retries + 1,
                                e,
                            )
                            continue

                    if results:
                        logger.debug(
                            "Provider '%s' returned %d results for '%s'",
                            provider,
                            len(results),
                            query,
                        )
                        span.set_attribute("search.result_provider", provider)
                        span.set_attribute("search.result_count", len(results))
                        return results
                    else:
                        logger.debug(
                            "Provider '%s' returned empty results for '%s'",
                            provider,
                            query,
                        )
                        # Empty results — break retry, try next provider
                        break

            # All providers failed
            logger.warning(
                "All search providers failed for query '%s' — returning empty",
                query,
            )
            span.set_attribute("search.result_provider", "none")
            span.set_attribute("search.result_count", 0)
            return []

    @property
    def provider_order(self) -> list[str]:
        """Return the configured provider order (read-only)."""
        return list(self._provider_order)
