---
phase:
  current: 1
  total: 1
  status:
    1: proposed
---

# ADR-0012: Replace DuckDuckGo with SearXNG for Web Search

## Status

Proposed

**Version:** 1.0
**Last Updated:** 2026-06-16

## Context

Despite the rate limiting, caching, and fallback mechanisms added in ADR-0011, web search remains a bottleneck. The `ddgs` library scrapes DuckDuckGo, Google, and Brave as backends. When rate-limited by one provider, it falls back to the others — all eventually block us from the same IP. This causes agents to produce content without web research, resulting in thin PDFs and failed sessions.

### Observed Impact

| Metric | Value | Period |
|--------|-------|--------|
| HTTP 429 errors | 101 | 7 minutes (9 concurrent sessions) |
| Agents producing content without web research | ~25% of sessions | During peak load |
| PDF underweight warnings | ~15% of sessions | Correlated with search failures |
| Complete session failures | ~5% of sessions | Search-dependent agents |

### Root Cause

The `ddgs` library's fallback chain (DuckDuckGo → Google → Brave) does not solve rate limiting because all three backends see the same source IP. A self-hosted metasearch engine breaks this by:

1. Running locally — no external rate limits
2. Aggregating results from 70+ engines via a single query
3. Acting as a privacy proxy — providers never see the user's IP

### Prior Art / Alternatives Considered

| Approach | Pros | Cons |
|----------|------|------|
| Keep ddgs with ADR-0011 throttling | No new dependencies | Still rate-limited under load |
| Paid search API (Google, SerpAPI) | No rate limits | Cost, API key management |
| Rotate residential proxies | Bypasses IP bans | Complexity, cost, ethically questionable |
| **SearXNG (chosen)** | Self-hosted, no rate limits, metasearch, free | Requires Docker, ~200MB RAM |

## Decision

Replace `ddgs`/`duckduckgo-search` with **SearXNG** as the web search backend.

### 1. SearXNG Overview

SearXNG is a free, open-source metasearch engine (17k+ GitHub stars) that aggregates results from 70+ search engines (Google, Bing, DuckDuckGo, Wikipedia, etc.) without sending the user's IP to any of them. It provides a clean JSON API.

- **Repository:** https://github.com/searxng/searxng
- **Docs:** https://docs.searxng.org/
- **License:** AGPLv3

### 2. Deployment Options

| Option | Setup | Pros | Cons |
|--------|-------|------|------|
| **Local Docker instance** | `docker run -d --restart unless-stopped -p 8888:8080 searxng/searxng` | Full control, no external dependency, fastest | Requires Docker, ~200MB RAM |
| **Public SearXNG instance** | Point to `https://searx.be` | Zero setup | Dependent on 3rd party, possible rate limits |

**Recommendation:** Local Docker instance with automatic fallback to a public instance.

#### Local Docker Setup

```bash
# One-time setup
docker run -d \
  --name searxng \
  --restart unless-stopped \
  -p 8888:8080 \
  -e SEARXNG_BASE_URL=http://localhost:8888 \
  searxng/searxng

# Or with docker-compose (recommended for persistence)
services:
  searxng:
    image: searxng/searxng
    container_name: searxng
    ports:
      - "8888:8080"
    environment:
      - SEARXNG_BASE_URL=http://localhost:8888
    restart: unless-stopped
```

#### SearXNG Configuration (`settings.yml`)

Mount a custom `settings.yml` to enable desired engines and disable the rate limiter (which blocks local clients by default):

```yaml
# settings.yml — mount into container at /etc/searxng/settings.yml
use_default_settings: true

general:
  instance_name: "DeepResearch SearXNG"

server:
  limiter: false  # IMPORTANT: disable for local instances to avoid 429 on local requests
  image_proxy: false

search:
  formats:
    - html
    - json  # REQUIRED for API access — public instances often disable this

engines:
  - name: google
    engine: google
    shortcut: g
  - name: bing
    engine: bing
    shortcut: b
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
```

**Critical notes:**
- **`server.limiter: false`** — The default SearXNG Docker image includes the `limiter` plugin, which rate-limits requests including those from localhost. This must be disabled for local deployments or the application will receive HTTP 429 errors from its own search backend.
- **`search.formats: [json]`** — Public SearXNG instances often disable JSON output (returning 403). Any fallback instance URL must have JSON format enabled in its `settings.yml`. The application should health-check JSON support before using a fallback URL (see `POST /api/system/search/test`).

Mount the config file when starting the container:

```bash
docker run -d \
  --name searxng \
  --restart unless-stopped \
  -p 8888:8080 \
  -v ./settings.yml:/etc/searxng/settings.yml \
  -e SEARXNG_BASE_URL=http://localhost:8888 \
  searxng/searxng
```

### 3. Configuration

Three layers of configuration, in order of precedence:

1. **`SEARXNG_URL` environment variable** — overrides all others
2. **`~/.deepresearch/config.json`** — persistent user config
3. **Settings tab** — dashboard UI

#### Config Schema

```json
{
  "search": {
    "engine": "searxng",
    "searxng_url": "http://localhost:8888",
    "searxng_fallback_url": "https://searx.be",
    "searxng_engines": ["google", "bing", "duckduckgo"],
    "searxng_categories": ["general"],
    "searxng_timeout": 10
  }
}
```

#### Settings UI

New **Search Engine** section in the Settings tab:

- Engine selector: `searxng` (default) | `ddgs` (legacy fallback)
- SearXNG URL input with "Test" button
- Fallback URL input
- Engine multi-select (google, bing, duckduckgo, wikipedia, etc.)

### 4. Integration Changes

#### `src/deepresearch/tools/web_search.py`

Replace `ddgs` calls with `httpx.get()` against SearXNG's JSON API:

```python
import httpx

# SearXNG configuration (loaded from settings)
_searxng_url: str = "http://localhost:8888"
_searxng_fallback_url: str = "https://searx.be"
_searxng_engines: list[str] = ["google", "bing", "duckduckgo"]
_searxng_categories: list[str] = ["general"]
_searxng_timeout: int = 10

async def _searxng_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search via SearXNG JSON API."""
    params = {
        "q": query,
        "format": "json",
        "categories": ",".join(_searxng_categories),
        "engines": ",".join(_searxng_engines),
    }

    # Try primary URL, then fallback
    for base_url in [_searxng_url, _searxng_fallback_url]:
        try:
            async with httpx.AsyncClient(timeout=_searxng_timeout) as client:
                resp = await client.get(f"{base_url}/search", params=params)
                resp.raise_for_status()
                data = resp.json()

                results = []
                for item in data.get("results", [])[:max_results]:
                    results.append({
                        "title": (item.get("title", "") or "")[:80],
                        "snippet": (item.get("content", "") or "")[:150],
                        "url": (item.get("url", "") or "")[:80],
                    })
                return results
        except Exception as e:
            logger.warning("SearXNG search failed (%s): %s", base_url, e)
            continue

    # Both URLs failed
    return []
```

#### Response Mapping

| ddgs field | SearXNG field | Truncation |
|------------|---------------|------------|
| `title` | `title` | 80 chars |
| `body` | `content` | 150 chars |
| `href` | `url` | 80 chars |

> **Note:** SearXNG also returns an `engine` field per result (e.g., `"engine": "google"`) indicating which backend provided the result. This is useful for debugging but is intentionally not mapped to maintain ddgs output compatibility.

#### Search Query Parameters

```
ddgs:     DDGS().text(query, max_results=5)
searxng:  GET /search?q=query&format=json&categories=general&engines=google,bing,duckduckgo
```

### 5. `pyproject.toml` Changes

```diff
 dependencies = [
     "httpx>=0.27.0",
-    "ddgs>=8.0.0",
     "litellm>=1.60.0",
     ...
  ]

  [project.optional-dependencies]
  dev = [
      "pytest>=8.0.0",
      ...
-    "duckduckgo-search>=8.0.0",
+ ddgs = [
+     "ddgs>=8.0.0",
+     "duckduckgo-search>=8.0.0",
+ ]
  ]
```

No new dependency needed — `httpx` is already used for HTTP requests. `ddgs` is moved to an optional extra (`pip install deepresearch[ddgs]`) for users who cannot run Docker.

### 6. Migration Path

A phased migration preserves backward compatibility:

| Phase | Description | Risk |
|-------|-------------|------|
| **Phase 1** | Add SearXNG client alongside ddgs. Feature flag: `search_engine = "searxng" \| "ddgs"` (default: searxng) | Low — ddgs remains available |
| **Phase 2** | Validate SearXNG under load. Monitor search success rate, latency, result quality | Low — both engines available |
| **Phase 3** | *Optional.* Remove ddgs from default dependencies. Make SearXNG the only default backend. ddgs remains available as an optional extra (`pip install deepresearch[ddgs]`) for users who cannot run Docker. Only execute after successful Phase 2 validation. | Low — ddgs still available as opt-in |

#### Feature Flag Implementation

```python
# src/deepresearch/tools/web_search.py

_search_engine: str = "searxng"  # configurable via settings

async def web_search(
    query: str, max_results: int = 5, retries: int = 3
) -> list[dict[str, str]]:
    if _search_engine == "searxng":
        return await _searxng_search(query, max_results)
    else:
        return await _ddgs_search(query, max_results)  # legacy path
```

### 7. Retained from ADR-0011

These mechanisms remain valuable with SearXNG:

- **Search cache** — avoids redundant queries across agents (same query → cached result)
- **Global search semaphore** — prevents local resource exhaustion (too many concurrent HTTP requests)
- **Rate limiter** — still useful to avoid overwhelming the local SearXNG instance under extreme load

The per-request jitter and retry logic also remain, but retries now hit the local SearXNG instance (fast) rather than external providers (slow/rate-limited).

### 8. Implementation Files

| File | Change |
|------|--------|
| `src/deepresearch/tools/web_search.py` | Add `_searxng_search()`, feature flag, remove `ddgs` import |
| `pyproject.toml` | Remove `ddgs>=8.0.0` and `duckduckgo-search>=8.0.0` |
| `src/deepresearch/web/server.py` | Add SearXNG config endpoint, settings persistence |
| Dashboard Settings tab | New Search Engine section |
| `scripts/setup.sh` (optional) | Add `docker run` command for SearXNG |
| `tests/` | Update search tests to mock SearXNG responses |

### 9. API Changes

#### `GET /api/system/search` — New Endpoint

Returns search engine configuration and health status. The `status` field reflects the result of the **last probe** (from `POST /api/system/search/test` or the most recent search), not a live health check:

```json
{
  "engine": "searxng",
  "searxng_url": "http://localhost:8888",
  "status": "healthy",
  "last_search_time": 2.3,
  "cached_queries": 42
}
```

> **Note:** Status is updated after each search attempt or explicit probe. A `"healthy"` status means the last request succeeded; `"degraded"` means fallback was used; `"unhealthy"` means both primary and fallback failed.

#### `POST /api/system/search/test` — New Endpoint

Tests the SearXNG connection with a probe query:

```json
{
  "status": "ok",
  "results_count": 5,
  "latency_ms": 340,
  "engine_url": "http://localhost:8888"
}
```

## Documentation

### SearXNG Resources

| Resource | URL |
|----------|-----|
| Repository | https://github.com/searxng/searxng |
| Documentation | https://docs.searxng.org/ |
| License | AGPLv3 |
| Docker Hub | https://hub.docker.com/r/searxng/searxng |

### Key Concepts

- **Metasearch engine** — SearXNG does not crawl the web itself. It forwards queries to configured upstream engines (Google, Bing, DuckDuckGo, etc.) and aggregates results.
- **JSON API** — Enabled via `search.formats: [json]` in `settings.yml`. Required for programmatic access. Often disabled on public instances.
- **Engine configuration** — Engines are configured in `settings.yml` under the `engines` key. Each engine can be enabled/disabled, and categories (general, images, news, etc.) control which engines are queried.
- **Limiter plugin** — The default Docker image includes a rate-limiter plugin (`server.limiter`). This must be disabled for local deployments or it will return HTTP 429 to local clients.

### Gotchas

1. **JSON format disabled on public instances** — Many public SearXNG instances disable JSON output for abuse prevention. Always verify JSON support before using a fallback URL (the `POST /api/system/search/test` endpoint does this automatically).
2. **Limiter plugin blocks local requests** — The default `settings.yml` in the Docker image enables the limiter plugin, which rate-limits all requests including those from localhost. Set `server.limiter: false` in your mounted `settings.yml`.
3. **Default engines may be limited** — The default Docker image ships with a conservative set of enabled engines. For best results, explicitly enable `google`, `bing`, and `duckduckgo` in your configuration.
4. **No persistent storage by default** — The container does not persist search history or cache across restarts. Use a volume mount for `settings.yml` and optionally for the data directory.

## Consequences

### Positive

1. **No more rate limiting** — local instance has no external rate limits
2. **Better search quality** — metasearch aggregates from 70+ engines, not just 3
3. **Privacy-respecting** — queries never leave the local network (when using local instance)
4. **No API keys** — free and open source
5. **Faster searches** — local instance eliminates network round-trip to external providers
6. **Fallback chain** — primary (local) → fallback (public instance) → empty results
7. **No new dependencies** — uses existing `httpx` library
8. **Backward compatible** — feature flag allows ddgs as fallback during migration

### Negative

1. **Docker dependency** — requires Docker for the recommended local deployment
2. **Memory overhead** — SearXNG container uses ~200MB RAM
3. **Additional service** — one more container to manage and monitor
4. **Public instance limitations** — fallback to public instances may have their own rate limits
5. **Setup complexity** — users must run `docker run` or `docker-compose up` before first use

### Risks

1. **SearXNG availability** — if the local instance crashes, searches fail. Mitigation: fallback to public instance, health check endpoint, `restart: unless-stopped`
2. **Public instance rate limits** — fallback public instances may throttle. Mitigation: configurable fallback URL, can point to self-hosted public instance
3. **SearXNG configuration** — default config may not enable all desired engines, and the limiter plugin may block local requests. Mitigation: documented `settings.yml` template with `limiter: false` and explicit engine list (see **Configuration** section).
4. **Docker not available** — some users may not have Docker. Mitigation: ddgs remains available as an optional extra (`pip install deepresearch[ddgs]`), documented in **Migration Path — Phase 3**.
5. **Single point of failure** — Local SearXNG instance failure impacts all search functionality. Mitigation: Auto-detect instance health on startup and per-search. If local instance is down, automatically fall back to public instance. Log health status.
6. **Operational overhead** — Self-hosting adds Docker maintenance (updates, resource monitoring). Mitigation: Docker `--restart unless-stopped` handles restarts. Document resource requirements (~200MB RAM, minimal CPU). Consider periodic health checks.
7. **Search quality variance** — Self-configured SearXNG may have different quality than ddgs. Mitigation: Enable multiple engines (google, bing, duckduckgo, wikipedia) in `settings.yml`. Document how to add/remove engines. Users can tune via settings.

## ADR References

- **ADR-0006** (Web Search and Tool Calling) — current `ddgs` implementation, `web_search()` function, tool calling loop
- **ADR-0011** (Concurrency Limits and Web Search Throttling) — rate limiter, cache, fallback mode (retained)

---

## Implementation Plan

| Step | Description | Files Changed |
|------|-------------|---------------|
| 1 | Add `_searxng_search()` function with httpx client | `tools/web_search.py` |
| 2 | Add feature flag `_search_engine` and config loading | `tools/web_search.py` |
| 3 | Add SearXNG config to settings schema and persistence | `web/server.py` |
| 4 | Add `GET /api/system/search` health endpoint | `web/server.py` |
| 5 | Add `POST /api/system/search/test` probe endpoint | `web/server.py` |
| 6 | Add Search Engine section to Settings tab | dashboard HTML/JS |
| 7 | Remove `ddgs` and `duckduckgo-search` from dependencies | `pyproject.toml` |
| 8 | Add setup script with `docker run` command | `scripts/setup.sh` |
| 9 | Update search tests to mock SearXNG responses | `tests/` |
| 10 | Validate under load: 9 concurrent sessions, measure success rate | manual testing |

## Open Questions

1. Should SearXNG be a hard requirement or optional? Decision: Optional — ddgs remains available as an optional extra (`pip install deepresearch[ddgs]`). SearXNG is the default but users without Docker can install with ddgs support.
2. Should we bundle a `docker-compose.yml` in the repo? Decision: Yes — provide a `docker-compose.yml` at the project root for easy SearXNG setup.
3. What SearXNG engines should be enabled by default? Decision: `google`, `bing`, `duckduckgo` — the three most reliable general-purpose engines. Users can customize via config.
4. Should the SearXNG health check run on server startup? Decision: Yes — log a warning if SearXNG is unreachable, but don't block startup (fallback to public instance).
5. When should ddgs be fully removed? Decision: Only after Phase 2 validation confirms SearXNG meets or exceeds ddgs on search success rate, latency, and result quality. Phase 3 is optional — if SearXNG proves unreliable for any use case, ddgs remains available as an opt-in dependency.

## Changelog

| Date | Change |
|------|--------|
| 2026-06-16 | Initial version — ADR-0012 proposed |
