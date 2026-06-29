# Changelog

All notable changes to DeepeResearch will be documented in this file.

## [1.8.0] - 2026-06-29
## [1.9.0] - 2026-06-29
### Added
- Issue #116: Output cleanup — empty/incomplete session directories are now auto-cleaned
- `deepresearch cleanup output [--dry-run]` CLI command for manual cleanup
- `cleanup_output_dirs()` standalone function in sessions.py — scans output/ dirs, removes empty/trivial ones
- `_has_meaningful_output(session_id)` — detects dirs with PDF/HTML output
- `_remove_output_dir(session_id)` — removes dir only if no meaningful output exists
- `clear_completed()` now auto-cleans empty output dirs (dirs with PDF/HTML preserved)

### Changed
- `clear_completed()` no longer leaves empty/incomplete session dirs on disk
- Session output dirs with PDF or HTML are always preserved

### Test
- 17 new tests for output cleanup logic (now 685 tests, all passing)


### Added
- ADR-0019 implementation: Alpine.js frontend reactivity (Phases 1–4)
- Alpine.js v3.14.8 via CDN for reactive DOM patching (replaces innerHTML builds)
- `Alpine.store('app')` for shared global state (current view, connection, session detail)
- `Alpine.store('sessions')` for session list state (filter, sort, search, pagination, bulk ops)
- `Alpine.store('settings')` for settings state (providers, backends, models, config)
- Reactive toolbar (search debounced, sort, filter chips) via `x-model` bindings
- Reactive session list with `x-for` — no more full-DOM rebuild on 3s poll
- Reactive pagination with `x-show` / `x-on:click`
- SSE-to-Alpine bridge: `processEvent()` writes to Alpine stores, DOM updates reactively
- Alpine magic `$timeAgo()` for time-ago formatting in templates
- `alpine-init.js` — store initialization script that runs before Alpine CDN loads

### Changed
- Session list: ~340 → ~90 LOC (removed `renderToolbar`, `renderSessionRow`, `renderPagination`, `bindToolbarEvents`, `bindBulkEvents`)
- Settings: all loader functions now dual-write to Alpine store alongside DOM
- Polling writes to `Alpine.store('sessions').list` instead of `innerHTML`
- View switching uses `Alpine.store('app').currentView` with `x-show` (alongside legacy `.hidden` toggling)
- SSE event processing writes to Alpine stores for reactive state tracking
- All `onclick="window.*"` replaced with `@click="$store.app.*"` in header navigation

### Removed
- Manual DOM manipulation code: `document.getElementById().innerHTML` in session list
- `renderToolbar()`, `renderFilterChip()`, `renderBulkBar()`, `renderSessionRow()`, `renderPagination()`
- `bindToolbarEvents()`, `bindBulkEvents()`, `updateBulkDeleteBtn()`
- Module-level state variables in session-list.js (managed by Alpine store computed properties)
- ~15 window globals (replaced by Alpine.store and exported functions)

### Documentation
- ADR-0019 status: Proposed → Accepted
- ADR-0019 added Implementation section with complete phase manifest

## [1.7.0] - 2026-06-27
### Added
- ADR-0020: Remove llmfit dependency — Phase 1 and Phase 2 implementation complete
- Python hardware detection via `psutil` + `nvidia-smi` subprocess (replaces `llmfit system --json`)
- `llama-server -hf` serving endpoint for direct HuggingFace model download-and-serve

### Removed
- llmfit dependency fully removed: hardware detection, model recommendations, and GGUF downloads
- `llmfit install` / `llmfit uninstall` endpoints removed
- Model recommendations engine and UI removed (unreliable — 12/15 models undownloadable)
- `GET /api/tools/recommendations` and `GET /api/hardware` endpoints removed

### Changed
- GGUF model acquisition now uses `llama-server -hf <user>/<model>:<quant>` (single-step download + serve)
- ADR-0020 promoted from Proposed to Accepted

### Documentation
- ADR-0020 status: Proposed → Accepted
- ADR-0005: Added superseded note referencing ADR-0020
- ADR-0018: Resolved `-hf` deferred decision — Accepted per ADR-0020

## [1.6.0] - 2026-06-26
### Added
- ADR-0017: Enhanced Tool Calling with Multi-Provider Web Search (Brave, DuckDuckGo, Google PSE, SearXNG, Serper, Tavily)
- ADR-0019: Frontend Reactivity Strategy (Alpine.js architecture)
- Per-session log files for isolated debugging (`logs/session-<id>.log`)
- llmfit integration: research-optimized model sorting, research filter (`--capability tool_use --min-fit good`), `--recommend -n 20`, auto-select top research model
- Filter model recommendations by installed backends + delete model button
- Enriched search streaming with ADR-0017 fields; MoE annotation in model recommendations
- Persistent download progress that survives page refresh

### Fixed
- #100 Local LLM — 5 bugs fixed (session save field name, RAM filter, Ollama pull, download timeout, MoE UI)
- Backend-frontend API matching audit — 5 issues resolved
- SSE buffer overflow in stream reader
- Sessions save using wrong field name (`model` → `selected_model`)
- Download progress: show 1-3% pre-progress during setup, track last known percentage
- 3 CRITICAL test false positives + 11 WARNING-level test findings (issue #111)
- Route inspection tests broken by `server.py` split
- ADR-0018 v1.2: Qwen3 thinking+tools conflict resolved in client.py

### Changed
- Refactored `web/server.py` into `web/routes/` package with dedicated route modules
- Ponytail-audit cleanup: deleted dead code, split server.py monolith
- Resolved all lint errors across tests/ and source code
- Auto-formatted 3 test files

### Test
- 49 new tests for local backends, LLM client, agents (now 600+ tests, all passing)

## [1.5.0] - 2026-06-20
### Added
- ADR-0018: Native llama.cpp backend integration (binary lifecycle management)
- 6 new API endpoints for llama.cpp lifecycle (status/install/uninstall/start/stop/restart)
- Platform-aware binary download from GitHub releases (Linux GPU detection, macOS, Windows)
- llama.cpp management card in Settings → Local Models (status, install, uninstall, start/stop)
- 17 new tests for llama.cpp endpoints (540 total, all passing)
