"""Settings and configuration routes (API keys, local models, scribe, context windows)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from deepresearch.web.settings_manager import (
    settings_manager,
    context_window_manager,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class SetKeyRequest(BaseModel):
    """Request body for POST /api/settings/keys."""

    provider: str
    key: str


class ScribeModelRequest(BaseModel):
    """Request body for POST /api/settings/scribe-model."""

    scribe_model: str


class MaxTokensRequest(BaseModel):
    """Request body for POST /api/settings/max-tokens."""

    max_tokens: int


class ContextWindowRequest(BaseModel):
    """Request body for POST /api/config/context."""

    model_id: str
    context_window: int


class AddEndpointRequest(BaseModel):
    """Request body for POST /api/settings/local-endpoints."""

    name: str
    endpoint: str
    type: str = "openai"


@router.get("/settings/keys")
async def get_settings_keys() -> JSONResponse:
    """Return all configured providers and their status."""
    return JSONResponse(settings_manager.get_keys())


@router.post("/settings/keys")
async def set_settings_key(req: SetKeyRequest) -> JSONResponse:
    """Save an API key for a provider."""
    try:
        settings_manager.set_key(req.provider, req.key)
        return JSONResponse({"status": "ok", "provider": req.provider})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.delete("/settings/keys/{provider}")
async def delete_settings_key(provider: str) -> Response:
    """Remove an API key for a provider."""
    settings_manager.delete_key(provider)
    return Response(status_code=204)


@router.get("/settings/local-models")
async def get_local_models() -> JSONResponse:
    """Discover and return available local model endpoints."""
    results: list[dict[str, Any]] = []

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
        pass

    saved = settings_manager.get_local_endpoints()
    results.extend(saved)

    return JSONResponse(results)


@router.post("/settings/local-endpoints")
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


@router.delete("/settings/local-endpoints/{name}")
async def remove_local_endpoint(name: str) -> Response:
    """Remove a saved local endpoint by name."""
    settings_manager.remove_local_endpoint(name)
    return Response(status_code=204)


@router.post("/settings/local-endpoints/{name}/test")
async def test_local_endpoint(name: str) -> JSONResponse:
    """Test connectivity to a local endpoint."""
    endpoints = settings_manager.get_local_endpoints()
    ep = next((e for e in endpoints if e.get("name") == name), None)
    if ep is None:
        return JSONResponse({"error": f"Endpoint '{name}' not found"}, status_code=404)
    try:
        endpoint_url = ep["endpoint"].rstrip("/")
        test_url = f"{endpoint_url}/models"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(test_url)
            if resp.status_code == 200:
                return JSONResponse(
                    {"status": "ok", "message": f"Connected to {endpoint_url}"}
                )
            return JSONResponse(
                {
                    "status": "error",
                    "message": f"Unexpected status {resp.status_code}",
                }
            )
    except httpx.ConnectError:
        return JSONResponse(
            {
                "status": "error",
                "message": f"Could not connect to {ep['endpoint']}",
            }
        )
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)})


@router.get("/settings/scribe-model")
async def get_scribe_model() -> JSONResponse:
    """Get the saved scribe model ID."""
    model = settings_manager.get_scribe_model()
    return JSONResponse({"scribe_model": model})


@router.post("/settings/scribe-model")
async def set_scribe_model(req: ScribeModelRequest) -> JSONResponse:
    """Save the scribe model ID."""
    settings_manager.set_scribe_model(req.scribe_model)
    logger.info("Scribe model set to: %s", req.scribe_model)
    return JSONResponse({"status": "ok", "scribe_model": req.scribe_model})


@router.delete("/settings/scribe-model")
async def delete_scribe_model() -> Response:
    """Remove the scribe model setting."""
    settings_manager.delete_scribe_model()
    return Response(status_code=204)


@router.get("/settings/max-tokens")
async def get_max_tokens() -> JSONResponse:
    """Get the configured max tokens per agent call."""
    value = settings_manager.get_max_tokens()
    return JSONResponse({"max_tokens": value})


@router.post("/settings/max-tokens")
async def set_max_tokens(req: MaxTokensRequest) -> JSONResponse:
    """Save the max tokens per agent call setting."""
    try:
        settings_manager.set_max_tokens(req.max_tokens)
        logger.info("Max tokens set to %d", req.max_tokens)
        return JSONResponse({"status": "ok", "max_tokens": req.max_tokens})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@router.get("/config/context")
async def get_context_windows() -> JSONResponse:
    """Return all context window overrides."""
    return JSONResponse(context_window_manager.get_overrides())


@router.post("/config/context")
async def set_context_window(req: ContextWindowRequest) -> JSONResponse:
    """Set a context window override for a model."""
    if req.context_window < 1:
        return JSONResponse({"error": "context_window must be >= 1"}, status_code=400)
    context_window_manager.set_override(req.model_id, req.context_window)
    return JSONResponse(
        {
            "status": "ok",
            "model_id": req.model_id,
            "context_window": req.context_window,
        }
    )


@router.delete("/config/context/{model_id:path}")
async def delete_context_window(model_id: str) -> Response:
    """Remove a context window override for a model."""
    removed = context_window_manager.delete_override(model_id)
    if removed:
        return Response(status_code=204)
    return JSONResponse(
        {"error": f"No override found for '{model_id}'"}, status_code=404
    )
