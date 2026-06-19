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


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    return TestClient(app)


class TestBackendRoutes:
    """Local-backend, tools, and hardware route registration."""

    def test_new_routes_registered(self, client: TestClient) -> None:
        """All new local-backend, tools, and hardware routes are registered."""
        routes = [r.path for r in app.routes]
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
