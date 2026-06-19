"""Tavily Search API provider.

Uses the Tavily Search API (``https://api.tavily.com/search``).
Requires ``TAVILY_API_KEY`` environment variable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

_API_KEY: str | None = os.environ.get("TAVILY_API_KEY")
_API_URL = "https://api.tavily.com/search"
_TIMEOUT = 10

# ── Time filter mapping ────────────────────────────────────────────────────

_TIME_RANGE_MAP: dict[str, str] = {
    "day": "day",
    "week": "week",
    "month": "month",
    "year": "year",
}


def _map_time_filter(time_filter: str | None) -> str | None:
    """Map unified time_filter to Tavily ``time_range`` value."""
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
    """Search via the Tavily Search API.

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
        logger.warning("TAVILY_API_KEY not set — skipping Tavily Search")
        return []

    if cancel_event and cancel_event.is_set():
        return []

    payload: dict[str, Any] = {
        "api_key": _API_KEY,
        "query": query,
        "max_results": max_results,
        "include_answer": False,
        "include_raw_content": False,
    }

    time_range = _map_time_filter(time_filter)
    if time_range:
        payload["time_range"] = time_range

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Tavily search failed: %s", e)
        return []

    results: list[dict[str, Any]] = []
    for item in data.get("results", [])[:max_results]:
        results.append(
            {
                "title": (item.get("title", "") or "")[:80],
                "snippet": (item.get("content", "") or "")[:150],
                "url": (item.get("url", "") or "")[:80],
                "source": "tavily",
            }
        )

    return results
