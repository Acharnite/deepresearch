"""Session management routes."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from deepresearch.constants import TIME_BUDGETS
from deepresearch.web.sessions import multi_session_manager
from deepresearch.web.routes._helpers import (
    get_session_semaphore,
    MAX_CONCURRENT_SESSIONS,
    error_generator,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class RunRequest(BaseModel):
    """Request body for POST /api/run."""

    topic: str
    time_budget: str = "medium"
    time_budget_seconds: int | None = None
    model_mode: str = "same"
    selected_model: str | None = None
    agent_models: dict[str, str] | None = None
    max_rounds: int | None = None
    output_language: str = "English"


class RunResponse(BaseModel):
    """Response from POST /api/run."""

    status: str
    session_id: str
    topic: str
    time_budget: str
    model_mode: str


@router.post("/run", status_code=201, response_model=RunResponse)
async def start_research(req: RunRequest) -> JSONResponse:
    """Start a new research session in the background."""
    sem = get_session_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=0.001)
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
        import httpx

        if req.selected_model and req.selected_model.startswith("ollama/"):
            try:
                async with httpx.AsyncClient(timeout=5) as hc:
                    r = await hc.get("http://localhost:11434/api/tags")
                    if r.status_code != 200:
                        sem.release()
                        return JSONResponse(
                            {
                                "error": f"Ollama is not responding (HTTP {r.status_code}). Make sure Ollama is running."
                            },
                            status_code=503,
                        )
                    model_name = req.selected_model.split("/", 1)[1]
                    models = r.json().get("models", [])
                    if not any(model_name in (m.get("name") or "") for m in models):
                        sem.release()
                        return JSONResponse(
                            {
                                "error": f"Model '{model_name}' not found in Ollama. Run 'ollama pull {model_name}' first."
                            },
                            status_code=400,
                        )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                sem.release()
                return JSONResponse(
                    {
                        "error": f"Cannot connect to Ollama on localhost:11434. Is Ollama running? ({e})"
                    },
                    status_code=503,
                )

        from deepresearch.web.settings_manager import settings_manager

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
            semaphore=sem,
        )
        return JSONResponse(
            {
                "status": "started",
                "session_id": info.session_id,
                "topic": info.topic,
                "time_budget": info.time_budget,
                "model_mode": info.model_mode,
            },
            status_code=201,
        )
    except Exception as e:
        sem.release()
        logger.warning("Session creation failed, semaphore released: %s", e)
        return JSONResponse({"error": str(e)}, status_code=409)


@router.get("/sessions")
async def list_sessions(
    limit: int | None = None,
    offset: int = 0,
    status: str | None = None,
    search: str | None = None,
) -> JSONResponse:
    """List research sessions with optional filtering and pagination."""
    result = multi_session_manager.list_sessions(
        limit=limit,
        offset=offset,
        status_filter=status,
        search=search,
    )
    return JSONResponse(result)


@router.get("/sessions/stats")
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


@router.get("/sessions/{session_id}")
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


@router.get("/sessions/{session_id}/state")
async def get_session_state(session_id: str) -> JSONResponse:
    """Get the current runtime state of a running session."""
    info = multi_session_manager.get_session(session_id)
    if info is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)

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


@router.get("/sessions/{session_id}/cost")
async def get_session_cost(session_id: str) -> JSONResponse:
    """Return token usage and cost for a session."""
    info = multi_session_manager.get_session(session_id)
    if info is None:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    tracker = getattr(info, "token_tracker", None)
    if tracker is None:
        return JSONResponse({"total_cost": 0, "total_tokens": 0, "per_model": {}})
    return JSONResponse(tracker.to_dict())


@router.get(
    "/sessions/{session_id}/events",
    responses={
        200: {
            "description": "SSE event stream",
            "content": {"text/event-stream": {}},
        }
    },
)
async def session_event_stream(
    session_id: str, request: Request
) -> EventSourceResponse:
    """SSE endpoint: stream events for a specific session."""
    info = multi_session_manager.get_session(session_id)
    if info is None:
        return EventSourceResponse(
            async_generator=lambda: error_generator("Session not found"),
        )

    bus = info.event_bus
    if bus is None:
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
            for event in info.event_history:
                if await request.is_disconnected():
                    break
                yield {"data": json.dumps(event)}

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


@router.post("/sessions/{session_id}/cancel")
async def cancel_session(session_id: str) -> JSONResponse:
    """Cancel a running session."""
    cancelled = await multi_session_manager.cancel_session(session_id)
    if cancelled:
        return JSONResponse({"status": "cancelled", "session_id": session_id})
    return JSONResponse(
        {"status": "not_found_or_already_done", "session_id": session_id}
    )


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> Response:
    """Delete a completed/error/cancelled session."""
    removed = multi_session_manager.remove_session(session_id)
    if removed:
        return Response(status_code=204)
    info = multi_session_manager.get_session(session_id)
    if info is not None:
        return JSONResponse(
            {"error": "Cannot delete a running session — cancel it first"},
            status_code=409,
        )
    return JSONResponse({"error": "Session not found"}, status_code=404)


@router.post("/sessions/clear-completed")
async def clear_completed_sessions() -> JSONResponse:
    """Remove all completed/error/cancelled sessions."""
    count = multi_session_manager.clear_completed()
    return JSONResponse({"status": "ok", "removed": count})


@router.post("/sessions/bulk-delete")
async def bulk_delete_sessions(request: Request) -> JSONResponse:
    """Delete multiple sessions by ID list."""
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
