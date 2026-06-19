"""Auto-detect time filter from search query keywords.

Maps query keywords (e.g. "today", "this week") to unified time filter
values (``"day"``, ``"week"``, ``"month"``, ``"year"``) for use by the
multi-provider search chain.
"""

from __future__ import annotations

import re

# ── Keyword-to-filter mapping ──────────────────────────────────────────────
# Each entry: (compiled_pattern, resulting_filter)
# Patterns are pre-compiled at module load for performance.

_TIME_FILTER_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(kw) + r"\b"), filter_val)
    for keywords, filter_val in [
        (["today", "latest", "just released", "breaking"], "day"),
        (["this week", "past week", "this week's"], "week"),
        (["this month", "past month", "recent"], "month"),
        (["this year", "past year", "2026"], "year"),
    ]
    for kw in keywords
]


def detect_time_filter(query: str) -> str | None:
    """Detect a time filter based on keywords in the query text.

    Checks for known time-sensitive keywords using word-boundary matching.
    The first matching rule wins.  Returns ``None`` if no keywords match.

    Args:
        query: The search query text.

    Returns:
        ``"day"``, ``"week"``, ``"month"``, ``"year"``, or ``None``.

    Examples:
        >>> detect_time_filter("latest AI news")
        'day'
        >>> detect_time_filter("events this week")
        'week'
        >>> detect_time_filter("quantum computing")
        None
    """
    query_lower = query.strip().lower()

    for pattern, filter_value in _TIME_FILTER_RULES:
        if pattern.search(query_lower):
            return filter_value

    return None
