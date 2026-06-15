---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0002: Agent Personality & Model Selection

## Status

Proposed

**Version:** 1.4
**Last Updated:** 2026-06-14

## Context

DeepeResearch relies on distinct agent personalities to produce multi-perspective research. Each agent must have a consistent, differentiated voice and methodology. Additionally, users need flexibility in assigning LLM models to agents — different models have different strengths, costs, and personalities of their own.

Beyond the original personality system, the following requirements emerged:
1. **Custom time budgets** — users want fine-grained control over research depth (not just quick/medium/deep presets)
2. **Local model support** — Ollama, llama.cpp, vLLM, and OpenAI-compatible endpoints for private/free research
3. **API key management** — users need a UI for configuring API keys across multiple providers without touching terminal or `.env` files directly
4. **Provider prefix routing** — model IDs like `opencode/go` should auto-route to the correct provider's API base and API key without manual configuration
5. **Model connectivity check** — before starting a session, the selected model should be tested with a minimal prompt to verify availability (15s timeout, immediate error on failure)
6. **Model selector UI** — the web dashboard needs visual model selection: dropdown for "same" mode, per-agent selectors for "manual" mode, info text for "random" mode
7. **Opencode AI provider** — a new, free-tier provider as the default model, accessible via `OPENCODE_API_KEY`

### Key Forces
1. **Personality consistency** — agents must maintain their persona across multiple rounds and interactions
2. **Personality distinctness** — each agent's output must be clearly distinguishable from others
3. **Model selection modes** — users need different levels of control over which model powers which agent
4. **Model role-play quality** — some models are better at maintaining personas than others
5. **Temperature tuning** — temperature must be calibrated per personality for optimal role-play
6. **Extensibility** — users should be able to add custom profiles
7. **Custom time budgets** — named presets are insufficient for power users who want precise duration control
8. **Local models** — Ollama/llama.cpp/vLLM provide free, private alternatives to cloud APIs
9. **API key management** — 9+ cloud providers need key configuration, preferably through the web UI
10. **Provider routing** — model IDs should self-describe their provider to simplify multi-provider configuration
11. **Pre-flight validation** — model unreachability should be detected before a session consumes tokens and time
12. **Web-first model selection** — the dashboard must provide an intuitive model picker that adapts to the selected mode

### Prior Art / Alternatives Considered
| Approach | Pros | Cons |
|----------|------|------|
| System prompt only | Simple, easy to debug | Insufficient for nuanced personalities |
| Few-shot examples | Stronger persona enforcement | Token-heavy, expensive |
| Temperature tuning only | Zero token overhead | Weak differentiation alone |
| Post-processing | Can correct persona drift | Complex, may distort content |
| Layered prompt composition | Modular, testable, extensible | More complex prompt structure |

## Decision

### Personality Architecture: Layered Prompt Composition

Each agent's personality is defined by **5 independent prompt components** combined at generation time:

1. **Persona Prompt** — Core identity description ("You are...")
2. **Methodology** — How the agent approaches research
3. **Knowledge Base** — What the agent knows (and doesn't)
4. **Bias Mitigation** — Self-awareness instructions for known biases
5. **Voice** — Writing style, tone, vocabulary, sentence structure

These components are compiled into a single system prompt per generation call. Components are stored as separate fields in the AgentProfile model for independent testing and modularity.

### Persona Enforcement: Temperature + Voice + Output Structure

| Profile | Temperature | Enforcement Strategy |
|---------|------------|---------------------|
| Curious Teenager | 0.85 | Voice instructions + short paragraph constraint |
| Skeptical Academic | 0.35 | Methodology rules + citation requirement |
| Creative Artist | 0.90 | Analogies required + metaphorical language |
| Pragmatic Engineer | 0.45 | Structure: problem → solution → impact |
| Philosophical Thinker | 0.75 | Ethical framework + "what if" exploration |
| Data Analyst | 0.30 | Data-first approach + statistical language |

### Model Selection Modes

Three modes controlled via `ModelConfig.mode`:

**Mode A — Same Model (default)**
- All agents use the same model (e.g., `gpt-4o`)
- Simplest, most predictable
- Personality differentiation comes entirely from prompts

**Mode B — Random Assignment (seeded)**
- Each agent assigned a random model from a configured pool
- Deterministic when `seed` is set
- Creates emergent diversity from model differences

**Mode C — Manual Per-Agent (CLI)**
- User specifies which model each agent uses via CLI wizard or config file
- Full control for power users

### Assignment Algorithm

1. If mode is `same`: assign `default_model` to all agents
2. If mode is `manual`: apply per_agent_overrides dict
3. If mode is `random`: shuffle model pool with seed; assign round-robin
4. Validate each assigned model is available (pre-flight check)
5. Fallback to `default_model` if assigned model unavailable

### Profiles: YAML-Based Extensibility

- Built-in profiles stored in `profiles/default.yaml`
- Fields defined in AgentProfile schema (see Data Model §5)
- User custom profiles: `~/.deepresearch/profiles/*.yaml`
- Profiles merged at startup: built-in + user
- Validation: all required fields present, temperature in [0.0, 1.0]

### Personality Differentiation Validation

To ensure agents are truly distinct, the following automated checks run in CI:

| Test | Method | Target |
|------|--------|--------|
| Semantic Similarity | Embedding cosine distance between agent outputs on same topic | Mean pairwise similarity < 0.85 (higher threshold acceptable in Mode A — same model) |
| Keyword Uniqueness | TF-IDF distinctive terms per agent | Each agent has ≥ 3 unique top keywords |
| Tonality Variance | Sentiment analysis + formality scores | Statistically significant difference (p < 0.05) |

### Scribe Agent

- No personality profile — fixed neutral academic tone
- Temperature: 0.3 (low creativity, high consistency)
- Focus on clarity, structure, and synthesis rather than perspective

### Custom Time Budget: Free-Form Minutes

- Named budgets (quick/medium/deep) remain as presets
- Custom: `time_budget_seconds` parameter, free-form minutes input via UI
- Custom budget defaults to single round (like quick mode)
- Session timeout = budget + 60s grace period, max 3600s (1 hour)

### Local Model Discovery: HTTP Auto-Detect + Manual Config

- **Ollama**: auto-discover via `GET http://localhost:11434/api/tags` using httpx
- **llama.cpp / vLLM**: manual endpoint configuration (name, URL, type)
- Endpoints stored in `~/.deepresearch/local_endpoints.json`
- "Test connection" button validates endpoint before saving
- Discovered models appear in model selection dropdown alongside cloud providers

### Provider Prefix Routing

The LLMClient (`llm/client.py`) auto-detects the provider from the model ID using `PROVIDER_ROUTES`:

```python
PROVIDER_ROUTES = {
    "opencode": {
        "type": "endpoint_routed",
        "api_key_env": "OPENCODE_API_KEY",
        "openai_compatible": True,
        "endpoints": {
            "go": "https://opencode.ai/zen/go/v1",
            "zen": "https://opencode.ai/zen/v1",
        },
    },
    "openrouter": {"api_base": "https://openrouter.ai/api/v1",   "api_key_env": "OPENROUTER_API_KEY"},
    "groq":       {"api_base": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "together":   {"api_base": "https://api.together.xyz/v1",    "api_key_env": "TOGETHER_API_KEY"},
    "deepseek":   {"api_base": "https://api.deepseek.com/v1",    "api_key_env": "DEEPSEEK_API_KEY"},
    "cohere":     {"api_base": "https://api.cohere.ai/v1",       "api_key_env": "COHERE_API_KEY"},
    "gemini":     {"api_base": "https://generativelanguage.googleapis.com", "api_key_env": "GEMINI_API_KEY"},
    "anthropic":  {"api_base": "https://api.anthropic.com",      "api_key_env": "ANTHROPIC_API_KEY"},
    "ollama":     {"api_base": "http://localhost:11434",          "api_key_env": None},
}
```

#### 3-Part Model ID Format (Endpoint-Routed Providers)

Opencode AI uses a **3-part model ID format** to simultaneously specify provider, endpoint, and model:

```
opencode/{endpoint}/{model-name}
```

| Model ID | Endpoint | Actual Model Passed to LiteLLM |
|----------|----------|-------------------------------|
| `opencode/go/deepseek-v4-flash` | Go (`https://opencode.ai/zen/go/v1`) | `deepseek-v4-flash` |
| `opencode/zen/claude-sonnet-4` | Zen (`https://opencode.ai/zen/v1`) | `claude-sonnet-4` |

**Resolution logic in `LLMClient.__init__`:**
1. Extract the prefix (`opencode`) → look up in `PROVIDER_ROUTES`
2. Detect `"type": "endpoint_routed"` → parse the second segment as the endpoint name
3. The third segment becomes the `actual_model` passed to LiteLLM
4. If the provider is marked `openai_compatible`, the model is prefixed with `openai/` for LiteLLM (e.g., `openai/deepseek-v4-flash`)
5. The `api_base` is set to the endpoint's URL, and `api_key` is read from `OPENCODE_API_KEY`

**Provider override:** A `provider` parameter can override auto-detection. For example, `provider="openrouter"` with `model="opencode/zen/claude-sonnet-4"` routes through OpenRouter's API base instead of Opencode's.

**Standard providers** (non-endpoint-routed) use a simpler 2-part format: `{provider}/{model-name}` (e.g., `groq/llama-3.3-70b`). The prefix maps directly to `api_base` and `api_key_env`.

No manual provider configuration needed — just use the right model ID prefix.

### Agent Streaming Output: generate_stream()

Agent LLM responses are now streamed in real-time for live dashboard rendering:

**Core method — `LLMClient.generate_stream()`:**
- Calls LiteLLM's `acompletion()` with `stream=True`
- Iterates over async chunks and delivers each text delta to an optional `event_callback`
- The callback receives `{"type": "stream", "text": "<chunk>"}` dicts

**Fallback behavior:**
- If streaming fails (network error, model doesn't support streaming, etc.), `generate_stream()` falls back to `generate()` (non-streaming)
- The full response is delivered as a single chunk via the callback
- This ensures streaming is never a blocker — agents work regardless of streaming support

**Integration with orchestrator:**
- `Orchestrator._make_stream_callback(agent_id)` creates a callback per agent
- Callbacks publish chunks as `agent_output` events to the `EventBus`
- The dashboard listens for `agent_output` events and appends text to per-agent panels
- Agents and scribe both use `generate_stream()` — see `ResearchAgent._generate_with_retry()` and `ScribeAgent.compile()` which call `generate_stream()` internally

**Benefits:**
- Users see live text as agents generate responses (not just state transitions)
- Long-running scribe compilations (2-5 minutes) become transparent
- Fallback guarantees no regression if streaming is unavailable

### Web Search Tool for Agents: DuckDuckGo Function Calling

Research agents can search the web in real-time via LiteLLM function calling:

- **Tool definition:** `WEB_SEARCH_TOOL` in `tools/web_search.py` — a LiteLLM-compatible function-calling schema with `name="web_search"`, requiring a `query` string and accepting optional `max_results` (default 5, max 10). The description tells the LLM to use it for up-to-date facts, recent developments, or external sources.
- **Execution:** `web_search()` queries DuckDuckGo via `duckduckgo_search.DDGS`, running in a thread via `asyncio.to_thread()` to avoid blocking the event loop. Returns structured results (`title`, `snippet`, `url`) or an error dict on failure.
- **Integration:** `ResearchAgent.research_round_1()` now calls `LLMClient.generate_with_tools()` with `WEB_SEARCH_TOOL` instead of the standard `_generate_with_retry()` — giving agents live web access during their initial research pass
- **Fallback:** If `generate_with_tools()` raises `LLMError`, the agent retries without tools via `_generate_with_retry()` — ensuring uninterrupted research even if the tool-calling path fails
- **Multi-turn loop:** Up to 5 tool-call rounds per generation (`max_tool_rounds = 5`), preventing infinite loops while allowing the agent to make multiple search queries in one generation

### Model Connectivity Check: Pre-Flight Validation

Before a session starts, the `MultiSessionManager.create_session()` runs a connectivity check:

1. **Test model selection**: uses `selected_model` or falls back to `opencode/go`
2. **Minimal prompt**: sends `"Respond with exactly one word: ok"` with `max_tokens=5`
3. **Timeout**: 15 seconds (`asyncio.wait_for`)
4. **On success**: session proceeds as normal
5. **On failure**: session status is set to `"error"` immediately, error message recorded, no LLM tokens wasted

This prevents silent failures that waste time and tokens. The check is fast and cheap — approximately 5 tokens for the test.

### Model Selector UI (Web Dashboard)

The dashboard provides three UX patterns matching the model selection modes:

- **"Same" mode** (`mode="same"`) — a single `<select id="modelSelector">` dropdown populated from `/api/models`. The default is `opencode/go`.
- **"Manual" mode** (`mode="manual"`) — per-agent `<select class="agent-model-select">` dropdowns, one for each agent profile. Agent profiles are fetched from `/api/profiles`.
- **"Random" mode** (`mode="random"`) — info text: "🎲 Random model from configured pool" — no model selection needed.

The model selector is fetched asynchronously on page load. The `/api/models` endpoint returns models from all sources (see Provider Model Auto-Discovery below). Default model (`opencode/go`) is pre-selected.

### Provider Model Auto-Discovery

The `/api/models` endpoint in `server.py` goes beyond the curated `models.yaml`:

1. **Curated models**: loaded from `src/config/models.yaml` (opencode/go, opencode/zen, gpt-4o, etc.)
2. **Provider API models**: fetched from ALL configured providers via their REST APIs:
   - `https://api.openai.com/v1/models` (OpenAI)
   - `https://openrouter.ai/api/v1/models` (OpenRouter)
   - `https://api.anthropic.com/v1/models` (Anthropic)
   - `https://api.groq.com/openai/v1/models` (Groq)
   - `https://api.together.xyz/v1/models` (Together)
   - `https://api.deepseek.com/v1/models` (DeepSeek)
   - `https://api.generativelanguage.googleapis.com/v1/models` (Google)
   - `https://api.cohere.ai/v1/models` (Cohere)
3. **Ollama local models**: discovered via `GET http://localhost:11434/api/tags` (auto-detected)
4. **Local endpoints**: manually configured llama.cpp/vLLM endpoints from `~/.deepresearch/local_endpoints.json`

**Caching**: results are cached for 60 seconds to avoid excessive API calls. Each provider API call has a 5-second timeout. Duplicates (same model ID from multiple sources) are de-duplicated — curated `models.yaml` takes precedence.

### Opencode AI Provider

Opencode AI is added as the 9th supported provider, with a dual-endpoint architecture:

- **Environment variable**: `OPENCODE_API_KEY`
- **Go endpoint**: `https://opencode.ai/zen/go/v1` — used via `opencode/go/{model-name}` (default)
- **Zen endpoint**: `https://opencode.ai/zen/v1` — used via `opencode/zen/{model-name}`
- **Default model**: `opencode/go/deepseek-v4-flash` (the new system default)
- **Secondary model**: `opencode/zen/claude-sonnet-4`
- **OpenRouter access**: also available via `openrouter/opencode/go/{model}` and `openrouter/opencode/zen/{model}`
- **Cost**: free tier (cost rate 0.0 in `client.py`)
- **Settings manager**: registered in `setting_manager.PROVIDERS` dict alongside the 8 existing providers
- **Model auto-discovery**: both Go and Zen endpoints have model listing APIs (`/v1/models`) that auto-discover available models
- **OpenAI-compatible**: models use `openai/` prefix for LiteLLM compatibility

Becoming the default means new users can run research immediately with just an Opencode AI key — no other provider configuration needed. The Go/zen split allows routing to different model families through the same provider.

### API Key Management: UI + Local .env File

- Keys stored in `~/.deepresearch/.env`
- 9 supported providers: OpenAI, Anthropic, Groq, Google, Cohere, Together, DeepSeek, OpenRouter, Opencode AI
- Environment variables override file values at runtime
- Keys masked in UI (only prefix shown for identification)
- Web form for adding/updating keys without terminal access

## Consequences

### Positive
1. **Modular profiles** — each component independently testable
2. **Testable differentiation** — automated semantic distance checks in CI
3. **Model-agnostic** — personality is prompt-defined, not model-defined
4. **User control** — three model selection modes cover casual to power users
5. **Bias awareness** — explicit bias_mitigation field in every profile
6. **Local models** — free, private research without API costs (Ollama/llama.cpp/vLLM)
7. **Flexible budgets** — custom time budgets from 1 to 60 minutes
8. **Easy key management** — UI-based API key configuration, no terminal needed
9. **Provider prefix routing** — no manual provider configuration; just use the right model ID
10. **Pre-flight connectivity check** — catches unreachable models before sessions waste time and tokens
11. **Web-first model selection** — intuitive dropdown/per-agent selectors in dashboard
12. **Model auto-discovery** — users see all available models from all configured providers in one list
13. **Opencode AI default** — free tier enables immediate research with just one API key
14. **9 providers** — broadest model selection across cloud and local backends
15. **Live agent streaming** — users see real-time text generation in the dashboard, not just state transitions
16. **Streaming fallback** — `generate_stream()` degrades gracefully to `generate()` if streaming is unavailable
17. **Dual Opencode endpoints** — Go and Zen offer different model families through the same API key
18. **Web search tool** — agents access live internet data via DuckDuckGo, improving factuality and recency of research findings
19. **Graceful tool degradation** — `generate_with_tools()` falls back to non-streaming, then to no-tools retry, ensuring research never blocks on tool-calling failures

### Negative
1. **Token overhead** — 5-component prompt consumes more tokens than single system prompt (~200-300 extra tokens per call)
2. **Role-play quality varies** — some models are significantly better at maintaining personas (Claude ≥ GPT-4o ≥ Llama 3)
3. **User profiles may be lower quality** — custom profiles may not produce the intended personality
4. **.env file security** — user must ensure `~/.deepresearch/.env` has correct file permissions
5. **Ollama must be running** — auto-discovery requires Ollama service to be active on localhost:11434
6. **Connectivity check adds latency** — each session start is delayed by up to 15s for the pre-flight test
7. **Provider API discovery timeout** — if a provider API is slow, model loading may be delayed (5s per provider)
8. **Model ID prefix collisions** — if a model ID doesn't match any known prefix, it falls through to default OpenAI routing, which may be unexpected
9. **3-part model IDs are verbose** — `opencode/go/deepseek-v4-flash` is longer and more complex than simple provider prefixes
10. **Streaming adds server load** — per-chunk events increase EventBus publish volume and SSE bandwidth
11. **Web search depends on DuckDuckGo availability** — if DuckDuckGo is unreachable (rate-limited, blocked, or down), agents fall back to no-tools mode silently, reducing search quality

### Neutral
1. Temperature + Voice combo is the primary personality lever
2. Profiles are static — no learned personality adaptation across sessions (intentional for v1.0)
3. Model randomness mode may produce unexpected but interesting combinations
4. API keys in environment variables is standard industry practice
5. Custom budget uses single round — deeper research still needs named budgets
6. Local endpoint configuration is manual for non-Ollama backends
7. Provider model discovery is best-effort — some provider APIs return different model lists than LiteLLM supports
8. 60s cache for model lists balances freshness with API call volume

## ADR References
- **ADR-0001** (Multi-Agent Research Architecture)
- **ADR-0003** (Web Frontend & Multi-Session Architecture)

---

## Implementation Status (Updated 2026-06-15)

| Decision | Status | Notes |
|----------|--------|-------|
| 6 YAML-defined personalities | ✅ Implemented | src/profiles/default.yaml |
| Temperature-based differentiation | ✅ Implemented | 0.20–0.95 range |
| Same Model mode (default) | ✅ Implemented | All agents same LLM |
| Random Model mode | ✅ Implemented | Deterministic seed |
| Manual Model mode | ✅ Implemented | Per-agent selection |
| Provider prefix routing | ✅ Implemented | 10+ providers supported |
| Model auto-discovery | ✅ Implemented | Cloud + Ollama |
| Cost tracking | ✅ Implemented | Per-model rates |

**Deviations from original design:**
- Added Opencode AI as default provider (not in original)
- Added web search tool calling — agents search DuckDuckGo
- Added `target_agent_ids` for directed questions between agents

**Implemented beyond original scope:**

| Feature | Status | Reference |
|---------|--------|-----------|
| Opencode AI as default provider | ✅ Implemented | Not in original scope |
| target_agent_ids for directed questions | ✅ Implemented | ADR-0007 |
