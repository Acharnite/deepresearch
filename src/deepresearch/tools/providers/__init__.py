"""Search provider modules for DeepeResearch agents.

Each module exports an async ``search()`` function with the same signature::

    async def search(query: str, max_results: int = 5,
                     time_filter: str | None = None,
                     cancel_event: asyncio.Event | None = None
                     ) -> list[dict[str, str]]:
        ...

Results are returned as a list of dicts with keys:
    - ``title``   (str, truncated to 80 chars)
    - ``snippet`` (str, truncated to 150 chars)
    - ``url``     (str, truncated to 80 chars)
    - ``source``  (str, provider name, e.g. ``"searxng"``)
"""

from __future__ import annotations

from deepresearch.tools.providers import brave
from deepresearch.tools.providers import duckduckgo
from deepresearch.tools.providers import google_pse
from deepresearch.tools.providers import searxng
from deepresearch.tools.providers import serper
from deepresearch.tools.providers import tavily

__all__ = [
    "searxng",
    "duckduckgo",
    "brave",
    "google_pse",
    "tavily",
    "serper",
]
