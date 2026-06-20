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
from typing import Any, Callable

import json as _json
from deepresearch.config.session import TimeBudget
from deepresearch.llm.tracker import TokenTracker
from deepresearch.constants import PDF_MIN_HEALTHY_BYTES
from deepresearch.web.event_bus import EventBus
from deepresearch.web.settings_manager import settings_manager

logger = logging.getLogger(__name__)
SESSION_DB_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent / "output" / "sessions_db.json"
)


def _load_session_db() -> dict[str, dict]:
    """Load persisted session metadata from disk."""
    try:
        if SESSION_DB_PATH.exists():
            return _json.loads(SESSION_DB_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to load session DB: %s", e)
    return {}


_session_db_lock = asyncio.Lock()


async def _atomic_update_session_db(
    updater: Callable[[dict[str, dict]], None],
) -> None:
    """Atomically load, modify via updater, and persist session DB."""
    async with _session_db_lock:
        try:
            db = _load_session_db()
            updater(db)
            SESSION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            SESSION_DB_PATH.write_text(
                _json.dumps(db, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            logger.warning("Failed to update session DB: %s", e)


def _slugify(text: str, max_len: int = 50) -> str:
    """Convert text to a safe filename slug."""
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", text.lower().strip())
    slug = re.sub(r"_+", "_", slug).strip("_")
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
    max_rounds: int = 4  # Default matches SessionConfig
    output_language: str = "English"  # Output language for the compiled paper
    token_tracker: TokenTracker | None = None  # Shared session-level token aggregation


class MultiSessionManager:
    """Manages multiple concurrent research sessions.

    - Sessions run as independent asyncio tasks
    - Each session has its own EventBus for SSE streaming
    - Sessions are identified by short UUID (8 chars)
    - Old completed sessions are cleaned up when max is reached
    """

    def __init__(self, max_sessions: int = 20) -> None:
        self._sessions: dict[str, SessionInfo] = {}

        # Load persisted sessions from disk
        self._load_sessions_from_disk()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._cancel_events: dict[str, asyncio.Event] = {}
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
        scribe_model: str | None = None,
        max_rounds: int | None = None,
        output_language: str = "English",
        semaphore: asyncio.Semaphore | None = None,
    ) -> SessionInfo:
        """Create and start a new research session. Returns immediately."""
        # Enforce max sessions — clean up oldest completed/errored first.
        if len(self._sessions) >= self._max:
            self._cleanup_old()

        session_id = str(uuid.uuid4())[:8]

        # Calculate seconds and rounds from TimeBudget (single source of truth).
        if time_budget_seconds is not None:
            budget = TimeBudget(
                keyword="custom", seconds=time_budget_seconds, max_rounds=4
            )
        else:
            budget = TimeBudget.from_keyword(time_budget)
        secs = budget.seconds

        # Derive max_rounds from budget if not explicitly provided.
        if max_rounds is None:
            max_rounds = budget.max_rounds
        # Clamp to valid range.
        max_rounds = max(1, min(max_rounds, 10))

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
            # event_bus set below after EventBus construction
            max_rounds=max_rounds,
            output_language=output_language,
        )
        # Create EventBus wired to session's event_history so all
        # published events auto-record for re-navigation.
        bus = EventBus(history=info.event_history)
        info.event_bus = bus
        self._sessions[session_id] = info

        # ── Model connectivity check ───────────────────────────────────
        test_model = selected_model
        if not test_model:
            # Fall back to the first configured default model.
            try:
                from deepresearch.config import load_model_config

                model_configs = load_model_config()
                default_model = next(
                    (m for m in model_configs if m.get("default")),
                    model_configs[0] if model_configs else None,
                )
                test_model = default_model["id"] if default_model else None
            except Exception:
                test_model = None
        if not test_model:
            # Fallback: try llmfit's top research-optimized model
            try:
                import subprocess
                result = subprocess.run(
                    ["llmfit", "recommend", "-n", "1", "--capability", "tool_use",
                     "--min-fit", "good", "--json"],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    import json
                    data = json.loads(result.stdout)
                    models = data.get("models", [])
                    if models:
                        test_model = models[0]["name"]
            except Exception:
                pass
        if not test_model:
            raise ValueError(
                "No model configured. Set 'model' in request body or configure a default."
            )
        try:
            # Use direct Ollama API for local models to bypass LiteLLM's
            # broken model-info lookup (constructs /api/generate/api/show which 404s).
            if test_model and test_model.startswith("ollama/"):
                import httpx
                model_name = test_model.split("/", 1)[1]
                payload = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Say ok"}],
                    "stream": False,
                    "options": {"num_predict": 256},
                }
                async with httpx.AsyncClient(timeout=15) as hc:
                    r = await hc.post("http://localhost:11434/api/chat", json=payload)
                    if r.status_code != 200:
                        raise ConnectionError(
                            f"Ollama returned HTTP {r.status_code}: {r.text[:100]}"
                        )
            else:
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
            entry = {
                "topic": info.topic,
                "status": info.status,
                "time_budget": info.time_budget,
                "time_budget_seconds": info.time_budget_seconds,
                "model_mode": info.model_mode,
                "selected_model": info.selected_model,
                "agent_models": info.agent_models,
                "created_at": info.created_at,
                "completed_at": info.completed_at,
                "result": info.result,
                "error": info.error,
                "output_language": info.output_language,
            }
            await _atomic_update_session_db(
                lambda db: db.__setitem__(info.session_id, entry)
            )
            await info.event_bus.publish(
                {
                    "event_type": "session_error",
                    "session_id": session_id,
                    "error": info.error,
                }
            )
            return info

        # Start background task with optional concurrency semaphore.
        self._tasks[session_id] = asyncio.create_task(
            self._run_session(
                session_id, scribe_model=scribe_model, semaphore=semaphore
            ),
        )

        return info

    async def _run_session(
        self,
        session_id: str,
        scribe_model: str | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        """Run the full orchestration lifecycle with a per-session event bus."""
        info = self._sessions[session_id]
        info.status = "running"

        # Semaphore is already acquired by the server handler before
        # create_session() returns. Only release happens here (in finally).

        # Create a cancel event for this session so cancellation is
        # immediate even when an agent is inside a long LLM call.
        cancel_event = asyncio.Event()
        self._cancel_events[session_id] = cancel_event

        # Lazy imports to break circular dependency chain.
        from deepresearch.agents.registry import AgentRegistry
        from deepresearch.llm.client import LLMClient
        from deepresearch.orchestrator import Orchestrator

        try:
            # Publish session_start to this session's bus.
            await info.event_bus.publish(
                {
                    "event_type": "session_start",
                    "session_id": session_id,
                    "topic": info.topic,
                    "max_rounds": info.max_rounds,
                }
            )

            token_tracker = TokenTracker()
            info.token_tracker = token_tracker
            llm = LLMClient(
                max_tokens=settings_manager.get_max_tokens(), tracker=token_tracker
            )
            registry = AgentRegistry(llm, token_tracker=token_tracker)

            # Pick the scribe model: passed scribe_model > selected_model > first agent_models > None
            scribe_model = scribe_model or info.selected_model
            if scribe_model is None and info.agent_models:
                scribe_model = next(iter(info.agent_models.values()), None)

            orchestrator = Orchestrator(
                agent_factory=registry.agent_factory,
                scribe_factory=lambda event_callback=None, model_name=None: (
                    registry.create_scribe_agent(
                        model_name=model_name or scribe_model,
                        event_callback=event_callback,
                    )
                ),
                event_bus=info.event_bus,
            )

            topic_slug = _slugify(info.topic) or "research"
            # Use absolute path based on output directory location (SESSION_DB_PATH.parent)
            output_dir = SESSION_DB_PATH.parent / session_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / f"{topic_slug}.pdf"

            # Build overrides dict with optional model selections.
            run_overrides: dict[str, Any] = {
                "time_budget": info.time_budget,
                "time_budget_seconds": info.time_budget_seconds,
                "model_mode": info.model_mode,
                "output_path": str(output_path),
                "output_language": info.output_language,
            }
            if info.selected_model is not None:
                run_overrides["selected_model"] = info.selected_model
            if info.agent_models is not None:
                run_overrides["agent_models"] = info.agent_models
            if scribe_model is not None:
                run_overrides["scribe_model"] = scribe_model

            pdf_path = await orchestrator.run(
                info.topic, cancel_event=cancel_event, **run_overrides
            )

            # Determine the HTML path (same stem, .html extension).
            html_path: str | None = None
            pdf_file = Path(pdf_path)
            if pdf_file.suffix == ".pdf":
                html_candidate = pdf_file.with_suffix(".html")
                if html_candidate.exists():
                    html_path = str(html_candidate)

            # Check underweight PDF — mark as error instead of complete
            pdf_size = pdf_file.stat().st_size if pdf_file.exists() else 0
            is_underweight = getattr(orchestrator, "_pdf_underweight", False) or (
                pdf_file.exists() and pdf_size < PDF_MIN_HEALTHY_BYTES
            )
            if is_underweight:
                info.result = {
                    "status": "error",
                    "error": f"PDF too small ({pdf_size} bytes) — research likely incomplete",
                    "pdf_path": str(pdf_path),
                    "pdf_filename": pdf_file.name,
                }
                info.status = "error"
                info.error = f"PDF too small ({pdf_size} bytes)"
            else:
                # Check if all agents failed — mark as error instead of complete
                total_agents = len(
                    [
                        a
                        for a in (
                            orchestrator.session_config.agent_profiles
                            if orchestrator.session_config
                            else []
                        )
                    ]
                )
                all_failed = (
                    len(orchestrator.failed_agents) >= total_agents and total_agents > 0
                )

                if all_failed:
                    info.result = {
                        "status": "error",
                        "error": f"All agents failed ({len(orchestrator.failed_agents)}/{total_agents})",
                        "pdf_path": str(pdf_path),
                        "pdf_filename": pdf_file.name,
                    }
                    info.status = "error"
                    info.error = f"All agents failed: {', '.join(orchestrator.failed_agents.keys())}"
                else:
                    info.result = {
                        "status": "complete",
                        "pdf_path": str(pdf_path),
                        "pdf_filename": pdf_file.name,
                        "html_path": html_path,
                    }
                    info.status = "complete"
            info.completed_at = datetime.now().isoformat()
            # Persist to disk so it survives restarts
            entry = {
                "topic": info.topic,
                "status": info.status,
                "time_budget": info.time_budget,
                "time_budget_seconds": info.time_budget_seconds,
                "model_mode": info.model_mode,
                "selected_model": info.selected_model,
                "agent_models": info.agent_models,
                "created_at": info.created_at,
                "completed_at": info.completed_at,
                "result": info.result,
                "error": info.error,
                "output_language": info.output_language,
            }
            await _atomic_update_session_db(
                lambda db: db.__setitem__(info.session_id, entry)
            )
            info.output_path = str(output_path)

            await info.event_bus.publish(
                {
                    "event_type": "session_end",
                    "session_id": session_id,
                    "status": "complete",
                }
            )

        except asyncio.CancelledError:
            info.status = "cancelled"
            info.error = "Cancelled by user"
            info.completed_at = datetime.now().isoformat()
            await info.event_bus.publish(
                {
                    "event_type": "session_end",
                    "session_id": session_id,
                    "status": "cancelled",
                }
            )

        except Exception as exc:
            logger.exception("Session %s failed", session_id)
            info.status = "error"
            info.error = str(exc)
            info.completed_at = datetime.now().isoformat()
            await info.event_bus.publish(
                {
                    "event_type": "session_error",
                    "session_id": session_id,
                    "error": str(exc),
                }
            )

        finally:
            self._cancel_events.pop(session_id, None)
            # Release concurrency semaphore if held.
            if semaphore is not None:
                semaphore.release()

    # ── Query methods ─────────────────────────────────────────────────

    def get_session(self, session_id: str) -> SessionInfo | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    def list_sessions(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        status_filter: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        """Return sessions sorted by creation time (newest first).

        Returns:
            dict with keys: sessions, total (count after filtering),
            offset, limit (echoed back).
        """
        sessions = sorted(
            self._sessions.values(),
            key=lambda x: x.created_at or "",
            reverse=True,
        )

        # Filter by status
        if status_filter and status_filter != "all":
            sessions = [s for s in sessions if s.status == status_filter]

        # Search by topic (case-insensitive substring)
        if search:
            q = search.lower()
            sessions = [s for s in sessions if q in (s.topic or "").lower()]

        total = len(sessions)

        # Apply pagination
        if limit is not None:
            sessions = sessions[offset : offset + limit]

        return {
            "sessions": [
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
                    "result": s.result,
                    "error": s.error,
                }
                for s in sessions
            ],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    # ── Cancel ────────────────────────────────────────────────────────

    async def cancel_session(self, session_id: str) -> bool:
        """Cancel a running session. Returns True if cancelled.

        Sets the per-session cancel event so that LLM retry loops
        and other cancel-aware code can exit immediately, then
        cancels the asyncio Task for interruptible await points.
        """
        if session_id in self._tasks and not self._tasks[session_id].done():
            # Signal cancellation immediately — cancel_event.is_set()
            # is checked before each retry and before each agent task.
            if session_id in self._cancel_events:
                self._cancel_events[session_id].set()
            self._tasks[session_id].cancel()
            if session_id in self._sessions:
                self._sessions[session_id].status = "cancelled"
                self._sessions[session_id].error = "Cancelled by user"
            return True
        return False

    def _load_sessions_from_disk(self) -> None:
        """Restore completed sessions from disk on startup."""
        db = _load_session_db()
        for sid, data in db.items():
            info = SessionInfo(
                session_id=sid,
                topic=data.get("topic", ""),
                status=data.get("status", "complete"),
                time_budget=data.get("time_budget", "medium"),
                time_budget_seconds=data.get("time_budget_seconds"),
                model_mode=data.get("model_mode", "same"),
                selected_model=data.get("selected_model"),
                agent_models=data.get("agent_models"),
                created_at=data.get("created_at", datetime.now().isoformat()),
                completed_at=data.get("completed_at"),
                result=data.get("result"),
                error=data.get("error"),
                output_language=data.get("output_language", "English"),
            )
            # Fallback: if result wasn't persisted (legacy sessions), derive from filesystem
            if not info.result:
                topic_slug = (
                    re.sub(r"[^a-zA-Z0-9_-]", "_", (info.topic or "").lower().strip())[
                        :50
                    ]
                    or "research"
                )
                pdf_path = SESSION_DB_PATH.parent / sid / f"{topic_slug}.pdf"
                if pdf_path.exists():
                    info.result = {
                        "pdf_filename": pdf_path.name,
                        "pdf_path": str(pdf_path),
                    }
            self._sessions[sid] = info
        if db:
            logger.info("Restored %d sessions from disk", len(db))

    def get_all_sessions(self) -> list[SessionInfo]:
        """Return all sessions, newest first."""
        return sorted(
            self._sessions.values(),
            key=lambda s: s.created_at or "",
            reverse=True,
        )

    def remove_session(self, session_id: str) -> bool:
        """Remove a session by ID (only if not running/queued). Returns True if removed."""
        info = self._sessions.get(session_id)
        if info is None:
            return False
        if info.status in ("running", "queued"):
            return False  # Don't remove active sessions
        self._remove_session(session_id, delete_files=True)
        return True

    # ── Cleanup ───────────────────────────────────────────────────────

    def _cleanup_old(self) -> None:
        """Remove the oldest completed/error/cancelled session."""
        completed = [
            s
            for s in self._sessions.values()
            if s.status in ("complete", "error", "cancelled")
        ]
        if not completed:
            return
        oldest = min(completed, key=lambda x: x.created_at)
        self._remove_session(oldest.session_id)

    def _remove_session(self, session_id: str, delete_files: bool = False) -> None:
        """Remove a session from internal state and persistent DB.

        Args:
            session_id: The session to remove.
            delete_files: If True, also delete output files (default: False).
        """
        self._sessions.pop(session_id, None)
        self._tasks.pop(session_id, None)
        self._cancel_events.pop(session_id, None)
        # Also remove from persistent DB so deleted sessions don't reappear on restart
        try:
            db = _load_session_db()
            if session_id in db:
                del db[session_id]
                SESSION_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                SESSION_DB_PATH.write_text(
                    _json.dumps(db, indent=2, default=str), encoding="utf-8"
                )
        except Exception:
            pass
        # Only delete files if explicitly requested
        if delete_files:
            output_dir = SESSION_DB_PATH.parent / session_id
            if output_dir.exists():
                shutil.rmtree(output_dir)

    def clear_completed(self) -> int:
        """Remove all completed/error/cancelled sessions from DB only. Returns count removed."""
        to_remove = [
            sid
            for sid, s in self._sessions.items()
            if s.status in ("complete", "error", "cancelled")
        ]
        for sid in to_remove:
            self._remove_session(sid, delete_files=False)  # Keep files!
        return len(to_remove)

    async def save_all_sessions(self) -> None:
        """Save ALL sessions to persistent DB before shutdown.

        Running/interrupted sessions get their current state preserved.
        Completed sessions are re-saved with latest data.
        """
        from datetime import datetime

        for session_id, info in list(self._sessions.items()):
            try:
                if info.status == "running":
                    info.status = "interrupted"
                    task = self._tasks.get(session_id)
                    if task and not task.done():
                        task.cancel()
                    logger.info("Marked session %s as interrupted", session_id)

                entry = {
                    "session_id": info.session_id,
                    "topic": info.topic,
                    "status": info.status or "unknown",
                    "model": info.selected_model,
                    "max_rounds": info.max_rounds,
                    "time_budget_seconds": info.time_budget_seconds,
                    "source": getattr(info, "source", None),
                    "output_file": getattr(info, "output_path", None),
                    "created_at": str(info.created_at),
                    "updated_at": str(datetime.now()),
                    "event_history": (info.event_history or [])[-100:],
                    "agent_states": getattr(info, "agent_states", {}),
                }
                await _atomic_update_session_db(
                    lambda db: db.__setitem__(session_id, entry)
                )
            except Exception as e:
                logger.error("Failed to save session %s: %s", session_id, e)


# Module-level singleton used by the web server endpoints.
multi_session_manager = MultiSessionManager()
