"""Dry-run preview for the Orchestrator.

Contains the implementation of ``dry_run()`` and related display helpers
that preview a session without executing any agents.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from deepresearch.config import ConfigError
from deepresearch.llm.client import _lookup_cost
from deepresearch.models import SessionConfig

console = Console()


def dry_run(
    orchestrator: Any,
    topic_str: str,
    time_budget: str,
    model_mode: str,
    config: SessionConfig | None = None,
) -> dict[str, Any]:
    """Preview a session without executing any agents.

    Args:
        orchestrator: The Orchestrator instance (for access to class
            attributes like ``TIME_BUDGET_OPTIONS``).
        topic_str: The research topic string.
        time_budget: Time budget keyword (``"quick"``, ``"medium"``, ``"deep"``).
        model_mode: Model assignment mode (``"same"``, ``"random"``, ``"manual"``).
        config: Optional pre-built SessionConfig.  If ``None``, one is
                built from the current configuration.

    Returns:
        Dict with preview information:
        - ``topic``, ``time_budget``, ``model_mode``
        - ``agent_assignments``: list of ``{agent_id, agent_name, emoji, model, temperature}``
        - ``estimated_cost``: float (USD)
        - ``estimated_tokens``: int
        - ``rounds``: int (1 for quick, 2 otherwise)
        - ``agents_count``: int
    """
    cfg = config or orchestrator.session_config
    if cfg is None:
        raise ConfigError(
            "No session config available for dry-run. "
            "Call configure() first or pass a config."
        )

    time_budget_label = orchestrator.TIME_BUDGET_OPTIONS.get(time_budget, time_budget)
    rounds = cfg.max_rounds

    agent_assignments: list[dict[str, Any]] = []
    for profile in cfg.agent_profiles:
        model = cfg.agent_models.get(profile.id, "unknown")
        agent_assignments.append(
            {
                "agent_id": profile.id,
                "agent_name": profile.name,
                "emoji": profile.emoji,
                "model": model,
                "temperature": profile.temperature,
            }
        )

    # Rough token estimation per agent per round.
    avg_prompt_tokens = 1500  # system + user prompt (estimate)
    avg_output_tokens = 2000  # agent response (estimate)
    total_agents = len(cfg.agent_profiles)
    total_rounds = rounds
    estimated_tokens = (
        total_agents * total_rounds * (avg_prompt_tokens + avg_output_tokens)
    )

    # Rough cost estimation using the most expensive assigned model.
    max_input_rate = max(
        _lookup_cost(m, 1000, 0) * 1000  # USD per 1K input tokens
        for m in cfg.agent_models.values()
    )
    max_output_rate = max(
        _lookup_cost(m, 0, 1000) * 1000 for m in cfg.agent_models.values()
    )
    input_cost = (estimated_tokens / 2 / 1000) * max_input_rate
    output_cost = (estimated_tokens / 2 / 1000) * max_output_rate
    estimated_cost = round(input_cost + output_cost, 4)

    # Show the Rich table.
    _show_dry_run_table(
        topic_str=topic_str,
        time_budget_label=time_budget_label,
        time_budget_seconds=cfg.time_budget_seconds,
        model_mode=model_mode,
        rounds=rounds,
        agent_assignments=agent_assignments,
        estimated_cost=estimated_cost,
        estimated_tokens=estimated_tokens,
    )

    return {
        "topic": topic_str,
        "time_budget": time_budget,
        "model_mode": model_mode,
        "agent_assignments": agent_assignments,
        "estimated_cost": estimated_cost,
        "estimated_tokens": estimated_tokens,
        "rounds": rounds,
        "max_rounds": rounds,
        "agents_count": total_agents,
    }


def _show_dry_run_table(
    topic_str: str,
    time_budget_label: str,
    time_budget_seconds: int,
    model_mode: str,
    rounds: int,
    agent_assignments: list[dict[str, Any]],
    estimated_cost: float,
    estimated_tokens: int,
) -> None:
    """Display dry-run preview as a Rich Table."""
    # Assignment table.
    table = Table(
        title="DeepResearch — Dry Run",
        title_style="bold cyan",
        border_style="blue",
    )
    table.add_column("Agent", style="green")
    table.add_column("Model", style="yellow")
    table.add_column("Temperature", justify="center")

    for a in agent_assignments:
        table.add_row(
            f"{a['emoji']} {a['agent_name']}",
            a["model"],
            str(a["temperature"]),
        )

    # Summary panel.
    round_timeline = "→".join([f"R{i}" for i in range(1, rounds + 1)])
    summary_lines = [
        f"[bold]Topic:[/bold] {topic_str}",
        f"[bold]Budget:[/bold] {time_budget_label} ({time_budget_seconds}s)",
        f"[bold]Model Mode:[/bold] {model_mode}",
        f"[bold]Max Rounds:[/bold] {rounds}",
        f"[bold]Projected:[/bold] Will run {rounds} rounds: {round_timeline}",
        f"[bold]Agents:[/bold] {len(agent_assignments)}",
        "",
        f"[bold]Est. Cost:[/bold] ${estimated_cost:.4f}",
        f"[bold]Est. Tokens:[/bold] {estimated_tokens:,}",
    ]
    summary = Panel(
        "\n".join(summary_lines),
        border_style="green",
    )

    console.print()
    console.print(summary)
    console.print(table)
    console.print("[bold green]✓ Configuration valid![/bold green]")


def show_dry_run(orchestrator: Any, config: SessionConfig) -> None:
    """Display configuration preview without executing any agents.

    Legacy method — delegates to ``dry_run()``.
    """
    dry_run(
        orchestrator,
        topic_str=config.topic.question,
        time_budget=config.topic.time_budget,
        model_mode=config.topic.model_mode,
        config=config,
    )
