# Changelog

All notable changes to DeepeResearch will be documented in this file.

## [0.10.0] - 2026-06-17

### Added
- ADR-0011: Session concurrency limit (max 3) + web search throttling
- ADR-0012: SearXNG migration — replaced ddgs with self-hosted SearXNG
- ADR-0013: SearXNG optimization — removed DDG/Wikidata/Brave, added academic engines
- Global web search rate limiter (1 search per 5 seconds)
- Search result cache (200 entries, LRU eviction)
- SearXNG health tracking + /api/system/search endpoint
- `--rounds` CLI flag for session round count
- Dynamic pipeline rendering (ROUND3-5 support in dashboard)
- Academic search engines: arXiv, PubMed, Semantic Scholar, Wikipedia

### Changed
- PDF minimum healthy threshold: 12KB → 20KB
- Web search backend: ddgs → SearXNG (with ddgs as optional fallback)
- pyproject.toml: ddgs moved to optional extra
- Tests: 305 → 311 tests (SearXNG-mocked, feature flag, health info)

### Fixed
- Rate limiting issues (101 HTTP 429 errors from Google/Brave eliminated)
- PDF underweight sessions (10-12KB → 20-29KB)
- DuckDuckGo captcha blocks (658 errors removed by engine removal)
- Quick/medium session PDF sizes (all now above 20KB threshold)

## [0.7.0] - 2026-06-15

### Added
- GitHub Actions CI pipeline (test + lint + build on push/PR)
- GitHub Actions release pipeline (PyPI + Docker + npm + GitHub Release on tag push)
- npm wrapper package (`npm install -g deepresearch`)
- Dockerfile with multi-stage build (amd64 + arm64)
- Docker healthcheck via curl
- Supply chain security (PyPI OIDC, npm provenance, cosign signing)
- Trivy vulnerability scanning in Docker pipeline
- Version sync check between pyproject.toml and npm/package.json

## [0.6.0] - 2026-06-15

### Fixed
- **Quick budget too short** — Increased from 2 to 5 minutes (300s) to allow meaningful research
- **Timeout floor too low** — Increased per-agent timeout floor from 30s to 120s to prevent premature agent termination
- **Round 2 always skipped for quick/custom** — Removed budget-based Round 2 skip logic; now runs whenever gaps warrant it
- **Empty results not detected** — Agents returning None/empty results now marked as failed instead of silently accepted
- **DuckDuckGo search failures** — Added retry with exponential backoff (3 attempts, 1s/2s/4s delays)
- **All-agent failure shows "complete"** — Sessions now show "error" status when all agents fail instead of misleading "complete"

## [0.4.0] - 2026-06-15

### Fixed
- **FATAL: Empty PDF from clarification loop** — Scribe clarification protocol now capped at 5 rounds and 3-minute time budget. Empty agent responses are skipped (no wasted recompilation). 2 consecutive empty responses stops the entire protocol.
- **Agent output lost on session reconnection** — Output buffer flushed before renderAgents saves state, preventing race condition that lost accumulated text.
- **Agent log collapse state not preserved** — Uses classList instead of style.display check.
- **Agent progress shows "done" during scribe** — Changed to "waiting" during compilation phase.
- **Q&A text truncated** — Removed white-space:nowrap, added word-break.
- **Scribe output too small** — Max-height increased from 80px to 250px.
- **Missing asyncio import** — Fixed NameError in scribe_agent.py.

## [0.3.0] - 2026-06-15

### Added
- Directed agent questions — agents can target specific agents by expertise via `target_agent_ids` in FollowUpQuestions
- Parallel clarification — scribe fires clarification requests as concurrent asyncio tasks instead of sequential await
- Force-stop cancel — `cancel_event` propagated through sessions→orchestrator→agent→LLM client, cancel takes effect within seconds

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
