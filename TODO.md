# DeepeResearch — TODO

## Completed
- [x] Orchestrator FSM — IDLE → CONFIGURING → ROUND1 → COLLABORATING → FOLLOWUP → ROUND2 → COMPILING → OUTPUT → COMPLETE
- [x] 6 agent personality profiles (YAML-based)
- [x] Same / Random / Manual model assignment modes
- [x] LiteLLM async client with retry and streaming
- [x] Collaboration bus (SharedKnowledge aggregation)
- [x] Scribe agent compilation pipeline
- [x] PDF generation via Jinja2 + WeasyPrint (HTML fallback)
- [x] Web dashboard (FastAPI + SSE streaming)
- [x] Multi-session management (create, delete, list, cancel)
- [x] Tool calling (DuckDuckGo web search via function calling)
- [x] Settings management UI (API keys, local endpoints)
- [x] Provider prefix routing (opencode/, openrouter/, ollama/, etc.)
- [x] Provider model auto-discovery (/api/models endpoint)
- [x] Orchestrator unit tests (32 tests, all passing)
- [x] PDF generation tests (all passing)
- [ ] ... (growing list)

---

## Next Testing Session

### Priority 1: Web Search Tool
- [ ] Test `web_search("quantum computing 2026")` returns real results
- [ ] Test `generate_with_tools()` with web_search tool — agent actually calls the tool
- [ ] Test streaming + tool calling works together (chunks arrive while tool executes)
- [ ] Test fallback when streaming+tool calling fails (non-streaming path)

### Priority 2: Full Pipeline (CLI)
- [ ] Run `deepresearch run "topic" --quick` and verify:
  - [ ] All 6 agents complete Round 1
  - [ ] Collaboration phase works
  - [ ] Scribe compiles paper
  - [ ] PDF is generated with real content
- [ ] Run `deepresearch run "topic" --medium` and verify Round 2 runs
- [ ] Run `deepresearch run "topic" --deep` and verify full pipeline

### Priority 3: Error Handling
- [ ] Kill network mid-session — verify graceful degradation
- [ ] Use an invalid model — verify error message is clear
- [ ] Empty topic — verify validation catches it

### Priority 4: Model Compatibility
- [ ] Test with OpenAI models (gpt-4o)
- [ ] Test with Ollama models (qwen3:8b)
- [ ] Test with OpenRouter models
- [ ] Test with Opencode AI (go/zen endpoints)

### Priority 5: Performance
- [ ] Measure Round 1 completion time
- [ ] Measure scribe compilation time
- [ ] Measure total session time vs time budget
- [ ] Check log file size after 3+ sessions
