# ADR-0014: Enforce Time Budget and Correct UI Labels

## Status

**Proposed**

## Version

1.1

## Last Updated

2026-06-17

## Context

The time budget system in DeepeResearch is broken in three ways:

1. **UI labels are wrong**: Quick says "(2 min)" but is 300s (5 min). Medium says "(5 min)" but is also 300s (5 min) — same as quick!
2. **Budget is not enforced**: The budget × 4 + 300 multiplier means the actual session timeout is 25 minutes for a "5 min" budget. Users report sessions taking 11+ minutes.
3. **Misleading user expectations**: Users expect "quick" to be fast, but it takes 10+ minutes.

### Current broken values

```python
TIME_BUDGET_SECONDS = {"quick": 300, "medium": 300, "deep": 480}
_MAX_ROUNDS_BY_BUDGET = {"quick": 3, "medium": 4, "deep": 5}
# Session timeout: budget * 4 + 300 → 1500s (25 min) for quick, 1500s (25 min) for medium, 2220s (37 min) for deep
```

### Root cause analysis

- Quick and Medium have IDENTICAL time budgets (300s) — no difference
- The `* 4 + 300` multiplier in `_should_continue()` makes the actual timeout 4× the stated budget
- SearXNG search delays (3-8s per search) + LLM inference time + clarification rounds easily exceed 5 minutes
- 6 agents × 3+ rounds × multiple searches = minimum ~6 minutes even in best case

## Decision

### 1. Correct time budgets to match reality

Given SearXNG search delays + LLM inference, realistic minimum times:

| Budget | Seconds | Rounds | Actual Expected | UI Label |
|--------|---------|--------|-----------------|----------|
| quick | 240 | 2 | 3-4 min | "Quick (~3 min)" |
| medium | 420 | 3 | 5-7 min | "Medium (~6 min)" |
| deep | 660 | 5 | 8-12 min | "Deep (~10 min)" |

### 2. Enforce hard time cap

Change `_should_continue()` to use the actual budget directly:

```python
# Time budget — HARD cap
if self.session_config is not None:
    if time.monotonic() - start_time > self.session_config.time_budget_seconds:
        logger.info("Time budget exceeded (%ds) — stopping rounds",
                    self.session_config.time_budget_seconds)
        return False
```

Remove the `* 4 + 300` multiplier. The budget IS the hard cap.

### 3. Unified per-agent timeout

There are currently **two** timeout methods: `_get_timeout()` (used for R0) and `_get_round_timeout()` (used for R1+). Replace both with a single `_get_round_timeout()` method:

```python
def _get_round_timeout(self) -> int:
    """Per-round timeout based on session budget and rounds."""
    b = self.session_config.time_budget_seconds
    m = self.session_config.max_rounds
    # Explicit scribe budget: max(60, 25% of budget) for report compilation
    scribe_budget = max(60, int(b * 0.25))
    usable = b - scribe_budget
    return max(60, int(usable / m))
```

For quick (240s, 2 rounds): scribe = max(60, 60) = 60s, agent timeout = max(60, (240-60)/2) = 90s
For medium (420s, 3 rounds): scribe = max(60, 105) = 105s, agent timeout = max(60, (420-105)/3) = 105s
For deep (660s, 5 rounds): scribe = max(60, 165) = 165s, agent timeout = max(60, (660-165)/5) = 99s

### 4. Update UI labels

In `dashboard.html`:

```html
<label><input type="radio" name="time_budget" value="quick" /> <span>⚡ Quick <span class="radio-desc">(~3 min)</span></span></label>
<label><input type="radio" name="time_budget" value="medium" checked /> <span>🔋 Medium <span class="radio-desc">(~6 min)</span></span></label>
<label><input type="radio" name="time_budget" value="deep" /> <span>🔬 Deep <span class="radio-desc">(~10 min)</span></span></label>
```

### 5. Update TIME_BUDGET_OPTIONS in orchestrator

```python
TIME_BUDGET_OPTIONS = {
    "quick": "Quick (~3 min — fastest results)",
    "medium": "Standard (~6 min — balanced)",
    "deep": "Deep (~10 min — most thorough)",
}
```

### 6. Progress indicator

Deferred to separate ADR — requires UX design for progress bar component.

### 7. Custom budget handling

Custom budgets via `--minutes N` also use the hard cap directly (no multiplier). `MAX_SESSION_DURATION = 1800s` (30 min) still applies as an absolute ceiling. If a user passes `--minutes 60`, the session still stops at 1800s.

### 8. Test updates

Existing tests use hardcoded budget values that will need updating:
- `time_budget_seconds=300` (quick/medium) → change to 240 (quick) or 420 (medium)
- `time_budget_seconds=480` (deep) → change to 660
- Tests asserting `_should_continue()` behavior with the old multiplier must be rewritten to expect hard-cap behavior

## Consequences

### Positive

- Users get accurate time expectations
- Quick sessions actually finish quickly
- Research stops at budget limit, no runaway sessions
- Consistent experience between UI labels and actual behavior

### Negative

- Quick sessions with only 2 rounds may produce thinner research
- Users who want thorough research must pick medium or deep
- Existing session data may have mismatched budget labels
- Existing tests require updating to match new budget values and hard-cap behavior

## Deliverables

- ADR document: `docs/adr/ADR-0014-enforce-time-budget-and-correct-labels.md`
