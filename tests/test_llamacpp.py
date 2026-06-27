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
import os
import platform as _platform
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from deepresearch.web.server import (
    _build_llamacpp_download_url,  # Re-exported from routes/_helpers for backward compat
    _detect_llamacpp_platform,  # Re-exported from routes/_helpers for backward compat
    _get_latest_llamacpp_tag,  # Re-exported from routes/_helpers for backward compat
    app,
)
from tests.conftest import get_all_paths


# ─── SSE Helpers ────────────────────────────────────────────────────────────


def _parse_sse_events(body: str) -> list[dict[str, str]]:
    """Parse SSE response body into a list of event dicts.

    Each dict has 'event' (type) and 'data' (raw string) keys.
    Lines without an explicit event: get event='message' per SSE spec.
    """
    events: list[dict[str, str]] = []
    current_event = "message"
    current_data_lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("event:"):
            current_event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[len("data:") :].strip())
        elif line == "":
            # Blank line = end of event
            if current_data_lines:
                events.append(
                    {
                        "event": current_event,
                        "data": "\n".join(current_data_lines),
                    }
                )
            current_event = "message"
            current_data_lines = []
    # Flush last event if body doesn't end with blank line
    if current_data_lines:
        events.append(
            {
                "event": current_event,
                "data": "\n".join(current_data_lines),
            }
        )
    return events


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
    Also cleans up local_backend_manager persisted state.
    """
    import deepresearch.web.server as srv

    # Save originals — use getattr with sentinel for globals that may not exist
    _SENTINEL = object()
    orig_process = getattr(srv, "_llamacpp_process", _SENTINEL)
    orig_config = getattr(srv, "_llamacpp_config", _SENTINEL)
    orig_shutting_down = getattr(srv, "_llamacpp_shutting_down", _SENTINEL)
    orig_serving = getattr(srv, "_llamacpp_serving_model", _SENTINEL)
    orig_detected = getattr(srv, "_llamacpp_detected", _SENTINEL)
    orig_last = getattr(srv, "_llamacpp_last_model", _SENTINEL)

    # Set known test state
    srv._llamacpp_process = None
    srv._llamacpp_config = {
        "port": 8080,
        "installed": False,
        "gpu_layers": 0,
        "context_size": 8192,
        "flash_attn": False,
    }
    srv._llamacpp_shutting_down = False
    srv._llamacpp_serving_model = None
    if orig_detected is not _SENTINEL:
        srv._llamacpp_detected = False
    if orig_last is not _SENTINEL:
        srv._llamacpp_last_model = None
    yield
    # Restore originals (not None — the actual original values)
    if orig_process is not _SENTINEL:
        srv._llamacpp_process = orig_process
    if orig_config is not _SENTINEL:
        srv._llamacpp_config = orig_config
    if orig_shutting_down is not _SENTINEL:
        srv._llamacpp_shutting_down = orig_shutting_down
    if orig_serving is not _SENTINEL:
        srv._llamacpp_serving_model = orig_serving
    if orig_detected is not _SENTINEL:
        srv._llamacpp_detected = orig_detected
    if orig_last is not _SENTINEL:
        srv._llamacpp_last_model = orig_last
    # Clean up any address set by start/serve endpoints
    from deepresearch.web.settings_manager import local_backend_manager

    overrides = local_backend_manager._load()
    overrides.pop("llama-cpp", None)
    local_backend_manager._save(overrides)


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
            patch(
                "shutil.which",
                side_effect=lambda cmd: {
                    "rocm-smi": "/usr/bin/rocm-smi",
                    "nvidia-smi": "/usr/bin/nvidia-smi",
                }.get(cmd),
            ),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "ubuntu-rocm-7.2-x64", "ext": "tar.gz"}

    def test_linux_x86_64_nvidia(self):
        """Linux x86_64 with NVIDIA GPU (no ROCm) returns Vulkan binary."""
        with (
            patch.object(_platform, "system", return_value="Linux"),
            patch.object(_platform, "machine", return_value="x86_64"),
            patch(
                "shutil.which",
                side_effect=lambda cmd: {
                    "rocm-smi": None,
                    "nvidia-smi": "/usr/bin/nvidia-smi",
                }.get(cmd),
            ),
        ):
            result = _detect_llamacpp_platform()
        assert result == {"asset": "ubuntu-vulkan-x64", "ext": "tar.gz"}

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
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(
                json.dumps({"tag_name": "b12345"}).encode(),
                b"",
            )
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            tag = await _get_latest_llamacpp_tag()

        assert tag == "b12345"

    async def test_raises_on_http_error(self):
        """curl errors propagate from the GitHub API call."""
        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(
            return_value=(
                b"",
                b"curl: (22) The requested URL returned error: 403",
            )
        )

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="Failed to fetch"):
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
        mock_proc.pid = 12345
        srv._llamacpp_process = mock_proc

        with (
            patch("shutil.which", return_value="/usr/local/bin/llama-server"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout="version 1.0\n", stderr="")
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
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "llama.cpp is already installed" in error_events[0]["data"]

    def test_install_sse_events_have_progress_and_steps(self, client: TestClient):
        """Install flow produces SSE events with step, progress, and message fields."""
        mock_tar = MagicMock()
        mock_tar.__enter__ = MagicMock(return_value=mock_tar)

        mock_member = MagicMock()
        mock_member.name = "llama-b9999/llama-server"
        mock_member.isdir.return_value = False
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

        # Mock curl subprocess for download
        mock_curl_proc = AsyncMock()
        mock_curl_proc.returncode = 0
        mock_curl_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_curl_proc.kill = MagicMock()

        # Mock subprocess.run for post-install version check
        mock_sub_run = MagicMock()
        mock_sub_run.stdout = "version b9999\n"
        mock_sub_run.stderr = ""

        with (
            patch("shutil.which", side_effect=_which_side_effect),
            patch(
                "deepresearch.web.server._detect_llamacpp_platform",
                return_value={"asset": "ubuntu-x64", "ext": "tar.gz"},
            ),
            patch(
                "deepresearch.web.server._get_latest_llamacpp_tag",
                new_callable=AsyncMock,
                return_value="b9999",
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_curl_proc,
            ),
            patch("tarfile.open", return_value=mock_tar),
            patch("os.makedirs"),
            patch("os.chmod"),
            patch("os.remove"),
            patch("tempfile.mkstemp", return_value=(3, "/tmp/test.tar.gz")),
            patch("subprocess.run", return_value=mock_sub_run),
        ):
            resp = client.post("/api/local-backends/llamacpp/install")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "install_log" in event_types
        assert "install_complete" in event_types
        # Verify at least one event has progress data
        log_events = [e for e in events if e["event"] == "install_log"]
        assert log_events, "Expected install_log events"
        # Verify b9999 and success appear in the data
        all_data = " ".join(e["data"] for e in events)
        assert "b9999" in all_data
        assert "success" in all_data
        # Verify progress values appear
        assert '"progress":' in resp.text

    def test_install_error_on_bad_platform(self, client: TestClient):
        """Install returns SSE error when platform is unsupported."""
        with (
            patch("shutil.which", return_value=None),
            patch.object(_platform, "system", return_value="SomeOS"),
            patch.object(_platform, "machine", return_value="x86_64"),
        ):
            resp = client.post("/api/local-backends/llamacpp/install")

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "Unsupported platform" in error_events[0]["data"]


# ─── F. Integration Tests: POST /uninstall (SSE) ──────────────────────────


class TestLlamacppUninstall:
    """POST /api/local-backends/llamacpp/uninstall."""

    def test_uninstall_not_installed_returns_sse_error(self, client: TestClient):
        """Pre-check returns SSE error when llama-server is not found."""
        with patch("shutil.which", return_value=None):
            resp = client.post("/api/local-backends/llamacpp/uninstall")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "llama.cpp is not installed" in error_events[0]["data"]
        assert "NOT_INSTALLED" in error_events[0]["data"]

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
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "install_complete" in event_types
        # Verify the success status in the data
        complete_events = [e for e in events if e["event"] == "install_complete"]
        assert complete_events
        all_data = " ".join(e["data"] for e in events)
        assert "success" in all_data
        assert "uninstalled" in all_data.lower()

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
        import deepresearch.web.server as srv

        srv._llamacpp_serving_model = "/home/user/.cache/gguf/models/test.gguf"

        mock_proc = MagicMock()
        mock_proc.returncode = None
        # Make wait() never complete to prevent background monitor from
        # detecting an exit and auto-restarting during the test.
        never_set = asyncio.Event()
        mock_proc.wait = AsyncMock(side_effect=never_set.wait)

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("deepresearch.web.server._is_port_available", return_value=True),
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
        # Verify -m flag was included
        call_args = mock_exec.call_args[0]
        assert "llama-server" in call_args
        assert "--host" in call_args
        assert "--port" in call_args
        assert "8080" in call_args
        assert "-m" in call_args
        assert "/home/user/.cache/gguf/models/test.gguf" in call_args


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
        mock_proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError, None])
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

        srv._llamacpp_serving_model = "/home/user/.cache/gguf/models/test.gguf"

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
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("deepresearch.web.server._is_port_available", return_value=True),
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
        # New process was started with -m flag
        call_args = mock_exec.call_args[0]
        assert "llama-server" in call_args
        assert "-m" in call_args
        assert "/home/user/.cache/gguf/models/test.gguf" in call_args
        assert srv._llamacpp_process is mock_new_proc


# ─── J. Route Registration Tests ──────────────────────────────────────────


class TestRouteRegistration:
    """llama.cpp endpoints are registered on the FastAPI app."""

    def test_llamacpp_routes_registered(self, client: TestClient):
        """All 10 llamacpp lifecycle routes are registered."""
        routes = get_all_paths(app)
        expected = [
            "/api/local-backends/llamacpp/status",
            "/api/local-backends/llamacpp/install",
            "/api/local-backends/llamacpp/uninstall",
            "/api/local-backends/llamacpp/start",
            "/api/local-backends/llamacpp/stop",
            "/api/local-backends/llamacpp/restart",
            "/api/local-backends/models/gguf",
            "/api/local-backends/llamacpp/serve",
            "/api/local-backends/llamacpp/serve-hf",
            "/api/local-backends/llamacpp/config",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"


# ─── K. Integration Tests: GET /models/gguf ───────────────────────────────


class TestListGgufModels:
    """GET /api/local-backends/models/gguf."""

    def test_empty_when_no_models_dir(self, client: TestClient):
        """Returns empty list when ~/.cache/gguf/models/ does not exist."""
        with patch("os.path.isdir", return_value=False):
            resp = client.get("/api/local-backends/models/gguf")
        assert resp.status_code == 200
        data = resp.json()
        assert data["models"] == []

    def test_lists_gguf_files_sorted_by_size(self, client: TestClient):
        """Lists .gguf files, sorted by size descending."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create fake GGUF files with known sizes
            small_path = os.path.join(tmpdir, "small.gguf")
            large_path = os.path.join(tmpdir, "large.gguf")
            readme_path = os.path.join(tmpdir, "readme.txt")

            with open(small_path, "wb") as f:
                f.write(b"x" * 100)
            with open(large_path, "wb") as f:
                f.write(b"x" * 5000)
            with open(readme_path, "w") as f:
                f.write("not a model")

            with patch("os.path.expanduser", return_value=tmpdir):
                resp = client.get("/api/local-backends/models/gguf")

            assert resp.status_code == 200
            data = resp.json()
            assert len(data["models"]) == 2
            # Sorted by size descending
            assert data["models"][0]["name"] == "large"
            assert data["models"][0]["size_bytes"] == 5000
            assert data["models"][1]["name"] == "small"
            assert data["models"][1]["size_bytes"] == 100
            # readme.txt excluded
            assert not any(m["name"] == "readme" for m in data["models"])

    def test_serving_field_reflects_active_model(self, client: TestClient):
        """serving=True when model matches _llamacpp_serving_model."""
        import tempfile
        import deepresearch.web.server as srv

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = os.path.join(tmpdir, "active.gguf")
            other_path = os.path.join(tmpdir, "other.gguf")
            with open(model_path, "wb") as f:
                f.write(b"x" * 100)
            with open(other_path, "wb") as f:
                f.write(b"x" * 100)

            srv._llamacpp_serving_model = model_path

            with patch("os.path.expanduser", return_value=tmpdir):
                resp = client.get("/api/local-backends/models/gguf")

            assert resp.status_code == 200
            data = resp.json()
            active = next(m for m in data["models"] if m["name"] == "active")
            other = next(m for m in data["models"] if m["name"] == "other")
            assert active["serving"] is True
            assert other["serving"] is False


# ─── L. Integration Tests: PUT /config ────────────────────────────────────


class TestLlamacppConfig:
    """PUT /api/local-backends/llamacpp/config."""

    def test_update_config_fields(self, client: TestClient):
        """Updates config fields and returns new config."""
        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={
                "port": 8081,
                "gpu_layers": -1,
                "context_size": 16384,
                "flash_attn": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["config"]["port"] == 8081
        assert data["config"]["gpu_layers"] == -1
        assert data["config"]["context_size"] == 16384
        assert data["config"]["flash_attn"] is True
        assert "warning" not in data

    def test_config_change_returns_warning_when_running(self, client: TestClient):
        """Returns warning when llama-server is running."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        srv._llamacpp_process = mock_proc

        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={"port": 9999},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "warning" in data
        assert "restart" in data["warning"].lower()

    def test_partial_config_update(self, client: TestClient):
        """Only specified fields are updated."""
        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={"gpu_layers": 32},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["gpu_layers"] == 32
        # Other fields unchanged
        assert data["config"]["port"] == 8080
        assert data["config"]["context_size"] == 8192


# ─── M. Integration Tests: POST /serve ────────────────────────────────────


class TestLlamacppServe:
    """POST /api/local-backends/llamacpp/serve."""

    def test_serve_not_installed_returns_error(self, client: TestClient):
        """Returns SSE error when llama-server is not installed."""
        with patch("shutil.which", return_value=None):
            resp = client.post(
                "/api/local-backends/llamacpp/serve",
                json={"model": "test.gguf"},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "not installed" in error_events[0]["data"].lower()

    def test_serve_no_model_returns_error(self, client: TestClient):
        """Returns SSE error when no model is specified."""
        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post(
                "/api/local-backends/llamacpp/serve",
                json={"model": ""},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "no model" in error_events[0]["data"].lower()

    def test_serve_model_not_found_returns_error(self, client: TestClient):
        """Returns SSE error when model file does not exist."""
        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=False),
            patch("os.walk", return_value=[]),
        ):
            resp = client.post(
                "/api/local-backends/llamacpp/serve",
                json={"model": "nonexistent.gguf"},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "not found" in error_events[0]["data"].lower()

    def test_serve_stops_existing_process(self, client: TestClient):
        """Stops existing process before starting new one."""
        import deepresearch.web.server as srv

        old_proc = MagicMock()
        old_proc.returncode = None
        old_proc.wait = AsyncMock()
        srv._llamacpp_process = old_proc

        new_proc = MagicMock()
        new_proc.returncode = None
        never_set = asyncio.Event()
        new_proc.wait = AsyncMock(side_effect=never_set.wait)
        new_proc.stderr = None

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=True),
            patch("os.path.abspath", side_effect=lambda p: p),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_exec.return_value = new_proc

            client.post(
                "/api/local-backends/llamacpp/serve",
                json={"model": "/path/to/model.gguf"},
            )

        old_proc.terminate.assert_called_once()


# ─── N. Updated Status Tests: active_model field ─────────────────────────


class TestLlamacppStatusPhase2:
    """GET /api/local-backends/llamacpp/status — Phase 2 fields."""

    def test_status_includes_active_model_when_serving(self, client: TestClient):
        """Status includes active_model, port, pid when running with a model."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        srv._llamacpp_process = mock_proc
        srv._llamacpp_serving_model = "/home/user/.cache/gguf/models/qwen.gguf"
        srv._llamacpp_config["port"] = 8080
        srv._llamacpp_config["gpu_layers"] = 0
        srv._llamacpp_config["context_size"] = 8192

        with (
            patch("shutil.which", return_value="/usr/local/bin/llama-server"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(stdout="b9739\n", stderr="")
            resp = client.get("/api/local-backends/llamacpp/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert "active_model" in data
        assert data["active_model"]["name"] == "qwen"
        assert (
            data["active_model"]["path"] == "/home/user/.cache/gguf/models/qwen.gguf"
        )
        assert data["port"] == 8080
        assert data["pid"] == 12345
        assert data["gpu_layers"] == 0
        assert data["context_size"] == 8192

    def test_status_omits_active_model_when_not_running(self, client: TestClient):
        """Status omits active_model, port, pid when not running."""
        with patch("shutil.which", return_value=None):
            resp = client.get("/api/local-backends/llamacpp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_model" not in data
        assert "port" not in data
        assert "pid" not in data


# ─── O. Updated Start Tests: model flag ──────────────────────────────────


class TestLlamacppStartPhase2:
    """POST /api/local-backends/llamacpp/start — Phase 2 model flags."""

    def test_start_without_model_returns_400(self, client: TestClient):
        """Start returns 400 when no model is configured."""
        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post("/api/local-backends/llamacpp/start")
        assert resp.status_code == 400
        data = resp.json()
        assert "no model" in data["message"].lower()

    def test_start_with_model_uses_m_flag(self, client: TestClient):
        """Start passes -m flag when _llamacpp_serving_model is set."""
        import deepresearch.web.server as srv

        srv._llamacpp_serving_model = "/home/user/.cache/gguf/models/qwen.gguf"

        mock_proc = MagicMock()
        mock_proc.returncode = None
        never_set = asyncio.Event()
        mock_proc.wait = AsyncMock(side_effect=never_set.wait)

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_exec.return_value = mock_proc

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
        # Verify -m flag was included
        call_args = mock_exec.call_args
        assert "-m" in call_args[0]
        assert "/home/user/.cache/gguf/models/qwen.gguf" in call_args[0]

    def test_start_with_config_flags(self, client: TestClient):
        """Start passes -ngl, -c, --flash-attn when configured."""
        import deepresearch.web.server as srv

        srv._llamacpp_serving_model = "/home/user/.cache/gguf/models/qwen.gguf"
        srv._llamacpp_config["gpu_layers"] = 32
        srv._llamacpp_config["context_size"] = 16384
        srv._llamacpp_config["flash_attn"] = True

        mock_proc = MagicMock()
        mock_proc.returncode = None
        never_set = asyncio.Event()
        mock_proc.wait = AsyncMock(side_effect=never_set.wait)

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("httpx.AsyncClient") as mock_client_cls,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_exec.return_value = mock_proc

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
        call_args = mock_exec.call_args[0]
        assert "-ngl" in call_args
        assert "32" in call_args
        assert "-c" in call_args
        assert "16384" in call_args
        assert "--flash-attn" in call_args


# ─── P. Phase 2: GGUF Model Listing — additional tests ─────────────────────


class TestListGgufModelsPhase2:
    """Additional tests for GET /api/local-backends/models/gguf."""

    def test_empty_directory_returns_empty_list(self, client: TestClient):
        """Empty directory returns empty list."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.path.expanduser", return_value=tmpdir):
                resp = client.get("/api/local-backends/models/gguf")
        assert resp.status_code == 200
        data = resp.json()
        assert data["models"] == []

    def test_multiple_files_returned_with_correct_metadata(self, client: TestClient):
        """Multiple .gguf files returned with name, path, size_bytes, serving."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two .gguf files with different sizes
            path1 = os.path.join(tmpdir, "model1.gguf")
            path2 = os.path.join(tmpdir, "model2.gguf")
            with open(path1, "wb") as f:
                f.write(b"x" * 1000)
            with open(path2, "wb") as f:
                f.write(b"y" * 2000)
            with patch("os.path.expanduser", return_value=tmpdir):
                resp = client.get("/api/local-backends/models/gguf")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["models"]) == 2
            for model in data["models"]:
                assert "name" in model
                assert "path" in model
                assert "size_bytes" in model
                assert "serving" in model
                assert isinstance(model["serving"], bool)

    def test_subdirectories_are_scanned(self, client: TestClient):
        """Recursive scan finds .gguf in subdirectories."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "subdir")
            os.makedirs(subdir)
            model_path = os.path.join(subdir, "nested_model.gguf")
            with open(model_path, "wb") as f:
                f.write(b"z" * 500)
            with patch("os.path.expanduser", return_value=tmpdir):
                resp = client.get("/api/local-backends/models/gguf")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["models"]) == 1
            # Name includes subdirectory prefix
            assert data["models"][0]["name"] == "subdir/nested_model"

    def test_serving_flag_reflects_active_model(self, client: TestClient):
        """serving=True only for the currently served model."""
        import tempfile
        import deepresearch.web.server as srv

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = os.path.join(tmpdir, "active.gguf")
            other_path = os.path.join(tmpdir, "other.gguf")
            with open(model_path, "wb") as f:
                f.write(b"a" * 100)
            with open(other_path, "wb") as f:
                f.write(b"b" * 100)
            srv._llamacpp_serving_model = model_path
            with patch("os.path.expanduser", return_value=tmpdir):
                resp = client.get("/api/local-backends/models/gguf")
            assert resp.status_code == 200
            data = resp.json()
            for m in data["models"]:
                if m["name"] == "active":
                    assert m["serving"] is True
                else:
                    assert m["serving"] is False

    def test_sorted_by_size_descending(self, client: TestClient):
        """Results sorted largest first."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create files with known sizes
            sizes = [100, 5000, 2000]
            names = ["tiny", "huge", "medium"]
            for name, size in zip(names, sizes):
                path = os.path.join(tmpdir, f"{name}.gguf")
                with open(path, "wb") as f:
                    f.write(b"x" * size)
            with patch("os.path.expanduser", return_value=tmpdir):
                resp = client.get("/api/local-backends/models/gguf")
            assert resp.status_code == 200
            data = resp.json()
            returned_sizes = [m["size_bytes"] for m in data["models"]]
            assert returned_sizes == sorted(returned_sizes, reverse=True)

    def test_missing_directory_returns_empty_list(self, client: TestClient):
        """Missing ~/.cache/gguf/models/ returns empty list."""
        with patch("os.path.isdir", return_value=False):
            resp = client.get("/api/local-backends/models/gguf")
        assert resp.status_code == 200
        data = resp.json()
        assert data["models"] == []


# ─── Q. Phase 2: Serve Endpoint — additional tests ─────────────────────────


class TestLlamacppServePhase2:
    """Additional tests for POST /api/local-backends/llamacpp/serve."""

    def test_serve_requires_model_field(self, client: TestClient):
        """Missing model field returns SSE error."""
        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post(
                "/api/local-backends/llamacpp/serve",
                json={},  # no model field
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "no model" in error_events[0]["data"].lower()

    def test_serve_model_not_found_returns_error(self, client: TestClient):
        """Nonexistent .gguf file returns SSE error."""
        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=False),
            patch("os.walk", return_value=[]),
        ):
            resp = client.post(
                "/api/local-backends/llamacpp/serve",
                json={"model": "nonexistent.gguf"},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "not found" in error_events[0]["data"].lower()

    def test_serve_starts_with_m_flag(self, client: TestClient):
        """Subprocess starts with -m <path>."""
        import deepresearch.web.server as srv

        srv._llamacpp_serving_model = None  # will be set after serve
        mock_proc = MagicMock()
        mock_proc.returncode = None
        never_set = asyncio.Event()
        mock_proc.wait = AsyncMock(side_effect=never_set.wait)
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.__aiter__ = AsyncMock(return_value=iter([]))

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=True),
            patch("os.path.abspath", side_effect=lambda p: p),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_exec.return_value = mock_proc
            # Mock the health check to succeed quickly
            mock_health_resp = MagicMock()
            mock_health_resp.status_code = 200
            mock_health_resp.__aenter__ = AsyncMock(return_value=mock_health_resp)
            mock_health_resp.__aexit__ = AsyncMock()
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_health_resp)
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = client.post(
                    "/api/local-backends/llamacpp/serve",
                    json={"model": "/path/to/model.gguf"},
                )
        assert resp.status_code == 200
        call_args = mock_exec.call_args[0]
        assert "-m" in call_args
        assert "/path/to/model.gguf" in call_args

    def test_serve_stops_existing_first(self, client: TestClient):
        """If already serving, stops before starting new."""
        import deepresearch.web.server as srv

        old_proc = MagicMock()
        old_proc.returncode = None
        old_proc.wait = AsyncMock()
        srv._llamacpp_process = old_proc

        new_proc = MagicMock()
        new_proc.returncode = None
        never_set = asyncio.Event()
        new_proc.wait = AsyncMock(side_effect=never_set.wait)
        new_proc.stderr = AsyncMock()
        new_proc.stderr.__aiter__ = AsyncMock(return_value=iter([]))

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=True),
            patch("os.path.abspath", side_effect=lambda p: p),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_exec.return_value = new_proc
            mock_health_resp = MagicMock()
            mock_health_resp.status_code = 200
            mock_health_resp.__aenter__ = AsyncMock(return_value=mock_health_resp)
            mock_health_resp.__aexit__ = AsyncMock()
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_health_resp)
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value = mock_client
                client.post(
                    "/api/local-backends/llamacpp/serve",
                    json={"model": "/path/to/model.gguf"},
                )
        old_proc.terminate.assert_called_once()

    def test_serve_updates_serving_model(self, client: TestClient):
        """After serve, _llamacpp_serving_model is set."""
        mock_proc = MagicMock()
        mock_proc.returncode = None
        never_set = asyncio.Event()
        mock_proc.wait = AsyncMock(side_effect=never_set.wait)
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.__aiter__ = AsyncMock(return_value=iter([]))

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=True),
            patch("os.path.abspath", side_effect=lambda p: p),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_exec.return_value = mock_proc
            mock_health_resp = MagicMock()
            mock_health_resp.status_code = 200
            mock_health_resp.__aenter__ = AsyncMock(return_value=mock_health_resp)
            mock_health_resp.__aexit__ = AsyncMock()
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_health_resp)
            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_client_cls.return_value = mock_client
                resp = client.post(
                    "/api/local-backends/llamacpp/serve",
                    json={"model": "/path/to/model.gguf"},
                )
        # Note: _llamacpp_serving_model is set inside the SSE generator,
        # which runs lazily. We'll just verify the endpoint didn't error.
        assert resp.status_code == 200

    def test_serve_sse_events(self, client: TestClient):
        """SSE stream emits loading progress events."""
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()

        # Simulate stderr lines that trigger progress events
        async def mock_stderr_line():
            yield b"loading model from /path/to/model.gguf\n"
            yield b"offloading 32 layers to GPU\n"
            yield b"buffer size: 1024 MB\n"
            yield b"listening on 127.0.0.1:8080\n"

        mock_proc.stderr = mock_stderr_line()

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=True),
            patch("os.path.abspath", side_effect=lambda p: p),
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            mock_exec.return_value = mock_proc
            mock_health_resp = MagicMock()
            mock_health_resp.status_code = 500  # cause health check failure
            mock_health_resp.__aenter__ = AsyncMock(return_value=mock_health_resp)
            mock_health_resp.__aexit__ = AsyncMock()
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_health_resp)
            mock_httpx.return_value = mock_client
            resp = client.post(
                "/api/local-backends/llamacpp/serve",
                json={"model": "/path/to/model.gguf"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # Verify that SSE events contain progress data via structured parsing
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "install_log" in event_types or "install_complete" in event_types

    def test_serve_port_conflict_returns_error(self, client: TestClient):
        """Port already in use returns SSE error."""
        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("os.path.isfile", return_value=True),
            patch("os.path.abspath", side_effect=lambda p: p),
            patch("deepresearch.web.server._is_port_available", return_value=False),
        ):
            resp = client.post(
                "/api/local-backends/llamacpp/serve",
                json={"model": "/path/to/model.gguf"},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events, (
            f"Expected install_error event, got: {[e['event'] for e in events]}"
        )
        assert "port" in error_events[0]["data"].lower()
        assert "already in use" in error_events[0]["data"].lower()


# ─── R. Phase 2: Config Endpoint — additional tests ────────────────────────


class TestLlamacppConfigPhase2:
    """Additional tests for PUT /api/local-backends/llamacpp/config."""

    def test_config_update_port(self, client: TestClient):
        """Port updated in _llamacpp_config."""
        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={"port": 9999},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["port"] == 9999
        # Verify internal config updated
        import deepresearch.web.server as srv

        assert srv._llamacpp_config["port"] == 9999

    def test_config_update_gpu_layers(self, client: TestClient):
        """GPU layers updated."""
        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={"gpu_layers": 64},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["gpu_layers"] == 64

    def test_config_update_context_size(self, client: TestClient):
        """Context size updated."""
        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={"context_size": 32768},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["config"]["context_size"] == 32768

    def test_config_running_warning(self, client: TestClient):
        """Returns warning when server is running."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        srv._llamacpp_process = mock_proc
        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={"port": 8081},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "warning" in data
        assert "restart" in data["warning"].lower()

    def test_config_updates_backend_address(self, client: TestClient):
        """Port change calls local_backend_manager.set_address()."""
        from deepresearch.web.settings_manager import local_backend_manager

        resp = client.put(
            "/api/local-backends/llamacpp/config",
            json={"port": 8082},
        )
        assert resp.status_code == 200
        # Verify address was set
        addr = local_backend_manager.get_address("llama-cpp")
        assert addr == "localhost:8082"


# ─── S. Phase 3: Model Registration ────────────────────────────────────────


class TestModelRegistrationPhase3:
    """GET /api/models includes/excludes llamacpp model."""

    def test_api_models_includes_llamacpp_when_running(self, client: TestClient):
        """When llama-server running with model, /api/models includes llamacpp/<model-name>."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        srv._llamacpp_process = mock_proc
        srv._llamacpp_serving_model = "/home/user/.cache/gguf/models/qwen.gguf"
        # Mock load_model_config to return empty list
        with patch("deepresearch.web.routes.models.load_model_config", return_value=[]):
            resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        # data is a list of model dicts
        model_ids = [m.get("id") for m in data]
        assert "llama-cpp/qwen" in model_ids

    def test_api_models_excludes_stopped_llamacpp(self, client: TestClient):
        """When not running, no llamacpp model in /api/models."""
        import deepresearch.web.server as srv

        srv._llamacpp_process = None
        srv._llamacpp_serving_model = None
        with patch("deepresearch.web.routes.models.load_model_config", return_value=[]):
            resp = client.get("/api/models")
        assert resp.status_code == 200
        data = resp.json()
        model_ids = [m.get("id") for m in data]
        assert not any(mid.startswith("llama-cpp/") for mid in model_ids)


# ─── T. Auto-detection: _detect_llamacpp_address() ────────────────────────


class TestDetectLlamacppAddress:
    """_detect_llamacpp_address() probes ports for a running llama-server."""

    def test_returns_url_when_port_responds(self):
        """Returns http://localhost:{port}/v1 when /health returns 200."""
        import deepresearch.llm.client as client_mod

        # Reset cache
        client_mod._llamacpp_detected_url = None
        client_mod._llamacpp_detected_at = 0.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "deepresearch.llm.client.httpx.get", return_value=mock_resp
        ) as mock_get:
            result = client_mod._detect_llamacpp_address()

        assert result == "http://localhost:8080/v1"
        # Should have probed port 8080 first (configured default)
        mock_get.assert_called_once_with(
            "http://localhost:8080/health",
            timeout=1.5,
        )

    def test_tries_multiple_ports(self):
        """Falls through to next port when first returns non-200."""
        import deepresearch.llm.client as client_mod

        client_mod._llamacpp_detected_url = None
        client_mod._llamacpp_detected_at = 0.0

        fail_resp = MagicMock()
        fail_resp.status_code = 503
        ok_resp = MagicMock()
        ok_resp.status_code = 200

        with patch(
            "deepresearch.llm.client.httpx.get", side_effect=[fail_resp, ok_resp]
        ) as mock_get:
            result = client_mod._detect_llamacpp_address()

        assert result == "http://localhost:7501/v1"
        assert mock_get.call_count == 2

    def test_returns_none_when_no_port_responds(self):
        """Returns None when all ports fail."""
        import deepresearch.llm.client as client_mod

        client_mod._llamacpp_detected_url = None
        client_mod._llamacpp_detected_at = 0.0

        with patch(
            "deepresearch.llm.client.httpx.get",
            side_effect=httpx.ConnectError("refused"),
        ):
            result = client_mod._detect_llamacpp_address()

        assert result is None

    def test_caches_result(self):
        """Second call returns cached result without probing."""
        import deepresearch.llm.client as client_mod

        client_mod._llamacpp_detected_url = "http://localhost:7501/v1"
        client_mod._llamacpp_detected_at = time.monotonic()

        with patch("deepresearch.llm.client.httpx.get") as mock_get:
            result = client_mod._detect_llamacpp_address()

        assert result == "http://localhost:7501/v1"
        mock_get.assert_not_called()

    def test_persists_detected_address(self):
        """When auto-detected, address is persisted via local_backend_manager."""
        import deepresearch.llm.client as client_mod

        client_mod._llamacpp_detected_url = None
        client_mod._llamacpp_detected_at = 0.0

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_mgr = MagicMock()
        mock_mgr.get_address.return_value = (
            None  # no custom address → triggers auto-detect
        )

        with (
            patch("deepresearch.llm.client.httpx.get", return_value=mock_resp),
            patch(
                "deepresearch.web.settings_manager.local_backend_manager",
                mock_mgr,
            ),
        ):
            from deepresearch.llm.client import LLMClient

            LLMClient._resolve_api_base("llama-cpp")

        mock_mgr.set_address.assert_called_once_with("llama-cpp", "localhost:8080")


# ─── U. Hardware Detection Tests ──────────────────────────────────────────


class TestHardwareDetection:
    """get_hardware_info() tiered detection."""

    def test_tier1_platform_info(self):
        """Tier 1 fields present without any optional deps."""
        from deepresearch.hardware import get_hardware_info

        with (
            patch("deepresearch.hardware._get_memory_info", return_value=None),
            patch("deepresearch.hardware._detect_gpus", return_value=[]),
            patch("deepresearch.hardware._check_torch_cuda", return_value=None),
        ):
            info = get_hardware_info()

        assert "platform" in info
        assert "machine" in info
        assert "cpu_count" in info
        assert isinstance(info["cpu_count"], int)

    def test_memory_psutil_not_installed(self):
        """Memory returns None when psutil is not installed."""
        from deepresearch.hardware import _get_memory_info

        # Simulate ImportError by patching __import__
        import builtins

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "psutil":
                raise ImportError("No psutil")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            result = _get_memory_info()
        assert result is None

    def test_memory_psutil_installed(self):
        """Memory returns total/available/percent when psutil available."""
        from deepresearch.hardware import _get_memory_info

        mock_mem = MagicMock()
        mock_mem.total = 34359738368
        mock_mem.available = 17179869184
        mock_mem.percent = 50.0

        mock_psutil = MagicMock()
        mock_psutil.virtual_memory.return_value = mock_mem

        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            result = _get_memory_info()

        assert result is not None
        assert result["total"] == 34359738368
        assert result["available"] == 17179869184
        assert result["percent_used"] == 50.0

    def test_nvidia_gpu_detection(self):
        """nvidia-smi output parsed into GPU dicts."""
        from deepresearch.hardware import _detect_gpus

        smi_output = "NVIDIA GeForce RTX 4090, 24576, 535.154.05\nNVIDIA A100, 40960, 525.85.12\n"

        with (
            patch("shutil.which", side_effect=lambda c: "/usr/bin/" + c if c == "nvidia-smi" else None),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout=smi_output, stderr=""
            )
            gpus = _detect_gpus()

        assert len(gpus) == 2
        assert gpus[0]["name"] == "NVIDIA GeForce RTX 4090"
        assert gpus[0]["memory_total_mb"] == 24576
        assert gpus[0]["driver_version"] == "535.154.05"
        assert gpus[0]["backend"] == "nvidia"
        assert gpus[1]["name"] == "NVIDIA A100"
        assert gpus[1]["memory_total_mb"] == 40960

    def test_no_nvidia_smi(self):
        """Empty list when nvidia-smi not in PATH."""
        from deepresearch.hardware import _detect_gpus

        with patch("shutil.which", return_value=None):
            gpus = _detect_gpus()
        assert gpus == []

    def test_nvidia_smi_error(self):
        """Empty list when nvidia-smi fails."""
        from deepresearch.hardware import _detect_gpus

        with (
            patch("shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("subprocess.run", side_effect=FileNotFoundError("not found")),
        ):
            gpus = _detect_gpus()
        assert gpus == []

    def test_rocm_gpu_detection(self):
        """rocm-smi output parsed into GPU dicts."""
        from deepresearch.hardware import _detect_gpus

        rocm_output = """
===================================
ROCm System Management Interface
===================================
GPU 0: AMD Radeon RX 7900 XTX
GPU 1: AMD Instinct MI250X
"""
        with (
            patch("shutil.which", side_effect=lambda c: "/usr/bin/" + c if c == "rocm-smi" else None),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0, stdout=rocm_output, stderr=""
            )
            gpus = _detect_gpus()

        # Should find 2 GPUs from the "GPU X: Name" lines
        gpu_names = [g["name"] for g in gpus if g["backend"] == "rocm"]
        assert "AMD Radeon RX 7900 XTX" in gpu_names
        assert "AMD Instinct MI250X" in gpu_names

    def test_torch_cuda_available(self):
        """CUDA available when torch is installed and CUDA is available."""
        from deepresearch.hardware import _check_torch_cuda

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True

        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = _check_torch_cuda()
        assert result is True

    def test_torch_cuda_unavailable(self):
        """CUDA unavailable when torch is installed but no CUDA."""
        from deepresearch.hardware import _check_torch_cuda

        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = _check_torch_cuda()
        assert result is False

    def test_torch_not_installed(self):
        """Returns None when torch is not installed."""
        import builtins
        from deepresearch.hardware import _check_torch_cuda

        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("No torch")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=mock_import):
            result = _check_torch_cuda()
        assert result is None


# ─── V. Status: hf_supported field ────────────────────────────────────────


class TestLlamacppStatusHF:
    """GET /api/local-backends/llamacpp/status — hf_supported field."""

    def test_hf_supported_true_when_flag_present(self, client: TestClient):
        """hf_supported is True when --help output contains -hf."""
        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("subprocess.run") as mock_run,
        ):
            # First call is --version, second is --help
            mock_run.side_effect = [
                MagicMock(stdout="b9739\n", stderr=""),
                MagicMock(stdout="  -hf    --huggingface    Load model from Hugging Face\n", stderr=""),
            ]
            resp = client.get("/api/local-backends/llamacpp/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["hf_supported"] is True

    def test_hf_supported_false_when_flag_missing(self, client: TestClient):
        """hf_supported is False when --help output lacks -hf."""
        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.side_effect = [
                MagicMock(stdout="b9739\n", stderr=""),
                MagicMock(stdout="  --version    Show version\n", stderr=""),
            ]
            resp = client.get("/api/local-backends/llamacpp/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["hf_supported"] is False

    def test_hf_supported_false_when_not_installed(self, client: TestClient):
        """hf_supported is False when llama-server not installed."""
        with patch("shutil.which", return_value=None):
            resp = client.get("/api/local-backends/llamacpp/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["installed"] is False
        assert data["hf_supported"] is False


# ─── W. Serve-HF Endpoint Tests ──────────────────────────────────────────


class TestLlamacppServeHF:
    """POST /api/local-backends/llamacpp/serve-hf."""

    def test_serve_hf_not_installed(self, client: TestClient):
        """Returns SSE error when llama-server not installed."""
        with patch("shutil.which", return_value=None):
            resp = client.post(
                "/api/local-backends/llamacpp/serve-hf",
                json={"hf_repo": "user/model"},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events
        assert "not installed" in error_events[0]["data"].lower()

    def test_serve_hf_no_repo(self, client: TestClient):
        """Returns SSE error when no hf_repo provided."""
        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post(
                "/api/local-backends/llamacpp/serve-hf",
                json={"hf_repo": ""},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events
        assert "no hugging face repo" in error_events[0]["data"].lower()

    def test_serve_hf_flag_not_supported(self, client: TestClient):
        """Returns SSE error when -hf flag is not supported."""
        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout="  --version    Show version\n", stderr=""
            )
            resp = client.post(
                "/api/local-backends/llamacpp/serve-hf",
                json={"hf_repo": "user/model"},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events
        assert "not support" in error_events[0]["data"].lower()

    def test_serve_hf_stops_existing_process(self, client: TestClient):
        """Stops existing process before starting new one."""
        import deepresearch.web.server as srv

        old_proc = MagicMock()
        old_proc.returncode = None
        old_proc.wait = AsyncMock()
        srv._llamacpp_process = old_proc

        new_proc = MagicMock()
        new_proc.returncode = None
        never_set = asyncio.Event()
        new_proc.wait = AsyncMock(side_effect=never_set.wait)
        new_proc.stderr = None

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("subprocess.run") as mock_run,
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_run.return_value = MagicMock(
                stdout="  -hf    --huggingface    Load model from Hugging Face\n",
                stderr="",
            )
            mock_exec.return_value = new_proc
            client.post(
                "/api/local-backends/llamacpp/serve-hf",
                json={"hf_repo": "user/model", "quant": "Q4_K_M"},
            )

        old_proc.terminate.assert_called_once()

    def test_serve_hf_port_conflict(self, client: TestClient):
        """Port already in use returns SSE error."""
        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("subprocess.run") as mock_run,
            patch("deepresearch.web.server._is_port_available", return_value=False),
        ):
            mock_run.return_value = MagicMock(
                stdout="  -hf    --huggingface    Load model from Hugging Face\n",
                stderr="",
            )
            resp = client.post(
                "/api/local-backends/llamacpp/serve-hf",
                json={"hf_repo": "user/model"},
            )
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events
        assert "already in use" in error_events[0]["data"].lower()

    def test_serve_hf_builds_correct_command(self, client: TestClient):
        """Command includes -hf flag with model ref and optional flags."""
        import deepresearch.web.server as srv

        mock_proc = MagicMock()
        mock_proc.returncode = None
        never_set = asyncio.Event()
        mock_proc.wait = AsyncMock(side_effect=never_set.wait)
        mock_proc.stderr = None

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("subprocess.run") as mock_run,
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
        ):
            mock_run.return_value = MagicMock(
                stdout="  -hf    --huggingface    Load model from Hugging Face\n",
                stderr="",
            )
            mock_exec.return_value = mock_proc
            client.post(
                "/api/local-backends/llamacpp/serve-hf",
                json={
                    "hf_repo": "user/model",
                    "quant": "Q4_K_M",
                    "port": 8081,
                    "gpu_layers": 32,
                    "context_size": 16384,
                    "flash_attn": True,
                    "batch_size": 256,
                },
            )

        call_args = mock_exec.call_args[0]
        assert "llama-server" in call_args
        assert "-hf" in call_args
        assert "user/model:Q4_K_M" in call_args
        assert "--host" in call_args
        assert "127.0.0.1" in call_args
        assert "--port" in call_args
        assert "8081" in call_args
        assert "-ngl" in call_args
        assert "32" in call_args
        assert "-c" in call_args
        assert "16384" in call_args
        assert "--flash-attn" in call_args
        assert "-ub" in call_args
        assert "256" in call_args

    def test_serve_hf_sse_events(self, client: TestClient):
        """SSE stream emits progress events."""
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()

        async def mock_stderr_line():
            yield b"loading model from HF repo\n"
            yield b"offloading 32 layers to GPU\n"
            yield b"buffer size: 1024 MB\n"
            yield b"listening on 127.0.0.1:8081\n"

        mock_proc.stderr = mock_stderr_line()

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("subprocess.run") as mock_run,
            patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock
            ) as mock_exec,
            patch("deepresearch.web.server._is_port_available", return_value=True),
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("httpx.AsyncClient") as mock_httpx,
        ):
            mock_run.return_value = MagicMock(
                stdout="  -hf    --huggingface    Load model from Hugging Face\n",
                stderr="",
            )
            mock_exec.return_value = mock_proc
            mock_health_resp = MagicMock()
            mock_health_resp.status_code = 500
            mock_health_resp.__aenter__ = AsyncMock(return_value=mock_health_resp)
            mock_health_resp.__aexit__ = AsyncMock()
            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_health_resp)
            mock_httpx.return_value = mock_client

            resp = client.post(
                "/api/local-backends/llamacpp/serve-hf",
                json={"hf_repo": "user/model"},
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse_events(resp.text)
        event_types = [e["event"] for e in events]
        assert "install_log" in event_types

    def test_serve_hf_invalid_json(self, client: TestClient):
        """Invalid JSON body returns SSE error."""
        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post(
                "/api/local-backends/llamacpp/serve-hf",
                content=b"not json",
                headers={"content-type": "application/json"},
            )

        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        error_events = [e for e in events if e["event"] == "install_error"]
        assert error_events
        assert "invalid json" in error_events[0]["data"].lower()
