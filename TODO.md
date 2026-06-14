# DeepeResearch — TODO

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

## Next Testing Session

### Priority 1: Verify latest fixes
- [ ] **Scribe model prefix** — scribe should use full model ID (e.g., `opencode/go/deepseek-v4-flash`)
- [ ] **Agent JSON parsing** — agents should return valid JSON after web search
- [ ] **Web search in dashboard** — 🔍 search results visible in agent output panels
- [ ] **Scribe row in dashboard** — 📝 scribe row with live output under agents
- [ ] **Dynamic rounds** — verify it loops when gaps exist, stops when resolved

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
