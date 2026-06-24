# ADR-0019: Frontend Reactivity Strategy

## Status

Proposed

**Version:** 1.0
**Last Updated:** 2026-06-24

## Context

### Problem

The deepresearch web dashboard is a single-page application built with ~4,200 lines of vanilla JavaScript (ES modules), ~524 lines of HTML, and ~692 lines of CSS. It is served directly by FastAPI as static files — there is no Node.js build pipeline, no bundler, and no transpilation step.

The frontend provides:
- Session list with real-time status updates via SSE
- Session detail view with agent progress, Q&A graph, and event log
- Settings view with API key management, model selection, backend configuration
- System log view with auto-refresh
- Model picker with provider-grouped searchable dropdown

### Current Pain Points

1. **Manual DOM manipulation** — Every state change requires `document.getElementById()`, `classList.add/remove()`, and `innerHTML` assignment. The session list rebuilds its entire DOM every 3 seconds via `innerHTML`.

2. **Window globals for cross-module communication** — Modules export functions onto `window` (e.g., `window.showSessions = function()...`) instead of using imports or an event bus.

3. **No reactive state** — When data changes (e.g., a session completes), every DOM element that depends on that data must be manually updated. This is error-prone and causes visual "jank" — stale UI until the next poll cycle.

4. **String-based HTML generation** — UI is built from template literals, making it hard to read, debug, or test.

5. **Tight coupling between state and view** — Each view file (settings.js, session-list.js, session-detail.js) manages both data fetching and DOM updates, making it difficult to change one without breaking the other.

### Constraints

- **No build step** — The team is Python-focused. Adding webpack/vite/node_modules is undesirable.
- **Incremental migration** — The frontend works today. A full rewrite is not justified.
- **Small team** — Long-term maintainability matters more than theoretical purity.
- **FastAPI integration** — The backend serves HTML templates and static files. The frontend must continue to work with this architecture.

### Decision Drivers

| Driver | Weight | Notes |
|--------|--------|-------|
| Reactivity (eliminating manual DOM updates) | High | Primary motivation |
| Build complexity | High | No build step preferred |
| Migration effort | Medium | Incremental preferred over rewrite |
| Learning curve | Medium | Python team, not frontend specialists |
| Long-term maintenance | High | Small team, need sustainable patterns |
| Bundle size / performance | Low | Dashboard is not performance-critical |
| Community / ecosystem | Low | Nice-to-have, not decisive |

## Considered Options

### Option A: Alpine.js (~15KB via CDN)

**What it is:** A lightweight reactive framework that adds declarative HTML attributes (`x-data`, `x-show`, `x-for`, `x-on`) to existing HTML. No build step required — include via `<script>` tag.

**Pros:**
- **Directly solves the reactivity problem** — Proxy-based fine-grained DOM patching eliminates `innerHTML` rebuilds
- **Smallest jump from current code** — Add attributes to existing HTML, keep the same structure
- **No build step** — CDN include, same as current static file serving
- **Incremental adoption** — Migrate one view at a time (session list first, then settings, etc.)
- **~15KB gzipped** — Negligible performance impact
- **Declarative HTML** — Easy to read, easy to understand what a view does
- **Active community** — 28K+ GitHub stars, well-documented
- **Alpine.store()** — Built-in shared state, replaces window globals

**Cons:**
- **New dependency** — Another thing to track (though trivially removable)
- **Paradigm shift** — Declarative (Alpine) vs imperative (current vanilla) during migration
- **Two systems during migration** — Some views Alpine, some vanilla until migration completes
- **Debugging** — "Framework magic" can obscure what's happening (mitigated by Alpine devtools)
- **Less control** — Must work within Alpine's reactivity model

### Option B: Vanilla Cleanup (custom reactive layer)

**What it is:** Extract a mini reactive state manager (~150-300 lines), replace window globals with an event bus, standardize template literals, and add a simple component pattern.

**Pros:**
- **Zero dependencies** — Stays exactly where we are
- **Full control** — Build exactly what we need, nothing more
- **No new paradigm** — Same imperative style, just better organized
- **Consistent codebase** — Everything is vanilla JS
- **No migration period** — Refactor in place

**Cons:**
- **Must build reactivity from scratch** — A custom reactive layer is ~150-300 lines that inevitably underperforms Alpine's Proxy-based approach
- **Still manual DOM updates** — Even with helpers, you're telling the DOM what to do rather than declaring what it should look like
- **More long-term maintenance** — Custom code that nobody else knows
- **No ecosystem** — No devtools, no community patterns, no Stack Overflow answers
- **Harder onboarding** — New contributors must learn custom patterns instead of a well-known framework
- **The "clever" trap** — Per ADR-0049 (The Ladder), writing a custom reactive framework is the "clever" path. Using a mature 15KB library is the "boring" path. Boring wins.

## Decision

**Adopt Alpine.js (Option A).**

### Rationale

The jank is fundamentally about full DOM rebuilds every 3 seconds via `innerHTML`. Alpine's Proxy-based reactivity does fine-grained DOM patching — only the elements that change get updated. This eliminates the rebuild cycle entirely.

A custom vanilla reactive layer would be 150-300 lines that:
1. Still requires manual DOM binding (no declarative attributes)
2. Doesn't match Alpine's performance characteristics
3. Becomes a maintenance burden with zero community support
4. Violates ADR-0049's principle: use existing solutions over building your own

Alpine.js is the boring, pragmatic choice. It's 15KB, has no build step, and can be added incrementally. If it doesn't work out, removing it is trivial — it's just `<script>` tags and HTML attributes.

### Comparison Matrix

| Dimension | Alpine.js | Vanilla Cleanup |
|-----------|-----------|----------------|
| Bundle size | 15KB gzipped | 0KB |
| Reactivity | Built-in (Proxy-based) | Custom (~150-300 lines) |
| Build step | No (CDN) | No |
| Migration effort | Medium (incremental, view-by-view) | Low (refactor in place) |
| Learning curve | Low (HTML attributes) | None |
| Long-term maintenance | Low (framework handles it) | Medium (custom code to maintain) |
| Debugging | Good (Alpine devtools) | Excellent (no abstraction layer) |
| Community | 28K+ stars, active | N/A |
| New contributor onboarding | Easy (well-known framework) | Harder (must learn custom patterns) |
| Risk of removal | Low (trivially removable) | Low (already have it) |
| DOM update performance | Excellent (fine-grained patching) | Good (depends on implementation) |
| Cross-view state sharing | Alpine.store() built-in | Custom event bus needed |

## Migration Plan

### Phase 1: Foundation (1 session)
- Add Alpine.js CDN to `dashboard.html`
- Create `Alpine.store('app', { ... })` with shared state (current view, sessions list, settings)
- Migrate session list view — highest jank impact (innerHTML rebuild every 3s)
- Verify SSE integration works with Alpine reactivity

### Phase 2: Settings View (1 session)
- Migrate settings view to Alpine
- Replace manual API key management with Alpine data binding
- Migrate model picker to Alpine x-data

### Phase 3: Remaining Views (1 session)
- Migrate session detail view
- Migrate system log view
- Migrate agent panels / Q&A graph

### Phase 4: Cleanup (1 session)
- Remove window globals
- Remove manual DOM manipulation code
- Remove vanilla state management helpers
- Update CSS if needed for Alpine-generated DOM

### Rollback Strategy
Each phase is independently rollbackable:
1. Remove `<script>` tags for Alpine
2. Remove `x-*` attributes from HTML
3. Restore vanilla JS for that view
4. No data loss — Alpine doesn't store data, it binds to existing state

## Consequences

### Positive

1. ✅ **DOM updates simplified** — Declare what the UI should look like, Alpine handles the rest
2. ✅ **Shared state management** — Alpine.store() provides reactive shared state across views
3. ✅ **New view creation** — Add `x-data` to HTML, write a data object, done
4. ✅ **Better debugging** — Alpine devtools show reactive dependencies
5. ✅ **Easier onboarding** — New contributors know Alpine (or can learn in an afternoon)
6. ✅ **Zero build step** — CDN include, same as current architecture
7. ✅ **Incremental migration** — Each view can be migrated independently
8. ✅ **Trivially removable** — If Alpine doesn't work out, removal is straightforward

### Negative

1. ❌ **Two systems during migration** — Some views Alpine, some vanilla until migration completes
2. ❌ **New dependency** — Must track Alpine version (trivial with CDN)
3. ❌ **Paradigm shift** — Declarative vs imperative during migration period

### Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Migration stalls | Two paradigms coexist | Phase 1 alone (session list) provides the highest value |
| Alpine.js abandoned | Framework disappears | 28K stars, active development; removal is trivial |
| Performance regression | UI slower than before | Alpine is faster than innerHTML rebuilds; benchmark before/after Phase 1 |
| Alpine store conflicts | State management issues | Use namespaced store keys; follow Alpine.store() best practices |

## Related Issues

- ADR-0049 (The Ladder): Use existing solutions over building your own — Alpine.js is the "boring" choice
- ADR-0003: Web frontend and multi-session — current frontend architecture context
- ADR-0008: Dashboard enhancements — existing dashboard improvements

## Open Questions

1. Should we consider Petite-Vue (~6KB) as an even lighter alternative? → Decision: deferred. Alpine has better documentation and ecosystem. Can revisit if bundle size becomes a concern.
2. Should we use Alpine plugins for routing (alpinejs-router)? → Decision: deferred. The dashboard is a single-page app with view switching, not traditional routing. Alpine.store() handles this.

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-06-24 | 1.0 | Initial version |
