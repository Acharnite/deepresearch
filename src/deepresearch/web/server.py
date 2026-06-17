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
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, AsyncGenerator

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles

from deepresearch import __version__ as _deepresearch_version
from deepresearch.config import load_agent_profiles, load_model_config
from deepresearch.constants import TIME_BUDGETS, TIME_BUDGET_SECONDS
from deepresearch.web.event_bus import event_bus as global_event_bus
from deepresearch.web.sessions import multi_session_manager
from deepresearch.web.settings_manager import PROVIDERS, settings_manager, context_window_manager
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
app = FastAPI(title="DeepeResearch Dashboard")

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
    return JSONResponse(_ws._current_agents)


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
    except RuntimeError as e:
        _session_semaphore.release()
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
    return JSONResponse({
        "total": len(all_sessions),
        "by_status": counts,
    })


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
                kw: v["seconds"]
                for kw, v in TIME_BUDGETS.items()
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
    return JSONResponse({"status": "ok", "model_id": req.model_id, "context_window": req.context_window})


@app.delete("/api/config/context/{model_id:path}")
async def delete_context_window(model_id: str) -> JSONResponse:
    """Remove a context window override for a model."""
    removed = context_window_manager.delete_override(model_id)
    if removed:
        return JSONResponse({"status": "ok", "model_id": model_id})
    return JSONResponse({"error": f"No override found for '{model_id}'"}, status_code=404)


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
