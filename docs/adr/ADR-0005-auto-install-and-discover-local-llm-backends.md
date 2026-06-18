---
phase:
  current: 3
  total: 3
  status:
    1: done
    2: done
    3: done
---

# ADR-0005: Local LLM Backends — Auto-Discovery, Installation, and LiteLLM Routing

## Status

In Progress (v2.4)

**Version:** 2.4
**Last Updated:** 2026-06-18

> **⚠️ Valgfrit:** Local LLM installation er **ikke påkrævet** for at bruge DeepeResearch.
> Systemet fungerer fuldt ud med cloud-baserede modeller (OpenAI, Anthropic, OpenRouter) via API keys.
> Local LLM er kun nødvendigt hvis du vil køre modeller lokalt uden API-omkostninger.

## Context

DeepeResearch currently supports cloud-based LLM providers (OpenAI, Anthropic, OpenRouter, Groq, etc.) via API keys, and has basic Ollama integration (localhost:11434). Users want to run research locally without paying for API calls, using open-source models through local inference backends.

**LiteLLM** is already a core dependency (`litellm>=1.40.0`) — used for routing to `openrouter/deepseek/deepseek-v4-flash` and other cloud providers via OpenRouter. The same routing layer can be extended to local backends.

### Current State
- LiteLLM already handles all LLM routing (cloud providers via OpenRouter)
- Ollama auto-discovered at localhost:11434 via `/api/tags`
- Custom local endpoints configurable via Settings tab (llama.cpp, vLLM)
- No auto-install capability — users must install backends manually
- No auto-discovery beyond Ollama — llama.cpp, vLLM, LM Studio, LocalAI require manual URL entry
- No installation UI — terminal-only setup

### Problem
1. Users must manually install and configure local backends (Ollama, vLLM, llama.cpp, LM Studio, LocalAI)
2. Each backend has different installation methods (pip, binary, Docker)
3. Users don't know which backend to choose for their hardware
4. No feedback during installation (silent failures, unclear errors)
5. Auto-discovery only works for Ollama — other backends require manual configuration
6. LiteLLM routing to local backends requires manual `api_base` configuration

### Key Forces
1. Cross-platform support (Linux, macOS, Windows WSL)
2. Hardware diversity (CPU-only, NVIDIA GPU, Apple Silicon, AMD)
3. Backend complexity (Ollama = simple, vLLM = Docker/pip, llama.cpp = binary, LM Studio = GUI)
4. User experience — install should be one-click from the web UI
5. Must not break existing LiteLLM + OpenRouter integration
6. Safety — auto-install should not overwrite existing installations
7. LiteLLM is already the routing layer — reuse it, don't duplicate

### Existing Solutions

- **llmfit** (AlexsJones/llmfit, 28.2k⭐, Rust, MIT) — hardware detection (`llmfit system --json`), model recommendations (`llmfit recommend --json`), scoring. CLI + REST API (`llmfit serve --port 8787`) + MCP server (`llmfit serve --mcp`). Install: `curl -fsSL https://llmfit.axjns.dev/install.sh | sh -s -- --local` (uden sudo). 5.8 MB static binary.
- **llmserve** (AlexsJones/llmserve, 285⭐) — LAV fit. TUI-only, ingen CLI mode, ingen REST API, kan ikke scriptes. Droppet.

llmfit is well-maintained, cross-platform Rust binary that solves hardware detection better than custom code. llmserve is rejected due to TUI-only interface.

## Decision

### Approach

1. **llmfit** (AlexsJones/llmfit) — hardware detection + model recommendations. Installeres via Web UI med live log tail. Køres som subprocess eller REST API sidecar.
2. **Ollama auto-install** — `curl -fsSL https://ollama.com/install.sh | sh` via Web UI med live log tail.
3. **Auto-discovery** — probe standardporte for Ollama (11434), llama.cpp (8080), vLLM (8000), LM Studio (1234), LocalAI (8080).
4. **Custom addresses** — bruger kan indstille custom host:port for hver backend.
5. **LiteLLM routing** — deepresearch bruger LiteLLM til at route til den valgte backend. OpenAI-kompatible endpoints (llama.cpp, vLLM, LM Studio, LocalAI) routes via `custom/openai`. Ollama routes via `ollama/`.

### Implementation Phases

| Phase | Scope | Timeline | Status |
|-------|-------|----------|--------|
| Phase 1 | Ollama auto-install + llmfit hardware detection + custom addresses | Week 1 | ✅ Done |
| Phase 2 | Auto-discovery (all backends) + LiteLLM routing integration + Ollama install via Web UI (SSE) | Week 2 | ✅ Done |
| Phase 3 | Local backend management: llmfit install/uninstall, Ollama start/stop/uninstall, model pull from recommendations table | Week 3 | ✅ Done |

Each phase is independently testable and deployable.

### Tool Integration

#### llmfit — Hardware Detection & Model Recommendations

```bash
# Detect hardware
llmfit system --json
# Returns: GPU type, VRAM, CPU cores, RAM, platform

# Get model recommendations
llmfit recommend --json
# Returns: model list with hardware fit scores

# REST API (sidecar)
llmfit serve --port 8787
# Also supports MCP: llmfit serve --mcp

# Install (no sudo)
curl -fsSL https://llmfit.axjns.dev/install.sh | sh -s -- --local
```

#### Backend Installation

Primært Ollama:
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Andre backends (llama.cpp, vLLM) kan installeres, men Ollama er hovedmålet.

#### LiteLLM Routing

LiteLLM er allerede dependency og bruges til at route til cloud providers via OpenRouter. Samme routing layer bruges til local backends:

| Backend | LiteLLM Model Prefix | Example |
|---------|---------------------|---------|
| Ollama | `ollama/<model>` | `ollama/llama3.2-3b` |
| llama.cpp | `custom/openai` med `api_base` | `custom/llama` → `http://localhost:8080/v1` |
| vLLM | `custom/openai` med `api_base` | `custom/vllm` → `http://localhost:8000/v1` |
| LM Studio | `custom/openai` med `api_base` | `custom/lmstudio` → `http://localhost:1234/v1` |
| LocalAI | `custom/openai` med `api_base` | `custom/localai` → `http://localhost:8080/v1` |

Brugeren vælger en model i Web UI, og deepresearch konfigurerer LiteLLM dynamisk med korrekt `api_base` + model prefix.

### Web UI Integration

#### Settings Tab → Local LLM Section

```
┌──────────────────────────────────────────────────────┐
│ Local LLM (valgfrit)                                 │
│ ⚠️ Kan bruges uden — OpenRouter default              │
│                                                      │
│ Hardware (via llmfit)                                │
│ ┌──────────────────────────────────────────────────┐ │
│ │ 🖥️ GPU: NVIDIA RTX 3080 (10GB VRAM)             │ │
│ │ 🧠 CPU: 12 cores, 64GB RAM                      │ │
│ │ 🔧 Backend: CUDA                                 │ │
│ │ [Install llmfit] [Refresh]                       │ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ Anbefalede modeller (via llmfit)                     │
│ ┌──────────────────────────────────────────────────┐ │
│ │ Model              │ Fit  │ Size │ Backend        │ │
│ │ llama3.2-3b        │ 95%  │ 2.0GB│ ollama         │ │
│ │ mistral-7b         │ 90%  │ 4.1GB│ ollama         │ │
│ │ qwen2.5-7b         │ 85%  │ 4.0GB│ ollama         │ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ Discovered Backends                                  │
│ ┌──────────────────────────────────────────────────┐ │
│ │ 🟢 Ollama — localhost:11434 — 5 modeller         │ │
│ │ 🔴 llama.cpp — localhost:8080 — ej fundet        │ │
│ │ ⚫ vLLM — [custom address]                       │ │
│ │ ⚫ LM Studio — [custom address]                   │ │
│ │ [Install Ollama] [Add Custom...]                  │ │
│ └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### Web UI Installation (live log tail)

Når en bruger klikker "Install" i Settings → Local LLM, sker følgende:

1. **POST /api/local-backends/{name}/install** startes
2. Serveren streamer live log output via SSE (Server-Sent Events)
3. Frontend viser en terminal-lignende log viewer:
   ```
   ┌──────────────────────────────────────────────────────┐
   │ Installing Ollama...                                 │
   │                                                      │
   │ $ curl -fsSL https://ollama.com/install.sh | sh      │
   │ ✓ Detected platform: linux-x86_64                    │
   │ ✓ Downloaded install script                          │
   │ ✓ Installing Ollama...                               │
   │ ████████████████████░░░░░░░░░░ 65%                   │
   │ ✓ Verifying installation...                          │
   │ ✓ Ollama v0.5.1 installed                            │
   │                                                      │
   │ ✅ Installation complete! (v0.5.1)                   │
   │ [Continue] [View Details]                            │
   └──────────────────────────────────────────────────────┘
   ```
4. Hvert trin vises med ikon: ⏳ (venter), ✅ (succes), ❌ (fejl)
5. **Hvis fejl:** Vis fejlbesked med "Retry" knap og mulighed for at kopiere loggen
6. Efter installation: Vis hardware-dashboard + "Test" knap

#### SSE Event Format
```javascript
// Server → Frontend via SSE
event: install_log
data: {"step": "download", "message": "Downloading install script...", "progress": 30}

event: install_log
data: {"step": "install", "message": "Installing Ollama...", "progress": 65}

event: install_complete
data: {"status": "success", "version": "0.5.1", "path": "/usr/local/bin/ollama"}

event: install_error
data: {"status": "error", "message": "Disk space insufficient: need 500MB, have 200MB", "code": "ENOSPC"}
```

#### Frontend State Machine
```
IDLE → INSTALLING → [SUCCESS | ERROR] → IDLE
  ↑                       ↓
  └───── RETRY ───────────┘
```

### Custom Address Configuration

Users may run backends on non-standard addresses, remote machines, or Docker containers. Each backend supports a configurable `host:port` address.

#### Configuration Model

```python
class LocalBackendConfig(BaseModel):
    name: str                           # "ollama", "llamacpp", "vllm", "lm-studio", "localai"
    address: str = ""                   # Custom host:port (e.g. "192.168.1.50:11434")
    auto_install: bool = True           # Allow auto-install if not found
    enabled: bool = True                # Enable/disable this backend
    installed_version: str | None = None  # Detected backend version
    last_checked: str | None = None     # ISO timestamp of last health check
    lite_llm_model: str = ""            # Model name for LiteLLM routing (auto-configured)
    lite_llm_api_base: str = ""         # api_base for LiteLLM (auto-configured)
```

Address resolution order:
1. User-configured `address` (from Settings tab or config file)
2. Auto-discovered address (from port probing)
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
2. **Default ports** — Probe `localhost:<default-port>` for each backend

| Backend | Default Port | Probe Endpoint | Response |
|---------|:------------:|----------------|----------|
| Ollama | 11434 | `GET /api/tags` | `{"models": [...]}` |
| llama.cpp | 8080 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| vLLM | 8000 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| LM Studio | 1234 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |
| LocalAI | 8080 | `GET /v1/models` | `{"data": [{"id": "..."}]}` |

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

async def get_model_recommendations() -> list[ModelRecommendation]:
    """Get model recommendations from llmfit."""
    result = await asyncio.create_subprocess_exec(
        "llmfit", "recommend", "--json",
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
POST   /api/local-backends/{name}/install   — Start installation (Ollama/llmfit) (SSE stream)
POST   /api/local-backends/{name}/uninstall — Uninstall a backend
PUT    /api/local-backends/{name}/address   — Update custom address
POST   /api/local-backends/{name}/test      — Test connectivity to address
GET    /api/hardware                        — Hardware info (via llmfit if installed)
GET    /api/recommendations                 — Model recommendations (via llmfit if installed)
GET    /api/tools/status                    — llmfit installation status + version
```

### LiteLLM Routing Integration

When a user selects a local model in Web UI, deepresearch dynamically configures LiteLLM:

```python
from litellm import completion

# Example: Route to Ollama
response = completion(
    model="ollama/llama3.2-3b",
    api_base="http://localhost:11434",
    messages=[{"role": "user", "content": "Hello"}]
)

# Example: Route to custom OpenAI-compatible backend
response = completion(
    model="custom/openai",
    api_base="http://localhost:8080/v1",
    messages=[{"role": "user", "content": "Hello"}]
)
```

The Web UI Settings tab allows users to:
1. Select a discovered backend
2. Choose a model from that backend's available models
3. LiteLLM configuration is auto-generated based on backend type + address

## Consequences

### Security Considerations

1. **Tool installation**: llmfit and Ollama are installed via standard curl|sh scripts. Users are shown live logs and can abort at any time.

2. **HTTPS-only downloads**: All install scripts downloaded over HTTPS. llmfit verification via SHA256 checksum.

3. **No arbitrary code execution**: Only pre-approved tools (llmfit, Ollama install script) are invoked. Users cannot provide custom scripts or binaries.

4. **Input sanitization**: Custom addresses are validated (host:port format, port range 1-65535, no URL schemes). Prevents command injection.

5. **Subprocess isolation**: Tool invocations run with current user privileges. Output is parsed as JSON only — no shell expansion of results.

6. **Docker alternative**: For maximum isolation, users can run backends via Docker. Docker containers are discovered via the same port-probing protocol.

### Positive
- One-click Ollama install from Web UI with live log feedback
- llmfit provides hardware-aware model recommendations (battle-tested, 28.2k⭐)
- LiteLLM already handles routing — no new routing code needed
- Custom address support retained for remote/Docker deployments
- Auto-discovery works for 5 backends via standard port probing
- Hardware fit scores help users pick the right model
- No llmserve dependency (dropped due to TUI-only limitations)

### Negative
- llmfit is an extra optional dependency (~5.8 MB binary)
- Ollama install uses curl|sh pattern (industry standard, but carries trust risk)
- Two external dependencies (llmfit, Ollama) must be maintained
- If llmfit changes CLI interface, integration must be updated
- Port probing fallback is custom code

### Risks
- llmfit may not be available on all platforms (Rust binary, but cross-platform)
- CLI interface changes in upstream llmfit could break integration
- Port conflicts if user already has something running on default ports
- Remote backends may have higher latency affecting research speed
- Custom addresses may become stale if services move

### Mitigations
- llmfit install without sudo (`--local` flag) — works on all Unix platforms
- Pin to specific llmfit version and test before upgrading
- Check port availability before starting
- "Test" button validates address before saving
- Periodic health check on configured addresses (warn if unreachable)
- Cache llmfit output to reduce subprocess calls

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
- #36 (Local LLM auto-install): ADR-0005 v2.3 — llmfit (HW detection) + Ollama auto-install + auto-discovery + LiteLLM routing + Web UI install with live log tail (SSE) and frontend state machine (Fase 2c).
- #94 (Epic: ADR-0017 — Deployment & Resiliency, v0.13.0): Parent epic that includes #36 as Phase 2.
- LiteLLM (core dependency): `litellm>=1.40.0` — already handles routing for both cloud and local backends.

## References
- ADR-0001: Multi-Agent Research Architecture (backend integration point)
- ADR-0003: Web Frontend and Multi-Session (Settings tab extension)
- ADR-0004: Test Findings (DuckDuckGo timeout → local model alternative)
- llmfit: https://github.com/AlexsJones/llmfit
- llmserve (dropped): https://github.com/AlexsJones/llmserve
- Ollama install: https://ollama.com/install.sh
- LiteLLM: https://github.com/BerriAI/litellm