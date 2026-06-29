# DeepeResearch — Design Document
**Version:** 2.0
**Status:** Active
**Design Authority:** Architects
**Last Updated:** 2026-06-29

## 1. Purpose & Scope

### Purpose
DeepeResearch is a multi-agent AI research system that generates comprehensive, multi-perspective research papers on any given topic. Multiple AI agents with distinct personalities, research methodologies, and worldviews collaborate in rounds to produce a nuanced, well-rounded final output that no single-perspective system could match.

### Scope
**In scope:**
- Orchestrator agent that manages the full research workflow (topic intake, configuration, round management, compilation trigger)
- 5–6 research agents with distinct personality profiles (e.g., curious teenager, skeptical academic, creative artist, pragmatic engineer, philosophical thinker, data-driven analyst)
- Scribe agent that compiles all findings into a coherent research paper
- Parallel agent execution using asyncio
- Multi-round research workflow with collaboration and follow-up stages
- PDF output generation
- Three model-selection modes: single model for all, random assignment, manual per-agent selection
- Time-budget integration (agents adapt depth to available time)
- Web dashboard with real-time SSE streaming, model selector UI, session management (delete/clear), and settings manager
- Provider prefix routing — model IDs auto-detect API base and key via prefix
- Provider model auto-discovery — fetches model lists from all configured provider APIs
- Model connectivity pre-flight check — tests model before session start
- Multi-provider web search (SearXNG, DuckDuckGo, Brave, Google PSE, Tavily, Serper) with tool calling, parallel content fetching, and search caching — initially via ADR-0006, enhanced per ADR-0017

**Out of scope:**
- Persistent cross-session agent memory (agents are stateless per session)
- Multi-language output (English only in v1.0)
- Citation or bibliography generation from external sources

### Stakeholders
- **End users:** Researchers, writers, students, curious minds who want multi-perspective analysis
- **Operators:** KodeHold agents running the DeepeResearch pipeline
- **Developers:** Engineers building and maintaining the system

## 2. Requirements

### Functional Requirements

| ID | Requirement | Priority | Description |
|----|-------------|----------|-------------|
| F1 | Topic Input | P0 | User can provide a research topic via CLI argument |
| F2 | Time Budget | P0 | User can specify a time budget (minutes) for the research session; agents adapt depth accordingly |
| F3 | Model Selection Modes | P0 | Three modes: (a) same model for all agents, (b) random assignment per agent, (c) manual per-agent selection |
| F4 | Agent Personalities | P0 | System supports 5–6 distinct research agent personalities out of the box |
| F5 | Parallel Execution | P0 | Research agents execute in parallel during each round |
| F6 | Round 1 — Independent Research | P0 | Each agent researches the topic independently and records findings |
| F7 | Collaboration & Sharing | P0 | After Round 1, agents can see each other's findings |
| F8 | Round 2 — Refined Research | P0 | Each agent produces a refined individual report informed by shared knowledge |
| F9 | Scribe Compilation | P0 | A scribe agent compiles all individual reports into a coherent research paper |
| F10 | PDF Output | P0 | The final research paper is output as a PDF file |
| F11 | Agent Profiles Listing | P1 | CLI command to list available agent profiles with descriptions |
| F12 | Model Listing | P1 | CLI command to list available models |
| F13 | Dry-Run Mode | P1 | CLI flag to validate configuration without executing LLM calls |
| F14 | Quick Mode | P1 | CLI flag to skip Round 2 for faster results |
| F15 | Deep Mode | P1 | CLI flag to add Round 3 with deeper investigation |
| F16 | Cost Estimation | P2 | CLI flag to estimate token cost before running |
| F17 | Custom Agent Profiles | P2 | User can define custom agent profiles via YAML |
| F18 | Web Dashboard | P1 | Real-time web UI with SSE streaming to monitor and control research sessions |
| F19 | Model Selector UI | P1 | Dashboard provides mode-aware model selection: dropdown for "same", per-agent for "manual", info for "random" |
| F20 | Session Deletion | P1 | DELETE API endpoint + dashboard buttons to delete individual sessions or clear all completed/errored sessions |
| F21 | Provider Model Auto-Discovery | P1 | /api/models fetches model lists from all configured provider APIs (OpenAI, OpenRouter, Anthropic, Groq, Together, DeepSeek, Google, Cohere) plus Ollama local models |
| F22 | Model Connectivity Check | P1 | Before starting a session, test the selected model with a minimal prompt (15s timeout); mark session as error immediately on failure |
| F23 | Opencode AI Provider | P1 | Support Opencode AI as a provider via OPENCODE_API_KEY and opencode/go as default model |
| F24 | Provider Prefix Routing | P1 | Auto-detect provider from model ID prefix (e.g., opencode/go → Opencode AI API) |

### Non-Functional Requirements

| ID | Requirement | Priority | Description |
|----|-------------|----------|-------------|
| N1 | Parallel Execution | P0 | All research agents in a round must execute concurrently; total round time ≈ max(individual agent time) |
| N2 | Model Agnostic | P0 | System must support multiple LLM providers (OpenAI, Anthropic, Ollama, any LiteLLM-supported) |
| N3 | Time-Budget Respect | P0 | Agents must respect the configured time budget; each response generation should be capped |
| N4 | Personality Consistency | P1 | Each agent's output should be distinguishable from others based on tone, methodology, and perspective |
| N5 | Deterministic Configuration | P1 | Same inputs + same seed → reproducible agent model assignments |
| N6 | Graceful Degradation | P1 | If one agent fails (timeout/error), the session continues without it; failure is logged |
| N7 | Async I/O | P0 | All LLM calls use async I/O; no blocking synchronous calls in hot paths |
| N8 | SSE Streaming | P1 | Dashboard receives real-time updates via Server-Sent Events; events delivered within 1s of state change |
| N9 | Model Discovery Caching | P1 | Auto-discovered model lists cached for 60s to avoid excessive API calls |
| N10 | Pre-Flight Validation | P1 | Model connectivity check completes within 15s; unreachable models caught before session start |

## 3. Architecture Overview

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User CLI                                  │
│              (argparse + rich)                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Orchestrator                                   │
│         (session lifecycle, config, model assignment,            │
│          round management, error handling)                       │
└───┬──────────┬──────────┬──────────┬──────────┬─────────────────┘
    │          │          │          │          │
    ▼          ▼          ▼          ▼          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Research Agent Pool                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────┐ │
│  │ Curious  │ │Skeptical │ │ Creative │ │Pragmatic │ │Philo- │ │
│  │ Teenager │ │ Academic │ │  Artist  │ │ Engineer │ │sopher │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └───────┘ │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Collaboration Bus                                │
│     (in-memory shared knowledge repository)                      │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Scribe Agent                                  │
│        (compilation, de-duplication, synthesis,                  │
│         clarification, tone calibration, PDF rendering)          │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │   PDF Output   │
                    └───────────────┘
```

### Execution Flow

```
User        Orchestrator     Research Agents     Scribe       PDF
 │               │                  │               │          │
 │──topic────────┤                  │               │          │
 │──config───────┤                  │               │          │
 │──time budget──┤                  │               │          │
 │──model mode───┤                  │               │          │
 │               │                  │               │          │
 │               │──assign models──→│               │          │
 │               │                  │               │          │
 │               │──Round 1────────→│               │          │
 │               │  (parallel via   │               │          │
 │               │   asyncio.gather)│               │          │
 │               │←───findings──────│               │          │
 │               │                  │               │          │
 │               │──share findings──│               │          │
 │               │  (Collaboration  │               │          │
 │               │   Bus update)    │               │          │
 │               │                  │               │          │
 │               │──Round 2────────→│               │          │
 │               │  (parallel, with │               │          │
 │               │   shared context)│               │          │
 │               │←───reports───────│               │          │
 │               │                  │               │          │
 │               │──send reports────│──────────────→│          │
 │               │                  │               │          │
 │               │                  │──clarify──────│          │
 │               │                  │←─respond──────│          │
 │               │                  │──(up to 5x)───│          │
 │               │                  │               │          │
 │               │                  │               │──compile─│
 │               │                  │               │──render──→│──PDF
 │               │←─────done────────────────────────────────────│
 │◄────output────┤                  │               │          │
```

### Technology Stack

| Technology | Purpose |
|------------|---------|
| Python 3.11+ | Runtime |
| LiteLLM | Unified LLM interface (OpenAI, Anthropic, Ollama, and 100+ providers) |
| asyncio | Parallel agent execution |
| Pydantic v2 | Data models, configuration validation |
| PyYAML | Agent profile definitions |
| WeasyPrint | HTML+CSS → PDF rendering |
| Jinja2 | Paper HTML template rendering |
| argparse + rich | CLI interface |
| FastAPI + uvicorn | Web server and REST API |
| SSE (Server-Sent Events) | Real-time streaming to dashboard |
| httpx | Async HTTP client for model auto-discovery |
| pytest + pytest-asyncio | Testing |

### Module Structure

```
workspaces/deepresearch/
├── pyproject.toml
├── src/
│   └── deepresearch/
│       ├── __init__.py
│       ├── __main__.py              # Entry point: python -m deepresearch
│       ├── main.py                  # CLI entry point & config orchestration
│       ├── models.py                # Core Pydantic data models
│       ├── schemas.py               # API schemas and request/response models
│       ├── constants.py             # Application constants
│       ├── service_manager.py       # Service lifecycle management
│       │
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── base_agent.py        # Abstract ResearchAgent base class
│       │   ├── registry.py          # Agent profile registry and discovery
│       │   ├── research_agent.py    # Concrete research agent implementation
│       │   └── scribe_agent.py      # Scribe agent (compilation, PDF)
│       │
│       ├── collaboration/
│       │   ├── __init__.py
│       │   └── bus.py               # CollaborationBus (in-memory)
│       │
│       ├── config/
│       │   ├── __init__.py
│       │   └── session.py           # Session configuration and validation
│       │
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── client.py            # LLMClient (LiteLLM wrapper, provider routing)
│       │   └── tracker.py           # Token usage tracking
│       │
│       ├── observability/
│       │   ├── __init__.py
│       │   ├── session_logging.py   # Per-session file logging
│       │   └── tracing.py           # Distributed tracing support
│       │
│       ├── orchestrator/
│       │   ├── __init__.py
│       │   ├── config.py            # Orchestrator-specific configuration
│       │   ├── dry_run.py           # Dry-run mode logic
│       │   ├── orchestrator.py      # Orchestrator finite state machine
│       │   ├── round_runner.py      # Round execution and agent management
│       │   ├── scribe_compiler.py   # Scribe compilation orchestration
│       │   └── session_state.py     # Session convergence state
│       │
│       ├── output/
│       │   ├── __init__.py
│       │   ├── pdf_generator.py     # PDF generation via WeasyPrint
│       │   └── templates/
│       │       ├── paper.html       # Jinja2 HTML template for paper
│       │       └── styles.css       # CSS styling for PDF output
│       │
│       ├── prompts/
│       │   ├── __init__.py
│       │   ├── research.py          # Research round prompt templates
│       │   ├── scribe.py            # Scribe compilation prompt templates
│       │   └── collaboration.py     # Collaboration/sharing prompt templates
│       │
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── cache.py             # Web search result cache (LRU)
│       │   ├── content_fetcher.py   # Parallel web content fetching
│       │   ├── parser.py            # Search result parsing
│       │   ├── registry.py          # Tool/function registry
│       │   ├── search_chain.py      # Multi-provider search chaining
│       │   ├── time_filter.py       # Time filter auto-detection
│       │   ├── web_search.py        # Web search orchestration
│       │   └── providers/
│       │       ├── __init__.py
│       │       ├── brave.py          # Brave Search provider
│       │       ├── duckduckgo.py     # DuckDuckGo provider
│       │       ├── google_pse.py     # Google PSE provider
│       │       ├── searxng.py        # SearXNG provider
│       │       ├── serper.py         # Serper.dev provider
│       │       └── tavily.py         # Tavily provider
│       │
│       ├── utils/
│       │   ├── __init__.py
│       │   └── prompts.py           # Prompt template utilities
│       │
│       └── web/
│           ├── __init__.py
│           ├── server.py             # FastAPI server (REST + SSE)
│           ├── dashboard.html        # Single-page dark-themed UI
│           ├── event_bus.py          # SSE event bus
│           ├── sessions.py           # MultiSessionManager, SessionInfo
│           ├── settings_manager.py   # API key & local endpoint management
│           ├── state.py              # Shared session state
│           ├── static/
│           │   ├── dashboard.css     # Dashboard styling
│           │   └── demo.html         # Demo/landing page
│           └── routes/
│               ├── __init__.py
│               ├── _helpers.py        # Route helper utilities
│               ├── backends.py        # Local backend management routes
│               ├── llamacpp.py        # llama.cpp lifecycle routes
│               ├── models.py          # Model discovery routes
│               ├── search.py          # Web search routes
│               ├── sessions.py        # Session management routes
│               └── settings.py        # Settings management routes
│
├── profiles/
│   └── default.yaml             # Built-in agent profiles
│
└── tests/
    ├── conftest.py              # Shared fixtures (mock LLM, profiles)
    ├── test_agents.py           # Agent execution tests
    ├── test_collaboration.py    # Collaboration bus tests
    ├── test_config.py           # Configuration loading tests
    ├── test_integration.py      # End-to-end workflow tests
    ├── test_llamacpp.py         # llama.cpp backend lifecycle tests
    ├── test_llm_client.py       # LLM client tests
    ├── test_local_backends.py   # Local backend management tests
    ├── test_models.py           # Data model tests
    ├── test_orchestrator.py     # Orchestrator FSM tests
    ├── test_pdf_generation.py   # PDF generation tests
    ├── test_polish.py           # Polish phase tests
    ├── test_system.py           # System-level tests
    ├── test_tool_cache.py       # Tool cache tests
    ├── test_tool_content_fetcher.py  # Content fetcher tests
    ├── test_tool_parser.py      # Tool parser tests
    ├── test_tool_providers.py   # Tool provider tests
    ├── test_tool_registry.py    # Tool registry tests
    ├── test_tools.py            # General tool tests
    ├── test_tool_time_filter.py # Time filter tests
    └── test_web.py              # Web dashboard & API tests
```

## 4. Component Design

### 4.1 Orchestrator (`src/deepresearch/orchestrator/orchestrator.py`)

The Orchestrator manages the full research session lifecycle via an adaptive finite state machine. Instead of a fixed 2-round sequence, the session runs a dynamic 2–5 round loop where each additional round depends on convergence criteria.

**Adaptive FSM States:**

```
CONFIGURING → ROUND1 → COLLABORATING → FOLLOWUP → REFINING → ROUNDn → … → COMPILING
```

- **CONFIGURING** — Parse config, validate parameters, assign models to agents.
- **ROUND1** — All agents perform independent initial research in parallel via `RoundRunner.run_round()`.
- **COLLABORATING** — Findings are aggregated into `SharedKnowledge` via the `CollaborationBus`. Agents review each other's perspectives and identify knowledge gaps and areas of disagreement.
- **FOLLOWUP** — Each agent formulates follow-up questions targeting other agents' findings (`RoundRunner.collect_followup_questions()`).
- **REFINING** — Agents refine their findings in response to targeted follow-up questions (`RoundRunner._run_refinement()`).
- **ROUNDn (2–5)** — Subsequent research rounds execute via `RoundRunner._run_round_n()`, passing previous findings so agents can build on prior work.
- **COMPILING** — After convergence, the scribe gathers all final reports and compiles the research paper.

**Convergence Detection (`SessionState.should_continue()`):**

After each round (starting from round 2), the Orchestrator calls `SessionState.should_continue()` to decide whether to run another round. The check evaluates six criteria in priority order:

1. **User cancellation** — `cancel_event` set by the caller (dashboard or CLI).
2. **Emergency timeout** — 30-minute absolute safety net (`MAX_SESSION_DURATION`).
3. **Max rounds reached** — Hard cap from `SessionConfig.budget.max_rounds` (default 4, range 1–10).
4. **Trend convergence** — Gap delta between consecutive rounds: if knowledge gaps and disagreements are no longer decreasing (delta ≥ 0), the session has converged.
5. **Diminishing returns** — Two consecutive non-decreasing gap deltas indicate further rounds are unlikely to yield novel insights.
6. **Confidence convergence** — (Stub) When per-agent confidence data is available, mean confidence ≥ 0.7 for 2+ rounds signals convergence. Currently gap analysis serves as the primary convergence signal.

The gap analysis tracks `gap_history` across rounds: each round's `total_gaps = len(knowledge_gaps) + len(areas_of_disagreement)`. When 3+ rounds of data show two consecutive non-decreasing deltas, the session terminates.

**Implementation:**

Round execution is delegated to `RoundRunner` (in `round_runner.py`), which provides:
- `run_round()` — Round 1/2 execution with parallel `asyncio.wait_for()` per agent
- `_run_round_n()` — Rounds 3+ with previous findings as context
- `collect_followup_questions()` — Cross-agent question gathering
- `_run_refinement()` — Targeted refinement based on follow-ups
- Agent retry logic — one automatic retry for timeout/empty/failure per round

`SessionState` (in `session_state.py`) owns convergence state: `gap_history`, `findings_history`, `current_round`, and the `should_continue()` decision logic. The `CollaborationBus` (`models.py`) computes `SharedKnowledge` including `knowledge_gaps`, `areas_of_disagreement`, and `confidence_scores`.

**Error Handling:**
- Agent timeout: `asyncio.wait_for()` per agent with configurable timeout.
- Agent failure: `asyncio.gather(return_exceptions=True)` — failed agents are logged and excluded.
- Configuration error: Raise `ConfigError` with descriptive message before any LLM calls.
- Scribe failure: Retry once, then fail with descriptive error.

**Scalability Note:** The single-process asyncio architecture is designed for 5-6 agents per session. Each agent is I/O-bound (LLM API calls), so Python's GIL is not a bottleneck. For future use cases requiring more than 20 agents per session, a distributed architecture (e.g., task queue with worker processes) would be needed. This is explicitly out of scope for v1.0.

### 4.2 Research Agents (`src/deepresearch/agents/base_agent.py`)

**Agent Lifecycle:**
1. Initialize with profile + model assignment
2. research_round_1(topic, time_budget) → Findings
3. formulate_questions(shared_knowledge) → FollowUpQuestions
4. research_round_2(topic, shared_knowledge, time_budget) → IndividualReport
5. (optional) clarify(query) → ClarificationResponse
6. write_report() → IndividualReport

**Abstract Methods (ResearchAgentInterface):**

| Method | Input | Output |
|--------|-------|--------|
| `research_round_1` | topic, time_budget | Findings |
| `formulate_questions` | shared_knowledge | FollowUpQuestions |
| `research_round_2` | topic, shared_knowledge, time_budget | IndividualReport |
| `write_report` | — | IndividualReport |
| `clarify` | query | ClarificationResponse |

**Profile Definition Fields:**

```python
class AgentProfile(BaseModel):
    id: str                    # Unique identifier (e.g., "curious_teenager")
    name: str                  # Display name (e.g., "Curious Teenager")
    emoji: str                 # Emoji for UI (e.g., "🧑‍🎤")
    persona_prompt: str        # Core persona description
    methodology: str           # Research methodology description
    knowledge_base: str        # Domain expertise description
    bias_mitigation: str       # Known biases and mitigation strategy
    voice: str                 # Writing voice/tone instructions
    temperature: float         # LLM temperature (0.0–1.0)
```

**Built-in Agent Personalities:**

| ID | Name | Emoji | Approach | Temperature |
|----|------|-------|----------|-------------|
| curious_teenager | Curious Teenager | 🧑‍🎤 | Asks "why" repeatedly, explores tangents, excited discovery | 0.85 |
| skeptical_academic | Skeptical Academic | 🧑‍🏫 | Cites sources, challenges assumptions, rigorous methodology | 0.35 |
| creative_artist | Creative Artist | 🎨 | Draws analogies, visual thinking, unexpected connections | 0.90 |
| pragmatic_engineer | Pragmatic Engineer | 🔧 | Focus on practical applications, feasibility, real-world impact | 0.45 |
| philosophical_thinker | Philosophical Thinker | 🤔 | Explores deeper meaning, ethics, implications for humanity | 0.75 |
| data_analyst | Data Analyst | 📊 | Looks for patterns, statistics, quantitative evidence | 0.30 |

### 4.3 Scribe Agent (`src/deepresearch/agents/scribe_agent.py`)

**Capabilities:**
- **Compilation:** Reads all IndividualReport objects and produces a coherent ResearchPaper
- **De-duplication:** Identifies overlapping content across reports and merges intelligently
- **Synthesis:** Creates new insights by connecting different perspectives
- **Clarification Query:** Can ask agents for clarification on ambiguous points
- **Tone Calibration:** Maintains a neutral academic tone (temperature 0.3)
- **PDF Rendering:** Renders the final paper via Jinja2 → WeasyPrint pipeline

**Clarification Protocol:**
1. Scribe identifies gaps or contradictions in individual reports
2. Scribe sends ClarificationQuery to specific agent(s)
3. Agent responds with ClarificationResponse
4. Scribe may iterate — up to 5 rounds of clarification
5. After limit, scribe proceeds with best available information

**Synthesis Strategy:** To prevent the scribe from becoming a bottleneck when synthesizing multiple diverse perspectives, the scribe follows a structured synthesis pipeline:
1. **Extraction phase:** Extract key claims, themes, and data points from each agent report
2. **Comparison phase:** Identify areas of agreement, disagreement, and complementary insights across all reports
3. **Weaving phase:** Create a narrative that presents each perspective in a logical flow, using the "Synthesis" section to explicitly highlight where agents agree and where they diverge
4. **Clarification trigger:** If the scribe detects contradictory claims that cannot be resolved through context, it activates the clarification protocol to query the specific agent

This structured approach ensures the scribe handles diversity of perspectives systematically rather than attempting a single-pass summarization.

### 4.4 Collaboration Bus (`src/deepresearch/collaboration/bus.py`)

In-memory shared knowledge repository. Each agent writes to the bus after Round 1; the orchestrator aggregates contributions into SharedKnowledge before Round 2.

```python
@dataclass
class CollaborationBus:
    round_1_findings: dict[str, Findings]    # agent_id → Findings
    shared_knowledge: SharedKnowledge | None = None
    round_2_reports: dict[str, IndividualReport] = field(default_factory=dict)
    follow_up_questions: dict[str, FollowUpQuestions] = field(default_factory=dict)
    clarification_responses: dict[str, list[ClarificationResponse]] = field(default_factory=dict)
```

**Echo Prevention:** To prevent agents from simply echoing each other's findings (groupthink), the following safeguards are in place:
- Agents only receive aggregated shared knowledge (themes, agreements, disagreements, gaps) — not each other's full reports
- Each agent's personality profile includes a `bias_mitigation` field that warns the agent about its tendencies
- Agents are prompted to identify what NEW perspective they contribute that others haven't covered
- In Round 2, each agent receives its own Round 1 findings alongside the shared knowledge, enabling it to see how its perspective differs

The echo prevention is especially important in "Same Model" mode where all agents use the same underlying LLM — the personality prompts and temperature differences are the primary differentiation mechanism.

### 4.5 Model Abstraction Layer (`src/deepresearch/llm/client.py`)

**LLMClient** wraps LiteLLM for all LLM interactions.

**Supported Models:** Any model supported by LiteLLM (OpenAI GPT-4o, Anthropic Claude 3.5 Sonnet, Ollama llama3, Opencode Go, etc.)

**Provider Prefix Routing:**
The LLMClient automatically detects the provider from the model ID using `PROVIDER_ROUTES`, a dict that maps model ID prefixes (e.g., `opencode`, `openrouter`, `anthropic`, `groq`, `together`, `deepseek`, `cohere`, `google`, `ollama`) to their API base URLs and API key environment variables. For example, `opencode/go` routes to `https://api.opencode.ai/v1` with `OPENCODE_API_KEY`. A `provider` override parameter supports cases like `openrouter/opencode/go` where the prefix differs from the routing logic.

**Features:**
- Async completion with configurable timeout
- Automatic retry with exponential backoff
- Token usage tracking
- Cost estimation (per-model input/output cost rates, including Opencode AI at 0.0 cost)
- Model capability inference (supports vision, tool use, etc.)

**Tool Calling & Web Search:**
The LLMClient provides `generate_with_tools()` for tool-calling, supporting three execution paths: native LiteLLM streaming with `tools=` for API models, direct HTTP for local backends (Ollama, llama-cpp, vllm), and regex text fallback for non-streaming output. Multi-provider web search chains across SearXNG, DuckDuckGo, Brave, Google PSE, Tavily, and Serper, with parallel content fetching, result enrichment (TL;DR, key points, quotes), search caching, time-filter auto-detection, and tool alias mapping. See ADR-0017 for the full design.

### 4.6 llama.cpp Backend Management

A native llama.cpp backend has been added for managing the `llama-server` binary lifecycle and serving downloaded GGUF models. The implementation follows the Ollama management pattern (ADR-0005) and is defined in ADR-0018.

**Phase 1 (current):** Binary lifecycle management via new API endpoints:
- `GET /api/local-backends/llamacpp/status` — installed + version + running
- `POST /api/local-backends/llamacpp/install` — download from GitHub releases (SSE)
- `POST /api/local-backends/llamacpp/uninstall` — remove binary (SSE)
- `POST /api/local-backends/llamacpp/start` — subprocess launch with health check
- `POST /api/local-backends/llamacpp/stop` — SIGTERM → SIGKILL
- `POST /api/local-backends/llamacpp/restart` — stop + start
- Platform detection: Linux (ROCm/Vulkan/NVIDIA/CPU), macOS (ARM/x64), Windows (CPU)
- Process tracking via global asyncio variable + FastAPI lifespan cleanup

**Phases 2-4 (planned):** GGUF model serving, LiteLLM integration, full frontend UI.

## 5. Data Model

### Pydantic Models

```python
class ResearchTopic(BaseModel):
    """The research topic provided by the user."""
    title: str
    description: str | None = None
    keywords: list[str] = []

class ModelConfig(BaseModel):
    """Configuration for model assignment to agents."""
    mode: Literal["same", "random", "manual"] = "same"
    default_model: str = "opencode/go"
    per_agent_overrides: dict[str, str] = {}   # agent_id → model
    seed: int | None = None                      # for reproducible random assignment

class AgentProfile(BaseModel):
    """Definition of an agent's personality and methodology."""
    id: str
    name: str
    emoji: str
    persona_prompt: str
    methodology: str
    knowledge_base: str
    bias_mitigation: str
    voice: str
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)

class Findings(BaseModel):
    """Results from a single agent's research round."""
    agent_id: str
    round_num: int
    summary: str
    key_points: list[str]
    perspectives: list[str]
    raw_notes: str | None = None

class SharedKnowledge(BaseModel):
    """Aggregated knowledge shared with all agents."""
    all_findings: list[Findings]
    consensus_points: list[str]
    conflicting_perspectives: list[tuple[str, str]]  # (perspective_a, perspective_b)
    unanswered_questions: list[str]

class FollowUpQuestions(BaseModel):
    """Questions an agent wants to ask other agents."""
    agent_id: str
    questions: list[str]

class IndividualReport(BaseModel):
    """A single agent's final report after Round 2."""
    agent_id: str
    title: str
    sections: list[PaperSection]
    references: list[str] = []
    word_count: int = 0

class ClarificationQuery(BaseModel):
    """A question from the scribe to an agent."""
    agent_id: str
    question: str
    context: str | None = None

class ClarificationResponse(BaseModel):
    """An agent's response to a clarification query."""
    agent_id: str
    answer: str

class ResearchPaper(BaseModel):
    """The final compiled research paper."""
    title: str
    sections: list[PaperSection]
    agent_contributions: list[AgentContribution]
    word_count: int
    generated_at: datetime

class PaperSection(BaseModel):
    """A section within the final paper."""
    heading: str
    level: int = 1  # 1 = h1, 2 = h2, etc.
    content: str
    contributors: list[str] = []  # agent_ids

class AgentContribution(BaseModel):
    """Record of an agent's contribution."""
    agent_id: str
    agent_name: str
    perspective_summary: str

class SessionConfig(BaseModel):
    """Complete session configuration."""
    topic: ResearchTopic
    time_budget_minutes: int = Field(default=30, ge=1, le=480)
    model_selection: ModelConfig = ModelConfig()
    selected_model: str = "opencode/go"  # model ID for "same" mode
    agent_models: dict[str, str] = {}    # agent_id → model for "manual" mode
    quick_mode: bool = False
    deep_mode: bool = False
    dry_run: bool = False
    output_path: str = "./output"
    agents: list[str] = []  # selected agent IDs (empty = all)
```

### Data Flow

```
User Input ──► ResearchTopic
                    │
                    ▼
          SessionConfig (validated)
                    │
                    ▼
              Orchestrator
                    │
                    ├──► CollaborationBus
                    │       ├── round_1_findings (after Round 1)
                    │       ├── shared_knowledge (after collaboration)
                    │       └── round_2_reports (after Round 2)
                    │
                    ├──► Round 1: ResearchAgent.research_round_1() → Findings
                    │
                    ├──► Collaboration: Orchestrator → SharedKnowledge
                    │
                    ├──► Round 2: ResearchAgent.research_round_2() → IndividualReport
                    │
                    └──► Compilation: Scribe → ResearchPaper → PDF
```

## 6. API Design

### 6.1 Internal Component Interfaces

```python
class ResearchAgentInterface(ABC):
    @abstractmethod
    async def research_round_1(self, topic: ResearchTopic, time_budget: int) -> Findings: ...
    @abstractmethod
    async def formulate_questions(self, shared_knowledge: SharedKnowledge) -> FollowUpQuestions: ...
    @abstractmethod
    async def research_round_2(self, topic: ResearchTopic, 
                                shared_knowledge: SharedKnowledge,
                                time_budget: int) -> IndividualReport: ...
    @abstractmethod
    async def clarify(self, query: ClarificationQuery) -> ClarificationResponse: ...

class CollaborationBusInterface(ABC):
    @abstractmethod
    async def publish_findings(self, agent_id: str, findings: Findings) -> None: ...
    @abstractmethod
    async def get_shared_knowledge(self) -> SharedKnowledge: ...
    @abstractmethod
    async def publish_report(self, agent_id: str, report: IndividualReport) -> None: ...


```

### 6.2 Orchestrator Workflow API

```python
class Orchestrator:
    async def run_session(self, config: SessionConfig) -> ResearchPaper: ...
    async def list_profiles(self) -> list[AgentProfile]: ...
    async def list_models(self) -> list[str]: ...
    async def estimate_cost(self, config: SessionConfig) -> CostEstimate: ...
```

### 6.3 CLI Interface

```bash
# Run a research session
python -m deepresearch run "Quantum Computing in Healthcare" --time 30

# List available agent profiles
python -m deepresearch profiles list

# List available LLM models
python -m deepresearch models list

# Quick mode (skip Round 2, faster results)
python -m deepresearch run "Topic" --quick

# Deep mode (add Round 3, more thorough)
python -m deepresearch run "Topic" --deep

# Random model assignment (deterministic with seed)
python -m deepresearch run "Topic" --random-models --seed 42

# Manual per-agent model selection
python -m deepresearch run "Topic" --manual-models

# Specify output path
python -m deepresearch run "Topic" --output ./my-paper.pdf

# Dry run (validate config only, no LLM calls)
python -m deepresearch run "Topic" --dry-run
```

### 6.4 Event Logging

```python
@dataclass
class SessionEvent:
    timestamp: datetime
    event_type: Literal[
        "session_start", "session_end",
        "config_validated", "models_assigned",
        "round_start", "round_end",
        "agent_start", "agent_complete", "agent_failed",
        "collaboration_phase", "follow_up_questions",
        "scribe_start", "scribe_end",
        "clarification_query", "clarification_response",
        "pdf_generated", "error"
    ]
    agent_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
```

### 6.5 Per-Session File Logging

Each session automatically gets its own log file at `logs/session-<session_id>.log`
for isolated debugging. This supplements the global `logs/deepresearch.log`.

- **Setup:** `observability/session_logging.py` — wired into
  `MultiSessionManager._run_session()`
- **File path:** `logs/session-<session_id>.log` (same directory as global log)
- **Format:** `2026-06-25 20:30:00 [DEBUG] deepresearch.orchestrator [a1b2c3d4]: message`
- **Level:** DEBUG — captures all `deepresearch.*` logger activity for that session
- **Scope:** All loggers under the `deepresearch.*` namespace write to the per-session
  file while the session is active
- **Lifecycle:** Created when session status changes to `running`, torn down in
  `finally` block (survives errors and cancellations)
- **Safety:** Failure to create the log file is caught gracefully — the session
  continues without per-session logging. Concurrent sessions each get their own
  independent log file.

## 7. Implementation Plan

### Phase 1: Core Infrastructure (2-3 days)

**Deliverables:**
- `pyproject.toml` with all dependencies
- Data models (`models/schemas.py`)
- Config loading and validation (`config.py`)
- LLM client wrapper (`llm/client.py`)
- Prompt template files
- CLI scaffold

**Gate checkpoint:** All data models tested, LLM client returns valid response for each provider.

### Phase 2: Orchestrator (2-3 days)

**Deliverables:**
- Complete session lifecycle (IDLE → COMPLETE)
- Configuration flow and model assignment
- Parallel execution with asyncio.gather
- Timeout and error handling
- Event logging

**Gate checkpoint:** Orchestrator runs through full lifecycle with mock agents, all error paths tested.

### Phase 3: Research Agents (2-3 days)

**Deliverables:**
- `ResearchAgent` base class with abstract methods
- Agent profile registry
- Concrete `ResearchAgent` implementation
- Default agent profiles YAML file
- Research prompt templates

**Gate checkpoint:** Each agent personality produces distinguishable output on a test topic.

### Phase 4: Collaboration System (1-2 days)

**Deliverables:**
- `CollaborationBus` implementation
- Shared knowledge aggregation
- Follow-up question flow
- Round 2 execution with shared context
- Quick mode (skip Round 2)

**Gate checkpoint:** Full 2-round workflow runs end-to-end with all agents.

### Phase 5: Scribe & PDF (2-3 days)

**Deliverables:**
- Scribe agent implementation
- Paper structure generation
- Clarification protocol
- Jinja2 HTML template
- CSS styling
- WeasyPrint PDF rendering
- Full integration test

**Gate checkpoint:** PDF output generated with proper structure, formatting, and all agent contributions.

### Phase 6: Polish (1-2 days)

**Deliverables:**
- Token usage tracking
- Cost estimation
- Dry-run mode
- Rich progress indicators
- Comprehensive error handling
- Configuration validation improvements
- Documentation

**Gate checkpoint:** All CLI flags functional, edge cases handled, documentation complete.

## 8. Testing Strategy

### 8.1 Unit Tests

| Test | Description |
|------|-------------|
| Data Models | Validate all Pydantic models with valid/invalid input |
| Config Loading | Test YAML and CLI config parsing |
| Prompt Builders | Verify prompts contain expected personality markers |
| Model Assignment | Test same/random/manual modes with seed determinism |
| Collaboration Bus | Test publish/read/aggregate operations |
| Cost Estimation | Verify token/cost calculation with known inputs |
| Custom Profile Validation | YAML parsing, field validation, error handling |

### 8.2 Integration Tests

| Test | Description |
|------|-------------|
| Single Agent | One agent completes full research lifecycle |
| Multi-Agent | All agents run Round 1 in parallel |
| Full 2-Round | Complete workflow from topic to IndividualReports |
| Scribe Compilation | Scribe compiles reports into structured paper |
| Clarification Loop | Scribe → Agent → Scribe clarification round trip |
| PDF Generation | Full pipeline produces valid PDF |

### 8.3 Mock LLM Fixture

A YAML-based mock LLM fixture provides canned responses for testing:

```yaml
responses:
  research_round_1:
    default: "These are my initial findings on {topic}..."
  research_round_2:
    default: "After reviewing other perspectives, I add..."
```

### 8.4 Personality Differentiation Tests

These personality differentiation tests are intended as CI gates for new profile additions and should be run before merging profile changes.

¹ In "same model" Mode A, a higher threshold (0.85) is acceptable since model-level diversity is absent.

| Test | Method | Target |
|------|--------|--------|
| Semantic Distance | Embedding similarity between agent outputs | < 0.85 cosine similarity¹ |
| Keyword Uniqueness | Distinctive terms per personality | Each agent has unique keywords |
| Tonality Variance | Sentiment/formality analysis | Agents have statistically different tones |

### 8.5 End-to-End Test

A single test that runs the full pipeline (with mock LLM) and validates the PDF output exists and contains expected sections.

### 8.6 Non-Functional Tests

| Test | Description |
|------|-------------|
| Parallel Execution | Verify all agents start within 100ms of each other |
| Graceful Degradation | One agent timeout → session continues |
| Time Budget Respect | Agent response truncated by time budget |
| Model Agnostic | Each supported provider returns expected output shape |

## 9. ADR Index

| # | Title | Status |
|---|-------|--------|
| ADR-0001 | Multi-Agent Research Architecture | Accepted |
| ADR-0002 | Agent Personality & Model Selection | Accepted |
| ADR-0003 | Web Frontend & Multi-Session Architecture | Accepted |
| ADR-0004 | Test Findings and Architecture Fixes | Accepted |
| ADR-0005 | Auto-Install and Auto-Discover Local LLM Backends | Accepted |
| ADR-0006 | Web Search and Tool Calling Integration | Accepted |
| ADR-0007 | Clarification Protocol and Refinement Phase | Accepted |
| ADR-0008 | Dashboard Enhancements | Accepted |
| ADR-0009 | CI/CD Pipeline, npm Wrapper, and Docker Distribution | Accepted |
| ADR-0010 | Dynamic Research Rounds | Accepted |
| ADR-0011 | Concurrency Limits and Web Search Throttling | Accepted |
| ADR-0012 | Replace DuckDuckGo with SearXNG for Web Search | Accepted |
| ADR-0013 | SearXNG Engine Optimization — Remove Problematic Backends, Tune Timeouts | Accepted |
| ADR-0014 | Enforce Time Budget and Correct UI Labels | Accepted |
| ADR-0015 | Fix JSON Parsing and Topic Drift | Accepted |
| ADR-0016 | Epic Tracker — Code Review Handlingsplan (2026-06-17) | Accepted |
| ADR-0017 | Enhanced Tool Calling with Multi-Provider Search | Accepted |
| ADR-0018 | Native llama.cpp Backend — Binary Lifecycle, GGUF Serving, and LiteLLM Integration | Accepted |
| ADR-0019 | Frontend Reactivity Strategy | Accepted |
| ADR-0020 | Remove llmfit Dependency — Adopt llama-server `-hf` Flag and Python Hardware Detection | Accepted |

## 10. Open Questions

| # | Question | Impact | Proposed Investigation |
|---|----------|--------|----------------------|
| 1 | Should agents have memory across sessions? | High | Defer to v1.1 — v1.0 agents are stateless |
| 2 | How should we handle research papers longer than 50 pages? | Medium | PDF pagination + WeasyPrint page break control |
| 3 | Should the scribe have sub-personalities for different paper sections? | Medium | Investigate in v1.1; v1.0 scribe has fixed neutral tone |
| 4 | How do we validate research quality? | High | Human evaluation for v1.0; automated metrics research for v1.1 |
| 5 | Should PDF styling be customizable? | Medium | CSS template override path in v1.1 |
| 6 | Should quick mode skip collaboration entirely or just Round 2? | Low | v1.0: quick mode skips Round 2 but preserves collaboration |
| 7 | What is the optimal number of clarification rounds? | Medium | Start with 5, gather usage data for tuning |
| 8 | Should we include source citations in agent reports? | High | Requires web search — out of scope for v1.0 |
| 9 | How do we handle model availability at session start? | Resolved | Pre-flight connectivity check implemented — 15s timeout, immediate error on failure |
| 10 | Should we support streaming output during research rounds? | Resolved | SSE streaming implemented in web dashboard — real-time progress via EventSource |

## 11. Changelog

| Version | Date | Changes |
|---------|------|---------|
| 2.0 | 2026-06-29 | Group 4 cleanup: ADR-0019 status → Accepted, VERSION → 1.8.0, TODO.md updated with recent work. |
| 1.9 | 2026-06-27 | ADR-0020 promoted from Proposed → Accepted after Phase 1 + Phase 2 implementation and review. Updated ADR index. Bumped VERSION to 1.7.0. Added CHANGES.md v1.7.0 entry. |
| 1.8 | 2026-06-26 | Documentation refresh: updated module structure diagram to reflect actual source layout (orchestrator/ package, web/routes/, config/, tools/providers/, observability/, output/); expanded test file list to all 22 files; fixed ADR-0018 status to Accepted; bumped VERSION to 1.6.0; added CHANGES.md entries for post-1.5.0 work. |
| 1.7 | 2026-06-25 | Added ADR-0020 (Remove llmfit Dependency — Adopt llama-server `-hf` Flag and Python Hardware Detection) to ADR index. Backfilled ADR-0019 (Frontend Reactivity Strategy) to ADR index and ADR README. |
| 1.6 | 2026-06-25 | Backfilled ADR-0017, ADR-0018, ADR-0019, ADR-0020 in ADR README index. |
| 1.5 | 2026-06-20 | Added ADR-0018 (Native llama.cpp Backend) with full Component Design section (Phase 1: binary lifecycle management, 6 API endpoints, platform detection, process tracking) |
| 1.4 | 2026-06-20 | Batch doc fixes: all 17 ADR statuses updated to Accepted in index; version bump |
| 1.3 | 2026-06-19 | Added ADR-0017 (Enhanced Tool Calling with Multi-Provider Search) to ADR index and inline references; updated Scope to reflect web search is in scope |
| 1.2 | 2026-06-17 | Batch 1 doc fixes: full ADR index (16 items), adaptive FSM description for dynamic rounds, version bump |
| 1.1 | 2026-06-13 | Added Opencode AI as default provider, provider prefix routing, model connectivity check, model selector UI, session deletion, provider model auto-discovery, web module in project structure |
| 1.0 | 2026-06-13 | Initial design document |
| 0.1 | 2026-06-13 | Template created |

## Appendix A: Agent Profiles (Reference)

### Built-in Agent Personalities

| ID | Name | Emoji | Approach | Tone | Temperature |
|----|------|-------|----------|------|-------------|
| curious_teenager | Curious Teenager | 🧑‍🎤 | Asks "why" repeatedly, explores tangents, excited discovery | Energetic, inquisitive, conversational | 0.85 |
| skeptical_academic | Skeptical Academic | 🧑‍🏫 | Cites sources, challenges assumptions, rigorous methodology | Formal, critical, precise | 0.35 |
| creative_artist | Creative Artist | 🎨 | Draws analogies, visual thinking, unexpected connections | Imaginative, metaphorical, expressive | 0.90 |
| pragmatic_engineer | Pragmatic Engineer | 🔧 | Focus on practical applications, feasibility, real-world impact | Direct, practical, solution-oriented | 0.45 |
| philosophical_thinker | Philosophical Thinker | 🤔 | Explores deeper meaning, ethics, implications for humanity | Contemplative, abstract, reflective | 0.75 |
| data_analyst | Data Analyst | 📊 | Looks for patterns, statistics, quantitative evidence | Analytical, precise, evidence-based | 0.30 |

### Profile YAML Format

Example: **Curious Teenager** (`profiles/default.yaml`)

```yaml
- id: curious_teenager
  name: "Curious Teenager"
  emoji: "🧑‍🎤"
  temperature: 0.85
  persona_prompt: >
    You are a curious teenager who is excited to learn about new topics.
    You ask "why" about everything and love exploring tangents and rabbit holes.
    You approach every topic with wide-eyed wonder and enthusiasm.
  methodology: >
    Start with the most basic questions and work outward. Follow every interesting
    tangent at least two layers deep. Look for surprising or counterintuitive facts.
    Ask how things connect to everyday life and popular culture.
  knowledge_base: >
    General pop culture awareness. Basic high-school level understanding of most
    academic topics. Deep knowledge of internet culture and memes. Limited
    specialized vocabulary.
  bias_mitigation: >
    Tendency to oversimplify complex topics. May focus on entertaining aspects
    over important ones. Consciously check: "Am I understanding this correctly,
    or am I just repeating something cool?"
  voice: >
    Energetic and conversational. Uses exclamation points freely. Asks rhetorical
    questions. Phrases like "Wait, so..." and "That's wild!" and "Here's what I'm
    wondering..." Keeps paragraphs short and punchy.
```

Example: **Skeptical Academic** (`profiles/default.yaml`)

```yaml
- id: skeptical_academic
  name: "Skeptical Academic"
  emoji: "🧑‍🏫"
  temperature: 0.35
  persona_prompt: >
    You are a tenured professor known for your rigorous standards and healthy
    skepticism. You never accept claims at face value. You demand evidence,
    challenge assumptions, and value methodological rigor above all else.
    Your questions cut to the heart of whether something is actually true.
  methodology: >
    Begin by identifying the core claims. Evaluate each claim against known
    evidence. Look for methodological flaws, confirmation bias, and logical
    fallacies. Seek out dissenting viewpoints. Apply Occam's razor.
    Demand peer-reviewed sources.
  knowledge_base: >
    Deep academic knowledge across sciences and humanities. Familiar with
    research methodologies, statistical analysis, and logical argumentation.
    Knows the history of ideas and classic debates in each field.
  bias_mitigation: >
    Risk of excessive negativity or gatekeeping. May dismiss novel ideas too
    quickly. Consciously check: "Am I being critical because the idea is weak,
    or because it challenges my worldview?"
  voice: >
    Formal and precise. Uses academic vocabulary appropriately. Prefers
    hedged language: "This suggests..." rather than "This proves..."
    Constructs careful arguments with premises and conclusions.
    Cites imagined sources ("As Smith (2021) argues...").
```
