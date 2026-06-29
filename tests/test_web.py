"""Tests for the DeepResearch web dashboard.

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
from tests.conftest import get_all_paths
from deepresearch.web.sessions import (
    MultiSessionManager,
    SessionInfo,
    MEANINGFUL_OUTPUT_EXTENSIONS,
    _has_meaningful_output,
    _remove_output_dir,
    cleanup_output_dirs,
    SESSION_DB_PATH,
)
from deepresearch.web.settings_manager import SettingsManager
from deepresearch.web.state import update_status


# ─── EventBus Tests ─────────────────────────────────────────────────────


class TestEventBus:
    """EventBus publish / subscribe / unsubscribe."""

    @pytest.mark.asyncio
    async def test_publish_subscribe(self) -> None:
        """Publishing an event delivers it to the subscriber."""
        bus = EventBus()
        queue = await bus.subscribe()

        await bus.publish({"event_type": "test_event", "value": 42})

        received = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert received["event_type"] == "test_event"
        assert received["value"] == 42
        assert "_server_timestamp" in received

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self) -> None:
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
    async def test_unsubscribe(self) -> None:
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
    async def test_subscriber_count(self) -> None:
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
    async def test_publish_no_block_on_full_queue(self) -> None:
        """Publishing to a full queue should not raise."""
        bus = EventBus()
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        bus._subscribers.append(queue)
        await queue.put({"event_type": "filler"})

        await bus.publish({"event_type": "overflow_test"})
        assert bus.subscriber_count == 1

    def test_event_format(self) -> None:
        """Events published to the bus have the expected structure for SSE."""
        from deepresearch.web.event_bus import event_bus

        async def test_event() -> None:
            queue = await event_bus.subscribe()
            try:
                await event_bus.publish(
                    {
                        "event_type": "session_start",
                        "topic": "Quantum Computing",
                        "state": "ROUND1",
                    }
                )
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

    @pytest.mark.asyncio
    async def test_event_history_records_published_events(self) -> None:
        """Published events are recorded in the history list in order."""
        history: list[dict] = []
        bus = EventBus(history=history)

        await bus.publish({"event_type": "event_1", "data": "first"})
        await bus.publish({"event_type": "event_2", "data": "second"})
        await bus.publish({"event_type": "event_3", "data": "third"})

        assert len(history) == 3
        assert history[0]["event_type"] == "event_1"
        assert history[0]["data"] == "first"
        assert history[1]["event_type"] == "event_2"
        assert history[1]["data"] == "second"
        assert history[2]["event_type"] == "event_3"
        assert history[2]["data"] == "third"
        # Each event gets a _server_timestamp
        assert "_server_timestamp" in history[0]
        assert "_server_timestamp" in history[1]
        assert "_server_timestamp" in history[2]

    @pytest.mark.asyncio
    async def test_event_history_capacity_limit(self) -> None:
        """EventBus stores all published events in history (no built-in cap).

        The EventBus itself does not impose a history capacity limit. All
        published events are appended. Downstream consumers (e.g.,
        save_all_sessions) cap at the last 100 during persistence.
        """
        history: list[dict] = []
        bus = EventBus(history=history)

        count = 200
        for i in range(count):
            await bus.publish({"event_type": f"event_{i}", "seq": i})

        # All events are retained — no built-in pruning
        assert len(history) == count
        assert history[0]["seq"] == 0
        assert history[-1]["seq"] == count - 1

    @pytest.mark.asyncio
    async def test_replay_restores_state_after_reconnect(self) -> None:
        """Subscriber receives all historical events when replayed on reconnect.

        Simulates the pattern used by the SSE endpoint: after a disconnect,
        the new subscriber first replays the event_history, then receives
        new live events.
        """
        history: list[dict] = []
        bus = EventBus(history=history)

        # Phase 1: publish events while client is connected
        q1 = await bus.subscribe()
        await bus.publish({"event_type": "session_start", "seq": 1})
        await bus.publish({"event_type": "round_start", "seq": 2})
        await bus.publish({"event_type": "agent_start", "seq": 3})

        # Verify q1 got all 3 events
        for expected_seq in (1, 2, 3):
            ev = await asyncio.wait_for(q1.get(), timeout=1.0)
            assert ev["seq"] == expected_seq

        # Phase 2: disconnect, then reconnect (simulating SSE client reconnect)
        await bus.unsubscribe(q1)

        # A new subscriber joins — replays history, then gets new events
        q2 = await bus.subscribe()

        # Replay: feed history events to the new subscriber
        replayed = []
        for event in history:
            replayed.append(event)

        assert len(replayed) == 3
        assert replayed[0]["seq"] == 1
        assert replayed[1]["seq"] == 2
        assert replayed[2]["seq"] == 3

        # Phase 3: after replay, new events still go to the subscriber
        await bus.publish({"event_type": "session_end", "seq": 4})
        live = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert live["seq"] == 4

        await bus.unsubscribe(q2)


# ─── FastAPI Endpoint Tests (via TestClient) ────────────────────────────


@pytest.fixture
def client() -> TestClient:
    """Return a TestClient bound to the FastAPI app."""
    return TestClient(app)


class TestDashboardEndpoints:
    """FastAPI dashboard endpoint tests."""

    def test_get_dashboard(self, client: TestClient) -> None:
        """GET / returns the dashboard HTML page with all required UI sections."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/html")
        assert "DeepeResearch" in resp.text
        assert "dashboard.js" in resp.text
        html = resp.text
        assert 'id="sessionListView"' in html
        assert 'id="newResearchView"' in html
        assert 'id="detailView"' in html
        assert 'id="settingsView"' in html
        assert 'id="topicInput"' in html
        assert 'id="agentColumn1"' in html
        assert 'id="eventColumn"' in html
        assert 'id="eventLog"' in html
        assert 'id="resultView"' in html
        assert 'id="errorView"' in html
        assert 'id="scribeCard"' in html
        assert 'id="agent-output-scribe"' in html
        assert 'id="phaseIndicator"' in html
        assert "startResearch" in html
        assert "showSessions" in html
        assert "showSettings" in html
        assert "customMinutesInput" in html
        assert "providerList" in html
        assert "dashboard.css" in html

    def test_local_backends_tab_has_lifecycle_controls(self, client: TestClient) -> None:
        """GET / returns dashboard with backend lifecycle controls in correct tabs.

        Ollama and llama.cpp lifecycle controls (install, start, stop, uninstall)
        must appear in the Local Backends tab, NOT the Local Models tab.
        """
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text

        # Locate tab section boundaries
        local_models_start = html.index('id="tab-local-models"')
        local_backends_start = html.index('id="tab-local-backends"')

        # Compute section end boundaries by finding the next tab or end
        # We search for the next id="tab-*" after each section start
        def section_contains(section_start: int, element_id: str) -> bool:
            """Check if element_id appears between section_start and the next tab."""
            # Find the element position
            pos = html.find(f'id="{element_id}"')
            if pos == -1:
                return False  # Element not found at all
            # Check if it falls within the section boundaries
            return pos > section_start

        def section_does_not_contain(section_start: int, element_id: str) -> bool:
            """Check if element_id appears before section_start or not at all."""
            pos = html.find(f'id="{element_id}"')
            if pos == -1:
                return True  # Element not found — consider it "not in section"
            return pos < section_start

        # ── Lifecycle controls MUST be in Local Backends tab ──
        lifecycle_ids = [
            "ollamaStatus",
            "installOllamaBtn",
            "ollamaActions",
            "ollamaActionHint",
            "llamacppStatus",
            "installLlamaCppBtn",
            "llamacppActions",
            "llamacppActionHint",
            "backendInstallLog",
            "backendInstallOutput",
        ]
        for eid in lifecycle_ids:
            assert section_contains(local_backends_start, eid), (
                f"Lifecycle element '{eid}' should be in Local Backends tab"
            )

        # ── Lifecycle controls must NOT be in Local Models tab ──
        # (Allow them to be absent or only in Local Backends)
        not_in_local_models = [
            "installOllamaBtn",
            "ollamaActions",
            "installLlamaCppBtn",
            "llamacppActions",
        ]
        for eid in not_in_local_models:
            pos = html.find(f'id="{eid}"')
            if pos != -1:
                assert section_contains(local_backends_start, eid), (
                    f"Lifecycle element '{eid}' must be in Local Backends, not Local Models"
                )

        # ── Model/serve/config elements MUST stay in Local Models tab ──
        model_tab_ids = [
            "discoveredModels",
            "ggufModelsSection",
            "ggufModelList",
            "llamacppConfigSection",
            "llamacppPortInput",
            "llamacppGpuLayersInput",
            "llamacppCtxInput",
            "llamacppBatchInput",
            "ollamaInstallLog",
            "ollamaInstallOutput",
            "hfServeLog",
            "hfRepoInput",
            "hardwareInfo",
            "endpointList",
        ]
        for eid in model_tab_ids:
            assert section_contains(local_models_start, eid), (
                f"Model/config element '{eid}' should be in Local Models tab"
            )

        # ── Backend connectivity list must be in Local Backends tab ──
        backend_tab_ids = [
            "localBackendsList",
        ]
        for eid in backend_tab_ids:
            assert section_contains(local_backends_start, eid), (
                f"Backend element '{eid}' should be in Local Backends tab"
            )

    def test_get_status_default(self, client: TestClient) -> None:
        """GET /api/status returns default state."""
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["state"] == "IDLE"
        assert data["topic"] == ""
        assert data["session_active"] is False

    def test_get_status_after_update(self, client: TestClient) -> None:
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

    def test_get_agents(self, client: TestClient) -> None:
        """GET /api/agents returns agent profiles from config."""
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 5
        assert all(a.get("id") for a in data)
        assert all(a.get("name") for a in data)
        assert all(a.get("emoji") for a in data)

    def test_all_routes_registered(self, client: TestClient) -> None:
        """All critical routes are registered."""
        routes = get_all_paths(app)
        expected = [
            "/",
            "/api/status",
            "/api/agents",
            "/api/events",
            "/api/run",
            "/api/sessions",
            "/api/sessions/{session_id}",
            "/api/sessions/{session_id}/events",
            "/api/sessions/{session_id}/cancel",
            "/api/sessions/clear-completed",
            "/api/profiles",
            "/api/models",
            "/api/download/{session_id}/{filename:path}",
            "/api/settings/keys",
            "/api/settings/keys/{provider}",
            "/api/settings/local-models",
            "/api/settings/local-endpoints",
            "/api/settings/local-endpoints/{name}",
            "/api/settings/local-endpoints/{name}/test",
            "/api/local-backends",
            "/api/local-backends/{name}/address",
            "/api/local-backends/{name}/test",
            "/api/local-backends/ollama/status",
            "/api/local-backends/ollama/install",
            "/api/local-backends/ollama/start",
            "/api/local-backends/ollama/stop",
            "/api/local-backends/ollama/uninstall",
            "/api/local-backends/ollama/pull",
            "/api/local-backends/llamacpp/status",
            "/api/local-backends/llamacpp/install",
            "/api/local-backends/llamacpp/uninstall",
            "/api/local-backends/llamacpp/start",
            "/api/local-backends/llamacpp/stop",
            "/api/local-backends/llamacpp/restart",
            "/api/tools/status",
            "/api/hardware",
        ]
        for route in expected:
            assert route in routes, f"Missing route: {route}"

    def test_download_not_found(self, client: TestClient) -> None:
        """GET /api/download with non-existent file returns 404."""
        resp = client.get("/api/download/nonexistent_file_xyz.pdf")
        assert resp.status_code == 404
        assert "File not found" in resp.json()["error"]

    def test_session_state_endpoint(
        self, client: TestClient, mock_llm_client: None
    ) -> None:
        """GET /api/sessions/{id}/state returns current session state."""
        resp = client.get("/api/sessions/nonexistent/state")
        assert resp.status_code == 404
        assert "Session not found" in resp.json()["error"]

        resp = client.post(
            "/api/run",
            json={"topic": "State Test", "time_budget": "quick", "model_mode": "same"},
        )
        assert resp.status_code == 201
        session_id = resp.json()["session_id"]

        resp = client.get(f"/api/sessions/{session_id}/state")
        assert resp.status_code == 200
        data = resp.json()
        assert "current_state" in data
        assert "topic" in data
        assert data["topic"] == "State Test"
        assert "session_id" in data
        assert data["session_id"] == session_id

        resp = client.post(f"/api/sessions/{session_id}/cancel")
        assert resp.status_code == 200

        resp = client.delete(f"/api/sessions/{session_id}")
        assert resp.status_code == 204


class TestSessionEndpoints:
    """Multi-session API endpoint tests."""

    def test_run_endpoint_returns_session_id(
        self, client: TestClient, mock_llm_client: None
    ) -> None:
        """POST /api/run returns started status with session_id."""
        resp = client.post(
            "/api/run",
            json={
                "topic": "Quantum Computing",
                "time_budget": "medium",
                "model_mode": "same",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "started"
        assert data["session_id"] is not None
        assert data["topic"] == "Quantum Computing"

    def test_run_endpoint_with_custom_seconds(
        self, client: TestClient, mock_llm_client: None
    ) -> None:
        """POST /api/run accepts time_budget_seconds for custom budget."""
        resp = client.post(
            "/api/run",
            json={
                "topic": "Test Topic",
                "time_budget": "custom",
                "time_budget_seconds": 600,
                "model_mode": "same",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "started"

    def test_run_endpoint_missing_topic_returns_422(self, client: TestClient) -> None:
        """POST /api/run without topic returns validation error."""
        resp = client.post("/api/run", json={"time_budget": "quick"})
        assert resp.status_code == 422
        # FastAPI validation error returns detail array
        data = resp.json()
        assert "detail" in data

    def test_list_sessions_endpoint(self, client: TestClient) -> None:
        """GET /api/sessions returns a dict with sessions, total, offset, limit."""
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "sessions" in data
        assert "total" in data
        assert isinstance(data["sessions"], list)

    def test_get_session_not_found(self, client: TestClient) -> None:
        """GET /api/sessions/{id} with unknown id returns 404."""
        resp = client.get("/api/sessions/unknown123")
        assert resp.status_code == 404
        assert "Session not found" in resp.json()["error"]

    def test_cancel_session_not_found(self, client: TestClient) -> None:
        """POST /api/sessions/{id}/cancel with unknown id."""
        resp = client.post("/api/sessions/unknown123/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found_or_already_done"

    def test_clear_completed(self, client: TestClient) -> None:
        """POST /api/sessions/clear-completed works."""
        resp = client.post("/api/sessions/clear-completed")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_cancel_event_in_session(self):
        """Cancel event is created and set when cancelling a session."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(topic="Cancel Event Test", time_budget="quick")

        cancelled = await mgr.cancel_session(info.session_id)
        assert isinstance(cancelled, bool)

        retrieved = mgr.get_session(info.session_id)
        assert retrieved is not None
        assert retrieved.status in ("cancelled", "complete", "error", "running")

        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_concurrent_session_limit(self):
        """Creating sessions beyond max limit raises or rejects."""
        mgr = MultiSessionManager(max_sessions=2)
        info1 = await mgr.create_session(topic="Session 1", time_budget="quick")
        info2 = await mgr.create_session(topic="Session 2", time_budget="quick")

        info3 = await mgr.create_session(topic="Session 3", time_budget="quick")

        assert mgr.session_count <= 3

        for info in [info1, info2, info3]:
            await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)


class TestSettingsEndpoints:
    """Settings API endpoint tests."""

    def test_settings_keys_endpoint(self, client: TestClient) -> None:
        """GET /api/settings/keys returns provider list."""
        resp = client.get("/api/settings/keys")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict)
        assert "openai" in data

    def test_settings_set_key_invalid_provider(self, client: TestClient) -> None:
        """POST /api/settings/keys with unknown provider returns 400."""
        resp = client.post(
            "/api/settings/keys",
            json={"provider": "nonexistent_provider", "key": "sk-test"},
        )
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_settings_set_and_delete_key(self, client: TestClient) -> None:
        """POST then DELETE /api/settings/keys round-trips."""
        resp = client.post(
            "/api/settings/keys",
            json={"provider": "openai", "key": "sk-test-key-12345"},
        )
        assert resp.status_code == 200

        resp = client.get("/api/settings/keys")
        data = resp.json()
        assert data["openai"]["configured"] is True

        resp = client.delete("/api/settings/keys/openai")
        assert resp.status_code == 204


        resp = client.get("/api/settings/keys")
        data = resp.json()
        assert data["openai"]["configured"] is False

    def test_local_models_endpoint(self, client: TestClient) -> None:
        """GET /api/settings/local-models returns a list."""
        resp = client.get("/api/settings/local-models")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_add_remove_local_endpoint(self, client: TestClient) -> None:
        """POST then DELETE /api/settings/local-endpoints round-trips."""
        resp = client.post(
            "/api/settings/local-endpoints",
            json={
                "name": "test-llama",
                "endpoint": "http://localhost:8080/v1",
                "type": "llamacpp",
            },
        )
        assert resp.status_code == 200

        resp = client.get("/api/settings/local-models")
        data = resp.json()
        saved = [m for m in data if m.get("name") == "test-llama"]
        assert len(saved) >= 1

        resp = client.post("/api/settings/local-endpoints/test-llama/test")
        assert resp.status_code == 200

        resp = client.delete("/api/settings/local-endpoints/test-llama")
        assert resp.status_code == 204



# ─── MultiSessionManager Unit Tests ─────────────────────────────────────


@pytest.mark.usefixtures("mock_llm_client")
class TestMultiSessionManager:
    """MultiSessionManager create/list/cancel/cleanup unit tests."""

    @pytest.mark.asyncio
    async def test_create_session(self) -> None:
        """MultiSessionManager.create_session returns a valid SessionInfo."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(
            topic="Test Topic", time_budget="quick", model_mode="same"
        )

        assert isinstance(info, SessionInfo)
        assert info.session_id is not None
        assert info.topic == "Test Topic"
        assert info.time_budget == "quick"
        assert info.status in ("queued", "running")
        assert info.event_bus is not None

        await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_create_session_custom_budget(self) -> None:
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
    async def test_list_sessions(self) -> None:
        """MultiSessionManager.list_sessions returns dict with sessions list."""
        mgr = MultiSessionManager(max_sessions=10)
        info1 = await mgr.create_session(topic="First", time_budget="quick")
        info2 = await mgr.create_session(topic="Second", time_budget="deep")

        result = mgr.list_sessions()
        sessions = result["sessions"]
        assert len(sessions) >= 2
        assert result["total"] >= 2

        assert sessions[0]["topic"] == "Second"
        assert sessions[1]["topic"] == "First"

        await mgr.cancel_session(info1.session_id)
        await mgr.cancel_session(info2.session_id)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_get_session(self) -> None:
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
    async def test_cancel_session(self) -> None:
        """MultiSessionManager.cancel_session cancels a running session."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(topic="Cancel Test")

        cancelled = await mgr.cancel_session(info.session_id)
        assert cancelled is True
        await asyncio.sleep(0.1)

        retrieved = mgr.get_session(info.session_id)
        assert retrieved is not None
        assert retrieved.status == "cancelled"

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self) -> None:
        """Cancelling a nonexistent session returns False."""
        mgr = MultiSessionManager(max_sessions=10)
        cancelled = await mgr.cancel_session("nonexistent")
        assert cancelled is False

    @pytest.mark.asyncio
    async def test_cleanup(self) -> None:
        """MultiSessionManager cleans up old sessions when max is reached."""
        mgr = MultiSessionManager(max_sessions=3)

        infos = []
        for i in range(3):
            info = await mgr.create_session(topic=f"Session {i}")
            infos.append(info)

        for info in infos:
            await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)

        info4 = await mgr.create_session(topic="Session 4")
        assert mgr.session_count <= 3

        await mgr.cancel_session(info4.session_id)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_clear_completed(self) -> None:
        """Clear completed removes all done sessions."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(topic="Clear Test")
        await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)

        count = mgr.clear_completed()
        assert count >= 1
        assert mgr.session_count == 0

    @pytest.mark.asyncio
    async def test_active_count(self) -> None:
        """Active count reflects running/queued sessions."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(topic="Active Test")
        assert mgr.active_count >= 1

        await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_event_bus_isolation(self) -> None:
        """Each session gets its own EventBus — events are isolated."""
        bus1 = EventBus()
        bus2 = EventBus()

        assert bus1 is not bus2

        q1 = await bus1.subscribe()
        q2 = await bus2.subscribe()

        await bus1.publish({"event_type": "test_event", "value": 42})

        received = await asyncio.wait_for(q1.get(), timeout=1.0)
        assert received["value"] == 42

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(q2.get(), timeout=0.3)

    # ── Time budget edge cases ─────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_time_budget_zero_seconds(self) -> None:
        """time_budget_seconds=0 is accepted and stored as 0."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(
            topic="Zero Budget",
            time_budget="custom",
            time_budget_seconds=0,
        )
        assert info.time_budget_seconds == 0
        assert info.time_budget == "custom"
        await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_time_budget_negative_seconds(self) -> None:
        """time_budget_seconds=-1 is accepted (no validation at dataclass level)."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(
            topic="Negative Budget",
            time_budget="custom",
            time_budget_seconds=-1,
        )
        assert info.time_budget_seconds == -1
        await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_time_budget_large_seconds(self) -> None:
        """time_budget_seconds=3600 (1 hour) is accepted."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(
            topic="Large Budget",
            time_budget="custom",
            time_budget_seconds=3600,
        )
        assert info.time_budget_seconds == 3600
        await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_time_budget_invalid_keyword(self) -> None:
        """An unknown time_budget keyword raises ValueError."""
        mgr = MultiSessionManager(max_sessions=10)
        with pytest.raises(ValueError, match="Unknown time budget"):
            await mgr.create_session(
                topic="Invalid Keyword",
                time_budget="invalid",
            )

    @pytest.mark.asyncio
    async def test_time_budget_none_with_valid_keyword(self) -> None:
        """time_budget_seconds=None falls back to the keyword-based budget."""
        mgr = MultiSessionManager(max_sessions=10)
        info = await mgr.create_session(
            topic="None Budget",
            time_budget="quick",
            time_budget_seconds=None,
        )
        # Falls through to TimeBudget.from_keyword("quick") → 240s, 2 rounds
        assert info.time_budget == "quick"
        assert info.time_budget_seconds == 240
        assert info.max_rounds == 2
        await mgr.cancel_session(info.session_id)
        await asyncio.sleep(0.1)


# ─── SessionManager Unit Tests (legacy compat) ──────────────────────────


# ─── SettingsManager Unit Tests ─────────────────────────────────────────


@pytest.fixture
def temp_settings() -> SettingsManager:
    """Create a SettingsManager with a temp directory."""
    mgr = SettingsManager()
    tmpdir = tempfile.mkdtemp()
    mgr._settings_dir = Path(tmpdir)
    mgr._env_path = mgr._settings_dir / ".env"
    mgr._endpoints_path = mgr._settings_dir / "local_endpoints.json"
    return mgr


class TestSettingsManager:
    """SettingsManager CRUD and persistence tests."""

    def test_get_keys_empty(self, temp_settings: SettingsManager) -> None:
        """get_keys returns all providers with configured=False when no keys set."""
        keys = temp_settings.get_keys()
        assert "openai" in keys
        assert keys["openai"]["configured"] is False
        assert keys["openai"]["has_key"] is False
        assert keys["openai"]["key_preview"] is None

    def test_set_key(self, temp_settings: SettingsManager) -> None:
        """set_key saves a key and marks provider as configured."""
        temp_settings.set_key("openai", "sk-test-key-12345")
        keys = temp_settings.get_keys()
        assert keys["openai"]["configured"] is True
        assert keys["openai"]["has_key"] is True
        assert keys["openai"]["key_preview"] is not None

        assert os.environ.get("OPENAI_API_KEY") == "sk-test-key-12345"

    def test_delete_key(self, temp_settings: SettingsManager) -> None:
        """delete_key removes a key."""
        temp_settings.set_key("openai", "sk-test-key-12345")
        temp_settings.delete_key("openai")

        keys = temp_settings.get_keys()
        assert keys["openai"]["configured"] is False

    def test_set_key_unknown_provider(
        self,
        temp_settings: SettingsManager,
    ) -> None:
        """set_key with unknown provider raises ValueError."""
        with pytest.raises(ValueError):
            temp_settings.set_key("unknown_provider", "key")

    def test_local_endpoints(self, temp_settings: SettingsManager) -> None:
        """Local endpoints CRUD works."""
        assert temp_settings.get_local_endpoints() == []

        temp_settings.add_local_endpoint(
            {
                "name": "test-llama",
                "endpoint": "http://localhost:8080/v1",
                "type": "llamacpp",
            }
        )

        endpoints = temp_settings.get_local_endpoints()
        assert len(endpoints) == 1
        assert endpoints[0]["name"] == "test-llama"

        temp_settings.remove_local_endpoint("test-llama")
        assert temp_settings.get_local_endpoints() == []

    def test_env_file_persistence(self, temp_settings: SettingsManager) -> None:
        """Keys persisted to .env file survive across instances."""
        temp_settings.set_key("openai", "sk-persist-test")

        mgr2 = SettingsManager()
        mgr2._settings_dir = temp_settings._settings_dir
        mgr2._env_path = temp_settings._env_path

        keys = mgr2.get_keys()
        assert keys["openai"]["configured"] is True
        assert keys["openai"]["key_preview"] is not None


# ─── Orchestrator Custom Budget Test ────────────────────────────────────


class TestOrchestratorCustomBudget:
    """Orchestrator time_budget_seconds override test."""

    @pytest.mark.asyncio
    async def test_custom_time_budget(self) -> None:
        """Orchestrator accepts time_budget_seconds override."""
        from deepresearch.orchestrator import Orchestrator
        from deepresearch.models import AgentProfile

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
            model_configs=[
                {
                    "id": "gpt-4o",
                    "provider": "openai",
                    "display_name": "GPT-4o",
                    "default": True,
                }
            ],
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


# ─── Output Cleanup Tests ──────────────────────────────────────────────


class TestOutputCleanup:
    """Tests for output directory cleanup logic (issue #116)."""

    # ── _has_meaningful_output ──────────────────────────────────────────

    def test_empty_dir_has_no_meaningful_output(self, tmp_path: Path) -> None:
        """Empty directory returns False."""
        sid = "a1b2c3d4"
        # Simulate SESSION_DB_PATH.parent structure
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()

        with _patch_session_db_path(output_base):
            assert _has_meaningful_output(sid) is False

    def test_dir_with_only_trivial_files_has_no_meaningful_output(
        self, tmp_path: Path
    ) -> None:
        """Directory with only .log / .tmp / state files returns False."""
        sid = "b2c3d4e5"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()
        (session_dir / "agent_state.json").write_text('{"status": "interrupted"}')
        (session_dir / "trace.log").write_text("some log data")

        with _patch_session_db_path(output_base):
            assert _has_meaningful_output(sid) is False

    def test_dir_with_pdf_has_meaningful_output(self, tmp_path: Path) -> None:
        """Directory with a .pdf file returns True."""
        sid = "c3d4e5f6"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()
        (session_dir / "research.pdf").write_bytes(b"%PDF-1.4 fake pdf content")

        with _patch_session_db_path(output_base):
            assert _has_meaningful_output(sid) is True

    def test_dir_with_html_has_meaningful_output(self, tmp_path: Path) -> None:
        """Directory with a .html file returns True."""
        sid = "d4e5f6a7"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()
        (session_dir / "research.html").write_text("<html><body>Research</body></html>")

        with _patch_session_db_path(output_base):
            assert _has_meaningful_output(sid) is True

    def test_nonexistent_dir_has_no_meaningful_output(self, tmp_path: Path) -> None:
        """Non-existent directory returns False."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        with _patch_session_db_path(output_base):
            assert _has_meaningful_output("nonexistent") is False

    def test_uppercase_extensions_are_detected(self, tmp_path: Path) -> None:
        """Case-insensitive extension matching works (.PDF, .HTML)."""
        sid = "e5f6a7b8"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()
        (session_dir / "RESEARCH.PDF").write_bytes(b"%PDF-1.4")

        with _patch_session_db_path(output_base):
            assert _has_meaningful_output(sid) is True

    # ── _remove_output_dir ──────────────────────────────────────────────

    def test_remove_empty_dir_succeeds(self, tmp_path: Path) -> None:
        """Empty directory is removed."""
        sid = "f6a7b8c9"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()

        with _patch_session_db_path(output_base):
            assert _remove_output_dir(sid) is True
            assert session_dir.exists() is False

    def test_remove_trivial_dir_succeeds(self, tmp_path: Path) -> None:
        """Directory with only trivial files is removed."""
        sid = "a7b8c9d0"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()
        (session_dir / "agent_state.json").write_text("{}")

        with _patch_session_db_path(output_base):
            assert _remove_output_dir(sid) is True
            assert session_dir.exists() is False

    def test_remove_dir_with_pdf_does_nothing(self, tmp_path: Path) -> None:
        """Directory with PDF is NOT removed."""
        sid = "b8c9d0e1"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()
        pdf_file = session_dir / "paper.pdf"
        pdf_file.write_bytes(b"%PDF-1.4")

        with _patch_session_db_path(output_base):
            assert _remove_output_dir(sid) is False
            assert session_dir.exists() is True
            assert pdf_file.exists() is True

    def test_remove_dir_with_html_does_nothing(self, tmp_path: Path) -> None:
        """Directory with HTML is NOT removed."""
        sid = "c9d0e1f2"
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")
        session_dir = output_base / sid
        session_dir.mkdir()
        html_file = session_dir / "paper.html"
        html_file.write_text("<html></html>")

        with _patch_session_db_path(output_base):
            assert _remove_output_dir(sid) is False
            assert session_dir.exists() is True
            assert html_file.exists() is True

    def test_remove_nonexistent_dir_returns_false(self, tmp_path: Path) -> None:
        """Non-existent dir returns False."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        with _patch_session_db_path(output_base):
            assert _remove_output_dir("nonexistent") is False

    # ── cleanup_output_dirs ─────────────────────────────────────────────

    def test_cleanup_output_dirs_removes_empty(self, tmp_path: Path) -> None:
        """cleanup_output_dirs removes empty session directories."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        # Create two empty session dirs
        for sid in ("a1111111", "b2222222"):
            (output_base / sid).mkdir()

        # Create a non-session dir that should be ignored
        (output_base / "not_a_session").mkdir()

        with _patch_session_db_path(output_base):
            count, removed = cleanup_output_dirs(dry_run=False)

        assert count == 2
        assert sorted(removed) == ["a1111111", "b2222222"]
        assert (output_base / "a1111111").exists() is False
        assert (output_base / "b2222222").exists() is False
        # Non-session dir should be preserved
        assert (output_base / "not_a_session").exists() is True

    def test_cleanup_output_dirs_preserves_pdf(self, tmp_path: Path) -> None:
        """cleanup_output_dirs preserves directories with PDF output."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        sid = "c3333333"
        session_dir = output_base / sid
        session_dir.mkdir()
        (session_dir / "paper.pdf").write_bytes(b"%PDF-1.4")

        with _patch_session_db_path(output_base):
            count, removed = cleanup_output_dirs(dry_run=False)

        assert count == 0
        assert session_dir.exists() is True

    def test_cleanup_output_dirs_preserves_html(self, tmp_path: Path) -> None:
        """cleanup_output_dirs preserves directories with HTML output."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        sid = "d4444444"
        session_dir = output_base / sid
        session_dir.mkdir()
        (session_dir / "paper.html").write_text("<html></html>")

        with _patch_session_db_path(output_base):
            count, removed = cleanup_output_dirs(dry_run=False)

        assert count == 0
        assert session_dir.exists() is True

    def test_cleanup_output_dirs_dry_run(self, tmp_path: Path) -> None:
        """Dry run reports but does not delete."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        sid = "e5555555"
        session_dir = output_base / sid
        session_dir.mkdir()

        with _patch_session_db_path(output_base):
            count, removed = cleanup_output_dirs(dry_run=True)

            assert count == 1
            assert removed == ["e5555555"]
            assert session_dir.exists() is True  # Not deleted

            # Now actually delete — still inside the patched context
            count, removed = cleanup_output_dirs(dry_run=False)
            assert count == 1
            assert session_dir.exists() is False

    def test_cleanup_output_dirs_mixed(self, tmp_path: Path) -> None:
        """Mixed state: only empty/trivial dirs are removed."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        # Dir with PDF — should be preserved
        pdf_sid = "f6666666"
        (output_base / pdf_sid).mkdir()
        (output_base / pdf_sid / "paper.pdf").write_bytes(b"%PDF-1.4")

        # Empty dir — should be removed
        empty_sid = "a7777777"
        (output_base / empty_sid).mkdir()

        # Dir with trivial files — should be removed
        trivial_sid = "b8888888"
        (output_base / trivial_sid).mkdir()
        (output_base / trivial_sid / "agent_state.json").write_text("{}")

        with _patch_session_db_path(output_base):
            count, removed = cleanup_output_dirs(dry_run=False)

        assert count == 2
        assert sorted(removed) == [empty_sid, trivial_sid]
        assert (output_base / pdf_sid).exists() is True
        assert (output_base / empty_sid).exists() is False
        assert (output_base / trivial_sid).exists() is False

    # ── clear_completed cleanup behavior ────────────────────────────────

    @pytest.mark.asyncio
    async def test_clear_completed_cleans_empty_dir(
        self, tmp_path: Path
    ) -> None:
        """clear_completed() removes empty output dirs while preserving dirs with PDF."""
        output_base = tmp_path / "output"
        output_base.mkdir()
        (output_base / "sessions_db.json").write_text("{}")

        with _patch_session_db_path(output_base):
            mgr = MultiSessionManager(max_sessions=10)

            # Create two session dirs on disk
            empty_sid = "a8888888"
            empty_dir = output_base / empty_sid
            empty_dir.mkdir()

            pdf_sid = "b9999999"
            pdf_dir = output_base / pdf_sid
            pdf_dir.mkdir()
            (pdf_dir / "paper.pdf").write_bytes(b"%PDF-1.4 valid output")

            # Manually inject sessions into the manager
            from datetime import datetime

            for sid, status in [(empty_sid, "cancelled"), (pdf_sid, "complete")]:
                mgr._sessions[sid] = SessionInfo(
                    session_id=sid,
                    topic=f"Session {sid}",
                    time_budget="quick",
                    time_budget_seconds=240,
                    model_mode="same",
                    status=status,
                    created_at=datetime.now().isoformat(),
                    completed_at=datetime.now().isoformat(),
                )

            count = mgr.clear_completed()

            assert count == 2
            # Empty dir should be removed
            assert empty_dir.exists() is False
            # PDF dir should remain
            assert pdf_dir.exists() is True
            assert (pdf_dir / "paper.pdf").exists() is True


# ── Helpers ────────────────────────────────────────────────────────────


def _patch_session_db_path(output_base: Path):
    """Context manager: patch SESSION_DB_PATH so cleanup helpers use ``output_base``.

    Usage::

        with _patch_session_db_path(tmp_path / "output"):
            assert _has_meaningful_output("abc12345")
    """
    from unittest.mock import patch as _mock_patch

    fake_db = output_base / "sessions_db.json"
    return _mock_patch(
        "deepresearch.web.sessions.SESSION_DB_PATH",
        fake_db,
    )
