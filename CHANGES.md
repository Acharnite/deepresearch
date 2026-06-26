# Changelog

All notable changes to DeepeResearch will be documented in this file.

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
