# ADR-0004: Test Findings and Architecture Fixes

## Status

Proposed

**Version:** 1.0
**Last Updated:** 2026-06-14

## Context

Five automated test runs were conducted to validate the DeepeResearch research pipeline across quick (120s) and medium (300s) budgets. The tests covered diverse topics (vertical farming, quantum computing, lab-grown meat, fusion energy, autonomous vehicles) and exercised the full pipeline: agent execution, web search, collaboration, refinement, scribe compilation, and PDF generation.

### Test Results Summary

| Test | Topic | Budget | Agents OK | PDF | Duration |
|------|-------|--------|-----------|-----|----------|
| 1 | Vertical farming | quick (120s) | 2/6 | 8KB | 300s |
| 2 | Quantum computing | quick (120s) | 0/6 | empty | 81s |
| 3 | Lab-grown meat | medium (300s) | 6/6 | 31KB | 555s |
| 4 | Fusion energy | quick (120s) | 0/6 | empty | 81s |
| 5 | Autonomous vehicles | medium (300s) | 5/6 | 30KB | 510s |

Quick budget fails 3/3 times (tests 1, 2, 4). Medium budget succeeds 2/3 times (tests 3, 5). Test 1 partially succeeded with 2/6 agents but produced a minimal 8KB PDF.

### Bugs Discovered

14 bugs were identified across the test suite, categorized by severity:

#### P0 — Critical (6 bugs)

1. **Quick budget systematically too short (#32)**: Per-agent timeout = `max(30, budget // 2)`. For quick (120s), that is 60s. 6 parallel agents each performing 3-6 DuckDuckGo web searches + LLM calls exceed this. Quick budget fails 3/3 times.

2. **Round 2 never runs (#23, #30, #33)**: Round 2 is skipped in ALL 5 sessions regardless of `gaps` count (gaps: 1, 3, 14, 23). The skip condition `gaps < 2 AND confidence >= 0.5` appears to have a logic error. Confirmed in 5 consecutive sessions.

3. **All agents timeout + session shows "complete" (#31)**: When all 6 agents fail with timeout, the session status is set to "complete" instead of "error". A 0-report, 0-char PDF is generated and presented as valid output.

4. **Agent returns empty result without failed marker (#22)**: philosophical-thinker returned summary=0, key_points=0, confidence=0.5 in session #23d80cc7. The agent was marked "success" despite producing nothing. Empty results propagate to shared knowledge as blank entries.

5. **Refinement always returns 0 agents (#34)**: Refinement phase returns `refined_agents: 0` in all sessions with data (tests 1, 3, 5). The entire follow-up to refinement pipeline is non-functional.

6. **DuckDuckGo ConnectTimeout (#28)**: `TimeoutException("Request timed out: ConnectTimeout('timed out')")` appears in logs. Web search failures cascade to agent timeouts.

#### P1 — Important (5 bugs)

7. **Scribe clarification asks same agent 5x (#29)**: In session #70c23273, skeptical-academic was asked 5 clarification questions consecutively. No diversity in agent selection.

8. **Scribe clarification stops too fast (#26)**: In session #23d80cc7, only 1 clarification question was asked before stopping. The 2-consecutive-empties limit may be too aggressive.

9. **Empty "Areas of Agreement" in shared knowledge (#24)**: `compute_shared_knowledge` returns empty agreement sections. Only disagreements and gaps are identified. This weakens refinement quality.

10. **Only 2/6 agents participate in refinement (#25)**: In test 1, only 2 of 6 agents received refinement questions despite 6 being active.

11. **data-analyst empty results in 2/5 sessions (#35)**: Low-temperature agents (data-analyst, 0.2) and high-temperature agents (philosophical-thinker, 0.85) both return empty results occasionally.

#### P2 — Enhancement (2 bugs)

12. **Scribe streaming sends individual tokens (#27)**: Each streaming token = 1 SSE event. High network overhead.

13. **Scribe clarification status (#26)**: Users perceive scribe as "waiting" during clarification because no status events are shown. Partially addressed in v0.4.0.

### Root Cause Analysis

#### Root Cause 1: Timeout Budget Mismatch
The `_get_timeout()` formula `max(30, budget // 2)` assumes agents complete in half the total budget. But with web search (variable latency + DuckDuckGo timeouts), agents need more time. The formula does not account for:
- Web search latency (0.5-5s per search)
- Multiple rounds of tool calls (3-6 searches per agent)
- DuckDuckGo ConnectTimeout (occasional 10-30s delays)
- 6 agents competing for DuckDuckGo bandwidth

#### Root Cause 2: Round 2 Skip Logic Bug
The `round2_skip` event fires with varying `gaps` values (1, 3, 14, 23), but Round 2 never runs. The skip condition in `orchestrator.py` has a logic error. The `budget: 'custom'` value in the event suggests the custom budget path may bypass the gap check.

#### Root Cause 3: Empty Result Handling
When an LLM call returns empty or unparseable JSON, the agent falls through with empty Findings. The orchestrator treats this as "success" because no exception was raised. The result is published to the collaboration bus as-is, poisoning shared knowledge.

#### Root Cause 4: Refinement Pipeline Disconnect
The refinement phase depends on follow-up questions being properly distributed to agents. But:
- philosophical-thinker sometimes does not respond to followup_start (missing from results)
- Follow-up questions may not match the correct agents
- Refinement timeout (`_get_timeout() // 2`) may be too short

### Prior Art / Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| Increase all budgets | Simple fix | Wastes tokens on already-working paths |
| Reduce agent count for quick | Faster | Loses multi-perspective value |
| Per-agent adaptive timeout | Precise | Complex to tune, hard to predict |
| Minimum timeout floor | Simple, effective | May still fail under heavy search load |
| Retry with backoff | Handles transient failures | Adds complexity, not a root cause fix |

## Decision

### Fix 1: Increase Quick Budget

**Change:** Raise quick budget from 120s to 300s (matching medium), and increase per-agent timeout floor from 30s to 120s.

**Rationale:** The 120s budget is fundamentally insufficient for 6 agents performing web searches. Each agent needs 3-6 searches at 0.5-5s each, plus LLM inference time. The 60s per-agent timeout (`budget // 2`) is too aggressive when DuckDuckGo can intermittently take 10-30s per request. A 120s floor ensures agents have time to complete at least one full search+LLM cycle even under adverse network conditions.

### Fix 2: Fix Round 2 Skip Logic

**Change:** Audit and fix the skip condition in `orchestrator.py`. Ensure `gaps >= 2` triggers Round 2 regardless of budget type. Remove the `budget: 'custom'` bypass if it exists.

**Rationale:** Round 2 is the primary mechanism for agents to address knowledge gaps identified during collaboration. Without it, the pipeline degrades to a single-pass system with no iterative improvement. The skip condition must be deterministic: if gaps exist, Round 2 should run.

### Fix 3: Mark Empty Results as Failed

**Change:** After an agent completes, validate that `summary` is non-empty and `key_points` has at least one entry. If validation fails, mark the agent as "failed" and exclude its output from shared knowledge.

**Rationale:** Empty results poison the collaboration bus. When blank findings propagate to `compute_shared_knowledge`, they produce empty agreement sections and meaningless gap analysis. Failing fast on empty results is both simpler and more correct than attempting to repair downstream.

### Fix 4: Fix Refinement Pipeline

**Change:** Debug why follow-up questions do not reach all agents. Ensure:
- Follow-up questions are routed to agents that produced non-empty Round 1 results
- Refinement timeout is at least 60s (currently `budget // 4` which can be too short)
- The `refined_agents` count reflects actual agents contacted, not a hardcoded 0

**Rationale:** Refinement is the mechanism by which agents deepen their analysis based on cross-agent insights. A non-functional refinement pipeline reduces the system to single-pass research with a collaboration step that has no downstream effect.

### Fix 5: DuckDuckGo Retry with Backoff

**Change:** Add retry with exponential backoff (3 attempts, base delay 2s, max 30s) for DuckDuckGo requests. Consider fallback to an alternative search provider on persistent failure.

**Rationale:** DuckDuckGo ConnectTimeout is a known transient failure. Retries with backoff handle most cases. A fallback provider provides resilience against prolonged outages.

### Fix 6: Status "error" When All Agents Fail

**Change:** When `failed_agents` count equals total agents, set session status to "error" instead of "complete". Do not generate a PDF. Emit a clear error event with the failure reason.

**Rationale:** Presenting an empty PDF as "complete" is misleading. Users must immediately know that the session failed so they can retry with a larger budget or diagnose the issue.

## Consequences

### Positive

1. **Quick budget reliability** — Increasing the budget and timeout floor eliminates the systematic timeout failures observed in tests 1, 2, and 4
2. **Round 2 functionality** — Fixing the skip logic restores the iterative improvement mechanism that is central to the multi-agent architecture
3. **Shared knowledge integrity** — Marking empty results as failed prevents poisoned collaboration data from propagating through the pipeline
4. **Refinement pipeline restoration** — Fixing follow-up routing ensures agents actually benefit from cross-agent insights
5. **Search resilience** — Retry with backoff handles transient DuckDuckGo failures without manual intervention
6. **Honest status reporting** — Error status on total failure gives users clear feedback instead of misleading "completion"

### Negative

1. **Quick sessions take longer** — Increasing quick budget from 120s to 300s means quick sessions may run up to 5 minutes instead of 2. Users who valued speed must accept this trade-off for reliability.
2. **Refinement adds latency** — Fixing the refinement pipeline means sessions with gaps will run an additional round, increasing total duration.
3. **Retry overhead** — DuckDuckGo retries add 2-30s per failed search attempt. This is acceptable given the alternative (agent timeout).
4. **More false-positive failures** — Marking empty results as "failed" may occasionally reject marginal agents that produced minimal but valid output. The threshold (non-empty summary + at least 1 key point) is conservative enough to minimize this.

### Neutral

1. All fixes are backward-compatible with existing session output format
2. No new external dependencies introduced
3. Fixes apply to both CLI and web dashboard modes
4. Existing ADRs (0001, 0002, 0003) remain valid; this ADR documents corrections to implementation, not architectural changes

## Related Issues
- #17 (Test Coverage 350+): Current suite has 309 tests. Gaps remain in multi-round, time budgets, providers, PDF edge cases, SSE reconnection.

## ADR References

- **ADR-0001** (Multi-Agent Research Architecture) — timeout formula and Round 2 skip logic defined here; this ADR corrects both
- **ADR-0002** (Agent Personality & Model Selection) — empty result handling affects agent quality assessment
- **ADR-0003** (Web Frontend & Multi-Session Architecture) — status reporting fix affects SSE event stream
