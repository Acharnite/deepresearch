"""Session-level token usage tracking."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelUsage:
    """Per-model accumulated token and cost data."""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0


class TokenTracker:
    """Shared session-level token usage aggregator.

    Pass an instance to all LLMClient instances in a session so they
    report usage to the same tracker.
    """

    def __init__(self) -> None:
        self._models: dict[str, ModelUsage] = {}

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost: float,
    ) -> None:
        """Record token usage for a model."""
        if model not in self._models:
            self._models[model] = ModelUsage(model=model)
        usage = self._models[model]
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        usage.cost += cost

    @property
    def total_cost(self) -> float:
        """Total accumulated cost across all models."""
        return sum(u.cost for u in self._models.values())

    @property
    def total_tokens(self) -> int:
        """Total accumulated tokens (prompt + completion) across all models."""
        return sum(
            u.prompt_tokens + u.completion_tokens for u in self._models.values()
        )

    def per_model(self) -> dict[str, dict]:
        """Per-model breakdown of tokens and cost."""
        return {
            name: {
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.prompt_tokens + u.completion_tokens,
                "cost": round(u.cost, 6),
            }
            for name, u in sorted(self._models.items())
        }

    def to_dict(self) -> dict:
        """Full summary as a serialisable dict."""
        return {
            "total_cost": round(self.total_cost, 6),
            "total_tokens": self.total_tokens,
            "per_model": self.per_model(),
        }
