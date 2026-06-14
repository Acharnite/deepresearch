"""Multi-session manager for concurrent research sessions.

The :class:`MultiSessionManager` replaces the singleton
:class:`SessionManager` and allows multiple research sessions
to run concurrently as independent asyncio tasks.

Each session has its own :class:`EventBus` for SSE streaming
and is identified by a short UUID.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import json as _json
from deepresearch.web.event_bus import EventBus

logger = logging.getLogger(__name__)
SESSION_DB_PATH = Path(__file__).resolve().parent.parent.parent / "output" / "sessions_db.json"


def _load_session_db() -> dict[str, dict]:
    """Load persisted session metadata from disk."""
    try:
        if SESSION_DB_PATH.exists():
            return _json.loads(SESSION_DB_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load session DB: %s", e)
    return {}


def _save_session_db(db: dict[str, dict]) -> None:
    """Persist session metadata to disk."""
    try:
        SESSION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_DB_PATH.write_text(_json.dumps(db, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning("Failed to save session DB: %s", e)




def _slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a safe filename slug."""
    slug = re.sub(r'[^a-zA-Z0-9_-]', '_', text.lower().strip())
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug[:max_len]


@dataclass
class SessionInfo:
    """Represents a single research session."""

    session_id: str
    topic: str
    time_budget: str
    time_budget_seconds: int
    model_mode: str
    status: str  # queued | running | complete | error | cancelled
    created_at: str
    completed_at: str | None = None
    result: dict | None = None
    error: str | None = None
    output_path: str | None = None
    event_bus: EventBus | None = None
    selected_model: str | None = None
    agent_models: dict[str, str] | None = None
    event_history: list[dict[str, Any]] = field(default_factory=list)


class MultiSessionManager:
    """Manages multiple concurrent research sessions.

    - Sessions run as independent asyncio tasks
    - Each session has its own EventBus for SSE streaming
    - Sessions are identified by short UUID (8 chars)
    - Old completed sessions are cleaned up when max is reached
    """

    def __init__(self, max_sessions: int = 20) -> None:
        self._sessions: dict[str, SessionInfo] = {}
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._max = max_sessions

    # ── Properties ────────────────────────────────────────────────────

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    @property
    def active_count(self) -> int:
        return sum(
            1 for s in self._sessions.values() if s.status in ("queued", "running")
        )

    # ── Session lifecycle ─────────────────────────────────────────────

    async def create_session(
        self,
        topic: str,
        time_budget: str = "medium",
        time_budget_seconds: int | None = None,
        model_mode: str = "same",
        selected_model: str | None = None,
        agent_models: dict[str, str] | None = None,
    ) -> SessionInfo:
        """Create and start a new research session. Returns immediately."""
        # Enforce max sessions — clean up oldest completed/errored first.
        if len(self._sessions) >= self._max:
            self._cleanup_old()

        session_id = str(uuid.uuid4())[:8]
        bus = EventBus()

        # Calculate seconds from named budget or direct value.
        if time_budget_seconds is not None:
            secs = time_budget_seconds
        else:
            secs = {"quick": 300, "medium": 900, "deep": 1800}.get(time_budget, 900)

        info = SessionInfo(
            session_id=session_id,
            topic=topic,
            time_budget=time_budget,
            time_budget_seconds=secs,
            model_mode=model_mode,
            selected_model=selected_model,
            agent_models=agent_models,
            status="queued",
            created_at=datetime.now().isoformat(),
            event_bus=bus,
        )
        self._sessions[session_id] = info

        # ── Model connectivity check ───────────────────────────────────
        test_model = selected_model or "gpt-4o"
        try:
            from deepresearch.llm.client import LLMClient

            test_client = LLMClient(model=test_model, timeout=10)
            await asyncio.wait_for(
                test_client.generate(
                    system_prompt="Respond with exactly one word: ok",
                    user_prompt="Test",
                    max_tokens=5,
                ),
                timeout=15,
            )
        except Exception as e:
            info.status = "error"
            info.error = f"Model connectivity check failed for '{test_model}': {e}"
            info.completed_at = datetime.now().isoformat()
            db = _load_session_db()
            db[info.session_id] = {
                "topic": info.topic, "status": info.status,
                "time_budget": info.time_budget, "time_budget_seconds": info.time_budget_seconds,
                "model_mode": info.model_mode, "selected_model": info.selected_model,
                "agent_models": info.agent_models,
                "created_at": info.created_at, "completed_at": info.completed_at,
                "error": info.error,
            }
            _save_session_db(db)
            await info.event_bus.publish({
                "event_type": "session_error",
                "session_id": session_id,
                "error": info.error,
            })
            return info

        # Start background task.
        self._tasks[session_id] = asyncio.create_task(
            self._run_session(session_id),
        )

        return info

    async def _run_session(self, session_id: str) -> None:
        """Run the full orchestration lifecycle with a per-session event bus."""
        info = self._sessions[session_id]
        info.status = "running"

        # Lazy imports to break circular dependency chain.
        from deepresearch.agents.registry import AgentRegistry
        from deepresearch.llm.client import LLMClient
        from deepresearch.orchestrator import Orchestrator

        try:
            # Publish session_start to this session's bus.
            await info.event_bus.publish({
                "event_type": "session_start",
                "session_id": session_id,
                "topic": info.topic,
            })

            llm = LLMClient()
            registry = AgentRegistry(llm)

            # Pick the scribe model: settings > selected_model > first agent_models > None
            import os as _os
            scribe_model = _os.environ.get("SCRIBE_MODEL") or info.selected_model
            if scribe_model is None and info.agent_models:
                scribe_model = next(iter(info.agent_models.values()), None)

            orchestrator = Orchestrator(
                agent_factory=registry.agent_factory,
                scribe_factory=lambda event_callback=None, model_name=None: registry.create_scribe_agent(
                    model_name=model_name or scribe_model, event_callback=event_callback
                ),
                event_bus=info.event_bus,
            )

            # Patch the event bus publish to also record events in session history
            # so late-connecting SSE clients can replay them.
            _original_publish = info.event_bus.publish

            async def _publish_with_history(event: dict[str, Any]) -> None:
                info.event_history.append(event)
                # Trim to last 500 events to avoid memory issues.
                if len(info.event_history) > 500:
                    info.event_history[:] = info.event_history[-500:]
                await _original_publish(event)

            info.event_bus.publish = _publish_with_history

            topic_slug = _slugify(info.topic) or "research"
            output_path = Path(f"./output/{session_id}/{topic_slug}.pdf")
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Build overrides dict with optional model selections.
            run_overrides: dict[str, Any] = {
                "time_budget": info.time_budget,
                "time_budget_seconds": info.time_budget_seconds,
                "model_mode": info.model_mode,
                "output_path": str(output_path),
            }
            if info.selected_model is not None:
                run_overrides["selected_model"] = info.selected_model
            if info.agent_models is not None:
                run_overrides["agent_models"] = info.agent_models

            pdf_path = await orchestrator.run(info.topic, **run_overrides)

            # Determine the HTML path (same stem, .html extension).
            html_path: str | None = None
            pdf_file = Path(pdf_path)
            if pdf_file.suffix == ".pdf":
                html_candidate = pdf_file.with_suffix(".html")
                if html_candidate.exists():
                    html_path = str(html_candidate)

            info.result = {
                "status": "complete",
                "pdf_path": str(pdf_path),
                "pdf_filename": pdf_file.name,
                "html_path": html_path,
            }
            info.status = "complete"
            info.completed_at = datetime.now().isoformat()
            # Persist to disk so it survives restarts
            db = _load_session_db()
            db[info.session_id] = {
                "topic": info.topic,
                "status": info.status,
                "time_budget": info.time_budget,
                "time_budget_seconds": info.time_budget_seconds,
                "model_mode": info.model_mode,
                "selected_model": info.selected_model,
                "agent_models": info.agent_models,
                "created_at": info.created_at,
                "completed_at": info.completed_at,
                "error": info.error,
            }
            _save_session_db(db)
            info.output_path = str(output_path)

            await info.event_bus.publish({
                "event_type": "session_end",
                "session_id": session_id,
                "status": "complete",
            })

        except asyncio.CancelledError:
            info.status = "cancelled"
            info.completed_at = datetime.now().isoformat()
            await info.event_bus.publish({
                "event_type": "session_end",
                "session_id": session_id,
                "status": "cancelled",
            })

        except Exception as exc:
            logger.exception("Session %s failed", session_id)
            info.status = "error"
            info.error = str(exc)
            info.completed_at = datetime.now().isoformat()
            await info.event_bus.publish({
                "event_type": "session_error",
                "session_id": session_id,
                "error": str(exc),
            })

    # ── Query methods ─────────────────────────────────────────────────

    def get_session(self, session_id: str) -> SessionInfo | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[dict]:
        """Return all sessions sorted by creation time (newest first)."""
        return [
            {
                "session_id": s.session_id,
                "topic": s.topic,
                "status": s.status,
                "time_budget": s.time_budget,
                "time_budget_seconds": s.time_budget_seconds,
                "model_mode": s.model_mode,
                "created_at": s.created_at,
                "completed_at": s.completed_at,
                "has_result": s.result is not None,
                "error": s.error,
            }
            for s in sorted(
                self._sessions.values(),
                key=lambda x: x.created_at,
                reverse=True,
            )
        ]

    # ── Cancel ────────────────────────────────────────────────────────

    async def cancel_session(self, session_id: str) -> bool:
        """Cancel a running session. Returns True if cancelled."""
        if session_id in self._tasks and not self._tasks[session_id].done():
            self._tasks[session_id].cancel()
            if session_id in self._sessions:
                self._sessions[session_id].status = "cancelled"
            return True
        return False

    def remove_session(self, session_id: str) -> bool:
        """Remove a session by ID (only if not running/queued). Returns True if removed."""
        info = self._sessions.get(session_id)
        if info is None:
            return False
        if info.status in ("running", "queued"):
            return False  # Don't remove active sessions
        self._remove_session(session_id)
        return True

    # ── Cleanup ───────────────────────────────────────────────────────

    def _cleanup_old(self) -> None:
        """Remove the oldest completed/error/cancelled session."""
        completed = [
            s for s in self._sessions.values()
            if s.status in ("complete", "error", "cancelled")
        ]
        if not completed:
            return
        oldest = min(completed, key=lambda x: x.created_at)
        self._remove_session(oldest.session_id)

    def _remove_session(self, session_id: str) -> None:
        """Remove a session from internal state."""
        self._sessions.pop(session_id, None)
        self._tasks.pop(session_id, None)
        # Clean up output files.
        output_dir = Path(f"./output/{session_id}")
        if output_dir.exists():
            shutil.rmtree(output_dir)

    def clear_completed(self) -> int:
        """Remove all completed/error/cancelled sessions. Returns count removed."""
        to_remove = [
            sid for sid, s in self._sessions.items()
            if s.status in ("complete", "error", "cancelled")
        ]
        for sid in to_remove:
            self._remove_session(sid)
        return len(to_remove)


# Module-level singleton used by the web server endpoints.
multi_session_manager = MultiSessionManager()
