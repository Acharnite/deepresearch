"""Configuration and model assignment for the Orchestrator.

Contains the implementation of ``configure()`` and ``assign_models()``
that handle session configuration validation and LLM model assignment.
"""

from __future__ import annotations

import hashlib
import random
from typing import Any

from rich.console import Console

from deepresearch.config import ConfigError, load_agent_profiles, load_model_config
from deepresearch.constants import MAX_ROUNDS_BY_BUDGET, TIME_BUDGET_SECONDS
from deepresearch.models import AgentProfile, ResearchTopic, SessionConfig

console = Console()


async def configure(
    orchestrator: Any,
    topic_str: str,
    **overrides: Any,
) -> SessionConfig:
    """Create a validated SessionConfig for a research session.

    Overrides (passed from CLI flags or tests):
        time_budget (str): ``"quick"``, ``"medium"``, ``"deep"``, or ``"custom"``.
        time_budget_seconds (int): Custom time budget in seconds (overrides
            ``time_budget`` keyword when provided).
        model_mode (str): ``"same"``, ``"random"``, or ``"manual"``.
        selected_model (str | None): Model ID to use for all agents
            when ``model_mode="same"``.
        agent_models (dict[str, str] | None): Per-agent model mapping
            when ``model_mode="manual"``.

    When an override is absent the method falls back to interactive
    prompts so it can also be used as a pure CLI flow.
    """
    orchestrator.state = "CONFIGURING"
    if orchestrator._event_bus:
        await orchestrator._event_bus.publish(
            {"event_type": "config_validated", "topic": topic_str},
            state=orchestrator.state,
        )

    # --- load configs (from override or from file) ---
    try:
        if orchestrator._profiles_override is not None:
            profiles = orchestrator._profiles_override
        else:
            profiles = load_agent_profiles(orchestrator.profiles_path)

        if orchestrator._model_configs_override is not None:
            orchestrator.model_configs = orchestrator._model_configs_override
        else:
            orchestrator.model_configs = load_model_config(orchestrator.models_path)
    except ConfigError as e:
        console.print(f"[red]Configuration error:[/red] {e}")
        raise

    if not profiles:
        raise ConfigError(
            "No agent profiles loaded — at least one profile is required."
        )
    if not orchestrator.model_configs:
        raise ConfigError("No model configurations loaded — cannot assign models.")

    # --- time budget ---
    time_budget: str = overrides.get("time_budget")  # type: ignore[assignment]
    if time_budget is None:
        time_budget = orchestrator._prompt_time_budget()

    # --- custom time budget seconds ---
    time_budget_seconds: int | None = overrides.get("time_budget_seconds")
    if time_budget_seconds is not None:
        # If custom seconds are provided, use "custom" as budget keyword.
        time_budget = orchestrator._CUSTOM_BUDGET_KEY

    # --- model mode ---
    model_mode: str = overrides.get("model_mode")  # type: ignore[assignment]
    if model_mode is None:
        model_mode = orchestrator._prompt_model_mode()

    topic = ResearchTopic(
        question=topic_str,
        time_budget=time_budget,
        model_mode=model_mode,
    )

    selected_model: str | None = overrides.get("selected_model")
    agent_models: dict[str, str] | None = overrides.get("agent_models")
    agent_models = await assign_models(
        orchestrator,
        model_mode,
        profiles,
        selected_model=selected_model,
        agent_models=agent_models,
    )

    if time_budget_seconds is not None:
        budget_seconds = time_budget_seconds
    else:
        budget_seconds = TIME_BUDGET_SECONDS.get(time_budget, 300)

    # Derive max_rounds from budget keyword.
    max_rounds = overrides.get("max_rounds") or MAX_ROUNDS_BY_BUDGET.get(time_budget, 4)

    output_language: str = overrides.get("output_language", "English")

    config = SessionConfig(
        topic=topic,
        agent_profiles=profiles,
        agent_models=agent_models,
        time_budget_seconds=budget_seconds,
        max_rounds=max_rounds,
        output_language=output_language,
    )
    orchestrator.session_config = config
    if orchestrator._event_bus:
        await orchestrator._event_bus.publish(
            {"event_type": "models_assigned", "assignments": agent_models},
            state=orchestrator.state,
        )
    return config


async def assign_models(
    orchestrator: Any,
    mode: str,
    profiles: list[AgentProfile],
    selected_model: str | None = None,
    agent_models: dict[str, str] | None = None,
) -> dict[str, str]:
    """Assign LLM models to agent profiles.

    Three modes:
        ``"same"``   — Every agent gets the default model.
        ``"random"`` — Models are randomly assigned (deterministic per
                       topic string via ``hash``).
        ``"manual"`` — Interactive selection per agent.

    Args:
        orchestrator: The Orchestrator instance (for access to model configs
            and prompt helpers).
        mode: Model assignment mode (same/random/manual).
        profiles: List of agent profiles to assign models to.
        selected_model: Optional override for "same" mode — use this
            specific model for all agents instead of the default.
        agent_models: Optional override for "manual" mode — use this
            per-agent mapping instead of interactive CLI prompts.

    Returns:
        ``dict[str, str]`` mapping ``agent_id → model_name``.
    """
    available = orchestrator.model_configs
    if not available:
        raise ConfigError("No model configurations loaded — cannot assign models.")

    if mode == "same":
        if selected_model:
            return {p.id: selected_model for p in profiles}
        default = next((m for m in available if m.get("default")), available[0])
        return {p.id: default["id"] for p in profiles}

    if mode == "random":
        seed_str = orchestrator._topic_seed
        random.seed(int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16))
        selected = random.choices(available, k=len(profiles))
        return {p.id: m["id"] for p, m in zip(profiles, selected)}

    if mode == "manual":
        if agent_models:
            return agent_models
        configs: dict[str, str] = {}
        for profile in profiles:
            model = orchestrator._prompt_for_model(profile, available)
            configs[profile.id] = model["id"]
        return configs

    raise ConfigError(f"Unknown model assignment mode: {mode}")
