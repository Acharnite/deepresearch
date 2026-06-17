"""Web search tool for DeepeResearch agents.

Supports two backends:
  - SearXNG (default): self-hosted metasearch engine, no rate limits
  - DuckDuckGo (legacy): scraped via ddgs library

The active backend is controlled by ``_search_engine`` (feature flag),
which reads from the settings manager on first use.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any

import httpx

from deepresearch.observability.tracing import tracer

logger = logging.getLogger(__name__)

# ── SearXNG configuration (loaded from settings / env) ────────────────
_search_engine: str = os.environ.get("SEARCH_ENGINE", "searxng")
_searxng_url: str = os.environ.get("SEARXNG_URL", "http://localhost:8888")
_searxng_fallback_url: str = os.environ.get(
    "SEARXNG_FALLBACK_URL", "https://searx.be"
)
_searxng_engines: list[str] = ["google", "bing", "startpage"]
_searxng_categories: list[str] = ["general"]
_searxng_timeout: int = 10

# ── Health tracking ───────────────────────────────────────────────────
_search_health: str = "unknown"  # "healthy" | "degraded" | "unhealthy"
_last_search_latency_ms: float = 0.0


def _load_search_config() -> None:
    """Load search config from the settings manager (if available)."""
    global _search_engine, _searxng_url, _searxng_fallback_url
    global _searxng_engines, _searxng_categories, _searxng_timeout

    try:
        from deepresearch.web.settings_manager import settings_manager

        config = settings_manager.get_search_config()
        if config.get("engine"):
            _search_engine = config["engine"]
        if config.get("searxng_url"):
            _searxng_url = config["searxng_url"]
        if config.get("searxng_fallback_url"):
            _searxng_fallback_url = config["searxng_fallback_url"]
        if config.get("searxng_engines"):
            _searxng_engines = config["searxng_engines"]
        if config.get("searxng_categories"):
            _searxng_categories = config["searxng_categories"]
        if config.get("searxng_timeout"):
            _searxng_timeout = config["searxng_timeout"]
        logger.debug(
            "Loaded search config: engine=%s, url=%s", _search_engine, _searxng_url
        )
    except Exception as e:
        logger.debug("Could not load search config (using defaults): %s", e)


# ── Global semaphore to limit concurrent searches ─────────────────────
_search_semaphore = asyncio.Semaphore(3)


# ── Tool definition for LiteLLM function calling ──────────────────────

WEB_SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for current information on a topic. Use this when you need up-to-date facts, recent developments, or external sources.",
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


def _normalize_query(query: str) -> str:
    """Normalize a search query for cache key consistency."""
    return query.strip().lower()


def _cache_key(query: str) -> str:
    """Return a short hash for a normalized query."""
    normalized = _normalize_query(query)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ── SearXNG backend ──────────────────────────────────────────────────


async def _searxng_search(
    query: str, max_results: int = 5
) -> list[dict[str, str]]:
    """Search via SearXNG JSON API with primary/fallback fallback.

    Returns:
        List of dicts with 'title', 'snippet', 'url' keys.
    """
    global _search_health, _last_search_latency_ms

    params = {
        "q": query,
        "format": "json",
        "categories": ",".join(_searxng_categories),
        "engines": ",".join(_searxng_engines),
    }

    for base_url in [_searxng_url, _searxng_fallback_url]:
        try:
            t0 = time.monotonic()
            async with httpx.AsyncClient(
                timeout=_searxng_timeout
            ) as client:
                resp = await client.get(f"{base_url}/search", params=params)
                resp.raise_for_status()
                data = resp.json()

            latency = (time.monotonic() - t0) * 1000
            _last_search_latency_ms = latency

            results: list[dict[str, str]] = []
            for item in data.get("results", [])[:max_results]:
                results.append(
                    {
                        "title": (item.get("title", "") or "")[:80],
                        "snippet": (item.get("content", "") or "")[:150],
                        "url": (item.get("url", "") or "")[:80],
                    }
                )

            # Track health
            if base_url == _searxng_url:
                _search_health = "healthy"
            else:
                _search_health = "degraded"
                logger.info(
                    "SearXNG fallback used (primary failed), got %d results",
                    len(results),
                )

            return results
        except Exception as e:
            logger.warning("SearXNG search failed (%s): %s", base_url, e)
            continue

    # Both URLs failed
    _search_health = "unhealthy"
    return []


# ── DuckDuckGo backend (legacy) ──────────────────────────────────────


async def _ddgs_search(
    query: str, max_results: int = 5
) -> list[dict[str, str]]:
    """Search via DuckDuckGo (ddgs library). Legacy fallback."""
    global _search_health

    from ddgs import DDGS

    def _search() -> list[dict[str, str]]:
        with DDGS() as ddgs:
            results: list[dict[str, str]] = []
            for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                if i >= max_results:
                    break
                results.append(
                    {
                        "title": (r.get("title", "") or "")[:80],
                        "snippet": (r.get("body", "") or "")[:150],
                        "url": (r.get("href", "") or "")[:80],
                    }
                )
            return results

    results = await asyncio.to_thread(_search)
    _search_health = "healthy"
    return results


# ── Main dispatch ─────────────────────────────────────────────────────


async def web_search(
    query: str, max_results: int = 5, retries: int = 3
) -> list[dict[str, str]]:
    """Execute a web search with concurrency control and retry.

    Dispatches to SearXNG or DuckDuckGo based on ``_search_engine``.

    Args:
        query: The search query.
        max_results: Max results to return (1-10).
        retries: Number of retry attempts.

    Returns:
        List of dicts with 'title', 'snippet', and 'url' keys.
        Returns an empty list on failure (fallback mode).
    """
    # Lazy-load search config from settings on first call
    if _search_engine == "searxng":
        _load_search_config()

    use_searxng = _search_engine == "searxng"

    async with _search_semaphore:
        with tracer.start_as_current_span(
            f"search.{_search_engine}",
            attributes={
                "search.engine": _search_engine,
                "search.query": query[:100],
            },
        ) as _:
            for attempt in range(retries):
                try:
                    if use_searxng:
                        results = await _searxng_search(query, max_results)
                    else:
                        results = await _ddgs_search(query, max_results)

                    logger.debug(
                        "Web search for '%s' returned %d results (engine=%s)",
                        query,
                        len(results),
                        _search_engine,
                    )
                    return results
                except Exception as e:
                    if attempt < retries - 1:
                        wait = 1.0 * (2**attempt)  # 1s, 2s, 4s
                        logger.warning(
                            "Web search failed for '%s': %s, retrying in %.1fs (attempt %d/%d)",
                            query,
                            e,
                            wait,
                            attempt + 1,
                            retries,
                        )
                        await asyncio.sleep(wait)
                    else:
                        logger.warning(
                            "Web search failed for query '%s': %s — returning empty results",
                            query,
                            str(e),
                        )

    return []


# ── Info helpers for server endpoints ─────────────────────────────────


def get_search_semaphore_info() -> dict[str, int]:
    """Return current search concurrency state for the /api/system/concurrency endpoint."""
    return {
        "active_searches": 3 - _search_semaphore._value,
        "max_searches": 3,
    }


def get_search_health_info() -> dict[str, Any]:
    """Return full search engine status for the /api/system/search endpoint."""
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
