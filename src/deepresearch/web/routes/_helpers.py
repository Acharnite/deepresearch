"""Shared state and helper functions for route modules.

All global mutable state that multiple route modules need lives here.
Import this module to read/write shared state — never import server.py
directly from route modules (circular import risk).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncGenerator

import httpx

logger = logging.getLogger(__name__)

# ── Session concurrency ────────────────────────────────────────────────
MAX_CONCURRENT_SESSIONS: int = 3
_session_semaphore: asyncio.Semaphore = asyncio.Semaphore(MAX_CONCURRENT_SESSIONS)


def get_session_semaphore() -> asyncio.Semaphore:
    return _session_semaphore


def set_session_semaphore(sem: asyncio.Semaphore) -> None:
    global _session_semaphore
    _session_semaphore = sem


# ── llama.cpp process tracking ─────────────────────────────────────────
llamacpp_process: asyncio.subprocess.Process | None = None
llamacpp_config: dict = {
    "port": 8080,
    "installed": False,
    "gpu_layers": 0,
    "context_size": 8192,
    "flash_attn": False,
}
llamacpp_shutting_down: bool = False
llamacpp_restart_attempts: int = 0
llamacpp_serving_model: str | None = None
MAX_RESTART_ATTEMPTS: int = 3

# ── Download state ─────────────────────────────────────────────────────
download_state: dict[str, Any] = {
    "active": False,
    "model": "",
    "progress": 0,
    "message": "",
    "status": "idle",
    "log": [],
}
download_process: asyncio.subprocess.Process | None = None

# ── Local model discovery cache ────────────────────────────────────────
_discovered_local_models_cache: list[dict[str, Any]] = []
_discovered_local_models_time: float = 0

# ── Provider model discovery cache ─────────────────────────────────────
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

# ── Backend definitions ────────────────────────────────────────────────
BACKEND_DEFINITIONS: list[dict] = [
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
        "binary": "llama-server",
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


# ── Shared generator helpers ───────────────────────────────────────────


async def error_generator(msg: str) -> AsyncGenerator[str, None]:
    """Generate a single error event."""
    yield {"event": "error", "data": __import__("json").dumps({"error": msg})}


async def install_error_generator(
    msg: str, code: str = "ALREADY_INSTALLED"
) -> AsyncGenerator[str, None]:
    """Generate a single install error event."""
    import json

    yield {
        "event": "install_error",
        "data": json.dumps({"status": "error", "message": msg, "code": code}),
    }


# ── Local model discovery ──────────────────────────────────────────────


def get_discovered_local_models() -> list[dict[str, Any]]:
    """Return cached discovered local models (refreshed every 60s)."""
    global _discovered_local_models_cache, _discovered_local_models_time
    now = time.time()
    if now - _discovered_local_models_time < 60 and _discovered_local_models_cache:
        return _discovered_local_models_cache

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


# ── Provider model discovery ───────────────────────────────────────────


def get_api_key(provider_id: str) -> str | None:
    """Get the actual API key for a provider from env or .env file."""
    from deepresearch.web.settings_manager import PROVIDERS, settings_manager

    info = PROVIDERS.get(provider_id)
    if not info:
        return None
    env_var = info["env_var"]
    key = os.environ.get(env_var)
    if key:
        return key
    return settings_manager._get_from_file(env_var)


def get_provider_auth(
    provider_id: str, api_key: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Return (headers, query_params) for a provider's model API."""
    if provider_id == "gemini":
        return {}, {"key": api_key}
    if provider_id == "anthropic":
        return {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, {}
    return {"Authorization": f"Bearer {api_key}"}, {}


def parse_provider_models(provider_id: str, data: dict | list) -> list[dict[str, str]]:
    """Parse a provider's model-list API response into [{id, display_name}]."""
    if provider_id == "anthropic":
        raw = data.get("data", []) if isinstance(data, dict) else data
        return [
            {"id": m["id"], "display_name": m.get("display_name") or m["id"]}
            for m in raw
            if isinstance(m, dict) and m.get("type") == "model"
        ]

    if provider_id == "gemini":
        raw = data.get("models", []) if isinstance(data, dict) else []
        result: list[dict[str, str]] = []
        for m in raw:
            name = m.get("name", "")
            if name.startswith("models/"):
                name = name[7:]
            result.append({"id": name, "display_name": m.get("displayName", name)})
        return result

    if provider_id == "cohere":
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

    raw = data.get("data", []) if isinstance(data, dict) else data
    return [{"id": m["id"]} for m in raw if isinstance(m, dict) and "id" in m]


async def discover_provider_models() -> list[dict[str, Any]]:
    """Fetch model lists from all configured (API-keyed) providers."""
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
            api_key = get_api_key(provider_id)
            if not api_key:
                continue

            headers, params = get_provider_auth(provider_id, api_key)

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
                raw_models = parse_provider_models(provider_id, data)

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

    # Opencode AI — Zen and Go
    opencode_key = get_api_key("opencode")
    if opencode_key:
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


# ── Backend probing ────────────────────────────────────────────────────


async def probe_backend(defn: dict) -> dict:
    """Probe a single local backend for installation and running status."""
    import shutil
    import subprocess

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


# ── llama.cpp platform helpers ─────────────────────────────────────────


def detect_llamacpp_platform() -> dict:
    """Return download info for the current platform."""
    import platform as _platform
    import shutil as _shutil

    system = _platform.system().lower()
    machine = _platform.machine().lower()

    if system == "darwin":
        if machine == "arm64":
            return {"asset": "macos-arm64", "ext": "tar.gz"}
        return {"asset": "macos-x64", "ext": "tar.gz"}

    if system == "linux":
        if machine == "aarch64":
            return {"asset": "ubuntu-arm64", "ext": "tar.gz"}
        if _shutil.which("rocm-smi"):
            return {"asset": "ubuntu-rocm-7.2-x64", "ext": "tar.gz"}
        if _shutil.which("nvidia-smi"):
            return {"asset": "ubuntu-vulkan-x64", "ext": "tar.gz"}
        return {"asset": "ubuntu-x64", "ext": "tar.gz"}

    if system == "windows":
        return {"asset": "win-cpu-x64", "ext": "zip"}

    raise RuntimeError(f"Unsupported platform: {system} {machine}")


async def get_latest_llamacpp_tag() -> str:
    """Resolve the latest release tag from GitHub API."""
    proc = await asyncio.create_subprocess_exec(
        "curl",
        "-fsSL",
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Failed to fetch latest llama.cpp tag: "
            f"{stderr.decode('utf-8', errors='replace').strip()}"
        )
    data = __import__("json").loads(stdout.decode("utf-8"))
    return data["tag_name"]


def build_llamacpp_download_url(tag: str, platform_info: dict) -> str:
    asset = platform_info["asset"]
    ext = platform_info["ext"]
    return (
        f"https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{tag}/llama-{tag}-bin-{asset}.{ext}"
    )


def is_port_available(port: int) -> bool:
    """Check if a TCP port is available (not in use)."""
    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


async def monitor_llamacpp_process() -> None:
    """Background task: monitor llama-server and restart if needed.

    Reads state from server.py's namespace via the calling module's _srv ref.
    """
    import deepresearch.web.server as _srv

    if _srv._llamacpp_process is None:
        return

    stderr_task = None
    if _srv._llamacpp_process.stderr is not None:

        async def _drain_stderr():
            try:
                async for line in _srv._llamacpp_process.stderr:  # type: ignore[union-attr]
                    text = line.decode("utf-8", errors="replace").rstrip()
                    if text:
                        logger.debug("llama-server stderr: %s", text)
            except Exception:
                pass

        stderr_task = asyncio.ensure_future(_drain_stderr())

    try:
        await _srv._llamacpp_process.wait()
    except Exception:
        pass

    if stderr_task is not None:
        stderr_task.cancel()

    if _srv._llamacpp_shutting_down:
        return

    logger.warning(
        "llama-server process exited (returncode=%s)",
        _srv._llamacpp_process.returncode,
    )
    _srv._llamacpp_process = None
    _srv._llamacpp_serving_model = None

    if (
        not _srv._llamacpp_shutting_down
        and _srv._llamacpp_restart_attempts < MAX_RESTART_ATTEMPTS
    ):
        backoff = (2**_srv._llamacpp_restart_attempts) * 5
        _srv._llamacpp_restart_attempts += 1
        logger.info(
            "Auto-restarting llama-server in %ds (attempt %d/%d)...",
            backoff,
            _srv._llamacpp_restart_attempts,
            MAX_RESTART_ATTEMPTS,
        )
        await asyncio.sleep(backoff)
        try:
            _srv._llamacpp_process = await asyncio.create_subprocess_exec(
                "llama-server",
                "--host",
                "127.0.0.1",
                "--port",
                str(_srv._llamacpp_config["port"]),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            logger.info(
                "llama-server auto-restarted (attempt %d)",
                _srv._llamacpp_restart_attempts,
            )
        except Exception as e:
            logger.error("Failed to auto-restart llama-server: %s", e)
    elif not _srv._llamacpp_shutting_down:
        logger.warning(
            "llama-server exceeded max restart attempts (%d); giving up",
            MAX_RESTART_ATTEMPTS,
        )
