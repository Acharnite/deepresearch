# Changelog

All notable changes to DeepeResearch will be documented in this file.

## [0.2.0] - 2026-06-15

### Added
- Pipeline bar at top of dashboard with full state sequence including REFINING
- Scribe card above agent progress with compact layout and live streaming
- Collapsible agent output logs with ▾/▴ toggle
- Spinning indicator animation on active agent states
- Colored left borders per agent in output logs
- Demo page (static/demo.html) with mock data for layout testing
- Session state API endpoint (GET /api/sessions/{id}/state) for reconnection
- Clarification status events (identifying_claims, asking_agent, recompiling)
- Parallel refinement phase (asyncio.gather instead of serial await)

### Changed
- Wider 16:9 layout (1600px max-width, 2-column grid)
- Agent cards interleaved — each agent's header and output rendered together
- Agents start with output logs minimized by default
- Scribe moved from sidebar to above agent progress section
- Refinement runs agents in parallel instead of serially
- Compile prompt explicitly lists agent names to prevent invention
- Token exhaustion errors (BudgetExceededError, ContextWindowExceededError, RateLimitError) fail immediately without retry

### Fixed
- Agent logs stay collapsed when new content arrives (collapsed state preserved across re-renders)
- PDF filename overflow — long names now word-break instead of overflowing container
- Page no longer scrolls to top when agent badge status updates (scroll position preserved)
- Session reconnection restores agent states, elapsed timer, and event history
- Scribe clarification shows status events instead of appearing frozen
- Agent output panels no longer separated from their headers

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
