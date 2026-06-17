"""Timeout calculation for research rounds."""

from __future__ import annotations

from deepresearch.models import SessionConfig


class TimeoutCalculator:
    """Calculates per-agent timeouts for research rounds.

    The scribe reserves 25% of the budget (min 60s); agents split the
    remaining 75% across all rounds.
    """

    def __init__(self, session_config: SessionConfig | None = None) -> None:
        self._config = session_config

    def get_round_timeout(self) -> int:
        """Per-agent timeout based on session budget, rounds, and scribe reservation.

        Scribe gets 25% of budget (min 60s). Agents split the remaining 75%.
        """
        if self._config is None:
            return 120
        # Support both new (budget dataclass) and old (Pydantic) SessionConfig.
        if hasattr(self._config, 'budget'):
            b = self._config.budget.seconds
            m = self._config.budget.max_rounds
        else:
            b = self._config.time_budget_seconds
            m = self._config.max_rounds
        scribe_budget = max(60, int(b * 0.25))
        agent_budget = b - scribe_budget
        per_round = max(90, int(agent_budget / m))
        return per_round
