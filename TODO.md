# DeepeResearch — TODO

## ADR-0017 (2026-06-20) — Multi-Provider Search — Implemented ✅

## Completed
- [x] Orchestrator FSM with 8 states
- [x] 6 agent personality profiles
- [x] Model assignment (same/random/manual)
- [x] LiteLLM async client with streaming
- [x] Collaboration bus
- [x] Scribe agent with clarification protocol
- [x] PDF generation
- [x] Web dashboard (FastAPI + SSE)
- [x] Web search tool (DuckDuckGo via function calling)
- [x] Provider prefix routing (opencode/, openrouter/, ollama/, gemini/)
- [x] Provider model auto-discovery
- [x] Agent live streaming output
- [x] Dynamic research rounds (up to 5, stops when gaps resolved)
- [x] Scribe output visible in dashboard
- [x] Persistent file logging
- [x] Model connectivity check in background
- [x] --model flag for CLI
- [x] Dynamic rounds: continue until gaps < 2 and confidence >= 0.5
- [x] ADR-0011: Session concurrency limit (max 3) + web search throttling
- [x] ADR-0012: SearXNG migration — replaced ddgs with self-hosted SearXNG
- [x] ADR-0013: SearXNG optimization — removed DDG/Wikidata/Brave, added academic engines
- [x] ADR-0015: Fix JSON parsing (strip tool output) + topic drift (topic in Scribe prompts)  ← 97d5b29
- [x] Global web search rate limiter (1 search per 5 seconds)
- [x] Search result cache (200 entries, LRU eviction)
- [x] --rounds CLI flag for session round count
- [x] Academic search engines: arXiv, PubMed, Semantic Scholar, Wikipedia
- [x] PDF minimum healthy threshold: 12KB → 20KB

## Epic #113 Group 4 — Documentation Gaps ✅

### Completed (2026-06-26)
- [x] Fixed ADR-0018 status in design doc index (Proposed → Accepted)
- [x] Updated module structure diagram to reflect actual source layout
- [x] Expanded test file list to all 22 files
- [x] Updated CHANGES.md with v1.6.0 (post-1.5.0 changes)
- [x] Bumped VERSION.md to 1.6.0
- [x] Updated design doc to v1.8 with changelog entry

## Completed (2026-06-29)
- [x] ADR-0019 implementation: Alpine.js frontend reactivity (Phases 1–4)
- [x] Alpine.js vendored locally (removed CDN dependency for offline support)
- [x] Bug #104: Fixed model picker transparent background (added --surface-1 CSS variable)
- [x] Bug #103: Model lists now refresh after GGUF model serve/stop
- [x] Bug #110: API cleanup — response_model, SSE content-type schema, auth docs
- [x] Bug #101: Closed as outdated (llmfit removed by ADR-0020)
- [x] Tests: 8 new tests for time budget edge cases + SSE reconnection (668 total)

## Next Testing Session

### Priority 1: Verify latest fixes
- [ ] **Scribe model prefix** — scribe should use full model ID (e.g., `opencode/go/deepseek-v4-flash`)
- [x] **Agent JSON parsing** — agents should return valid JSON after web search (see ADR-0015: _strip_tool_output)
- [x] **Web search in dashboard** — 🔍 search results visible in agent output panels
- [x] **Scribe row in dashboard** — 📝 scribe row with live output under agents
- [x] **Dynamic rounds** — verify it loops when gaps exist, stops when resolved

### Priority 2: Full Pipeline
- [ ] CLI: `deepresearch run "topic" --quick --model "opencode/go/deepseek-v4-flash"`
- [ ] CLI: `deepresearch run "topic" --medium --model "opencode/go/deepseek-v4-flash"`
- [ ] Dashboard: same flows via web UI

### Priority 3: Model Compatibility
- [ ] Test with OpenAI (gpt-4o)
- [ ] Test with Ollama (qwen3:8b)
- [ ] Test with OpenRouter
- [ ] Test with Opencode Zen endpoint

### Priority 4: Performance
- [ ] Measure Round 1 + web search time
- [ ] Measure scribe compilation time
- [ ] Check log file size after 3+ sessions
- [ ] Verify no memory leaks over multiple sessions
