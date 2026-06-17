"""Session configuration — immutable dataclasses.

Single source of truth for how a research session is configured.
All entry points (CLI, API) produce a ``SessionConfig``, and
``Orchestrator.run()`` accepts only this object.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import asyncio

from deepresearch.models import AgentProfile, ResearchTopic


@dataclass(frozen=True)
class TimeBudget:
    """Single source of truth for session time budget.

    Created from a keyword string ("quick", "medium", "deep", "custom")
    or from a raw number of minutes.
    """
    keyword: Literal["quick", "medium", "deep", "custom"]
    seconds: int
    max_rounds: int

    @classmethod
    def from_keyword(cls, kw: str) -> "TimeBudget":
        table = {
            "quick":  ("quick",  240, 2),
            "medium": ("medium", 420, 3),
            "deep":   ("deep",   660, 5),
            "custom": ("custom", 600, 4),
        }
        kw = kw.lower()
        if kw not in table:
            valid = ", ".join(table.keys())
            raise ValueError(f"Unknown time budget '{kw}'. Valid: {valid}")
        return cls(*table[kw])

    @classmethod
    def from_minutes(cls, minutes: int) -> "TimeBudget":
        return cls(keyword="custom", seconds=minutes * 60, max_rounds=4)


@dataclass(frozen=True)
class ModelAssignment:
    """Model assignment for all agents in a session.

    ``mode`` determines how models are assigned:
    - ``same``: all agents use the same ``selected_model``
    - ``random``: randomly (deterministic) assign from available models
    - ``manual``: use ``per_agent`` dict for explicit assignment
    """
    mode: Literal["same", "random", "manual"]
    selected_model: str | None = None
    per_agent: Mapping[str, str] = field(default_factory=dict)

    def resolve(self, agent_ids: Sequence[str]) -> dict[str, str]:
        """Return a deterministic dict[agent_id, model_id] for the given agent IDs."""
        if self.mode == "same":
            assert self.selected_model is not None
            return {aid: self.selected_model for aid in agent_ids}
        if self.mode == "manual":
            return dict(self.per_agent)
        # random — deterministic via stable seed (fixes #63's use of Python hash())
        seed = int(hashlib.sha256(
            f"{self.selected_model or ''}".encode()
        ).hexdigest()[:8], 16)
        rng = random.Random(seed)
        models = list(self.per_agent.values()) if self.per_agent else [self.selected_model]
        return {aid: rng.choice(models) for aid in agent_ids}


@dataclass(frozen=True)
class OutputConfig:
    """Output options for a research session."""
    output_dir: Path = Path("output")
    output_language: str = "en"
    generate_pdf: bool = True


@dataclass(frozen=True)
class SessionConfig:
    """The ONLY config object passed to ``Orchestrator.run()``.

    Construct via one of the factory classmethods:
    - ``from_cli(args)`` — from CLI arguments
    - ``from_api(payload)`` — from web API request body
    - ``from_dict(data)`` — from a generic dict

    Once created, the instance is immutable (frozen=True).
    """
    topic: ResearchTopic
    budget: TimeBudget
    models: ModelAssignment
    output: OutputConfig
    agents: tuple[AgentProfile, ...]
    cancel_event: asyncio.Event | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "SessionConfig":
        """Construct from a flat dict (e.g. API payload or CLI namespace)."""
        topic = ResearchTopic(
            question=data.get("topic", data.get("question", "")),
            time_budget="quick",  # unused — kept for model compat
            model_mode="same",    # unused — kept for model compat
        )
        budget = TimeBudget.from_keyword(data.get("time_budget", "medium"))
        if data.get("time_budget_seconds"):
            budget = TimeBudget(
                keyword="custom",
                seconds=int(data["time_budget_seconds"]),
                max_rounds=data.get("max_rounds", 4),
            )
        models = ModelAssignment(
            mode=data.get("model_mode", "same"),
            selected_model=data.get("selected_model"),
            per_agent=data.get("agent_models", {}),
        )
        output = OutputConfig(
            output_dir=Path(data.get("output_dir", "output")),
            output_language=data.get("output_language", "en"),
        )
        return cls(
            topic=topic,
            budget=budget,
            models=models,
            output=output,
            agents=tuple(data.get("agent_profiles", [])),
        )
