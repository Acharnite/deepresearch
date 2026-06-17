---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0008: Dashboard Enhancements

## Status

Proposed

**Version:** 1.0
**Last Updated:** 2026-06-15

## Context

The original DeepeResearch dashboard (ADR-0003) provided basic session management and SSE streaming. Users needed richer real-time visibility into agent progress, Q&A interactions between agents, pipeline state transitions, and scribe compilation output. The dashboard needed to evolve from a minimal monitoring tool into a comprehensive research cockpit.

### Key Forces
1. Users need to see what each agent is doing in real-time (not just state badges)
2. Q&A interactions between agents should be visualized, not just logged
3. Pipeline state transitions need prominent visibility
4. Scribe output during long compilations should be visible
5. The dashboard must handle session reconnection gracefully
6. Layout must accommodate 6 agent panels plus sidebar without excessive scrolling
7. A static demo page is needed for layout testing without running a full session

### Prior Art / Alternatives Considered
| Approach | Pros | Cons |
|----------|------|------|
| Progress bars | Familiar UX | Misleading for async parallel work |
| State badges (chosen) | Accurate, lightweight | Less visual detail |
| Text-only event log | Simple | Overwhelming volume, hard to scan |
| Visual Q&A graph (chosen) | Shows agent relationships | Complex SVG rendering |
| Separate pages per agent | Clean separation | Navigation overhead, no overview |

## Decision

### Pipeline Visualization Bar

A full-width horizontal bar at the top of the dashboard shows all pipeline states:

- **States:** IDLE → CONFIGURING → ROUND1 → COLLABORATING → FOLLOWUP → REFINING → ROUND2 → COMPILING → OUTPUT → COMPLETE
- **Current phase glows** — CSS animation highlights the active state
- **Completed states** show green checkmarks
- **Future states** are grayed out
- Fixed at the top for constant visibility regardless of scroll position

### Agent State Badges

Color-coded badges indicate each agent's current state:

| State | Color | Animation | Description |
|-------|-------|-----------|-------------|
| researching | Blue (#58a6ff) | Spinning indicator | Agent is actively generating |
| searching | Orange (#f0883e) | Spinning indicator | Agent is executing web search |
| writing | Pink (#f778ba) | Spinning indicator | Agent is writing report |
| done | Green (#3fb950) | None | Agent completed successfully |
| failed | Red (#f85149) | None | Agent failed |
| waiting | Gray (#8b949e) | None | Agent is idle (during scribe compilation) |

- Active states (researching, searching, writing) show a spinning indicator animation
- State transitions are driven by SSE events: `agent_start`, `agent_output` (with `agent_state`), `agent_complete`, `agent_failed`
- During scribe compilation, agents show "waiting"; during refinement, agents show "researching"; agents show "done" only on session complete

### Collapsible Agent Logs

Each agent panel has a collapsible log section:

- **Toggle:** ▾ (expanded) / ▴ (collapsed) indicator
- **Default state:** Agents start with logs minimized to reduce visual clutter
- **Entire header row clickable** — clicking anywhere on the agent header toggles collapse
- **Independent state** — each agent's collapse state is independent

### Colored Agent Borders

Each agent has a unique color for its left border on output panels:

- Matches the agent's color from the Q&A graph palette
- Provides visual differentiation when scanning multiple agent panels
- Colors: curious-teen (#58a6ff), skeptical-academic (#3fb950), creative-artist (#bc8cff), pragmatic-engineer (#39d2c0), philosophical-thinker (#f0883e), data-analyst (#f778ba)

### Scribe Card

A dedicated scribe output panel:

- **Position:** Above the agent progress section, below the pipeline bar
- **Max height:** 250px with overflow scroll
- **Content:** Real-time scribe compilation text as it streams
- **Visibility:** Shows during COMPILING state, hidden otherwise
- **Streaming:** Text accumulates as scribe LLM generates output

### Q&A Panel

Shows agent questions and answers:

- **Questions:** Displayed with the asking agent's name and color
- **Answers:** Displayed below questions with the answering agent's name
- **Text wrapping:** Enabled for long questions/answers
- **Position:** Sidebar area, below pipeline state

### Visual Q&A Graph

An SVG-based circular layout showing agent interactions:

- **Layout:** Agents arranged in a circle around the center
- **Nodes:** Circles with agent emoji and truncated name (12 chars max)
- **Arrows:**
  - Blue arrows (#58a6ff) for questions
  - Orange arrows (#f0883e) for clarifications
  - Opacity increases for more recent interactions (0.4 → 1.0)
- **Latest arrow glow:** The most recent interaction has a Gaussian blur glow filter
- **Arrow offset:** Reverse arrows are offset by 3px to prevent overlap
- **History:** Last 5 interactions displayed, last 10 stored in memory
- **Interaction tracking:** `addQAInteraction(from, to, type, question)` function

### Event Log

Timestamped event display:

- **Format:** `[HH:MM:SS] event_type — details`
- **Scrollable:** Fixed height with overflow scroll
- **Auto-scroll:** Scrolls to bottom on new events
- **Color-coded:** Different colors for different event types

### Session Reconnection

When a dashboard reconnects to an active session:

1. **Fetch state** — GET `/api/sessions/{id}` retrieves current session state
2. **Fetch event history** — SSE connection replays buffered events (up to 500)
3. **Restore agent progress** — replayed events restore agent states, output text, and Q&A
4. **Restore pipeline state** — pipeline bar updates to current phase
5. **Live streaming resumes** — after replay, live events stream normally

### State Transitions

Agent state badges follow precise transition rules:

```
IDLE → [agent_start] → researching
researching → [agent_output + agent_state="searching"] → searching
searching → [agent_output + agent_state="researching"] → researching
researching → [agent_output + agent_state="writing"] → writing
writing/ researching → [agent_complete] → done
any → [agent_failed] → failed
[scribe compilation starts] → waiting
[refinement starts] → researching
[session complete] → done (final)
```

### 16:9 Layout

The dashboard uses a wider layout optimized for widescreen displays:

- **Container width:** 1600px (max)
- **Grid:** 2-column layout — agents (left, ~65%) + sidebar (right, ~35%)
- **Agent panels:** Stacked vertically in left column
- **Sidebar:** Pipeline bar + scribe card + Q&A panel + event log
- **Responsive:** Falls back to single-column on narrower screens

### Demo Page

A static `demo.html` for layout testing:

- **Purpose:** Test dashboard layout without running a full research session
- **Mock data:** Pre-populated agent states, Q&A interactions, pipeline states
- **No server required:** Opens as a local file
- **Same CSS/JS:** Uses the same styles and scripts as the live dashboard

## Consequences

### Positive
1. **Better UX** — real-time visibility into every aspect of the research pipeline
2. **Agent relationships visible** — Q&A graph shows which agents are interacting
3. **Pipeline clarity** — prominent state bar prevents confusion about current phase
4. **Scribe transparency** — users see compilation text as it generates (2-5 minutes)
5. **Session resilience** — reconnection restores full state, no lost progress
6. **Layout optimization** — 16:9 layout uses widescreen space effectively
7. **Demo capability** — layout testing without server overhead

### Negative
1. **Complex rendering logic** — state machine for agent badges, SVG graph rendering
2. **SSE event volume** — streaming output generates many events per second
3. **Memory usage** — event history buffer (500 events) per session
4. **SVG graph complexity** — circular layout, arrow offset, glow filters
5. **Demo maintenance** — mock data must stay synchronized with real event formats

### Neutral
1. Dashboard is a single HTML file (vanilla JS + CSS, no build step)
2. Q&A graph shows last 5 interactions — older interactions fade out
3. Pipeline bar is CSS-only (no JavaScript state machine needed)
4. Collapsible agent logs use CSS transitions for smooth animation
5. Demo page is static — no live updates, requires manual refresh

## Related Issues
- #52 (Q&A Graph — Interactive visualization): Extends the dashboard with a real-time SVG graph showing agent-scribe communication. Uses the existing qa-graph.js module.

## ADR References
- **ADR-0001** (Multi-Agent Research Architecture) — pipeline states and agent lifecycle
- **ADR-0003** (Web Frontend & Multi-Session Architecture) — base dashboard architecture
- **ADR-0007** (Clarification Protocol and Refinement Phase) — Q&A visualization

---

## Implementation Status (Updated 2026-06-15)

| Decision | Status | Notes |
|----------|--------|-------|
| Pipeline visualization bar | ✅ Implemented | Full-width, glow animation |
| Agent state badges | ✅ Implemented | 6 states, color-coded, spinning |
| Collapsible agent logs | ✅ Implemented | ▾/▴ toggle, header click |
| Colored agent borders | ✅ Implemented | Per-agent unique colors |
| Scribe card | ✅ Implemented | 250px max-height, streaming |
| Q&A panel | ✅ Implemented | Questions + answers, text wrap |
| Visual Q&A graph | ✅ Implemented | SVG circular, blue/orange arrows, glow |
| Event log | ✅ Implemented | Timestamped, scrollable |
| Session reconnection | ✅ Implemented | State + event history fetch |
| 16:9 layout | ✅ Implemented | 1600px, 2-column grid |
| Demo page | ✅ Implemented | Static demo.html with mock data |
