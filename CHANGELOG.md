# Changelog

All notable changes to DeepeResearch will be documented in this file.

## [Unreleased]

### Fixed
- **Dashboard 3-column layout** — Removed `main-grid` class from progressView div that was overriding `progress-grid`'s 3-column grid layout (`1.3fr 1.3fr 1fr`) with a 2-column layout (`1fr 360px`). The CSS cascade conflict caused the 3-column layout to never take effect despite being defined.

## [0.0.57] - 2026-06-14

### Added
- Session SSE for completed/persisted sessions
- State badges (researching, searching, writing, questioning, answering, refining, done, failed)
- 3-column dashboard layout (agents left, agents middle, event log right)
- Scribe live streaming output panel
- Q&A panel for agent questions
- Research pipeline visualization
- Dynamic research rounds (up to 5, auto-stop when gaps < 2 & confidence >= 0.5)
- Scribe clarification with web search
- Refinement phase with agent web search

### Fixed
- Scribe clarification loop deduplication (_asked_claims tracking)
- Scribe must use actual agent names, not invent new ones
- Orchestrator package split (config, events, dry_run)
- Dashboard split into 16 ES modules + CSS (no build step)
- C1-C5, W14 code audit critical bugs
- Elapsed refresh, column widths, version log
- REFINING state, agent state transitions
