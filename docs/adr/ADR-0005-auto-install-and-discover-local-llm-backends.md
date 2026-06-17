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

**Version:** 2.0
**Last Updated:** 2026-06-16

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

### Existing Solutions
- **llmfit** (28.1k stars, Rust, MIT) — hardware detection, model scoring, recommendations, download management
- **llmserve** (281 stars, Rust, MIT) — backend detection, model serving, TUI for managing servers

Both are well-maintained, cross-platform Rust binaries that solve hardware detection and backend management better than custom code.

## Decision

### Approach: Integrate llmfit + llmserve

Replace custom auto-install logic with integration of existing open-source tools. This reduces ~470 lines of custom code to integration with mature, community-maintained binaries.

### Implementation Phases

| Phase | Scope | Timeline |
|-------|-------|----------|
| Phase 1 | llmfit/llmserve detection + install UI + custom addresses | Week 1 |
| Phase 2 | Hardware-aware model recommendations | Week 2 |
| Phase 3 | Backend management via llmserve | Week 3 |
| Phase 4 | Model selection integration + polish | Week 4 |

Each phase is independently testable and deployable.

### Tool Integration

#### llmfit — Hardware Detection & Model Recommendations

```bash
# Detect hardware
llmfit system --json
# Returns: GPU type, VRAM, CPU cores, RAM, platform

# Get model recommendations
llmfit recommend --json --use-case coding
# Returns: model list with hardware fit scores

# Download a model
llmfit download <model-name>
# Manages download with progress
```

#### llmserve — Backend Management

```bash
# List running backends
llmserve list --json

# Start a backend
llmserve start --model <model-name> --backend ollama

# Stop a backend
llmserve stop <server-id>
```

### Web UI Integration

#### Settings Tab → Local LLM Section

```
┌──────────────────────────────────────────────────────┐
│ Local LLM                                            │
│                                                      │
│ Hardware Summary (from llmfit)                       │
│ ┌──────────────────────────────────────────────────┐ │
│ │ GPU: NVIDIA RTX 3080 (10GB VRAM)                 │ │
│ │ CPU: 12 cores, 64GB RAM                         │ │
│ │ Platform: Linux x86_64                          │ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ Recommended Models (from llmfit recommend)           │
│ ┌──────────────────────────────────────────────────┐ │
│ │ Model                │ Fit  │ Size  │ Backend    │ │
│ │ codellama-7b         │ 95%  │ 4.1GB │ ollama     │ │
│ │ mistral-7b           │ 90%  │ 4.1GB │ ollama     │ │
│ │ deepseek-coder-6.7b  │ 85%  │ 4.0GB │ ollama     │ │
│ │ [Show more...]                                      │ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ Running Backends (from llmserve / port probing)      │
│ ┌──────────────────────────────────────────────────┐ │
│ │ 🟢 Ollama — localhost:11434 — 3 models loaded    │ │
│ │ 🟡 llama.cpp — localhost:8080 — idle             │ │
│ │ [Configure Custom Address...]                     │ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ Tool Status                                          │
│ ┌──────────────────────────────────────────────────┐ │
│ │ llmfit: ✅ Installed (v0.3.2)                   │ │
│ │ llmserve: ❌ Not installed [Install Instructions]│ │
│ └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

#### Install Instructions Modal

If llmfit/llmserve are not detected, show platform-specific instructions:

```
┌──────────────────────────────────────────────────────┐
│ Install Local LLM Tools                              │
│                                                      │
│ macOS:                                               │
│   brew install llmfit llmserve                       │
│                                                      │
│ Linux:                                               │
│   curl -fsSL https://llmfit.dev/install.sh | sh      │
│   curl -fsSL https://llmserve.dev/install.sh | sh    │
│                                                      │
│ Python (pip):                                        │
│   pip install llmfit llmserve                        │
│                                                      │
│ [Copy to Clipboard] [I've installed these tools]     │
└──────────────────────────────────────────────────────┘
```

### Custom Address Configuration

Users may run backends on non-standard addresses, remote machines, or Docker containers. Each backend supports a configurable `host:port` address. This is retained from v1.0.

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
2. Auto-discovered address (from llmserve or port probing)
3. Default address (`localhost:<default-port>`)

#### Remote Backend Support

| Scenario | Address | Notes |
|----------|---------|-------|
| Local Ollama | `localhost:11434` | Default |
| Remote Ollama | `192.168.1.50:11434` | Same network |
| Docker Ollama | `localhost:11434` | Port mapped |
| Remote vLLM | `gpu-server.local:8000` | Network inference |
| SSH tunnel | `localhost:8000` | Via `ssh -L 8000:localhost:8000` |
| Cloud VM | `10.0.0.5:8000` | VPC or public IP |

### Auto-Discovery Protocol

On startup and on-demand, the server detects backends in this order:

1. **Custom addresses** — Check each backend's saved custom address first
2. **llmserve** — Query `llmserve list --json` for managed backends
3. **Default ports** — Probe `localhost:<default-port>` for each backend

| Backend | Default Port | Probe Endpoint | Response |
|---------|:------------:|----------------|----------|
| Ollama | 11434 | `GET /api/tags` | `{"models": [...]}` |
| llama.cpp | 8080 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| vLLM | 8000 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| SGLang | 30000 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |

If a custom address is configured, it takes priority. The probe is a lightweight HTTP GET with a 2-second timeout.

### Hardware-Aware Model Recommendations

The server calls llmfit to get hardware info and model suggestions:

```python
async def get_hardware_info() -> HardwareInfo:
    """Get hardware info from llmfit."""
    result = await asyncio.create_subprocess_exec(
        "llmfit", "system", "--json",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await result.communicate()
    return json.loads(stdout)

async def get_model_recommendations(use_case: str = "coding") -> list[ModelRecommendation]:
    """Get model recommendations from llmfit."""
    result = await asyncio.create_subprocess_exec(
        "llmfit", "recommend", "--json", "--use-case", use_case,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await result.communicate()
    return json.loads(stdout)
```

Model recommendations include a hardware fit score (0-100%) indicating how well each model runs on the detected hardware.

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
GET    /api/hardware                        — Hardware info (from llmfit)
GET    /api/recommendations                 — Model recommendations (from llmfit)
GET    /api/tools/status                    — llmfit/llmserve installation status
```

## Consequences

### Security Considerations

1. **Tool installation**: llmfit and llmserve are installed via standard package managers (brew, pip, curl script). Users are shown instructions and must install manually — no silent background installation.

2. **HTTPS-only downloads**: llmfit's model downloads use HTTPS with checksum verification. We do not implement custom download logic.

3. **No arbitrary code execution**: Only pre-approved tools (llmfit, llmserve) are invoked. Users cannot provide custom scripts or binaries.

4. **Input sanitization**: Custom addresses are validated (host:port format, port range 1-65535, no URL schemes). Prevents command injection.

5. **Subprocess isolation**: Tool invocations run with current user privileges. Output is parsed as JSON only — no shell expansion of results.

6. **Docker alternative**: For maximum isolation, users can run backends via Docker. Docker containers are discovered via the same port-probing protocol.

### Positive
- Dramatically less custom code (~300 lines vs ~470) — leverage community-maintained tools
- Hardware detection is battle-tested (llmfit has 28.1k stars)
- Model recommendations based on real hardware benchmarks
- Custom address support retained for remote/Docker deployments
- Clear install instructions — users choose their installation method
- Auto-discovery still works for Ollama and other backends on known ports
- Hardware fit scores help users pick the right model

### Negative
- Users must install llmfit/llmserve separately (extra step vs fully automatic)
- Two external tool dependencies (llmfit, llmserve) must be maintained
- If llmfit/llmserve change their CLI interface, integration must be updated
- Port probing fallback is still custom code

### Risks
- llmfit/llmserve may not be available on all platforms (Rust binaries)
- CLI interface changes in upstream tools could break integration
- Port conflicts if user already has something running on default ports
- Remote backends may have higher latency affecting research speed
- Custom addresses may become stale if services move

### Mitigations
- Provide installation instructions for all major platforms (macOS, Linux, Windows WSL)
- Pin to specific tool versions and test before upgrading
- Check port availability before starting
- "Test" button validates address before saving
- Periodic health check on configured addresses (warn if unreachable)
- Cache tool output to reduce subprocess calls

### Docker Alternative

For maximum isolation and reproducibility, users can run backends via Docker:

```bash
# Ollama
docker run -d -v ollama:/root/.ollama -p 11434:11434 ollama/ollama

# vLLM
docker run -d --gpus all -p 8000:8000 vllm/vllm-openai --model meta-llama/Llama-3.1-8B
```

Docker containers are discovered via the same auto-discovery protocol (they expose the same API endpoints on mapped ports). The web UI shows Docker containers as "running (docker)" with a separate badge.

### Search Engine Setup

DeepeResearch uses web search as a core part of the research pipeline. Users can choose between a self-hosted SearXNG instance (recommended) or the legacy DuckDuckGo backend (ddgs). ADR-0012 covers the full rationale for local search.

#### Quick Install

| Option | Command | Notes |
|--------|---------|-------|
| Docker (recommended) | `docker run -d --name searxng --restart unless-stopped -p 8888:8080 -e SEARXNG_BASE_URL=http://localhost:8888 searxng/searxng` | Requires Docker |
| With custom config | `docker run -d --name searxng --restart unless-stopped -p 8888:8080 -v ./settings.yml:/etc/searxng/settings.yml -e SEARXNG_BASE_URL=http://localhost:8888 searxng/searxng` | Disables limiter, enables JSON |
| Skip (use ddgs) | `pip install deepresearch[ddgs]` | Uses DuckDuckGo via ddgs library (rate-limited under load) |

#### Critical settings.yml

```yaml
use_default_settings: true
server:
  limiter: false
search:
  formats: [html, json]
```

#### Verify it works

```bash
curl "http://localhost:8888/search?q=test&format=json" | python -m json.tool
```

#### User Choice

| Option | Pros | Cons |
|--------|------|------|
| **SearXNG** (recommended) | No rate limits, self-hosted, full control | Requires Docker |
| **ddgs** (legacy) | No Docker needed, zero setup | Rate-limited under concurrent load |

SearXNG runs on port 8888 by default and is auto-discovered by the same port-probing protocol used for LLM backends.

## Related Issues
- #36 (llmfit/llmserve Integration): ADR-0005 v2.0 replaces 5 custom auto-installers with llmfit (hardware detection) + llmserve (model serving). SearXNG install instructions also included.
- #37 (Deployment — systemd/launchd/NSSM): Natural companion — users who install local LLMs also want persistent service deployment.
- #50 (Server Crash — no graceful shutdown): Resolved by #37 deployment as a systemd/launchd service with auto-restart.

## References
- ADR-0001: Multi-Agent Research Architecture (backend integration point)
- ADR-0003: Web Frontend and Multi-Session (Settings tab extension)
- ADR-0004: Test Findings (DuckDuckGo timeout → local model alternative)
- llmfit: https://github.com/llmfit/llmfit
- llmserve: https://github.com/llmserve/llmserve
