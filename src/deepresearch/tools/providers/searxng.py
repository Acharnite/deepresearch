"""SearXNG search provider.

Extracted from ``web_search.py`` as part of the multi-provider migration
(ADR-0017).  SearXNG remains the default primary provider.

Configuration (from env / settings manager):
    - ``SEARXNG_URL`` — Primary SearXNG instance URL (default: ``http://localhost:8888``)
    - ``SEARXNG_FALLBACK_URL`` — Fallback SearXNG instance (default: ``https://searx.be``)
    - ``SEARXNG_ENGINES`` — Comma-separated engine list (default: ``google,bing,startpage``)
    - ``SEARXNG_CATEGORIES`` — Comma-separated category list (default: ``general``)
    - ``SEARXNG_TIMEOUT`` — HTTP timeout in seconds (default: 10)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

_SEARXNG_URL: str = os.environ.get("SEARXNG_URL", "http://localhost:8888")
_SEARXNG_FALLBACK_URL: str = os.environ.get("SEARXNG_FALLBACK_URL", "https://searx.be")
_SEARXNG_ENGINES: list[str] = ["google", "bing", "startpage"]
_SEARXNG_CATEGORIES: list[str] = ["general"]
_SEARXNG_TIMEOUT: int = 10

# ── Health tracking ────────────────────────────────────────────────────────

_search_health: str = "unknown"
_last_search_latency_ms: float = 0.0


def _load_config() -> None:
    """Load SearXNG config from the settings manager (if available)."""
    global _SEARXNG_URL, _SEARXNG_FALLBACK_URL, _SEARXNG_ENGINES
    global _SEARXNG_CATEGORIES, _SEARXNG_TIMEOUT

    try:
        from deepresearch.web.settings_manager import settings_manager

        config = settings_manager.get_search_config()
        if config.get("searxng_url"):
            _SEARXNG_URL = config["searxng_url"]
        if config.get("searxng_fallback_url"):
            _SEARXNG_FALLBACK_URL = config["searxng_fallback_url"]
        if config.get("searxng_engines"):
            _SEARXNG_ENGINES = config["searxng_engines"]
        if config.get("searxng_categories"):
            _SEARXNG_CATEGORIES = config["searxng_categories"]
        if config.get("searxng_timeout"):
            _SEARXNG_TIMEOUT = config["searxng_timeout"]
        logger.debug(
            "Loaded SearXNG config: url=%s, fallback=%s",
            _SEARXNG_URL,
            _SEARXNG_FALLBACK_URL,
        )
    except Exception:
        logger.debug("Could not load SearXNG config (using defaults)", exc_info=True)


# ── Time filter mapping ────────────────────────────────────────────────────

_TIME_RANGE_MAP: dict[str, str] = {
    "day": "day",
    "week": "week",
    "month": "month",
    "year": "year",
}


def _map_time_filter(time_filter: str | None) -> str | None:
    """Map unified time_filter to SearXNG ``time_range`` value."""
    if time_filter is None:
        return None
    return _TIME_RANGE_MAP.get(time_filter)


# ── Public search function ─────────────────────────────────────────────────


async def search(
    query: str,
    max_results: int = 5,
    time_filter: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    """Search via SearXNG JSON API with primary/fallback fallback.

    Args:
        query: Search query.
        max_results: Max results to return.
        time_filter: Unified time filter (day/week/month/year).
        cancel_event: Optional cancellation event.

    Returns:
        List of result dicts with ``title``, ``snippet``, ``url``, ``source``.
    """
    global _search_health, _last_search_latency_ms

    # Lazy-load config from settings on first call
    _load_config()

    params: dict[str, str] = {
        "q": query,
        "format": "json",
        "categories": ",".join(_SEARXNG_CATEGORIES),
        "engines": ",".join(_SEARXNG_ENGINES),
    }

    time_range = _map_time_filter(time_filter)
    if time_range:
        params["time_range"] = time_range

    for base_url in [_SEARXNG_URL, _SEARXNG_FALLBACK_URL]:
        if cancel_event and cancel_event.is_set():
            return []

        try:
            t0 = time.monotonic()
            async with httpx.AsyncClient(timeout=_SEARXNG_TIMEOUT) as client:
                resp = await client.get(f"{base_url}/search", params=params)
                resp.raise_for_status()
                data = resp.json()

            latency = (time.monotonic() - t0) * 1000
            _last_search_latency_ms = latency

            results: list[dict[str, Any]] = []
            for item in data.get("results", [])[:max_results]:
                results.append(
                    {
                        "title": (item.get("title", "") or "")[:80],
                        "snippet": (item.get("content", "") or "")[:150],
                        "url": (item.get("url", "") or "")[:80],
                        "source": "searxng",
                    }
                )

            if base_url == _SEARXNG_URL:
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

    _search_health = "unhealthy"
    return []


# ── Info helpers ───────────────────────────────────────────────────────────


def get_search_health_info() -> dict[str, Any]:
    """Return full SearXNG health status."""
    return {
        "searxng_url": _SEARXNG_URL,
        "searxng_fallback_url": _SEARXNG_FALLBACK_URL,
        "searxng_engines": _SEARXNG_ENGINES,
        "searxng_categories": _SEARXNG_CATEGORIES,
        "searxng_timeout": _SEARXNG_TIMEOUT,
        "status": _search_health,
        "last_search_latency_ms": _last_search_latency_ms,
    }
