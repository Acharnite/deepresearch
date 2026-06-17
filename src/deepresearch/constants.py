"""Shared constants for DeepeResearch.

Single source of truth for time budgets, round limits, and PDF size thresholds.
All other modules MUST import from here — never define these values locally.
"""

TIME_BUDGET_SECONDS: dict[str, int] = {
    "quick": 240,
    "medium": 420,
    "deep": 660,
}

MAX_ROUNDS_BY_BUDGET: dict[str, int] = {
    "quick": 2,
    "medium": 3,
    "deep": 5,
    "custom": 4,
}

PDF_MIN_HEALTHY_BYTES: int = 20_000
