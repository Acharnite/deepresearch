"""Tests for local backend discovery, management, and model download endpoints.

Covers:
  - GET /api/local-backends – auto-discovery of 5 backends
  - PUT /api/local-backends/{name}/address – set custom address
  - GET /api/local-backends/{name}/address – get custom address
  - POST /api/local-backends/{name}/test – test connectivity
  - POST /api/local-backends/models/download – model download (SSE)
  - GET /api/local-backends/models/download/progress – download progress
  - GET /api/tools/status – llmfit installation status
  - GET /api/tools/recommendations – model recommendations
  - GET /api/hardware – system hardware info
"""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from deepresearch.web.server import app
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
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current_data_lines.append(line[len("data:"):].strip())
        elif line == "":
            # Blank line = end of event
            if current_data_lines:
                events.append({
                    "event": current_event,
                    "data": "\n".join(current_data_lines),
                })
            current_event = "message"
            current_data_lines = []
    # Flush last event if body doesn't end with blank line
    if current_data_lines:
        events.append({
            "event": current_event,
            "data": "\n".join(current_data_lines),
        })
    return events


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_llamacpp_globals():
    """Reset llamacpp global state to prevent cross-test leakage."""
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
    srv._llamacpp_config = {"port": 8080, "installed": False, "gpu_layers": 0, "context_size": 8192, "flash_attn": False}
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


class TestBackendRoutes:
    """Local-backend, tools, and hardware route registration."""

    def test_new_routes_registered(self, client: TestClient) -> None:
        """All new local-backend, tools, and hardware routes are registered."""
        routes = get_all_paths(app)
        expected = [
            "/api/local-backends",
            "/api/local-backends/{name}/address",
            "/api/local-backends/{name}/test",
            "/api/local-backends/models/download",
            "/api/local-backends/models/download/progress",
            "/api/local-backends/ollama/status",
            "/api/local-backends/ollama/install",
            "/api/local-backends/ollama/start",
            "/api/local-backends/ollama/stop",
            "/api/local-backends/ollama/uninstall",
            "/api/local-backends/ollama/pull",
            "/api/local-backends/llmfit/install",
            "/api/local-backends/llmfit/uninstall",
            "/api/local-backends/llamacpp/status",
            "/api/local-backends/llamacpp/install",
            "/api/local-backends/llamacpp/uninstall",
            "/api/local-backends/llamacpp/start",
            "/api/local-backends/llamacpp/stop",
            "/api/local-backends/llamacpp/restart",
            "/api/tools/status",
            "/api/tools/recommendations",
            "/api/hardware",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"


class TestBackendListing:
    """GET /api/local-backends — auto-discovery of 5 backends."""

    def test_list_returns_json(self, client: TestClient) -> None:
        """GET /api/local-backends returns JSON with backends list."""
        resp = client.get("/api/local-backends")
        assert resp.status_code == 200
        data = resp.json()
        assert "backends" in data
        assert isinstance(data["backends"], list)

    def test_list_contains_all_five(self, client: TestClient) -> None:
        """GET /api/local-backends contains all 5 backend entries."""
        resp = client.get("/api/local-backends")
        data = resp.json()
        names = {b["name"] for b in data["backends"]}
        expected = {"ollama", "llama-cpp", "vllm", "lm-studio", "local-ai"}
        assert names == expected

    def test_list_entries_have_required_fields(self, client: TestClient) -> None:
        """Each backend entry has name, running, port fields."""
        resp = client.get("/api/local-backends")
        data = resp.json()
        for backend in data["backends"]:
            assert "name" in backend, f"Missing 'name' in {backend}"
            assert "running" in backend, f"Missing 'running' in {backend}"
            assert "port" in backend, f"Missing 'port' in {backend}"
            assert isinstance(backend["running"], bool)
            assert isinstance(backend["port"], int)
            assert "custom_address" in backend

    def test_list_port_values(self, client: TestClient) -> None:
        """Each backend has the expected default port."""
        resp = client.get("/api/local-backends")
        data = resp.json()
        ports = {b["name"]: b["port"] for b in data["backends"]}
        assert ports["ollama"] == 11434
        assert ports["llama-cpp"] == 8080
        assert ports["vllm"] == 8000
        assert ports["lm-studio"] == 1234
        assert ports["local-ai"] == 8080


class TestBackendAddress:
    """PUT /api/local-backends/{name}/address and GET /api/local-backends/{name}/address."""

    def test_set_address_valid(self, client: TestClient) -> None:
        """PUT with valid host:port returns success."""
        resp = client.put(
            "/api/local-backends/ollama/address",
            json={"address": "192.168.1.100:11434"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["name"] == "ollama"
        assert data["address"] == "192.168.1.100:11434"

    def test_set_address_invalid_format(self, client: TestClient) -> None:
        """PUT with invalid address format returns 400."""
        resp = client.put(
            "/api/local-backends/ollama/address",
            json={"address": "not-a-valid-address"},
        )
        assert resp.status_code == 400

    def test_set_address_unknown_backend(self, client: TestClient) -> None:
        """PUT with unknown backend name returns 404."""
        resp = client.put(
            "/api/local-backends/unknown-backend/address",
            json={"address": "localhost:9999"},
        )
        assert resp.status_code == 404
        assert "Unknown backend" in resp.json()["message"]

    def test_set_address_persists(self, client: TestClient) -> None:
        """Address set via PUT is returned by GET."""
        put_resp = client.put(
            "/api/local-backends/ollama/address",
            json={"address": "10.0.0.1:11434"},
        )
        assert put_resp.status_code == 200

        get_resp = client.get("/api/local-backends/ollama/address")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["address"] == "10.0.0.1:11434"

    def test_get_address_not_set(self, client: TestClient) -> None:
        """GET address for a backend with no custom address returns null."""
        resp = client.get("/api/local-backends/llama-cpp/address")
        assert resp.status_code == 200
        data = resp.json()
        assert data["address"] is None

    def test_get_address_unknown_backend(self, client: TestClient) -> None:
        """GET address for unknown backend returns 404."""
        resp = client.get("/api/local-backends/ghost/address")
        assert resp.status_code == 404
        assert "Unknown backend" in resp.json()["message"]


class TestBackendTest:
    """POST /api/local-backends/{name}/test — connectivity tests."""

    def test_backend_known_returns_json(self, client: TestClient) -> None:
        """POST test for a known backend returns JSON with status field."""
        resp = client.post("/api/local-backends/ollama/test")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data

    def test_backend_unknown_returns_404(self, client: TestClient) -> None:
        """POST test for unknown backend returns 404."""
        resp = client.post("/api/local-backends/nonexistent/test")
        assert resp.status_code == 404
        assert "Unknown backend" in resp.json()["message"]


class TestBackendDownload:
    """POST /api/local-backends/models/download — model download (SSE)."""

    def test_invalid_body_returns_error(self, client: TestClient) -> None:
        """POST download with missing required fields returns 422."""
        resp = client.post(
            "/api/local-backends/models/download",
            json={},
        )
        assert resp.status_code == 422

    def test_valid_body_returns_sse(self, client: TestClient) -> None:
        """POST download with valid body returns SSE response."""
        resp = client.post(
            "/api/local-backends/models/download",
            json={"name": "test-model", "download_type": "ollama"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

    def test_auto_mode_returns_sse(self, client: TestClient) -> None:
        """POST download with auto download_type returns SSE."""
        resp = client.post(
            "/api/local-backends/models/download",
            json={"name": "test-model", "download_type": "auto"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("text/event-stream")


class TestBackendDownloadProgress:
    """GET /api/local-backends/models/download/progress — download progress."""

    def test_returns_json(self, client: TestClient) -> None:
        """GET download/progress returns JSON with download state."""
        resp = client.get("/api/local-backends/models/download/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert "active" in data
        assert "model" in data
        assert "progress" in data
        assert "message" in data
        assert "status" in data
        assert "log" in data

    def test_has_expected_fields(self, client: TestClient) -> None:
        """GET download/progress returns all expected state fields."""
        resp = client.get("/api/local-backends/models/download/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("active"), bool)
        assert isinstance(data.get("model"), str)
        assert isinstance(data.get("progress"), (int, float))
        assert isinstance(data.get("message"), str)
        assert isinstance(data.get("status"), str)
        assert isinstance(data.get("log"), list)


class TestBackendTools:
    """GET /api/tools/status and GET /api/tools/recommendations."""

    def test_tools_status_returns_json(self, client: TestClient) -> None:
        """GET /api/tools/status returns JSON with llmfit status."""
        resp = client.get("/api/tools/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "llmfit" in data
        assert "installed" in data["llmfit"]

    def test_tools_recommendations_returns_json(self, client: TestClient) -> None:
        """GET /api/tools/recommendations returns JSON."""
        resp = client.get("/api/tools/recommendations")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data


class TestBackendHardware:
    """GET /api/hardware — system hardware info."""

    def test_hardware_returns_json(self, client: TestClient) -> None:
        """GET /api/hardware returns JSON with available flag."""
        resp = client.get("/api/hardware")
        assert resp.status_code == 200
        data = resp.json()
        assert "available" in data


# ── llama.cpp Binary Lifecycle Endpoint Tests ──────────────────────────


class TestLlamaCppPlatformDetection:
    """Unit tests for _detect_llamacpp_platform and _build_llamacpp_download_url."""

    def _platform_patch(self, system: str, machine: str):
        """Context manager that patches platform.system/machine and shutil.which.

        These are imported *inside* _detect_llamacpp_platform (``import platform as _platform``,
        ``import shutil as _shutil``), so we patch the top-level module names.
        """
        from contextlib import ExitStack
        from unittest.mock import patch

        stack = ExitStack()
        stack.enter_context(patch("platform.system", return_value=system))
        stack.enter_context(patch("platform.machine", return_value=machine))
        stack.enter_context(patch("shutil.which", return_value=None))
        return stack

    def test_detect_platform_macos_arm64(self) -> None:
        """_detect_llamacpp_platform returns macos-arm64 on macOS arm64."""
        from deepresearch.web.server import _detect_llamacpp_platform

        with self._platform_patch("darwin", "arm64"):
            result = _detect_llamacpp_platform()
            assert result["asset"] == "macos-arm64"
            assert result["ext"] == "tar.gz"

    def test_detect_platform_macos_x64(self) -> None:
        """_detect_llamacpp_platform returns macos-x64 on macOS x86_64."""
        from deepresearch.web.server import _detect_llamacpp_platform

        with self._platform_patch("darwin", "x86_64"):
            result = _detect_llamacpp_platform()
            assert result["asset"] == "macos-x64"
            assert result["ext"] == "tar.gz"

    def test_detect_platform_linux_x64(self) -> None:
        """_detect_llamacpp_platform returns ubuntu-x64 on Linux x86_64."""
        from deepresearch.web.server import _detect_llamacpp_platform

        with self._platform_patch("linux", "x86_64"):
            result = _detect_llamacpp_platform()
            assert result["asset"] == "ubuntu-x64"
            assert result["ext"] == "tar.gz"

    def test_detect_platform_linux_arm64(self) -> None:
        """_detect_llamacpp_platform returns ubuntu-arm64 on Linux aarch64."""
        from deepresearch.web.server import _detect_llamacpp_platform

        with self._platform_patch("linux", "aarch64"):
            result = _detect_llamacpp_platform()
            assert result["asset"] == "ubuntu-arm64"
            assert result["ext"] == "tar.gz"

    def test_detect_platform_windows(self) -> None:
        """_detect_llamacpp_platform returns win-cpu-x64 on Windows."""
        from deepresearch.web.server import _detect_llamacpp_platform

        with self._platform_patch("windows", "amd64"):
            result = _detect_llamacpp_platform()
            assert result["asset"] == "win-cpu-x64"
            assert result["ext"] == "zip"

    def test_detect_platform_unsupported_raises(self) -> None:
        """_detect_llamacpp_platform raises RuntimeError for unknown platform."""
        import pytest
        from deepresearch.web.server import _detect_llamacpp_platform

        with self._platform_patch("beos", "hppa"):
            with pytest.raises(RuntimeError, match="Unsupported platform"):
                _detect_llamacpp_platform()

    def test_build_download_url(self) -> None:
        """_build_llamacpp_download_url constructs the correct GitHub URL."""
        from deepresearch.web.server import _build_llamacpp_download_url

        platform_info = {"asset": "ubuntu-x64", "ext": "tar.gz"}
        url = _build_llamacpp_download_url("b9739", platform_info)
        expected = (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            "b9739/llama-b9739-bin-ubuntu-x64.tar.gz"
        )
        assert url == expected

    def test_build_download_url_windows(self) -> None:
        """_build_llamacpp_download_url constructs URL with .zip for Windows."""
        from deepresearch.web.server import _build_llamacpp_download_url

        platform_info = {"asset": "win-cpu-x64", "ext": "zip"}
        url = _build_llamacpp_download_url("b9739", platform_info)
        expected = (
            "https://github.com/ggml-org/llama.cpp/releases/download/"
            "b9739/llama-b9739-bin-win-cpu-x64.zip"
        )
        assert url == expected


class TestLlamaCppStatusEndpoint:
    """GET /api/local-backends/llamacpp/status."""

    def test_status_returns_json(self, client: TestClient) -> None:
        """GET /api/local-backends/llamacpp/status returns JSON with expected keys."""
        resp = client.get("/api/local-backends/llamacpp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "installed" in data
        assert "running" in data
        assert "version" in data

    def test_status_fields_are_typed(self, client: TestClient) -> None:
        """Status fields have the correct types."""
        resp = client.get("/api/local-backends/llamacpp/status")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["installed"], bool)
        assert isinstance(data["running"], bool)
        assert data["version"] is None or isinstance(data["version"], str)


class TestLlamaCppInstallEndpoint:
    """POST /api/local-backends/llamacpp/install."""

    def test_install_already_installed(self, client: TestClient) -> None:
        """POST /install returns error SSE event when already installed."""
        import json
        from unittest.mock import patch

        with patch("shutil.which", return_value="/usr/bin/llama-server"):
            resp = client.post("/api/local-backends/llamacpp/install")
            assert resp.status_code == 200
            assert resp.headers.get("content-type", "").startswith("text/event-stream")

            events = _parse_sse_events(resp.text)
            error_events = [e for e in events if e["event"] == "install_error"]
            assert error_events, f"Expected install_error event, got: {[e['event'] for e in events]}"
            data = json.loads(error_events[0]["data"])
            assert data["status"] == "error"
            assert "already installed" in data["message"].lower()
            assert data["code"] == "ALREADY_INSTALLED"

    def test_install_sse_events_when_not_installed(self, client: TestClient) -> None:
        """POST /install produces expected SSE event types when not installed."""
        from unittest.mock import patch, AsyncMock, MagicMock, mock_open
        import json

        # Patch detect to avoid platform dependency
        platform_info = {"asset": "ubuntu-x64", "ext": "tar.gz"}

        # Mock httpx.AsyncClient for GitHub tag API call + download
        mock_tag_resp = MagicMock()
        mock_tag_resp.status_code = 200
        mock_tag_resp.json = MagicMock(return_value={"tag_name": "b9999"})
        mock_tag_resp.__aenter__ = AsyncMock(return_value=mock_tag_resp)
        mock_tag_resp.__aexit__ = AsyncMock()

        # Mock the download streaming response
        mock_stream_resp = MagicMock()
        mock_stream_resp.status_code = 200
        mock_stream_resp.headers = {"content-length": "1000"}
        mock_stream_resp.__aenter__ = AsyncMock(return_value=mock_stream_resp)
        mock_stream_resp.__aexit__ = AsyncMock()

        async def mock_aiter_bytes():
            yield b"x" * 1000

        mock_stream_resp.aiter_bytes = mock_aiter_bytes

        # Side effect: first call is tag API, second is download stream
        async def mock_get_side_effect(url, **kwargs):
            if "releases/latest" in url:
                return mock_tag_resp
            return mock_stream_resp

        mock_client_instance = AsyncMock()
        mock_client_instance.get = AsyncMock(side_effect=mock_get_side_effect)
        mock_client_instance.stream = MagicMock(return_value=mock_stream_resp)
        mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
        mock_client_instance.__aexit__ = AsyncMock()

        # Mock tarfile extraction — the download data is not a real tar.gz
        mock_tar = MagicMock()
        mock_tar.__enter__ = MagicMock(return_value=mock_tar)
        mock_tar.__exit__ = MagicMock(return_value=None)
        mock_member = MagicMock()
        mock_member.name = "llama-b9999/llama-server"
        mock_member.isdir.return_value = False
        mock_tar.getmembers.return_value = [mock_member]

        # Mock subprocess for version check after install
        mock_ver_result = MagicMock()
        mock_ver_result.stdout = "version 1.0"
        mock_ver_result.stderr = ""

        # shutil.which is called: (1) pre-check → None, (2) verify → path,
        # (3) path yield → path. Return the path for calls 2+.
        which_calls = [None, "/home/user/.local/bin/llama-server"]

        def _which_side_effect(x):
            if x == "llama-server":
                return which_calls.pop(0) if which_calls else "/home/user/.local/bin/llama-server"
            return None

        # IMPORTANT: SSE generators run lazily. The with block MUST stay
        # active while consuming response lines so patches remain applied.
        with (
            patch("shutil.which", side_effect=_which_side_effect),
            patch("deepresearch.web.server._detect_llamacpp_platform", return_value=platform_info),
            patch("httpx.AsyncClient", return_value=mock_client_instance),
            patch("tarfile.open", return_value=mock_tar),
            patch("os.path.exists", return_value=True),
            patch("os.remove"),
            patch("os.makedirs"),
            patch("os.chmod"),
            patch("subprocess.run", return_value=mock_ver_result),
        ):
            resp = client.post("/api/local-backends/llamacpp/install")
            assert resp.status_code == 200
            assert resp.headers.get("content-type", "").startswith("text/event-stream")

            # Consume the SSE stream with patches still active
            events = _parse_sse_events(resp.text)
            event_types = [e["event"] for e in events]

            assert "install_log" in event_types, f"Expected install_log events, got: {event_types}"
            # Should end with install_complete
            assert "install_complete" in event_types, (
                f"Expected install_complete event, got: {event_types}"
            )
            assert "install_error" not in event_types, (
                f"Unexpected install_error in: {event_types}"
            )


class TestLlamaCppUninstallEndpoint:
    """POST /api/local-backends/llamacpp/uninstall."""

    def test_uninstall_not_installed(self, client: TestClient) -> None:
        """POST /uninstall returns error SSE event when not installed."""
        from unittest.mock import patch
        import json

        with patch("shutil.which", return_value=None):
            resp = client.post("/api/local-backends/llamacpp/uninstall")
            assert resp.status_code == 200
            assert resp.headers.get("content-type", "").startswith("text/event-stream")

            events = _parse_sse_events(resp.text)
            error_events = [e for e in events if e["event"] == "install_error"]
            assert error_events, f"Expected install_error event, got: {[e['event'] for e in events]}"
            data = json.loads(error_events[0]["data"])
            assert data["code"] == "NOT_INSTALLED"
            assert "not installed" in data["message"].lower()


class TestLlamaCppStartEndpoint:
    """POST /api/local-backends/llamacpp/start."""

    def test_start_not_installed(self, client: TestClient) -> None:
        """POST /start returns error when llama-server is not installed."""
        from unittest.mock import patch

        with patch("shutil.which", return_value=None):
            resp = client.post("/api/local-backends/llamacpp/start")
            assert resp.status_code == 400
            data = resp.json()
            assert data["status"] == "error"
            assert "not installed" in data["message"].lower()

    def test_start_already_running(self, client: TestClient) -> None:
        """POST /start returns ok with 'already running' when process exists."""
        from unittest.mock import patch, MagicMock

        mock_process = MagicMock()
        mock_process.returncode = None

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("deepresearch.web.server._llamacpp_process", mock_process),
        ):
            resp = client.post("/api/local-backends/llamacpp/start")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "already running" in data["message"].lower()

    def test_start_success(self, client: TestClient) -> None:
        """POST /start returns ok when starting succeeds."""
        from unittest.mock import patch, MagicMock, AsyncMock
        import deepresearch.web.server as server_mod

        mock_process = MagicMock()
        mock_process.returncode = None

        mock_http_resp = MagicMock()
        mock_http_resp.status_code = 200

        mock_http_client = MagicMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_http_resp)

        with (
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch.object(server_mod, "_llamacpp_process", None),
            patch.object(server_mod, "_llamacpp_serving_model", "/path/to/model.gguf"),
            patch.object(server_mod, "_is_port_available", return_value=True),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)),
            patch("asyncio.sleep", AsyncMock()),
            patch("httpx.AsyncClient", return_value=mock_http_client),
        ):
            resp = client.post("/api/local-backends/llamacpp/start")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "started" in data["message"].lower()


class TestLlamaCppStopEndpoint:
    """POST /api/local-backends/llamacpp/stop."""

    def test_stop_no_process(self, client: TestClient) -> None:
        """POST /stop returns ok with 'not running' when no process."""
        from unittest.mock import patch

        with patch("deepresearch.web.server._llamacpp_process", None):
            resp = client.post("/api/local-backends/llamacpp/stop")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "not running" in data["message"].lower()

    def test_stop_success(self, client: TestClient) -> None:
        """POST /stop returns ok with 'stopped' when process is running."""
        from unittest.mock import patch, MagicMock, AsyncMock
        import deepresearch.web.server as server_mod

        mock_process = MagicMock()
        mock_process.returncode = None

        with (
            patch.object(server_mod, "_llamacpp_process", mock_process),
            patch("asyncio.wait_for", AsyncMock()),
        ):
            resp = client.post("/api/local-backends/llamacpp/stop")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert "stopped" in data["message"].lower()


class TestLlamaCppRestartEndpoint:
    """POST /api/local-backends/llamacpp/restart."""

    def test_restart_not_installed(self, client: TestClient) -> None:
        """POST /restart returns error when llama-server is not installed."""
        from unittest.mock import patch

        with (
            patch("shutil.which", return_value=None),
            patch("deepresearch.web.server._llamacpp_process", None),
        ):
            resp = client.post("/api/local-backends/llamacpp/restart")
            # Stop succeeds (not running), start fails (not installed)
            assert resp.status_code == 400
            data = resp.json()
            assert data["status"] == "error"
            assert "not installed" in data["message"].lower()

    def test_restart_success(self, client: TestClient) -> None:
        """POST /restart returns ok when stop and start both succeed."""
        from unittest.mock import patch, MagicMock, AsyncMock
        import deepresearch.web.server as server_mod

        mock_process = MagicMock()
        mock_process.returncode = None

        mock_http_resp = MagicMock()
        mock_http_resp.status_code = 200

        mock_http_client = MagicMock()
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock()
        mock_http_client.get = AsyncMock(return_value=mock_http_resp)

        with (
            patch.object(server_mod, "_llamacpp_process", mock_process),
            patch.object(server_mod, "_llamacpp_serving_model", "/path/to/model.gguf"),
            patch.object(server_mod, "_is_port_available", return_value=True),
            patch("shutil.which", return_value="/usr/bin/llama-server"),
            patch("asyncio.wait_for", AsyncMock()),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)),
            patch("asyncio.sleep", AsyncMock()),
            patch("httpx.AsyncClient", return_value=mock_http_client),
        ):
            resp = client.post("/api/local-backends/llamacpp/restart")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"


# ── SettingsManager Unit Tests ──────────────────────────────────────────


class TestSettingsManager:
    """Unit tests for SettingsManager edge cases (empty/corrupt config)."""

    def test_get_keys_when_no_env_file(self) -> None:
        """``get_keys()`` returns all providers as ``configured=False`` when no .env exists."""
        import os
        from unittest.mock import patch

        from deepresearch.web.settings_manager import PROVIDERS, SettingsManager

        # Unset all provider env vars and disable .env file reads so they appear unconfigured.
        env_vars = [info["env_var"] for info in PROVIDERS.values()]
        with (
            patch.dict(os.environ, {v: "" for v in env_vars}, clear=False),
            patch.object(SettingsManager, "_get_from_file", return_value=None),
        ):
            mgr = SettingsManager()
            keys = mgr.get_keys()
        assert isinstance(keys, dict)
        for provider_id, info in keys.items():
            assert info["configured"] is False, f"{provider_id} expected unconfigured"
            assert info["has_key"] is False
            assert info["key_preview"] is None

    def test_get_local_endpoints_no_file(self) -> None:
        """``get_local_endpoints()`` returns empty list when file does not exist."""
        from deepresearch.web.settings_manager import SettingsManager

        mgr = SettingsManager()
        assert mgr.get_local_endpoints() == []

    def test_get_local_endpoints_corrupted_json(self, tmp_path) -> None:
        """``get_local_endpoints()`` returns empty list when JSON is corrupted."""
        from deepresearch.web.settings_manager import SettingsManager

        mgr = SettingsManager()
        mgr._endpoints_path = tmp_path / "local_endpoints.json"
        mgr._endpoints_path.write_text("{invalid json}")
        endpoints = mgr.get_local_endpoints()
        assert endpoints == []

    def test_get_local_endpoints_empty_json(self, tmp_path) -> None:
        """``get_local_endpoints()`` returns empty list when JSON is empty array."""
        from deepresearch.web.settings_manager import SettingsManager

        mgr = SettingsManager()
        mgr._endpoints_path = tmp_path / "local_endpoints.json"
        mgr._endpoints_path.write_text("[]")
        assert mgr.get_local_endpoints() == []

    def test_get_local_endpoints_not_a_list(self, tmp_path) -> None:
        """``get_local_endpoints()`` returns empty list when JSON root is not a list.

        The method only catches JSONDecodeError; a valid JSON non-list value
        is returned as-is (it's up to the caller to handle the type).
        """
        from deepresearch.web.settings_manager import SettingsManager

        mgr = SettingsManager()
        mgr._endpoints_path = tmp_path / "local_endpoints.json"
        # Valid JSON but not a list — this is an accepted edge case
        mgr._endpoints_path.write_text('"string"')
        result = mgr.get_local_endpoints()
        # A valid JSON string is returned as-is (no JSONDecodeError)
        assert result == "string"

    def test_set_key_unknown_provider_raises(self) -> None:
        """``set_key()`` with unknown provider raises ``ValueError``."""
        from deepresearch.web.settings_manager import SettingsManager

        mgr = SettingsManager()
        with pytest.raises(ValueError, match="Unknown provider"):
            mgr.set_key("nonexistent_provider", "sk-...")

    def test_delete_key_unknown_provider_safe(self) -> None:
        """``delete_key()`` with unknown provider does not raise."""
        from deepresearch.web.settings_manager import SettingsManager

        mgr = SettingsManager()
        # Should not raise
        mgr.delete_key("nonexistent_provider")


class TestLocalBackendManager:
    """Unit tests for LocalBackendManager edge cases."""

    def test_get_address_no_file(self, tmp_path) -> None:
        """``get_address()`` returns None when no overrides file exists."""
        from deepresearch.web.settings_manager import LocalBackendManager

        mgr = LocalBackendManager()
        mgr._path = tmp_path / "local_backends.json"
        addr = mgr.get_address("ollama")
        assert addr is None

    def test_get_address_unknown_backend(self) -> None:
        """``get_address()`` returns None for an unknown backend name."""
        from deepresearch.web.settings_manager import LocalBackendManager

        mgr = LocalBackendManager()
        assert mgr.get_address("not-a-backend") is None

    def test_set_and_get_address_roundtrip(self, tmp_path) -> None:
        """``set_address()`` then ``get_address()`` returns the same value."""
        from deepresearch.web.settings_manager import LocalBackendManager

        mgr = LocalBackendManager()
        mgr._path = tmp_path / "local_backends.json"
        mgr.set_address("ollama", "10.0.0.5:11434")
        assert mgr.get_address("ollama") == "10.0.0.5:11434"

    def test_set_address_overwrites_previous(self, tmp_path) -> None:
        """``set_address()`` overwrites a previously set address."""
        from deepresearch.web.settings_manager import LocalBackendManager

        mgr = LocalBackendManager()
        mgr._path = tmp_path / "local_backends.json"
        mgr.set_address("ollama", "192.168.1.1:11434")
        mgr.set_address("ollama", "10.0.0.1:11434")
        assert mgr.get_address("ollama") == "10.0.0.1:11434"

    def test_load_corrupted_json(self, tmp_path) -> None:
        """``_load()`` returns empty dict when JSON is corrupted."""
        from deepresearch.web.settings_manager import LocalBackendManager

        mgr = LocalBackendManager()
        bad_file = tmp_path / "corrupted.json"
        bad_file.write_text("not json")
        mgr._path = bad_file
        assert mgr._load() == {}


class TestGgufModelListing:
    """Tests for the list_gguf_models endpoint — model discovery edge cases."""

    def test_no_models_directory(self, client: TestClient) -> None:
        """When the GGUF models directory does not exist, returns empty list."""
        from unittest.mock import patch

        with patch("deepresearch.web.routes.llamacpp._os.path.isdir", return_value=False):
            resp = client.get("/api/local-backends/models/gguf")
            assert resp.status_code == 200
            data = resp.json()
            assert data == {"models": []}

    def test_empty_models_directory(self, client: TestClient) -> None:
        """When the GGUF models directory is empty, returns empty list."""
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "deepresearch.web.routes.llamacpp._os.path.expanduser",
                return_value=tmpdir,
            ):
                resp = client.get("/api/local-backends/models/gguf")
                assert resp.status_code == 200
                data = resp.json()
                assert data == {"models": []}

    def test_list_gguf_returns_json(self, client: TestClient) -> None:
        """GET /api/local-backends/models/gguf always returns JSON with a models key."""
        resp = client.get("/api/local-backends/models/gguf")
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert isinstance(data["models"], list)
