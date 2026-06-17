---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0006: Web Search and Tool Calling Integration

## Status

Proposed

**Version:** 1.0
**Last Updated:** 2026-06-15

## Context

Research agents in DeepeResearch initially relied solely on their training data to answer research questions. This produces outputs that are stale, factually outdated, or missing recent developments. Agents need real-time web access to supplement their training data and produce current, factually grounded research.

### Key Forces
1. Agents need live web access but must not depend on paid APIs
2. LiteLLM function calling provides a clean tool-call interface
3. DuckDuckGo requires no API key and has zero cost
4. Web search calls are synchronous (blocking I/O) and must not block the asyncio event loop
5. DuckDuckGo can be unreliable (timeouts, rate limits) — graceful fallback is essential
6. Agents may need multiple search queries per generation to refine their research
7. Cancel event support is needed so sessions can be stopped mid-search

### Prior Art / Alternatives Considered
| Approach | Pros | Cons |
|----------|------|------|
| Brave Search API | High quality results | Requires API key, costs money |
| Google Custom Search | Comprehensive | API key required, 100 queries/day free tier |
| DuckDuckGo (chosen) | Free, no API key, privacy-respecting | Occasional timeouts, rate limits |
| SerpAPI | Multiple search engines | Paid, requires API key |
| Wikipedia API | Structured, reliable | Limited to Wikipedia content |
| No web search | Simple, no external deps | Stale, outdated research |

## Decision

### Tool Definition: `WEB_SEARCH_TOOL`

A LiteLLM-compatible function-calling schema defined in `tools/web_search.py`:

- **Type:** `function`
- **Name:** `web_search`
- **Parameters:**
  - `query` (string, required) — the search query, 2–8 words
  - `max_results` (integer, optional, default 5) — number of results (1–10)
- **Description:** Informs the LLM when to invoke the tool — for up-to-date facts, recent developments, or external sources

### Web Search Execution: `web_search()`

The `web_search()` async function:

- Uses DuckDuckGo Search (`duckduckgo_search.DDGS`) library
- Runs synchronous search in a thread via `asyncio.to_thread()` to avoid blocking the event loop
- Returns structured results as a list of dicts: `[{title, snippet, url}, ...]`
- **Truncation:** title ≤ 80 chars, snippet ≤ 150 chars, URL ≤ 80 chars — keeps tool results compact for token efficiency
- **Retry with exponential backoff:** 3 attempts with 1s, 2s, 4s wait between retries
- **Graceful failure:** Returns an error dict (`{"title": "Search Error", "snippet": "Search failed: <msg>", "url": ""}`) instead of raising exceptions
- Respects the `max_results` cap (default 5, max 10)

### Multi-Turn Tool Calling Loop: `generate_with_tools()`

`LLMClient.generate_with_tools()` orchestrates the tool calling loop:

1. **Build messages** — constructs the initial message list from system prompt + user prompt via `_build_messages()`
2. **Streaming LLM call** — calls LiteLLM `acompletion()` with `stream=True` and the `tools` parameter; accumulates both text content and incremental tool call deltas from streaming chunks
3. **Assemble tool calls** — streaming tool call deltas (partial ID, name, arguments) are pieced together by tracking `tc.index` across chunks
4. **Execute tools** — if tool calls are present, the assistant message with `tool_calls` is appended, then each tool is executed sequentially. Currently only `web_search` is supported; unknown tools produce an error response fed back to the LLM
5. **Feed results back** — tool results are appended as `tool` role messages with the matching `tool_call_id`
6. **Repeat** — the loop continues (up to `max_tool_rounds = 5`) until the LLM produces a final text response without tool calls
7. **Return** — the accumulated `full_text` is returned; if an `event_callback` is set, a `[Final response complete]` signal is published

### Graceful Fallback Chain

The tool calling system has a multi-level fallback strategy:

```
Streaming + Tools → Streaming (no tools) → Non-streaming → No-tools retry
```

1. **Streaming + tools** (primary path) — if the model supports both streaming and function calling
2. **Streaming without tools** — if streaming with tools fails, falls back to `generate_stream()`
3. **Non-streaming** — if streaming fails entirely, falls back to `generate()` (non-streaming `acompletion()`)
4. **No-tools retry** — if `generate_with_tools()` raises `LLMError`, the agent retries via `_generate_with_retry()` without tools

This ensures research never blocks on tool-calling failures.

### ResearchAgent Integration

- `ResearchAgent.research_round_1()` calls `generate_with_tools()` with `WEB_SEARCH_TOOL` instead of `_generate_with_retry()` — agents search the web during their initial research pass
- Response parsing remains unchanged: JSON → `Findings` with `summary`, `key_points`, `perspective`, `confidence`, and `raw_response`
- All 6 research agents use web search in Round 1
- Agents can issue multiple search queries per generation (up to 5 tool-call rounds), refining queries based on prior results

### Cancel Event Support

- The `web_search()` function respects the cancel event: if the event is set between retry attempts, the search loop exits early
- The `generate_with_tools()` loop checks the cancel event before each tool execution round
- Cancellation propagates from the orchestrator's `_cancel_event` through the LLM client to tool execution

## Consequences

### Positive
1. **Live facts** — agents access current information, improving research quality and recency
2. **Graceful degradation** — multi-level fallback ensures research continues even when tool calling fails
3. **Multiple queries per agent** — up to 5 tool-call rounds per generation allow iterative search refinement
4. **No API costs** — DuckDuckGo is free and requires no API key
5. **Non-blocking** — `asyncio.to_thread()` keeps the event loop responsive during synchronous searches
6. **Compact results** — truncation keeps token usage efficient (title 80 chars, snippet 150 chars, URL 80 chars)
7. **Retry resilience** — exponential backoff handles transient DuckDuckGo failures
8. **Cancel support** — sessions can be stopped mid-search without resource leaks

### Negative
1. **DuckDuckGo reliability** — occasional timeouts and rate limits reduce search quality silently
2. **No search quality ranking** — DuckDuckGo results are less sophisticated than Google/Bing
3. **Token overhead** — tool definitions and results consume additional tokens per generation
4. **Streaming tool call assembly** — complex delta tracking across streaming chunks adds implementation complexity
5. **Search rate limits** — rapid successive searches from the same IP may be throttled

### Neutral
1. Tool calling is only used in Round 1 — Round 2 uses standard generation
2. The `ddgs` library is an unofficial DuckDuckGo client — may break if DuckDuckGo changes their API
3. Search results are not cached between agents — multiple agents may search for similar topics
4. Max 5 tool-call rounds prevents infinite loops but may limit complex research strategies

## Related Issues
- #51 (ASA — Attributed Source Attribution): Web search results already carry URL, title, snippet, and engine metadata. ASA pipes this data through Findings → IndividualReport → PDF citations.

## ADR References
- **ADR-0001** (Multi-Agent Research Architecture) — architecture this integrates with
- **ADR-0002** (Agent Personality & Model Selection) — LLMClient and provider routing

---

## Implementation Status (Updated 2026-06-15)

| Decision | Status | Notes |
|----------|--------|-------|
| WEB_SEARCH_TOOL schema | ✅ Implemented | tools/web_search.py |
| web_search() with DuckDuckGo | ✅ Implemented | ddgs library, asyncio.to_thread() |
| Exponential backoff retry | ✅ Implemented | 3 attempts, 1s/2s/4s |
| generate_with_tools() loop | ✅ Implemented | max 5 rounds, streaming assembly |
| Graceful fallback chain | ✅ Implemented | streaming+tools → streaming → non-streaming → no-tools |
| All 6 agents use web search | ✅ Implemented | Round 1 only |
| Cancel event support | ✅ Implemented | Checked in retry loop |
