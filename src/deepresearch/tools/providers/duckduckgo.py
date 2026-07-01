"""DuckDuckGo search provider.

Extracted from ``web_search.py`` as part of the multi-provider migration
(ADR-0017).  DuckDuckGo is a legacy fallback — relies on the third-party
``ddgs`` library.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Time filter mapping ────────────────────────────────────────────────────

_TIME_RANGE_MAP: dict[str, str] = {
    "day": "d",
    "week": "w",
    "month": "m",
    "year": "y",
}


def _map_time_filter(time_filter: str | None) -> str | None:
    """Map unified time_filter to DDGS ``time_range`` value."""
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
    """Search via DuckDuckGo using the ``ddgs`` library.

    Args:
        query: Search query.
        max_results: Max results to return.
        time_filter: Unified time filter (day/week/month/year).
        cancel_event: Optional cancellation event.

    Returns:
        List of result dicts with ``title``, ``snippet``, ``url``, ``source``.
    """
    if cancel_event and cancel_event.is_set():
        return []

    from ddgs import DDGS

    time_range = _map_time_filter(time_filter)

    def _search() -> list[dict[str, Any]]:
        with DDGS() as ddgs:
            results: list[dict[str, Any]] = []
            kwargs: dict[str, Any] = {"query": query, "max_results": max_results}
            if time_range:
                kwargs["time_range"] = time_range
            for i, r in enumerate(ddgs.text(**kwargs)):
                if i >= max_results:
                    break
                results.append(
                    {
                        "title": (r.get("title", "") or "")[:80],
                        "snippet": (r.get("body", "") or "")[:150],
                        "url": (r.get("href", "") or "")[:80],
                        "source": "duckduckgo",
                    }
                )
            return results

    results = await asyncio.to_thread(_search)
    return results
