"""Google Programmable Search Engine (PSE) provider.

Uses the Google Custom Search JSON API
(``https://www.googleapis.com/customsearch/v1``).
Requires ``GOOGLE_PSE_API_KEY`` and ``GOOGLE_PSE_CX`` environment variables.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────

_API_KEY: str | None = os.environ.get("GOOGLE_PSE_API_KEY")
_CX: str | None = os.environ.get("GOOGLE_PSE_CX")
_API_URL = "https://www.googleapis.com/customsearch/v1"
_TIMEOUT = 10


# ── Public search function ─────────────────────────────────────────────────


async def search(
    query: str,
    max_results: int = 5,
    time_filter: str | None = None,
    cancel_event: asyncio.Event | None = None,
) -> list[dict[str, Any]]:
    """Search via Google Programmable Search Engine.

    Args:
        query: Search query.
        max_results: Max results to return.
        time_filter: Unified time filter (day/week/month/year).
        cancel_event: Optional cancellation event.

    Returns:
        List of result dicts with ``title``, ``snippet``, ``url``, ``source``.
        Empty list if API key or CX is not configured.
    """
    if _API_KEY is None or _CX is None:
        missing = []
        if _API_KEY is None:
            missing.append("GOOGLE_PSE_API_KEY")
        if _CX is None:
            missing.append("GOOGLE_PSE_CX")
        logger.warning(
            "Google PSE not configured — missing %s; skipping", ", ".join(missing)
        )
        return []

    if cancel_event and cancel_event.is_set():
        return []

    params: dict[str, str] = {
        "key": _API_KEY,
        "cx": _CX,
        "q": query,
        "num": str(min(max_results, 10)),
    }

    # Google PSE supports sort=date for recency-based sorting
    if time_filter in ("day", "week", "month", "year"):
        params["sort"] = "date"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning("Google PSE search failed: %s", e)
        return []

    results: list[dict[str, Any]] = []
    for item in data.get("items", [])[:max_results]:
        results.append(
            {
                "title": (item.get("title", "") or "")[:80],
                "snippet": (item.get("snippet", "") or "")[:150],
                "url": (item.get("link", "") or "")[:80],
                "source": "google_pse",
            }
        )

    return results
