"""Web search tool for DeepeResearch agents using DuckDuckGo."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── Tool definition for LiteLLM function calling ──────────────────────────

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


async def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Execute a DuckDuckGo web search and return structured results.

    Args:
        query: The search query.
        max_results: Max results to return (1-10).

    Returns:
        List of dicts with 'title', 'snippet', and 'url' keys.
    """
    try:
        import asyncio

        from ddgs import DDGS

        def _search() -> list[dict[str, str]]:
            with DDGS() as ddgs:
                results: list[dict[str, str]] = []
                for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                    if i >= max_results:
                        break
                    results.append({
                        "title": (r.get("title", "") or "")[:80],      # Truncate title
                        "snippet": (r.get("body", "") or "")[:150],    # Truncate snippet to 150 chars
                        "url": (r.get("href", "") or "")[:80],         # Truncate URL
                    })
                return results

        results = await asyncio.to_thread(_search)
        logger.debug("Web search for '%s' returned %d results", query, len(results))
        return results

    except Exception as e:
        logger.warning("Web search failed for '%s': %s", query, e)
        return [{"title": "Search Error", "snippet": f"Search failed: {e}", "url": ""}]
