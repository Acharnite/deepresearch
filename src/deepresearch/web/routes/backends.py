"""Local backend management routes (discovery, ollama)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from deepresearch.web.settings_manager import local_backend_manager
from deepresearch.web.routes._helpers import (
    BACKEND_DEFINITIONS,
    install_error_generator,
    probe_backend,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class BackendAddressRequest(BaseModel):
    """Request body for setting a custom backend address."""

    address: str


class PullModelRequest(BaseModel):
    model: str


@router.get("/local-backends")
async def list_local_backends() -> JSONResponse:
    """Return status for all known local backends, probed concurrently."""

    async def _probe_with_custom(defn: dict) -> dict:
        name = defn["name"]
        custom = local_backend_manager.get_address(name)
        if custom is not None:
            parts = custom.rsplit(":", 1)
            if len(parts) == 2 and parts[1].isdigit():
                custom_defn = dict(defn)
                custom_defn["port"] = int(parts[1])
                result = await probe_backend(custom_defn)
                result["custom_address"] = custom
                return result
        result = await probe_backend(defn)
        result["custom_address"] = None
        return result

    results = await asyncio.gather(
        *(_probe_with_custom(defn) for defn in BACKEND_DEFINITIONS)
    )
    return JSONResponse({"backends": results})


@router.post("/local-backends/{name}/test")
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


@router.put("/local-backends/{name}/address")
async def set_backend_address(name: str, req: BackendAddressRequest) -> JSONResponse:
    """Set a custom address override for a local backend."""
    defn = next((d for d in BACKEND_DEFINITIONS if d["name"] == name), None)
    if defn is None:
        return JSONResponse(
            {"status": "error", "message": f"Unknown backend: {name}"},
            status_code=404,
        )

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


@router.get("/local-backends/{name}/address")
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


# ── Ollama ─────────────────────────────────────────────────────────────


@router.get("/local-backends/ollama/status")
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


@router.api_route("/local-backends/ollama/install", methods=["GET", "POST"])
async def install_ollama(request: Request) -> EventSourceResponse:
    """Install Ollama via curl|sh with live SSE log streaming."""
    import shutil

    if shutil.which("ollama"):
        return EventSourceResponse(
            install_error_generator("Ollama is already installed")
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

            process = await asyncio.create_subprocess_exec(
                "sh",
                "-c",
                "curl -fsSL https://ollama.com/install.sh | sh",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )

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


@router.post("/local-backends/ollama/start")
async def start_ollama() -> JSONResponse:
    """Start Ollama service."""
    import shutil
    import subprocess

    if not shutil.which("ollama"):
        return JSONResponse(
            {"status": "error", "message": "Ollama is not installed"},
            status_code=400,
        )

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


@router.post("/local-backends/ollama/stop")
async def stop_ollama() -> JSONResponse:
    """Stop Ollama service."""
    import subprocess

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


@router.api_route("/local-backends/ollama/uninstall", methods=["GET", "POST"])
async def uninstall_ollama(request: Request) -> EventSourceResponse:
    """Uninstall Ollama with live SSE log streaming."""
    import shutil

    if not shutil.which("ollama"):
        return EventSourceResponse(
            install_error_generator("Ollama is not installed", "NOT_INSTALLED")
        )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {"step": "stop", "message": "Stopping Ollama...", "progress": 10}
                ),
            }

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


@router.post("/local-backends/ollama/pull")
async def pull_ollama_model(
    req: PullModelRequest, request: Request
) -> EventSourceResponse:
    """Pull an Ollama model via 'ollama pull' with SSE log streaming."""
    import shutil

    if not shutil.which("ollama"):
        return EventSourceResponse(
            install_error_generator("Ollama is not installed", "NOT_INSTALLED")
        )

    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get("http://localhost:11434/api/tags")
            if resp.status_code != 200:
                raise RuntimeError("Ollama not responding")
    except Exception:
        return EventSourceResponse(
            install_error_generator(
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
                if "file does not exist" in line_str.lower():
                    yield {
                        "event": "install_error",
                        "data": json.dumps(
                            {
                                "status": "error",
                                "message": f"Model '{req.model}' not found in Ollama library. "
                                f"Ollama's registry is separate from HuggingFace. "
                                f"Check available models at https://ollama.com/library",
                                "code": "MODEL_NOT_FOUND",
                            }
                        ),
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


@router.delete("/local-backends/models/{model_name}")
async def delete_ollama_model(model_name: str) -> Response:
    """Delete an Ollama model via 'ollama rm'."""
    import shutil
    import subprocess

    if not shutil.which("ollama"):
        return JSONResponse(
            {"status": "error", "message": "Ollama is not installed"}, status_code=400
        )

    try:
        result = subprocess.run(
            ["ollama", "rm", model_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return Response(status_code=204)
        else:
            return JSONResponse(
                {
                    "status": "error",
                    "message": result.stderr.strip() or "Failed to delete model",
                },
                status_code=500,
            )
    except subprocess.TimeoutExpired:
        return JSONResponse(
            {"status": "error", "message": "Delete timed out"}, status_code=500
        )


# ── Model Download ─────────────────────────────────────────────────────
