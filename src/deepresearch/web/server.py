"""FastAPI web server for the DeepeResearch real-time dashboard.

Provides multi-session management, SSE event streaming, settings
configuration (API keys, local models), and file download endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import re
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles

from deepresearch import __version__ as _deepresearch_version
from deepresearch.config import load_agent_profiles, load_model_config
from deepresearch.constants import TIME_BUDGETS
from deepresearch.web.event_bus import event_bus as global_event_bus
from deepresearch.web.sessions import multi_session_manager
from deepresearch.web.settings_manager import (
    PROVIDERS,
    settings_manager,
    context_window_manager,
    local_backend_manager,
)
from deepresearch.web import state as _ws

logger = logging.getLogger(__name__)

# ── Session concurrency limit ─────────────────────────────────────────────
# Maximum number of sessions that can run concurrently across the server.
# Configurable via --max-concurrent CLI argument (default 3, range 1-10).
MAX_CONCURRENT_SESSIONS = 3
_session_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)

# ── Persistent file logging ─────────────────────────────────────────────
_log_dir = Path(__file__).resolve().parent.parent.parent.parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "deepresearch.log"

_file_handler = logging.handlers.RotatingFileHandler(
    _log_file,
    maxBytes=10_485_760,
    backupCount=5,  # 10 MB per file, keep 5
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)

# Add to root logger — catches ALL deepresearch.* loggers
root_logger = logging.getLogger()
root_logger.addHandler(_file_handler)
root_logger.setLevel(logging.DEBUG)

logging.getLogger("deepresearch").info("File logging initialized: %s", _log_file)

# Suppress noisy third-party loggers in file output.
for _noisy in (
    "LiteLLM",
    "LiteLLM.litellm",
    "httpx",
    "httpcore",
    "asyncio",
    "weasyprint",
    "fontTools",
    "PIL",
    "matplotlib",
    "fpdf",
):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# ── Persistent download state (survives page refresh) ──────────────────
_download_state: dict[str, Any] = {
    "active": False,
    "model": "",
    "progress": 0,
    "message": "",
    "status": "idle",  # idle | downloading | complete | error
    "log": [],  # last 50 log lines
}
_download_process: asyncio.subprocess.Process | None = None
_download_task: asyncio.Task | None = None

# ── Load .env keys into os.environ at startup ──────────────────────────
# This ensures LLMClient (which reads from os.environ) can find API keys
# that were persisted to .env via dashboard settings across server restarts.
_settings_env_path = settings_manager._settings_dir / ".env"
if _settings_env_path.exists():
    _loaded = 0
    with open(_settings_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                if _v and _k not in os.environ:
                    os.environ[_k] = _v
                    _loaded += 1
    if _loaded:
        logger.info("Loaded %d API key(s) from .env into environment", _loaded)

VERSION = f"v{_deepresearch_version}"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Lifespan handler — saves session state on shutdown."""
    yield
    # Shutdown: save all sessions to persistent DB
    logger.warning("Server shutting down — saving session state...")
    try:
        from deepresearch.web.sessions import multi_session_manager

        await multi_session_manager.save_all_sessions()
        logger.warning("Session state saved successfully")
    except Exception as e:
        logger.error("Failed to save sessions during shutdown: %s", e)


app = FastAPI(title="DeepeResearch Dashboard", lifespan=_lifespan)

# ── Serve static files (CSS, JS modules) ────────────────────────────────
HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
logger.info(
    "DeepeResearch %s starting on port %d",
    VERSION,
    __import__("os").environ.get("PORT", 7500),
)

# ── CORS (allow browser-based access from any origin) ──────────────────
# NOTE: allow_credentials=False because allow_origins=["*"] is used.
# CORS spec explicitly forbids the combination of wildcard origin + credentials.
# The dashboard uses fetch() without credentials, so this is safe.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models for request/response ──────────────────────────────


class RunRequest(BaseModel):
    """Request body for POST /api/run."""

    topic: str
    time_budget: str = "medium"
    time_budget_seconds: int | None = None
    model_mode: str = "same"
    selected_model: str | None = None  # NEW: for "same" mode — which model
    agent_models: dict[str, str] | None = (
        None  # NEW: for "manual" mode — per-agent model mapping
    )
    max_rounds: int | None = None  # Override max rounds (1-10)
    output_language: str = "English"  # Output language for the compiled paper


class RunResponse(BaseModel):
    """Response from POST /api/run."""

    status: str
    session_id: str
    topic: str
    time_budget: str
    model_mode: str


# ── Endpoints ───────────────────────────────────────────────────────────

HERE = Path(__file__).resolve().parent


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Serve the self-contained dashboard HTML page."""
    html_path = HERE / "dashboard.html"
    return html_path.read_text(encoding="utf-8")


# ── Global SSE (legacy) ────────────────────────────────────────────────


@app.get("/api/events")
async def event_stream(request: Request) -> EventSourceResponse:
    """SSE endpoint: streams orchestrator events as ``event:`` messages.

    This global stream receives all events from all sessions. Use the
    per-session endpoint ``/api/sessions/{session_id}/events`` for a
    session-specific stream.

    Each event's ``data`` is a JSON object. The connection stays open
    until the client disconnects.
    """
    queue = await global_event_bus.subscribe()
    logger.debug(
        "SSE client connected (subscriber count: %d)", global_event_bus.subscriber_count
    )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            while True:
                if await request.is_disconnected():
                    logger.debug("SSE client disconnected")
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            await global_event_bus.unsubscribe(queue)
            logger.debug(
                "SSE subscriber cleaned up (remaining: %d)",
                global_event_bus.subscriber_count,
            )

    return EventSourceResponse(generate())


# ── Legacy status endpoint (kept for backward compatibility) ────────────


@app.get("/api/status")
async def get_status() -> JSONResponse:
    """Return the current (latest) session state as JSON (polling fallback)."""
    return JSONResponse(
        {
            "state": _ws._current_state,
            "topic": _ws._current_topic,
            "agents": _ws._current_agents,
            "agent_progress": _ws._agent_progress,
            "elapsed_start": _ws._elapsed_start,
            "session_active": _ws._session_active,
            "phase_label": _ws._phase_label,
        }
    )


@app.get("/api/version")
async def get_version() -> JSONResponse:
    """Return the current dashboard version for deployment verification."""
    return JSONResponse({"version": VERSION})


@app.get("/api/agents")
async def get_agents() -> JSONResponse:
    """Return agent profile metadata."""
    from deepresearch.config import load_agent_profiles

    profiles = load_agent_profiles()
    return JSONResponse(
        [{"id": p.id, "name": p.name, "emoji": p.emoji} for p in profiles]
    )


# ── Multi-session Management Endpoints ─────────────────────────────────


@app.post("/api/run")
async def start_research(req: RunRequest) -> JSONResponse:
    """Start a new research session in the background.

    Returns immediately with a ``session_id``. The session runs
    independently and its progress can be tracked via the per-session
    SSE endpoint or the session listing.

    Respects the global session concurrency limit. If the limit is
    reached, returns HTTP 429 with active session count.
    """
    # Atomic concurrency check — acquire immediately or reject.
    # This replaces the old non-atomic locked() check.
    # Note: wait_for(timeout=0) always raises TimeoutError for coroutines
    # in Python 3.12+ (coro is never "done" before being awaited), so a
    # small non-zero timeout is used instead.  When capacity is available,
    # acquire() completes synchronously before the timer fires (1 ms).
    try:
        await asyncio.wait_for(_session_semaphore.acquire(), timeout=0.001)
    except asyncio.TimeoutError:
        return JSONResponse(
            {
                "error": "Concurrency limit reached",
                "active_sessions": multi_session_manager.active_count,
                "max_concurrent": MAX_CONCURRENT_SESSIONS,
            },
            status_code=429,
        )

    try:
        # Pre-flight check: verify Ollama is running if selected model uses it
        if req.selected_model and req.selected_model.startswith("ollama/"):
            try:
                async with httpx.AsyncClient(timeout=5) as hc:
                    r = await hc.get("http://localhost:11434/api/tags")
                    if r.status_code != 200:
                        _session_semaphore.release()
                        return JSONResponse(
                            {"error": f"Ollama is not responding (HTTP {r.status_code}). Make sure Ollama is running."},
                            status_code=503,
                        )
                    # Check if the specific model exists
                    model_name = req.selected_model.split("/", 1)[1]
                    models = r.json().get("models", [])
                    if not any(model_name in (m.get("name") or "") for m in models):
                        _session_semaphore.release()
                        return JSONResponse(
                            {"error": f"Model '{model_name}' not found in Ollama. Run 'ollama pull {model_name}' first."},
                            status_code=400,
                        )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                _session_semaphore.release()
                return JSONResponse(
                    {"error": f"Cannot connect to Ollama on localhost:11434. Is Ollama running? ({e})"},
                    status_code=503,
                )
        
        scribe_model = settings_manager.get_scribe_model()
        info = await multi_session_manager.create_session(
            topic=req.topic,
            time_budget=req.time_budget,
            time_budget_seconds=req.time_budget_seconds,
            model_mode=req.model_mode,
            selected_model=req.selected_model,
            agent_models=req.agent_models,
            scribe_model=scribe_model,
            max_rounds=req.max_rounds,
            output_language=req.output_language,
            semaphore=_session_semaphore,
        )
        # Semaphore released by _run_session's finally block.
        return JSONResponse(
            {
                "status": "started",
                "session_id": info.session_id,
                "topic": info.topic,
                "time_budget": info.time_budget,
                "model_mode": info.model_mode,
            }
        )
    except Exception as e:
        _session_semaphore.release()
        logger.warning("Session creation failed, semaphore released: %s", e)
        return JSONResponse({"error": str(e)}, status_code=409)


@app.get("/api/sessions")
async def list_sessions(
    limit: int | None = None,
    offset: int = 0,
    status: str | None = None,
    search: str | None = None,
) -> JSONResponse:
    """List research sessions with optional filtering and pagination.

    Query params:
        limit: Max sessions to return (omit for all).
        offset: Skip first N sessions (for pagination).
        status: Filter by status (running, complete, error, cancelled, all).
        search: Filter by topic substring (case-insensitive).
    """
    result = multi_session_manager.list_sessions(
        limit=limit,
        offset=offset,
        status_filter=status,
        search=search,
    )
    return JSONResponse(result)


@app.get("/api/sessions/stats")
async def session_stats() -> JSONResponse:
    """Return session counts grouped by status."""
    sessions = multi_session_manager.list_sessions()
    all_sessions = sessions.get("sessions", [])
    counts: dict[str, int] = {}
    for s in all_sessions:
        st = s.get("status", "unknown")
        counts[st] = counts.get(st, 0) + 1
    return JSONResponse(
        {
            "total": len(all_sessions),
            "by_status": counts,
        }
    )


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str) -> JSONResponse:
    """Get details for a specific session."""
    info = multi_session_manager.get_session(session_id)
    if info is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    return JSONResponse(
        {
            "session_id": info.session_id,
            "topic": info.topic,
            "status": info.status,
            "time_budget": info.time_budget,
            "time_budget_seconds": info.time_budget_seconds,
            "estimated_duration_seconds": {
                kw: v["seconds"] for kw, v in TIME_BUDGETS.items()
            },
            "model_mode": info.model_mode,
            "created_at": info.created_at,
            "completed_at": info.completed_at,
            "result": info.result,
            "error": info.error,
        }
    )


@app.get("/api/sessions/{session_id}/state")
async def get_session_state(session_id: str) -> JSONResponse:
    """Get the current runtime state of a running session.

    Returns agent states, scribe info, event count, and other live
    data that the dashboard needs to restore its view after navigation.
    """
    info = multi_session_manager.get_session(session_id)
    if info is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    # Extract agent states from event history
    agent_states: dict[str, dict[str, str]] = {}
    scribe_info: dict[str, str] = {"status": "waiting", "state": "waiting"}
    current_state = "IDLE"

    for event in info.event_history:
        event_type = event.get("event_type", "")

        if event_type == "session_start":
            current_state = "CONFIGURING"
        elif event_type == "models_assigned":
            current_state = "ROUND1"
            for agent_id in event.get("assignments") or {}:
                if agent_id not in agent_states:
                    agent_states[agent_id] = {"status": "waiting", "state": "waiting"}
        elif event_type == "round_start":
            round_num = event.get("round", 1)
            current_state = f"ROUND{round_num}"
            for aid in agent_states:
                agent_states[aid] = {"status": "waiting", "state": "waiting"}
        elif event_type == "agent_start":
            aid = event.get("agent_id", "")
            if aid:
                agent_states[aid] = {
                    "status": "running",
                    "state": event.get("agent_state", "researching"),
                }
        elif event_type == "agent_complete":
            aid = event.get("agent_id", "")
            if aid and aid in agent_states:
                agent_states[aid]["status"] = "done"
        elif event_type == "agent_failed":
            aid = event.get("agent_id", "")
            if aid:
                agent_states[aid] = {"status": "failed", "state": "failed"}
        elif event_type == "collaboration_phase":
            current_state = "COLLABORATING"
            for aid in agent_states:
                if agent_states[aid].get("status") != "failed":
                    agent_states[aid] = {"status": "waiting", "state": "waiting"}
        elif event_type == "followup_start":
            current_state = "FOLLOWUP"
        elif event_type == "refinement_start":
            current_state = "REFINING"
        elif event_type == "scribe_start":
            current_state = "COMPILING"
            scribe_info = {"status": "running", "state": "writing"}
        elif event_type == "scribe_end":
            scribe_info = {"status": "done", "state": "done"}
        elif event_type == "pdf_generated":
            current_state = "OUTPUT"
        elif event_type == "session_end":
            current_state = "COMPLETE"
        elif event_type == "session_error":
            current_state = "ERROR"

    return JSONResponse(
        {
            "session_id": info.session_id,
            "topic": info.topic,
            "status": info.status,
            "current_state": current_state,
            "agent_states": agent_states,
            "scribe_info": scribe_info,
            "event_count": len(info.event_history),
            "elapsed_start": info.created_at,
            "max_rounds": info.max_rounds,
        }
    )


@app.get("/api/sessions/{session_id}/cost")
async def get_session_cost(session_id: str) -> JSONResponse:
    """Return token usage and cost for a session."""
    info = multi_session_manager.get_session(session_id)
    if info is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    tracker = getattr(info, "token_tracker", None)
    if tracker is None:
        return JSONResponse({"total_cost": 0, "total_tokens": 0, "per_model": {}})
    return JSONResponse(tracker.to_dict())


@app.get("/api/sessions/{session_id}/events")
async def session_event_stream(
    session_id: str, request: Request
) -> EventSourceResponse:
    """SSE endpoint: stream events for a specific session."""
    info = multi_session_manager.get_session(session_id)
    if info is None:
        return EventSourceResponse(
            async_generator=lambda: _error_generator("Session not found"),
        )

    bus = info.event_bus
    if bus is None:
        # Completed/persisted session — return session data directly instead of SSE
        return JSONResponse(
            {
                "event_type": "session_data",
                "session_id": session_id,
                "topic": info.topic or "",
                "status": info.status or "complete",
                "state": "COMPLETE",
                "result": info.result,
                "error": info.error,
                "completed_at": info.completed_at,
            }
        )

    queue = await bus.subscribe()

    async def generate() -> AsyncGenerator[str, None]:
        try:
            # FIRST: replay all buffered events from session history.
            # This ensures late-connecting SSE clients see events that
            # were published before they subscribed (e.g. models_assigned).
            for event in info.event_history:
                if await request.is_disconnected():
                    break
                yield {"data": json.dumps(event)}

            # THEN: stream live events from the bus.
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {"data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            await bus.unsubscribe(queue)

    return EventSourceResponse(generate())


async def _error_generator(msg: str) -> AsyncGenerator[str, None]:
    """Generate a single error event."""
    yield {"event": "error", "data": json.dumps({"error": msg})}


async def _install_error_generator(
    msg: str, code: str = "ALREADY_INSTALLED"
) -> AsyncGenerator[str, None]:
    """Generate a single install error event."""
    yield {
        "event": "install_error",
        "data": json.dumps({"status": "error", "message": msg, "code": code}),
    }


@app.post("/api/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> JSONResponse:
    """Cancel a running session."""
    cancelled = await multi_session_manager.cancel_session(session_id)
    if cancelled:
        return JSONResponse({"status": "cancelled", "session_id": session_id})
    return JSONResponse(
        {"status": "not_found_or_already_done", "session_id": session_id}
    )


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str) -> JSONResponse:
    """Delete a completed/error/cancelled session."""
    removed = multi_session_manager.remove_session(session_id)
    if removed:
        return JSONResponse({"status": "deleted", "session_id": session_id})
    # Check if exists but not deletable (running/queued)
    info = multi_session_manager.get_session(session_id)
    if info is not None:
        return JSONResponse(
            {"error": "Cannot delete a running session — cancel it first"},
            status_code=409,
        )
    return JSONResponse({"error": "Session not found"}, status_code=404)


@app.post("/api/sessions/clear-completed")
async def clear_completed_sessions() -> JSONResponse:
    """Remove all completed/error/cancelled sessions."""
    count = multi_session_manager.clear_completed()
    return JSONResponse({"status": "ok", "removed": count})


@app.post("/api/sessions/bulk-delete")
async def bulk_delete_sessions(request: Request) -> JSONResponse:
    """Delete multiple sessions by ID list.

    Request body: {"session_ids": ["id1", "id2", ...]}
    """
    body = await request.json()
    ids = body.get("session_ids", [])
    if not ids:
        return JSONResponse({"error": "No session IDs provided"}, status_code=400)

    removed = 0
    errors = []
    for sid in ids:
        ok = multi_session_manager.remove_session(sid)
        if ok:
            removed += 1
        else:
            info = multi_session_manager.get_session(sid)
            if info is not None:
                errors.append(f"{sid}: Cannot delete a running session")
            else:
                errors.append(f"{sid}: Not found")

    return JSONResponse({"status": "ok", "removed": removed, "errors": errors})


# ── Legacy session endpoints (kept for backward compatibility) ─────────


@app.get("/api/session")
async def get_legacy_session() -> JSONResponse:
    """Return the latest session status and result (backward compat)."""
    result = multi_session_manager.list_sessions()
    sessions = result.get("sessions", [])
    if not sessions:
        return JSONResponse({"status": "idle", "result": None})
    latest = sessions[0]
    info = multi_session_manager.get_session(latest["session_id"])
    return JSONResponse(
        {
            "status": info.status if info else "idle",
            "result": info.result if info else None,
        }
    )


@app.post("/api/cancel")
async def cancel_legacy() -> JSONResponse:
    """Cancel the most recent running session (backward compat)."""
    # Find the most recent running session.
    result = multi_session_manager.list_sessions()
    sessions = result.get("sessions", [])
    for s in sessions:
        if s["status"] == "running":
            cancelled = await multi_session_manager.cancel_session(s["session_id"])
            if cancelled:
                return JSONResponse({"status": "cancelled"})
    return JSONResponse({"status": "no_active_session"})


# ── File Download Endpoint ─────────────────────────────────────────────


@app.get("/api/download/{session_id}/{filename:path}")
async def download_file(session_id: str, filename: str) -> Any:
    """Download a generated file (PDF, HTML, or fallback text) for a session."""
    from fastapi.responses import FileResponse

    # PurePosixPath validation — rejects absolute paths and ".." lexically
    requested_path = PurePosixPath(filename)
    if not requested_path.is_relative_to(PurePosixPath(".")):
        return JSONResponse({"error": "Invalid path"}, status_code=403)

    # Prevent path traversal (legacy string-level check)
    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    # Only allow files from the session's output directory (absolute path)
    from deepresearch.web.sessions import SESSION_DB_PATH

    DOWNLOADS_DIR = SESSION_DB_PATH.parent
    base_dir = DOWNLOADS_DIR / session_id
    safe_path = (base_dir / filename).resolve()
    if not str(safe_path).startswith(str(base_dir.resolve())):
        return JSONResponse({"error": "Access denied"}, status_code=403)

    # Also check the legacy flat output directory as a fallback
    possible_paths: list[Path] = [safe_path]

    if not safe_path.exists():
        # Fallback: search in the flat output directory
        legacy_path = SESSION_DB_PATH.parent / filename
        possible_paths.append(legacy_path)

    for path in possible_paths:
        if path.exists() and path.is_file():
            media_type = (
                "application/pdf"
                if path.suffix == ".pdf"
                else "text/html"
                if path.suffix in (".html", ".htm")
                else "text/plain"
            )
            headers = {"Content-Disposition": f'inline; filename="{path.name}"'}
            return FileResponse(
                path, media_type=media_type, filename=path.name, headers=headers
            )

    return JSONResponse({"error": f"File not found: {filename}"}, status_code=404)


# ── Configuration Endpoints ────────────────────────────────────────────


@app.get("/api/profiles")
async def get_profiles() -> JSONResponse:
    """Return the list of available agent profiles."""
    try:
        profiles = load_agent_profiles()
        profile_list = [
            {
                "id": p.id,
                "name": p.name,
                "emoji": p.emoji,
                "temperature": p.temperature,
                "voice": p.voice,
            }
            for p in profiles
        ]
        return JSONResponse(profile_list)
    except Exception as e:
        logger.exception("Failed to load profiles")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/models")
async def get_models() -> JSONResponse:
    """Return the list of available model configurations, including local
    and auto-discovered provider models.  Context window overrides from
    settings are applied on top of the YAML defaults."""
    try:
        models = load_model_config()

        # Append locally discovered models from settings.
        local_endpoints = settings_manager.get_local_endpoints()
        for ep in local_endpoints:
            models.append(
                {
                    "id": ep.get("name", "local-model"),
                    "provider": ep.get("type", "local"),
                    "display_name": f"{ep.get('name', 'Local')} ({ep.get('endpoint', '?')})",
                    "default": False,
                    "endpoint": ep.get("endpoint"),
                }
            )

        # Append auto-discovered Ollama models from settings.
        discovered = _get_discovered_local_models()
        for d in discovered:
            # Avoid duplicates.
            if not any(m.get("id") == d["id"] for m in models):
                models.append(d)

        # NEW: Auto-discover models from ALL configured API-keyed providers.
        provider_models = await _discover_provider_models()
        for pm in provider_models:
            if not any(m.get("id") == pm["id"] for m in models):
                models.append(pm)

        # Apply context window overrides from settings.
        overrides = context_window_manager.get_overrides()
        for m in models:
            mid = m.get("id", "")
            if mid in overrides:
                m["context_window"] = overrides[mid]

        return JSONResponse(models)
    except Exception as e:
        logger.exception("Failed to load models")
        return JSONResponse({"error": str(e)}, status_code=500)


_discovered_local_models_cache: list[dict[str, Any]] = []
_discovered_local_models_time: float = 0


def _get_discovered_local_models() -> list[dict[str, Any]]:
    """Return cached discovered local models (refreshed every 60s)."""
    global _discovered_local_models_cache, _discovered_local_models_time
    now = time.time()
    if now - _discovered_local_models_time < 60 and _discovered_local_models_cache:
        return _discovered_local_models_cache

    # Try Ollama discovery.
    discovered: list[dict[str, Any]] = []
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            for model in data.get("models", []):
                discovered.append(
                    {
                        "id": f"ollama/{model['name']}",
                        "provider": "ollama",
                        "display_name": model["name"],
                        "default": False,
                        "endpoint": "http://localhost:11434",
                        "source": "ollama",
                    }
                )
    except Exception:
        pass

    _discovered_local_models_cache = discovered
    _discovered_local_models_time = now
    return discovered


# ── Provider Model Discovery ───────────────────────────────────────────
#
# Auto-discover models from ALL configured (API-keyed) providers.
# Cached for 60 seconds, same as local model discovery.

# Model listing API endpoints for supported providers.
PROVIDER_MODEL_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "together": "https://api.together.xyz/v1/models",
    "deepseek": "https://api.deepseek.com/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1/models",
    "cohere": "https://api.cohere.ai/v1/models",
}

_discovered_provider_models_cache: list[dict[str, Any]] = []
_discovered_provider_models_time: float = 0


def _get_api_key(provider_id: str) -> str | None:
    """Get the actual API key for a provider from env or .env file."""
    info = PROVIDERS.get(provider_id)
    if not info:
        return None
    env_var = info["env_var"]
    # Check environment first (set_key also sets os.environ).
    key = os.environ.get(env_var)
    if key:
        return key
    # Fall back to the .env file on disk.
    return settings_manager._get_from_file(env_var)


def _get_provider_auth(
    provider_id: str, api_key: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (headers, query_params) for a provider's model API.

    Different providers use different auth mechanisms:
      - OpenAI-compatible: ``Authorization: Bearer <key>`` header
      - Anthropic: ``x-api-key`` header + version header
      - Google: ``?key=<key>`` query parameter
      - Cohere: ``Authorization: Bearer <key>`` header
    """
    if provider_id == "gemini":
        return {}, {"key": api_key}
    if provider_id == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, {}
    # Everything else uses Bearer token.
    return {"Authorization": f"Bearer {api_key}"}, {}


def _parse_provider_models(provider_id: str, data: dict | list) -> list[dict[str, str]]:
    """Parse a provider's model-list API response into ``[{id, display_name}]``.

    Handles the idiosyncratic response shapes of each provider.
    """
    if provider_id == "anthropic":
        # Anthropic: {"data": [{"type": "model", "id": "claude-...", "display_name": "..."}]}
        raw = data.get("data", []) if isinstance(data, dict) else data
        return [
            {"id": m["id"], "display_name": m.get("display_name") or m["id"]}
            for m in raw
            if isinstance(m, dict) and m.get("type") == "model"
        ]

    if provider_id == "gemini":
        # Google: {"models": [{"name": "models/gemini-pro", "displayName": "Gemini Pro", ...}]}
        raw = data.get("models", []) if isinstance(data, dict) else []
        result: list[dict[str, str]] = []
        for m in raw:
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[7:]
            result.append({"id": name, "display_name": m.get("displayName", name)})
        return result

    if provider_id == "cohere":
        # Cohere returns a top-level JSON array: [{"name": "command-r-plus", ...}]
        if isinstance(data, list):
            return [
                {
                    "id": m.get("name", m.get("id", "")),
                    "display_name": m.get("name", m.get("id", "")),
                }
                for m in data
                if isinstance(m, dict)
            ]
        return []

    # OpenAI-compatible (openai, openrouter, groq, together, deepseek):
    # {"data": [{"id": "gpt-4o", ...}]}
    raw = data.get("data", []) if isinstance(data, dict) else data
    return [{"id": m["id"]} for m in raw if isinstance(m, dict) and "id" in m]


async def _discover_provider_models() -> list[dict[str, Any]]:
    """Fetch model lists from all configured (API-keyed) providers.

    Results are cached for 60 seconds. Individual provider API failures
    are logged and skipped — one broken provider does not block others.
    """
    global _discovered_provider_models_cache, _discovered_provider_models_time
    now = time.time()
    if (
        now - _discovered_provider_models_time < 60
        and _discovered_provider_models_cache
    ):
        return _discovered_provider_models_cache

    discovered: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=5) as client:
        for provider_id, url in PROVIDER_MODEL_ENDPOINTS.items():
            api_key = _get_api_key(provider_id)
            if not api_key:
                continue  # Provider is not configured — skip

            headers, params = _get_provider_auth(provider_id, api_key)

            try:
                resp = await client.get(url, headers=headers, params=params)
                if resp.status_code != 200:
                    logger.warning(
                        "Model discovery for '%s' returned %d (expected 200)",
                        provider_id,
                        resp.status_code,
                    )
                    continue

                data = resp.json()
                raw_models = _parse_provider_models(provider_id, data)

                for m in raw_models:
                    discovered.append(
                        {
                            "id": f"{provider_id}/{m['id']}",
                            "provider": provider_id,
                            "display_name": m.get("display_name", m["id"]),
                            "default": False,
                        }
                    )
            except httpx.TimeoutException:
                logger.warning("Model discovery timed out for '%s' (5s)", provider_id)
            except Exception as e:
                logger.warning("Model discovery failed for '%s': %s", provider_id, e)

    # ── Opencode AI — Zen has model listing, Go has its own listing ──────
    opencode_key = _get_api_key("opencode")
    if opencode_key:
        # Zen: fetch models from API
        try:
            async with httpx.AsyncClient(timeout=10) as oc_client:
                resp = await oc_client.get(
                    "https://opencode.ai/zen/v1/models",
                    headers={"Authorization": f"Bearer {opencode_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("data", []):
                        mid = m["id"]
                        discovered.append(
                            {
                                "id": f"opencode/zen/{mid}",
                                "provider": "opencode",
                                "display_name": mid,
                                "default": False,
                                "endpoint": "zen",
                            }
                        )
        except Exception as e:
            logger.warning("Failed to discover Opencode Zen models: %s", e)

        # Go: fetch models from its own API (not hardcoded!)
        try:
            async with httpx.AsyncClient(timeout=10) as oc_client2:
                resp = await oc_client2.get(
                    "https://opencode.ai/zen/go/v1/models",
                    headers={"Authorization": f"Bearer {opencode_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("data", []):
                        mid = m["id"]
                        discovered.append(
                            {
                                "id": f"opencode/go/{mid}",
                                "provider": "opencode",
                                "display_name": mid,
                                "default": False,
                                "endpoint": "go",
                            }
                        )
        except Exception as e:
            logger.warning("Failed to discover Opencode Go models: %s", e)

    _discovered_provider_models_cache = discovered
    _discovered_provider_models_time = now
    return discovered


# ── Settings Endpoints ─────────────────────────────────────────────────


@app.get("/api/settings/keys")
async def get_settings_keys() -> JSONResponse:
    """Return all configured providers and their status."""
    return JSONResponse(settings_manager.get_keys())


class SetKeyRequest(BaseModel):
    """Request body for POST /api/settings/keys."""

    provider: str
    key: str


@app.post("/api/settings/keys")
async def set_settings_key(req: SetKeyRequest) -> JSONResponse:
    """Save an API key for a provider."""
    try:
        settings_manager.set_key(req.provider, req.key)
        return JSONResponse({"status": "ok", "provider": req.provider})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.delete("/api/settings/keys/{provider}")
async def delete_settings_key(provider: str) -> JSONResponse:
    """Remove an API key for a provider."""
    settings_manager.delete_key(provider)
    return JSONResponse({"status": "ok", "provider": provider})


# ── Local Model Endpoints ──────────────────────────────────────────────


@app.get("/api/settings/local-models")
async def get_local_models() -> JSONResponse:
    """Discover and return available local model endpoints."""
    results: list[dict[str, Any]] = []

    # Try Ollama auto-discovery.
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code == 200:
                data = resp.json()
                for model in data.get("models", []):
                    results.append(
                        {
                            "source": "ollama",
                            "name": model["name"],
                            "provider": "ollama",
                            "endpoint": "http://localhost:11434",
                            "size": model.get("size", 0),
                        }
                    )
    except Exception:
        pass  # Ollama not running

    # Add configured endpoints from settings.
    saved = settings_manager.get_local_endpoints()
    results.extend(saved)

    return JSONResponse(results)


class AddEndpointRequest(BaseModel):
    """Request body for POST /api/settings/local-endpoints."""

    name: str
    endpoint: str
    type: str = "openai"  # ollama, llamacpp, vllm, openai


@app.post("/api/settings/local-endpoints")
async def add_local_endpoint(req: AddEndpointRequest) -> JSONResponse:
    """Add a custom local endpoint (llama.cpp, vLLM, etc.)."""
    settings_manager.add_local_endpoint(
        {
            "name": req.name,
            "endpoint": req.endpoint,
            "type": req.type,
        }
    )
    return JSONResponse({"status": "ok"})


@app.delete("/api/settings/local-endpoints/{name}")
async def remove_local_endpoint(name: str) -> JSONResponse:
    """Remove a saved local endpoint by name."""
    settings_manager.remove_local_endpoint(name)
    return JSONResponse({"status": "ok", "name": name})


@app.post("/api/settings/local-endpoints/{name}/test")
async def test_local_endpoint(name: str) -> JSONResponse:
    """Test connectivity to a local endpoint."""
    endpoints = settings_manager.get_local_endpoints()
    ep = next((e for e in endpoints if e.get("name") == name), None)
    if ep is None:
        return JSONResponse({"error": f"Endpoint '{name}' not found"}, status_code=404)
    try:
        endpoint_url = ep["endpoint"].rstrip("/")
        # Try a simple health/model check.
        test_url = f"{endpoint_url}/models"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(test_url)
            if resp.status_code == 200:
                return JSONResponse(
                    {"status": "ok", "message": f"Connected to {endpoint_url}"}
                )
            return JSONResponse(
                {"status": "error", "message": f"Unexpected status {resp.status_code}"}
            )
    except httpx.ConnectError:
        return JSONResponse(
            {"status": "error", "message": f"Could not connect to {ep['endpoint']}"}
        )
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


# ── Scribe Model Setting ────────────────────────────────────────────────


@app.get("/api/settings/scribe-model")
async def get_scribe_model() -> JSONResponse:
    """Get the saved scribe model ID."""
    model = settings_manager.get_scribe_model()
    return JSONResponse({"scribe_model": model})


class ScribeModelRequest(BaseModel):
    """Request body for POST /api/settings/scribe-model."""

    scribe_model: str


@app.post("/api/settings/scribe-model")
async def set_scribe_model(req: ScribeModelRequest) -> JSONResponse:
    """Save the scribe model ID."""
    settings_manager.set_scribe_model(req.scribe_model)
    logger.info("Scribe model set to: %s", req.scribe_model)
    return JSONResponse({"status": "ok", "scribe_model": req.scribe_model})


@app.delete("/api/settings/scribe-model")
async def delete_scribe_model() -> JSONResponse:
    """Remove the scribe model setting."""
    settings_manager.delete_scribe_model()
    return JSONResponse({"status": "ok"})


# ── Max Tokens per Agent Call ─────────────────────────────────────────


@app.get("/api/settings/max-tokens")
async def get_max_tokens() -> JSONResponse:
    """Get the configured max tokens per agent call."""
    value = settings_manager.get_max_tokens()
    return JSONResponse({"max_tokens": value})


class MaxTokensRequest(BaseModel):
    """Request body for POST /api/settings/max-tokens."""

    max_tokens: int


@app.post("/api/settings/max-tokens")
async def set_max_tokens(req: MaxTokensRequest) -> JSONResponse:
    """Save the max tokens per agent call setting."""
    try:
        settings_manager.set_max_tokens(req.max_tokens)
        logger.info("Max tokens set to %d", req.max_tokens)
        return JSONResponse({"status": "ok", "max_tokens": req.max_tokens})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# ── Context Window Overrides ───────────────────────────────────────────


class ContextWindowRequest(BaseModel):
    """Request body for POST /api/config/context."""

    model_id: str
    context_window: int


@app.get("/api/config/context")
async def get_context_windows() -> JSONResponse:
    """Return all context window overrides."""
    return JSONResponse(context_window_manager.get_overrides())


@app.post("/api/config/context")
async def set_context_window(req: ContextWindowRequest) -> JSONResponse:
    """Set a context window override for a model."""
    if req.context_window < 1:
        return JSONResponse({"error": "context_window must be >= 1"}, status_code=400)
    context_window_manager.set_override(req.model_id, req.context_window)
    return JSONResponse(
        {"status": "ok", "model_id": req.model_id, "context_window": req.context_window}
    )


@app.delete("/api/config/context/{model_id:path}")
async def delete_context_window(model_id: str) -> JSONResponse:
    """Remove a context window override for a model."""
    removed = context_window_manager.delete_override(model_id)
    if removed:
        return JSONResponse({"status": "ok", "model_id": model_id})
    return JSONResponse(
        {"error": f"No override found for '{model_id}'"}, status_code=404
    )


# ── System Log Buffer ──────────────────────────────────────────────────
# In-memory log buffer that captures log records from the deepresearch
# logger tree. Accessible via the dashboard's System Log tab.

SYSTEM_LOG: list[dict[str, Any]] = []
MAX_LOG_ENTRIES = 500


class SystemLogHandler(logging.Handler):
    """Custom logging handler that captures log records into SYSTEM_LOG."""

    def emit(self, record: logging.LogRecord) -> None:
        """Append a log entry to the in-memory buffer."""
        try:
            entry: dict[str, Any] = {
                "timestamp": datetime.now().isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            SYSTEM_LOG.append(entry)
            # Trim to max size
            if len(SYSTEM_LOG) > MAX_LOG_ENTRIES:
                SYSTEM_LOG[:] = SYSTEM_LOG[-MAX_LOG_ENTRIES:]
        except Exception:
            pass


# Install the handler on the root deepresearch logger so it captures
# all child loggers (deepresearch.web.server, deepresearch.llm.client, etc.)
_deepresearch_logger = logging.getLogger("deepresearch")
_system_log_handler = SystemLogHandler()
_system_log_handler.setLevel(logging.INFO)
_deepresearch_logger.addHandler(_system_log_handler)
_deepresearch_logger.setLevel(logging.DEBUG)

logger.info("System log initialized — up to %d entries", MAX_LOG_ENTRIES)


@app.get("/api/system/log")
async def get_system_log(limit: int = 200, level: str = "") -> JSONResponse:
    """Return recent system log entries, newest first.

    Args:
        limit: Max entries to return (default 200).
        level: Optional filter — "ERROR", "WARNING", "INFO", "DEBUG".
    """
    entries = list(reversed(SYSTEM_LOG))  # newest first
    if level:
        entries = [e for e in entries if e["level"] == level.upper()]
    return JSONResponse(entries[:limit])


@app.post("/api/system/log/clear")
async def clear_system_log() -> JSONResponse:
    """Clear all system log entries."""
    SYSTEM_LOG.clear()
    return JSONResponse({"status": "ok"})


@app.get("/api/system/concurrency")
async def get_concurrency_status() -> JSONResponse:
    """Return current concurrency state for sessions and web searches."""
    from deepresearch.tools.web_search import get_search_semaphore_info

    active_sessions = multi_session_manager.active_count
    search_info = get_search_semaphore_info()
    return JSONResponse(
        {
            "active_sessions": active_sessions,
            "max_concurrent": MAX_CONCURRENT_SESSIONS,
            "active_searches": search_info["active_searches"],
            "max_searches": search_info["max_searches"],
        }
    )


# ── Search Engine Endpoints ──────────────────────────────────────────


@app.get("/api/system/search")
async def get_search_status() -> JSONResponse:
    """Return search engine configuration, health, and cache stats."""
    from deepresearch.tools.web_search import get_search_health_info

    return JSONResponse(get_search_health_info())


class SearchTestRequest(BaseModel):
    """Request body for POST /api/system/search/test."""

    query: str = "test search"


@app.post("/api/system/search/test")
async def test_search_engine(req: SearchTestRequest | None = None) -> JSONResponse:
    """Probe SearXNG with a test query and return latency + result count."""
    from deepresearch.tools.web_search import _searxng_url, _searxng_timeout

    query = req.query if req else "test search"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_searxng_timeout) as client:
            resp = await client.get(
                f"{_searxng_url}/search",
                params={"q": query, "format": "json", "categories": "general"},
            )
            resp.raise_for_status()
            data = resp.json()
            latency = (time.monotonic() - t0) * 1000
            result_count = len(data.get("results", []))
            return JSONResponse(
                {
                    "status": "ok",
                    "results_count": result_count,
                    "latency_ms": round(latency, 1),
                    "engine_url": _searxng_url,
                }
            )
    except httpx.ConnectError:
        latency = (time.monotonic() - t0) * 1000
        return JSONResponse(
            {
                "status": "error",
                "message": f"Could not connect to SearXNG at {_searxng_url}",
                "latency_ms": round(latency, 1),
                "engine_url": _searxng_url,
            },
            status_code=502,
        )
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return JSONResponse(
            {
                "status": "error",
                "message": str(e),
                "latency_ms": round(latency, 1),
                "engine_url": _searxng_url,
            },
            status_code=500,
        )


# ── Tools / hardware (llmfit) ───────────────────────────────────────────


@app.get("/api/tools/status")
async def get_tools_status() -> JSONResponse:
    """Check if llmfit is installed and return version."""
    import shutil
    import subprocess

    result: dict[str, dict[str, bool | str]] = {"llmfit": {"installed": False}}
    if shutil.which("llmfit"):
        result["llmfit"]["installed"] = True  # type: ignore[assignment]
        try:
            version = subprocess.run(
                ["llmfit", "--version"], capture_output=True, text=True, timeout=5
            )
            result["llmfit"]["version"] = (
                version.stdout.strip() or version.stderr.strip()
            )
        except Exception:
            result["llmfit"]["version"] = "unknown"
    return JSONResponse(result)


@app.get("/api/hardware")
async def get_hardware_info() -> JSONResponse:
    """Return hardware specs via llmfit system --json (if installed)."""
    import shutil
    import subprocess

    if not shutil.which("llmfit"):
        return JSONResponse({"available": False, "message": "llmfit not installed"})
    try:
        result = subprocess.run(
            ["llmfit", "system", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return JSONResponse({"available": True, "hardware": data.get("system", {})})
        return JSONResponse({"available": False, "error": result.stderr.strip()})
    except FileNotFoundError:
        return JSONResponse({"available": False, "message": "llmfit not found"})
    except subprocess.TimeoutExpired:
        return JSONResponse({"available": False, "message": "llmfit timed out"})


@app.get("/api/tools/recommendations")
async def get_model_recommendations() -> JSONResponse:
    """Return model recommendations via llmfit recommend --json (if installed).
    Filters models based on available hardware (RAM/VRAM)."""
    import shutil
    import subprocess

    if not shutil.which("llmfit"):
        return JSONResponse({"available": False, "message": "llmfit not installed"})
    try:
        result = subprocess.run(
            ["llmfit", "recommend", "-n", "20", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            models = data.get("models", [])
            models.sort(key=lambda m: m.get("score", 0), reverse=True)

            # Get hardware info for filtering
            hw_info = {}
            try:
                hw_result = subprocess.run(
                    ["llmfit", "system", "--json"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if hw_result.returncode == 0:
                    hw_data = json.loads(hw_result.stdout)
                    sys_info = hw_data.get("system", {})
                    hw_info = {
                        "total_ram_gb": sys_info.get("total_ram_gb", 0) or 0,
                        "gpu_vram_gb": sys_info.get("gpu_vram_gb", 0) or 0,
                    }
            except Exception:
                pass

            # Max usable memory for a model (leave headroom for OS)
            if hw_info:
                usable_memory_gb = max(
                    hw_info.get("gpu_vram_gb", 0),
                    hw_info.get("total_ram_gb", 0) * 0.7,
                )
                if usable_memory_gb <= 0:
                    usable_memory_gb = float("inf")
            else:
                usable_memory_gb = float("inf")

            # Calculate research-optimized score for each model
            # Weights: tool_use capability (+30), context length >= 32K (+20),
            # speed > 20 tok/s (+10), ideal fit (+10), good fit (+5)
            for m in models[:40]:
                rscore = 0
                tags = []
                # tool_use capability — critical for web search
                if "tool_use" in (m.get("capability_ids") or []):
                    rscore += 30
                    tags.append("tool_use")
                # Context length — need at least 32K for research
                ctx = m.get("effective_context_length", 0) or 0
                if ctx >= 128000:
                    rscore += 25
                    tags.append("128K ctx")
                elif ctx >= 32000:
                    rscore += 20
                    tags.append("32K ctx")
                elif ctx >= 16000:
                    rscore += 10
                    tags.append("16K ctx")
                # Speed — fast models preferred
                tps = m.get("estimated_tps", 0) or 0
                if tps > 100:
                    rscore += 15
                    tags.append("fast")
                elif tps > 40:
                    rscore += 10
                elif tps > 20:
                    rscore += 5
                # Fit level bonus
                fit = m.get("fit_level", "")
                if fit == "ideal":
                    rscore += 10
                elif fit == "good":
                    rscore += 5
                m["research_score"] = rscore
                m["research_tags"] = tags

            # Sort by research_score (primary), then llmfit score (tiebreaker)
            models.sort(key=lambda m: (m.get("research_score", 0), m.get("score", 0)), reverse=True)

            # Filter and annotate models
            filtered_models = []
            for m in models[:40]:
                required_ram = m.get("memory_required_gb") or m.get("required_ram_gb") or m.get("min_ram_gb") or 0
                if hw_info and required_ram > usable_memory_gb:
                    m["_warning"] = (
                        f"Requires {required_ram}GB RAM, "
                        f"only {usable_memory_gb:.0f}GB available"
                    )
                # Annotate with capability info for frontend filtering
                m["supports_tool_use"] = "tool_use" in (m.get("capability_ids") or [])
                # MoE detection: if total_memory_gb >> memory_required_gb, it's a MoE model
                mem_req = m.get("memory_required_gb", 0) or 0
                mem_total = m.get("total_memory_gb", 0) or 0
                if mem_req > 0 and mem_total > mem_req * 2:
                    m["_moe_annotation"] = (
                        f"MoE: {mem_req:.1f} GB VRAM + {mem_total - mem_req:.0f} GB system RAM"
                    )
                filtered_models.append(m)

            return JSONResponse(
                {
                    "available": True,
                    "models": filtered_models[:15],
                    "system": data.get("system", {}),
                    "hardware": hw_info if hw_info else None,
                }
            )
        return JSONResponse({"available": False, "error": result.stderr.strip()})
    except FileNotFoundError:
        return JSONResponse({"available": False, "message": "llmfit not found"})
    except subprocess.TimeoutExpired:
        return JSONResponse({"available": False, "message": "llmfit timed out"})


# ── Local Backend Discovery ────────────────────────────


class BackendAddressRequest(BaseModel):
    """Request body for setting a custom backend address."""

    address: str  # e.g. "localhost:11434" or "192.168.1.100:8080"


BACKEND_DEFINITIONS = [
    {
        "name": "ollama",
        "label": "Ollama",
        "description": "Easy-to-use local LLM runner",
        "port": 11434,
        "path": "/api/tags",
        "binary": "ollama",
    },
    {
        "name": "llama-cpp",
        "label": "llama.cpp",
        "description": "Lightweight CPU/GPU inference",
        "port": 8080,
        "path": "/v1/models",
        "binary": None,
    },
    {
        "name": "vllm",
        "label": "vLLM",
        "description": "High-throughput GPU inference",
        "port": 8000,
        "path": "/v1/models",
        "binary": None,
    },
    {
        "name": "lm-studio",
        "label": "LM Studio",
        "description": "Desktop app for local models",
        "port": 1234,
        "path": "/v1/models",
        "binary": None,
    },
    {
        "name": "local-ai",
        "label": "LocalAI",
        "description": "OpenAI-compatible local API",
        "port": 8080,
        "path": "/readyz",
        "binary": None,
    },
]


async def _probe_backend(defn: dict) -> dict:
    """Probe a single local backend for installation and running status."""
    import subprocess
    import shutil

    name: str = defn["name"]
    label: str = defn["label"]
    description: str = defn["description"]
    port: int = defn["port"]
    path: str = defn["path"]
    binary: str | None = defn.get("binary")

    installed: bool | None = None
    version: str | None = None
    running: bool = False

    if binary:
        installed = shutil.which(binary) is not None
        if installed:
            try:
                result = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True, timeout=5
                )
                version = result.stdout.strip() or result.stderr.strip() or None
            except Exception:
                version = None
    else:
        installed = None

    # Probe the port
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(f"http://localhost:{port}{path}")
            running = resp.status_code == 200
    except Exception:
        running = False

    return {
        "name": name,
        "installed": installed,
        "running": running,
        "port": port,
        "version": version,
        "label": label,
        "description": description,
    }


@app.get("/api/local-backends")
async def list_local_backends() -> JSONResponse:
    """Return status for all known local backends, probed concurrently.

    Respects custom address overrides from local_backend_manager.
    """

    async def _probe_with_custom(defn: dict) -> dict:
        name = defn["name"]
        custom = local_backend_manager.get_address(name)
        if custom is not None:
            parts = custom.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                custom_defn = dict(defn)
                custom_defn["port"] = int(parts[1])
                result = await _probe_backend(custom_defn)
                result["custom_address"] = custom
                return result
        result = await _probe_backend(defn)
        result["custom_address"] = None
        return result

    results = await asyncio.gather(
        *(_probe_with_custom(defn) for defn in BACKEND_DEFINITIONS)
    )
    return JSONResponse({"backends": results})


@app.post("/api/local-backends/{name}/test")
async def test_local_backend(name: str) -> JSONResponse:
    """Test connectivity to a specific local backend."""
    defn = next((d for d in BACKEND_DEFINITIONS if d["name"] == name), None)
    if defn is None:
        return JSONResponse(
            {"status": "error", "message": f"Unknown backend: {name}"},
            status_code=404,
        )

    port = defn["port"]
    path = defn["path"]
    check_url = f"http://localhost:{port}{path}"

    # Check for custom address override
    custom = local_backend_manager.get_address(name)
    if custom is not None:
        parts = custom.rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdigit():
            port = int(parts[1])
            check_url = f"http://{custom}{path}"

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(check_url)
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            running = resp.status_code == 200
            return JSONResponse(
                {
                    "status": "ok",
                    "running": running,
                    "port": port,
                    "latency_ms": latency_ms,
                }
            )
    except httpx.ConnectError:
        return JSONResponse(
            {
                "status": "error",
                "running": False,
                "message": f"Connection refused on port {port}",
            }
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {
                "status": "error",
                "running": False,
                "message": f"Connection timed out on port {port}",
            }
        )


@app.put("/api/local-backends/{name}/address")
async def set_backend_address(name: str, req: BackendAddressRequest) -> JSONResponse:
    """Set a custom address override for a local backend."""
    defn = next((d for d in BACKEND_DEFINITIONS if d["name"] == name), None)
    if defn is None:
        return JSONResponse(
            {"status": "error", "message": f"Unknown backend: {name}"},
            status_code=404,
        )

    # Validate address format: host:port
    if not re.match(r"^[a-zA-Z0-9.-]+:\d+$", req.address):
        return JSONResponse(
            {
                "status": "error",
                "message": f"Invalid address format: {req.address}. Expected host:port",
            },
            status_code=400,
        )

    local_backend_manager.set_address(name, req.address)
    return JSONResponse({"status": "ok", "name": name, "address": req.address})


@app.get("/api/local-backends/{name}/address")
async def get_backend_address(name: str) -> JSONResponse:
    """Get the custom address override for a local backend."""
    defn = next((d for d in BACKEND_DEFINITIONS if d["name"] == name), None)
    if defn is None:
        return JSONResponse(
            {"status": "error", "message": f"Unknown backend: {name}"},
            status_code=404,
        )

    addr = local_backend_manager.get_address(name)
    return JSONResponse(
        {
            "status": "ok",
            "name": name,
            "address": addr,
        }
    )


# ── Local Backend Installation (Ollama) ────────────────────────────────


@app.get("/api/local-backends/ollama/status")
async def get_ollama_status() -> JSONResponse:
    """Check if Ollama is installed and running."""
    import shutil
    import subprocess

    installed = shutil.which("ollama") is not None
    version = None
    running = False

    if installed:
        try:
            result = subprocess.run(
                ["ollama", "--version"], capture_output=True, text=True, timeout=5
            )
            version = result.stdout.strip() or result.stderr.strip()
        except Exception:
            version = "unknown"

    # Check if running by probing localhost:11434
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            running = resp.status_code == 200
    except Exception:
        running = False

    return JSONResponse(
        {
            "installed": installed,
            "running": running,
            "version": version,
        }
    )


@app.post("/api/local-backends/ollama/install")
async def install_ollama(request: Request) -> EventSourceResponse:
    """Install Ollama via curl|sh with live SSE log streaming."""
    import shutil

    # Pre-check: already installed?
    if shutil.which("ollama"):
        return EventSourceResponse(
            _install_error_generator("Ollama is already installed")
        )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "download",
                        "message": "Downloading Ollama install script...",
                        "progress": 10,
                    }
                ),
            }

            # Run the install script
            process = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "curl -fsSL https://ollama.com/install.sh | sh",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Stream output line by line
            assert process.stdout is not None
            line_count = 0
            async for line in process.stdout:
                line_str = line.decode("utf-8", errors="replace").rstrip()
                if not line_str:
                    continue
                line_count += 1
                # Parse progress from output (simple heuristic)
                progress = min(30 + line_count * 3, 90)
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {
                            "step": "install" if line_count > 1 else "download",
                            "message": line_str,
                            "progress": progress,
                        }
                    ),
                }

                if await request.is_disconnected():
                    process.terminate()
                    return

            await process.wait()

            if process.returncode == 0:
                # Verify installation
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {
                            "step": "verify",
                            "message": "Verifying installation...",
                            "progress": 95,
                        }
                    ),
                }

                import subprocess

                version = "unknown"
                try:
                    result = subprocess.run(
                        ["ollama", "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    version = result.stdout.strip() or result.stderr.strip()
                except Exception:
                    pass

                yield {
                    "event": "install_complete",
                    "data": json.dumps(
                        {
                            "status": "success",
                            "version": version,
                            "path": shutil.which("ollama") or "/usr/local/bin/ollama",
                        }
                    ),
                }
            else:
                yield {
                    "event": "install_error",
                    "data": json.dumps(
                        {
                            "status": "error",
                            "message": f"Installation failed with exit code {process.returncode}",
                            "code": "INSTALL_FAILED",
                        }
                    ),
                }
        except Exception as e:
            yield {
                "event": "install_error",
                "data": json.dumps(
                    {
                        "status": "error",
                        "message": str(e),
                        "code": "UNEXPECTED_ERROR",
                    }
                ),
            }

    return EventSourceResponse(generate())


# ── Local Backend Management ──────────────────────────────────────────


class PullModelRequest(BaseModel):
    model: str


class DownloadModelRequest(BaseModel):
    name: str
    download_type: str = "auto"  # "ollama", "llmfit", "auto"
    repo: str | None = None  # HuggingFace repo from gguf_sources
    quant: str | None = None  # Specific quantization (e.g. "Q4_K_M")


@app.post("/api/local-backends/llmfit/install")
async def install_llmfit(request: Request) -> EventSourceResponse:
    """Install llmfit via curl|sh with live SSE log streaming."""
    import shutil

    # Pre-check: already installed?
    if shutil.which("llmfit"):
        return EventSourceResponse(
            _install_error_generator("llmfit is already installed")
        )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "download",
                        "message": "Downloading llmfit install script...",
                        "progress": 10,
                    }
                ),
            }

            # Run the install script
            process = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "curl -fsSL https://llmfit.axjns.dev/install.sh | sh -s -- --local",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            # Stream output line by line
            assert process.stdout is not None
            line_count = 0
            async for line in process.stdout:
                line_str = line.decode("utf-8", errors="replace").rstrip()
                if not line_str:
                    continue
                line_count += 1
                progress = min(30 + line_count * 3, 90)
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {
                            "step": "install" if line_count > 1 else "download",
                            "message": line_str,
                            "progress": progress,
                        }
                    ),
                }

                if await request.is_disconnected():
                    process.terminate()
                    return

            await process.wait()

            if process.returncode == 0:
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {
                            "step": "verify",
                            "message": "Verifying installation...",
                            "progress": 95,
                        }
                    ),
                }

                import subprocess

                version = "unknown"
                try:
                    result = subprocess.run(
                        ["llmfit", "--version"],
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    version = result.stdout.strip() or result.stderr.strip()
                except Exception:
                    pass

                yield {
                    "event": "install_complete",
                    "data": json.dumps(
                        {
                            "status": "success",
                            "version": version,
                            "path": shutil.which("llmfit") or "~/.local/bin/llmfit",
                        }
                    ),
                }
            else:
                yield {
                    "event": "install_error",
                    "data": json.dumps(
                        {
                            "status": "error",
                            "message": f"Installation failed with exit code {process.returncode}",
                            "code": "INSTALL_FAILED",
                        }
                    ),
                }
        except Exception as e:
            yield {
                "event": "install_error",
                "data": json.dumps(
                    {
                        "status": "error",
                        "message": str(e),
                        "code": "UNEXPECTED_ERROR",
                    }
                ),
            }

    return EventSourceResponse(generate())


@app.post("/api/local-backends/llmfit/uninstall")
async def uninstall_llmfit() -> JSONResponse:
    """Uninstall llmfit by removing the binary."""
    import shutil
    import os

    path = shutil.which("llmfit")
    if path:
        try:
            os.remove(path)
        except Exception as e:
            return JSONResponse(
                {"status": "error", "message": f"Failed to remove {path}: {e}"},
                status_code=500,
            )

    # Also check common locations
    home = os.path.expanduser("~")
    for loc in [os.path.join(home, ".local", "bin", "llmfit"), "/usr/local/bin/llmfit"]:
        if os.path.exists(loc):
            try:
                os.remove(loc)
            except Exception:
                pass

    if shutil.which("llmfit"):
        return JSONResponse(
            {"status": "error", "message": "llmfit still found after removal attempt"},
            status_code=500,
        )

    return JSONResponse({"status": "ok", "message": "llmfit uninstalled"})


@app.post("/api/local-backends/ollama/start")
async def start_ollama() -> JSONResponse:
    """Start Ollama service."""
    import shutil
    import subprocess

    if not shutil.which("ollama"):
        return JSONResponse(
            {"status": "error", "message": "Ollama is not installed"},
            status_code=400,
        )

    # Try systemctl --user first, fall back to direct serve
    try:
        subprocess.run(
            ["systemctl", "--user", "start", "ollama"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return JSONResponse({"status": "ok", "message": "Ollama started"})
    except Exception:
        pass

    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return JSONResponse({"status": "ok", "message": "Ollama started"})
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": f"Failed to start Ollama: {e}"},
            status_code=500,
        )


@app.post("/api/local-backends/ollama/stop")
async def stop_ollama() -> JSONResponse:
    """Stop Ollama service."""
    import subprocess

    # Try systemctl --user first, fall back to pkill
    try:
        subprocess.run(
            ["systemctl", "--user", "stop", "ollama"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return JSONResponse({"status": "ok", "message": "Ollama stopped"})
    except Exception:
        pass

    try:
        subprocess.run(
            ["pkill", "ollama"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return JSONResponse({"status": "ok", "message": "Ollama stopped"})
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": f"Failed to stop Ollama: {e}"},
            status_code=500,
        )


@app.post("/api/local-backends/ollama/uninstall")
async def uninstall_ollama(request: Request) -> EventSourceResponse:
    """Uninstall Ollama with live SSE log streaming (dangerous)."""
    import shutil

    # Pre-check: installed?
    if not shutil.which("ollama"):
        return EventSourceResponse(
            _install_error_generator("Ollama is not installed", "NOT_INSTALLED")
        )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "stop",
                        "message": "Stopping Ollama...",
                        "progress": 10,
                    }
                ),
            }

            # Step 1: Stop Ollama
            stop_proc = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "systemctl --user stop ollama 2>/dev/null; pkill ollama 2>/dev/null",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert stop_proc.stdout is not None
            async for line in stop_proc.stdout:
                if await request.is_disconnected():
                    stop_proc.terminate()
                    return
            await stop_proc.wait()

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "remove_binary",
                        "message": "Removing Ollama binary...",
                        "progress": 30,
                    }
                ),
            }

            # Step 2: Remove binary
            which_proc = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "which ollama",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert which_proc.stdout is not None
            ollama_paths = []
            async for line in which_proc.stdout:
                p = line.decode("utf-8", errors="replace").strip()
                if p:
                    ollama_paths.append(p)
            await which_proc.wait()

            import os as _os

            for p in ollama_paths:
                try:
                    _os.remove(p)
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "remove_binary",
                                "message": f"Removed {p}",
                                "progress": 35,
                            }
                        ),
                    }
                except Exception as ex:
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "remove_binary",
                                "message": f"Could not remove {p}: {ex}",
                                "progress": 35,
                            }
                        ),
                    }

            # Also try common locations
            for p in ["/usr/local/bin/ollama", "/usr/bin/ollama"]:
                if _os.path.exists(p):
                    try:
                        _os.remove(p)
                        yield {
                            "event": "install_log",
                            "data": json.dumps(
                                {
                                    "step": "remove_binary",
                                    "message": f"Removed {p}",
                                    "progress": 38,
                                }
                            ),
                        }
                    except Exception:
                        pass

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "remove_service",
                        "message": "Removing systemd service files...",
                        "progress": 45,
                    }
                ),
            }

            # Step 3: Remove systemd service
            rm_service_proc = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "rm -f /etc/systemd/system/ollama.service "
                "/usr/lib/systemd/system/ollama.service "
                "$HOME/.config/systemd/user/ollama.service",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert rm_service_proc.stdout is not None
            async for line in rm_service_proc.stdout:
                pass
            await rm_service_proc.wait()

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "remove_data",
                        "message": "Removing data directories...",
                        "progress": 60,
                    }
                ),
            }

            # Step 4: Remove data directories
            rm_data_proc = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "rm -rf ~/.ollama /usr/share/ollama /usr/local/share/ollama",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert rm_data_proc.stdout is not None
            async for line in rm_data_proc.stdout:
                pass
            await rm_data_proc.wait()

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "remove_user",
                        "message": "Removing ollama user (if exists)...",
                        "progress": 80,
                    }
                ),
            }

            # Step 5: Remove user (optional)
            rm_user_proc = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "userdel ollama 2>/dev/null",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert rm_user_proc.stdout is not None
            async for line in rm_user_proc.stdout:
                pass
            await rm_user_proc.wait()

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "verify",
                        "message": "Verifying uninstallation...",
                        "progress": 95,
                    }
                ),
            }

            # Step 6: Verify
            if shutil.which("ollama") is not None:
                yield {
                    "event": "install_error",
                    "data": json.dumps(
                        {
                            "status": "error",
                            "message": "Ollama binary still found after removal",
                            "code": "REMOVAL_FAILED",
                        }
                    ),
                }
                return

            yield {
                "event": "install_complete",
                "data": json.dumps(
                    {
                        "status": "success",
                        "message": "Ollama has been fully uninstalled",
                    }
                ),
            }

        except Exception as e:
            yield {
                "event": "install_error",
                "data": json.dumps(
                    {
                        "status": "error",
                        "message": str(e),
                        "code": "UNEXPECTED_ERROR",
                    }
                ),
            }

    return EventSourceResponse(generate())


@app.post("/api/local-backends/ollama/pull")
async def pull_ollama_model(
    req: PullModelRequest, request: Request
) -> EventSourceResponse:
    """Pull an Ollama model via 'ollama pull' with SSE log streaming."""
    import shutil

    # Pre-check: Ollama installed
    if not shutil.which("ollama"):
        return EventSourceResponse(
            _install_error_generator("Ollama is not installed", "NOT_INSTALLED")
        )

    # Pre-check: Ollama running
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                raise RuntimeError("Ollama not responding")
    except Exception:
        return EventSourceResponse(
            _install_error_generator(
                "Ollama is not running. Start it first.", "NOT_RUNNING"
            )
        )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "pull",
                        "message": f"Pulling model {req.model}. This may take a while...",
                        "progress": 5,
                    }
                ),
            }

            process = await asyncio.create_subprocess_exec(
                "ollama",
                "pull",
                req.model,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

            assert process.stdout is not None
            line_count = 0
            async for line in process.stdout:
                line_str = line.decode("utf-8", errors="replace").rstrip()
                if not line_str:
                    continue
                # Detect model-not-found errors (HF repo IDs in Ollama registry)
                if "file does not exist" in line_str.lower():
                    yield {
                        "event": "install_error",
                        "data": json.dumps({
                            "status": "error",
                            "message": f"Model '{req.model}' not found in Ollama library. "
                                       f"Ollama's registry is separate from HuggingFace. "
                                       f"Check available models at https://ollama.com/library",
                            "code": "MODEL_NOT_FOUND",
                        }),
                    }
                    process.terminate()
                    return
                line_count += 1
                progress = min(10 + line_count * 2, 95)
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {
                            "step": "pull",
                            "message": line_str,
                            "progress": progress,
                        }
                    ),
                }

                if await request.is_disconnected():
                    process.terminate()
                    return

            await process.wait()

            if process.returncode == 0:
                yield {
                    "event": "install_complete",
                    "data": json.dumps(
                        {
                            "status": "success",
                            "model": req.model,
                        }
                    ),
                }
            else:
                yield {
                    "event": "install_error",
                    "data": json.dumps(
                        {
                            "status": "error",
                            "message": f"Pull failed with exit code {process.returncode}",
                            "code": "PULL_FAILED",
                        }
                    ),
                }
        except Exception as e:
            yield {
                "event": "install_error",
                "data": json.dumps(
                    {
                        "status": "error",
                        "message": str(e),
                        "code": "UNEXPECTED_ERROR",
                    }
                ),
            }

    return EventSourceResponse(generate())


@app.get("/api/local-backends/models/download/progress")
async def get_download_progress() -> JSONResponse:
    """Return current download state (survives page refresh)."""
    return JSONResponse(_download_state)


@app.post("/api/local-backends/models/download")
async def download_model(
    req: DownloadModelRequest, request: Request
) -> EventSourceResponse:
    """Smart model download via Ollama pull or llmfit download with SSE log streaming."""
    import shutil

    async def generate() -> AsyncGenerator[str, None]:
        # ── Persistent download state ──
        _download_state["active"] = True
        _download_state["model"] = req.name
        _download_state["status"] = "downloading"
        _download_state["progress"] = 0
        _download_state["message"] = "Starting download..."
        _download_state["log"] = []

        def _update_state(progress: float, message: str) -> None:
            _download_state["progress"] = progress
            _download_state["message"] = message
            _download_state["log"].append(message)
            if len(_download_state["log"]) > 50:
                _download_state["log"] = _download_state["log"][-50:]

        try:
            # Decide download method
            use_ollama = False
            use_llmfit = False

            if req.download_type == "ollama":
                use_ollama = True
            elif req.download_type == "llmfit":
                use_llmfit = True
            else:  # "auto"
                # Check if Ollama is available and has the model
                if shutil.which("ollama"):
                    use_ollama = True
                elif req.repo:
                    use_llmfit = True
                else:
                    use_ollama = shutil.which("ollama") is not None

            # ── Ollama path ──────────────────────────────────────────────
            if use_ollama:
                # Pre-check: Ollama running
                try:
                    async with httpx.AsyncClient(timeout=2) as client:
                        resp = await client.get("http://localhost:11434/api/tags")
                        if resp.status_code != 200:
                            raise RuntimeError("Ollama not responding")
                except Exception:
                    # Fallback to llmfit if repo available
                    if req.repo and shutil.which("llmfit"):
                        use_ollama = False
                        use_llmfit = True
                        yield {
                            "event": "install_log",
                            "data": json.dumps(
                                {
                                    "step": "fallback",
                                    "message": "Ollama not running — falling back to llmfit download",
                                    "progress": 5,
                                }
                            ),
                        }
                    else:
                        _download_state["status"] = "error"
                        _download_state["message"] = "Ollama is not running."
                        yield {
                            "event": "install_error",
                            "data": json.dumps(
                                {
                                    "status": "error",
                                    "message": "Ollama is not running. Start it first or install llmfit for GGUF downloads.",
                                    "code": "NOT_RUNNING",
                                }
                            ),
                        }
                        return

                if use_ollama:
                    _update_state(
                        5, f"Pulling model {req.name}. This may take a while..."
                    )
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "pull",
                                "message": f"Pulling model {req.name}. This may take a while...",
                                "progress": 5,
                            }
                        ),
                    }

                    process = await asyncio.create_subprocess_exec(
                        "ollama",
                        "pull",
                        req.name,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.STDOUT,
                    )
                    _download_process = process

                    assert process.stdout is not None
                    line_count = 0
                    async for line in process.stdout:
                        line_str = line.decode("utf-8", errors="replace").rstrip()
                        if not line_str:
                            continue
                        line_count += 1
                        progress = min(10 + line_count * 2, 95)
                        _update_state(progress, line_str)
                        yield {
                            "event": "install_log",
                            "data": json.dumps(
                                {
                                    "step": "pull",
                                    "message": line_str,
                                    "progress": progress,
                                }
                            ),
                        }

                    await process.wait()

                    if process.returncode == 0:
                        _download_state["status"] = "complete"
                        _download_state["message"] = f"Pull completed: {req.name}"
                        _download_state["progress"] = 100
                        yield {
                            "event": "install_complete",
                            "data": json.dumps(
                                {
                                    "status": "success",
                                    "model": req.name,
                                }
                            ),
                        }
                    else:
                        _download_state["status"] = "error"
                        _download_state["message"] = (
                            f"Pull failed with exit code {process.returncode}"
                        )
                        yield {
                            "event": "install_error",
                            "data": json.dumps(
                                {
                                    "status": "error",
                                    "message": f"Pull failed with exit code {process.returncode}",
                                    "code": "PULL_FAILED",
                                }
                            ),
                        }
                    _download_state["active"] = False
                    _download_process = None
                    return

            # ── llmfit path ──────────────────────────────────────────────
            if use_llmfit:
                # Pre-check: llmfit installed
                if not shutil.which("llmfit"):
                    _download_state["status"] = "error"
                    _download_state["message"] = "llmfit is not installed."
                    yield {
                        "event": "install_error",
                        "data": json.dumps(
                            {
                                "status": "error",
                                "message": "llmfit is not installed. Install it first to download GGUF models.",
                                "code": "NOT_INSTALLED",
                            }
                        ),
                    }
                    return

                repo = req.repo or req.name
                model_display = req.name.split("/")[-1] if "/" in req.name else req.name
                output_dir = os.path.expanduser("~/.cache/llmfit/models/")
                os.makedirs(output_dir, exist_ok=True)

                _update_state(5, f"Starting download of {model_display} from {repo}...")
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {
                            "step": "download",
                            "message": f"Starting download of {model_display} from {repo}...",
                            "progress": 5,
                        }
                    ),
                }

                # Build command: llmfit download <repo> --output-dir <dir>  (NO --json!)
                cmd = ["llmfit", "download", repo, "--output-dir", output_dir]

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
                _download_process = process

                assert process.stdout is not None
                import re as _re

                last_pct = 0.0
                pre_pct = 0
                async for line in process.stdout:
                    line_str = line.decode("utf-8", errors="replace").strip()
                    # Strip ANSI escape codes (ESC[K) and carriage returns
                    clean = _re.sub("\[K|\r", "", line_str).strip()
                    if not clean:
                        continue
                    # Parse percentage from llmfit output: "100.0% - message"
                    pct_match = _re.match(r"\s*(\d+\.?\d*)\s*%", clean)
                    msg = clean
                    if pct_match:
                        pct_val = float(pct_match.group(1))
                        last_pct = min(pct_val, 99)
                        # Strip leading percentage for cleaner message
                        msg = _re.sub(r"^\s*\d+\.?\d*\s*%\s*-\s*", "", clean)
                        _update_state(last_pct, msg)
                        yield {
                            "event": "install_log",
                            "data": json.dumps(
                                {
                                    "step": "download",
                                    "message": msg,
                                    "progress": last_pct,
                                }
                            ),
                        }
                    else:
                        # Non-percentage line: show incremental pre-progress
                        pre_pct = min(pre_pct + 1, 3)
                        _update_state(pre_pct, msg)
                        yield {
                            "event": "install_log",
                            "data": json.dumps(
                                {
                                    "step": "download",
                                    "message": msg,
                                    "progress": pre_pct,
                                }
                            ),
                        }

                await process.wait()

                if process.returncode == 0:
                    _download_state["status"] = "complete"
                    _download_state["message"] = f"Download completed: {model_display}"
                    _download_state["progress"] = 100
                    yield {
                        "event": "install_complete",
                        "data": json.dumps(
                            {
                                "status": "success",
                                "message": f"Download completed: {model_display}",
                                "model": req.name,
                            }
                        ),
                    }
                else:
                    _download_state["status"] = "error"
                    _download_state["message"] = (
                        f"llmfit download failed with exit code {process.returncode}"
                    )
                    yield {
                        "event": "install_error",
                        "data": json.dumps(
                            {
                                "status": "error",
                                "message": f"llmfit download failed with exit code {process.returncode}",
                                "code": "DOWNLOAD_FAILED",
                            }
                        ),
                    }

        except Exception as e:
            _download_state["status"] = "error"
            _download_state["message"] = str(e)
            yield {
                "event": "install_error",
                "data": json.dumps(
                    {
                        "status": "error",
                        "message": str(e),
                        "code": "UNEXPECTED_ERROR",
                    }
                ),
            }
        finally:
            _download_state["active"] = False
            _download_process = None

    return EventSourceResponse(generate(), ping=15)


# ── Standalone launcher ─────────────────────────────────────────────────


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    max_concurrent: int = 3,
) -> None:
    """Start the uvicorn server (blocking).

    Args:
        host: Bind address.
        port: Bind port.
        max_concurrent: Max concurrent research sessions (1-10).
    """
    global _session_semaphore, MAX_CONCURRENT_SESSIONS
    max_concurrent = max(1, min(max_concurrent, 10))
    MAX_CONCURRENT_SESSIONS = max_concurrent
    _session_semaphore = asyncio.Semaphore(max_concurrent)
    logger.info("Session concurrency limit set to %d", max_concurrent)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
