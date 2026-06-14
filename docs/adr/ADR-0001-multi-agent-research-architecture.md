---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0001: Multi-Agent Research Architecture

## Status

Proposed

**Version:** 1.2
**Last Updated:** 2026-06-14

## Context

DeepeResearch aims to produce multi-perspective research papers by having multiple AI agents with different personalities collaborate on a shared topic. We need to decide on the fundamental architecture: how agents are structured, how they communicate, how they execute in parallel, and how the final output is produced.

In addition to the CLI-only original design, users requested a **web frontend** for real-time monitoring and control, **multi-session support** for running concurrent research tasks, and the ability to **track progress live** via the browser.

### Key Forces
1. Parallel execution is critical
2. Model flexibility is a requirement (Option A/B/C)
3. In-process vs. distributed — trade-offs in complexity, isolation, performance
4. Communication protocol — shared memory bus vs. message queue vs. files
5. Single VS multi-process
6. KodeHold pattern alignment
7. Output format (PDF)
8. **Web frontend requirement** — users want a browser dashboard with live progress
9. **Multi-session** — users want to run and compare multiple research sessions concurrently
10. **Real-time monitoring** — SSE (Server-Sent Events) streaming for per-step progress

### Prior Art / Alternatives Considered
| Approach | Pros | Cons |
|----------|------|------|
| LangGraph | Built-in graph execution | Heavy, complex for round-based flow |
| AutoGen | Agent orchestration | Overengineered, steep learning curve |
| CrewAI | Role-based agents | Vendor lock-in, specific models |
| Custom asyncio | Full control, minimal deps | More code |
| OpenAI Swarm | Lightweight | OpenAI-only |

## Decision

### Architecture: Single-Process, Async-Driven, Custom Orchestration
- Single-process Python application using asyncio for parallel agent execution
- Custom orchestrator managing all workflow stages
- asyncio.gather() for concurrent agent execution
- In-process CollaborationBus for zero-latency communication
- Fault isolation via asyncio.wait_for() with timeouts
- No third-party orchestration frameworks (LangGraph, AutoGen, CrewAI, Swarm rejected)

### Communication: In-Memory CollaborationBus
- Round 1: Each agent writes Findings → bus
- Collaboration: Orchestrator aggregates into SharedKnowledge
- Round 2: Each agent reads SharedKnowledge, writes new Findings
- Final Reports: Each agent writes IndividualReport → scribe reads all
- Shared-nothing-writes, shared-all-reads model prevents race conditions

### Scribe: Special Agent, Not a Meta-Orchestrator
- Research agents: DIVERGE (explore individual perspectives)
- Scribe agent: CONVERGE (synthesize into one document)
- Scribe sees all individual reports in full

### PDF Generation: WeasyPrint HTML+CSS
- WeasyPrint was chosen over ReportLab, LaTeX, FPDF/pdfkit
- Rendered from Jinja2 templates

### Web Dashboard: FastAPI + SSE
- FastAPI for async HTTP server and SSE streaming endpoint
- SSE (Server-Sent Events) per session for real-time progress updates
- Single self-contained HTML dashboard (vanilla JS + CSS, dark theme)
- Session list UI with per-session progress views

### Multi-Session: Per-Session EventBus + asyncio Tasks
- `MultiSessionManager` manages a dict of `SessionInfo` + `asyncio.Task` objects
- Each session gets its own `EventBus` instance for SSE isolation
- Sessions identified by UUID, stored in-memory
- Max 20 concurrent sessions with auto-cleanup of oldest completed
- Per-session output directories (`./output/{session_id}/`)

### Simplified Pipeline: Round 1 → Collaboration → Scribe → PDF
The pipeline was simplified from the original 6-phase flow to a streamlined default:

```
Round 1 (all agents) → Collaboration (shared knowledge) 
  → Follow-up Questions → [Round 2 if medium/deep] 
  → Collect Reports → Scribe Compilation → PDF
```

Key simplifications:
- **`collect_reports` direct conversion** — When Round 2 is skipped (quick/custom budgets), `collect_reports()` converts `Findings` directly to `IndividualReport` objects with zero extra LLM calls. The findings' `summary`, `key_points`, and `raw_response` fields are mapped directly to report fields.
- **Round 2 skip for quick/custom** — Budgets `"quick"` and `"custom"` skip Round 2 entirely, saving tokens and time. Only `"medium"` and `"deep"` budgets run a second round.
- **Agent dispatch pattern** — The `AgentRegistry.agent_factory()` returns a single `dispatch()` callable that routes to the correct lifecycle method based on argument types (ResearchTopic → Round 1, SharedKnowledge → Review, 2 args → Round 2, Findings → Report, ClarificationQuery → Clarify).

### Clarification Protocol Error Handling
The scribe's clarification protocol now properly handles agent dispatch:
- `Orchestrator._handle_clarification()` looks up agents by ID in `self._agents` dict
- The dispatcher in `AgentRegistry.agent_factory` recognizes `ClarificationQuery` instances and routes to the agent's `clarify()` method
- If the agent is unavailable or fails, a default `ClarificationResponse` is returned instead of crashing
- Maximum 5 clarification rounds per agent per session (`_MAX_CLARIFICATION_ROUNDS = 5`)
- Mixed-type dispatch handles edge cases where agents have different method signatures

### Simplified collect_reports: Direct Findings Conversion
`collect_reports()` no longer requires extra LLM calls for agents that skipped Round 2:
- If `round_2` results exist, they are returned directly
- Otherwise, each agent's Round 1 `Findings` is converted to an `IndividualReport` by mapping fields: `summary → perspective_summary`, `key_points → key_insights`, `raw_response → analysis/full_text`
- This dramatically reduces token consumption for quick/custom budget sessions

### Session Timeout: budget × 4 + 300s
The session timeout calculation was revised for reliability:
- **Before:** `time_budget_seconds + 60` (tight grace period, causing frequent timeouts)
- **After:** `time_budget_seconds × 4 + 300` (generous: 4× budget + 5 min grace)
- Capped at `MAX_SESSION_DURATION = 1800` seconds (30 minutes)
- Per-agent round timeout: half the total budget per round, minimum 30 seconds
- This accommodates the scribe's longer processing time (300s timeout for scribe LLM calls)

### File-Based Logging
Persistent file logging was added to supplement console output:
- **Log file:** `logs/deepresearch.log`
- **Rotation:** 10 MB per file, up to 5 backups (`RotatingFileHandler`)
- **Level:** DEBUG (captures all deepresearch.* logger activity)
- **Format:** `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
- Log file is created relative to the project root, directory auto-created on server start
- The root logger gets the file handler so all child loggers benefit

## Consequences

### Positive
1. Minimal dependencies (LiteLLM, Pydantic, PyYAML, WeasyPrint, Jinja2)
2. Highly testable (in-process mock agents)
3. Fast parallel execution (total time ≈ max single-agent time)
4. Full model flexibility via LiteLLM
5. Simple failure handling via gather(return_exceptions=True)
6. KodeHold-aligned Python project structure
7. **Real-time progress monitoring** via SSE dashboard
8. **Concurrent sessions** for power users and comparison studies
9. **Session history** — completed sessions remain viewable in the UI

### Positive (Post-Iteration)
1. **Simplified pipeline** — direct Findings→Report conversion removes unnecessary LLM calls for quick/custom budgets
2. **Robust session timeout** — budget × 4 + 300s prevents premature timeouts on long-running scribe calls
3. **File-based logging** — persistent DEBUG logs for troubleshooting without console capture
4. **Clarification protocol reliability** — proper agent dispatch handles edge cases without crashing
5. **Agent streaming output** — LLM chunks published as events for live dashboard rendering

### Negative
1. No horizontal scaling (acceptable for 5-6 agents)
2. Single point of failure (mitigation: optional checkpointing in v1.1)
3. Python GIL (not a concern — all I/O-bound)
4. **More memory per session** — each session holds EventBus events, output directory state
5. **Session cleanup needed** — completed sessions must be pruned to avoid memory leak
6. **File logs may grow quickly** — DEBUG-level logging with 10MB rotation may still rotate often on active servers
7. **Event history buffer adds memory** — 500 events per session for SSE replay could accumulate with many concurrent sessions

### Neutral
1. Collaboration bus is in-memory (acceptable for v1.0)
2. Shared LiteLLM client (per-agent rate limiting optional)
3. Clarification queries run sequentially (intentional)
4. In-memory sessions are lost on server restart (acceptable for v1.0)

## ADR References
- **ADR-0002** (Agent Personality & Model Selection)
- **ADR-0003** (Web Frontend & Multi-Session Architecture)
