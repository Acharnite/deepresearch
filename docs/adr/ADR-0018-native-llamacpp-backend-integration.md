# ADR-0018: Native llama.cpp Backend — Binary Lifecycle, GGUF Serving, and LiteLLM Integration

## Status

Accepted

**Version:** 1.4
**Last Updated:** 2026-06-29

## Context

### Problem

DeepeResearch has a gap: GGUF models can be downloaded via `POST /api/local-backends/models/download` (which runs `llmfit download <repo> --output-dir ~/.cache/llmfit/models/`) and land on disk, but there is **no way to serve them**. The downloaded GGUF files sit unused in `~/.cache/llmfit/models/`.

The `BACKEND_DEFINITIONS` in `server.py` (line 1624) already lists `llama-cpp` as a known backend with port 8080, but its `binary` field is `None`:

```python
{
    "name": "llama-cpp",
    "label": "llama.cpp",
    "description": "Lightweight CPU/GPU inference",
    "port": 8080,
    "path": "/v1/models",
    "binary": None,  # ← No managed binary lifecycle!
}
```

ADR-0005 covers auto-discovery of llama.cpp on port 8080 and LiteLLM routing via `custom/openai` with `api_base`, but it does **not** cover managing the llama.cpp binary lifecycle — install, start, stop, uninstall, or GGUF model serving.

### Current State

| Concern | Status |
|---------|--------|
| **BACKEND_DEFINITIONS** | llama-cpp listed with `binary: None`, port 8080 |
| **Auto-discovery** | Probes `localhost:8080/v1/models` via `_probe_backend()` — reports `installed: null` (no binary check) |
| **LiteLLM routing** | `llama-cpp/` prefix → `http://localhost:8080/v1` via `custom/openai` pattern |
| **GGUF download** | `llmfit download` → file lands in `~/.cache/llmfit/models/` — stops there |
| **Ollama lifecycle** | Fully implemented: install, start, stop, uninstall, pull (SSE streamed) |
| **Frontend** | Ollama management card in Settings → Local Models tab with status, install/uninstall, start/stop, shared log viewer |
| **llamafit** | Not part of llama.cpp — separate tool. This ADR is about llama.cpp's `llama-server` binary. |

### Key Forces

1. **Gap closure** — GGUF downloads are useless without serving. This ADR connects the two.
2. **Pattern reuse** — Ollama lifecycle (install/start/stop/uninstall/status) is the proven template. Reuse it for llama.cpp.
3. **Lightweight** — llama.cpp is a single ~10 MB static binary. Unlike Ollama, no systemd service, no daemon, no user creation. Simpler lifecycle.
4. **GPU diversity** — llama.cpp releases ship variants for CPU, CUDA 12, CUDA 13, Vulkan, ROCm, Metal, SYCL, OpenVINO. Platform detection is critical.
5. **Multiple GGUF files** — Users may download multiple GGUF models. Need a mechanism to pick which one to serve.
6. **Process management** — llama-server runs as a subprocess of the deepresearch server. Must handle graceful shutdown, crash recovery, and port conflicts.
7. **LiteLLM integration** — Running llama-server must auto-register as a LiteLLM-accessible backend for the model selector.

## Decision

### Approach

1. **Managed binary lifecycle** — Download pre-built `llama-server` binary from GitHub releases (`ggml-org/llama.cpp`). Store in `~/.local/bin/llama-server`. Platform-aware download based on OS + arch + GPU detection.

2. **Per-user installation** — Binary goes to `~/.local/bin/` (no sudo needed). Follows the same pattern as llmfit's `--local` install.

3. **Single llama-server instance** — One server at a time serving one GGUF model. Model switching = stop + restart with different GGUF. Port configurable (default 8080).

4. **Process lifecycle via global variable** — Track the subprocess with `_llamacpp_process: asyncio.subprocess.Process | None = None`. Graceful shutdown via SIGTERM, fallback to SIGKILL after 5s timeout. Auto-cleanup on deepresearch server shutdown via FastAPI's `lifespan` async context manager.

5. **GGUF model management** — `GET /api/local-backends/models/gguf` lists all `.gguf` files in `~/.cache/llmfit/models/`. `POST /api/local-backends/llamacpp/serve` starts llama-server with a selected GGUF. `POST /api/local-backends/llamacpp/stop` stops it.

6. **LiteLLM auto-registration** — When llama-server is running and a model is active, register it in the LiteLLM provider routes dynamically so it appears in the model selector dropdown.

7. **Frontend card** — llama.cpp management card in Settings → Local Models tab, parallel to the Ollama card, with status, install/uninstall, start/stop, GGUF model list, port config, shared log viewer.

### Platform Detection for Binary Download

The llama.cpp GitHub releases page (https://github.com/ggml-org/llama.cpp/releases) publishes per-commit builds with the naming convention:

```
llama-{tag}-bin-{platform}-{variant}-{arch}.{ext}
```

| Platform | Variants | Extension | Priority |
|----------|----------|-----------|----------|
| macOS arm64 | CPU (Metal built-in) | tar.gz | 1 (Apple Silicon) |
| macOS x64 | CPU (Metal built-in) | tar.gz | 2 (Intel Mac) |
| Ubuntu x64 | CPU, Vulkan, ROCm 7.2, OpenVINO, SYCL | tar.gz | 3a (Linux x86_64) |
| Ubuntu arm64 | CPU, Vulkan | tar.gz | 3b (Linux ARM) |
| Windows x64 | CPU, CUDA 12.4, CUDA 13.3, Vulkan, OpenVINO, SYCL, HIP | zip | 4 (Windows) |

**Resolution order for Linux:**
1. Detect AMD GPU → `ubuntu-rocm-7.2-x64` (ROCm variant)
2. Detect Vulkan support → `ubuntu-vulkan-x64`
3. Detect NVIDIA GPU → use CPU binary (`ubuntu-x64`) with `-ngl N` flag for GPU offload
4. Fall back → `ubuntu-x64` CPU variant
5. If ARM → `ubuntu-arm64` CPU variant

**For macOS:**
- Apple Silicon (arm64) → `macos-arm64` (Metal enabled by default)
- Intel (x64) → `macos-x64` (Metal enabled by default)

### Binary Storage

```
~/.local/bin/
  ├── llama-server          # Symlink or actual binary
  └── llama-bench           # Optional benchmark tool (extracted from same tarball)
```

### Version Pinning

Pin to the **latest stable-ish release**. Use the `latest` GitHub API to resolve the latest tag, then download that specific tag. Allow explicit version override in settings.

**Auto-update:** Not automatic. User clicks "Install" which always downloads latest. No background auto-update (avoids surprises). A "Check for updates" button can re-download.

### GGUF Model Directory

```
~/.cache/llmfit/models/
  ├── *.gguf                # Downloaded GGUF files
  ├── <model-name>/
  │   ├── *.gguf            # Multi-file models in subdirectories
  │   └── mmproj-*.gguf     # Multimodal projection files
  └── ...
```

llama.cpp's router mode supports `--model-dir` which loads all `.gguf` files from a directory. For single-model mode, `-m <path>` points to a specific file.

### llama-server Process Lifecycle

**Start command:**
```bash
~/.local/bin/llama-server \
  -m ~/.cache/llmfit/models/<model-file>.gguf \
  --host 127.0.0.1 \
  --port <port> \
  -c <context-size> \
  -ngl <gpu-layers> \
  --temp 0.7 \
  --flash-attn 1
```

Default values:
- port: 8080 (configurable)
- context-size: 8192 (configurable)
- gpu-layers: 0 (CPU only, configurable; -1 = all layers)
- flash-attn: enabled if GPU layers > 0

**Stop command:** SIGTERM → wait 5s → SIGKILL

**Process tracking:**
- Global `_llamacpp_process: asyncio.subprocess.Process | None` variable for process reference
- Process group tracking for clean subprocess cleanup
- FastAPI `lifespan` shutdown handler to stop llama-server on shutdown
- Health check: Probe `GET /v1/models` on configured port every 30s
- Auto-restart: On crash, attempt restart up to 3 times with exponential backoff (5s, 15s, 45s), then give up and report error
- Add a `_shutting_down: bool` flag that the health check checks before attempting to restart. The lifespan shutdown handler sets this flag before sending SIGTERM.

**Port conflict detection:** Before starting, probe the configured port. If something is already listening, fail with a clear error message showing port and suggesting a different port.

### Model Switching

When the user selects a different GGUF model to serve:

1. `POST /api/local-backends/llamacpp/stop` — stop current server
2. `POST /api/local-backends/llamacpp/serve` — `{"model": "path/to/model.gguf"}` — start with new model

The frontend can combine this into a single "Switch Model" action.

### LiteLLM Integration

When llama-server is running with a model loaded:

1. **Provider route registration:** The `llama-cpp` entry in `PROVIDER_ROUTES` (client.py:97-102) already exists with `local_backend: True` and `local_backend_port: 8080`. The `_resolve_api_base()` function (client.py:356) dynamically resolves via `local_backend_manager.get_address("llama-cpp")`. This already works — but the **port** needs to be updated dynamically if the user changes it.

2. **Dynamic port update:** When the user changes the port in settings, `local_backend_manager.set_address("llama-cpp", "localhost:{port}")` is called. The next LLM call automatically uses the new address.

3. **Model selector visibility:** A running llama-server instance exposes its loaded model via `GET /v1/models`. The `/api/models` endpoint should automatically include this model in the dropdown as `llama-cpp/<model-name>` (matching the prefix expected by `PROVIDER_ROUTES`).

4. **Current model tracking:** The `/api/local-backends/llamacpp/status` endpoint returns the currently loaded model ID (parsed from `GET /v1/models` response), which the frontend displays.

### API Endpoints

| Method | Endpoint | Purpose | SSE? |
|--------|----------|---------|------|
| `GET` | `/api/local-backends/llamacpp/status` | Installed? Running? Version? Port? Current model? | No |
| `POST` | `/api/local-backends/llamacpp/install` | Download binary from GitHub releases | Yes |
| `POST` | `/api/local-backends/llamacpp/uninstall` | Stop server, remove binary, remove config (SSE progress) | Yes |
| `POST` | `/api/local-backends/llamacpp/start` | Start llama-server with last-served model | No |
| `POST` | `/api/local-backends/llamacpp/stop` | Stop llama-server gracefully | No |
| `POST` | `/api/local-backends/llamacpp/restart` | Stop + start | No |
| `GET` | `/api/local-backends/models/gguf` | List GGUF files in `~/.cache/llmfit/models/` | No |
| `POST` | `/api/local-backends/llamacpp/serve` | Start llama-server with specific GGUF model | Yes (SSE) |
| `PUT` | `/api/local-backends/llamacpp/config` | Update port, context size, GPU layers | No |
| `PUT` | `/api/local-backends/llamacpp/address` | Set custom address override (for remote/Docker llama.cpp) | No |
| `GET` | `/api/local-backends/llamacpp/address` | Get current address override | No |

### Request/Response Schemas

**Status response:**
```json
{
    "installed": true,
    "running": true,
    "version": "b9739",
    "port": 8080,
    "binary_path": "/home/user/.local/bin/llama-server",
    "active_model": {
        "path": "/home/user/.cache/llmfit/models/qwen3.5-9b-Q4_K_M.gguf",
        "name": "qwen3.5-9b-Q4_K_M"
    },
    "gpu_layers": 0,
    "context_size": 8192,
    "pid": 12345
}
```

**Serve request:**
```json
{
    "model": "qwen3.5-9b-Q4_K_M",
    "port": 8080,
    "gpu_layers": 0,
    "context_size": 8192,
    "flash_attn": true
}
```

**Config update:**
```json
{
    "port": 8081,
    "gpu_layers": -1,
    "context_size": 16384,
    "flash_attn": true
}
```

> **Note:** When the port is changed, also call `local_backend_manager.set_address("llama-cpp", "localhost:{new_port}")` to keep LiteLLM routing in sync.

**GGUF list response:**
```json
{
    "models": [
        {
            "name": "qwen3.5-9b-Q4_K_M",
            "path": "/home/user/.cache/llmfit/models/qwen3.5-9b-Q4_K_M.gguf",
            "size_bytes": 5100000000,
            "serving": true
        },
        {
            "name": "llama-3.2-3b-Q4_K_M",
            "path": "/home/user/.cache/llmfit/models/llama-3.2-3b-Q4_K_M.gguf",
            "size_bytes": 1900000000,
            "serving": false
        }
    ]
}
```

### Implementation Plan

#### Phase 1: Binary Download + Lifecycle Endpoints + Basic Status

**Scope:** Core infrastructure — download llama-server binary, the full lifecycle (install/uninstall/start/stop/restart), and status endpoint.

**Server changes:**
- Add `LLAMACPP_BINARY_DIR` constant (`~/.local/bin/`)
- Add `LLAMACPP_STATE_DIR` constant (`~/.local/share/llama-server/`)
- Add `_llamacpp_process: asyncio.subprocess.Process | None` global variable
- Add `_llamacpp_config` dict for current runtime config (port, model path, etc.)

**Endpoints:**
- `GET /api/local-backends/llamacpp/status` — check `shutil.which("llama-server")`, check process alive + port probe
- `POST /api/local-backends/llamacpp/install` — platform detection → download URL construction → curl/httpx download → tar.gz/zip extraction → binary placement → SSE stream
- `POST /api/local-backends/llamacpp/uninstall` — Stop llama-server (SIGTERM→SIGKILL), remove binary from `~/.local/bin/llama-server`, remove config. SSE streaming for progress.
- `POST /api/local-backends/llamacpp/start` — start llama-server with last served model (or require model param). If no model has ever been served, return 400 with "No model configured. Use POST /serve to select a GGUF model first."
- `POST /api/local-backends/llamacpp/stop` — SIGTERM → wait → SIGKILL fallback
- `POST /api/local-backends/llamacpp/restart` — stop + start

**Update BACKEND_DEFINITIONS:**
```python
{
    "name": "llama-cpp",
    "label": "llama.cpp",
    "description": "Lightweight CPU/GPU inference",
    "port": 8080,
    "path": "/v1/models",
    "binary": "llama-server",
}
```
This makes `_probe_backend()` set `installed: true` when `llama-server` is on PATH.

**Key implementation detail — platform detection:**
```python
import platform
import shutil

def _detect_llamacpp_platform() -> dict:
    """Return download info for the current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        if machine == "arm64":
            return {"asset": "macos-arm64", "ext": "tar.gz"}
        return {"asset": "macos-x64", "ext": "tar.gz"}

    if system == "linux":
        if machine == "aarch64":
            return {"asset": "ubuntu-arm64", "ext": "tar.gz"}
        # Check for GPU — priority: ROCm > Vulkan > NVIDIA (CPU binary with -ngl N)
        if shutil.which("rocm-smi"):
            return {"asset": "ubuntu-rocm-7.2-x64", "ext": "tar.gz"}
        if shutil.which("nvidia-smi"):
            # Use CPU binary with -ngl N for GPU offload (no CUDA variant on Linux)
            return {"asset": "ubuntu-x64", "ext": "tar.gz"}
        return {"asset": "ubuntu-x64", "ext": "tar.gz"}

    if system == "windows":
        # Minimal support for WSL/Cygwin; native Windows later
        return {"asset": "win-cpu-x64", "ext": "zip"}

    raise RuntimeError(f"Unsupported platform: {system} {machine}")
```

**Download URL construction:**
```python
async def _get_latest_llamacpp_tag() -> str:
    """Resolve the latest release tag from GitHub API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
        )
        data = resp.json()
        return data["tag_name"]  # e.g. "b9739"

def _build_download_url(tag: str, platform_info: dict) -> str:
    asset = platform_info["asset"]
    ext = platform_info["ext"]
    return (
        f"https://github.com/ggml-org/llama.cpp/releases/download/"
        f"{tag}/llama-{tag}-bin-{asset}.{ext}"
    )
```

**Binary extraction:**
- `tar.gz`: Extract with `tar -xzf <file> --strip-components=1 -C <dest_dir>` to handle the versioned subdirectory prefix (e.g., `llama-b9739/llama-server` → `llama-server`).
- `zip`: Use Python's `zipfile` module
- Binary goes to `~/.local/bin/llama-server`, ensure it's executable (`chmod +x`)

#### Phase 2: GGUF Model Management + Serve Action

**Scope:** After `llmfit download` succeeds, auto-discover the GGUF file. Allow selecting and serving a specific GGUF model.

**Server changes:**
- Add `GET /api/local-backends/models/gguf` — scan `~/.cache/llmfit/models/` for `.gguf` files
- Add `POST /api/local-backends/llamacpp/serve` — takes `{"model": "filename.gguf"}` → stop existing → start with new model → SSE stream loading progress

**Loading progress SSE (from llama-server stderr):**
llama-server outputs loading progress to stderr:
```
load_model: loading model from /path/to/model.gguf
llm_load_tensors: offloading 0 layers to GPU
llm_load_tensors: CPU_Mapped model buffer size = X MB
```
Parse these lines and emit SSE events.

**Integration with download flow:**
When `POST /api/local-backends/models/download` completes successfully (the `llmfit download` path), the response should now include a field like `"gguf_available": true` and the frontend should offer to serve the model immediately:
```
✅ Download complete! [Serve with llama.cpp] [Close]
```

#### Phase 3: LiteLLM Integration + Model Selector

**Scope:** Running llama-server model appears in the model selector dropdown.

**Server changes:**
- After starting llama-server, probe `GET /v1/models` to get the model ID
- Expose this model in the `/api/models` endpoint responses
- Add a field to the settings/config that remembers the last served model

**LiteLLM client:**
- Update `PROVIDER_ROUTES["llama-cpp"]` to use dynamic port from `local_backend_manager`
- The existing `_resolve_api_base()` already handles this — but verify the address update triggers work

**`/api/models` endpoint update:**
When llama-server is running, include its model in the response:
```json
{
    "llamacpp/qwen3.5-9b-Q4_K_M": {
        "id": "llamacpp/qwen3.5-9b-Q4_K_M",
        "provider": "llama-cpp",
        "local": true,
        "context_length": 8192,
        "command": ""
    }
}
```

#### Phase 4: Frontend UI

**Scope:** llama.cpp management card in Settings → Local Models tab, GGUF model list, port configuration.

**dashboard.html changes:**
Add a new section in the Local Models tab (`#tab-local-models`), after the Ollama section:

```html
<div class="card-header">
    <span>🦙 llama.cpp</span>
    <span id="llamacppStatus">...</span>
</div>
<div id="llamacppActions">
    <button id="installLlamacppBtn" class="btn btn-primary btn-sm">⬇ Install llama.cpp</button>
</div>
<div id="llamacppConfig">
    <label>Port: <input type="number" id="llamacppPort" value="8080" /></label>
    <label>GPU Layers: <input type="number" id="llamacppGpuLayers" value="0" /></label>
    <label>Context Size: <input type="number" id="llamacppCtxSize" value="8192" /></label>
</div>
<div id="ggufModelList"></div>
```

Reuse the shared log viewer component (`#ollamaInstallLog` / `#ollamaInstallOutput`) — rename to something shared, or just reuse the same DOM IDs.

**settings.js changes:**
Add management functions following the Ollama pattern:

| Function | What it does |
|----------|-------------|
| `checkLlamacppStatus()` | Fetch status, update UI |
| `installLlamacpp()` | Create EventSource for install SSE |
| `uninstallLlamacpp()` | POST uninstall, confirm |
| `manageLlamacpp(action)` | POST start/stop/restart |
| `loadGgufModels()` | GET GGUF list, render model rows |
| `serveGgufModel(name)` | POST serve with model name |
| `updateLlamacppConfig()` | PUT config (port, GPU layers, etc.) |

**GGUF Model List UI:**
```
┌──────────────────────────────────────────────┐
│ 📦 Downloaded GGUF Models                    │
│                                              │
│ ┌──────────────────────────────────────────┐ │
│ │ qwen3.5-9b-Q4_K_M.gguf  (4.8 GB)  ▶ Serve│ │ (green button)
│ │ llama-3.2-3b-Q4_K_M.gguf (1.8 GB) ▶ Serve│ │
│ └──────────────────────────────────────────┘ │
│                                              │
│ Current: qwen3.5-9b-Q4_K_M on port 8080     │
│              ⏹ Stop  🔄 Restart              │
└──────────────────────────────────────────────┘
```

**api.js changes:**
Add fetch wrappers:
```javascript
export async function fetchLlamacppStatus() { ... }
export function getLlamacppInstallURL() { ... }
export async function uninstallLlamacpp() { ... }
export async function startLlamacpp() { ... }
export async function stopLlamacpp() { ... }
export async function restartLlamacpp() { ... }
export async function fetchGgufModels() { ... }
export async function serveGgufModel(modelName, config) { ... }
export async function updateLlamacppConfig(config) { ... }
```

### Server Shutdown Behavior

On deepresearch server shutdown (SIGTERM/SIGINT):
1. Read `_llamacpp_process` global variable
2. Send SIGTERM to llama-server process
3. Wait 5 seconds
4. If still alive, send SIGKILL
5. Set `_llamacpp_process = None` and clean up process state
6. Log shutdown

This is handled via FastAPI's `lifespan` async context manager in `run_server()`. The shutdown phase of the lifespan handler performs cleanup — this is the modern async-safe pattern for FastAPI, preferable to `atexit` which runs outside the asyncio event loop.

### Multiple GGUF Models

**Decision: One model at a time.** This keeps the implementation simple and matches the expected use case — users typically want to run one local model during a research session.

If users need multiple models:
- They can stop and restart with a different model
- Advanced users can run additional llama-server instances independently (outside deepresearch) and point the custom address setting at them
- llama.cpp's router mode (no `-m` flag, uses `--model-dir`) is a future enhancement — it loads multiple models and routes requests — but introduces complexity around memory management and model selection in the UI

### Pre-existing llama.cpp Installation

Detection: `shutil.which("llama-server")` already works. If found:
- Set `installed: true` in status
- Do NOT overwrite the user's binary
- Show version from `llama-server --version`
- Skip the install button, show start/stop controls instead
- Check if the user's `llama-server` is on PATH vs our managed location

If the user's `llama-server` is at a different path, we still attempt to manage it via the same start/stop API, using whichever binary is on PATH.

## Consequences

### Positive

1. ✅ **Closes the GGUF gap** — downloaded GGUF models can finally be served, completing the download-to-inference pipeline
2. ✅ **Pattern reuse** — follows the battle-tested Ollama lifecycle pattern (install/start/stop/uninstall/status) with SSE streaming
3. ✅ **No sudo needed** — binary goes to `~/.local/bin/`, same pattern as llmfit
4. ✅ **Cross-platform** — llama.cpp provides pre-built binaries for Linux (x86_64, ARM), macOS (Apple Silicon, Intel), and Windows (CPU, CUDA 12, CUDA 13, Vulkan, ROCm)
5. ✅ **Lightweight** — ~10 MB binary vs Ollama's ~1 GB installation with systemd service
6. ✅ **GPU acceleration** — Built-in support for CUDA, Metal, Vulkan, ROCm via variant selection at download time
7. ✅ **Clean process management** — Global asyncio process variable + SIGTERM/SIGKILL + health checks + lifespan cleanup
8. ✅ **LiteLLM integration** — Existing provider routing for `llama-cpp/` model prefix works with dynamic port resolution
9. ✅ **Model selector visibility** — Running llama-server model appears in dropdown as `llamacpp/<model-name>`

### Negative

1. ❌ **One model at a time** — Cannot serve multiple GGUF models simultaneously through managed llama-server
2. ❌ **Binary size per platform** — CUDA variants are ~125 MB (vs CPU ~12 MB), takes longer to download
3. ❌ **No built-in model download** — Still dependent on llmfit for GGUF downloads (llama.cpp's `-hf` flag could be an alternative in the future)
4. ❌ **No streaming model load progress for CUDA** — llmfit download already has SSE; the `llama-server serve` step adds another layer but has limited progress signals

### Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| llama.cpp GitHub API rate limit | Cannot resolve latest tag | Cache tag for 1 hour; fall back to hardcoded tag |
| GitHub release asset URL change | Download fails if URL pattern changes | Pin to known-working URL template; log clear error to update |
| CUDA version mismatch | CUDA binary crashes if wrong NVIDIA driver | Default to CPU variant; let user pick GPU variant in settings |
| Port conflict (8080 already in use) | llama-server fails to start | Probe port before starting; suggest alternative |
| llama-server crash during research | Research fails mid-session | Health check + auto-restart (3 attempts); report error to user |
| Large GGUF load time | Server unavailable for 30-60s while loading | SSE progress stream; loading state in UI |
| User kills deepresearch server | llama-server orphaned | FastAPI lifespan handler; process cleanup; re-parent detection on restart |
| Existing llama.cpp installation | Conflicts with managed version | Detect existing binary; don't overwrite; use PATH binary instead |
| No GGUF files downloaded | "Serve" button does nothing | Disable serve button when GGUF list is empty; prompt to download first |

### Updates to Other ADRs

**ADR-0005:** Add a Phase 4 for llama.cpp binary lifecycle integration. The existing ADR-0005 covers auto-discovery and LiteLLM routing — this ADR fills the binary management gap.

**BACKEND_DEFINITIONS:** Update `llama-cpp` entry to set `"binary": "llama-server"` so `_probe_backend()` correctly detects installed status.

### Documentation

Primary documentation: llama.cpp official README at https://github.com/ggml-org/llama.cpp

Key commands reference for the managed `llama-server`:
```
# Binary info
llama-server --version

# Server with model
llama-server -m <gguf> --host 127.0.0.1 --port <port> -c <ctx> -ngl <layers>

# Health check
curl http://localhost:<port>/v1/models

# Chat completion
curl http://localhost:<port>/v1/chat/completions \
  -d '{"model":"<model-id>","messages":[{"role":"user","content":"Hello"}]}'
```

Known gotchas:
- llama.cpp's `/v1/chat/completions` expects the model ID in the body (same as OpenAI API)
- The model name exposed at `/v1/models` is the base filename without `.gguf`
- Flash attention is `--flash-attn 1` or `-fa` (boolean flag)
- GPU offload uses `-ngl N` (layers), with `-1` meaning "all layers"
- On macOS, Metal is enabled by default — no special flag needed

### Recommended Model for Research Agent

**Recommended: Meta Llama 3.1 8B Instruct (Q6_K quantization)**

Rationale:

1. **No thinking/tool conflict** — Llama 3.1 has no reasoning/thinking mode, so tool calling works natively without the `enable_thinking` workaround that breaks Qwen3.
2. **Native tool calling** — llama.cpp has a dedicated tool calling handler for Llama 3.1 with 89% accuracy on ToolBench.
3. **VRAM fit** — Q6_K is ~6GB, plus ~2.7GB for 16K context = ~8.7GB total, well within 11GB VRAM.
4. **Proven** — Most widely tested model with llama.cpp's OpenAI-compatible server.

**Models to avoid for research agent use:**

| Model | Problem |
|-------|---------|
| Qwen3 8B | `enable_thinking=true` puts tool calls in `reasoning_content` (empty `content`). `enable_thinking=false` prevents tool calls entirely. Upstream bug (#20837, #21158, #20809). |
| Gemma 4 | Same thinking+tools conflict — forces reasoning trace by default. |
| Qwen 3.5 | Same thinking+tools conflict despite top benchmarks. |

**Alternative options (if Llama 3.1 doesn't meet quality needs):**

- Qwen 2.5 7B — No reasoning mode, Hermes-style tool format, well-supported
- Mistral Nemo 12B — Native llama.cpp handler, ~8GB VRAM at Q4
- Hermes 2/3 — Purpose-built for function calling

**Key code change needed:** When serving Llama 3.1 8B, the `chat_template_kwargs: {"enable_thinking": false}` in `client.py:749-754` is unnecessary (Llama 3.1 ignores it) but harmless. It should be conditioned on the model family in the future.

## Related Issues

- ADR-0005: Local LLM Backends — Auto-Discovery, Installation, and LiteLLM Routing (parent context)
- Ollama lifecycle implementation in server.py (lines 1845-2475) — pattern to follow
- BACKEND_DEFINITIONS in server.py (line 1624-1665) — needs update
- PROVIDER_ROUTES in client.py (line 97-102) — llama-cpp route already exists

## Open Questions

1. Should we support `llama.cpp` router mode (`--model-dir`) for multi-model serving? → Decision: deferred. Phase 1 is single-model.
2. Should we support the `-hf` flag for direct HuggingFace downloads via llama-server? → Decision: Accepted per ADR-0020. The `-hf` flag is the primary model acquisition mechanism. llmfit download is deprecated.
3. CUDA variant selection — should we auto-detect CUDA version with `nvidia-smi`? → Yes, implement in Phase 1 with fallback to CPU variant.
4. Should the full tarball be extracted or just `llama-server`? → Extract only `llama-server` (and optionally `llama-bench`). No need for other tools.
5. How to handle `~/.local/bin` not being on PATH? → Add it if missing, or use full path for managed binary. The `_probe_backend()` function should check both PATH and `~/.local/bin/llama-server`.

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-06-29 | 1.4 | Phase 2-3 frontend completed: Lifecycle controls moved to Local Backends tab (#106). Streamlined Serve & Connect with auto-refresh model dropdown (#107). LiteLLM integration: serving model appears in /api/models dropdown automatically. Toast notifications on serve/stop state changes. |
| 2026-06-27 | 1.3 | Resolved `-hf` deferred decision: Accepted per ADR-0020. `-hf` is now the primary model acquisition mechanism; llmfit download deprecated. |
| 2026-06-23 | 1.2 | Added recommended model section (Llama 3.1 8B Q6_K). Documented thinking+tools conflict for Qwen3/Gemma4. |
| 2026-06-21 | 1.1 | Phase 2+3 implemented: GGUF model listing, llama-server serve endpoint, config management, /api/models registration |
| 2026-06-20 | 1.0 | Initial version |
