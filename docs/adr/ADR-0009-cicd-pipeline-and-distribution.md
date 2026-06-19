---
phase:
  current: 1
  total: 1
  status:
    1: done
---

# ADR-0009: CI/CD Pipeline, npm Wrapper, and Docker Distribution

## Status

Accepted

**Version:** 1.1
**Last Updated:** 2026-06-15

## Context

DeepeResearch is a Python package that needs multiple distribution channels:
- **PyPI** — standard Python installation via `pip install deepresearch`
- **npm wrapper** — for JavaScript/Node.js ecosystem users who want `npm install -g deepresearch`
- **Docker image** — zero-dependency containerized deployment
- Currently: manual `git clone` + `pip install -e ".[dev]"`

### Key Forces
1. No automated builds — releases are manual
2. No npm presence — JS developers can't easily install
3. No Docker image — no containerized deployment option
4. No CI/CD — tests don't run automatically on push
5. No release automation — version bumps are manual

### Platform Support
> **Windows is not currently supported.** The npm wrapper and Docker target Linux/macOS. Windows users should use WSL2 or install Python directly.

### Prior Art / Alternatives Considered
| Approach | Pros | Cons |
|----------|------|------|
| GitHub Actions CI + PyPI publish | Standard Python workflow, well-supported | Only covers Python users |
| npm wrapper (thin) | Accessible to JS ecosystem, `npx deepresearch` works | Not native JS, requires Python runtime |
| Docker multi-arch | Zero-dependency, reproducible, covers all platforms | ~600-700MB image size (Python + WeasyPrint deps) |
| Semantic release (semantic-release) | Automated versioning from commits | Heavy, opinionated, may conflict with manual version bumps |
| Release Drafter + manual publish | GitHub Release notes auto-generated | Still requires manual publish step |
| All-in-one `release.yml` | Single workflow for all targets | Complex, hard to debug partial failures |

## Decision

### 1. GitHub Actions Workflow (`.github/workflows/`)

**CI Pipeline** (runs on every push + PR):

`.github/workflows/ci.yml`
- Trigger: `push` to `master`, `pull_request` to `master`
- Concurrency:
  ```yaml
  concurrency:
    group: ci-${{ github.ref }}
    cancel-in-progress: true
  ```
- Permissions:
  ```yaml
  permissions:
    contents: read
    packages: write
  ```
- Caching: `actions/setup-python` with `cache: 'pip'` for fast dependency install
- Jobs:
  1. **test**: Python 3.11 + 3.12 matrix, `pip install -e ".[dev]"`, `pytest tests/ -x -q`
  2. **lint**: `ruff check .` + `ruff format --check .`
  3. **build**: Build sdist + wheel, verify with `twine check`
  4. **version-sync**: Verify `npm/package.json` version matches `pyproject.toml` version

**Release Pipeline** (runs on git tag push):

`.github/workflows/release.yml`
- Trigger: `push` with tag pattern `v*`
- Jobs:
  1. **test**: Full test suite
  2. **publish-pypi**: Build + publish to PyPI via `pypa/gh-action-pypi-publish`
  3. **docker**: Build + push to Docker Hub (`acharnite/deepresearch`)
  4. **npm-publish**: Build npm wrapper + publish to npm registry
  5. **github-release**: Create GitHub Release with changelog

**Docker Build** (runs on push to master + tags):

`.github/workflows/docker.yml`
- Trigger: `push` to `master`, tags `v*`
- Multi-arch: `linux/amd64`, `linux/arm64`
- Tags: `latest` (on master push), version tag (on v* push)
- Pushes to: `ghcr.io/acharnite/deepresearch` + `docker.io/acharnite/deepresearch`
- Caching: `docker/build-push-action` with `cache-from: type=gha` for layer caching
- Vulnerability scanning:
  ```yaml
  - uses: aquasecurity/trivy-action@master
    with:
      image-ref: ${{ env.IMAGE_TAG }}
      severity: CRITICAL,HIGH
      exit-code: 1
  ```

### 2. npm Wrapper Package

Create `npm/package.json` (NOT in root — separate npm directory):

```json
{
  "name": "deepresearch",
  "version": "0.6.2",
  "description": "Multi-agent AI research system — 6 AI agents collaborate to produce research papers",
  "bin": {
    "deepresearch": "bin/deepresearch.js"
  },
  "scripts": {
    "postinstall": "node scripts/install.js"
  },
  "keywords": ["ai", "research", "multi-agent", "llm", "pdf"],
  "license": "MIT",
  "repository": {
    "type": "git",
    "url": "https://github.com/Acharnite/deepresearch"
  },
  "engines": { "node": ">=18" }
}
```

The `bin/deepresearch.js` script:
```javascript
#!/usr/bin/env node
const { execSync, execFileSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const INSTALL_DIR = path.join(require('os').homedir(), '.deepresearch');
const VENV_DIR = path.join(INSTALL_DIR, 'venv');
const PYTHON = process.platform === 'win32' ? 'python' : 'python3';

function ensureInstalled() {
  if (fs.existsSync(path.join(VENV_DIR, 'bin', 'deepresearch'))) {
    return; // Already installed
  }
  
  console.log('🔧 Setting up DeepeResearch (first-time install)...');
  
  // Create venv
  execSync(`${PYTHON} -m venv "${VENV_DIR}"`, { stdio: 'inherit' });
  
  // Install from PyPI (single source of truth — no git fallback)
  execSync(`"${path.join(VENV_DIR, 'bin', 'pip')}" install deepresearch`, { stdio: 'inherit' });
  
  console.log('✅ DeepeResearch installed!');
}

ensureInstalled();

// Forward all args to the Python CLI (safe from argument injection)
const deepresearchBin = path.join(VENV_DIR, 'bin', 'deepresearch');
const child = execFileSync(
  deepresearchBin,
  process.argv.slice(2),
  { stdio: 'inherit', cwd: process.cwd() }
);
process.exit(child.status || 0);
```

The `scripts/install.js` (postinstall):
- Checks if Python 3.11+ is available
- Creates venv in `~/.deepresearch/venv/`
- Installs deepresearch from PyPI
- Provides clear error messages if Python is missing

### 3. Dockerfile

Create `Dockerfile` in project root:

```dockerfile
# Multi-stage build
# Stage 1: Build
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

# System deps for WeasyPrint
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-dev libffi curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages
COPY --from=builder /install /usr/local

# Create non-root user
RUN useradd --create-home --shell /bin/bash deepresearch
USER deepresearch
WORKDIR /home/deepresearch

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/status || exit 1

ENTRYPOINT ["deepresearch"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
```

`.dockerignore`:
```
.git
.venv
__pycache__
*.pyc
.pytest_cache
output/
logs/
node_modules/
npm/
tests/
docs/
*.pdf
```

### 4. Release Workflow Summary

On git tag push (`v0.7.0`):
1. Tests pass → build Python package → publish to PyPI
2. Build Docker image (amd64 + arm64) → push to GHCR + Docker Hub
3. Build npm wrapper → publish to npm registry
4. Create GitHub Release with changelog from CHANGELOG.md

User installation options:
```bash
# npm (easiest)
npm install -g deepresearch

# Docker
docker run -p 8080:8080 -e OPENCODE_API_KEY=xxx ghcr.io/acharnite/deepresearch

# pip
pip install deepresearch

# pipx
pipx install deepresearch

# Source
git clone https://github.com/Acharnite/deepresearch.git
cd deepresearch && pip install -e ".[dev]"
```

### 5. Supply Chain Security

#### PyPI — Trusted Publisher (OIDC)
Use PyPI Trusted Publishers via GitHub Actions OIDC. No API tokens stored as secrets. The publish job authenticates via `id-token: write` permission and `pypa/gh-action-pypi-publish` with no `password` field.

#### npm — Provenance
Publish with `npm publish --provenance` to generate npm provenance attestations. This links the published package back to the GitHub Actions build, enabling users to verify the build origin.

#### Docker — Cosign Image Signing
Sign Docker images with [cosign](https://github.com/sigstore/cosstore) (sigstore) after push. Attach signatures as annotations so users can verify image integrity:
```yaml
- uses: sigstore/cosign-installer@v3
- run: cosign sign ${{ env.IMAGE_TAG }}
```

#### GitHub Release — SHA256 Checksums
Attach SHA256 checksums for all release artifacts (sdist, wheel, Docker manifest) to the GitHub Release. Users can verify downloads with `sha256sum -c`.

### 6. Rollback Strategy

If a release partially fails (e.g., PyPI publishes but Docker fails), the GitHub Release is **not** created. Manual intervention is required. Failed Docker images are not tagged as `latest`. Each publish job is independent — a failure in one does not roll back the others.

## Consequences

### Positive
1. One-push release: git tag triggers PyPI + Docker + npm + GitHub Release
2. npm makes it accessible to JS ecosystem (`npx deepresearch` works)
3. Docker provides zero-dependency deployment
4. CI runs tests on every push — prevents regressions
5. Multi-arch Docker (amd64 + arm64) covers most servers
6. Consistent release process across all distribution channels
7. Automated changelog generation from commit history

### Negative
1. npm wrapper adds Python runtime check — may confuse pure JS users
2. Docker image is ~600-700MB (Python + WeasyPrint deps)
3. npm package is a thin wrapper — not a native JS tool
4. Release pipeline has 4 publish targets — any one can fail
5. npm postinstall may fail on systems without Python
6. Version synchronization across PyPI, npm, and Docker tags must be maintained

### Neutral
1. PyPI name `deepresearch` may be taken (fallback: `deepresearch-ai`)
2. Docker Hub rate limits for free accounts (mitigated by using GHCR as primary)
3. npm package version must be manually updated to match pyproject.toml version

### Risks and Mitigations
| Risk | Mitigation |
|------|-----------|
| PyPI name `deepresearch` already taken | Check availability before first publish; fallback to `deepresearch-ai` |
| Docker Hub rate limits | Use GHCR as primary registry (no rate limits for public repos) |
| npm postinstall fails without Python | Clear error message in install.js with download link |
| Version drift between npm and PyPI | CI step to verify version match before publish |
| Release pipeline partial failure | GitHub Release not created on failure; manual intervention required; failed Docker images not tagged `latest` |
| Supply chain attack (dependency tampering) | Trusted Publisher (OIDC) for PyPI, npm provenance, cosign for Docker, SHA256 checksums for GitHub Release |

## Related Issues
- #37 (Deployment — systemd/launchd/NSSM): Extends CI/CD with service deployment automation. Users can install DeepeResearch as a persistent background service via `deepresearch service install`.
- #50 (Server Crash): Resolved by #37 — systemd/launchd auto-restarts on crash, handles SIGTERM gracefully.

## ADR References
- **ADR-0001**: Multi-Agent Research Architecture
- **ADR-0005**: Auto-Install and Auto-Discover Local LLM Backends (Docker discovery)
