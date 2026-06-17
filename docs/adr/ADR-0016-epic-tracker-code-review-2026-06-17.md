---
phase:
  current: 3
  total: 4
  status:
     3: active
     1: done
     2: done
---

# ADR-0016: Epic Tracker — Code Review Handlingsplan (2026-06-17)

## Status

Accepted

**Version:** 1.2
**Last Updated:** 2026-06-17

## Context

On **2026-06-17**, a comprehensive code review of the DeepResearch project
(8,640 LOC Python in `src/`, ~9.5K LOC total in `src/`, ~6.9K LOC in `tests/`,
15 existing ADRs, 7 open + 38 closed GitHub issues at the time of review)
produced **33 new GitHub issues** (#53–#85) on
[Acharnite/deepresearch](https://github.com/Acharnite/deepresearch).
The review session was triggered by a recurring "running in circles" symptom
visible across recent commits:

1. **Six time-budget-related fixes** in the last 30 days (PRs touching
   `_get_timeout`, `_should_continue`, `TIME_BUDGETS`). Each fix lands in
   one place; a parallel copy in `web/sessions.py` or `web/server.py`
   silently re-introduces the bug.
2. **Repeated JSON-parse fixes** in `LLMClient.parse_json_response` —
   the streaming-vs-non-streaming path leaks tool output text into
   the parsed JSON, causing parse failures. ADR-0015 was the third
   attempt at this fix.
3. **PDF threshold bumping**: the PDF size threshold was raised 3 times
   across `orchestrator.py` and `web/sessions.py` to work around
   premature splitting. The two copies disagree (`20_000` vs `12_000`).

All three symptoms trace to the same root: **three sources of truth for
the same configuration value** (#56, the "P0 budget inconsistency" issue
that the review explicitly identified as the keystone).

### Why a single epic-tracker ADR, not 33 per-issue ADRs?

The review findings are interconnected. Several P1/P2 issues are
**architectural fixes** that subsume or replace the P0 narrow fixes
(e.g. #82 *SessionConfig dataclass* is the architectural form of #56
*constants duplication*; #81 *EventStore* supersedes the narrow #60
*DB race*; #85 *OpenTelemetry tracing* depends on #81 being in place).
Tracking them in 33 separate documents loses the dependency structure
and the ordering rationale.

This ADR:

- Documents **what was found** (33 issues, 1:1 mapping to GitHub)
- Defines **the order to address** (4 phases, dependency-driven)
- Provides a **status tracking table** (the live artifact)
- Lists **dependencies between issues** (graph + table)
- Defines **"done" criteria** for the epic and for each phase
- Is **updated on every issue close** and every phase completion
  (per the Update Protocol section below)

> **Discrepancy note**: The code-review session report mentioned
> "10 P0, 13 P1, 5 P2, 9 P3 + 5 Refactor" (37 findings). The actual
> GitHub issues are **9 P0, 11 P1, 8 P2, 5 P3 (33 total)**, with
> enhancement-type issues (#67, #68, #69, #81, #82) labeled P1 and
> (#83, #84, #85) labeled P2. This ADR uses the actual GitHub labels
> as the source of truth.

### Issue label distribution (as filed in GitHub)

| Priority | Bug | Enhancement | Documentation | Total |
|----------|-----|-------------|---------------|-------|
| **P0** | 9 | 0 | 0 | **9** |
| **P1** | 5 | 5 | 1 | **11** |
| **P2** | 5 | 3 | 0 | **8** |
| **P3** | 0 | 0 | 5 | **5** |
| **Total** | **19** | **8** | **6** | **33** |

## Discovery — The 33 Issues

Issues are organized by priority. Each row includes the issue number,
title, file:line target, and the phase this ADR assigns it to.

### P0 — Critical Foundation Bugs (9)

| # | Title | File:line | Phase |
|---|-------|-----------|-------|
| [#53](https://github.com/Acharnite/deepresearch/issues/53) | Inverted "all agents failed" check makes error path unreachable | `web/sessions.py:344-356` | 1 |
| [#54](https://github.com/Acharnite/deepresearch/issues/54) | CORS misconfiguration: `allow_origins=['*']` + `allow_credentials=True` is invalid CORS spec | `web/server.py:114-120` | 1 |
| [#55](https://github.com/Acharnite/deepresearch/issues/55) | Race condition: session concurrency check is non-atomic with semaphore acquisition | `web/server.py:249`, `web/sessions.py:240` | 1 |
| [#56](https://github.com/Acharnite/deepresearch/issues/56) | Time budget, max-rounds, and PDF threshold are duplicated AND inconsistent | `orchestrator.py:74-89,1513`, `web/sessions.py:156,159,331` | 1 |
| [#57](https://github.com/Acharnite/deepresearch/issues/57) | Clarification protocol effective cap is 2 not 5 — constant and code disagree | `agents/scribe_agent.py:36,366,401` | 1 |
| [#58](https://github.com/Acharnite/deepresearch/issues/58) | Search backend default engines still includes DuckDuckGo (contradicts ADR-0013) | `tools/web_search.py:30,232` | 1 |
| [#59](https://github.com/Acharnite/deepresearch/issues/59) | Dead code inflates `orchestrator.py` by ~150 LOC | `orchestrator.py` (7 dead methods) | 1 |
| [#60](https://github.com/Acharnite/deepresearch/issues/60) | Sync/async DB writers race on same file (`sessions.json`) | `web/sessions.py:43-90` | 1 |
| [#61](https://github.com/Acharnite/deepresearch/issues/61) | Time budget exceeded does not cancel in-flight agent tasks | `orchestrator.py:1025-1029` | 1 |

### P1 — Bugs, Architectural Improvements, and One Doc Gap (11)

| # | Title | File:line | Phase |
|---|-------|-----------|-------|
| [#62](https://github.com/Acharnite/deepresearch/issues/62) | Empty-result retry uses string-repr length, not content length | `orchestrator.py:327` | 3 |
| [#63](https://github.com/Acharnite/deepresearch/issues/63) | Deterministic random model assignment is NOT deterministic (uses Python `hash`) | `orchestrator/config.py:162` | 3 |
| [#64](https://github.com/Acharnite/deepresearch/issues/64) | Token usage tracking is fragmented — no session-level aggregator | `llm/client.py:228`, `agents/registry.py:79` | 3 |
| [#65](https://github.com/Acharnite/deepresearch/issues/65) | Search semaphore info returns permits-remaining labeled as "active searches" | `tools/web_search.py:280` | 3 |
| [#66](https://github.com/Acharnite/deepresearch/issues/66) | Search rate limiter and cache are dead infrastructure (ADR-0011 partially unimplemented) | `tools/web_search.py:73-90,140-220` | 3 |
| [#67](https://github.com/Acharnite/deepresearch/issues/67) | Type-based dispatch in `AgentRegistry` is fragile (5 type paths) | `agents/registry.py:161-242` | 2 |
| [#68](https://github.com/Acharnite/deepresearch/issues/68) | `orchestrator.py` is a 1771-line god class | `orchestrator/orchestrator.py` (entire file) | 2 |
| [#69](https://github.com/Acharnite/deepresearch/issues/69) | ADR-0014 refactor incomplete: `_get_timeout` not renamed | `orchestrator.py:422` | 2 |
| [#70](https://github.com/Acharnite/deepresearch/issues/70) | ADR index in design doc only lists 3 of 15 ADRs | `docs/design/README.md:753` | 4 |
| [#81](https://github.com/Acharnite/deepresearch/issues/81) | Replace 3 parallel event systems with unified `EventStore` | `orchestrator.py:1764`, `web/event_bus.py`, `sessions.py:286` | 2 |
| [#82](https://github.com/Acharnite/deepresearch/issues/82) | Centralize all session config into immutable `SessionConfig` dataclass | `main.py:45-181`, `config.py`, `models.py:9` | 2 |

### P2 — Bugs and Refactors (8)

| # | Title | File:line | Phase |
|---|-------|-----------|-------|
| [#71](https://github.com/Acharnite/deepresearch/issues/71) | Anthropic `claude-haiku-3-5` cost key is wrong model ID | `llm/client.py:25` | 3 |
| [#72](https://github.com/Acharnite/deepresearch/issues/72) | Hardcoded `estimated_duration_seconds` in API doesn't match real budgets | `web/server.py:339` | 3 |
| [#73](https://github.com/Acharnite/deepresearch/issues/73) | Hardcoded model connectivity test uses `'gpt-4o'` fallback | `web/sessions.py:182` | 3 |
| [#74](https://github.com/Acharnite/deepresearch/issues/74) | Scribe timeout budget (25% of session) is too aggressive | `orchestrator.py:431` | 3 |
| [#75](https://github.com/Acharnite/deepresearch/issues/75) | Path traversal protection in `/api/download` is incomplete | `web/server.py:598` | 3 |
| [#83](https://github.com/Acharnite/deepresearch/issues/83) | Move prompt templates from Python strings to YAML files | `agents/research_agent.py:41-98`, `agents/scribe_agent.py:38-87` | 4 |
| [#84](https://github.com/Acharnite/deepresearch/issues/84) | Add connection pooling + circuit breaker to `LLMClient` | `llm/client.py` | 3 |
| [#85](https://github.com/Acharnite/deepresearch/issues/85) | Add OpenTelemetry distributed tracing for observability | `orchestrator.py`, `llm/client.py`, `web/server.py` | 4 |

### P3 — Documentation Drift (5)

| # | Title | File:line | Phase |
|---|-------|-----------|-------|
| [#76](https://github.com/Acharnite/deepresearch/issues/76) | Design doc version (1.1) diverges from project version (0.11.2) | `docs/design/README.md:1-5` | 4 |
| [#77](https://github.com/Acharnite/deepresearch/issues/77) | `CHANGELOG.md` missing v0.11.0, v0.11.1, v0.11.2 entries | `CHANGELOG.md:5` | 4 |
| [#78](https://github.com/Acharnite/deepresearch/issues/78) | `TODO.md` shows ADR-0015 as not done but it is committed | `TODO.md:25` | 4 |
| [#79](https://github.com/Acharnite/deepresearch/issues/79) | Design doc §4.1 FSM still describes old 2-round hardcoded flow | `docs/design/README.md:264` | 4 |
| [#80](https://github.com/Acharnite/deepresearch/issues/80) | Most ADRs missing 'Documentation' section per ADR-0048 requirement | `docs/adr/ADR-0001` through `0015` | 4 |

## Decision

### Phased Execution Plan

The 33 issues are addressed in **4 sequential phases**. Each phase has a
distinct goal, a fixed scope, an ordering rationale, and a clear exit
criterion. Phases are NOT interchangeable — Phase 1 establishes
correctness, Phase 2 establishes the architecture that prevents
re-introduction, Phase 3 sweeps the remaining bugs and performance work,
and Phase 4 closes the documentation and observability loop.

#### Phase 1 — Critical Foundation (P0 bugs blocking core flows)

**Goal:** Make the system functionally correct. The 9 P0 bugs include
the inverted error check (#53), CORS spec violation (#54), a TOCTOU
race on session creation (#55), the keystone constants duplication
(#56), the dead clarification-protocol constant (#57), the
DDG-in-default-engines regression (#58), ~150 LOC of dead code
inflating the god class (#59), the DB writers race (#60), and the
un-cancelled asyncio tasks on time-budget exit (#61).

**Issue order (within the phase):**

1. **#59 dead code first** — deleting 7 dead methods and the
   `ModelConfig` class shrinks `orchestrator.py` from 1771 → ~1620
   lines, making every subsequent change in this phase and Phase 2
   less risky. The CI guard `test_no_dead_code_resurrected` also
   prevents regression during later refactors.
2. **#56 constants** — the keystone. Create
   `src/deepresearch/constants.py` with the single
   `TIME_BUDGETS` / `MAX_ROUNDS` / `PDF_SUMMARIZATION_THRESHOLD`
   tables, then migrate `orchestrator.py` and `web/sessions.py`
   imports. Required for #61, #69, and #72 to be unblocked.
3. **#61 task cancellation** — implements `self._outstanding_tasks`
   tracking + `_cancel_event` plumbed into the LLM retry loop. The
   5% over-budget acceptance test will fail until #56 lands (needs
   the unified budget source), so #56 must precede it.
4. **#53 inverted error check** — simple 3-line fix in
   `web/sessions.py:344-356`. No dependencies.
5. **#57 clarification cap** — choose 2 (per ADR-0007) or 5 (per the
   constant), make them match, and rewrite the test. No
   dependencies.
6. **#58 default engines** — replace `DEFAULT_ENGINES` with the
   post-ADR-0013 list and move config load to server startup.
7. **#60 DB writers race** — delete the sync writer, hold the
   asyncio lock across the full read-modify-write, add `fcntl.flock`
   for multi-process safety. Required for #81 (Phase 2) to be
   unblocked.
8. **#54 CORS** — add `cors_allowed_origins` config, replace the
   `*`+credentials combination. One config setting, one line of
   middleware change.
9. **#55 race on semaphore** — move `acquire()` into the server
   request path (or drop the pre-check). Closes Phase 1.

**Exit criteria:**

- [ ] All 9 P0 issues closed on GitHub
- [ ] `src/deepresearch/constants.py` exists and is the only source
      of `TIME_BUDGETS` / `MAX_ROUNDS` / `PDF_SUMMARIZATION_THRESHOLD`
- [ ] `orchestrator.py` line count < 1620
- [ ] `test_no_dead_code_resurrected` passes
- [ ] All 309+ existing tests still pass
- [ ] New unit test per P0 fix

#### Phase 2 — Architectural Refactors (Issue #67, #68, #69, #81, #82)

**Goal:** Break the "running in circles" cycle by removing the
architectural debt that allowed the P0 bugs to recur. This phase
deliberately comes **after** P0 fixes — fixing the architecture
without first stabilizing the symptoms would just relitigate the
same bugs in the refactor.

**Issue order (within the phase):**

1. **#82 `SessionConfig` dataclass** — the architectural form of
   #56. Implement the frozen dataclasses (`TimeBudget`,
   `ModelAssignment`, `SessionConfig`) and migrate
   `ResearchTopic` to topic-only fields. This unblocks #67 (cleaner
   registry), #69 (single source of timeout formula), and #70
   (clearer ADR references).
2. **#67 dispatch enum** — replace the 5-branch
   `isinstance`/`len(args)` dispatch in `AgentRegistry.dispatch()`
   with a `Phase` enum + handler map. Adding a new phase becomes
   a one-line handler map entry.
3. **#81 `EventStore`** — unify the three event systems
   (`_log_event` list, `event_bus` pub/sub,
   `_publish_with_history` monkey-patch) into a single class with
   atomic persistence. Required for #85 to be unblocked.
4. **#68 split `Orchestrator`** — extract `SessionState`,
   `RoundRunner`, `TimeoutCalculator`, `ScribeCompiler`,
   `AgentRegistry` as collaborator classes. The
   `Orchestrator` class itself becomes a thin lifecycle
   coordinator (< 500 lines). The pairing with #59 (dead code
   already removed) makes this tractable.
5. **#69 rename `_get_timeout` → `_get_round_timeout`** — completes
   the ADR-0014 §3 mandate, now using the formula from the new
   `constants.py` (#56) and the `SessionConfig` (#82).

**Exit criteria:**

- [x] All 5 issues closed
- [x] `Orchestrator` class < 500 lines (currently 1771)
- [x] No `isinstance`-based dispatch in `registry.py`
- [x] `SessionConfig` is the only config object passed to
      `Orchestrator.run()`
- [x] `EventStore` is the only writer for session events
- [x] All 309+ tests pass; new unit tests for each refactored class

> ✅ All 5 issues closed 2026-06-17

#### Phase 3 — Stability & Performance (P1/P2 bug fixes + connection pool)

**Goal:** With the architecture now solid, sweep the remaining
correctness bugs and add the connection-pool performance work
that the open issues have been waiting for.

**Issue order (within the phase):**

- [x] **#63 deterministic seed** — `hash()` → `hashlib.sha256` (or
   `mmh3`). Required for N5 determinism. Low risk, isolated.
- [ ] **#62 empty-result retry** — content-based check using
   `summary`/`key_points` instead of `len(str(...))`. Low risk.
- [x] **#65 semaphore info** — invert the label, or use the
   `SearchSemaphore` wrapper class. Low risk.
- [ ] **#64 token tracker** — implement `TokenTracker` shared
   across `LLMClient` instances; add `GET /api/sessions/{id}/cost`.
- [x] **#66 search rate limit + cache** — implement the dead
   `MIN_SEARCH_INTERVAL` and `_search_cache` (Option A from the
   issue), or delete them. Decision recorded in CHANGELOG.
- [x] **#71 Anthropic cost key** — fix `claude-haiku-3-5` →
   `claude-3-5-haiku-20241022` (and similar model ID
   mismatches).
- [x] **#72 estimated duration** — replace hardcoded 360/600/900
   with `TIME_BUDGETS[...]` from constants.
- [x] **#73 model connectivity default** — drop the `'gpt-4o'`
   fallback, use the first configured model or fail with a
   helpful error.
- [x] **#74 Scribe budget** — increase from 25% to 40% with a
   120-second floor.
- [x] **#75 path traversal** — `pathlib.PurePosixPath` validation
    + `is_relative_to(DOWNLOADS_DIR)` containment check.
- [ ] **#84 connection pool + circuit breaker** — shared
    `httpx.AsyncClient` (ClassVar), per-model `CircuitBreaker`.
    Benchmark: ≥2× speedup on a 10-call session.

**Exit criteria:**

- [ ] 8/11 P1+P2 bugs and refactors (#62–#66, #71–#75, #84)
      closed (#63, #65, #66, #71–#75 done)
- [ ] Determinism test passes with `PYTHONHASHSEED=0` and
      `PYTHONHASHSEED=42`
- [ ] `GET /api/sessions/{id}/cost` returns accurate per-model
      totals
- [ ] Benchmark script shows ≥2× speedup on 10-call session with
      connection pool
- [ ] No flaky tests in CI for 5 consecutive runs

#### Phase 4 — Documentation & Observability (P3 + prompts + tracing)

**Goal:** Once the code is stable, make the project maintainable
long-term by closing the documentation drift and adding the
observability that prevents future "why was this slow?" tickets.

**Issue order (within the phase):**

1. **#70 ADR index** — replace the 3-of-15 index in
   `docs/design/README.md:753` with the full 16-item table (now
   including ADR-0015 and this ADR-0016), and add a CI check
   that fails if any ADR is added without being indexed.
2. **#80 ADR Documentation sections** — add the `## Documentation`
   section to ADR-0001, 0006, 0007, 0010, 0011, 0014, 0015
   (per the ADR-0048 template).
3. **#79 design doc FSM** — rewrite §4.1 to describe the
   adaptive 2–5 round loop (ADR-0010) instead of the
   2-round hardcoded flow.
4. **#76 design doc version** — bump to 1.2, set Last Updated to
   2026-06-17, add the "Recent Architectural Changes" section.
5. **#78 TODO.md** — mark the ADR-0015 entry as done (commit
   `97d5b29`).
6. **#77 CHANGELOG** — add v0.11.0, v0.11.1, v0.11.2 entries.
   Include a "0.12.0 — Code Review Handlingsplan" entry that
   closes this epic.
7. **#83 prompts to YAML** — move all `_ROUND_*_FORMAT`,
   `_SCRIBE_SYSTEM_PROMPT`, `_COMPILE_FORMAT` strings into
   `prompts/*.yaml` with version + metadata + Jinja
   substitution. `PromptTemplate` class replaces the prompt
   builder functions in `utils/prompts.py`.
8. **#85 OpenTelemetry** — depends on #81 (EventStore for
   stable correlation IDs). Add spans for research round,
   refinement, clarification, scribe compile, LLM call, web
   search. Console exporter in dev, OTLP collector in prod
   (Jaeger/Tempo docker-compose snippet in
   `docs/observability/`).

**Exit criteria:**

- [ ] All 5 P3 issues closed
- [ ] All 3 refactor issues (#83, #85, plus any from Phase 3
      deemed P2 enhancement) closed
- [ ] Design doc version 1.2 with all 16 ADRs indexed
- [ ] `CHANGELOG.md` has v0.11.0/v0.11.1/v0.11.2/v0.12.0 entries
- [ ] `prompts/*.yaml` exists; no `_ROUND_*_FORMAT` literals
      remain in `agents/`
- [ ] OpenTelemetry traces visible in dev console exporter
- [ ] Sample Jaeger/Tempo docker-compose checked into
      `docs/observability/`

### Dependency Graph

Issues that **must be completed before** others can start. Read
as "A → B" means "A blocks B".

```mermaid
graph TD
    %% Phase 1 — P0 bugs
    59[59 Dead code removal]:::p0
    56[56 Constants consolidation<br/>★ KEYSTONE]:::p0
    60[60 DB writers race]:::p0
    61[61 Task cancellation]:::p0
    53[53 Inverted error check]:::p0
    57[57 Clarification cap]:::p0
    58[58 Default engines]:::p0
    54[54 CORS misconfig]:::p0
    55[55 Semaphore race]:::p0

    59 --> 82
    56 --> 61
    56 --> 69
    56 --> 72
    56 --> 82
    60 --> 81

    %% Phase 2 — Architectural refactors
    82[82 SessionConfig dataclass]:::p1
    67[67 Dispatch enum]:::p1
    81[81 EventStore]:::p1
    68[68 Split Orchestrator]:::p1
    69[69 _get_timeout rename]:::p1

    82 --> 67
    82 --> 69
    67 --> 68
    68 --> 69

    %% Phase 3 — Bug sweeps + perf
    84[84 Connection pool]:::p2
    62[62 Empty retry]:::p1
    63[63 Deterministic seed]:::p1
    64[64 Token tracker]:::p1
    65[65 Semaphore info]:::p1
    66[66 Rate limit + cache]:::p1
    71[71 Cost key]:::p2
    72[72 Estimated duration]:::p2
    73[73 Model connectivity]:::p2
    74[74 Scribe budget]:::p2
    75[75 Path traversal]:::p2

    81 --> 85

    %% Phase 4 — Docs + observability
    70[70 ADR index]:::p1doc
    80[80 ADR Doc sections]:::p3
    79[79 Design FSM doc]:::p3
    76[76 Design doc version]:::p3
    78[78 TODO.md]:::p3
    77[77 CHANGELOG]:::p3
    83[83 Prompts to YAML]:::p2
    85[85 OpenTelemetry]:::p2

    classDef p0 fill:#ff7b72,color:#fff,stroke:#da3633,stroke-width:2px
    classDef p1 fill:#d2a8ff,color:#000,stroke:#8957e5
    classDef p2 fill:#ffa657,color:#000,stroke:#bc4c00
    classDef p3 fill:#7ee787,color:#000,stroke:#1a7f37
    classDef p1doc fill:#79c0ff,color:#000,stroke:#0969da
```

**Critical-path table** (issues that block ≥2 others):

| Issue | Title | Blocks |
|-------|-------|--------|
| **#56** | Constants consolidation | #61, #69, #72, #82 |
| **#82** | `SessionConfig` dataclass | #67, #69 |
| **#81** | `EventStore` | #85 |
| **#60** | DB writers race | #81 |
| **#59** | Dead code removal | #68 |
| **#68** | Split orchestrator | #69 |
| **#70** | ADR index | #76, #79 |

### Concrete code examples for architectural changes

#### #82 — `SessionConfig` dataclass (the keystone refactor)

```python
# src/deepresearch/config/session.py
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Sequence
from deepresearch.models import ResearchTopic
from deepresearch.agents.profiles import AgentProfile


@dataclass(frozen=True)
class TimeBudget:
    """Single source of truth for session time budget.
    Replaces the 3 copies in orchestrator.py, web/sessions.py, web/server.py.
    """
    keyword: Literal["quick", "medium", "deep", "custom"]
    seconds: int
    max_rounds: int

    @classmethod
    def from_keyword(cls, kw: str) -> "TimeBudget":
        table = {
            "quick":  ("quick",  240, 2),
            "medium": ("medium", 420, 3),
            "deep":   ("deep",   660, 5),
            "custom": ("custom", 600, 4),
        }
        return cls(*table[kw])

    @classmethod
    def from_minutes(cls, minutes: int) -> "TimeBudget":
        return cls(keyword="custom", seconds=minutes * 60, max_rounds=4)


@dataclass(frozen=True)
class ModelAssignment:
    mode: Literal["same", "random", "manual"]
    selected_model: str | None
    per_agent: Mapping[str, str]

    def resolve(self, agent_ids: Sequence[str]) -> dict[str, str]:
        if self.mode == "same":
            assert self.selected_model is not None
            return {aid: self.selected_model for aid in agent_ids}
        if self.mode == "manual":
            return dict(self.per_agent)
        import hashlib, random
        seed = int(hashlib.sha256(
            f"{self.selected_model}".encode()
        ).hexdigest()[:8], 16)
        rng = random.Random(seed)
        models = list(self.per_agent.values()) or [self.selected_model]
        return {aid: rng.choice(models) for aid in agent_ids}


@dataclass(frozen=True)
class SessionConfig:
    """The ONLY config object passed to Orchestrator.run()."""
    topic: ResearchTopic
    budget: TimeBudget
    models: ModelAssignment
    output: "OutputConfig"
    agents: tuple[AgentProfile, ...]
    cancel_event: "asyncio.Event | None" = None

    @classmethod
    def from_cli(cls, args) -> "SessionConfig": ...
    @classmethod
    def from_api(cls, payload: dict) -> "SessionConfig": ...
    @classmethod
    def from_yaml(cls, path: Path) -> "SessionConfig": ...

    def to_yaml(self) -> str: ...
    def to_dict(self) -> dict: ...
```

After this lands, `ResearchTopic` shrinks to topic-only fields (no
`time_budget`, no `model_mode`), and the 3 copies of `TIME_BUDGETS`
in `orchestrator.py:74-89`, `web/sessions.py:156`, and
`web/server.py:339` are all replaced by `from
deepresearch.config.session import TimeBudget`.

#### #67 — `Phase` enum + handler map

```python
# src/deepresearch/agents/registry.py (after #82)
from enum import Enum
from typing import Callable

class Phase(Enum):
    INITIAL_ROUND = "initial_round"
    REFINEMENT    = "refinement"
    CROSS_CHECK   = "cross_check"     # easy to add (was: 2 edits)
    RED_TEAM      = "red_team"        # easy to add (was: 2 edits)
    SCRIBE_COMPILE = "scribe_compile"

class AgentRegistry:
    _HANDLERS: dict[Phase, Callable] = {}

    def dispatch(self, phase: Phase, **kwargs):
        handler = self._HANDLERS[phase]
        return handler(self, **kwargs)

# Adding a new phase = 1 line in the map, not 2 edits
AgentRegistry._HANDLERS = {
    Phase.INITIAL_ROUND:  lambda self, topic, **k: self._initial_round(topic, **k),
    Phase.REFINEMENT:     lambda self, topic, prior, **k: self._refine(topic, prior, **k),
    Phase.SCRIBE_COMPILE: lambda self, reports, **k: self._compile(reports, **k),
}
```

#### #81 — `EventStore`

```python
# src/deepresearch/events/store.py
class EventStore:
    """Single source of truth for all session events."""

    async def emit(self, session_id: str, event: SessionEvent) -> None: ...
    async def subscribe(self, session_id: str) -> AsyncIterator[SessionEvent]: ...
    async def replay(self, session_id: str, since: datetime | None = None
                     ) -> list[SessionEvent]: ...
    async def persist(self, session_id: str) -> None:  # atomic
        ...

@dataclass(frozen=True)
class SessionEvent:
    id: str
    session_id: str
    type: str           # "round_start", "agent_complete", ...
    timestamp: datetime
    data: dict
    correlation_id: str  # for OTel correlation (depends on #85)
```

#### #84 — connection pool + circuit breaker

```python
# src/deepresearch/llm/client.py (after #82)
from typing import ClassVar
import httpx

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, reset_timeout: float = 60):
        self._failures = 0
        self._opened_at: float | None = None

    def is_open(self) -> bool: ...
    def record_failure(self): ...
    def record_success(self): ...

class LLMClient:
    _pool: ClassVar[httpx.AsyncClient | None] = None
    _breakers: ClassVar[dict[str, CircuitBreaker]] = {}

    def __init__(self, model: str, tracker: "TokenTracker | None" = None):
        if LLMClient._pool is None:
            LLMClient._pool = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                limits=httpx.Limits(
                    max_connections=50,
                    max_keepalive_connections=20,
                ),
            )
        self._breaker = LLMClient._breakers.setdefault(
            model, CircuitBreaker(failure_threshold=3, reset_timeout=60)
        )
        self.tracker = tracker
```

#### #85 — OpenTelemetry span (depends on #81)

```python
# src/deepresearch/observability/tracing.py
from opentelemetry import trace
from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor

tracer = trace.get_tracer("deepresearch")
AsyncioInstrumentor().instrument()

async def research_round_1(self, topic):
    with tracer.start_as_current_span(
        "research.round_1",
        attributes={
            "agent.id": self.profile.id,
            "model": self.llm.model,
            "session.id": self.session_id,  # EventStore correlation_id
            "topic.length": len(topic.question),
        },
    ) as span:
        findings = await self._do_research(topic)
        span.set_attribute("findings.key_points", len(findings.key_points))
        return findings
```

## Consequences

### Positive

1. **Breaks the "running in circles" cycle** — Phase 2's
   architectural refactors (#82, #67, #81, #68, #69) eliminate
   the three-sources-of-truth pattern that caused the recurring
   P0 bugs.
2. **Predictable execution order** — the dependency graph makes
   parallelization possible within each phase (e.g. #53, #57, #58
   are independent in Phase 1) but enforces sequencing between
   phases.
3. **Single source of truth for status** — the status table
   below is the canonical artifact.
4. **Acceptance criteria are testable** — each phase has
   concrete exit criteria.
5. **Architecture recovers lost invariants** — `SessionConfig`
   frozen dataclass, `EventStore` atomic persistence, and
   `CircuitBreaker` per-model isolation restore the determinism
   and atomicity properties that ADR-0001 promised (N5, N7).

### Negative

1. **Phase 2 is high-risk** — splitting the 1771-line god
   class and unifying the three event systems will surface
   hidden coupling. The test suite must be expanded in
   parallel.
2. **Phases are sequential by design** — this is slower than
   running all 33 issues in parallel via multiple engineers.
3. **Scope creep risk** — new issues can be added mid-execution.

## Status Tracking Table

| Issue | Title (short) | Severity | Phase | Status | Assignee | Closed Date |
|-------|---------------|----------|-------|--------|----------|-------------|
| [#53](https://github.com/Acharnite/deepresearch/issues/53) | Inverted "all agents failed" check | P0 | 1 | closed 2026-06-17 | — | — |
| [#54](https://github.com/Acharnite/deepresearch/issues/54) | CORS misconfiguration | P0 | 1 | closed 2026-06-17 | — | — |
| [#55](https://github.com/Acharnite/deepresearch/issues/55) | Session concurrency race | P0 | 1 | closed 2026-06-17 | — | — |
| [#56](https://github.com/Acharnite/deepresearch/issues/56) | Constants duplicated 3× (keystone) | P0 | 1 | closed 2026-06-17 | — | — |
| [#57](https://github.com/Acharnite/deepresearch/issues/57) | Clarification cap constant mismatch | P0 | 1 | closed 2026-06-17 | — | — |
| [#58](https://github.com/Acharnite/deepresearch/issues/58) | DDG still in default engines | P0 | 1 | closed 2026-06-17 | — | — |
| [#59](https://github.com/Acharnite/deepresearch/issues/59) | Dead code in orchestrator | P0 | 1 | closed 2026-06-17 | — | — |
| [#60](https://github.com/Acharnite/deepresearch/issues/60) | DB writers race | P0 | 1 | closed 2026-06-17 | — | — |
| [#61](https://github.com/Acharnite/deepresearch/issues/61) | Tasks not cancelled on budget exit | P0 | 1 | closed 2026-06-17 | — | — |
| [#62](https://github.com/Acharnite/deepresearch/issues/62) | Empty-result retry uses repr length | P1 | 3 | open | — | — |
| [#63](https://github.com/Acharnite/deepresearch/issues/63) | Deterministic seed uses `hash()` | P1 | 3 | closed 2026-06-17 | — | — |
| [#64](https://github.com/Acharnite/deepresearch/issues/64) | Token tracking fragmented | P1 | 3 | open | — | — |
| [#65](https://github.com/Acharnite/deepresearch/issues/65) | Semaphore info label inverted | P1 | 3 | closed 2026-06-17 | — | — |
| [#66](https://github.com/Acharnite/deepresearch/issues/66) | Search rate limit + cache dead | P1 | 3 | closed 2026-06-17 | — | — |
| [#67](https://github.com/Acharnite/deepresearch/issues/67) | Type-based dispatch fragile | P1 | 2 | closed 2026-06-17 | — | — |
| [#68](https://github.com/Acharnite/deepresearch/issues/68) | `orchestrator.py` 1771-line god class | P1 | 2 | closed 2026-06-17 | — | — |
| [#69](https://github.com/Acharnite/deepresearch/issues/69) | `_get_timeout` rename incomplete | P1 | 2 | closed 2026-06-17 | — | — |
| [#70](https://github.com/Acharnite/deepresearch/issues/70) | ADR index lists 3 of 15 | P1 | 4 | open | — | — |
| [#71](https://github.com/Acharnite/deepresearch/issues/71) | Anthropic cost key wrong | P2 | 3 | closed 2026-06-17 | — | — |
| [#72](https://github.com/Acharnite/deepresearch/issues/72) | Hardcoded `estimated_duration_seconds` | P2 | 3 | closed 2026-06-17 | — | — |
| [#73](https://github.com/Acharnite/deepresearch/issues/73) | `'gpt-4o'` connectivity fallback | P2 | 3 | closed 2026-06-17 | — | — |
| [#74](https://github.com/Acharnite/deepresearch/issues/74) | Scribe budget 25% too aggressive | P2 | 3 | closed 2026-06-17 | — | — |
| [#75](https://github.com/Acharnite/deepresearch/issues/75) | Path traversal protection incomplete | P2 | 3 | closed 2026-06-17 | — | — |
| [#76](https://github.com/Acharnite/deepresearch/issues/76) | Design doc version stale | P3 | 4 | open | — | — |
| [#77](https://github.com/Acharnite/deepresearch/issues/77) | CHANGELOG missing v0.11.0–0.11.2 | P3 | 4 | open | — | — |
| [#78](https://github.com/Acharnite/deepresearch/issues/78) | TODO.md shows ADR-0015 as not done | P3 | 4 | open | — | — |
| [#79](https://github.com/Acharnite/deepresearch/issues/79) | Design doc §4.1 still 2-round FSM | P3 | 4 | open | — | — |
| [#80](https://github.com/Acharnite/deepresearch/issues/80) | ADRs missing Documentation section | P3 | 4 | open | — | — |
| [#81](https://github.com/Acharnite/deepresearch/issues/81) | Replace 3 event systems with EventStore | P1 | 2 | closed 2026-06-17 | — | — |
| [#82](https://github.com/Acharnite/deepresearch/issues/82) | Centralize `SessionConfig` dataclass | P1 | 2 | closed 2026-06-17 | — | — |
| [#83](https://github.com/Acharnite/deepresearch/issues/83) | Move prompt templates to YAML | P2 | 4 | open | — | — |
| [#84](https://github.com/Acharnite/deepresearch/issues/84) | Connection pool + circuit breaker | P2 | 3 | open | — | — |
| [#85](https://github.com/Acharnite/deepresearch/issues/85) | OpenTelemetry tracing | P2 | 4 | open | — | — |

**Phase progress:**

| Phase | Issues | Closed | % |
|-------|--------|--------|---|
| Phase 1 (P0) | 9 | 9/9 | 100% |
| Phase 2 (Arch) | 5 | 5/5 | 100% |
| Phase 2 complete | — | ✅ | — |
| Phase 3 (Stab/Perf) | 13 | 8/13 | 62% |
| Phase 4 (Docs/Obs) | 8 | 0/8 | 0% |
| **Total** | **33** | **22/33** | **67%** |

## Update Protocol

This ADR is a **living document**. The following events trigger an update:

| Event | What to update | Who |
|-------|----------------|-----|
| An issue is closed on GitHub | Status column in tracking table; "Phase progress" row counts | Director (delegated to Scribes) |
| All issues in a phase are closed | YAML frontmatter `status.{N}: complete`; bump Version to 1.x | Director (delegated to Scribes) |
| An issue is added to the epic mid-execution | Append a new row to the tracking table; reassign Phase if needed | Director (delegated to Scribes) |
| Scope change (new dependency discovered) | Update Dependency Graph section; bump Version | Director (delegated to Scribes) |
| All 4 phases complete | Change ADR status to "Accepted" or "Superseded by ADR-00XX" | Director (delegated to Scribes) |

**Versioning rule:** any structural change (new phase, new
dependency, scope change) increments the version. Row-level
status updates do NOT increment the version.

## Acceptance Criteria — When is this ADR "done"?

This ADR is "done" when **all** of the following are true:

- [ ] All 33 issues (#53–#85) closed on GitHub
- [ ] All 4 phases marked `complete` in the YAML frontmatter
- [ ] Code review session 2026-06-17 has been fully addressed
- [ ] Status changes to either "Accepted" or "Superseded by ADR-00XX"
- [ ] `CHANGELOG.md` has a v0.12.0 entry that references this ADR
- [ ] Design doc version bumped to 1.3+ and references this epic

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| P0 issues reveal deeper architectural problems than expected | Medium | Medium | Phase 2 is specifically designed to absorb the architectural fallout |
| Phase 2 refactors break existing tests | High | High | Test suite expansion runs in parallel; 309+ baseline is a hard gate |
| Scope creep — new issues added mid-execution | Medium | Low | New issues go into a future ADR, not this one |
| Phase ordering is wrong | Low | Medium | Re-order within phase is allowed; dependency graph is source of truth |
| `EventStore` (#81) cannot be completed within Phase 2 | Low | High | Defer #85 to a future ADR rather than ship tracing without stable correlation IDs |
| Engineer capacity insufficient for 4 phases | Medium | Medium | Phases can be parallelized within each phase |

## References

### GitHub Issues (33 — full epic)

- P0: [#53](https://github.com/Acharnite/deepresearch/issues/53), [#54](https://github.com/Acharnite/deepresearch/issues/54), [#55](https://github.com/Acharnite/deepresearch/issues/55), [#56](https://github.com/Acharnite/deepresearch/issues/56), [#57](https://github.com/Acharnite/deepresearch/issues/57), [#58](https://github.com/Acharnite/deepresearch/issues/58), [#59](https://github.com/Acharnite/deepresearch/issues/59), [#60](https://github.com/Acharnite/deepresearch/issues/60), [#61](https://github.com/Acharnite/deepresearch/issues/61)
- P1: [#62](https://github.com/Acharnite/deepresearch/issues/62), [#63](https://github.com/Acharnite/deepresearch/issues/63), [#64](https://github.com/Acharnite/deepresearch/issues/64), [#65](https://github.com/Acharnite/deepresearch/issues/65), [#66](https://github.com/Acharnite/deepresearch/issues/66), [#67](https://github.com/Acharnite/deepresearch/issues/67), [#68](https://github.com/Acharnite/deepresearch/issues/68), [#69](https://github.com/Acharnite/deepresearch/issues/69), [#70](https://github.com/Acharnite/deepresearch/issues/70), [#81](https://github.com/Acharnite/deepresearch/issues/81), [#82](https://github.com/Acharnite/deepresearch/issues/82)
- P2: [#71](https://github.com/Acharnite/deepresearch/issues/71), [#72](https://github.com/Acharnite/deepresearch/issues/72), [#73](https://github.com/Acharnite/deepresearch/issues/73), [#74](https://github.com/Acharnite/deepresearch/issues/74), [#75](https://github.com/Acharnite/deepresearch/issues/75), [#83](https://github.com/Acharnite/deepresearch/issues/83), [#84](https://github.com/Acharnite/deepresearch/issues/84), [#85](https://github.com/Acharnite/deepresearch/issues/85)
- P3: [#76](https://github.com/Acharnite/deepresearch/issues/76), [#77](https://github.com/Acharnite/deepresearch/issues/77), [#78](https://github.com/Acharnite/deepresearch/issues/78), [#79](https://github.com/Acharnite/deepresearch/issues/79), [#80](https://github.com/Acharnite/deepresearch/issues/80)

### Related ADRs

- **ADR-0001** — Multi-Agent Research Architecture (N5 determinism, N7 token tracking)
- **ADR-0006** — Web Search and Tool Calling Integration
- **ADR-0007** — Clarification Protocol and Refinement
- **ADR-0009** — CI/CD Pipeline, npm Wrapper, and Docker Distribution
- **ADR-0010** — Dynamic Research Rounds
- **ADR-0011** — Concurrency Limits and Web Search Throttling
- **ADR-0012** — Replace DuckDuckGo with SearXNG
- **ADR-0013** — SearXNG Engine Optimization
- **ADR-0014** — Enforce Time Budget and Correct Labels
- **ADR-0015** — Fix JSON Parsing and Topic Drift
- **ADR-0048** (KodeHold) — Mandatory Documentation Review

## Deliverables

- ADR document: `docs/adr/ADR-0016-epic-tracker-code-review-2026-06-17.md`
- ADR index update: `docs/adr/README.md` (add 3 new rows)
