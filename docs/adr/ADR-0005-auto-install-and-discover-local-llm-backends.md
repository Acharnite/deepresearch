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

### Backend Installation Priority

| Priority | Backend | Install Method | Hardware | Complexity |
|----------|---------|---------------|----------|------------|
| 1 | **Ollama** | `curl -fsSL https://ollama.com/install.sh \| sh` | CPU/GPU (auto-detect) | Low |
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

3. **"Start" button** for installed-but-stopped backends:
   - Starts the backend as a subprocess
   - Shows startup logs
   - Verifies connectivity after start

4. **"Stop" button** for running backends:
   - Graceful shutdown
   - Process cleanup

### Auto-Discovery Protocol

On startup and on-demand, the server probes common ports:

| Backend | Default Port | Probe Endpoint | Response |
|---------|:------------:|----------------|----------|
| Ollama | 11434 | `GET /api/tags` | `{"models": [...]}` |
| llama.cpp | 8080 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| vLLM | 8000 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| SGLang | 30000 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |

Discovery also checks process list (`ps aux`) to detect running backends on non-standard ports.

### Installation Implementation

Each backend has an install script that runs server-side:

```python
# Example: Ollama install
async def install_ollama(progress_callback) -> InstallResult:
    """Install Ollama with real-time progress."""
    # 1. Check if already installed
    if shutil.which("ollama"):
        return InstallResult(status="already_installed")
    
    # 2. Download install script
    await progress_callback("Downloading Ollama installer...")
    
    # 3. Run installer (with progress parsing)
    process = await asyncio.create_subprocess_exec(
        "curl", "-fsSL", "https://ollama.com/install.sh", "|", "sh",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    
    # 4. Stream output to progress callback
    async for line in process.stdout:
        await progress_callback(line.decode().strip())
    
    # 5. Verify installation
    if shutil.which("ollama"):
        return InstallResult(status="success")
    return InstallResult(status="error", error="Installation completed but ollama not found in PATH")
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
    
    # Apple Silicon detection
    if platform.system() == "darwin" and platform.machine() == "arm64":
        gpu_type = "apple_silicon"
    
    return HardwareInfo(gpu_type=gpu_type, gpu_memory=gpu_memory)
```

### Backend Recommendations by Hardware

| Hardware | Recommended | Alternative | Notes |
|----------|-------------|-------------|-------|
| NVIDIA GPU (≥8GB VRAM) | vLLM | SGLang, Ollama | Best throughput with vLLM |
| NVIDIA GPU (<8GB VRAM) | Ollama | llama.cpp | Ollama auto-quantizes |
| Apple Silicon (M1+) | Ollama | llama.cpp | Metal acceleration |
| CPU-only | Ollama | llama.cpp | Slow but works |
| No GPU, limited RAM | Ollama | — | Smallest footprint |

### API Endpoints

```
GET  /api/local-backends              — List all backends with status
POST /api/local-backends/{name}/install — Start installation (SSE stream)
POST /api/local-backends/{name}/start   — Start a stopped backend
POST /api/local-backends/{name}/stop    — Stop a running backend
GET  /api/local-backends/{name}/logs    — Stream installation/runtime logs (SSE)
GET  /api/hardware                      — Detected hardware info
```

### SSE Installation Stream

Installation output streams to the client in real-time:

```
data: {"status": "downloading", "progress": 45, "message": "Downloading Ollama v0.4.0..."}
data: {"status": "extracting", "progress": 70, "message": "Extracting to /usr/local/bin/..."}
data: {"status": "verifying", "progress": 90, "message": "Verifying installation..."}
data: {"status": "complete", "progress": 100, "message": "Ollama installed successfully!"}
```

## Consequences

### Positive
- One-click installation from web UI — no terminal required
- Real-time feedback during installation (log output, not spinner)
- Auto-discovery finds backends on common ports — no manual URL entry
- Hardware-aware recommendations — users get the best backend for their system
- Graceful handling of existing installations (no overwrites)
- Consistent API for all backends (start/stop/logs/status)

### Negative
- Installation scripts may fail on non-standard systems
- vLLM/SGLang require CUDA — installation fails on non-NVIDIA hardware (must show clear error)
- Subprocess management adds complexity (zombie processes, port conflicts)
- Binary downloads (llama.cpp) may have platform-specific issues

### Risks
- Install scripts from external sources (Ollama, llama.cpp releases) could change
- Port conflicts if user already has something running on default ports
- Disk space requirements vary (Ollama: ~500MB, vLLM: ~5GB with PyTorch)

### Mitigations
- Verify checksums for binary downloads
- Check port availability before starting
- Show disk space requirements before installation
- Cache installation logs for debugging

## References
- ADR-0001: Multi-Agent Research Architecture (backend integration point)
- ADR-0003: Web Frontend and Multi-Session (Settings tab extension)
- ADR-0004: Test Findings (DuckDuckGo timeout → local model alternative)
