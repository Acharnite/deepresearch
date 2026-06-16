---
phase:
  current: 1
  total: 1
  status:
    1: proposed
---

# ADR-0010: Dynamic Research Rounds

## Status

Proposed

**Version:** 1.1
**Last Updated:** 2026-06-16

## Context

The current research system supports exactly 2 research rounds (`ROUND1` and `ROUND2`). While this works well for straightforward topics, complex, multi-faceted research questions benefit from deeper iterative exploration (3–5+ rounds). The architecture currently enforces a hard 2-round limit through several design constraints:

### Current Architecture (from code analysis)

#### Finite State Machine
```
IDLE → CONFIGURING → ROUND1 → COLLABORATING → FOLLOWUP
  → REFINING → ROUND2 → COMPILING → OUTPUT → COMPLETE
```

Only two round states are defined (`ROUND1` and `ROUND2`). Round 2 is optional — skipped when knowledge gaps are below a threshold.

#### Round Execution

`run_round(round_num, agents, topic, shared)` in `orchestrator.py` (line 231):

- `round_num` is a parameter but the method is only ever called with `1` or `2`
- Round 1 is called with `(topic,)` — agents execute independent research
- Round 2+ is called with `(topic, shared)` — agents receive shared context from collaboration
- Parallel execution via `asyncio.create_task()` with per-agent timeouts

#### Dynamic Round 2 Decision (orchestrator.py lines 938–978)

```python
_gap_threshold = 2
_knowledge_gaps = len(shared.knowledge_gaps)
_disagreements = len(shared.areas_of_disagreement)
_total_gaps = _knowledge_gaps + _disagreements
_low_confidence_agents = sum(1 for f in round_1_results
                             if f.confidence < 0.5)
_should_run_round_2 = (_total_gaps >= _gap_threshold
                       or _low_confidence_agents > 0)
```

Round 2 runs if gaps ≥ 2 or any low-confidence agents exist. This is a single binary decision point — there is no mechanism to run Round 3, Round 4, or beyond.

#### Agent Dispatch (registry.py)

Routes by argument type signature:

| Arguments | Method Called |
|-----------|--------------|
| `(ResearchTopic,)` | `research_round_1(topic)` |
| `(SharedKnowledge,)` | `review_findings(shared)` |
| `(ResearchTopic, SharedKnowledge)` | `research_round_2(topic, shared, questions)` |
| `(Findings,)` | `write_report(r1, None)` |
| `(ClarificationQuery,)` | `clarify(query)` |

#### Base Agent Interface (base_agent.py)

Three round-related abstract methods:

- `research_round_1(topic: ResearchTopic) -> Findings`
- `review_findings(shared: SharedKnowledge) -> FollowUpQuestions`
- `research_round_2(topic: ResearchTopic, shared: SharedKnowledge, questions: FollowUpQuestions) -> Findings`
- `write_report(r1: Findings, r2: Findings | None) -> IndividualReport`

#### SharedKnowledge Model (models.py)

```python
class SharedKnowledge(BaseModel):
    round_number: int
    all_summaries: dict[str, str]
    key_themes: list[str]
    areas_of_agreement: list[str]
    areas_of_disagreement: list[str]
    knowledge_gaps: list[str]
```

#### Dashboard State Rendering (constants.js)

```javascript
export const STATE_ORDER = ['IDLE','CONFIGURING','ROUND1','COLLABORATING',
  'FOLLOWUP','REFINING','ROUND2','COMPILING','OUTPUT','COMPLETE'];
```

All state labels, badge classes, and colors are hardcoded in lookup dictionaries — no dynamic generation for variable round counts.

#### Key Constraints for Multi-Round Support

1. Round N needs shared knowledge aggregated from the previous round (N-1)
2. Agents must track round history to avoid repeating findings across rounds
3. Round N must know which specific questions or gaps to address (targeted direction)
4. A convergence detection mechanism is needed — when do we stop adding rounds?
5. Budget and timeout must scale with the number of rounds to prevent runaway sessions
6. The dashboard pipeline bar must dynamically render N round segments instead of a fixed ROUND1/ROUND2 pair

### Prior Art / Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| Fixed N rounds (e.g., always 3) | Simple implementation | Wastes tokens when convergence is reached early; rigid |
| User-configurable round count | User control | Poor UX; users don't know how many rounds they need |
| Adaptive convergence-based (chosen) | Optimal depth, no wasted rounds | More complex convergence logic |
| LLM decides when to stop | Flexible | Unpredictable, model-dependent, costly |
| Tree-of-thought exploration | Very thorough | Exponential cost, hard to bound |

## Decision

### 1. FSM Change: Replace Hardcoded ROUND1/ROUND2 with Adaptive Loop

**Before:**
```
IDLE → CONFIGURING → ROUND1 → COLLABORATING → FOLLOWUP
  → REFINING → ROUND2 → COMPILING → OUTPUT → COMPLETE
```

**After:**
```
IDLE → CONFIGURING → ROUND(1) → COLLABORATING → FOLLOWUP → REFINING ─┐
                         ↓                                              │
                         └─── ROUND(N≥2) ←──────────────────────────────┘
                                                     ↓
                                           COMPILING → OUTPUT → COMPLETE
```

The first round (ROUND1) passes only `(topic,)` to agents. Subsequent rounds (ROUND2..N) pass `(topic, shared_knowledge)`. After each round (including the first), the collaboration/follow-up/refinement phases run to produce updated shared knowledge for the next round.

**Note that the first round has special behavior** — no shared knowledge is passed to agents (`shared=None`). All subsequent rounds receive the current `SharedKnowledge` object.

The FSM collapses `ROUND1` and `ROUND2` into an adaptive loop where `ROUND(1)` is the first iteration and `ROUND(N≥2)` represents the loop continuation. The `state` property dynamically reports `"ROUND1"`, `"ROUND2"`, `"ROUND3"`, etc. based on the current round index. The pipeline no longer distinguishes "first round" from "second round" at the state machine level — it is a uniform iterative loop.

### 2. Round Loop: While-Loop with Convergence Check

Replace the hardcoded sequence with a convergence-driven loop:

```python
round_num = 1
round_results: dict[int, dict[str, Findings]] = {}
round_history: list[SharedKnowledge] = []

while _should_continue(round_num, round_history, config):
    # For round 1, shared=None (no shared knowledge yet).
    # For rounds 2+, shared is the current SharedKnowledge.
    shared = round_history[-1] if round_history else None

    # Run all agents for this round
    results = await run_round(round_num, agents, topic,
                              shared=shared)
    round_results[round_num] = results

    # Publish findings to collaboration bus
    for agent_id, findings in results.items():
        await bus.publish_findings(round_num, agent_id, findings)

    # Compute shared knowledge for next iteration
    if _should_continue(round_num + 1, round_history, config):
        shared = await compute_shared_knowledge(round_num, round_results)
        round_history.append(shared)

    round_num += 1
```

Note that for round 1, `shared` is `None` because no collaboration has happened yet and there is no prior shared knowledge to distribute. For rounds 2+, `shared` is the current `SharedKnowledge` produced by the previous collaboration phase.

### 3. Stop Conditions

The `_should_continue()` function evaluates multiple stopping criteria in a strict priority order:

```python
async def _should_continue(
    self,
    round_num: int,
    round_history: list[SharedKnowledge],
    start_time: float,
) -> bool:
    # 1. Cancel event — user-initiated cancellation takes precedence
    if self._cancel_event and self._cancel_event.is_set():
        return False

    # 2. Time budget — reserve 10% for compilation; stop adding rounds
    if time.monotone() - start_time > self._get_session_timeout() * 0.9:
        return False

    # 3. Max rounds — hard safety cap prevents unbounded execution
    if round_num >= self.session_config.max_rounds:
        return False

    # 4. Convergence — gaps below threshold means knowledge is stable
    gaps = _compute_gap_delta(round_history)
    if gaps is not None and gaps >= 0:
        return False

    # 5. Diminishing returns — 2 consecutive non-decreasing gap deltas
    if _diminishing_returns(round_history):
        return False

    return True
```

| Condition | Threshold | Rationale |
|-----------|-----------|-----------|
| **Cancel event** | `cancel_event.is_set()` | User-initiated cancellation |
| **Time budget exceeded** | `elapsed_time > total_budget * 0.9` | Reserve 10% for compilation; stop adding rounds |
| **Max rounds** | `max_rounds` (default 5, configurable) | Hard safety cap prevents unbounded execution |
| **Gap convergence** | `total_gaps < 2 AND confidence >= 0.5` for 2 consecutive rounds | No significant disagreements or unknown areas remain |
| **Diminishing returns** | `Δgaps[last_round] - Δgaps[current_round] >= 0` | Gaps are not decreasing — additional rounds won't help |

**Order of evaluation:** Cancel → Time → Max Rounds → Convergence → Diminishing Returns. This ensures safety conditions are evaluated first, preventing runaway sessions even if convergence detection has a bug.

**Default max_rounds:** 5. Rationale: 3–4 rounds typically sufficient for convergence on complex topics; 5 provides headroom. Configurable via `SessionConfig.max_rounds` and the `--rounds` CLI flag.

The event previously named `round2_skip` is renamed to `round_skip` with the round number included:

```python
self._log_event("round_skip", round=round_num, gaps=_total_gaps)
```

This makes the event log unambiguous for N rounds rather than implying a binary R1/R2 choice.

### 4. Convergence Detection

Convergence is detected by tracking gaps over the last rounds, with a requirement of **2 consecutive non-decreasing rounds** before stopping:

```python
def _total_gaps(shared: SharedKnowledge) -> int:
    return len(shared.knowledge_gaps) + len(shared.areas_of_disagreement)


def _compute_gap_delta(
    round_history: list[SharedKnowledge],
) -> float:
    """Compute the change in total gaps between the last two rounds.
    Negative value means gaps are decreasing (progress).
    Positive or zero means stagnation — should stop.

    Requires 2 consecutive rounds of non-decreasing gaps to trigger.
    """
    if len(round_history) < 3:
        return -1.0  # Not enough data, continue

    d1 = _total_gaps(round_history[-2]) - _total_gaps(round_history[-1])
    d2 = _total_gaps(round_history[-3]) - _total_gaps(round_history[-2])
    return d1 if d1 <= 0 and d2 <= 0 else -1.0  # Only stop if 2 consecutive non-decreasing
```

**Diminishing returns stop:** If both `d1 <= 0` and `d2 <= 0` (two consecutive rounds where gaps did not decrease), stop. This catches:
- Agents repeating themselves across rounds
- Topics where all known perspectives have been exhausted
- Edge cases where gap count oscillates without improvement

The two-round requirement prevents false positives from temporary plateaus — gaps may appear to plateau for one round but then decrease in the subsequent round.

**Confidence convergence:** Track `mean_confidence` across agents. Stop when `mean_confidence >= 0.7` and `Δgaps < 0` (gaps decreasing but slowly) — indicates the system has reached a solid understanding.

**NOTE:** Convergence quality is bounded by the underlying gap extraction in `bus.py` (currently heuristic keyword matching). Improved gap extraction (e.g., LLM-based) would directly improve convergence accuracy.

### 5. Agent Interface: Add `research_round_n`

Add a new abstract method to `BaseAgent` alongside existing methods:

```python
@abstractmethod
async def research_round_n(
    self,
    topic: ResearchTopic,
    shared: SharedKnowledge,
    round_num: int,
    prev_findings: Findings,
    questions: FollowUpQuestions | None = None,  # PRESERVED for R3+
) -> Findings:
    """Research round for round N (N >= 3).

    Args:
        topic: Original research topic.
        shared: Shared knowledge from collaboration phase.
        round_num: Current round number (3, 4, 5...).
        prev_findings: Agent's own findings from the previous round,
            to avoid repetition.
        questions: Follow-up questions from the refinement phase.
            Preserved and passed to ALL rounds (not just R2).
    """
```

**Backward compatibility:** Keep `research_round_1` and `research_round_2` as-is. The new method is the primary dispatch target for R3+, while R1 and R2 continue to use their existing methods. This ensures no breaking changes for existing agent implementations.

**For rounds >= 3**, the registry dispatches to `research_round_n`. For rounds 1 and 2, it continues using `research_round_1` and `research_round_2`.

**Follow-up questions for R3+:** The `questions` parameter from the refinement phase is passed to ALL rounds. If refinement only ran once after R1, the questions are still embedded in `SharedKnowledge` and accessible via `shared.areas_of_disagreement` and `shared.knowledge_gaps`. This ensures that later rounds have at least indirect access to the refinement context even if the explicit `questions` parameter is `None`.

### 6. Registry Dispatch: Add New Route with Uniform Output Type

Add dispatch route for R3+ in `registry.py`. Unlike R2 which returns raw `Findings`, the R3+ route wraps its output through `write_report` to produce a uniform `IndividualReport` type:

```python
if (
    len(args) == 4
    and isinstance(args[0], ResearchTopic)
    and isinstance(args[1], SharedKnowledge)
    and isinstance(args[2], int)          # round_num
    and isinstance(args[3], Findings)     # prev_findings
):
    # Round N (3+) — deep iterative research.
    # Wrap through write_report for uniform IndividualReport output
    r_n = await agent.research_round_n(
        args[0], args[1], args[2], args[3]
    )
    return await agent.write_report(args[3], r_n)
```

The updated dispatch table:

| Arguments | Method Called | Round | Output Type |
|-----------|--------------|-------|-------------|
| `(ResearchTopic,)` | `research_round_1(topic)` | 1 | `Findings` |
| `(SharedKnowledge,)` | `review_findings(shared)` | Follow-up | `FollowUpQuestions` |
| `(ResearchTopic, SharedKnowledge)` | `research_round_2(topic, shared, questions)` | 2 | `Findings` |
| `(ResearchTopic, SharedKnowledge, int, Findings)` | `research_round_n(topic, shared, round_num, prev_findings)` → `write_report()` | 3+ | `IndividualReport` |
| `(Findings,)` | `write_report(r1, None)` | Compilation | `IndividualReport` |

This ensures type uniformity across rounds: R2 returns `Findings`, R3+ returns `IndividualReport` (through write_report wrapping). The `collect_reports` logic must handle both types, or R2 can also be wrapped for consistency in a future refactor.

### 7. SharedKnowledge Enhancements

Add round history tracking to `SharedKnowledge`:

```python
class SharedKnowledge(BaseModel):
    round_number: int
    all_summaries: dict[str, str]
    key_themes: list[str]
    areas_of_agreement: list[str]
    areas_of_disagreement: list[str]
    knowledge_gaps: list[str]
    round_history: list[SharedKnowledge] = []  # NEW
```

The `round_history` field accumulates `SharedKnowledge` snapshots from each collaboration phase, enabling:
- Convergence detection (compare gaps across rounds)
- Dashboard timeline visualization (show how gaps evolved)
- Agent prompt context ("In Round 2, the key gaps were X. In Round 3, they narrowed to Y.")

The first round's `SharedKnowledge` has an empty `round_history`. Each subsequent round appends the previous round's `SharedKnowledge` before computing the current one. The field is excluded from serialization to avoid unbounded growth in file output.

### 8. Dashboard Changes

#### Pipeline State Generation

Replace the hardcoded `STATE_ORDER` array with a dynamically generated pipeline:

```javascript
// constants.js
function generateStateOrder(roundCount) {
  const base = ['IDLE','CONFIGURING'];
  for (let i = 1; i <= roundCount; i++) {
    base.push(`ROUND${i}`);
  }
  base.push('COLLABORATING','FOLLOWUP','REFINING','COMPILING','OUTPUT','COMPLETE');
  return base;
}
```

Or more precisely, since collaboration/followup/refining happen between rounds, the actual state flow needs careful ordering. The dashboard receives the actual round count from the session via the `pipeline_summary` event or a new `session_config` event. Then:

```javascript
function buildPipelineStates(actualRounds) {
  const states = ['IDLE', 'CONFIGURING'];
  for (let i = 1; i <= actualRounds; i++) {
    states.push(`ROUND${i}`);
    if (i < actualRounds) {
      states.push('COLLABORATING', 'FOLLOWUP', 'REFINING');
    }
  }
  states.push('COMPILING', 'OUTPUT', 'COMPLETE');
  return states;
}
```

**Color assignment:** Round N states cycle through a color palette. For ROUND1–ROUND5, assign colors from a gradient:

| State | Color |
|-------|-------|
| ROUND1 | `#58a6ff` (blue — unchanged) |
| ROUND2 | `#39d2c0` (teal — unchanged) |
| ROUND3 | `#f0883e` (orange) |
| ROUND4 | `#bc8cff` (violet) |
| ROUND5 | `#f778ba` (pink) |

**Label generation:** `ROUND{N}` → `"Round {N}"` (e.g., `ROUND3` → `"Round 3"`).

#### HTML Changes

The current `dashboard.html` has hardcoded `<span class="phase-step">` elements for `ROUND1` and `ROUND2`. These must be replaced with a dynamic container:

```html
<div class="phase-indicator" id="phaseIndicator"></div>
```

The `buildPipelineStates()` JS function creates all phase-step elements at session init. It generates `ROUND1` through `ROUND{N}` dynamically based on `max_rounds` from the session config. The `session-detail.js` module must also be updated to call `buildPipelineStates(session.max_rounds)` on session load and re-render the phase indicator whenever the session state changes.

Additionally, `state.js` must expose `max_rounds` from the session state so that the dashboard can pass it to `buildPipelineStates()`:

```javascript
// state.js — expose max_rounds from session
export function getSessionConfig() {
  return {
    max_rounds: state.sessionConfig?.max_rounds ?? 5,
    // ... other config fields
  };
}
```

#### Constants Updates

Add entries for ROUND3, ROUND4, and ROUND5 to all lookup dictionaries:

```javascript
export const STATE_LABELS = {
  ROUND1: 'Round 1', ROUND2: 'Round 2',
  ROUND3: 'Round 3', ROUND4: 'Round 4', ROUND5: 'Round 5',
  IDLE: 'Idle', CONFIGURING: 'Configuring',
  COLLABORATING: 'Collaborating', FOLLOWUP: 'Follow-Up',
  REFINING: 'Refining', COMPILING: 'Compiling',
  OUTPUT: 'Output', COMPLETE: 'Complete',
};

export const STATE_BADGE_CLASSES = {
  ROUND1: 'badge-round1', ROUND2: 'badge-round2',
  ROUND3: 'badge-round3', ROUND4: 'badge-round4', ROUND5: 'badge-round5',
  IDLE: 'badge-idle', CONFIGURING: 'badge-configuring',
  COLLABORATING: 'badge-collaborating', FOLLOWUP: 'badge-followup',
  REFINING: 'badge-refining', COMPILING: 'badge-compiling',
  OUTPUT: 'badge-output', COMPLETE: 'badge-complete',
};

export const STATE_COLORS = {
  ROUND1: '#58a6ff', ROUND2: '#39d2c0',
  ROUND3: '#f0883e', ROUND4: '#bc8cff', ROUND5: '#f778ba',
  IDLE: '#8b949e', CONFIGURING: '#d2a8ff',
  COLLABORATING: '#79c0ff', FOLLOWUP: '#ffa657',
  REFINING: '#ff7b72', COMPILING: '#3fb950',
  OUTPUT: '#7ee787', COMPLETE: '#56d364',
};
```

These entries ensure the dashboard renders correctly for sessions that use 3, 4, or 5 rounds. The color palette for rounds extends from blue through teal, orange, violet, and pink to provide visual distinction across 5 possible round states.

### 9. Budget Scaling & Timeouts

#### Per-Round Timeout Formula

Per-round timeout must scale with the number of rounds to prevent budget exhaustion:

```python
def _get_round_timeout(
    total_budget: int,
    max_rounds: int,
) -> int:
    """Compute per-round timeout.

    Formula: total_budget / (max_rounds + 2)
    The +2 reserves budget for:
    - Collaboration + follow-up + refinement (1 slot)
    - Compilation + output (1 slot)
    """
    return max(30, total_budget // (max_rounds + 2))
```

| Config | Max Rounds | Total Budget | Per-Round Timeout |
|--------|-----------|-------------|-------------------|
| quick | 3 | 300s | 60s |
| medium | 4 | 300s | 50s |
| deep | 5 | 480s | ~68s |
| custom (10 min) | 5 | 600s | ~85s |

The `max(30, ...)` floor prevents unrealistically short timeouts. If the computed timeout drops below 30s, `max_rounds` must be reduced or the total budget must be increased.

**Session-level timeout** remains `budget_seconds * 4 + 300` (from ADR-0001), capped at 1800s (30 minutes). This generous multiplier accounts for the overhead of multiple rounds.

#### max_rounds by Budget Mapping

The `max_rounds` value is derived from the user's time budget keyword at configuration time:

```python
# Orchestrator class attribute
_MAX_ROUNDS_BY_BUDGET: dict[str, int] = {
    "quick": 3,
    "medium": 4,
    "deep": 5,
    "custom": 4,
}

# In configure():
max_rounds = self._MAX_ROUNDS_BY_BUDGET.get(
    config.topic.time_budget, 4
)
config.max_rounds = max_rounds
```

This mapping ensures that users get an appropriate number of rounds based on their intent:
- **quick** (3 rounds): Speed priority; surface-level exploration that converges rapidly
- **medium** (4 rounds): Balanced; allows one extra round beyond the current 2-round cap
- **deep** (5 rounds): Maximum depth; thorough multi-perspective analysis for complex topics
- **custom** (4 rounds): Sensible default for user-specified budgets

The `--rounds N` CLI flag overrides this mapping, giving power users direct control over the round count regardless of the budget keyword.

#### _get_round_timeout Scope

The `_get_round_timeout()` function only replaces the timeout used in `run_round()`. It does NOT affect other timed operations:

```python
# Per-agent round timeout — only replaces the timeout used in run_round()
# Does NOT affect collect_followup_questions() or _refine_agent(),
# which keep their existing max(30, _get_timeout() // 2) calculation.
def _get_round_timeout(self) -> int:
    b = self.session_config.time_budget_seconds
    m = self.session_config.max_rounds
    return max(60, b // (m + 2))
```

The `collect_followup_questions()` and `_refine_agent()` methods keep their existing timeout of `max(30, _get_timeout() // 2)`. This is intentional — follow-up and refinement have fixed complexity regardless of round count, while research rounds expand to fill available budget.

### 10. Agent Prompt Adaptation

For R3+, the agent's system prompt is augmented with round-specific context:

```
This is Round {N} of {max_rounds}.

Your previous findings (Round {N-1}): {prev_findings.summary}

Knowledge gaps identified so far: {shared.knowledge_gaps}

IMPORTANT: Only provide NEW insights not covered in previous rounds.
Do NOT repeat your earlier findings. Focus specifically on:
1. The knowledge gaps listed above
2. Areas where your earlier analysis was incomplete
3. New perspectives that emerge from cross-agent collaboration

Round {N-1} key points (yours): {prev_findings.key_points}
```

This prompt structure is rendered in `prompts.py` via a new function:

```python
def build_round_n_prompt(
    topic: ResearchTopic,
    shared: SharedKnowledge,
    round_num: int,
    max_rounds: int,
    prev_findings: Findings,
) -> str:
```

### 11. collect_reports Update

The `collect_reports()` method must handle variable-length round results. The current method only passes `round_1` and `round_2` to `write_report()`. For N rounds, the most recent complete round's findings should be passed as `round_2` (or the equivalent):

```python
async def collect_reports(self, agents, round_results: dict[int, dict]):
    # round_results: {1: {agent_id: Findings}, 2: {agent_id: Findings}, ...}
    latest_round = max(round_results.keys())
    for agent_id, agent_fn in agents.items():
        r1 = round_results.get(1, {}).get(agent_id)
        r_latest = round_results.get(latest_round, {}).get(agent_id)
        # r_latest may be the same as r1 if agent only participated in R1
        report = await agent_fn(r_latest or r1, r_latest)
```

## Consequences

### Positive

1. **Deep research capability** — complex topics benefit from 3–5+ rounds of iterative investigation, with agents progressively narrowing knowledge gaps
2. **Adaptive depth** — the system automatically stops when convergence is reached, avoiding wasted rounds on simple topics
3. **Backward compatible** — existing R1 and R2 methods remain unchanged; R3+ uses a new method with a superset interface
4. **Dynamic dashboard** — pipeline bar adapts to the actual number of rounds, providing accurate visual progress
5. **Diminishing returns detection** — prevents agents from spinning their wheels when no new insights emerge
6. **Configurable depth** — `max_rounds` in `SessionConfig` lets users control the depth vs. speed trade-off
7. **Rounding history** — `SharedKnowledge.round_history` enables better convergence analytics and richer agent prompts

### Negative

1. **Increased total session time** — deep research with 5 rounds can take 2–3× longer than the current 2-round setup
2. **Longer agent prompts** — each round accumulates context from previous rounds, increasing token consumption per agent call
3. **More complex convergence logic** — the `_should_continue()` function must evaluate multiple conditions in priority order
4. **Dashboard complexity** — dynamic pipeline rendering is more complex than the current hardcoded `STATE_ORDER` array
5. **Agent prompt tuning needed** — the "don't repeat yourself" instruction in Round N prompts must be carefully tuned to avoid overly terse or dismissive agent responses
6. **`collect_reports` complexity** — must handle variable-length round results instead of the current fixed R1/R2 contract

### Risks

1. **Agent repetition** — agents may produce redundant findings in later rounds even with anti-repetition prompting. Mitigation: diminishing returns detection stops the loop when gaps stop decreasing
2. **Context window overflow** — accumulated round context may exceed small model context windows (e.g., 8K token models). Mitigation: per-round prompt summarization, hard `max_rounds` cap of 5
3. **Increased LLM costs** — 5 rounds × 6 agents = 30 LLM calls vs. the current 12 (or 6 if R2 is skipped). Mitigation: convergence-based early stopping prevents unnecessary rounds
4. **User expectation mismatch** — users expecting quick results may be surprised by 5+ minute sessions. Mitigation: dashboard shows `Round 3/5` progress indicator, and the `time_budget` keyword ("quick" vs "deep") maps to different `max_rounds` defaults
5. **Diminishing returns false positives** — gaps may appear to plateau temporarily but then decrease in a later round. Mitigation: require 2 consecutive rounds of non-decreasing gaps before stopping

### Default max_rounds by Budget

| Budget Keyword | Default max_rounds | Rationale |
|---------------|-------------------|-----------|
| `quick` | 3 | Speed priority; 3 rounds sufficient for surface-level exploration |
| `medium` | 4 | Balanced; allows one extra round beyond current 2 |
| `deep` | 5 | Maximum depth; 5 rounds for thorough multi-perspective analysis |
| `custom` | 4 | Sensible default for user-specified budgets |

## ADR References

- **ADR-0001** (Multi-Agent Research Architecture) — original FSM, `run_round()`, timeouts, and pipeline design
- **ADR-0004** (Test Findings and Architecture Fixes) — Round 2 skip logic fix and gap threshold decision
- **ADR-0007** (Clarification Protocol and Refinement Phase) — refinement phase that runs between rounds; this ADR extends the refinement pattern to N rounds
- **ADR-0008** (Dashboard Enhancements) — dashboard rendering architecture; this ADR replaces hardcoded `STATE_ORDER` with dynamic pipeline generation

---

## Implementation Plan

| Step | Description | Files Changed |
|------|-------------|---------------|
| 1 | Add `research_round_n` abstract method to `BaseAgent` | `base_agent.py` |
| 2 | Add `research_round_n` method to `ResearchAgent` (with round-specific prompt) | `research_agent.py` |
| 3 | Add `research_round_n` stub to `ScribeAgent` (raises `NotImplementedError` — scribe doesn't do rounds) | `scribe_agent.py` |
| 4 | Add R3+ dispatch route in `AgentRegistry` (with `write_report` wrapping for uniform output) | `registry.py` |
| 5 | Add `round_history` field to `SharedKnowledge` model | `models.py` |
| 6 | Update `collaboration/bus.py` to support round N — accept `round_num`, populate `round_history`, aggregate findings from correct round | `bus.py` |
| 7 | Implement `_should_continue()` convergence detection in orchestrator | `orchestrator.py` |
| 8 | Refactor `_run_session()` to use while-loop instead of hardcoded R1→R2 | `orchestrator.py` |
| 9 | Add `build_round_n_prompt()` to prompts module | `prompts.py` |
| 10 | Update `_get_round_timeout()` with budget scaling formula and scope isolation | `orchestrator.py` |
| 11 | Update `collect_reports()` for variable-length round results | `orchestrator.py` |
| 12 | Replace hardcoded `STATE_ORDER` with dynamic pipeline generation in dashboard | `constants.js`, `session-detail.js` |
| 13 | Replace hardcoded `<span class="phase-step">` elements with dynamic `phaseIndicator` container in dashboard HTML | `dashboard.html` |
| 14 | Expose `max_rounds` from session state in `state.js` | `state.js` |
| 15 | Add `max_rounds` field to `SessionConfig` and budget mappings (`_MAX_ROUNDS_BY_BUDGET`) | `models.py`, `orchestrator.py` |
| 16 | Update CLI argument parser in `__main__.py` to accept `--rounds N` flag. Pass to orchestrator as `max_rounds` | `__main__.py` |
| 17 | Update `dry_run.py` to display both `max_rounds` and projected round timeline | `dry_run.py` |
| 18 | Update tests for multi-round scenarios | `test_orchestrator.py`, `test_agents.py` |

## Open Questions

1. Should agents receive ALL previous round findings or just the most recent one? Decision: Most recent only. Full history is too verbose for prompts; convergence detection uses the structured `round_history` field instead.
2. Should the `round_history` in `SharedKnowledge` be persisted to disk? Decision: No — it is memory-only for convergence analysis. Dashboard gets convergence data via events.
3. Should the refinement phase run between every round or only once after R1? Decision: Only once after R1 (current behavior). Additional refinement between later rounds is Phase 2 scope.
4. Should `max_rounds` be user-configurable via CLI flag (`--max-rounds`)? Decision: Yes — added to `SessionConfig` with budget-appropriate defaults. CLI flag added as `--rounds N`.
5. Should R2 also use the `write_report` wrapping pattern for uniform output with R3+? Decision: Not yet — keeping R2's raw `Findings` output for backward compatibility. A future refactor can standardize all rounds.

## Changelog

| Date | Change |
|------|--------|
| 2026-06-16 | v1.1 — Applied review findings: FSM diagram fix (C1), dashboard HTML/constants (C2, I4), registry dispatch type uniformity (C3), 2-round diminishing returns (I1), follow-up questions for R3+ (I2), CollaborationBus plan step (I3), max_rounds mapping & timeout scope (I5, I6), gap extraction note (m1), first-round special behavior note (m2), explicit _should_continue ordering (m3), round_skip event rename (m4), CLI --rounds flag (m5), dry_run.py update (m6) |
| 2026-06-16 | Initial version — ADR-0010 proposed |
