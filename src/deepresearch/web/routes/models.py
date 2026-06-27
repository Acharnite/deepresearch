"""Model recommendations, system info, profiles, and model listing routes."""

from __future__ import annotations

import json
import logging
import os

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from deepresearch.config import load_agent_profiles, load_model_config
from deepresearch.web.settings_manager import settings_manager, context_window_manager
from deepresearch.web.routes._helpers import (
    get_discovered_local_models,
    discover_provider_models,
    MAX_CONCURRENT_SESSIONS,
)

# Mutable state lives in server.py (tests patch it there).
import deepresearch.web.server as _srv

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/profiles")
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


@router.get("/models")
async def get_models() -> JSONResponse:
    """Return the list of available model configurations, including local
    and auto-discovered provider models."""
    try:
        models = load_model_config()

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

        discovered = get_discovered_local_models()
        for d in discovered:
            if not any(m.get("id") == d["id"] for m in models):
                models.append(d)

        if (
            _srv._llamacpp_process is not None
            and _srv._llamacpp_process.returncode is None
            and _srv._llamacpp_serving_model
        ):
            model_name = os.path.basename(_srv._llamacpp_serving_model).replace(
                ".gguf", ""
            )
            llamacpp_entry = {
                "id": f"llama-cpp/{model_name}",
                "provider": "llama-cpp",
                "display_name": f"llama-cpp/{model_name} (local)",
                "local": True,
                "context_length": _srv._llamacpp_config.get("context_size", 8192),
                "command": "",
            }
            if not any(m.get("id") == llamacpp_entry["id"] for m in models):
                models.append(llamacpp_entry)

        provider_models = await discover_provider_models()
        for pm in provider_models:
            if not any(m.get("id") == pm["id"] for m in models):
                models.append(pm)

        overrides = context_window_manager.get_overrides()
        for m in models:
            mid = m.get("id", "")
            if mid in overrides:
                m["context_window"] = overrides[mid]

        return JSONResponse(models)
    except Exception as e:
        logger.exception("Failed to load models")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/tools/status")
async def get_tools_status() -> JSONResponse:
    """Check which tools are installed (Ollama, llama.cpp)."""
    import shutil
    import subprocess

    result: dict[str, dict[str, bool | str]] = {}

    result["ollama"] = {"installed": False, "running": False}
    if shutil.which("ollama"):
        result["ollama"]["installed"] = True  # type: ignore[assignment]
        try:
            ver = subprocess.run(
                ["ollama", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            result["ollama"]["version"] = ver.stdout.strip() or ver.stderr.strip()
        except Exception:
            result["ollama"]["version"] = "unknown"
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get("http://localhost:11434/api/tags")
                result["ollama"]["running"] = resp.status_code == 200  # type: ignore[assignment]
        except Exception:
            result["ollama"]["running"] = False

    result["llamacpp"] = {"installed": False, "running": False}
    if shutil.which("llama-server"):
        result["llamacpp"]["installed"] = True  # type: ignore[assignment]
        try:
            ver = subprocess.run(
                ["llama-server", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            result["llamacpp"]["version"] = ver.stdout.strip() or ver.stderr.strip()
        except Exception:
            result["llamacpp"]["version"] = "unknown"
        result["llamacpp"]["running"] = (  # type: ignore[assignment]
            _srv._llamacpp_process is not None
            and _srv._llamacpp_process.returncode is None
        )

    return JSONResponse(result)


@router.get("/hardware")
async def get_hardware_info() -> JSONResponse:
    """Return hardware specs. Tries llmfit first, then falls back to built-in detection."""
    import shutil
    import subprocess

    # Try llmfit first (Phase 1 backward compat)
    if shutil.which("llmfit"):
        try:
            result = subprocess.run(
                ["llmfit", "system", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return JSONResponse(
                    {"available": True, "hardware": data.get("system", {})}
                )
            return JSONResponse({"available": False, "error": result.stderr.strip()})
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            pass

    # Fall back to built-in hardware detection
    from deepresearch.hardware import get_hardware_info as _detect

    hw = _detect()
    return JSONResponse({"available": True, "hardware": hw})


@router.get("/system/concurrency")
async def get_concurrency_status() -> JSONResponse:
    """Return current concurrency state for sessions and web searches."""
    from deepresearch.web.sessions import multi_session_manager
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
