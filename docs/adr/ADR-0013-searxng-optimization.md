---
phase:
  current: 1
  total: 1
  status:
    1: proposed
---

# ADR-0013: SearXNG Engine Optimization — Remove Problematic Backends, Tune Timeouts

## Status

Proposed

**Version:** 1.2
**Last Updated:** 2026-06-17

## Context

Since deploying SearXNG (ADR-0012) to replace the `ddgs` scraper, search reliability has improved — the local instance eliminates external rate limits on the application side. However, SearXNG itself queries upstream search engines on behalf of the application, and those upstream engines impose their own rate limits and blocking. During a 6-session test run, SearXNG generated **1,127 errors** from problematic backends.

### Engine Error Breakdown (6 sessions)

| Engine | Errors | Type | Root Cause |
|--------|--------|------|------------|
| DuckDuckGo | 658 | `httpx.TimeoutException`, captcha blocks | DuckDuckGo aggressively blocks automated queries via Tor exit nodes / datacenter IPs |
| Wikidata | 140 | `httpx.TimeoutException`, HTTP 502 Bad Gateway | Wikidata API is unreliable under load; upstream instabilities |
| Brave | 34 | HTTP 403 Forbidden | Brave blocks SearXNG's requests from datacenter IPs |
| Google | 1 | Captcha (3600s suspension) | Single captcha triggered, suspended for 1 hour — Google is otherwise the most reliable |

### Impact on DeepResearch Sessions

These errors cascade up the stack:

```
Upstream search error → SearXNG returns fewer/empty results → Agent receives thin research → PDF underweight warnings → Session failure
```

| Symptom | Frequency | Root Cause |
|---------|-----------|------------|
| Agents getting 0–1 search results per query | ~40% of searches | DDG/Wikidata/Brave errors in SearXNG |
| Thin PDF warnings (< 12 KB) | ~20% of sessions | Insufficient search results for agent context |
| "Quick session too short" (Issue #46) | ~15% of sessions | Session completes too fast with no data |
| PDF borderline size warnings (Issue #48) | ~10% of sessions | Just above minimum but thin |
| Server crash under load (Issue #50) | ~5% of sessions | Cascading failures from repeated retries |

### Root Cause

SearXNG's default configuration enables 84 of 269 bundled engines, including problematic ones. When SearXNG queries these engines:
- DuckDuckGo returns captchas or timeouts (it blocks datacenter IPs aggressively)
- Wikidata's API is unstable (2–5s timeouts, 502 errors)
- Brave returns 403 Forbidden (blocks non-browser user agents / datacenter IPs)

Additionally, the default `request_timeout: 2.0` is too aggressive for upstream engines that may take 3–5 seconds to respond, causing premature timeouts even on healthy engines.

### Prior Art / Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| **Remove problematic engines (chosen)** | Zero errors from removed engines; simplest fix | Fewer total engines (but enough remain) |
| Increase timeouts only | Fixes timeout errors | Doesn't fix captcha/403 blocks |
| Add retry to all engines | Handles transient failures | Doesn't fix permanent blocks (DDG captcha, Brave 403) |
| Proxy SearXNG through rotating IPs | Bypasses IP-based blocks | Complexity, cost, questionable ethics |
| Use paid search API as fallback | No rate limits at all | Cost, API key management |

## Decision

### 1. Remove Problematic Engines

Remove DuckDuckGo, Wikidata, and Brave from SearXNG's active engine list. These engines consistently fail under datacenter IPs and degrade search reliability for all sessions.

The remaining active engines — **Google**, **Bing**, and **Startpage** — are the most reliable:
- **Google** — most comprehensive index, best result quality. Occasional captchas handled via `retry_on_http_error`.
- **Bing** — stable, no errors observed. Good fallback for Google.
- **Startpage** — privacy proxy for Google results. Good secondary source.

### 2. Tune Request Timeouts and Retries

Increase `request_timeout` from 2.0s to 5.0s globally, and per-engine timeouts to 8.0s for the remaining engines. Add `retries: 2` for transient HTTP errors and `retry_on_http_error` for captcha/rate-limit codes.

### 3. Updated `settings.yml`

Replace the current minimal configuration (from ADR-0012) with a tuned version:

```yaml
# settings.yml — mount into container at /etc/searxng/settings.yml
use_default_settings:
  engines:
    remove:
      - duckduckgo
      - wikidata
      - brave

general:
  instance_name: "DeepResearch SearXNG"

server:
  limiter: false  # IMPORTANT: disable for local instances to avoid 429 on local requests
  image_proxy: false
  secret_key: "ultrasecretkey"  # Override via SEARXNG_SECRET env var

search:
  formats:
    - html
    - json  # REQUIRED for API access

outgoing:
  request_timeout: 5.0        # Increased from default 2.0 — gives upstream engines time
  max_request_timeout: 10.0   # Default is 10.0 — explicit for clarity
  retries: 2                  # Retry twice on transient HTTP errors
  retry_on_http_error:
    - 429                     # Rate limited — retry
    - 503                     # Service unavailable — retry

engines:
  - name: google
    engine: google
    shortcut: g
    retry_on_http_error:      # Retry on captcha/block pages
      - 403
      - 429
    timeout: 8.0              # Longer timeout for Google

  - name: bing
    engine: bing
    shortcut: b
    retry_on_http_error:
      - 429
    timeout: 8.0

  - name: startpage
    engine: startpage
    shortcut: sp
    retry_on_http_error:
      - 429
    timeout: 8.0

  # Academic search — supplement general web results
  - name: arxiv
    engine: arxiv
    shortcut: ar
    categories: science
    timeout: 10.0
    weight: 0.7               # Higher weight — primary academic source

  - name: pubmed
    engine: pubmed
    shortcut: pm
    categories: science
    timeout: 10.0
    weight: 0.6

  - name: semantic scholar
    engine: semantic_scholar
    shortcut: ss
    categories: science
    timeout: 10.0
    weight: 0.5

  - name: wikipedia
    engine: wikipedia
    shortcut: wp
    categories: science
    timeout: 5.0
    weight: 0.6
```

### 4. Update ADR-0012 `settings.yml` Template

The engine listing in ADR-0012 §2 (Configuration) should be updated to reflect these findings — remove DuckDuckGo from the recommended engine list and add the timeout/retry configuration.

### 5. Add Academic/Research Search Engines

For DeepeResearch's multi-agent academic research use case, adding specialized academic search engines significantly improves the quality and depth of research results. Beyond the general web search engines (Google, Bing, Startpage), these academic sources provide direct access to scientific literature, preprints, and structured scholarly data.

#### Candidate Engines

| Engine | Type | Value | Recommended |
|--------|------|-------|-------------|
| **arXiv** | Scientific preprints | High — primary source for CS/physics/math/biology preprints | ✅ |
| **PubMed** | Medical research | High — essential for biomedical and health topics | ✅ |
| **Semantic Scholar** | AI academic search | High — superior citation graph and influence metrics | ✅ |
| **CORE** | Open access papers | Medium — aggregates millions of open access papers (requires API key) | ❌ (optional, see docs) |
| **Crossref** | DOI resolution | Low — useful for metadata lookups but not primary search | ❌ |
| **OpenAlex** | Open scholarly catalog | Medium — free Scopus/Web of Science alternative, but API-heavy | ❌ |
| **Springer Nature** | Academic publisher | Low — publisher-specific, narrow coverage vs general academic engines | ❌ |
| **Wikipedia** | General reference | High — already enabled by default | Already active |
| **Reuters** | News | Medium — useful for current events context | ❌ |
| **Hugging Face** | ML models/datasets | Low — models/datasets search, not research papers | ❌ |
| **GitHub Code** | Code search | Low — implementation details, not a research source | ❌ |

#### Analysis

Adding too many engines creates three problems:
1. **Slower response** — SearXNG waits for all configured engines; every extra engine adds latency
2. **More errors** — each additional engine is a failure point that can delay or degrade results
3. **Redundant results** — many academic engines index overlapping content (e.g., CORE and Semantic Scholar both index open access papers)

The recommended engines were chosen to minimize overlap while maximizing coverage:
- **arXiv + PubMed** cover distinct domains (preprints vs. biomedical literature)
- **Semantic Scholar** provides AI-powered relevance ranking across CS/neuroscience/bio
- **CORE** (optional) serves as an open-access fallback — requires an API key (see Documentation section)
- **Wikipedia** is already enabled and provides broad general-reference coverage

Engines like Crossref, OpenAlex, and Springer Nature were excluded because they either require API keys, overlap significantly with included engines, or provide narrow value for the latency cost.

#### Updated Engine Configuration

Add these engines to the `settings.yml` `engines:` section (see §3 above for the full template):

```yaml
  - name: arxiv
    engine: arxiv
    shortcut: ar
    categories: science
    timeout: 10.0               # Academic APIs can be slower
    weight: 0.7                 # Higher weight — primary academic source

  - name: pubmed
    engine: pubmed
    shortcut: pm
    categories: science
    timeout: 10.0
    weight: 0.6

  - name: semantic scholar
    engine: semantic_scholar
    shortcut: ss
    categories: science
    timeout: 10.0
    weight: 0.5

  - name: wikipedia
    engine: wikipedia
    shortcut: wp
    categories: science
    timeout: 5.0
    weight: 0.6
```

Note: Academic engines use tiered weights (arxiv=0.7, pubmed=0.6, wikipedia=0.6, semantic scholar=0.5) to ensure their results supplement rather than dominate general web search results. CORE (not in default config — requires API key) would use weight: 0.5. The 10.0s timeout accommodates slower academic API response times.

### 6. Implementation Steps

| Step | Action | Command / Details |
|------|--------|-------------------|
| 1 | Stop old container | `docker stop searxng && docker rm searxng` |
| 2 | Write tuned `settings.yml` | As above, at project root or `docker/` directory |
| 3 | Start container with new config | `docker run -d --name searxng --restart unless-stopped -p 8888:8080 -v $(pwd)/settings.yml:/etc/searxng/settings.yml -e SEARXNG_BASE_URL=http://localhost:8888 -e SEARXNG_SECRET=$(openssl rand -hex 32) searxng/searxng` |
| 4 | Verify search works | `curl "http://localhost:8888/search?q=test&format=json"` returns results |
| 5 | Check logs for removed engines | `docker logs searxng 2>&1 \| grep -E "duckduckgo\|wikidata\|brave"` — should show no errors from these engines |
| 6 | Run test suite | `pytest tests/ -x -v` — confirm search tests pass |

### 7. Config Schema Update

The `searxng_engines` list in the application config should default to `["google", "bing", "startpage"]` instead of `["google", "bing", "duckduckgo"]`:

```json
{
  "search": {
    "engine": "searxng",
    "searxng_url": "http://localhost:8888",
    "searxng_fallback_url": "https://searx.be",
    "searxng_engines": ["google", "bing", "startpage"],
    "searxng_categories": ["general"],
    "searxng_timeout": 10
  }
}
```

## Expected Impact

### Engine Error Reduction

| Engine | Before (errors) | After | Mechanism |
|--------|-----------------|-------|-----------|
| DuckDuckGo | 658 | 0 | Removed via `use_default_settings.engines.remove` |
| Wikidata | 140 | 0 | Removed via `use_default_settings.engines.remove` |
| Brave | 34 | 0 | Removed via `use_default_settings.engines.remove` |
| Google | Captcha (3600s suspend) | Transient retry | `retry_on_http_error: [403, 429]` retries after captcha clears |
| Bing | 0 (stable) | 0 | Unchanged — remains stable |
| Startpage | 4 (sporadic) | ~0 | Timeout increase + retry_on_http_error |
| arXiv | — (new) | ~0 | Added — academic preprint coverage |
| PubMed | — (new) | ~0 | Added — biomedical literature coverage |
| Semantic Scholar | — (new) | ~0 | Added — AI-powered academic search |
| CORE (optional) | — (new) | ~0 | Optional — open access paper aggregation (requires API key) |
| Wikipedia | — (existing) | ~0 | Reconfigured with explicit timeout + weight |

### Downstream Impact

| Issue | Before | Expected After |
|-------|--------|----------------|
| Search results per query | 0–3 on problematic sessions | 5–10 consistently |
| PDF underweight warnings (Issue #48) | ~20% of sessions | ~5% or fewer |
| Quick session too short (Issue #46) | ~15% | ~5% |
| Server crash under load (Issue #50) | ~5% | Near 0% |
| DDG errors (Issue #47) | 658 errors | 0 (removed) |
| Research depth (academic sources) | None (default engines only) | Peer-reviewed + preprint coverage from 4 academic engines |

### Resource Impact

| Metric | Before | After |
|--------|--------|-------|
| Active engines | 70+ (default) | 7 (3 general + 4 academic) |
| SearXNG memory | ~200 MB | ~180 MB (fewer engines loaded) |
| SearXNG CPU | Moderate (retries + timeouts) | Lower (no failing engines) |
| Per-request latency | 2–8s (waiting on failing engines) | 3–5s (clean queries to working engines) |

## Consequences

### Positive

1. **Zero errors from DDG/Wikidata/Brave** — removed engines cannot fail
2. **Faster searches** — no waiting 2–5s for failing engines to time out
3. **More consistent results** — every query goes to known-working engines
4. **Fewer PDF underweight warnings** — agents get enough search results
5. **Reduced SearXNG CPU/memory** — fewer active engines to manage
6. **Google captchas handled gracefully** — `retry_on_http_error` means brief pauses instead of permanent failures
7. **Global timeout increase** — from 2.0s to 5.0s gives healthy engines room to respond
8. **Retries on transient errors** — `retries: 2` catches intermittent 503s from any remaining engine
9. **Academic search coverage** — arXiv, PubMed, Semantic Scholar, and Wikipedia provide direct access to scientific literature and reference content, significantly improving research depth for academic queries

### Negative

1. **Reduced general web diversity** — removed DDG, Wikidata, and Brave from general web results. Mitigation: academic engines (arXiv, PubMed, Semantic Scholar, Wikipedia) add research-depth diversity not available from general web engines alone; CORE can be added as an optional engine
2. **Google single point of failure** — if Google blocks entirely (not just captcha), result quality drops significantly. Mitigation: Bing and Startpage remain active as fallbacks.
3. **Startpage dependency on Google** — Startpage proxies Google results, so a Google block also affects Startpage. Mitigation: Bing is independent and unaffected.
4. **No Wikidata structured data** — Wikidata provided infobox-style structured results not available from general web engines. Mitigation: Wikidata was already failing 140/140 queries, so no actual loss.
5. **Configuration divergence from SearXNG defaults** — the custom `settings.yml` must be maintained separately from the default config. Mitigation: documented template in ADR.
6. **CORE requires an API key** — enabling CORE search requires registering for an API key and setting `inactive: false`. Mitigation: CORE is excluded from default config; documented as optional.

### Risks

1. **Google begins blocking datacenter IPs** — Google has been the most reliable engine, but could change. Mitigation: Bing and Startpage remain; can add additional engines (Qwant, Yahoo) to the `settings.yml`.
2. **Future SearXNG updates change `settings.yml` format** — the config structure may change across SearXNG versions. Mitigation: pin SearXNG version in `docker run` (e.g., `searxng/searxng:2025.12.0` or latest stable tag).
3. **All remaining web engines blocked simultaneously** — unlikely but possible. Mitigation: SearXNG has 70+ engines — add more (Qwant, Yahoo, Mojeek) via config update. Academic engines (arXiv, PubMed, etc.) operate independently of commercial search APIs and are unaffected by Google/Bing blocks. CORE can be added as an API-key-protected academic fallback.
4. **Loss of DDG's unique result quality** — DuckDuckGo sometimes returns different results than Google/Bing (less SEO-gamed content). Mitigation: acceptable trade-off given 658 errors.
5. **`retry_on_http_error` for Google 403 loops** — if Google consistently returns 403, retries will burn SearXNG resources. Mitigation: `retries: 2` (global) limits retry chain; max 3 attempts per query.

## ADR References

- **ADR-0012** (Replace DuckDuckGo with SearXNG) — established SearXNG as the search backend; this ADR optimizes the engine configuration
- **ADR-0011** (Concurrency Limits and Web Search Throttling) — rate limiter, cache, global semaphore (retained)
- **ADR-0006** (Web Search and Tool Calling) — original `web_search()` tool calling loop

## Related Issues

| Issue | Description | Addressed By |
|-------|-------------|--------------|
| #46 | Quick sessions produce output too short | Reduced search failures → agents get more data → longer sessions |
| #47 | DDGS errors cause search failures | Removed DDG entirely from SearXNG engine list |
| #48 | PDF borderline size warnings | More consistent search results → thicker PDFs |
| #49 | Optimize SearXNG engine configuration | This ADR — engine removal + timeout/retry tuning |
| #50 | Server crash under concurrent load | Fewer engine timeouts → less resource contention |

## Implementation Plan

| Step | Description | Owner | Verification |
|------|-------------|-------|-------------|
| 1 | Create tuned `settings.yml` with engine removal + timeouts | ops | Config syntax valid |
| 2 | Stop old SearXNG container | ops | `docker ps` shows container stopped |
| 3 | Start new container with updated config | ops | Container healthy (`docker ps`) |
| 4 | Verify JSON search API returns results | ops | `curl "http://localhost:8888/search?q=test&format=json"` returns ≥1 result |
| 5 | Verify no DDG/Wikidata/Brave errors in logs | ops | `docker logs searxng \| grep -E "duckduckgo\|wikidata\|brave"` empty |
| 6 | Update application default engines list | dev | `searxng_engines` defaults to `["google", "bing", "startpage"]` |
| 7 | Run full test suite | qa | `pytest tests/ -x -v` all pass |
| 8 | Load test with 6 concurrent sessions | qa | Zero search errors; no PDF underweight warnings |
| 9 | Update ADR-0012 settings.yml template | docs | ADR-0012 §2 reflects current engine list |

## Open Questions

1. **Should we pin the SearXNG version?** Decision: Yes — use `searxng/searxng:latest` for now (pinned after next proven stable release). Document the current version.
2. **Should Startpage remain as a default?** Decision: Yes — Startpage acts as a Google privacy proxy and provides useful secondary coverage. If Google blocks, Startpage blocks too (same upstream), but the additional redundancy is worthwhile.
3. **Should we add Qwant or Mojeek as additional engines?** Decision: Not yet — the seven-engine set (Google, Bing, Startpage + arXiv, PubMed, Semantic Scholar, Wikipedia) provides sufficient breadth. CORE can be added as an optional eighth engine if open-access paper coverage is needed (see Documentation section). Add more general web engines only if search reliability degrades further.
4. **Should the application-side timeout (`searxng_timeout: 10`) be adjusted?** Decision: Keep at 10s — it matches `max_request_timeout: 10.0` in SearXNG config and provides headroom for the 8.0s per-engine timeout plus retries.

## Documentation

### References

- **SearXNG settings.yml documentation**: https://docs.searxng.org/admin/settings/settings.yml.html
- **SearXNG engine configuration**: https://docs.searxng.org/admin/engines/configured_engines.html
- **CORE API key registration**: https://core.ac.uk/services/api/

### Settings Merge Behavior

This ADR uses `use_default_settings` with selective engine removal rather than a fully custom `settings.yml`. The merge semantics are:

1. SearXNG starts with its bundled default configuration (84 of 269 engines enabled)
2. `use_default_settings.engines.remove` subtracts engines from the default set (DuckDuckGo, Wikidata, Brave)
3. Explicit engine definitions in the `engines:` list **override** the default engine configuration for matching engines (e.g., Google gets custom `timeout: 8.0` + `retry_on_http_error`)
4. Engines not mentioned in either `remove` or explicit `engines:` retain their default behavior

This approach minimizes the config surface area — only deviations from defaults need to be specified.

### Per-Engine Override Semantics

When an engine is listed in the `engines:` section, its settings **fully replace** the default configuration for that engine name. This means:

- All default settings for that engine are discarded
- Only the explicitly listed fields take effect
- Missing fields (e.g., `disabled: false`) default to SearXNG's built-in defaults, not the previous engine defaults

### Optional: Enabling CORE Academic Search

CORE (`core.ac.uk`) provides open-access paper aggregation but requires an API key. To enable it:

```yaml
  - name: core.ac.uk
    engine: core
    shortcut: core
    categories: science
    api_key: "your-core-api-key"   # Register at https://core.ac.uk/services/api/
    inactive: false                 # CORE is inactive by default without an API key
    timeout: 10.0
    weight: 0.5
```

Note the engine name is `core.ac.uk` (not `core`), matching SearXNG's engine naming convention.

### Gotchas

1. **Environment variable syntax**: SearXNG uses `${VAR}` syntax, not `{{VAR}}` (which is Jinja2). The secret key env var is `SEARXNG_SECRET`, not `SEARXNG_SECRET_KEY`.
2. **CORE API key requirement**: CORE is `inactive: true` by default. Without an API key, it produces zero results. Set `inactive: false` and provide `api_key`.
3. **`use_default_settings` ordering**: The `remove` list is applied before explicit engine overrides. If you both remove and redefine an engine, redefine wins.
4. **Engine names can differ from engine types**: The `name:` field is a label; `engine:` selects the backend driver. For example, `name: semantic scholar` uses `engine: semantic_scholar`.

## Changelog

| Date | Change |
|------|--------|
| 2026-06-17 | Initial version — ADR-0013 proposed |
| 2026-06-17 | Added academic research engines (arXiv, PubMed, Semantic Scholar, CORE, Wikipedia) to engine configuration — §3 settings.yml updated, §5 added |
| 2026-06-17 | v1.2 — Review fixes: removed CORE from default config (requires API key), fixed `secret_key` env var syntax (`SEARXNG_SECRET` not `SEARXNG_SECRET_KEY`), added `categories: science` to academic engines, renamed `semantic_scholar` → `semantic scholar`, tiered weights (arxiv=0.7, pubmed=0.6, wikipedia=0.6, semantic scholar=0.5), added Documentation section |
