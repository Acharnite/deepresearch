"""Shared web server state for the DeepResearch dashboard.

Holds the global status variables and the ``update_status`` helper
that both ``server.py`` and ``session_manager.py`` need to import.
Extracted into its own module to break the circular import between
those two modules.
"""

from __future__ import annotations

from typing import Any

# ── Static state cache (updated by orchestrator / session_manager) ─────

_current_state: str = "IDLE"
_current_topic: str = ""
_current_agents: list[dict[str, Any]] = []
_agent_progress: dict[str, float] = {}
_elapsed_start: float | None = None
_session_active: bool = False
_phase_label: str = "Idle"


def update_status(
    *,
    state: str = "",
    topic: str = "",
    agents: list[dict[str, Any]] | None = None,
    agent_progress: dict[str, float] | None = None,
    elapsed_start: float | None = None,
    session_active: bool | None = None,
    phase_label: str = "",
) -> None:
    """Update the shared status cache used by the ``/api/status`` endpoint.

    Called by the orchestrator hook so polling clients see fresh state.
    """
    global _current_state, _current_topic, _current_agents
    global _agent_progress, _elapsed_start, _session_active, _phase_label

    if state:
        _current_state = state
    if topic:
        _current_topic = topic
    if agents is not None:
        _current_agents = agents
    if agent_progress is not None:
        _agent_progress = agent_progress
    if elapsed_start is not None:
        _elapsed_start = elapsed_start
    if session_active is not None:
        _session_active = session_active
    if phase_label:
        _phase_label = phase_label
