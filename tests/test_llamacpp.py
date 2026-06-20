"""Tests for llama.cpp binary lifecycle endpoints.

Covers:
  - _detect_llamacpp_platform() — platform detection (macOS, Linux, Windows)
  - _build_llamacpp_download_url() — URL construction
  - _get_latest_llamacpp_tag() — GitHub API tag resolution
  - GET /api/local-backends/llamacpp/status
  - POST /api/local-backends/llamacpp/install (SSE)
  - POST /api/local-backends/llamacpp/uninstall (SSE)
  - POST /api/local-backends/llamacpp/start
  - POST /api/local-backends/llamacpp/stop
  - POST /api/local-backends/llamacpp/restart
"""

from __future__ import annotations

import asyncio
import json
import platform as _platform
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from deepresearch.web.server import (
    _build_llamacpp_download_url,
    _detect_llamacpp_platform,
    _get_latest_llamacpp_tag,
    app,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_llamacpp_globals():
    """Reset llamacpp global state before each test.

    This prevents state leakage between tests that peek at or mutate
    _llamacpp_process, _llamacpp_config, or _llamacpp_shutting_down.
    """
    import deepresearch.web.server as srv

    srv._llamacpp_process = None
    srv._llamacpp_config = {"port": 8080, "installed": False}
    srv._llamacpp_shutting_down = False
    yield


# ─── A. Unit Tests: Platform Detection ────────────────────────────────────


class TestPlatformDetection:
    """_detect_llamacpp_platform() for each supported platform."""

    def test_darwin_arm64(self):
        """macOS ARM64 returns macos-arm64 with tar.gz."""
        with (
            patch.object(_platform, "system", return_value="Darwin"),
            patch.object(_platform, "machine", return_value="arm64"),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "macos-arm64", "ext": "tar.gz"}

    def test_darwin_x64(self):
        """macOS x86_64 returns macos-x64 with tar.gz."""
        with (
            patch.object(_platform, "system", return_value="Darwin"),
            patch.object(_platform, "machine", return_value="x86_64"),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "macos-x64", "ext": "tar.gz"}

    def test_linux_x86_64_rocm(self):
        """Linux x86_64 with ROCm returns ubuntu-rocm-7.2-x64."""
        with (
            patch.object(_platform, "system", return_value="Linux"),
            patch.object(_platform, "machine", return_value="x86_64"),
            patch("shutil.which", side_effect=lambda cmd: {
                "rocm-smi": "/usr/bin/rocm-smi",
                "nvidia-smi": "/usr/bin/nvidia-smi",
            }.get(cmd)),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "ubuntu-rocm-7.2-x64", "ext": "tar.gz"}

    def test_linux_x86_64_nvidia(self):
        """Linux x86_64 with NVIDIA GPU (no ROCm) returns ubuntu-x64."""
        with (
            patch.object(_platform, "system", return_value="Linux"),
            patch.object(_platform, "machine", return_value="x86_64"),
            patch("shutil.which", side_effect=lambda cmd: {
                "rocm-smi": None,
                "nvidia-smi": "/usr/bin/nvidia-smi",
            }.get(cmd)),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "ubuntu-x64", "ext": "tar.gz"}

    def test_linux_x86_64_cpu(self):
        """Linux x86_64 with no GPU returns ubuntu-x64."""
        with (
            patch.object(_platform, "system", return_value="Linux"),
            patch.object(_platform, "machine", return_value="x86_64"),
            patch("shutil.which", return_value=None),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "ubuntu-x64", "ext": "tar.gz"}

    def test_linux_aarch64(self):
        """Linux aarch64 returns ubuntu-arm64 (no GPU check)."""
        with (
            patch.object(_platform, "system", return_value="Linux"),
            patch.object(_platform, "machine", return_value="aarch64"),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "ubuntu-arm64", "ext": "tar.gz"}

    def test_windows(self):
        """Windows x64 returns win-cpu-x64 with zip."""
        with (
            patch.object(_platform, "system", return_value="Windows"),
            patch.object(_platform, "machine", return_value="AMD64"),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "win-cpu-x64", "ext": "zip"}

    def test_unsupported_platform(self):
        """Unsupported OS raises RuntimeError."""
        with (
            patch.object(_platform, "system", return_value="SomeOS"),
            patch.object(_platform, "machine", return_value="x86_64"),
        ):
            with pytest.raises(RuntimeError, match="Unsupported platform"):
                _detect_llamacpp_platform()


class TestGpuDetectionPriority:
    """GPU detection prioritization on Linux x86_64."""

    def test_rocm_checked_before_nvidia(self):
        """ROCm is detected before NVIDIA when both binaries exist (ROCm check
        satisifies the condition so nvidia-smi is never looked up)."""
        checked_order: list[str] = []

        def tracking_which(cmd: str) -> str | None:
            checked_order.append(cmd)
            lookup = {
                "rocm-smi": "/usr/bin/rocm-smi",
                "nvidia-smi": "/usr/bin/nvidia-smi",
            }
            return lookup.get(cmd)

        with (
            patch.object(_platform, "system", return_value="Linux"),
            patch.object(_platform, "machine", return_value="x86_64"),
            patch("shutil.which", side_effect=tracking_which),
        ):
            result = _detect_llamacpp_platform()

        assert result["asset"] == "ubuntu-rocm-7.2-x64"
        # ROCm was checked first and returned immediately — nvidia-smi never
        # needed to be checked because the ROCm match already satisfied
        assert "rocm-smi" in checked_order
        assert "nvidia-smi" not in checked_order


# ─── B. Unit Tests: Download URL Construction ─────────────────────────────


class TestBuildDownloadUrl:
    """_build_llamacpp_download_url() constructs correct URLs."""

    def test_url_macos_arm64(self):
        """macOS ARM64 URL is correctly constructed."""
        url = _build_llamacpp_download_url(
            "b9739", {"asset": "macos-arm64", "ext": "tar.gz"}
        )
        expected = (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            "b9739/llama-b9739-bin-macos-arm64.tar.gz"
        )
        assert url == expected

    def test_url_linux_rocm(self):
        """Linux ROCm URL is correctly constructed."""
        url = _build_llamacpp_download_url(
            "b9999", {"asset": "ubuntu-rocm-7.2-x64", "ext": "tar.gz"}
        )
        expected = (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            "b9999/llama-b9999-bin-ubuntu-rocm-7.2-x64.tar.gz"
        )
        assert url == expected

    def test_url_windows_zip(self):
        """Windows URL uses .zip extension."""
        url = _build_llamacpp_download_url(
            "b9739", {"asset": "win-cpu-x64", "ext": "zip"}
        )
        expected = (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            "b9739/llama-b9739-bin-win-cpu-x64.zip"
        )
        assert url == expected


# ─── C. Unit Tests: Latest Tag Resolution ──────────────────────────────────


@pytest.mark.asyncio
class TestLatestTag:
    """_get_latest_llamacpp_tag() resolves latest release from GitHub."""

    async def test_returns_tag_name(self):
        """Tag name is extracted from GitHub API JSON response."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # .json() is called synchronously (not awaited) in the endpoint
        mock_resp.json = MagicMock(return_value={"tag_name": "b12345"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            tag = await _get_latest_llamacpp_tag()

        assert tag == "b12345"

    async def test_raises_on_http_error(self):
        """HTTP errors propagate from the GitHub API call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.raise_for_status.side_effect = Exception("HTTP 403")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            with pytest.raises(Exception, match="HTTP 403"):
                await _get_latest_llamacpp_tag()


# ─── D. Integration Tests: GET /status ────────────────────────────────────


class TestLlamacppStatus:
    """GET /api/local-backends/llamacpp/status."""

    def test_status_returns_expected_json_structure(self, client: TestClient):
        """Response contains installed, running, version fields of correct types."""
        with patch("shutil.which", return_value=None):
            resp = client.get("/api/local-backends/llamacpp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "installed" in data
        assert "running" in data
        assert "version" in data
        assert isinstance(data["installed"], bool)
        assert isinstance(data["running"], bool)

    def test_status_installed_and_running(self, client: TestClient):
        """When installed and process is alive, both flags are True."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        srv._llamacpp_process = mock_proc

        with (
            patch("shutil.which", return_value="/usr/local/bin/llama-server"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout="version 1.0\n", stderr=""
            )
            resp = client.get("/api/local-backends/llamacpp/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["installed"] is True
        assert data["running"] is True
        assert data["version"] == "version 1.0"

    def test_status_not_installed(self, client: TestClient):
        """When not installed, installed=False, running=False, version=None."""
        with patch("shutil.which", return_value=None):
            resp = client.get("/api/local-backends/llamacpp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["installed"] is False
        assert data["running"] is False
        assert data["version"] is None

    def test_status_version_unknown_on_subprocess_failure(self, client: TestClient):
        """Version is 'unknown' when --version subprocess fails."""
        with (
            patch("shutil.which", return_value="/usr/local/bin/llama-server"),
            patch("subprocess.run", side_effect=FileNotFoundError("not found")),
        ):
            resp = client.get("/api/local-backends/llamacpp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["installed"] is True
        assert data["version"] == "unknown"

    def test_status_version_from_stderr(self, client: TestClient):
        """Version falls back to stderr when stdout is empty."""
        with (
            patch("shutil.which", return_value="/usr/local/bin/llama-server"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout="", stderr="llama-server version 2.0"
            )
            resp = client.get("/api/local-backends/llamacpp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["version"] == "llama-server version 2.0"


# ─── E. Integration Tests: POST /install (SSE) ────────────────────────────


class TestLlamacppInstall:
    """POST /api/local-backends/llamacpp/install."""

    def test_install_already_installed_returns_sse_error(self, client: TestClient):
        """Pre-check returns SSE error event when llama-server is already in PATH."""
        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post("/api/local-backends/llamacpp/install")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert "install_error" in body
        assert "llama.cpp is already installed" in body

    def test_install_sse_events_have_progress_and_steps(self, client: TestClient):
        """Install flow produces SSE events with step, progress, and message fields."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-length": "2048"}
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock()

        async def mock_aiter_bytes():
            yield b"x" * 1024
            yield b"x" * 1024

        mock_resp.aiter_bytes = mock_aiter_bytes

        mock_tar = MagicMock()
        mock_tar.__enter__ = MagicMock(return_value=mock_tar)

        mock_member = MagicMock()
        mock_member.name = "llama-b9999/llama-server"
        mock_tar.getmembers.return_value = [mock_member]

        # Track which() calls: first llama-server check is pre-check (None),
        # later llama-server check is post-extract verification (return path).
        _which_call_count: int = 0

        def _which_side_effect(cmd: str) -> str | None:
            nonlocal _which_call_count
            _which_call_count += 1
            if cmd == "llama-server":
                if _which_call_count == 1:
                    return None  # pre-check — not installed
                return "/home/user/.local/bin/llama-server"  # verify — found after extraction
            # GPU detection inside _detect_llamacpp_platform
            if cmd in ("rocm-smi", "nvidia-smi"):
                return None
            return None

        with (
            patch("shutil.which", side_effect=_which_side_effect),
            # Mock platform detection directly so we don't need platform + GPU stubs
            patch(
                "deepresearch.web.server._detect_llamacpp_platform",
                return_value={"asset": "ubuntu-x64", "ext": "tar.gz"},
            ),
            patch(
                "deepresearch.web.server._get_latest_llamacpp_tag",
                new_callable=AsyncMock,
                return_value="b9999",
            ),
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("tarfile.open", return_value=mock_tar),
            patch("os.makedirs"),
            patch("os.chmod"),
            patch("os.remove"),
            patch("tempfile.mkstemp", return_value=(3, "/tmp/test.tar.gz")),
            patch("subprocess.run") as mock_sub,
        ):
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()

            mock_stream = MagicMock()
            mock_stream.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_stream.__aexit__ = AsyncMock()
            mock_client.stream = MagicMock(return_value=mock_stream)

            mock_client_cls.return_value = mock_client

            mock_sub.return_value = MagicMock(
                stdout="version b9999\n", stderr=""
            )

            resp = client.post("/api/local-backends/llamacpp/install")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert "install_log" in body
        assert "install_complete" in body
        assert "b9999" in body
        assert "success" in body
        # Verify progress values appear
        assert '"progress":' in body

    def test_install_error_on_bad_platform(self, client: TestClient):
        """Install returns SSE error when platform is unsupported."""
        with (
            patch("shutil.which", return_value=None),
            patch.object(_platform, "system", return_value="SomeOS"),
            patch.object(_platform, "machine", return_value="x86_64"),
        ):
            resp = client.post("/api/local-backends/llamacpp/install")

        assert resp.status_code == 200
        body = resp.text
        assert "install_error" in body
        assert "Unsupported platform" in body


# ─── F. Integration Tests: POST /uninstall (SSE) ──────────────────────────


class TestLlamacppUninstall:
    """POST /api/local-backends/llamacpp/uninstall."""

    def test_uninstall_not_installed_returns_sse_error(self, client: TestClient):
        """Pre-check returns SSE error when llama-server is not found."""
        with patch("shutil.which", return_value=None):
            resp = client.post("/api/local-backends/llamacpp/uninstall")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        assert "install_error" in body
        assert "llama.cpp is not installed" in body
        assert "NOT_INSTALLED" in body

    def test_uninstall_installed_success(self, client: TestClient):
        """Uninstall removes binary, state dir, returns success SSE events."""
        # which() is called 3 times: pre-check, get binary_path, then verify.
        # First 2 calls return path, 3rd (verify) returns None to pass.
        _which_count: int = 0

        def _which_llama(cmd: str) -> str | None:
            nonlocal _which_count
            _which_count += 1
            if cmd == "llama-server" and _which_count <= 2:
                return "/home/user/.local/bin/llama-server"
            return None

        with (
            patch("shutil.which", side_effect=_which_llama),
            patch("os.remove"),
            patch("os.path.exists", return_value=False),
            patch("shutil.rmtree"),
        ):
            resp = client.post("/api/local-backends/llamacpp/uninstall")

        assert resp.status_code == 200
        body = resp.text
        assert "install_complete" in body
        assert '"status": "success"' in body
        assert "uninstalled" in body.lower()

    def test_uninstall_stops_running_process_first(self, client: TestClient):
        """Uninstall terminates a running process before removing binary."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        srv._llamacpp_process = mock_proc

        _which_count: int = 0

        def _which_llama(cmd: str) -> str | None:
            nonlocal _which_count
            _which_count += 1
            if cmd == "llama-server" and _which_count <= 2:
                return "/home/user/.local/bin/llama-server"
            return None

        with (
            patch("shutil.which", side_effect=_which_llama),
            patch("os.remove"),
            patch("os.path.exists", return_value=False),
            patch("shutil.rmtree"),
        ):
            resp = client.post("/api/local-backends/llamacpp/uninstall")

        assert resp.status_code == 200
        body = resp.text
        assert "install_complete" in body
        mock_proc.terminate.assert_called_once()
        # After uninstall, process should be None
        assert srv._llamacpp_process is None


# ─── G. Integration Tests: POST /start ────────────────────────────────────


class TestLlamacppStart:
    """POST /api/local-backends/llamacpp/start."""

    def test_start_not_installed_returns_400(self, client: TestClient):
        """Start returns 400 error when llama-server is not installed."""
        with patch("shutil.which", return_value=None):
            resp = client.post("/api/local-backends/llamacpp/start")
        assert resp.status_code == 400
        data = resp.json()
        assert data["status"] == "error"
        assert "not installed" in data["message"].lower()

    def test_start_already_running_returns_ok(self, client: TestClient):
        """Start returns ok with 'already running' when process is alive."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        srv._llamacpp_process = mock_proc

        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post("/api/local-backends/llamacpp/start")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "already running" in data["message"].lower()

    def test_start_starts_subprocess_when_not_running(self, client: TestClient):
        """Start launches llama-server as subprocess when not already running."""
        mock_proc = MagicMock()
        mock_proc.returncode = None
        # Make wait() never complete to prevent background monitor from
        # detecting an exit and auto-restarting during the test.
        never_set = asyncio.Event()
        mock_proc.wait = AsyncMock(side_effect=never_set.wait)

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_exec.return_value = mock_proc

            # Mock health check to succeed
            mock_health_resp = MagicMock()
            mock_health_resp.status_code = 200
            mock_health_resp.__aenter__ = AsyncMock(return_value=mock_health_resp)
            mock_health_resp.__aexit__ = AsyncMock()

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_health_resp)
            mock_client_cls.return_value = mock_client

            resp = client.post("/api/local-backends/llamacpp/start")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "started" in data["message"].lower()
        mock_exec.assert_called_with(
            "llama-server", "--host", "127.0.0.1", "--port", "8080",
            stdout=-1, stderr=-2,
        )


# ─── H. Integration Tests: POST /stop ─────────────────────────────────────


class TestLlamacppStop:
    """POST /api/local-backends/llamacpp/stop."""

    def test_stop_not_running_returns_ok(self, client: TestClient):
        """Stop returns ok when no process is running."""
        resp = client.post("/api/local-backends/llamacpp/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "not running" in data["message"].lower()

    def test_stop_running_process_terminates(self, client: TestClient):
        """Stop terminates the running process and clears the reference."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        srv._llamacpp_process = mock_proc

        resp = client.post("/api/local-backends/llamacpp/stop")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "stopped" in data["message"].lower()
        mock_proc.terminate.assert_called_once()
        assert srv._llamacpp_process is None

    def test_stop_uses_kill_on_timeout(self, client: TestClient):
        """Stop kills the process if terminate does not complete in 5s."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        # Simulate terminate timing out
        mock_proc.wait = AsyncMock(
            side_effect=[asyncio.TimeoutError, None]
        )
        srv._llamacpp_process = mock_proc

        resp = client.post("/api/local-backends/llamacpp/stop")
        assert resp.status_code == 200
        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()
        assert srv._llamacpp_process is None


# ─── I. Integration Tests: POST /restart ──────────────────────────────────


class TestLlamacppRestart:
    """POST /api/local-backends/llamacpp/restart."""

    def test_restart_not_installed_returns_400(self, client: TestClient):
        """Restart returns 400 error when not installed (start fails)."""
        with patch("shutil.which", return_value=None):
            resp = client.post("/api/local-backends/llamacpp/restart")
        assert resp.status_code == 400
        data = resp.json()
        assert data["status"] == "error"
        assert "not installed" in data["message"].lower()

    def test_restart_stops_then_starts(self, client: TestClient):
        """Restart calls stop then start, returning start's response."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        # stop() calls wait — let it complete
        mock_proc.wait = AsyncMock()
        srv._llamacpp_process = mock_proc

        mock_new_proc = MagicMock()
        mock_new_proc.returncode = None
        # Prevent background monitor from auto-restarting during test
        never_set = asyncio.Event()
        mock_new_proc.wait = AsyncMock(side_effect=never_set.wait)

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            mock_exec.return_value = mock_new_proc

            mock_health_resp = MagicMock()
            mock_health_resp.status_code = 200
            mock_health_resp.__aenter__ = AsyncMock(return_value=mock_health_resp)
            mock_health_resp.__aexit__ = AsyncMock()

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_health_resp)
            mock_client_cls.return_value = mock_client

            resp = client.post("/api/local-backends/llamacpp/restart")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "started" in data["message"].lower()
        # Old process was terminated
        mock_proc.terminate.assert_called_once()
        # New process was started
        mock_exec.assert_called_with(
            "llama-server", "--host", "127.0.0.1", "--port", "8080",
            stdout=-1, stderr=-2,
        )
        assert srv._llamacpp_process is mock_new_proc


# ─── J. Route Registration Tests ──────────────────────────────────────────


class TestRouteRegistration:
    """llama.cpp endpoints are registered on the FastAPI app."""

    def test_llamacpp_routes_registered(self, client: TestClient):
        """All 6 llamacpp lifecycle routes are registered."""
        routes = [r.path for r in app.routes]
        expected = [
            "/api/local-backends/llamacpp/status",
            "/api/local-backends/llamacpp/install",
            "/api/local-backends/llamacpp/uninstall",
            "/api/local-backends/llamacpp/start",
            "/api/local-backends/llamacpp/stop",
            "/api/local-backends/llamacpp/restart",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"
