---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0005: Auto-Install and Auto-Discover Local LLM Backends

## Status

Proposed

**Version:** 1.0
**Last Updated:** 2026-06-15

## Context

DeepeResearch currently supports cloud-based LLM providers (OpenAI, Anthropic, OpenRouter, Groq, etc.) via API keys, and has basic Ollama integration (localhost:11434). Users want to run research locally without paying for API calls, using open-source models through local inference backends.

### Current State
- Ollama auto-discovered at localhost:11434 via `/api/tags`
- Custom local endpoints configurable via Settings tab (llama.cpp, vLLM)
- No auto-install capability — users must install backends manually
- No auto-discovery beyond Ollama — llama.cpp, vLLM, SGLang require manual URL entry
- No installation UI — terminal-only setup

### Problem
1. Users must manually install and configure local backends (Ollama, vLLM, llama.cpp, SGLang)
2. Each backend has different installation methods (pip, binary, Docker)
3. Users don't know which backend to choose for their hardware
4. No feedback during installation (silent failures, unclear errors)
5. Auto-discovery only works for Ollama — other backends require manual configuration

### Key Forces
1. Cross-platform support (Linux, macOS, Windows WSL)
2. Hardware diversity (CPU-only, NVIDIA GPU, Apple Silicon, AMD)
3. Backend complexity (Ollama = simple, vLLM = Docker, llama.cpp = binary, SGLang = pip+torch)
4. User experience — install should be one-click from the web UI
5. Must not break existing Ollama integration
6. Safety — auto-install should not overwrite existing installations

## Decision

### Implementation Phases

| Phase | Scope | Timeline |
|-------|-------|----------|
| Phase 1 | Ollama auto-install + custom addresses | Week 1 |
| Phase 2 | llama.cpp binary download | Week 2 |
| Phase 3 | vLLM + SGLang (pip install) | Week 3 |
| Phase 4 | Docker alternative + AMD support | Week 4 |

Each phase is independently testable and deployable.

### Backend Installation Priority

| Priority | Backend | Install Method | Hardware | Complexity |
|----------|---------|---------------|----------|------------|
| 1 | **Ollama** | Download `install.sh` to temp file, execute via `sh` | CPU/GPU (auto-detect) | Low |
| 2 | **llama.cpp** | Pre-built binary download via GitHub releases | CPU/GPU (auto-detect) | Medium |
| 3 | **vLLM** | `pip install vllm` (requires CUDA) | NVIDIA GPU only | High |
| 4 | **SGLang** | `pip install sglang[all]` (requires CUDA) | NVIDIA GPU only | High |

### Web UI Installation Flow

1. **Settings Tab → Local Models** section shows detected backends:
   - 🟢 Running (green badge)
   - 🟡 Installed but not running (yellow badge)
   - 🔴 Not installed (red badge) with "Install" button

2. **"Install" button** triggers backend-specific installation:
   - Shows a modal with **live log output** (SSE stream from server)
   - Installation runs as a background task on the server
   - Real-time progress: downloading, extracting, verifying
   - Success/error status with clear messaging

3. **"Uninstall" button** for installed backends:
   - Confirms with user (shows disk space to be reclaimed)
   - Stops the backend if running
   - Removes installed files
   - Resets state to `not_installed`

4. **"Start" button** for installed-but-stopped backends:
   - Starts the backend as a subprocess
   - Shows startup logs
   - Verifies connectivity after start

5. **"Stop" button** for running backends:
   - Graceful shutdown
   - Process cleanup

### Auto-Discovery Protocol

On startup and on-demand, the server probes backends in this order:

1. **User-configured addresses** — Check each backend's saved custom address first
2. **Default ports** — Probe `localhost:<default-port>` for each backend

| Backend | Default Port | Probe Endpoint | Response |
|---------|:------------:|----------------|----------|
| Ollama | 11434 | `GET /api/tags` | `{"models": [...]}` |
| llama.cpp | 8080 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| vLLM | 8000 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| SGLang | 30000 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |

If a custom address is configured, it takes priority over the default port. The probe is a lightweight HTTP GET with a 2-second timeout.

### Custom Address Configuration

Users may run backends on non-standard addresses, remote machines, or Docker containers with port mapping. Each backend supports a configurable `host:port` address.

#### Configuration Model

```python
class LocalBackendConfig(BaseModel):
    name: str                           # "ollama", "llamacpp", "vllm", "sglang"
    address: str = ""                   # Custom host:port (e.g. "192.168.1.50:11434")
    auto_install: bool = True           # Allow auto-install if not found
    enabled: bool = True                # Enable/disable this backend
    installed_version: str | None = None  # Detected backend version
    last_checked: str | None = None     # ISO timestamp of last health check
```

Address resolution order:
1. User-configured `address` (from Settings tab or config file)
2. Auto-discovered address (from probe scan)
3. Default address (`localhost:<default-port>`)

#### Web UI — Address Configuration

In Settings → Local Models, each backend card has:

```
┌──────────────────────────────────────────────────┐
│ 🟢 Ollama                                        │
│ Status: Running on localhost:11434                │
│                                                    │
│ Address: [localhost:11434          ] [Test] [Save]│
│ ☑ Enable auto-install if not found                │
│                                                    │
│ [Start] [Stop] [Logs] [Install]                  │
└──────────────────────────────────────────────────┘
```

- **Address input field** — editable, shows current address
- **"Test" button** — probes the address and shows connectivity status
- **"Save" button** — persists the custom address
- If user changes address to a non-default, auto-discovery still probes defaults AND the custom address

#### Remote Backend Support

Custom addresses enable running inference on remote machines:

| Scenario | Address | Notes |
|----------|---------|-------|
| Local Ollama | `localhost:11434` | Default |
| Remote Ollama | `192.168.1.50:11434` | Same network |
| Docker Ollama | `localhost:11434` | Port mapped |
| Remote vLLM | `gpu-server.local:8000` | Network inference |
| SSH tunnel | `localhost:8000` | Via `ssh -L 8000:localhost:8000` |
| Cloud VM | `10.0.0.5:8000` | VPC or public IP |

#### Storage

Custom addresses stored in `~/.deepresearch/local_backends.json`:

```json
{
  "ollama": {
    "address": "192.168.1.50:11434",
    "auto_install": true,
    "enabled": true,
    "installed_version": "0.4.0",
    "last_checked": "2026-06-15T17:00:00"
  },
  "llamacpp": {
    "address": "",
    "auto_install": true,
    "enabled": true,
    "installed_version": null,
    "last_checked": null
  },
  "vllm": {
    "address": "gpu-server:8000",
    "auto_install": false,
    "enabled": true,
    "installed_version": "0.5.0",
    "last_checked": "2026-06-15T16:45:00"
  },
  "sglang": {
    "address": "",
    "auto_install": true,
    "enabled": false,
    "installed_version": null,
    "last_checked": null
  }
}
```

### Installation Implementation

Each backend has an install script that runs server-side:

```python
# Example: Ollama install
async def install_ollama(progress_callback) -> InstallResult:
    """Install Ollama with real-time progress."""
    import tempfile
    
    # 1. Check if already installed
    if shutil.which("ollama"):
        return InstallResult(status="already_installed")
    
    # 2. Download install script to temp file (enables checksum verification)
    await progress_callback("Downloading Ollama installer...")
    script_path = os.path.join(tempfile.gettempdir(), "ollama_install.sh")
    
    download = await asyncio.create_subprocess_exec(
        "curl", "-fsSL", "-o", script_path, "https://ollama.com/install.sh",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    await download.wait()
    
    if download.returncode != 0:
        return InstallResult(status="error", error="Failed to download installer")
    
    # 3. Execute downloaded script (not piped)
    await progress_callback("Running installer...")
    process = await asyncio.create_subprocess_exec(
        "sh", script_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    
    # 4. Stream output
    async for line in process.stdout:
        await progress_callback(line.decode().strip())
    
    await process.wait()
    
    # 5. Cleanup temp file
    os.unlink(script_path)
    
    # 6. Verify
    if shutil.which("ollama"):
        return InstallResult(status="success")
    return InstallResult(status="error", error="Installation completed but ollama not in PATH")
```

### Installation State Machine

Each backend tracks installation state:

```
not_installed → downloading → extracting → verifying → installed
                     ↓              ↓            ↓
                  failed         failed       failed
                     ↓              ↓            ↓
                  (cleanup)     (cleanup)    (cleanup)
```

On any failure:
1. Remove partially downloaded files (temp directory)
2. Remove partially extracted binaries
3. Log the exact failure point for debugging
4. Reset state to `not_installed` so user can retry

State is persisted in `~/.deepresearch/install_states.json`:
```json
{
  "ollama": {
    "state": "installed",
    "version": "0.4.0",
    "installed_at": "2026-06-15T17:00:00",
    "address": "localhost:11434"
  },
  "vllm": {
    "state": "not_installed",
    "last_attempt_error": "CUDA not found",
    "last_attempt_at": "2026-06-15T16:30:00"
  }
}
```

### Hardware Detection

Before installation, detect hardware to recommend appropriate backends:

```python
def detect_hardware() -> HardwareInfo:
    gpu_type = None
    gpu_memory = None
    
    # NVIDIA GPU detection
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            gpu_type = "nvidia"
            # Parse memory
    except FileNotFoundError:
        pass
    
    # AMD GPU detection (ROCm)
    try:
        result = subprocess.run(["rocm-smi", "--showid"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            gpu_type = "amd_rocm"
    except FileNotFoundError:
        pass
    # Fallback: check for ROCm installation directory
    if gpu_type is None and os.path.exists("/opt/rocm"):
        gpu_type = "amd_rocm"
    
    # Apple Silicon detection
    if platform.system() == "darwin" and platform.machine() == "arm64":
        gpu_type = "apple_silicon"
    
    return HardwareInfo(gpu_type=gpu_type, gpu_memory=gpu_memory)
```

### Disk Space Requirements

Before installation, verify sufficient disk space:

| Backend | Disk Space Required |
|---------|:-------------------:|
| Ollama | ~500 MB |
| llama.cpp | ~500 MB |
| vLLM | ~5000 MB (includes PyTorch) |
| SGLang | ~5000 MB (includes PyTorch) |

Each install function checks available space before proceeding:

```python
async def install_backend(name: str, required_mb: int, progress_callback) -> InstallResult:
    # Check disk space first
    import shutil as _shutil
    free_mb = _shutil.disk_usage("/").free // (1024 * 1024)
    if free_mb < required_mb:
        await progress_callback(f"Insufficient disk space: {free_mb}MB free, {required_mb}MB required")
        return InstallResult(status="error", error=f"Need {required_mb}MB, only {free_mb}MB available")
    # ... continue with installation
```

### Address Validation

Custom addresses are validated to prevent injection and ensure correct format:

```python
import re

def validate_address(address: str) -> str:
    """Validate and normalize backend address."""
    # Strip scheme if present
    address = re.sub(r'^https?://', '', address)
    # Validate host:port format
    match = re.match(r'^([a-zA-Z0-9._-]+):(\d{1,5})$', address)
    if not match:
        raise ValueError(f"Invalid address format: {address}. Expected host:port")
    host, port = match.groups()
    if not (1 <= int(port) <= 65535):
        raise ValueError(f"Invalid port: {port}. Must be 1-65535")
    return f"{host}:{port}"
```

### Backend Recommendations by Hardware

| Hardware | Recommended | Alternative | Notes |
|----------|-------------|-------------|-------|
| NVIDIA GPU (≥8GB VRAM) | vLLM | SGLang, Ollama | Best throughput with vLLM |
| NVIDIA GPU (<8GB VRAM) | Ollama | llama.cpp | Ollama auto-quantizes |
| AMD GPU (ROCm) | vLLM | SGLang | vLLM supports ROCm |
| Apple Silicon (M1+) | Ollama | llama.cpp | Metal acceleration |
| CPU-only | Ollama | llama.cpp | Slow but works |
| No GPU, limited RAM | Ollama | — | Smallest footprint |

### API Endpoints

```
GET    /api/local-backends                  — List all backends with status + address
POST   /api/local-backends/{name}/install   — Start installation (SSE stream)
POST   /api/local-backends/{name}/start     — Start a stopped backend
POST   /api/local-backends/{name}/stop      — Stop a running backend
GET    /api/local-backends/{name}/logs      — Stream installation/runtime logs (SSE)
PUT    /api/local-backends/{name}/address   — Update custom address
POST   /api/local-backends/{name}/test      — Test connectivity to address
DELETE /api/local-backends/{name}           — Uninstall a backend
GET    /api/hardware                        — Detected hardware info
```

### SSE Installation Stream

Installation output streams to the client in real-time:

```
data: {"status": "downloading", "progress": 45, "message": "Downloading Ollama v0.4.0..."}
data: {"status": "extracting", "progress": 70, "message": "Extracting to /usr/local/bin/..."}
data: {"status": "verifying", "progress": 90, "message": "Verifying installation..."}
data: {"status": "complete", "progress": 100, "message": "Ollama installed successfully!"}
data: {"status": "error", "progress": -1, "message": "CUDA not found — vLLM requires NVIDIA GPU", "error_type": "hardware_incompatible"}
```

## Consequences

### Security Considerations

Auto-installation of external software requires careful security measures:

1. **Script downloads**: All install scripts are downloaded over HTTPS. Checksums are verified where available (e.g., GitHub releases for llama.cpp binaries).

2. **Shell script execution**: Ollama's `install.sh` is executed from a temp file (not piped from curl). Users are warned before first installation and can review the script.

3. **No arbitrary code execution**: Only pre-approved install URLs are supported. Users cannot provide custom install scripts.

4. **Input sanitization**: Custom addresses are validated (host:port format, port range 1-65535, no URL schemes). Prevents command injection.

5. **Privilege awareness**: Installation uses current user privileges. No sudo/root escalation without explicit user consent.

6. **Docker alternative**: For maximum isolation, users can run backends via Docker instead of auto-install. Docker images are pre-built and verified.

### Positive
- One-click installation from web UI — no terminal required
- Real-time feedback during installation (log output, not spinner)
- Auto-discovery finds backends on common ports — no manual URL entry
- Hardware-aware recommendations — users get the best backend for their system
- Graceful handling of existing installations (no overwrites)
- Consistent API for all backends (start/stop/logs/status)
- Custom address configuration — supports remote, Docker, and non-standard deployments

### Negative
- Installation scripts may fail on non-standard systems
- vLLM/SGLang require CUDA — installation fails on non-NVIDIA hardware (must show clear error)
- Subprocess management adds complexity (zombie processes, port conflicts)
- Binary downloads (llama.cpp) may have platform-specific issues

### Risks
- Install scripts from external sources (Ollama, llama.cpp releases) could change
- Port conflicts if user already has something running on default ports
- Disk space requirements vary (Ollama: ~500MB, vLLM: ~5GB with PyTorch)
- Remote backends may have higher latency affecting research speed
- Custom addresses may become stale if services move

### Mitigations
- Verify checksums for binary downloads
- Check port availability before starting
- Show disk space requirements before installation
- Cache installation logs for debugging
- "Test" button validates address before saving
- Periodic health check on configured addresses (warn if unreachable)

### Docker Alternative

For maximum isolation and reproducibility, users can run backends via Docker:

```bash
# Ollama
docker run -d -v ollama:/root/.ollama -p 11434:11434 ollama/ollama

# vLLM
docker run -d --gpus all -p 8000:8000 vllm/vllm-openai --model meta-llama/Llama-3.1-8B
```

Docker containers are discovered via the same auto-discovery protocol (they expose the same API endpoints on mapped ports). The web UI shows Docker containers as "running (docker)" with a separate badge.

## References
- ADR-0001: Multi-Agent Research Architecture (backend integration point)
- ADR-0003: Web Frontend and Multi-Session (Settings tab extension)
- ADR-0004: Test Findings (DuckDuckGo timeout → local model alternative)
