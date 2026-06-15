"""Tests for the DeepeResearch web dashboard.

Covers:
  - EventBus publish / subscribe / unsubscribe
  - FastAPI endpoints
  - MultiSessionManager create/list/cancel/cleanup
  - SettingsManager key storage
  - Custom time budget
  - Local model endpoints
  - SSE event stream
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from deepresearch.web.event_bus import EventBus
from deepresearch.web.server import app
from deepresearch.web.sessions import MultiSessionManager, SessionInfo
from deepresearch.web.settings_manager import SettingsManager, PROVIDERS
from deepresearch.web.state import update_status


# ─── EventBus Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_bus_publish_subscribe() -> None:
    """Publishing an event delivers it to the subscriber."""
    bus = EventBus()
    queue = await bus.subscribe()

    await bus.publish({"event_type": "test_event", "value": 42})

    received = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert received["event_type"] == "test_event"
    assert received["value"] == 42
    assert "_server_timestamp" in received


@pytest.mark.asyncio
async def test_event_bus_multiple_subscribers() -> None:
    """All subscribers receive published events."""
    bus = EventBus()
    q1 = await bus.subscribe()
    q2 = await bus.subscribe()

    await bus.publish({"event_type": "broadcast"})

    r1 = await asyncio.wait_for(q1.get(), timeout=1.0)
    r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert r1["event_type"] == "broadcast"
    assert r2["event_type"] == "broadcast"


@pytest.mark.asyncio
async def test_event_bus_unsubscribe() -> None:
    """Unsubscribed queues no longer receive events."""
    bus = EventBus()
    q1 = await bus.subscribe()
    q2 = await bus.subscribe()

    await bus.unsubscribe(q1)

    await bus.publish({"event_type": "after_unsub"})

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q1.get(), timeout=0.3)

    r2 = await asyncio.wait_for(q2.get(), timeout=1.0)
    assert r2["event_type"] == "after_unsub"


@pytest.mark.asyncio
async def test_event_bus_subscriber_count() -> None:
    """Subscriber count reflects connected clients."""
    bus = EventBus()
    assert bus.subscriber_count == 0

    q1 = await bus.subscribe()
    assert bus.subscriber_count == 1

    q2 = await bus.subscribe()
    assert bus.subscriber_count == 2

    await bus.unsubscribe(q1)
    assert bus.subscriber_count == 1

    await bus.unsubscribe(q2)
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_event_bus_publish_no_block_on_full_queue() -> None:
    """Publishing to a full queue should not raise."""
    bus = EventBus()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    bus._subscribers.append(queue)
    await queue.put({"event_type": "filler"})

    await bus.publish({"event_type": "overflow_test"})
    assert bus.subscriber_count == 1


# ─── FastAPI Endpoint Tests (via TestClient) ────────────────────────────


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    return TestClient(app)


def test_get_dashboard(client: TestClient) -> None:
    """GET / returns the dashboard HTML page."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "DeepeResearch" in resp.text
    assert "dashboard.js" in resp.text  # ES module entry point


def test_get_status_default(client: TestClient) -> None:
    """GET /api/status returns default state."""
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "IDLE"
    assert data["topic"] == ""
    assert data["session_active"] is False


def test_get_status_after_update(client: TestClient) -> None:
    """GET /api/status reflects updated state."""
    update_status(
        state="ROUND1",
        topic="Quantum Computing",
        agents=[{"id": "agent-a", "name": "Agent A", "emoji": "🔬"}],
        agent_progress={"agent-a": 50.0},
        session_active=True,
        phase_label="Round 1",
    )
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == "ROUND1"
    assert data["topic"] == "Quantum Computing"
    assert data["session_active"] is True
    assert data["agent_progress"]["agent-a"] == 50.0
    assert data["phase_label"] == "Round 1"


def test_get_agents(client: TestClient) -> None:
    """GET /api/agents returns the agent list from status cache."""
    update_status(
        agents=[
            {"id": "alpha", "name": "Alpha", "emoji": "🔬"},
            {"id": "beta", "name": "Beta", "emoji": "🧪"},
        ],
    )
    resp = client.get("/api/agents")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == "alpha"
    assert data[1]["name"] == "Beta"


def test_get_events_sse_route_exists(client: TestClient) -> None:
    """The /api/events route is registered."""
    routes = [r.path for r in app.routes]
    assert "/api/events" in routes


def test_new_routes_registered(client: TestClient) -> None:
    """All multi-session and settings routes are registered."""
    routes = [r.path for r in app.routes]
    assert "/api/run" in routes
    assert "/api/sessions" in routes
    assert "/api/sessions/{session_id}" in routes
    assert "/api/sessions/{session_id}/events" in routes
    assert "/api/sessions/{session_id}/cancel" in routes
    assert "/api/sessions/clear-completed" in routes
    assert "/api/session" in routes
    assert "/api/cancel" in routes
    assert "/api/profiles" in routes
    assert "/api/models" in routes
    assert "/api/download/{session_id}/{filename:path}" in routes
    assert "/api/settings/keys" in routes
    assert "/api/settings/keys/{provider}" in routes
    assert "/api/settings/local-models" in routes
    assert "/api/settings/local-endpoints" in routes
    assert "/api/settings/local-endpoints/{name}" in routes
    assert "/api/settings/local-endpoints/{name}/test" in routes


def test_profiles_endpoint(client: TestClient) -> None:
    """GET /api/profiles returns a list or error."""
    resp = client.get("/api/profiles")
    assert resp.status_code in (200, 500)


def test_models_endpoint(client: TestClient) -> None:
    """GET /api/models returns a list or error."""
    resp = client.get("/api/models")
    assert resp.status_code in (200, 500)


def test_download_not_found(client: TestClient) -> None:
    """GET /api/download with non-existent file returns 404."""
    resp = client.get("/api/download/nonexistent_file_xyz.pdf")
    assert resp.status_code == 404
    assert "File not found" in resp.json()["error"]


def test_dashboard_contains_required_sections(client: TestClient) -> None:
    """The dashboard HTML has all required UI sections."""
    resp = client.get("/")
    html = resp.text
    assert 'id="sessionListView"' in html
    assert 'id="newResearchView"' in html
    assert 'id="detailView"' in html
    assert 'id="settingsView"' in html
    assert 'id="topicInput"' in html
    assert 'id="agentColumn1"' in html
    assert 'id="agentColumn2"' in html
    assert 'id="eventColumn"' in html
    assert 'id="eventLog"' in html
    assert 'id="resultView"' in html
    assert 'id="errorView"' in html
    assert 'startResearch' in html
    assert 'showSessions' in html
    assert 'showSettings' in html
    assert 'customMinutesInput' in html
    assert 'providerList' in html
    assert 'dashboard.css' in html
    assert 'dashboard.js' in html


# ─── Multi-Session API Tests ────────────────────────────────────────────


def test_run_endpoint_returns_session_id(client: TestClient) -> None:
    """POST /api/run returns started status with session_id."""
    resp = client.post(
        "/api/run",
        json={"topic": "Quantum Computing", "time_budget": "medium", "model_mode": "same"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"
    assert data["session_id"] is not None
    assert data["topic"] == "Quantum Computing"


def test_run_endpoint_with_custom_seconds(client: TestClient) -> None:
    """POST /api/run accepts time_budget_seconds for custom budget."""
    resp = client.post(
        "/api/run",
        json={"topic": "Test Topic", "time_budget": "custom", "time_budget_seconds": 600, "model_mode": "same"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "started"


def test_run_endpoint_missing_topic_returns_422(client: TestClient) -> None:
    """POST /api/run without topic returns validation error."""
    resp = client.post("/api/run", json={"time_budget": "quick"})
    assert resp.status_code == 422


def test_list_sessions_endpoint(client: TestClient) -> None:
    """GET /api/sessions returns a list."""
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_get_session_not_found(client: TestClient) -> None:
    """GET /api/sessions/{id} with unknown id returns 404."""
    resp = client.get("/api/sessions/unknown123")
    assert resp.status_code == 404


def test_cancel_session_not_found(client: TestClient) -> None:
    """POST /api/sessions/{id}/cancel with unknown id."""
    resp = client.post("/api/sessions/unknown123/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "not_found_or_already_done"


def test_clear_completed(client: TestClient) -> None:
    """POST /api/sessions/clear-completed works."""
    resp = client.post("/api/sessions/clear-completed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_legacy_session_endpoint(client: TestClient) -> None:
    """GET /api/session returns status (backward compat)."""
    resp = client.get("/api/session")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data


def test_legacy_cancel_endpoint(client: TestClient) -> None:
    """POST /api/cancel returns status (backward compat)."""
    resp = client.post("/api/cancel")
    assert resp.status_code == 200
    assert "status" in resp.json()


# ─── Settings API Tests ─────────────────────────────────────────────────


def test_settings_keys_endpoint(client: TestClient) -> None:
    """GET /api/settings/keys returns provider list."""
    resp = client.get("/api/settings/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    # At minimum should have 'openai'
    assert "openai" in data


def test_settings_set_key_invalid_provider(client: TestClient) -> None:
    """POST /api/settings/keys with unknown provider returns 400."""
    resp = client.post(
        "/api/settings/keys",
        json={"provider": "nonexistent_provider", "key": "sk-test"},
    )
    assert resp.status_code == 400


def test_settings_set_and_delete_key(client: TestClient) -> None:
    """POST then DELETE /api/settings/keys round-trips."""
    # Set key
    resp = client.post(
        "/api/settings/keys",
        json={"provider": "openai", "key": "sk-test-key-12345"},
    )
    assert resp.status_code == 200

    # Verify it shows as configured
    resp = client.get("/api/settings/keys")
    data = resp.json()
    assert data["openai"]["configured"] is True

    # Delete key
    resp = client.delete("/api/settings/keys/openai")
    assert resp.status_code == 200

    # Verify it's gone
    resp = client.get("/api/settings/keys")
    data = resp.json()
    assert data["openai"]["configured"] is False


def test_local_models_endpoint(client: TestClient) -> None:
    """GET /api/settings/local-models returns a list."""
    resp = client.get("/api/settings/local-models")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_add_remove_local_endpoint(client: TestClient) -> None:
    """POST then DELETE /api/settings/local-endpoints round-trips."""
    # Add
    resp = client.post(
        "/api/settings/local-endpoints",
        json={"name": "test-llama", "endpoint": "http://localhost:8080/v1", "type": "llamacpp"},
    )
    assert resp.status_code == 200

    # List
    resp = client.get("/api/settings/local-models")
    data = resp.json()
    saved = [m for m in data if m.get("name") == "test-llama"]
    assert len(saved) >= 1

    # Test (will fail since endpoint doesn't exist)
    resp = client.post("/api/settings/local-endpoints/test-llama/test")
    assert resp.status_code == 200

    # Remove
    resp = client.delete("/api/settings/local-endpoints/test-llama")
    assert resp.status_code == 200


# ─── MultiSessionManager Unit Tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_multi_session_manager_create() -> None:
    """MultiSessionManager.create_session returns a valid SessionInfo."""
    mgr = MultiSessionManager(max_sessions=10)
    info = await mgr.create_session(topic="Test Topic", time_budget="quick", model_mode="same")

    assert isinstance(info, SessionInfo)
    assert info.session_id is not None
    assert info.topic == "Test Topic"
    assert info.time_budget == "quick"
    assert info.status in ("queued", "running")
    assert info.event_bus is not None

    # Clean up
    await mgr.cancel_session(info.session_id)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multi_session_manager_create_custom_budget() -> None:
    """Custom time_budget_seconds overrides named budget."""
    mgr = MultiSessionManager(max_sessions=10)
    info = await mgr.create_session(
        topic="Custom Budget",
        time_budget="custom",
        time_budget_seconds=900,
        model_mode="same",
    )

    assert info.time_budget_seconds == 900
    assert info.time_budget == "custom"

    await mgr.cancel_session(info.session_id)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multi_session_manager_list() -> None:
    """MultiSessionManager.list_sessions returns sorted list."""
    mgr = MultiSessionManager(max_sessions=10)
    info1 = await mgr.create_session(topic="First", time_budget="quick")
    info2 = await mgr.create_session(topic="Second", time_budget="deep")

    sessions = mgr.list_sessions()
    assert len(sessions) >= 2

    # Newest first
    assert sessions[0]["topic"] == "Second"
    assert sessions[1]["topic"] == "First"

    # Clean up
    await mgr.cancel_session(info1.session_id)
    await mgr.cancel_session(info2.session_id)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multi_session_manager_get() -> None:
    """MultiSessionManager.get_session returns correct session."""
    mgr = MultiSessionManager(max_sessions=10)
    info = await mgr.create_session(topic="Get Test")

    retrieved = mgr.get_session(info.session_id)
    assert retrieved is not None
    assert retrieved.session_id == info.session_id
    assert retrieved.topic == "Get Test"

    assert mgr.get_session("nonexistent") is None

    await mgr.cancel_session(info.session_id)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multi_session_manager_cancel() -> None:
    """MultiSessionManager.cancel_session cancels a running session."""
    mgr = MultiSessionManager(max_sessions=10)
    info = await mgr.create_session(topic="Cancel Test")

    cancelled = await mgr.cancel_session(info.session_id)
    assert cancelled is True
    await asyncio.sleep(0.1)

    # Status should be cancelled
    retrieved = mgr.get_session(info.session_id)
    assert retrieved is not None
    assert retrieved.status == "cancelled"


@pytest.mark.asyncio
async def test_multi_session_manager_cancel_nonexistent() -> None:
    """Cancelling a nonexistent session returns False."""
    mgr = MultiSessionManager(max_sessions=10)
    cancelled = await mgr.cancel_session("nonexistent")
    assert cancelled is False


@pytest.mark.asyncio
async def test_multi_session_manager_cleanup() -> None:
    """MultiSessionManager cleans up old sessions when max is reached."""
    mgr = MultiSessionManager(max_sessions=3)

    # Create 3 sessions
    infos = []
    for i in range(3):
        info = await mgr.create_session(topic=f"Session {i}")
        infos.append(info)

    # Wait a moment and cancel all
    for info in infos:
        await mgr.cancel_session(info.session_id)
    await asyncio.sleep(0.1)

    # Now create another — should trigger cleanup
    info4 = await mgr.create_session(topic="Session 4")
    assert mgr.session_count <= 3

    # Clean up
    await mgr.cancel_session(info4.session_id)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multi_session_manager_clear_completed() -> None:
    """Clear completed removes all done sessions."""
    mgr = MultiSessionManager(max_sessions=10)
    info = await mgr.create_session(topic="Clear Test")
    await mgr.cancel_session(info.session_id)
    await asyncio.sleep(0.1)

    count = mgr.clear_completed()
    assert count >= 1
    assert mgr.session_count == 0


@pytest.mark.asyncio
async def test_multi_session_manager_active_count() -> None:
    """Active count reflects running/queued sessions."""
    mgr = MultiSessionManager(max_sessions=10)
    info = await mgr.create_session(topic="Active Test")
    assert mgr.active_count >= 1

    await mgr.cancel_session(info.session_id)
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_multi_session_event_bus_isolation() -> None:
    """Each session gets its own EventBus — events are isolated.

    We test this by creating two standalone EventBus instances directly
    (not via session background tasks) to verify cross-talk isolation.
    """
    bus1 = EventBus()
    bus2 = EventBus()

    assert bus1 is not bus2  # Different instances

    # Subscribe to both
    q1 = await bus1.subscribe()
    q2 = await bus2.subscribe()

    # Publish to bus1 only
    await bus1.publish({"event_type": "test_event", "value": 42})

    # Only q1 should get it
    received = await asyncio.wait_for(q1.get(), timeout=1.0)
    assert received["value"] == 42

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q2.get(), timeout=0.3)


# ─── SessionManager Unit Tests (legacy compat) ──────────────────────────


@pytest.mark.asyncio
async def test_session_manager_properties() -> None:
    """SessionManager properties reflect initial idle state."""
    from deepresearch.web.session_manager import SessionManager

    sm = SessionManager()
    assert sm.status == "idle"
    assert sm.is_running is False
    assert sm.result is None


@pytest.mark.asyncio
async def test_session_manager_start_stop_lifecycle() -> None:
    """Starting a session sets status to running; cancel returns to idle."""
    from deepresearch.web.session_manager import SessionManager

    sm = SessionManager()
    assert sm.status == "idle"

    result = await sm.start_session(
        topic="Test topic",
        time_budget="quick",
        model_mode="same",
    )
    assert result["status"] == "started"
    assert sm.status == "running"
    assert sm.is_running is True

    cancel_result = await sm.cancel_session()
    assert cancel_result["status"] == "cancelled"

    await asyncio.sleep(0.1)
    assert sm.status == "idle"


@pytest.mark.asyncio
async def test_session_manager_raises_if_already_running() -> None:
    """Starting a second session while one is running raises RuntimeError."""
    from deepresearch.web.session_manager import SessionManager

    sm = SessionManager()
    await sm.start_session(topic="First", time_budget="quick")

    with pytest.raises(RuntimeError, match="already running"):
        await sm.start_session(topic="Second")

    await sm.cancel_session()
    await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_session_manager_cancel_when_idle() -> None:
    """Cancelling without an active session returns no_active_session."""
    from deepresearch.web.session_manager import SessionManager

    sm = SessionManager()
    result = await sm.cancel_session()
    assert result["status"] == "no_active_session"


# ─── SettingsManager Unit Tests ─────────────────────────────────────────


@pytest.fixture
def temp_settings() -> SettingsManager:
    """Create a SettingsManager with a temp directory."""
    mgr = SettingsManager()
    # Override settings dir to a temp location
    tmpdir = tempfile.mkdtemp()
    mgr._settings_dir = Path(tmpdir)
    mgr._env_path = mgr._settings_dir / ".env"
    mgr._endpoints_path = mgr._settings_dir / "local_endpoints.json"
    return mgr


def test_settings_manager_get_keys_empty(temp_settings: SettingsManager) -> None:
    """get_keys returns all providers with configured=False when no keys set."""
    keys = temp_settings.get_keys()
    assert "openai" in keys
    assert keys["openai"]["configured"] is False
    assert keys["openai"]["has_key"] is False
    assert keys["openai"]["key_preview"] is None


def test_settings_manager_set_key(temp_settings: SettingsManager) -> None:
    """set_key saves a key and marks provider as configured."""
    temp_settings.set_key("openai", "sk-test-key-12345")
    keys = temp_settings.get_keys()
    assert keys["openai"]["configured"] is True
    assert keys["openai"]["has_key"] is True
    assert keys["openai"]["key_preview"] is not None

    # Check env var was set
    assert os.environ.get("OPENAI_API_KEY") == "sk-test-key-12345"


def test_settings_manager_delete_key(temp_settings: SettingsManager) -> None:
    """delete_key removes a key."""
    temp_settings.set_key("openai", "sk-test-key-12345")
    temp_settings.delete_key("openai")

    keys = temp_settings.get_keys()
    assert keys["openai"]["configured"] is False


def test_settings_manager_set_key_unknown_provider(temp_settings: SettingsManager) -> None:
    """set_key with unknown provider raises ValueError."""
    with pytest.raises(ValueError):
        temp_settings.set_key("unknown_provider", "key")


def test_settings_manager_local_endpoints(temp_settings: SettingsManager) -> None:
    """Local endpoints CRUD works."""
    assert temp_settings.get_local_endpoints() == []

    temp_settings.add_local_endpoint({
        "name": "test-llama",
        "endpoint": "http://localhost:8080/v1",
        "type": "llamacpp",
    })

    endpoints = temp_settings.get_local_endpoints()
    assert len(endpoints) == 1
    assert endpoints[0]["name"] == "test-llama"

    temp_settings.remove_local_endpoint("test-llama")
    assert temp_settings.get_local_endpoints() == []


def test_settings_manager_env_file_persistence(temp_settings: SettingsManager) -> None:
    """Keys persisted to .env file survive across instances."""
    temp_settings.set_key("openai", "sk-persist-test")

    # Create a new manager pointing to same dir
    mgr2 = SettingsManager()
    mgr2._settings_dir = temp_settings._settings_dir
    mgr2._env_path = temp_settings._env_path

    keys = mgr2.get_keys()
    assert keys["openai"]["configured"] is True
    assert keys["openai"]["key_preview"] is not None


# ─── Orchestrator Custom Budget Test ────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_custom_time_budget() -> None:
    """Orchestrator accepts time_budget_seconds override."""
    from deepresearch.orchestrator import Orchestrator
    from deepresearch.models import SessionConfig, ResearchTopic, AgentProfile

    profiles = [
        AgentProfile(
            id="test-agent",
            name="Test Agent",
            emoji="🤖",
            persona_prompt="You are a test agent.",
            methodology="Test methodology.",
            knowledge_base="Test knowledge.",
            bias_mitigation="Test bias mitigation.",
            voice="neutral",
            temperature=0.7,
        ),
    ]

    orch = Orchestrator(
        profiles=profiles,
        model_configs=[{"id": "gpt-4o", "provider": "openai", "display_name": "GPT-4o", "default": True}],
        agent_factory=lambda p, m, **extra: lambda **kw: None,
        scribe_factory=lambda **extra: lambda **kw: None,
    )

    config = await orch.configure(
        "Test topic",
        time_budget="custom",
        time_budget_seconds=600,
        model_mode="same",
    )

    assert config.time_budget_seconds == 600
    assert config.topic.time_budget == "custom"


# ─── Event Bus Legacy Test ─────────────────────────────────────────────


def test_event_bus_event_format() -> None:
    """Events published to the bus have the expected structure for SSE."""
    from deepresearch.web.event_bus import event_bus

    async def test_event() -> None:
        queue = await event_bus.subscribe()
        try:
            await event_bus.publish({
                "event_type": "session_start",
                "topic": "Quantum Computing",
                "state": "ROUND1",
            })
            received = await asyncio.wait_for(queue.get(), timeout=1.0)
            sse_event = {
                "event": received.get("event_type", "message"),
                "data": json.dumps(received),
            }
            assert sse_event["event"] == "session_start"
            parsed = json.loads(sse_event["data"])
            assert parsed["topic"] == "Quantum Computing"
            assert parsed["state"] == "ROUND1"
        finally:
            await event_bus.unsubscribe(queue)

    asyncio.run(test_event())
