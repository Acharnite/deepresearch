"""Background session manager for web-triggered research sessions.

The :class:`SessionManager` wraps the Orchestrator lifecycle so web
endpoints can start, monitor, and cancel research sessions without
blocking the HTTP request thread.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from deepresearch.web.state import update_status

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages a single background research session launched from the web UI.

    The manager ensures only one session runs at a time and exposes the
    session's status and result for polling via REST endpoints.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[Any] | None = None
        self._result: dict[str, Any] | None = None
        self._status: str = "idle"  # idle | running | complete | error

    # ── Public properties ──────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._status == "running"

    @property
    def result(self) -> dict[str, Any] | None:
        return self._result

    @property
    def status(self) -> str:
        return self._status

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start_session(
        self,
        topic: str,
        time_budget: str = "medium",
        model_mode: str = "same",
        output_dir: str = "./output",
    ) -> dict[str, Any]:
        """Start a research session in the background.

        Returns immediately with session metadata.  The actual research
        runs in an ``asyncio.Task`` so the HTTP endpoint is not blocked.

        Raises:
            RuntimeError: If a session is already running.
        """
        if self._status == "running":
            raise RuntimeError("A session is already running")

        self._status = "running"
        self._result = None

        # Reset server status so the dashboard reflects the new session.
        update_status(
            state="CONFIGURING",
            topic=topic,
            session_active=True,
            phase_label="Starting...",
        )

        self._task = asyncio.create_task(
            self._run_session(topic, time_budget, model_mode, output_dir)
        )

        return {
            "status": "started",
            "topic": topic,
            "time_budget": time_budget,
            "model_mode": model_mode,
        }

    async def _run_session(
        self,
        topic: str,
        time_budget: str,
        model_mode: str,
        output_dir: str,
    ) -> None:
        """Run the full orchestration lifecycle and store the result.

        Imports are deferred to avoid circular imports at module level
        (``deepresearch.web → server → session_manager → orchestrator → event_bus → web``).
        """
        # Lazy imports to break circular dependency chain.
        from deepresearch.agents.registry import AgentRegistry
        from deepresearch.llm.client import LLMClient
        from deepresearch.orchestrator import Orchestrator

        try:
            output_path = Path(output_dir) / "deepresearch_output.pdf"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Wire up real components.
            llm = LLMClient()
            registry = AgentRegistry(llm)

            orchestrator = Orchestrator(
                agent_factory=registry.agent_factory,
                scribe_factory=lambda: registry.create_scribe_agent(),
            )

            pdf_path = await orchestrator.run(
                topic,
                time_budget=time_budget,
                model_mode=model_mode,
                output_path=str(output_path),
            )

            # Determine the HTML path (same stem, .html extension).
            html_path: str | None = None
            pdf_file = Path(pdf_path)
            if pdf_file.suffix == ".pdf":
                html_candidate = pdf_file.with_suffix(".html")
                if html_candidate.exists():
                    html_path = str(html_candidate)

            self._result = {
                "status": "complete",
                "pdf_path": str(pdf_path),
                "pdf_filename": pdf_file.name,
                "html_path": html_path,
                "state_history": [],
            }
            self._status = "complete"

            update_status(
                state="COMPLETE",
                session_active=False,
                phase_label="Complete",
            )

        except asyncio.CancelledError:
            self._result = {"status": "cancelled"}
            self._status = "idle"
            update_status(
                state="IDLE",
                session_active=False,
                phase_label="Cancelled",
            )

        except Exception as exc:
            logger.exception("Session failed")
            self._result = {"status": "error", "error": str(exc)}
            self._status = "error"
            update_status(
                state="ERROR",
                session_active=False,
                phase_label=f"Error: {exc}",
            )

    async def cancel_session(self) -> dict[str, str]:
        """Cancel the running session if there is one."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._status = "idle"
            update_status(
                state="IDLE",
                session_active=False,
                phase_label="Cancelled",
            )
            return {"status": "cancelled"}
        return {"status": "no_active_session"}


# Module-level singleton used by the web server endpoints.
session_manager = SessionManager()
