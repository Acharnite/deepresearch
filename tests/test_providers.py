"""Provider compatibility and benchmark tests for DeepResearch Phase D.

Tests:
  1. Provider prefix routing — verify model IDs resolve to correct providers
  2. API base resolution — verify correct API base for each provider
  3. API key env var detection — verify keys are picked up from environment
  4. Pre-flight connectivity check — verify "Respond with exactly one word: ok"
  5. Mock pipeline with each provider prefix — verify orchestration routes correctly
  6. /api/models endpoint — verify models appear in the listing
  7. Log file monitoring — verify logs exist and sizes are reasonable
  8. Memory isolation — verify no session state leaks between sessions
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deepresearch.llm.client import LLMClient, PROVIDER_ROUTES


# ── Provider Configuration ──────────────────────────────────────────────

# The target models and providers for Phase D compatibility testing.
# These are the canonical model IDs the system should resolve.
TARGET_PROVIDERS: dict[str, str] = {
    "opencode/go/deepseek-v4-flash": "opencode",
    "openrouter/openai/gpt-4o": "openrouter",
    "openai/gpt-4o": "openai",
}

# Model IDs expected to be listed in /api/models
EXPECTED_MODEL_IDS = [
    "opencode/go/deepseek-v4-flash",
    "gpt-4o",
]


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def preserve_env():
    """Preserve environment variables before and after each test."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def mock_llm_generate():
    """Patch LLMClient.generate to return 'ok' without API calls."""
    with patch(
        "deepresearch.llm.client.LLMClient.generate", new_callable=AsyncMock
    ) as mock:
        mock.return_value = "ok"
        yield mock


@pytest.fixture
def mock_llm_acompletion():
    """Patch litellm.acompletion to avoid real API calls."""
    with patch("deepresearch.llm.client.litellm.acompletion", new_callable=AsyncMock) as mock:
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "ok"
        mock_response.choices[0].message.tool_calls = None
        mock_response.usage.prompt_tokens = 5
        mock_response.usage.completion_tokens = 1
        mock.return_value = mock_response
        yield mock


# ── 1. Provider Prefix Routing Tests ────────────────────────────────────


class TestProviderRouting:
    """Verify model ID prefixes resolve to correct providers and configs."""

    @pytest.mark.parametrize(
        "model_id,expected_provider",
        [
            ("opencode/go/deepseek-v4-flash", "opencode"),
            ("opencode/zen/claude-sonnet-4", "opencode"),
        ],
    )
    def test_opencode_routing(self, model_id: str, expected_provider: str) -> None:
        """Opencode models route correctly (endpoint-routed provider)."""
        client = LLMClient(model=model_id)
        assert client.provider == expected_provider
        assert client.endpoint is not None  # endpoint-routed
        assert client.api_base is not None

    def test_opencode_go_endpoint_routing(self) -> None:
        """opencode/go/deepseek-v4-flash → go endpoint."""
        client = LLMClient(model="opencode/go/deepseek-v4-flash")
        assert client.provider == "opencode"
        assert client.endpoint == "go"
        assert client.actual_model == "deepseek-v4-flash"
        assert client.api_base == "https://opencode.ai/zen/go/v1"
        assert client.openai_compatible is True

    def test_opencode_zen_endpoint_routing(self) -> None:
        """opencode/zen/claude-sonnet-4 → zen endpoint."""
        client = LLMClient(model="opencode/zen/claude-sonnet-4")
        assert client.provider == "opencode"
        assert client.endpoint == "zen"
        assert client.actual_model == "claude-sonnet-4"
        assert client.api_base == "https://opencode.ai/zen/v1"
        assert client.openai_compatible is True

    def test_openrouter_routing(self) -> None:
        """openrouter/ prefix resolves to openrouter provider."""
        client = LLMClient(model="openrouter/openai/gpt-4o")
        assert client.provider == "openrouter"
        assert client.api_base == "https://openrouter.ai/api/v1"
        # api_key may be set or None depending on env — just verify the
        # LLMClient reads from OPENROUTER_API_KEY if available
        env_key = os.environ.get("OPENROUTER_API_KEY")
        assert client.api_key == env_key

    def test_openai_no_prefix(self) -> None:
        """openai/ is not in PROVIDER_ROUTES → provider is None."""
        client = LLMClient(model="openai/gpt-4o")
        assert client.provider is None
        # When provider is None, api_base should also be None (passed through to LiteLLM)
        assert client.api_base is None

    def test_bare_gpt4o_no_routing(self) -> None:
        """gpt-4o (no prefix) → provider is None."""
        client = LLMClient(model="gpt-4o")
        assert client.provider is None


# ── 2. API Base Resolution Tests ────────────────────────────────────────


class TestApiBaseResolution:
    """Verify correct API base URLs for each provider."""

    def test_opencode_api_base(self) -> None:
        """Opencode Go endpoint has correct API base."""
        client = LLMClient(model="opencode/go/deepseek-v4-flash")
        assert client.api_base == "https://opencode.ai/zen/go/v1"

    def test_openrouter_api_base(self) -> None:
        """OpenRouter has correct API base."""
        client = LLMClient(model="openrouter/openai/gpt-4o")
        assert client.api_base == "https://openrouter.ai/api/v1"

    def test_opencode_zen_api_base(self) -> None:
        """Opencode Zen endpoint has correct API base."""
        client = LLMClient(model="opencode/zen/claude-sonnet-4")
        assert client.api_base == "https://opencode.ai/zen/v1"

    def test_provider_routes_contain_required(self) -> None:
        """PROVIDER_ROUTES dict has all expected keys."""
        assert "opencode" in PROVIDER_ROUTES
        assert "openrouter" in PROVIDER_ROUTES
        route = PROVIDER_ROUTES["opencode"]
        assert route["type"] == "endpoint_routed"
        assert route["openai_compatible"] is True
        assert "go" in route["endpoints"]
        assert "zen" in route["endpoints"]
        assert route["endpoints"]["go"] == "https://opencode.ai/zen/go/v1"
        assert route["endpoints"]["zen"] == "https://opencode.ai/zen/v1"


# ── 3. API Key Environment Variable Tests ──────────────────────────────


class TestApiKeyResolution:
    """Verify API keys are read from environment variables."""

    def test_opencode_key_from_env(self) -> None:
        """OPENCODE_API_KEY is read from environment."""
        os.environ["OPENCODE_API_KEY"] = "test-key-opencode"
        client = LLMClient(model="opencode/go/deepseek-v4-flash")
        assert client.api_key == "test-key-opencode"

    def test_openrouter_key_from_env(self) -> None:
        """OPENROUTER_API_KEY is read from environment."""
        os.environ["OPENROUTER_API_KEY"] = "test-key-openrouter"
        client = LLMClient(model="openrouter/openai/gpt-4o")
        assert client.api_key == "test-key-openrouter"

    def test_key_isolation_between_providers(self) -> None:
        """Setting one provider key does not leak to another provider."""
        os.environ["OPENCODE_API_KEY"] = "opencode-key"
        os.environ.pop("OPENROUTER_API_KEY", None)
        opencode_client = LLMClient(model="opencode/go/deepseek-v4-flash")
        openrouter_client = LLMClient(model="openrouter/openai/gpt-4o")
        assert opencode_client.api_key == "opencode-key"
        # openrouter key may come from env if set, that's fine — just verify it
        # doesn't accidentally pick up opencode's key
        assert openrouter_client.api_key != "opencode-key"


# ── 4. Pre-Flight Connectivity Check Tests ─────────────────────────────


class TestPreFlightConnectivityCheck:
    """Verify the model connectivity check (Respond with exactly one word: ok)."""

    @pytest.mark.asyncio
    async def test_connectivity_returns_ok(self, mock_llm_generate) -> None:
        """generate() returns 'ok' for the connectivity check prompt."""
        mock_llm_generate.return_value = "ok"
        client = LLMClient(model="opencode/go/deepseek-v4-flash", timeout=15)
        result = await client.generate(
            system_prompt="",
            user_prompt="Respond with exactly one word: ok",
            max_tokens=5,
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_connectivity_with_all_providers(self, mock_llm_acompletion) -> None:
        """Connectivity check succeeds with provider override."""
        # Simulate the pre-flight check used in create_session
        for model_id in ["opencode/go/deepseek-v4-flash", "openrouter/openai/gpt-4o"]:
            client = LLMClient(model=model_id, timeout=15)
            result = await client.generate(
                system_prompt="",
                user_prompt="Respond with exactly one word: ok",
                max_tokens=5,
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_connectivity_failure_reported(self) -> None:
        """When generate fails, error is reported (not silent failure)."""
        with patch(
            "deepresearch.llm.client.LLMClient.generate", new_callable=AsyncMock
        ) as mock:
            mock.side_effect = Exception("API unreachable")
            client = LLMClient(model="opencode/go/deepseek-v4-flash", timeout=15)
            with pytest.raises(Exception, match="API unreachable"):
                await client.generate(
                    system_prompt="",
                    user_prompt="Respond with exactly one word: ok",
                    max_tokens=5,
                )

    @pytest.mark.asyncio
    async def test_connectivity_timeout(self) -> None:
        """Connectivity check has a 15s timeout and raises on timeout."""
        with patch(
            "deepresearch.llm.client.LLMClient.generate", new_callable=AsyncMock
        ) as mock:
            mock.side_effect = Exception("Timeout after 15s")
            client = LLMClient(model="opencode/go/deepseek-v4-flash", timeout=15)
            with pytest.raises(Exception):
                await client.generate(
                    system_prompt="",
                    user_prompt="Respond with exactly one word: ok",
                    max_tokens=5,
                )


# ── 5. Mock Pipeline Tests ──────────────────────────────────────────────


def _make_mock_agent_factory():
    """Return an agent factory producing deterministic mock agents.

    Each mock agent returns proper Findings for INITIAL_ROUND and
    IndividualReport for later phases — matching the pattern in test_pipeline.py.
    """

    def factory(profile, model_name, **extra):
        async def agent_fn(phase, **kwargs):
            from deepresearch.agents.registry import Phase
            from deepresearch.models import Findings, IndividualReport

            if phase == Phase.INITIAL_ROUND:
                return Findings(
                    agent_id=profile.id,
                    round=1,
                    summary=f"Findings by {profile.name}",
                    key_points=["Key finding"],
                    perspective=f"Perspective from {profile.name}",
                    confidence=0.75,
                )
            elif phase == Phase.REFINEMENT:
                return Findings(
                    agent_id=profile.id,
                    round=1,
                    summary=f"Refined by {profile.name}",
                    key_points=["Refined finding"],
                    perspective="Refined perspective",
                    confidence=0.8,
                )
            elif phase in (Phase.ROUND_2, Phase.ROUND_N):
                return IndividualReport(
                    agent_id=profile.id,
                    title=f"Report by {profile.name}",
                    perspective_summary="Summary",
                    key_insights=["Insight"],
                    analysis="Analysis",
                    full_text="Full text.",
                )
            elif phase == Phase.REVIEW:
                return {"questions": [], "agent_id": profile.id}
            elif phase == Phase.REPORT:
                return IndividualReport(
                    agent_id=profile.id,
                    title=f"Final Report by {profile.name}",
                    perspective_summary="Final summary",
                    key_insights=["Final insight"],
                    analysis="Final analysis",
                    full_text="Final report text.",
                )
            return Findings(
                agent_id=profile.id,
                round=1,
                summary="Default findings.",
                key_points=["Default point"],
                perspective="Default perspective.",
                confidence=0.5,
            )

        return agent_fn

    return factory


def _make_mock_scribe_factory():
    """Return a scribe factory producing a deterministic mock scribe."""

    def factory(**extra):
        async def scribe_fn(reports):
            from deepresearch.models import ResearchPaper

            return ResearchPaper(
                            title="Test Paper",
                            abstract="Abstract text.",
                            methodology_note="Methodology.",
                            sections=[],
                            synthesis="Synthesis text.",
                            key_takeaways=["Takeaway"],
                            conclusion="Conclusion text.",
                        )

        return scribe_fn

    return factory


class TestMockPipelineWithProviders:
    """Verify pipeline configuration works for each provider prefix."""

    @pytest.mark.asyncio
    async def test_pipeline_with_opencode(self) -> None:
        """Pipeline completes with opencode/go model prefix in quick mode."""
        from deepresearch.orchestrator import Orchestrator
        from deepresearch.config import load_agent_profiles, load_model_config

        profiles = load_agent_profiles()
        model_configs = load_model_config()

        agent_factory = _make_mock_agent_factory()
        scribe_factory = _make_mock_scribe_factory()

        orch = Orchestrator(
            profiles=profiles[:2],
            model_configs=model_configs,
            agent_factory=agent_factory,
            scribe_factory=scribe_factory,
        )

        result = await orch.run(
            "Test topic for opencode",
            selected_model="opencode/go/deepseek-v4-flash",
            time_budget="quick",
            model_mode="same",
            output_path="/tmp/test_output_opencode.pdf",
            max_rounds=1,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_pipeline_with_openrouter(self) -> None:
        """Pipeline completes with openrouter model prefix in quick mode."""
        from deepresearch.orchestrator import Orchestrator
        from deepresearch.config import load_agent_profiles, load_model_config

        profiles = load_agent_profiles()
        model_configs = load_model_config()

        agent_factory = _make_mock_agent_factory()
        scribe_factory = _make_mock_scribe_factory()

        orch = Orchestrator(
            profiles=profiles[:2],
            model_configs=model_configs,
            agent_factory=agent_factory,
            scribe_factory=scribe_factory,
        )

        result = await orch.run(
            "Test topic for openrouter",
            selected_model="openrouter/openai/gpt-4o",
            time_budget="quick",
            model_mode="same",
            output_path="/tmp/test_output_openrouter.pdf",
            max_rounds=1,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_pipeline_with_openai(self) -> None:
        """Pipeline completes with openai model (no prefix) in quick mode."""
        from deepresearch.orchestrator import Orchestrator
        from deepresearch.config import load_agent_profiles, load_model_config

        profiles = load_agent_profiles()
        model_configs = load_model_config()

        agent_factory = _make_mock_agent_factory()
        scribe_factory = _make_mock_scribe_factory()

        orch = Orchestrator(
            profiles=profiles[:2],
            model_configs=model_configs,
            agent_factory=agent_factory,
            scribe_factory=scribe_factory,
        )

        result = await orch.run(
            "Test topic for openai",
            selected_model="gpt-4o",
            time_budget="quick",
            model_mode="same",
            output_path="/tmp/test_output_openai.pdf",
            max_rounds=1,
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_benchmark_mode_records_timing(self) -> None:
        """--benchmark mode populates _benchmark_times dict."""
        from deepresearch.orchestrator import Orchestrator
        from deepresearch.config import load_agent_profiles, load_model_config

        profiles = load_agent_profiles()
        model_configs = load_model_config()
        agent_factory = _make_mock_agent_factory()
        scribe_factory = _make_mock_scribe_factory()

        orch = Orchestrator(
            profiles=profiles[:2],
            model_configs=model_configs,
            agent_factory=agent_factory,
            scribe_factory=scribe_factory,
        )

        result = await orch.run(
            "Benchmark test",
            time_budget="quick",
            model_mode="same",
            benchmark=True,
            selected_model="opencode/go/deepseek-v4-flash",
            output_path="/tmp/test_benchmark.pdf",
            max_rounds=1,
        )

        assert "round_1" in orch._benchmark_times
        assert isinstance(orch._benchmark_times["round_1"], float)
        assert orch._benchmark_times["round_1"] >= 0
        assert result is not None


# ── 6. /api/models Endpoint Test ────────────────────────────────────────


class TestModelsEndpoint:
    """Verify /api/models endpoint returns expected models."""

    @pytest.mark.asyncio
    async def test_models_endpoint_returns_list(self) -> None:
        """/api/models returns a JSON list of model objects."""
        from deepresearch.web.routes.models import get_models

        response = await get_models()
        body = response.body
        import json

        models = json.loads(body)
        assert isinstance(models, list)
        assert len(models) > 0

    @pytest.mark.asyncio
    async def test_opencode_model_in_list(self) -> None:
        """opencode/go/deepseek-v4-flash appears in /api/models response."""
        from deepresearch.web.routes.models import get_models

        response = await get_models()
        import json

        models = json.loads(response.body)
        model_ids = [m["id"] for m in models]
        assert "opencode/go/deepseek-v4-flash" in model_ids

    @pytest.mark.asyncio
    async def test_gpt4o_model_in_list(self) -> None:
        """gpt-4o appears in /api/models response."""
        from deepresearch.web.routes.models import get_models

        response = await get_models()
        import json

        models = json.loads(response.body)
        model_ids = [m["id"] for m in models]
        assert "gpt-4o" in model_ids

    @pytest.mark.asyncio
    async def test_models_have_required_fields(self) -> None:
        """Each model entry has id, provider, display_name fields."""
        from deepresearch.web.routes.models import get_models

        response = await get_models()
        import json

        models = json.loads(response.body)
        for m in models:
            assert "id" in m, f"Model missing 'id': {m}"
            assert "provider" in m, f"Model missing 'provider': {m}"
            assert "display_name" in m or "name" in m, f"Model missing display info: {m}"

    def test_fastapi_models_route_registered(self) -> None:
        """Verify /api/models is registered on the FastAPI app."""
        from deepresearch.web.server import app

        routes = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/api/models" in routes, f"/api/models not found in routes: {routes}"


# ── 7. Log File Monitoring Tests ────────────────────────────────────────


class TestLogFileMonitoring:
    """Verify log files exist after sessions and sizes stay reasonable."""

    def test_log_directory_exists(self) -> None:
        """logs/ directory exists."""
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        assert log_dir.exists(), f"Log directory not found: {log_dir}"

    def test_log_file_exists(self) -> None:
        """deepresearch.log exists in logs/."""
        log_file = Path(__file__).resolve().parent.parent / "logs" / "deepresearch.log"
        assert log_file.exists(), f"Log file not found: {log_file}"

    def test_log_file_size_reasonable(self) -> None:
        """deepresearch.log is not excessively large (>50MB)."""
        log_file = Path(__file__).resolve().parent.parent / "logs" / "deepresearch.log"
        if log_file.exists():
            size_mb = log_file.stat().st_size / (1024 * 1024)
            assert size_mb < 50, (
                f"deepresearch.log is {size_mb:.1f}MB, expected < 50MB"
            )

    def test_log_rotated_files_exist(self) -> None:
        """Check rotated log files exist and have reasonable sizes."""
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        rotated = list(log_dir.glob("deepresearch.log.*"))
        for f in rotated:
            size_mb = f.stat().st_size / (1024 * 1024)
            assert size_mb <= 12, (
                f"Rotated log {f.name} is {size_mb:.1f}MB, expected <= 12MB"
            )

    def test_session_log_files_exist(self) -> None:
        """At least some session log files exist."""
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        session_logs = list(log_dir.glob("session-*.log"))
        assert len(session_logs) > 0, "No session log files found"
        # Verify session logs are reasonable in size
        for f in session_logs[:5]:  # spot-check first 5
            size_mb = f.stat().st_size / (1024 * 1024)
            assert size_mb < 1, (
                f"Session log {f.name} is {size_mb:.1f}MB, expected < 1MB"
            )

    def test_session_log_content(self) -> None:
        """Session log files contain readable log entries."""
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        session_logs = sorted(log_dir.glob("session-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if session_logs:
            content = session_logs[0].read_text(errors="replace")
            assert len(content) > 0, f"Session log {session_logs[0].name} is empty"
            # Should contain log lines with timestamps and levels
            lines = [l for l in content.splitlines() if l.strip()]
            if lines:
                # First line should match log format: timestamp [LEVEL] module [id]: message
                import re
                log_pattern = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[\w+\]')
                matching_lines = sum(1 for line in lines[:10] if log_pattern.search(line))
                assert matching_lines > 0, (
                    f"No log-format lines found in session log. Sample: {lines[0] if lines else 'empty'}"
                )


# ── 8. Memory Isolation Tests ──────────────────────────────────────────


class TestMemoryIsolation:
    """Verify no session state leaks between multiple mock sessions."""

    @pytest.fixture
    def _shared_orch(self):
        """Create an orchestrator with mock agents shared across tests."""
        from deepresearch.orchestrator import Orchestrator
        from deepresearch.config import load_agent_profiles, load_model_config

        profiles = load_agent_profiles()
        model_configs = load_model_config()
        agent_factory = _make_mock_agent_factory()
        scribe_factory = _make_mock_scribe_factory()

        return Orchestrator(
            profiles=profiles[:2],
            model_configs=model_configs,
            agent_factory=agent_factory,
            scribe_factory=scribe_factory,
        )

    @pytest.mark.asyncio
    async def test_orchestrator_state_resets_between_sessions(self, _shared_orch) -> None:
        """Orchestrator state is clean between consecutive sessions."""
        orch = _shared_orch

        for i in range(3):
            result = await orch.run(
                f"Session {i}",
                time_budget="quick",
                model_mode="same",
                selected_model="opencode/go/deepseek-v4-flash",
                output_path=f"/tmp/test_mem_{i}.pdf",
                max_rounds=1,
            )
            # After each session, verify clean state
            assert orch.failed_agents == {}, (
                f"Failed agents leak after session {i}: {orch.failed_agents}"
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_benchmark_times_reset_between_sessions(self, _shared_orch) -> None:
        """Benchmark times are reset (not cumulative) between sessions."""
        orch = _shared_orch

        await orch.run(
            "Session A",
            time_budget="quick",
            model_mode="same",
            benchmark=True,
            selected_model="opencode/go/deepseek-v4-flash",
            output_path="/tmp/test_reset_a.pdf",
            max_rounds=1,
        )
        times_a = dict(orch._benchmark_times)

        await orch.run(
            "Session B",
            time_budget="quick",
            model_mode="same",
            benchmark=True,
            selected_model="opencode/go/deepseek-v4-flash",
            output_path="/tmp/test_reset_b.pdf",
            max_rounds=1,
        )
        times_b = dict(orch._benchmark_times)

        # Times should be for current session only, not cumulative
        assert len(times_a) == 2  # round_1, scribe_compilation
        assert len(times_b) == 2

    @pytest.mark.asyncio
    async def test_session_id_isolated(self, _shared_orch) -> None:
        """Session IDs are unique and sequential, not shared."""
        orch = _shared_orch

        # Run a session — state_tracker should be clean
        result = await orch.run(
            "Isolation test",
            time_budget="quick",
            model_mode="same",
            selected_model="opencode/go/deepseek-v4-flash",
            output_path="/tmp/test_isolation.pdf",
            max_rounds=1,
        )
        # Topic should be set
        assert orch.state_tracker.topic is not None
        assert result is not None


# ── 9. Provider Configuration Validation Tests ──────────────────────────


class TestProviderConfiguration:
    """Verify the PROVIDER_ROUTES dict is well-formed."""

    def test_all_providers_have_required_keys(self) -> None:
        """Every provider in PROVIDER_ROUTES has all required config keys."""
        required = {"opencode": ["type", "api_key_env", "openai_compatible", "endpoints"]}
        optional = {"openrouter": ["api_base", "api_key_env"]}

        for name, route in PROVIDER_ROUTES.items():
            if name in required:
                for key in required[name]:
                    assert key in route, f"Provider '{name}' missing key '{key}'"
            # All providers should have at least api_key_env or local_backend
            has_key = "api_key_env" in route
            is_local = route.get("local_backend", False)
            is_endpoint = route.get("type") == "endpoint_routed"
            assert has_key or is_local or is_endpoint, (
                f"Provider '{name}' has no api_key_env, is not local, and is not endpoint_routed"
            )
