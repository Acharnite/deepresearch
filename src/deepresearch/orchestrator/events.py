"""Event logging for the Orchestrator.

Contains the implementation of ``_log_event`` that records session events
for observability, testing, and real-time dashboard updates via SSE.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from deepresearch.web.event_bus import event_bus

logger = logging.getLogger(__name__)


def log_event(
    orchestrator: Any,
    event_type: str,
    **details: Any,
) -> None:
    """Record a session event for observability / testing.

    Also publishes to the web ``EventBus`` so SSE subscribers receive
    real-time updates.  This is a fire-and-forget operation — failures
    are silently ignored to avoid disrupting the session.
    """
    elapsed = 0.0
    if orchestrator._session_start_time is not None:
        elapsed = (datetime.now() - orchestrator._session_start_time).total_seconds()
    event = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "state": orchestrator.state,
        "elapsed_seconds": round(elapsed, 1),
        **details,
    }
    orchestrator.events.append(event)
    logger.debug("Session event: %s %s", event_type, details)
    # Fire-and-forget publish to web event bus (per-session if available).
    bus = orchestrator._event_bus or event_bus
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(bus.publish(event))
    except RuntimeError:
        pass  # No running event loop — skip web event bus.
