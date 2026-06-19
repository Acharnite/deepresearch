---
phase:
  current: 1
  total: 1
  status:
    1: proposed
---

# ADR-0011: Concurrency Limits and Web Search Throttling

## Status

Accepted

**Version:** 1.0
**Last Updated:** 2026-06-16

## Context

When running multiple concurrent research sessions (e.g., 9 sessions — 3 quick, 3 medium, 3 deep), web search providers (Google, Brave, DuckDuckGo) return HTTP 429 (rate limit) errors. This causes agents to fail with empty results, leading to PDF underweight errors and session failures.

### Root Cause Analysis

The request volume scales multiplicatively:

```
concurrent_sessions × agents_per_session × searches_per_agent_per_round × rounds
= 9 × 6 × ~3 × 2
≈ 324 simultaneous search requests
```

Each of the 6 agents in each session performs multiple web searches per research round. With 9 sessions running concurrently, the aggregate search volume overwhelms free-tier web search providers. DuckDuckGo in particular has aggressive rate limiting for automated queries from the same IP.

### Observed Failures

| Symptom | Frequency | Impact |
|---------|-----------|--------|
| HTTP 429 from DuckDuckGo | ~40% of sessions under 9 concurrent | Agents return empty findings |
| "Search failed" error dicts | ~25% of sessions | Agents lack web-sourced evidence |
| PDF underweight warnings | ~15% of sessions | Reports lack sufficient citations |
| Complete session failure | ~5% of sessions | Agent round produces no usable output |

### Current Architecture

- **Web search:** `web_search()` in `src/deepresearch/tools/web_search.py` — DuckDuckGo via `ddgs` library, synchronous via `asyncio.to_thread()`
- **Session management:** `MultiSessionManager` in `src/deepresearch/web/sessions.py` — no concurrency limit, creates session tasks unconditionally
- **Server:** `POST /api/run` in `src/deepresearch/web/server.py` — accepts new sessions without checking active count
- **Agent dispatch:** Each agent calls `web_search()` independently during its generation loop (up to 5 tool-call rounds per agent)

### Key Forces

1. Free web search providers have aggressive rate limits per IP
2. No coordination between sessions — each runs independently
3. DuckDuckGo rate limits are undocumented and inconsistent
4. Users expect multiple sessions to work without configuration
5. Search staggering reduces throughput but prevents 429 errors
6. A global search semaphore prevents coordinated overload

### Prior Art / Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| No limits (current) | Maximum throughput | 429 errors under load |
| Per-session search limit | Simple | Doesn't coordinate across sessions |
| Global search semaphore + staggering (chosen) | Prevents provider overload, minimal throughput loss | Slightly slower individual sessions |
| Switch to paid search API | No rate limits | Cost, API key management |
| Cache search results across agents | Reduces total searches | Stale results, complex invalidation |
| Queue all searches through a single worker | Simple coordination | Single point of failure, bottleneck |

## Decision

### 1. Session Concurrency Limit

**Maximum 3 concurrent research sessions** by default, configurable via `--max-concurrent` CLI flag and settings.

When the limit is reached, the server returns HTTP 429 to new `POST /api/run` requests with a message indicating the limit has been reached.

```python
# src/deepresearch/web/sessions.py

class MultiSessionManager:
    _session_semaphore: asyncio.Semaphore  # initialized with max_concurrent value

    def __init__(self, max_concurrent: int = 3) -> None:
        self._max_concurrent = max_concurrent
        self._session_semaphore = asyncio.Semaphore(max_concurrent)
        # ... existing init ...
```

The semaphore is acquired before creating a new session task and released when the session completes:

```python
async def start_session(self, topic: str, config: dict) -> str:
    if self._session_semaphore.locked():
        raise ConcurrencyLimitReached(
            f"Maximum {self._max_concurrent} concurrent sessions reached. "
            "Try again later."
        )
    async with self._session_semaphore:
        session_id = self._create_session(topic, config)
        # ... start session task ...
        return session_id
```

**Why FIFO queuing was rejected:** Users expect immediate feedback. A queue with indefinite wait is worse than a fast 429 with "try again later." The client (dashboard or CLI) can retry after a delay.

### 2. Web Search Strottling

Two layers of throttling prevent provider overload:

#### Per-Agent Search Delay

Add a random 1–3 second jitter between consecutive web search requests within each agent. This prevents burst patterns where all 6 agents search simultaneously.

```python
# src/deepresearch/tools/web_search.py

import asyncio
import random

_SEARCH_DELAY_MIN = 1.0  # seconds
_SEARCH_DELAY_MAX = 3.0  # seconds

async def web_search(
    query: str, max_results: int = 5, retries: int = 3
) -> list[dict[str, str]]:
    # ... existing implementation ...

    # Add jitter before each search attempt
    delay = random.uniform(_SEARCH_DELAY_MIN, _SEARCH_DELAY_MAX)
    await asyncio.sleep(delay)
```

The jitter is applied before each search call (including retries), not after. This ensures the delay is between searches regardless of success/failure.

#### Global Search Rate Limit

A global `asyncio.Semaphore` limits concurrent searches to **5 across all agents and sessions**. This prevents the aggregate search volume from overwhelming providers even when multiple sessions are active.

```python
# src/deepresearch/tools/web_search.py

# Global search concurrency limit — shared across all agents and sessions.
# Prevents HTTP 429 errors from web search providers under heavy concurrent load.
_global_search_semaphore: asyncio.Semaphore = asyncio.Semaphore(5)

async def web_search(
    query: str, max_results: int = 5, retries: int = 3
) -> list[dict[str, str]]:
    async with _global_search_semaphore:
        # ... existing implementation (with per-request jitter) ...
```

**Rationale for 5:** With 3 concurrent sessions × 6 agents = 18 agents potentially searching, a limit of 5 concurrent searches means at most 5 agents search simultaneously while others wait. This keeps the total search rate below DuckDuckGo's observed ~10 req/30s threshold for automated queries.

### 3. API Changes

#### `POST /api/run` — 429 Response

When the session concurrency limit is reached, the endpoint returns:

```json
{
  "error": "concurrency_limit_reached",
  "message": "Maximum 3 concurrent sessions. Try again later.",
  "active_sessions": 3,
  "max_concurrent": 3
}
```

HTTP status: **429 Too Many Requests**

#### `GET /api/system/concurrency` — New Endpoint

Returns the current concurrency state:

```json
{
  "active_sessions": 2,
  "max_concurrent": 3,
  "available_slots": 1,
  "queued_sessions": 0
}
```

This endpoint is used by the dashboard to show concurrency status and disable the "New Session" button when slots are full.

### 4. CLI Changes

Add `--max-concurrent` flag to the `run` command:

```python
# src/deepresearch/main.py — in build_parser()

run_parser.add_argument(
    "--max-concurrent",
    type=int,
    default=3,
    metavar="[1-20]",
    help="Maximum concurrent research sessions (default: 3)",
)
```

The value is passed to `MultiSessionManager` at startup:

```python
# In main() or serve() command
manager = MultiSessionManager(max_concurrent=args.max_concurrent)
```

### 5. Settings Integration

The concurrency limit is configurable via the Settings tab in the dashboard:

- `concurrency.max_sessions` — integer, default 3
- `concurrency.max_global_searches` — integer, default 5
- `concurrency.search_delay_min` — float, default 1.0
- `concurrency.search_delay_max` — float, default 3.0

These values are persisted in the settings file and loaded at server startup.

### 6. Implementation Files

| File | Change |
|------|--------|
| `src/deepresearch/web/server.py` | Add session semaphore, new `/api/system/concurrency` endpoint, 429 response for `POST /api/run` |
| `src/deepresearch/tools/web_search.py` | Add per-request jitter delay, global search semaphore |
| `src/deepresearch/web/sessions.py` | Check semaphore before starting session, release on completion |
| `src/deepresearch/__main__.py` | Add `--max-concurrent` CLI flag |
| `src/deepresearch/main.py` | Pass `max_concurrent` to `MultiSessionManager` |
| Dashboard settings tab | Add concurrency configuration fields |

## Consequences

### Positive

1. **Prevents 429 errors** — staggered searches and concurrency limits keep request volume below provider thresholds
2. **Graceful degradation** — 429 response with clear message instead of silent failures
3. **Observable state** — `/api/system/concurrency` endpoint lets the dashboard show slot availability
4. **Configurable** — users can tune limits based on their search provider and network conditions
5. **Minimal throughput impact** — per-agent jitter adds 1–3s per search but prevents retries that cost 4–10s each
6. **Backward compatible** — default limit of 3 sessions is generous for single-user deployments

### Negative

1. **Reduced parallelism** — sessions queue or fail when limit is reached; users can't run 9 sessions simultaneously
2. **Search latency increase** — per-agent jitter adds 1–3s per search call; global semaphore adds wait time under load
3. **Global semaphore contention** — with 5 concurrent searches across 18 agents, ~13 agents wait at any given time
4. **Hardcoded defaults** — 3 sessions / 5 searches are conservative defaults; power users may need to override

### Risks

1. **DuckDuckGo rate limit changes** — if DuckDuckGo tightens limits, the 5-search global may be insufficient. Mitigation: configurable via settings, can be lowered
2. **Semaphore deadlock** — if a session crashes without releasing the semaphore, slots are permanently consumed. Mitigation: `try/finally` blocks around semaphore acquisition, session cleanup on error
3. **Dashboard confusion** — users may not understand why sessions queue or fail. Mitigation: clear 429 message, concurrency indicator in dashboard

### Throughput Impact Analysis

| Scenario | Before (no limits) | After (with limits) | Impact |
|----------|---------------------|----------------------|--------|
| 1 session, 6 agents | 18 searches in ~5s | 18 searches in ~12s | +7s (jitter) |
| 3 sessions, 18 agents | 54 searches in ~5s, ~40% fail | 54 searches in ~35s, 0% fail | +30s but reliable |
| 9 sessions, 54 agents | 162 searches in ~5s, ~60% fail | 54 searches (queued), 108 waiting | Slower but functional |

The key insight: staggered searches with retries are faster than failed searches with retries. A search that succeeds on the first attempt (with 2s jitter) is faster than a search that fails twice before succeeding (4s + 8s backoff = 12s wasted).

## ADR References

- **ADR-0006** (Web Search and Tool Calling) — `web_search()` function, DuckDuckGo integration, tool calling loop
- **ADR-0003** (Web Frontend and Multi-Session) — `MultiSessionManager`, `POST /api/run`, dashboard settings
- **ADR-0001** (Multi-Agent Research Architecture) — 6-agent architecture that drives search volume

---

## Implementation Plan

| Step | Description | Files Changed |
|------|-------------|---------------|
| 1 | Add `_global_search_semaphore` and per-request jitter to `web_search()` | `tools/web_search.py` |
| 2 | Add `_session_semaphore` to `MultiSessionManager`, check before session start | `web/sessions.py` |
| 3 | Add 429 response for `POST /api/run` when limit reached | `web/server.py` |
| 4 | Add `GET /api/system/concurrency` endpoint | `web/server.py` |
| 5 | Add `--max-concurrent` CLI flag | `main.py` |
| 6 | Add concurrency settings to dashboard Settings tab | dashboard HTML/JS |
| 7 | Add tests for concurrency limiting and search throttling | `tests/` |
| 8 | Update ADR-0006 with throttling addendum | `docs/adr/ADR-0006-*.md` |

## Open Questions

1. Should the session concurrency limit apply to the CLI `run` command as well? Decision: No — CLI runs one session at a time; the limit only applies to the web server's multi-session mode.
2. Should search results be cached across agents within the same session? Decision: Not in this ADR — adds complexity around cache invalidation and staleness. Future ADR if needed.
3. Should the global search semaphore be per-provider (DuckDuckGo, Google, Brave) instead of global? Decision: Global for now — most deployments use a single provider. Can be split later if multi-provider support is added.

## Changelog

| Date | Change |
|------|--------|
| 2026-06-16 | Initial version — ADR-0011 proposed |

## Documentation

- **Design doc reference:** §3.3 Concurrency
- **Implementation PR:** TBD
- **Related ADRs:** ADR-0006, ADR-0014
