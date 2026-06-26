"""Web search tool for DeepResearch agents.

Refactored per ADR-0017.  Delegates to ``SearchChain`` for multi-provider
fallback and wires in content fetching, caching, and time filter
auto-detection.

Backward-compatible:
  - ``web_search(query, max_results=5, retries=3)`` signature preserved
  - ``WEB_SEARCH_TOOL`` dict preserved
  - ``get_search_health_info()`` / ``get_search_semaphore_info()`` preserved
  - Old ``_search_engine`` / ``_search_health`` globals kept for compat
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from typing import Any

from deepresearch.observability.tracing import tracer
from deepresearch.tools.cache import SearchCache
from deepresearch.tools.content_fetcher import fetch_page_content
from deepresearch.tools.search_chain import SearchChain
from deepresearch.tools.time_filter import detect_time_filter

logger = logging.getLogger(__name__)

# ── Configuration from environment (ADR-0017) ──────────────────────────

_SEARCH_CACHE_ENABLED: bool = os.environ.get(
    "SEARCH_CACHE_ENABLED", "true"
).lower() in ("1", "true", "yes")
_SEARCH_CACHE_TTL_EVERGREEN: int = int(
    os.environ.get("SEARCH_CACHE_TTL_EVERGREEN", "3600")
)
_SEARCH_CACHE_TTL_CURRENT: int = int(os.environ.get("SEARCH_CACHE_TTL_CURRENT", "300"))
_SEARCH_FETCH_CONTENT: bool = os.environ.get(
    "SEARCH_FETCH_CONTENT", "true"
).lower() in ("1", "true", "yes")
_SEARCH_FETCH_MAX_PAGES: int = int(os.environ.get("SEARCH_FETCH_MAX_PAGES", "5"))
_SEARCH_FETCH_MAX_CHARS: int = int(os.environ.get("SEARCH_FETCH_MAX_CHARS", "2000"))


# ── Global lazy singletons (patchable in tests) ────────────────────────

_search_chain: SearchChain | None = None
_search_cache: SearchCache | None = None


def _get_search_chain() -> SearchChain:
    """Return the global ``SearchChain`` instance (lazy-init)."""
    global _search_chain
    if _search_chain is None:
        _search_chain = SearchChain()
    return _search_chain


def _get_search_cache() -> SearchCache | None:
    """Return the global ``SearchCache`` instance (lazy-init, best-effort).

    Returns ``None`` if caching is disabled or initialization fails.
    """
    global _search_cache
    if _search_cache is None and _SEARCH_CACHE_ENABLED:
        try:
            _search_cache = SearchCache()
        except Exception as e:
            logger.warning("Failed to initialize SearchCache: %s", e)
            _search_cache = None
    return _search_cache


# ── Backward-compat globals (kept for existing consumers) ──────────────

_search_engine: str = os.environ.get("SEARCH_ENGINE", "searxng")
_search_health: str = "unknown"
_last_search_latency_ms: float = 0.0
_search_semaphore = asyncio.Semaphore(3)
_searxng_url: str = os.environ.get("SEARXNG_URL", "http://localhost:8888")
_searxng_fallback_url: str = os.environ.get("SEARXNG_FALLBACK_URL", "https://searx.be")
_searxng_engines: list[str] = ["google", "bing", "startpage"]
_searxng_categories: list[str] = ["general"]
_searxng_timeout: int = 10


def _load_search_config() -> None:
    """Load search config from the settings manager (backward compat)."""
    # No-op — config is now managed by individual providers via their own
    # _load_config() calls.  Kept so callers that invoke it before
    # web_search() continue to work without error.
    pass


# ── Tool definition for LiteLLM function calling ───────────────────────

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information on a topic. "
            "Use this when you need up-to-date facts, recent developments, "
            "or external sources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query (clear, specific, 2-8 words).",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of results to return (1-10, default 5).",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


# ── Helpers ────────────────────────────────────────────────────────────


def _make_cache_key(query: str, max_results: int) -> str:
    """Create a SHA-256 cache key from query parameters."""
    normalized = " ".join(query.strip().lower().split())
    raw = "|".join([normalized, str(max_results)])
    return hashlib.sha256(raw.encode()).hexdigest()


def _extract_quotes(text: str, max_quotes: int = 3) -> list[str]:
    """Extract notable quoted excerpts from text (simple heuristic)."""
    quotes: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text)
    for s in sentences[:10]:
        s = s.strip()
        if 40 < len(s) < 300:
            quotes.append(s)
        if len(quotes) >= max_quotes:
            break
    return quotes


def _normalize_query(query: str) -> str:
    """Normalize a search query for cache key consistency (backward compat)."""
    return query.strip().lower()


def _cache_key(query: str) -> str:
    """Return a short hash for a normalized query (backward compat).

    The old inline cache-key function is preserved for any external
    callers.  New code uses ``SearchCache`` instead.
    """
    normalized = _normalize_query(query)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── Main search function ───────────────────────────────────────────────


async def web_search(
    query: str, max_results: int = 5, retries: int = 3
) -> list[dict[str, Any]]:
    """Execute a web search with multi-provider fallback and enrichment.

    Delegates to ``SearchChain`` for provider dispatch.  Wires in time
    filter auto-detection, disk caching, and parallel content fetching.

    Args:
        query: The search query.
        max_results: Max results to return (1-10).
        retries: Ignored in the new implementation (per-provider retry
            is handled by ``SearchChain``).  Kept in the signature for
            backward compatibility.

    Returns:
        List of enriched result dicts.  Each dict contains at minimum:
        ``title``, ``snippet``, ``url`` (backward compat), plus enriched
        fields: ``content``, ``key_points``, ``tl_dr``, ``quotes``,
        ``source``, ``time_filter``.
    """
    with tracer.start_as_current_span(
        "web_search",
        attributes={
            "search.query": query[:100],
            "search.max_results": max_results,
        },
    ):
        # 1. Auto-detect time filter from query keywords
        time_filter = detect_time_filter(query)

        # 2. Cache lookup
        cache = _get_search_cache()
        cache_key = None
        if cache:
            cache_key = _make_cache_key(query, max_results)
            cached = await cache.get(cache_key)
            if cached is not None:
                logger.debug("Cache hit for '%s' (%d results)", query, len(cached))
                return cached

        # 3. Execute search via SearchChain
        chain = _get_search_chain()
        results = await chain.search(
            query=query,
            max_results=max_results,
            time_filter=time_filter,
        )

        # 4. Build enriched result envelope
        enriched: list[dict[str, Any]] = []
        for r in results:
            enriched.append(
                {
                    "title": r.get("title", ""),
                    "snippet": r.get("snippet", ""),
                    "url": r.get("url", ""),
                    "content": None,
                    "key_points": [],
                    "tl_dr": None,
                    "quotes": [],
                    "source": r.get("source", "unknown"),
                    "time_filter": time_filter,
                }
            )

        # 5. Cache results
        if cache and cache_key and enriched:
            ttl = (
                _SEARCH_CACHE_TTL_CURRENT
                if time_filter
                else _SEARCH_CACHE_TTL_EVERGREEN
            )
            await cache.set(cache_key, enriched, ttl=ttl)

        # 6. Parallel content fetching
        if _SEARCH_FETCH_CONTENT and enriched:
            urls = [r["url"] for r in enriched[:_SEARCH_FETCH_MAX_PAGES]]
            fetched = await fetch_page_content(urls, max_chars=_SEARCH_FETCH_MAX_CHARS)
            url_to_content = {f["url"]: f for f in fetched}
            for r in enriched:
                fc = url_to_content.get(r["url"])
                if fc and fc.get("content"):
                    r["content"] = fc["content"]
                    # Basic quote extraction from fetched content
                    quotes = _extract_quotes(fc["content"])
                    if quotes:
                        r["quotes"] = quotes

        logger.debug(
            "Web search for '%s' returned %d results",
            query,
            len(enriched),
        )
        return enriched


# ── Info helpers for server endpoints (backward compat) ────────────────


def get_search_semaphore_info() -> dict[str, int]:
    """Return current search concurrency state (backward compat)."""
    return {
        "active_searches": 3 - _search_semaphore._value,  # type: ignore[attr-defined]
        "max_searches": 3,
    }


def get_search_health_info() -> dict[str, Any]:
    """Return full search engine status for server endpoints.

    Returns backward-compatible keys.  Some values are static since
    the new architecture delegates health to individual providers.
    """
    return {
        "engine": _search_engine,
        "searxng_url": _searxng_url,
        "searxng_fallback_url": _searxng_fallback_url,
        "searxng_engines": _searxng_engines,
        "searxng_categories": _searxng_categories,
        "searxng_timeout": _searxng_timeout,
        "status": _search_health,
        "last_search_latency_ms": _last_search_latency_ms,
    }
