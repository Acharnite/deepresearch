---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0003: Web Frontend & Multi-Session Architecture

## Status

Accepted

**Version:** 1.3
**Last Updated:** 2026-06-14

## Context

DeepeResearch started as a CLI-only tool. Users requested:
1. A web UI to monitor research progress in real-time
2. Ability to start, control, and download results from the browser
3. Running multiple concurrent research sessions
4. Configuring API keys and local models through the UI
5. A visual model selector — dropdown for "same" mode, per-agent selectors for "manual" mode
6. Session management — ability to delete individual sessions and clear all completed/errored sessions
7. Provider model auto-discovery — model lists fetched automatically from all configured providers

### Key Forces
1. Real-time updates require server-sent events or WebSockets
2. Multiple sessions need isolation (events, state, output)
3. Background tasks must not block HTTP requests
4. API keys must be stored securely and not in the codebase
5. Local models need service discovery (Ollama, llama.cpp, vLLM)
6. The UI must work without JavaScript frameworks (single HTML file)
7. Model selection must be mode-aware — different UI for same/random/manual modes
8. Session cleanup must include output file cleanup, not just state removal
9. Provider model lists must be discoverable from their REST APIs, not just curated YAML

### Prior Art / Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| **SSE (chosen)** | Simple, one-directional, works with EventSource API | No client→server streaming |
| WebSockets | Bidirectional | More complex, needs custom protocol |
| Polling | Simplest to implement | High latency, more server load |
| React/Vue | Rich components | Build step, dependency overhead |

## Decision

### Web Server: FastAPI + SSE
- FastAPI for async HTTP + SSE
- SSE (Server-Sent Events) for real-time progress streaming
- Single self-contained HTML file for dashboard (vanilla JS + CSS)
- Dark theme, responsive layout, no build step

### Multi-Session: Per-Session EventBus + asyncio Tasks
- Each session gets its own EventBus instance for SSE isolation
- MultiSessionManager manages a dict of SessionInfo + asyncio.Task
- Sessions identified by UUID, stored in memory
- Max 20 concurrent sessions, auto-cleanup of oldest completed
- Per-session output directories (`./output/{session_id}/`)

### API Key Management: Local .env File
- Keys stored in `~/.deepresearch/.env`
- Environment variables override file values at runtime
- 9 supported providers: OpenAI, Anthropic, Groq, Google, Cohere, Together, DeepSeek, OpenRouter, Opencode AI
- Keys masked in UI (only preview prefix shown)

### Local Model Discovery: HTTP Auto-Detect + Manual Config
- Ollama: auto-discover via `GET http://localhost:11434/api/tags`
- llama.cpp/vLLM: manual endpoint configuration (name, URL, type)
- Endpoints stored in `~/.deepresearch/local_endpoints.json`
- Test connection button validates endpoint before saving

### Custom Time Budget: Free-Form Minutes
- Named budgets (quick/medium/deep) remain as presets
- Custom: `time_budget_seconds` parameter, free-form minutes input
- Custom budget defaults to single round (like quick mode)
- Session timeout = budget + 60s grace period, max 3600s (1 hour)

### Model Selector UI
- **"Same" mode** — single `<select id="modelSelector">` dropdown populated from `/api/models`. Default model is `opencode/go`, pre-selected on page load.
- **"Manual" mode** — per-agent `<select class="agent-model-select">` dropdowns, one per agent profile. Agent profiles fetched from `/api/profiles`.
- **"Random" mode** — static info text: "🎲 Random model from configured pool" — no selection needed.
- Radio buttons toggle visibility of the three UX patterns via CSS class toggling.
- Model list fetched asynchronously on page load and cached client-side.

### Session Deletion: DELETE + Clear All
- **`DELETE /api/sessions/{id}`** endpoint — deletes session state and cleans up `./output/{session_id}/` directory.
- **Delete button** on each session card in the dashboard list view (🗑 icon).
- **Clear All button** — `window.clearAllSessions()` — iterates all sessions with status `complete` or `error`, deletes each one.
- Running sessions cannot be deleted — user must cancel first.
- Delete confirmation via `confirm()` dialog before proceeding.

### Provider Model Auto-Discovery
The `/api/models` endpoint goes beyond the curated `models.yaml` to discover models from all configured providers:

1. **Curated models** from `src/config/models.yaml`
2. **Provider API models** fetched via REST from OpenAI, OpenRouter, Anthropic, Groq, Together, DeepSeek, Google, and Cohere — each provider's model listing API (e.g., `https://api.openai.com/v1/models`)
3. **Ollama local models** via `GET http://localhost:11434/api/tags`
4. **Manually configured local endpoints** from `~/.deepresearch/local_endpoints.json` (llama.cpp, vLLM)
- **Caching**: 60-second cache (`_discovered_provider_models_cache`) to avoid excessive API calls
- **Timeout**: 5 seconds per provider API call
- **De-duplication**: curated `models.yaml` entries take precedence over auto-discovered ones with the same ID

### SSE Event History Buffer: Replay on Connect

Late-connecting SSE clients (dashboards opened after session start) need to see past events. An in-memory event history buffer was added to `SessionInfo`:

**Mechanism:**
- `SessionInfo.event_history: list[dict]` captures every event published to the session's `EventBus`
- The bus's `publish()` method is patched at session start to also append to `event_history`:
  ```python
  async def _publish_with_history(event):
      info.event_history.append(event)
      if len(info.event_history) > 500:
          info.event_history[:] = info.event_history[-500:]
      await _original_publish(event)
  ```
- History is trimmed to the last 500 events to bound memory usage (hard cap: **500 events max per session**)

**SSE replay flow (`GET /api/sessions/{session_id}/events`):**
1. Client subscribes to the session's EventBus queue
2. **First:** all buffered events from `event_history` are replayed (synchronously, one by one)
3. **Then:** live events stream from the bus queue (asynchronous, with 30s keepalive timeout)
4. This ensures late arrivals see `session_start`, `models_assigned`, `round_start`, etc.

**Legacy global SSE (`GET /api/events`):**
- Uses the global `event_bus` singleton — no history replay (events before subscribe are lost)
- Kept for backward compatibility; new clients should use per-session endpoints

### Agent Live Streaming Output Panels

The dashboard renders per-agent live text output as agents generate responses:

**Event flow:**
1. `Orchestrator._make_stream_callback(agent_id)` creates an async callback per agent
2. Each research agent is constructed with `event_callback` passed via `AgentRegistry.create_research_agent()`
3. The callback receives `{"type": "stream", "text": "<chunk>"}` dicts from `LLMClient.generate_stream()`
4. These are published as `agent_output` events to the session's `EventBus`
5. The dashboard SSE handler in `server.py` forwards events to connected clients

**Dashboard rendering (`dashboard.html`):**
- Agent progress panels detect `event_type === 'agent_output'` 
- Text chunks are appended to per-agent streaming text areas
- Icons in the event legend map event types: `agent_output → 💬`
- Long-running agents (especially the scribe) show accumulating text in real-time

**Scribe streaming:**
- The scribe agent also uses `generate_stream()` via `ScribeAgent.compile()`
- The orchestrator creates a dedicated `_make_stream_callback("scribe")` for the scribe
- The dashboard renders scribe output in a dedicated "Scribe" panel

### File Logging and System Log Tab

Two complementary logging systems were added for observability:

#### 1. File-Based Logging (`logs/deepresearch.log`)
- **Setup:** `logging.handlers.RotatingFileHandler` in `server.py`
- **Path:** `logs/deepresearch.log` (relative to project root, auto-created)
- **Rotation:** 10 MB per file, 5 backups
- **Level:** DEBUG — captures ALL deepresearch.* logger activity
- **Format:** `2026-06-14 10:30:00 [DEBUG] deepresearch.orchestrator: Session event: ...`
- **Root logger:** The handler is added to the root logger, so all child loggers benefit
- Auto-created on server start — directory and file created if absent

#### 2. In-Memory System Log Buffer (`/api/system/log`)
- **Handler:** `SystemLogHandler` — a custom `logging.Handler` that captures log records into an in-memory list
- **Buffer:** `SYSTEM_LOG` list, max 500 entries
- **Level:** INFO (captures from `deepresearch` logger tree)
- **Endpoint:**
  - `GET /api/system/log?limit=200&level=ERROR` — returns recent entries, newest first, with optional level filter
  - `POST /api/system/log/clear` — clears the buffer
- **Dashboard tab:** "📋 System Log" button opens a log viewer with:
  - Auto-refresh (polls every 2 seconds)
  - Level filter dropdown (All / ERROR / WARNING / INFO / DEBUG)
  - Clear button
  - Timestamped, color-coded entries by severity

**Use cases:**
- Debugging session failures without server console access
- Tracing LLM errors, timeout events, and connectivity issues
- Users can share log output when reporting bugs

### Model Connectivity Check: Pre-Flight

Before a session starts, `MultiSessionManager.create_session()` runs a connectivity check on the selected model:
1. Sends `"Respond with exactly one word: ok"` with `max_tokens=5`
2. 15-second timeout via `asyncio.wait_for()`
3. **On success** → session proceeds normally
4. **On failure** → session status set to `"error"` immediately; error message includes the failed model ID
- This prevents silent failures and wasted tokens on unreachable models
- The check is cheap (~5 tokens) and fast on healthy models

## Consequences

### Positive
1. Full web control: start, monitor, cancel, download from browser
2. Concurrent sessions for power users and comparison studies
3. Easy API key setup through the UI — no terminal needed
4. Local model support for private/free research (Ollama, etc.)
5. Flexible time budgets from 1 to 60 minutes
6. Zero build step — single HTML file, no npm/pip-compile needed
7. Mode-aware model selector — different UX for same/random/manual modes
8. Session deletion with file cleanup — keeps output directory tidy
9. Provider model auto-discovery — always up-to-date model lists from all configured providers
10. Pre-flight connectivity check — fails fast on unreachable models, saving time and tokens
11. **SSE event replay** — late-connecting dashboards see full session history from the start
12. **Live agent streaming** — per-agent text output panels show real-time LLM generation
13. **File-based logging** — persistent DEBUG logs survive server restarts
14. **System Log tab** — in-browser log viewer for debugging without terminal access

### Negative
1. In-memory sessions: lost on server restart (acceptable for v1.0)
2. .env file security: user must ensure file permissions are correct
3. Ollama auto-discovery requires Ollama to be running and on localhost
4. SSE connections per session tab may consume resources with many sessions
5. Connectivity check adds ~15s latency to each session start
6. Provider model discovery may fail or timeout for slow provider APIs (5s timeout per provider)
7. **Event history buffer adds memory** — 500 events per session × 20 sessions = up to 10,000 events in memory
8. **File logs may grow quickly** — DEBUG-level logging with 10MB rotation can rotate frequently on active servers
9. **Agent streaming adds SSE event volume** — each token chunk becomes a separate SSE event, increasing bandwidth

### Neutral
1. API keys in environment variables is standard practice
2. Max 20 sessions prevents memory exhaustion
3. Custom budget uses single round — deeper research still needs named budgets
4. File download path is session-scoped for isolation
5. 60s cache for auto-discovered models balances freshness with API load
6. Delete confirmation dialog prevents accidental session removal
7. Event history trimmed to 500 entries — recent history always available, oldest events are lost
8. System log buffer is in-memory (500 entries) — lost on server restart, distinct from file-based logging

## Related Issues
- #52 (Q&A Graph — Interactive visualization): Extends the frontend with a real-time SVG interaction graph showing agent-scribe communication flow.

## ADR References
- **ADR-0001** (Multi-Agent Research Architecture) — core architecture this frontend connects to
- **ADR-0002** (Agent Personality & Model Selection) — model selection and configuration

---

## Implementation Status (Updated 2026-06-15)

| Decision | Status | Notes |
|----------|--------|-------|
| FastAPI + SSE streaming | ✅ Implemented | Real-time events |
| Dark-themed dashboard | ✅ Implemented | GitHub-dark aesthetic |
| Multi-session support | ✅ Implemented | Max concurrent sessions |
| Session persistence | ✅ Implemented | sessions_db.json |
| API key management | ✅ Implemented | Settings tab |
| Model selector dropdown | ✅ Implemented | Auto-discovery + cache |
| Cancel button | ✅ Implemented | CancelEvent propagation |
| Delete/Clear sessions | ✅ Implemented | With file cleanup |
| 3-column layout | ✅ Implemented | Agent progress + sidebar |

**Deviations from original design:**
- Added Q&A panel and visual graph (ADR-0005 scope)
- Added state badges instead of progress bars
- Added scribe output panel
- Added pipeline visualization at top
- Session reconnection with state fetch

**Implemented beyond original scope:**

| Feature | Status | Reference |
|---------|--------|-----------|
| Q&A panel and visual graph | ✅ Implemented | ADR-0008 |
| Pipeline visualization bar | ✅ Implemented | ADR-0008 |
| Scribe output panel | ✅ Implemented | ADR-0008 |
| Collapsible agent logs | ✅ Implemented | ADR-0008 |
| Session reconnection with state fetch | ✅ Implemented | ADR-0008 |
| Demo page for layout testing | ✅ Implemented | ADR-0008 |
