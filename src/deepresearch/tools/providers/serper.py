"""Serper.dev Search API provider.

Uses the Serper.dev Google Search API
(``https://google.serper.dev/search``).
Requires ``SERPER_API_KEY`` environment variable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

_API_KEY: str | None = os.environ.get("SERPER_API_KEY")
_API_URL = "https://google.serper.dev/search"
_TIMEOUT = 10

# ── Time filter mapping ────────────────────────────────────────────────────

_TBS_MAP: dict[str, str] = {
    "day": "qdr:d",
    "week": "qdr:w",
    "month": "qdr:m",
    "year": "qdr:y",
}


def _map_time_filter(time_filter: str | None) -> str | None:
    """Map unified time_filter to Serper ``tbs`` value."""
    if time_filter is None:
        return None
    return _TBS_MAP.get(time_filter)


# ── Public search function ─────────────────────────────────────────────────


async def search(
    query: str,
    max_results: int = 5,
    time_filter: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    """Search via the Serper.dev Google Search API.

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
        logger.warning("SERPER_API_KEY not set — skipping Serper Search")
        return []

    if cancel_event and cancel_event.is_set():
        return []

    headers = {
        "X-API-KEY": _API_KEY,
        "Content-Type": "application/json",
    }

    payload: dict[str, Any] = {
        "q": query,
        "num": max_results,
    }

    tbs = _map_time_filter(time_filter)
    if tbs:
        payload["tbs"] = tbs

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Serper search failed: %s", e)
        return []

    results: list[dict[str, Any]] = []
    for item in data.get("organic", [])[:max_results]:
        results.append(
            {
                "title": (item.get("title", "") or "")[:80],
                "snippet": (item.get("snippet", "") or "")[:150],
                "url": (item.get("link", "") or "")[:80],
                "source": "serper",
            }
        )

    return results
