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

**Version:** 1.1
**Last Updated:** 2026-06-13

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

### Negative
1. No horizontal scaling (acceptable for 5-6 agents)
2. Single point of failure (mitigation: optional checkpointing in v1.1)
3. No streaming (deferred to v2.0)
4. Python GIL (not a concern — all I/O-bound)
5. **More memory per session** — each session holds EventBus events, output directory state
6. **Session cleanup needed** — completed sessions must be pruned to avoid memory leak

### Neutral
1. Collaboration bus is in-memory (acceptable for v1.0)
2. Shared LiteLLM client (per-agent rate limiting optional)
3. Clarification queries run sequentially (intentional)
4. In-memory sessions are lost on server restart (acceptable for v1.0)

## ADR References
- **ADR-0002** (Agent Personality & Model Selection)
- **ADR-0003** (Web Frontend & Multi-Session Architecture)
