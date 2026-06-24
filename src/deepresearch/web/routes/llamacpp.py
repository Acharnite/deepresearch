"""llama.cpp lifecycle routes (install, uninstall, start, stop, serve, config)."""
from __future__ import annotations

import asyncio
import json
import logging
import os as _os
from typing import Any, AsyncGenerator

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from deepresearch.web.settings_manager import local_backend_manager
from deepresearch.web.routes._helpers import (
    MAX_RESTART_ATTEMPTS,
    install_error_generator,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Mutable state and functions live in server.py (tests patch them there).
# Import server module to access — safe because this module is imported
# AFTER server.py is fully defined.
import deepresearch.web.server as _srv


class LlamacppConfigRequest(BaseModel):
    """Request body for PUT /api/local-backends/llamacpp/config."""

    port: int | None = None
    gpu_layers: int | None = None
    context_size: int | None = None
    flash_attn: bool | None = None
    batch_size: int | None = None


@router.get("/local-backends/llamacpp/status")
async def get_llamacpp_status() -> JSONResponse:
    """Check if llama-server is installed and running."""
    import shutil
    import subprocess

    installed = shutil.which("llama-server") is not None
    version = None
    running = False

    if installed:
        try:
            result = subprocess.run(
                ["llama-server", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = result.stdout.strip() or result.stderr.strip()
        except Exception:
            version = "unknown"

    running = (
        _srv._llamacpp_process is not None
        and _srv._llamacpp_process.returncode is None
    )

    response: dict = {
        "installed": installed,
        "running": running,
        "version": version,
    }

    if running:
        response["port"] = _srv._llamacpp_config.get("port", 8080)
        response["binary_path"] = shutil.which("llama-server")
        response["gpu_layers"] = _srv._llamacpp_config.get("gpu_layers", 0)
        response["context_size"] = _srv._llamacpp_config.get("context_size", 8192)
        response["batch_size"] = _srv._llamacpp_config.get("batch_size", 512)
        if _srv._llamacpp_process is not None:
            response["pid"] = _srv._llamacpp_process.pid
        if _srv._llamacpp_serving_model:
            model_name = _os.path.splitext(
                _os.path.basename(_srv._llamacpp_serving_model)
            )[0]
            response["active_model"] = {
                "path": _srv._llamacpp_serving_model,
                "name": model_name,
            }

    return JSONResponse(response)


@router.api_route("/local-backends/llamacpp/install", methods=["GET", "POST"])
async def install_llamacpp(request: Request) -> EventSourceResponse:
    """Download and install llama-server binary with SSE streaming."""
    import shutil
    import tempfile

    if shutil.which("llama-server"):
        return EventSourceResponse(
            install_error_generator("llama.cpp is already installed")
        )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {"step": "detect", "message": "Detecting platform...", "progress": 5}
                ),
            }
            platform_info = _srv._detect_llamacpp_platform()

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "detect",
                        "message": f"Platform: {platform_info['asset']}",
                        "progress": 10,
                    }
                ),
            }

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "tag",
                        "message": "Resolving latest release...",
                        "progress": 15,
                    }
                ),
            }
            tag = await _srv._get_latest_llamacpp_tag()

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "tag",
                        "message": f"Latest release: {tag}",
                        "progress": 20,
                    }
                ),
            }

            download_url = _srv._build_llamacpp_download_url(tag, platform_info)

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "download",
                        "message": f"Downloading {download_url}...",
                        "progress": 25,
                    }
                ),
            }

            ext = platform_info["ext"]
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}")
            _os.close(tmp_fd)

            try:
                curl_proc = await asyncio.create_subprocess_exec(
                    "curl",
                    "-fSL",
                    "-o",
                    tmp_path,
                    download_url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await asyncio.wait_for(
                    curl_proc.communicate(), timeout=300
                )
                if await request.is_disconnected():
                    curl_proc.kill()
                    _os.remove(tmp_path)
                    return
                if curl_proc.returncode != 0:
                    raise RuntimeError(
                        f"curl failed (exit {curl_proc.returncode}): "
                        f"{stderr.decode('utf-8', errors='replace').strip()}"
                    )
            except Exception as exc:
                _os.remove(tmp_path)
                raise

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {"step": "download", "message": "Download complete", "progress": 75}
                ),
            }

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "extract",
                        "message": "Extracting llama-server binary...",
                        "progress": 80,
                    }
                ),
            }

            install_dir = _os.path.expanduser("~/.local/bin")
            _os.makedirs(install_dir, exist_ok=True)

            if ext == "tar.gz":
                import tarfile as _tarfile

                with _tarfile.open(tmp_path, "r:gz") as tar:
                    members = tar.getmembers()
                    found_binary = False
                    for m in members:
                        if m.isdir():
                            continue
                        parts = m.name.split("/")
                        if len(parts) >= 2:
                            m.name = "/".join(parts[1:])
                        if parts[-1] in ("llama-server", "llama-server.exe"):
                            found_binary = True
                        tar.extract(m, path=install_dir)
                    if not found_binary:
                        raise RuntimeError(
                            "llama-server binary not found in archive"
                        )
            elif ext == "zip":
                import zipfile as _zipfile

                with _zipfile.ZipFile(tmp_path) as zf:
                    found_binary = False
                    for info in zf.infolist():
                        if info.is_dir():
                            continue
                        parts = info.filename.split("/")
                        if len(parts) >= 2:
                            flat_name = "/".join(parts[1:])
                        else:
                            flat_name = info.filename
                        if parts[-1] in ("llama-server", "llama-server.exe"):
                            found_binary = True
                        info.filename = flat_name
                        zf.extract(info, install_dir)
                    if not found_binary:
                        raise RuntimeError(
                            "llama-server binary not found in archive"
                        )

            _os.remove(tmp_path)

            binary_path = _os.path.join(install_dir, "llama-server")
            if _os.path.exists(binary_path):
                _os.chmod(binary_path, 0o755)

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {"step": "verify", "message": "Verifying installation...", "progress": 90}
                ),
            }

            import subprocess as _subprocess

            if not shutil.which("llama-server"):
                raise RuntimeError(
                    "llama-server binary not found in PATH after install"
                )

            version = "unknown"
            try:
                result = _subprocess.run(
                    ["llama-server", "--version"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                version = result.stdout.strip() or result.stderr.strip()
            except Exception:
                pass

            _srv._llamacpp_config["installed"] = True

            yield {
                "event": "install_complete",
                "data": json.dumps(
                    {
                        "status": "success",
                        "version": version,
                        "path": shutil.which("llama-server")
                        or _os.path.join(install_dir, "llama-server"),
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


@router.api_route("/local-backends/llamacpp/uninstall", methods=["GET", "POST"])
async def uninstall_llamacpp(request: Request) -> EventSourceResponse:
    """Uninstall llama-server binary with SSE streaming."""
    import shutil

    if not shutil.which("llama-server"):
        return EventSourceResponse(
            install_error_generator("llama.cpp is not installed", "NOT_INSTALLED")
        )

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "stop",
                        "message": "Stopping llama.cpp if running...",
                        "progress": 10,
                    }
                ),
            }

            if (
                _srv._llamacpp_process is not None
                and _srv._llamacpp_process.returncode is None
            ):
                _srv._llamacpp_process.terminate()
                try:
                    await asyncio.wait_for(_srv._llamacpp_process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    _srv._llamacpp_process.kill()
                    await _srv._llamacpp_process.wait()
                _srv._llamacpp_process = None
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {"step": "stop", "message": "llama.cpp stopped", "progress": 20}
                    ),
                }
            else:
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {"step": "stop", "message": "Not running", "progress": 20}
                    ),
                }

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "remove_binary",
                        "message": "Removing llama-server binary...",
                        "progress": 30,
                    }
                ),
            }

            binary_path = shutil.which("llama-server")
            if binary_path:
                try:
                    _os.remove(binary_path)
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "remove_binary",
                                "message": f"Removed {binary_path}",
                                "progress": 50,
                            }
                        ),
                    }
                except Exception as ex:
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "remove_binary",
                                "message": f"Could not remove {binary_path}: {ex}",
                                "progress": 50,
                            }
                        ),
                    }

            local_bin = _os.path.expanduser("~/.local/bin/llama-server")
            if _os.path.exists(local_bin) and (
                not binary_path or local_bin != binary_path
            ):
                try:
                    _os.remove(local_bin)
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "remove_binary",
                                "message": f"Removed {local_bin}",
                                "progress": 55,
                            }
                        ),
                    }
                except Exception:
                    pass

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "remove_state",
                        "message": "Removing state directory...",
                        "progress": 60,
                    }
                ),
            }

            state_dir = _os.path.expanduser("~/.local/share/llama-server")
            if _os.path.exists(state_dir):
                import shutil as _shutil

                _shutil.rmtree(state_dir, ignore_errors=True)
                yield {
                    "event": "install_log",
                    "data": json.dumps(
                        {
                            "step": "remove_state",
                            "message": f"Removed {state_dir}",
                            "progress": 70,
                        }
                    ),
                }

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {"step": "verify", "message": "Verifying removal...", "progress": 85}
                ),
            }

            if shutil.which("llama-server"):
                yield {
                    "event": "install_error",
                    "data": json.dumps(
                        {
                            "status": "error",
                            "message": "llama-server still found after removal attempt",
                            "code": "REMOVAL_FAILED",
                        }
                    ),
                }
                return

            _srv._llamacpp_config["installed"] = False

            yield {
                "event": "install_complete",
                "data": json.dumps(
                    {
                        "status": "success",
                        "message": "llama.cpp uninstalled",
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


@router.post("/local-backends/llamacpp/start")
async def start_llamacpp() -> JSONResponse:
    """Start llama-server as a managed subprocess."""
    import shutil

    if not shutil.which("llama-server"):
        return JSONResponse(
            {"status": "error", "message": "llama.cpp is not installed"},
            status_code=400,
        )

    if (
        _srv._llamacpp_process is not None
        and _srv._llamacpp_process.returncode is None
    ):
        return JSONResponse(
            {"status": "ok", "message": "llama.cpp is already running"}
        )

    if not _srv._llamacpp_serving_model:
        return JSONResponse(
            {
                "status": "error",
                "message": "No model configured. Use POST /llamacpp/serve to select a GGUF model first.",
            },
            status_code=400,
        )

    port = _srv._llamacpp_config.get("port", 8080)
    gpu_layers = _srv._llamacpp_config.get("gpu_layers", 0)
    context_size = _srv._llamacpp_config.get("context_size", 8192)
    flash_attn = _srv._llamacpp_config.get("flash_attn", False)

    if not _srv._is_port_available(port):
        return JSONResponse(
            {"status": "error", "message": f"Port {port} is already in use"},
            status_code=409,
        )

    cmd = [
        "llama-server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-m",
        _srv._llamacpp_serving_model,
    ]
    if gpu_layers != 0:
        cmd.extend(["-ngl", str(gpu_layers)])
    cmd.extend(["-c", str(context_size)])
    if flash_attn and gpu_layers > 0:
        cmd.extend(["--flash-attn", "1"])

    try:
        _srv._llamacpp_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as e:
        _srv._llamacpp_process = None
        return JSONResponse(
            {"status": "error", "message": f"Failed to start llama.cpp: {e}"},
            status_code=500,
        )

    _health_ok = False
    for _attempt in range(10):
        await asyncio.sleep(0.5)
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                resp = await client.get(f"http://localhost:{port}/v1/models")
                if resp.status_code == 200:
                    _health_ok = True
                    break
        except Exception:
            continue

    if _health_ok:
        _srv._llamacpp_config["installed"] = True
        _srv._llamacpp_restart_attempts = 0
        local_backend_manager.set_address("llama-cpp", f"localhost:{port}")
        asyncio.ensure_future(_srv.monitor_llamacpp_process())
        return JSONResponse(
            {
                "status": "ok",
                "message": f"llama.cpp started on port {port}",
            }
        )

    asyncio.ensure_future(_srv.monitor_llamacpp_process())
    return JSONResponse(
        {
            "status": "ok",
            "message": f"llama.cpp process started on port {port} (health check pending)",
        }
    )


@router.post("/local-backends/llamacpp/stop")
async def stop_llamacpp() -> JSONResponse:
    """Stop the managed llama-server subprocess."""
    if (
        _srv._llamacpp_process is None
        or _srv._llamacpp_process.returncode is not None
    ):
        return JSONResponse(
            {"status": "ok", "message": "llama.cpp is not running"}
        )

    try:
        _srv._llamacpp_process.terminate()
        try:
            await asyncio.wait_for(_srv._llamacpp_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            _srv._llamacpp_process.kill()
            await _srv._llamacpp_process.wait()
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": f"Failed to stop llama.cpp: {e}"},
            status_code=500,
        )

    _srv._llamacpp_process = None
    return JSONResponse({"status": "ok", "message": "llama.cpp stopped"})


@router.post("/local-backends/llamacpp/restart")
async def restart_llamacpp() -> JSONResponse:
    """Restart the managed llama-server subprocess."""
    stop_result = await stop_llamacpp()
    if stop_result.status_code != 200:
        return stop_result
    return await start_llamacpp()


@router.get("/local-backends/models/gguf")
async def list_gguf_models() -> JSONResponse:
    """List all .gguf files under ~/.cache/llmfit/models/ recursively."""
    models_dir = _os.path.expanduser("~/.cache/llmfit/models/")
    models: list[dict] = []

    if not _os.path.isdir(models_dir):
        return JSONResponse({"models": []})

    for dirpath, _dirnames, filenames in _os.walk(models_dir):
        for fname in filenames:
            if not fname.endswith(".gguf"):
                continue
            full_path = _os.path.join(dirpath, fname)
            try:
                size_bytes = _os.path.getsize(full_path)
            except OSError:
                size_bytes = 0
            rel_path = _os.path.relpath(full_path, models_dir)
            name = rel_path.replace(_os.sep, "/").removesuffix(".gguf")
            serving = (
                _srv._llamacpp_serving_model is not None
                and _os.path.realpath(_srv._llamacpp_serving_model)
                == _os.path.realpath(full_path)
            )
            models.append(
                {
                    "name": name,
                    "path": full_path,
                    "size_bytes": size_bytes,
                    "serving": serving,
                }
            )

    models.sort(key=lambda m: m["size_bytes"], reverse=True)
    return JSONResponse({"models": models})


@router.api_route("/local-backends/llamacpp/serve", methods=["GET", "POST"])
async def serve_llamacpp_model(request: Request) -> EventSourceResponse:
    """Start llama-server with a specific GGUF model. Streams loading progress via SSE."""
    import shutil

    try:
        body = await request.json()
    except Exception:
        body = {}
    qp = dict(request.query_params)
    model_input = body.get("model") or qp.get("model", "")
    port = int(
        body.get("port")
        or qp.get("port", _srv._llamacpp_config.get("port", 8080))
    )
    gpu_layers = int(body.get("gpu_layers") or qp.get("gpu_layers", 0))
    context_size = int(body.get("context_size") or qp.get("context_size", 8192))
    flash_attn = body.get("flash_attn", gpu_layers > 0)
    batch_size = int(body.get("batch_size") or qp.get("batch_size", 512))

    if not shutil.which("llama-server"):
        return EventSourceResponse(
            install_error_generator("llama.cpp is not installed", "NOT_INSTALLED")
        )

    if not model_input:
        return EventSourceResponse(
            install_error_generator("No model specified", "NO_MODEL")
        )

    model_path: str | None = None
    if _os.path.isfile(model_input):
        model_path = _os.path.abspath(model_input)
    elif model_input.endswith(".gguf"):
        models_dir = _os.path.expanduser("~/.cache/llmfit/models/")
        candidate = _os.path.join(models_dir, model_input)
        if _os.path.isfile(candidate):
            model_path = _os.path.abspath(candidate)
        else:
            for dirpath, _d, filenames in _os.walk(models_dir):
                if model_input in filenames:
                    model_path = _os.path.abspath(_os.path.join(dirpath, model_input))
                    break
    else:
        name_with_ext = (
            model_input if model_input.endswith(".gguf") else f"{model_input}.gguf"
        )
        models_dir = _os.path.expanduser("~/.cache/llmfit/models/")
        for dirpath, _d, filenames in _os.walk(models_dir):
            if name_with_ext in filenames:
                model_path = _os.path.abspath(
                    _os.path.join(dirpath, name_with_ext)
                )
                break

    if not model_path or not _os.path.isfile(model_path):
        return EventSourceResponse(
            install_error_generator(
                f"Model file not found: {model_input}", "MODEL_NOT_FOUND"
            )
        )

    _srv._llamacpp_config["port"] = port
    _srv._llamacpp_config["gpu_layers"] = gpu_layers
    _srv._llamacpp_config["context_size"] = context_size
    _srv._llamacpp_config["flash_attn"] = flash_attn
    _srv._llamacpp_config["batch_size"] = batch_size

    if (
        _srv._llamacpp_process is not None
        and _srv._llamacpp_process.returncode is None
    ):
        _srv._llamacpp_process.terminate()
        try:
            await asyncio.wait_for(_srv._llamacpp_process.wait(), timeout=5)
        except asyncio.TimeoutError:
            _srv._llamacpp_process.kill()
            await _srv._llamacpp_process.wait()
        _srv._llamacpp_process = None

    if not _srv._is_port_available(port):
        return EventSourceResponse(
            install_error_generator(f"Port {port} is already in use", "PORT_IN_USE")
        )

    cmd = [
        "llama-server",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "-m",
        model_path,
    ]
    if gpu_layers != 0:
        cmd.extend(["-ngl", str(gpu_layers)])
    cmd.extend(["-c", str(context_size)])
    if flash_attn and gpu_layers > 0:
        cmd.extend(["--flash-attn", "1"])
    if batch_size != 512:
        cmd.extend(["--batch-size", str(batch_size)])

    async def generate() -> AsyncGenerator[str, None]:
        try:
            yield {
                "event": "install_log",
                "data": json.dumps(
                    {
                        "step": "start",
                        "message": f"Starting llama-server with {_os.path.basename(model_path)}...",
                        "progress": 5,
                    }
                ),
            }

            _srv._llamacpp_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            _srv._llamacpp_serving_model = model_path

            yield {
                "event": "install_log",
                "data": json.dumps(
                    {"step": "loading", "message": "Loading model...", "progress": 10}
                ),
            }

            assert _srv._llamacpp_process.stderr is not None
            progress = 10
            async for raw_line in _srv._llamacpp_process.stderr:
                line = raw_line.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue

                if "loading model from" in line.lower():
                    progress = max(progress, 15)
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "loading",
                                "message": "Loading model into memory...",
                                "progress": progress,
                            }
                        ),
                    }
                elif "offloading" in line.lower() and "layers" in line.lower():
                    progress = min(progress + 15, 60)
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {"step": "gpu", "message": line.strip(), "progress": progress}
                        ),
                    }
                elif "buffer size" in line.lower() or "model buffer" in line.lower():
                    progress = min(progress + 20, 80)
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "loaded",
                                "message": line.strip(),
                                "progress": progress,
                            }
                        ),
                    }
                elif "listening on" in line.lower():
                    progress = 95
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {"step": "ready", "message": line.strip(), "progress": progress}
                        ),
                    }
                else:
                    progress = min(progress + 2, 90)
                    yield {
                        "event": "install_log",
                        "data": json.dumps(
                            {
                                "step": "loading",
                                "message": line.strip(),
                                "progress": progress,
                            }
                        ),
                    }

                if await request.is_disconnected():
                    _srv._llamacpp_process.terminate()
                    return

            await _srv._llamacpp_process.wait()

            for _attempt in range(5):
                await asyncio.sleep(1)
                try:
                    async with httpx.AsyncClient(timeout=2) as client:
                        resp = await client.get(f"http://localhost:{port}/v1/models")
                        if resp.status_code == 200:
                            _srv._llamacpp_config["installed"] = True
                            local_backend_manager.set_address(
                                "llama-cpp", f"localhost:{port}"
                            )
                            asyncio.ensure_future(_srv.monitor_llamacpp_process())
                            yield {
                                "event": "install_complete",
                                "data": json.dumps(
                                    {
                                        "status": "success",
                                        "model": _os.path.basename(model_path),
                                        "port": port,
                                    }
                                ),
                            }
                            return
                except Exception:
                    continue

            yield {
                "event": "install_error",
                "data": json.dumps(
                    {
                        "status": "error",
                        "message": "llama-server started but health check failed",
                        "code": "HEALTH_CHECK_FAILED",
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

    return EventSourceResponse(generate(), ping=15)


@router.put("/local-backends/llamacpp/config")
async def update_llamacpp_config(req: LlamacppConfigRequest) -> JSONResponse:
    """Update llama.cpp configuration. Returns warning if server is running."""
    running = (
        _srv._llamacpp_process is not None
        and _srv._llamacpp_process.returncode is None
    )

    warning = None
    if running:
        warning = "Config changes require restart to take effect"

    if req.port is not None:
        _srv._llamacpp_config["port"] = req.port
        local_backend_manager.set_address("llama-cpp", f"localhost:{req.port}")
    if req.gpu_layers is not None:
        _srv._llamacpp_config["gpu_layers"] = req.gpu_layers
    if req.context_size is not None:
        _srv._llamacpp_config["context_size"] = req.context_size
    if req.flash_attn is not None:
        _srv._llamacpp_config["flash_attn"] = req.flash_attn
    if req.batch_size is not None:
        _srv._llamacpp_config["batch_size"] = req.batch_size

    response: dict = {
        "status": "ok",
        "config": {
            "port": _srv._llamacpp_config.get("port", 8080),
            "gpu_layers": _srv._llamacpp_config.get("gpu_layers", 0),
            "context_size": _srv._llamacpp_config.get("context_size", 8192),
            "flash_attn": _srv._llamacpp_config.get("flash_attn", False),
            "batch_size": _srv._llamacpp_config.get("batch_size", 512),
        },
    }
    if warning:
        response["warning"] = warning

    return JSONResponse(response)
