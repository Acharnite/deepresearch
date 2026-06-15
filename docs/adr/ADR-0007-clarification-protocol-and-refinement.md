---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0007: Clarification Protocol and Refinement Phase

## Status

Proposed

**Version:** 1.0
**Last Updated:** 2026-06-15

## Context

After Round 1, agents may have gaps, ambiguities, or contradictions in their findings. The scribe, when compiling the final paper, may encounter claims that are unclear, unsupported, or conflicting across agents. A clarification protocol allows the scribe to ask targeted questions to specific agents before finalizing the paper. Additionally, agents should be able to refine their findings based on follow-up questions from other agents before Round 2.

### Key Forces
1. Scribe needs to resolve ambiguities before producing a coherent paper
2. Clarification must not become a bottleneck — parallel execution is critical
3. Agents need to know which questions are directed at them specifically
4. Refinement must be time-bounded to prevent runaway clarification loops
5. Empty or failed clarification responses must not waste recompilation cycles
6. Follow-up questions between agents need directed routing (not broadcast)

### Prior Art / Alternatives Considered
| Approach | Pros | Cons |
|----------|------|------|
| No clarification | Simple, fast | Ambiguous claims persist in final paper |
| Sequential clarification | Simple to implement | Slow, blocks on each agent |
| Parallel clarification (chosen) | Fast, agents respond concurrently | Complex task management |
| Broadcast questions | Simple | Agents waste time on irrelevant questions |
| Directed questions (chosen) | Targeted, efficient | Requires target routing infrastructure |

## Decision

### Follow-Up Questions: Directed Agent Routing

Each agent reviews shared knowledge after Round 1 and generates follow-up questions. Questions can be directed to specific agents via `target_agent_ids`:

- **`FollowUpQuestions` model** has a `target_agent_ids` field (list of optional strings)
- Each question maps to a target agent by index position — `questions[i]` targets `target_agent_ids[i]`
- If `target_agent_ids[i]` is `None`, the question is general (any agent can answer)
- The orchestrator's `collect_followup_questions()` passes `agent_ids` to each agent so it knows which agents are available for targeting

### Clarification Protocol: Scribe-Initiated

After initial compilation, the scribe identifies ambiguous or contradictory claims and asks agents for clarification:

1. **Identify claims** — the scribe LLM reviews the compiled paper and original reports, identifies the single most important claim needing clarification
2. **Fire concurrent clarifications** — clarification requests to agents are fired as `asyncio.create_task()` (not sequential `await`)
3. **Wait and recompile** — after all pending clarifications complete, the scribe recompiles the paper incorporating the new information
4. **Repeat** — the protocol loops until no more clarifications are needed or guard rails trip

### Parallel Clarification Execution

Clarification requests to agents are fired concurrently:

```python
task = asyncio.create_task(
    self._clarify_claim(claim, agent_id, context, clarification_fn)
)
pending.append((claim, agent_id, task))
```

This allows multiple agents to be clarified in parallel rather than sequentially. The scribe identifies claims one by one (fast LLM call), fires agent clarifications as background tasks, and only waits + recompiles at the end.

### Guard Rails

The clarification protocol has multiple guard rails to prevent runaway loops:

| Guard Rail | Value | Purpose |
|------------|-------|---------|
| Max clarification rounds per agent | 5 (`_MAX_CLARIFICATION_ROUNDS`) | Prevents over-clarifying a single agent |
| Max total rounds | `min(5 * num_agents, 5)` | Caps total clarification iterations |
| Time budget | 180 seconds (3 minutes) | Hard wall-clock limit |
| Consecutive empty responses | 2 | Stops protocol when agents can't help |
| Asked claims tracking | `_asked_claims` set | Prevents duplicate questions |

### Empty Response Handling

Empty clarification responses are handled gracefully:

- If an agent returns `None` or an empty string, the response is skipped
- No recompilation is triggered for empty responses (saves LLM calls)
- A consecutive empty counter tracks how many empty responses occur in a row
- After 2 consecutive empty responses, the protocol stops entirely
- The counter resets to 0 on any successful (non-empty) response

### Refinement Phase

After follow-up questions are collected, agents refine their findings based on the questions directed at them:

1. **Question filtering** — each agent receives only questions targeting it (by `target_agent_ids`)
2. **Parallel execution** — refinement runs via `asyncio.gather()` across all non-failed agents
3. **Timeout** — refinement timeout is `_get_timeout() // 2` (half the normal round timeout)
4. **Result merging** — refined findings replace the agent's Round 1 results in `round_1_results`
5. **Conditional** — agents with no targeted questions are skipped (return `None`)

### Status Events

The clarification protocol publishes status events for dashboard visibility:

- `scribe_clarifying` with steps:
  - `identifying_claims` — scribe LLM is identifying claims needing clarification
  - `asking_agent:{agent_id}` — clarification request fired to a specific agent
  - `recompiling` — paper is being recompiled with new clarification data

### Orchestrator Integration

The orchestrator routes clarification queries to agents:

- `Orchestrator._handle_clarification()` looks up agents by ID in `self._agents` dict
- The dispatcher in `AgentRegistry.agent_factory` recognizes `ClarificationQuery` instances and routes to the agent's `clarify()` method
- If the agent is unavailable or fails, a default `ClarificationResponse` is returned instead of crashing
- The scribe's `compile()` method receives `clarification_fn` and `status_callback` parameters

## Consequences

### Positive
1. **Higher quality synthesis** — ambiguities and contradictions are resolved before final paper
2. **Targeted questions** — directed routing means agents only answer relevant questions
3. **Parallel execution** — concurrent clarification requests minimize latency
4. **Reduced redundancy** — asked claims tracking prevents duplicate questions
5. **Time-bounded** — 3-minute budget and round limits prevent runaway loops
6. **Graceful degradation** — empty responses and agent failures don't crash the protocol
7. **Refinement improves Round 2** — agents refine before Round 2, producing better second-pass findings

### Negative
1. **Added complexity** — clarification protocol adds significant code to `ScribeAgent` and orchestrator
2. **Latency** — clarification adds 1–3 minutes to total session time
3. **LLM call overhead** — each clarification round requires scribe LLM calls for identification + recompilation
4. **Empty response waste** — even with guards, some LLM calls produce unusable responses
5. **Refinement timeout** — half-timeout may be too short for complex refinements

### Neutral
1. Clarification is optional — only runs when `clarification_fn` is provided
2. The protocol is single-threaded for claim identification (fast LLM call) but parallel for agent responses
3. Refinement replaces Round 1 results in-place — no separate storage
4. Status events are best-effort — dashboard may miss rapid state transitions

## ADR References
- **ADR-0001** (Multi-Agent Research Architecture) — pipeline integration and agent dispatch
- **ADR-0002** (Agent Personality & Model Selection) — agent clarify() method interface

---

## Implementation Status (Updated 2026-06-15)

| Decision | Status | Notes |
|----------|--------|-------|
| Follow-up questions with target_agent_ids | ✅ Implemented | Directed question routing |
| Clarification protocol | ✅ Implemented | scribe_agent.py _run_clarification_protocol() |
| Parallel clarification via create_task | ✅ Implemented | Concurrent agent requests |
| Guard rails (5 rounds, 3min, 2 empties) | ✅ Implemented | Multiple safety limits |
| Empty response handling | ✅ Implemented | Skipped, no wasted recompilation |
| Refinement phase | ✅ Implemented | asyncio.gather(), half-timeout |
| Asked claims tracking | ✅ Implemented | _asked_claims set |
| Status events (scribe_clarifying) | ✅ Implemented | Identifying, asking, recompiling steps |
| Orchestrator clarification routing | ✅ Implemented | _handle_clarification() |
