"""FastAPI web server for the DeepResearch real-time dashboard.

Thin wiring layer — all route handlers live in ``routes/`` submodules.
This file creates the FastAPI app, configures middleware, mounts static
files, registers route modules, and provides the standalone launcher.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse
from fastapi.staticfiles import StaticFiles

from deepresearch import __version__ as _deepresearch_version
from deepresearch.web.event_bus import event_bus as global_event_bus
from deepresearch.web import state as _ws
from deepresearch.web.routes import _helpers as _h

logger = logging.getLogger(__name__)

# ── Persistent file logging ─────────────────────────────────────────────
_log_dir = Path(__file__).resolve().parent.parent.parent.parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "deepresearch.log"

_file_handler = logging.handlers.RotatingFileHandler(
    _log_file,
    maxBytes=10_485_760,
    backupCount=5,
)
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)

root_logger = logging.getLogger()
root_logger.addHandler(_file_handler)
root_logger.setLevel(logging.DEBUG)

logging.getLogger("deepresearch").info("File logging initialized: %s", _log_file)

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
from deepresearch.web.settings_manager import settings_manager  # noqa: E402

_settings_env_path = settings_manager._settings_dir / ".env"
if _settings_env_path.exists():
    _loaded = 0
    for line in _settings_env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                _loaded += 1
    if _loaded:
        logger.info("Loaded %d API key(s) from .env into environment", _loaded)

VERSION = f"v{_deepresearch_version}"


# ── Lifespan ────────────────────────────────────────────────────────────


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Lifespan handler — saves session state on shutdown."""
    yield
    logger.warning("Server shutting down — saving session state...")
    try:
        from deepresearch.web.sessions import multi_session_manager

        await multi_session_manager.save_all_sessions()
        logger.warning("Session state saved successfully")
    except Exception as e:
        logger.error("Failed to save sessions during shutdown: %s", e)

    _h.llamacpp_shutting_down = True
    if _h.llamacpp_process is not None and _h.llamacpp_process.returncode is None:
        logger.warning("Stopping llama.cpp...")
        _h.llamacpp_process.terminate()
        try:
            await asyncio.wait_for(_h.llamacpp_process.wait(), timeout=5)
            logger.warning("llama.cpp stopped")
        except asyncio.TimeoutError:
            _h.llamacpp_process.kill()
            await _h.llamacpp_process.wait()
            logger.warning("llama.cpp killed (force)")


app = FastAPI(title="DeepResearch Dashboard", lifespan=_lifespan)

# ── Serve static files ─────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
logger.info(
    "DeepResearch %s starting on port %d",
    VERSION,
    int(__import__("os").environ.get("PORT", 7500)),
)

# ── CORS ────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── System Log Buffer ──────────────────────────────────────────────────
SYSTEM_LOG: list[dict[str, Any]] = []
MAX_LOG_ENTRIES = 500


class SystemLogHandler(logging.Handler):
    """Custom logging handler that captures log records into SYSTEM_LOG."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict[str, Any] = {
                "timestamp": datetime.now().isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            }
            SYSTEM_LOG.append(entry)
            if len(SYSTEM_LOG) > MAX_LOG_ENTRIES:
                SYSTEM_LOG[:] = SYSTEM_LOG[-MAX_LOG_ENTRIES:]
        except Exception:
            pass


_deepresearch_logger = logging.getLogger("deepresearch")
_system_log_handler = SystemLogHandler()
_system_log_handler.setLevel(logging.INFO)
_deepresearch_logger.addHandler(_system_log_handler)
_deepresearch_logger.setLevel(logging.DEBUG)

logger.info("System log initialized — up to %d entries", MAX_LOG_ENTRIES)


# ── Core Endpoints (kept in server.py) ─────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> str:
    """Serve the self-contained dashboard HTML page."""
    html_path = HERE / "dashboard.html"
    return html_path.read_text(encoding="utf-8")


@app.get("/api/events")
async def event_stream(request: Request) -> EventSourceResponse:
    """Global SSE endpoint: streams all orchestrator events."""
    queue = await global_event_bus.subscribe()
    logger.debug(
        "SSE client connected (subscriber count: %d)",
        global_event_bus.subscriber_count,
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


@app.get("/api/status")
async def get_status() -> JSONResponse:
    """Return the current session state as JSON (polling fallback)."""
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
    """Return the current dashboard version."""
    return JSONResponse({"version": VERSION})


@app.get("/api/agents")
async def get_agents() -> JSONResponse:
    """Return agent profile metadata."""
    from deepresearch.config import load_agent_profiles

    profiles = load_agent_profiles()
    return JSONResponse(
        [{"id": p.id, "name": p.name, "emoji": p.emoji} for p in profiles]
    )


@app.get("/api/download/{session_id}/{filename:path}")
async def download_file(session_id: str, filename: str) -> Any:
    """Download a generated file for a session."""
    from fastapi.responses import FileResponse

    requested_path = PurePosixPath(filename)
    if not requested_path.is_relative_to(PurePosixPath(".")):
        return JSONResponse({"error": "Invalid path"}, status_code=403)

    if ".." in filename or "/" in filename or "\\" in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    from deepresearch.web.sessions import SESSION_DB_PATH

    DOWNLOADS_DIR = SESSION_DB_PATH.parent
    base_dir = DOWNLOADS_DIR / session_id
    safe_path = (base_dir / filename).resolve()
    if not str(safe_path).startswith(str(base_dir.resolve())):
        return JSONResponse({"error": "Access denied"}, status_code=403)

    possible_paths: list[Path] = [safe_path]

    if not safe_path.exists():
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


@app.get("/api/system/log")
async def get_system_log(limit: int = 200, level: str = "") -> JSONResponse:
    """Return recent system log entries, newest first."""
    entries = list(reversed(SYSTEM_LOG))
    if level:
        entries = [e for e in entries if e["level"] == level.upper()]
    return JSONResponse(entries[:limit])


@app.post("/api/system/log/clear")
async def clear_system_log() -> JSONResponse:
    """Clear all system log entries."""
    SYSTEM_LOG.clear()
    return JSONResponse({"status": "ok"})


# ── Register Route Modules ─────────────────────────────────────────────

from deepresearch.web.routes.sessions import router as sessions_router  # noqa: E402
from deepresearch.web.routes.backends import router as backends_router  # noqa: E402
from deepresearch.web.routes.llamacpp import router as llamacpp_router  # noqa: E402
from deepresearch.web.routes.settings import router as settings_router  # noqa: E402
from deepresearch.web.routes.search import router as search_router  # noqa: E402
from deepresearch.web.routes.models import router as models_router  # noqa: E402

# ── Backward-compatible re-exports (tests import these from server.py) ──
from deepresearch.web.routes._helpers import (  # noqa: E402
    build_llamacpp_download_url as _build_llamacpp_download_url,  # noqa: F401
    detect_llamacpp_platform as _detect_llamacpp_platform,  # noqa: F401
    get_latest_llamacpp_tag as _get_latest_llamacpp_tag,  # noqa: F401
    is_port_available as _is_port_available,  # noqa: F401
    monitor_llamacpp_process as monitor_llamacpp_process,
)

# Backward-compat: tests patch these names on the server module

# Backward-compatible aliases for mutable state (tests mutate these via server module)
_llamacpp_process = _h.llamacpp_process
_llamacpp_config = _h.llamacpp_config
_llamacpp_shutting_down = _h.llamacpp_shutting_down
_llamacpp_serving_model = _h.llamacpp_serving_model
_llamacpp_restart_attempts = _h.llamacpp_restart_attempts

app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(backends_router, prefix="/api", tags=["backends"])
app.include_router(llamacpp_router, prefix="/api", tags=["llamacpp"])
app.include_router(settings_router, prefix="/api", tags=["settings"])
app.include_router(search_router, prefix="/api", tags=["search"])
app.include_router(models_router, prefix="/api", tags=["models"])


# ── Standalone launcher ─────────────────────────────────────────────────


def run_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    max_concurrent: int = 3,
) -> None:
    """Start the uvicorn server (blocking)."""
    max_concurrent = max(1, min(max_concurrent, 10))
    _h.MAX_CONCURRENT_SESSIONS = max_concurrent
    _h.set_session_semaphore(asyncio.Semaphore(max_concurrent))
    logger.info("Session concurrency limit set to %d", max_concurrent)

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
