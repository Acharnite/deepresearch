# Changelog

All notable changes to DeepeResearch will be documented in this file.

## [1.5.0] - 2026-06-20
### Added
- ADR-0018: Native llama.cpp backend integration (binary lifecycle management)
- 6 new API endpoints for llama.cpp lifecycle (status/install/uninstall/start/stop/restart)
- Platform-aware binary download from GitHub releases (Linux GPU detection, macOS, Windows)
- llama.cpp management card in Settings → Local Models (status, install, uninstall, start/stop)
- 17 new tests for llama.cpp endpoints (540 total, all passing)
