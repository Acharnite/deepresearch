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

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from deepresearch.web.server import app


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    return TestClient(app)


# ─── Endpoint Registration ─────────────────────────────────────────────


def test_new_routes_registered(client: TestClient) -> None:
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


# ─── GET /api/local-backends ───────────────────────────────────────────


def test_list_local_backends_returns_json(client: TestClient) -> None:
    """GET /api/local-backends returns JSON with backends list."""
    resp = client.get("/api/local-backends")
    assert resp.status_code == 200
    data = resp.json()
    assert "backends" in data
    assert isinstance(data["backends"], list)


def test_list_local_backends_contains_all_five(client: TestClient) -> None:
    """GET /api/local-backends contains all 5 backend entries."""
    resp = client.get("/api/local-backends")
    data = resp.json()
    names = {b["name"] for b in data["backends"]}
    expected = {"ollama", "llama-cpp", "vllm", "lm-studio", "local-ai"}
    assert names == expected


def test_list_local_backends_each_has_required_fields(
    client: TestClient,
) -> None:
    """Each backend entry has name, running, port fields."""
    resp = client.get("/api/local-backends")
    data = resp.json()
    for backend in data["backends"]:
        assert "name" in backend, f"Missing 'name' in {backend}"
        assert "running" in backend, f"Missing 'running' in {backend}"
        assert "port" in backend, f"Missing 'port' in {backend}"
        assert isinstance(backend["running"], bool)
        assert isinstance(backend["port"], int)
        # custom_address should always be present (null if not set)
        assert "custom_address" in backend


def test_list_local_backends_port_values(client: TestClient) -> None:
    """Each backend has the expected default port."""
    resp = client.get("/api/local-backends")
    data = resp.json()
    ports = {b["name"]: b["port"] for b in data["backends"]}
    assert ports["ollama"] == 11434
    assert ports["llama-cpp"] == 8080
    assert ports["vllm"] == 8000
    assert ports["lm-studio"] == 1234
    assert ports["local-ai"] == 8080


# ─── PUT /api/local-backends/{name}/address ────────────────────────────


def test_set_backend_address_valid(client: TestClient) -> None:
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


def test_set_backend_address_invalid_format(client: TestClient) -> None:
    """PUT with invalid address format returns 400."""
    resp = client.put(
        "/api/local-backends/ollama/address",
        json={"address": "not-a-valid-address"},
    )
    assert resp.status_code == 400


def test_set_backend_address_unknown_backend(client: TestClient) -> None:
    """PUT with unknown backend name returns 404."""
    resp = client.put(
        "/api/local-backends/unknown-backend/address",
        json={"address": "localhost:9999"},
    )
    assert resp.status_code == 404
    assert "Unknown backend" in resp.json()["message"]


def test_set_backend_address_persists(client: TestClient) -> None:
    """Address set via PUT is returned by GET."""
    # Set address
    put_resp = client.put(
        "/api/local-backends/ollama/address",
        json={"address": "10.0.0.1:11434"},
    )
    assert put_resp.status_code == 200

    # Verify via GET
    get_resp = client.get("/api/local-backends/ollama/address")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["address"] == "10.0.0.1:11434"


# ─── GET /api/local-backends/{name}/address ────────────────────────────


def test_get_backend_address_not_set(client: TestClient) -> None:
    """GET address for a backend with no custom address returns null address."""
    resp = client.get("/api/local-backends/llama-cpp/address")
    assert resp.status_code == 200
    data = resp.json()
    assert data["address"] is None


def test_get_backend_address_unknown_backend(client: TestClient) -> None:
    """GET address for unknown backend returns 404."""
    resp = client.get("/api/local-backends/ghost/address")
    assert resp.status_code == 404
    assert "Unknown backend" in resp.json()["message"]


# ─── POST /api/local-backends/{name}/test ──────────────────────────────


def test_test_backend_known_returns_json(client: TestClient) -> None:
    """POST test for a known backend returns JSON with status field."""
    resp = client.post("/api/local-backends/ollama/test")
    assert resp.status_code == 200
    data = resp.json()
    # Should have status (running or not), port, and latency or message
    assert "status" in data


def test_test_backend_unknown_returns_404(client: TestClient) -> None:
    """POST test for unknown backend returns 404."""
    resp = client.post("/api/local-backends/nonexistent/test")
    assert resp.status_code == 404
    assert "Unknown backend" in resp.json()["message"]


# ─── POST /api/local-backends/models/download ──────────────────────────


def test_download_model_invalid_body_returns_error(client: TestClient) -> None:
    """POST download with missing required fields returns 422."""
    resp = client.post(
        "/api/local-backends/models/download",
        json={},  # Missing "name"
    )
    assert resp.status_code == 422


def test_download_model_returns_sse(client: TestClient) -> None:
    """POST download with valid body returns SSE response."""
    resp = client.post(
        "/api/local-backends/models/download",
        json={"name": "test-model", "download_type": "ollama"},
    )
    # Should be an SSE response (EventSourceResponse)
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith(
        "text/event-stream"
    )


def test_download_model_auto_mode_returns_sse(client: TestClient) -> None:
    """POST download with auto download_type returns SSE."""
    resp = client.post(
        "/api/local-backends/models/download",
        json={"name": "test-model", "download_type": "auto"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith(
        "text/event-stream"
    )


# ─── GET /api/local-backends/models/download/progress ──────────────────


def test_download_progress_returns_json(client: TestClient) -> None:
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


def test_download_progress_has_expected_fields(client: TestClient) -> None:
    """GET download/progress returns all expected state fields."""
    resp = client.get("/api/local-backends/models/download/progress")
    assert resp.status_code == 200
    data = resp.json()
    # All fields should be present with correct types
    assert isinstance(data.get("active"), bool)
    assert isinstance(data.get("model"), str)
    assert isinstance(data.get("progress"), (int, float))
    assert isinstance(data.get("message"), str)
    assert isinstance(data.get("status"), str)
    assert isinstance(data.get("log"), list)


# ─── GET /api/tools/status ─────────────────────────────────────────────


def test_tools_status_returns_json(client: TestClient) -> None:
    """GET /api/tools/status returns JSON with llmfit status."""
    resp = client.get("/api/tools/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "llmfit" in data
    assert "installed" in data["llmfit"]


# ─── GET /api/tools/recommendations ────────────────────────────────────


def test_tools_recommendations_returns_json(client: TestClient) -> None:
    """GET /api/tools/recommendations returns JSON (available: False if llmfit missing)."""
    resp = client.get("/api/tools/recommendations")
    assert resp.status_code == 200
    data = resp.json()
    # When llmfit is not installed, returns {"available": False, "message": ...}
    assert "available" in data


# ─── GET /api/hardware ─────────────────────────────────────────────────


def test_hardware_returns_json(client: TestClient) -> None:
    """GET /api/hardware returns JSON with available flag."""
    resp = client.get("/api/hardware")
    assert resp.status_code == 200
    data = resp.json()
    # When llmfit is not installed, returns {"available": False, "message": ...}
    assert "available" in data
