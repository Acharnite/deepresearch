"""Brave Search API provider.

Uses the Brave Search API (``https://api.search.brave.com/res/v1/web/search``).
Requires ``BRAVE_API_KEY`` environment variable.

ponytail: boilerplate pattern shared across all providers (searxng, duckduckgo,
google_pse, tavily, serper) — intentional per ADR-0017 §1 (unified provider
interface).  Ceiling: code duplication across providers.
Upgrade path: extract into a ProviderBase class when adding a 7th provider.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

_API_KEY: str | None = os.environ.get("BRAVE_API_KEY")
_API_URL = "https://api.search.brave.com/res/v1/web/search"
_TIMEOUT = 10

# ── Time filter mapping ────────────────────────────────────────────────────

_FRESHNESS_MAP: dict[str, str] = {
    "day": "pd",
    "week": "pw",
    "month": "pm",
    "year": "py",
}


def _map_time_filter(time_filter: str | None) -> str | None:
    """Map unified time_filter to Brave ``freshness`` value."""
    if time_filter is None:
        return None
    return _FRESHNESS_MAP.get(time_filter)


# ── Public search function ─────────────────────────────────────────────────


async def search(
    query: str,
    max_results: int = 5,
    time_filter: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    """Search via the Brave Search API.

    Args:
        query: Search query.
        max_results: Max results to return.
        time_filter: Unified time filter (day/week/month/year).
        cancel_event: Optional cancellation event.

    Returns:
        List of result dicts with ``title``, ``snippet``, ``url``, ``source``.
        Empty list if no API key is configured.
    """
    if _API_KEY is None:
        logger.warning("BRAVE_API_KEY not set — skipping Brave Search")
        return []

    if cancel_event and cancel_event.is_set():
        return []

    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": _API_KEY,
    }

    params: dict[str, str] = {
        "q": query,
        "count": str(max_results),
    }

    freshness = _map_time_filter(time_filter)
    if freshness:
        params["freshness"] = freshness

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_API_URL, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Brave Search failed: %s", e)
        return []

    results: list[dict[str, Any]] = []
    web_results = data.get("web", {}).get("results", [])[:max_results]
    for item in web_results:
        results.append(
            {
                "title": (item.get("title", "") or "")[:80],
                "snippet": (item.get("description", "") or "")[:150],
                "url": (item.get("url", "") or "")[:80],
                "source": "brave",
            }
        )

    return results
