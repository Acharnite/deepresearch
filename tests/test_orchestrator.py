"""Tests for the DeepeResearch Orchestrator.

Covers:
  - Configuration flow (time budget, model mode selection)
  - Model assignment (same / random / manual)
  - Parallel execution with mock agents
  - Error handling (agent failure, graceful degradation)
  - Full session lifecycle (mock all agent calls)
  - Dry-run preview
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from deepresearch.models import (
    AgentProfile,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchPaper,
    ResearchTopic,
    SessionConfig,
    SharedKnowledge,
)
from deepresearch.orchestrator import Orchestrator, ConfigError


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def profiles() -> list[AgentProfile]:
    """Return a minimal set of agent profiles for testing."""
    return [
        AgentProfile(
            id="agent-a",
            name="Agent Alpha",
            emoji="🔬",
            persona_prompt="You are agent A.",
            methodology="Method A.",
            knowledge_base="Knowledge A.",
            bias_mitigation="Bias A.",
            voice="Voice A.",
            temperature=0.5,
        ),
        AgentProfile(
            id="agent-b",
            name="Agent Beta",
            emoji="🧪",
            persona_prompt="You are agent B.",
            methodology="Method B.",
            knowledge_base="Knowledge B.",
            bias_mitigation="Bias B.",
            voice="Voice B.",
            temperature=0.7,
        ),
    ]


@pytest.fixture
def model_configs() -> list[dict]:
    """Return minimal model definitions for testing."""
    return [
        {"id": "openrouter/opencode/go", "provider": "openrouter", "display_name": "Opencode Go (via OpenRouter)"},
        {"id": "gpt-4o", "provider": "openai", "display_name": "GPT-4o", "default": True},
        {"id": "claude-sonnet-4-20250514", "provider": "anthropic", "display_name": "Claude Sonnet 4"},
    ]


@pytest.fixture
def mock_prompt() -> MagicMock:
    """A callable that returns predictable values for prompts."""
    prompt = MagicMock()
    prompt.side_effect = ["medium", "same"]  # time_budget, model_mode
    return prompt


@pytest.fixture
def mock_findings() -> Findings:
    return Findings(
        agent_id="agent-a",
        round=1,
        summary="Test findings summary.",
        key_points=["Key point 1", "Key point 2"],
        perspective="A test perspective.",
        confidence=0.8,
    )


@pytest.fixture
def mock_followup() -> FollowUpQuestions:
    return FollowUpQuestions(agent_id="agent-a", questions=["What about X?", "Can we explore Y?"])


@pytest.fixture
def mock_report() -> IndividualReport:
    return IndividualReport(
        agent_id="agent-a",
        title="Test Report",
        perspective_summary="Summary.",
        key_insights=["Insight 1"],
        analysis="Analysis text.",
        full_text="Full report text.",
    )


@pytest.fixture
def mock_paper() -> ResearchPaper:
    return ResearchPaper(
        title="Test Paper",
        abstract="Abstract.",
        methodology_note="Method.",
        sections=[],
        synthesis="Synthesis.",
        key_takeaways=["Takeaway 1"],
        conclusion="Conclusion.",
    )


@pytest.fixture
def mock_agent_factory(mock_findings, mock_followup, mock_report) -> MagicMock:
    """Factory that creates AsyncMock agent functions.

    The created agents return different types based on the arguments:
      - ``ResearchTopic`` only → ``Findings`` (Round 1)
      - ``ResearchTopic, SharedKnowledge`` → ``IndividualReport`` (Round 2)
      - ``SharedKnowledge`` → ``FollowUpQuestions``
      - ``Findings`` → ``IndividualReport`` (report writing)
    """

    def factory(profile: AgentProfile, model_name: str, **extra):
        async def agent_fn(*args, **kwargs):
            # Inspect first arg type to determine behavior.
            if args:
                first = args[0]
                if isinstance(first, ResearchTopic) and (len(args) == 1 or args[1] is None):
                    return Findings(
                        agent_id=profile.id,
                        round=1,
                        summary=f"Findings by {profile.name}",
                        key_points=["Key point"],
                        perspective="Perspective",
                        confidence=0.7,
                    )
                elif isinstance(first, ResearchTopic) and len(args) > 1 and isinstance(args[1], SharedKnowledge):
                    return IndividualReport(
                        agent_id=profile.id,
                        title=f"Report by {profile.name}",
                        perspective_summary="Summary",
                        key_insights=["Insight"],
                        analysis="Analysis",
                        full_text="Full text",
                    )
                elif isinstance(first, SharedKnowledge):
                    return FollowUpQuestions(
                        agent_id=profile.id,
                        questions=["What else can we explore?"],
                    )
                elif isinstance(first, Findings):
                    return IndividualReport(
                        agent_id=profile.id,
                        title=f"Report by {profile.name}",
                        perspective_summary="Summary",
                        key_insights=["Insight"],
                        analysis="Analysis",
                        full_text="Full text",
                    )
            return mock_findings

        agent_fn.__name__ = f"agent_{profile.id}"
        return agent_fn

    return factory


@pytest.fixture
def mock_scribe_factory(mock_paper) -> MagicMock:
    """Factory that creates a scribe returning a ResearchPaper."""

    def factory(**extra):
        async def scribe(reports):
            return ResearchPaper(
                title="Compiled Paper",
                abstract=f"Synthesis of {len(reports)} agents.",
                methodology_note="Method.",
                sections=[],
                synthesis="Synthesis.",
                key_takeaways=["Takeaway"],
                conclusion="Conclusion.",
            )

        return scribe

    return factory


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestConfigure:
    """Configuration flow — time budget prompts, model mode selection."""

    def test_configure_with_overrides(self, profiles, model_configs):
        """Configure should use overrides when provided (no interactive prompts)."""
        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
        )
        config = asyncio.run(orch.configure(
            "Test topic",
            time_budget="deep",
            model_mode="random",
        ))
        assert isinstance(config, SessionConfig)
        assert config.topic.question == "Test topic"
        assert config.topic.time_budget == "deep"
        assert config.topic.model_mode == "random"
        assert config.time_budget_seconds == 480  # deep → 480s
        assert len(config.agent_profiles) == 2
        assert len(config.agent_models) == 2

    def test_configure_defaults(self, profiles, model_configs):
        """Configure should apply defaults when no overrides given (but still need prompts)."""
        # Simulate two prompts: first for time_budget, second for model_mode.
        prompt_responses = iter(["medium", "same"])

        def mock_prompt(msg, **kw):
            return next(prompt_responses)

        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
            prompt_func=mock_prompt,
        )
        config = asyncio.run(orch.configure("Test topic"))
        assert config.topic.time_budget == "medium"
        assert config.topic.model_mode == "same"

    def test_configure_empty_profiles_raises(self):
        """Configure should raise if no profiles are loaded."""
        orch = Orchestrator(profiles=[], model_configs=[{"id": "gpt-4o", "default": True}])
        with pytest.raises(ConfigError, match="No agent profiles loaded"):
            asyncio.run(orch.configure("Test topic"))

    def test_configure_empty_models_raises(self, profiles):
        """Configure should raise if no models are loaded."""
        orch = Orchestrator(profiles=profiles, model_configs=[])
        with pytest.raises(ConfigError, match="No model configurations loaded"):
            asyncio.run(orch.configure("Test topic"))

    def test_state_transitions(self, profiles, model_configs):
        """State should be CONFIGURING during configure()."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        assert orch.state == "IDLE"
        asyncio.run(orch.configure("Test", time_budget="quick", model_mode="same"))
        assert orch.state == "CONFIGURING"  # stays CONFIGURING after configure returns
        # (run() transitions to subsequent states)


class TestAssignModels:
    """Model assignment — all 3 modes produce correct results."""

    def test_same_mode(self, profiles, model_configs):
        """Same mode assigns the default model to all agents."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        result = asyncio.run(orch.assign_models("same", profiles, selected_model="opencode/go"))
        assert result == {"agent-a": "opencode/go", "agent-b": "opencode/go"}

    def test_same_mode_no_default(self, profiles):
        """Same mode falls back to first model if no default is marked."""
        models = [
            {"id": "model-x", "provider": "test"},
            {"id": "model-y", "provider": "test"},
        ]
        orch = Orchestrator(profiles=profiles, model_configs=models)
        result = asyncio.run(orch.assign_models("same", profiles))
        # Falls back to first model.
        assert all(v == "model-x" for v in result.values())

    def test_random_mode_deterministic(self, profiles, model_configs):
        """Random mode should be deterministic for the same topic."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)

        # First call.
        asyncio.run(orch.configure("Deterministic topic", time_budget="quick", model_mode="random"))
        result_1 = asyncio.run(orch.assign_models("random", profiles))

        # Reset and second call.
        orch2 = Orchestrator(profiles=profiles, model_configs=model_configs)
        asyncio.run(orch2.configure("Deterministic topic", time_budget="quick", model_mode="random"))
        result_2 = asyncio.run(orch2.assign_models("random", profiles))

        assert result_1 == result_2

    def test_random_mode_different_topics(self, model_configs):
        """Different topics should produce different random assignments."""
        # Use 4 agents and 2 models → very low collision probability (1/16).
        many_profiles = [
            AgentProfile(id=f"agent-{c}", name=f"Agent {c}", emoji="🧪",
                         persona_prompt="P", methodology="M", knowledge_base="K",
                         bias_mitigation="B", voice="V", temperature=0.5)
            for c in "abcd"
        ]

        orch_a = Orchestrator(profiles=many_profiles, model_configs=model_configs)
        asyncio.run(orch_a.configure("Topic A", time_budget="quick", model_mode="random"))
        result_a = asyncio.run(orch_a.assign_models("random", many_profiles))

        orch_b = Orchestrator(profiles=many_profiles, model_configs=model_configs)
        asyncio.run(orch_b.configure("Topic B", time_budget="quick", model_mode="random"))
        result_b = asyncio.run(orch_b.assign_models("random", many_profiles))

        # With 4 agents × 2 models there are 16 possible outcomes —
        # overwhelmingly likely to differ for two different seeds.
        assert result_a != result_b

    def test_manual_mode(self, profiles, model_configs):
        """Manual mode uses the prompt function for each agent."""
        selections = iter(["1", "0"])  # Agent A picks index 1, agent B picks index 0

        def mock_prompt(msg, **kw):
            return next(selections)

        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
            prompt_func=mock_prompt,
        )
        result = asyncio.run(orch.assign_models("manual", profiles))
        assert result == {"agent-a": "gpt-4o", "agent-b": "openrouter/opencode/go"}

    def test_unknown_mode_raises(self, profiles, model_configs):
        """Unknown mode string should raise ConfigError."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        with pytest.raises(ConfigError, match="Unknown model assignment mode"):
            asyncio.run(orch.assign_models("invalid", profiles))


class TestRunRound:
    """Parallel execution with mock agents."""

    @pytest.mark.asyncio
    async def test_all_agents_succeed(self, profiles, model_configs):
        """All agents complete successfully in parallel."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        topic = ResearchTopic(question="Test", time_budget="quick", model_mode="same")

        # Create mock agents that return findings.
        agents = {
            "agent-a": AsyncMock(return_value=Findings(agent_id="a", round=1, summary="S", key_points=["K"], perspective="P")),
            "agent-b": AsyncMock(return_value=Findings(agent_id="b", round=1, summary="S", key_points=["K"], perspective="P")),
        }

        results = await orch.run_round(1, agents, topic)
        assert len(results) == 2
        assert "agent-a" in results
        assert "agent-b" in results

    @pytest.mark.asyncio
    async def test_one_agent_fails_others_continue(self, profiles, model_configs):
        """If one agent raises an exception, others should still succeed."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        topic = ResearchTopic(question="Test", time_budget="quick", model_mode="same")

        async def failing_agent(topic):
            raise RuntimeError("Agent crashed")

        successful = Findings(agent_id="b", round=1, summary="S", key_points=["K"], perspective="P")

        agents = {
            "agent-a": failing_agent,
            "agent-b": AsyncMock(return_value=successful),
        }

        results = await orch.run_round(1, agents, topic)
        assert "agent-a" not in results  # failed
        assert "agent-b" in results  # succeeded
        assert "agent-a" in orch.failed_agents
        assert orch.failed_agents["agent-a"] == "Agent crashed"

    @pytest.mark.asyncio
    async def test_timeout_handling(self, profiles, model_configs):
        """Agent that times out should be handled gracefully."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        # Override _get_timeout to return a very short timeout (0.1s).
        orch._get_timeout = lambda: 0.1  # type: ignore[method-assign]
        topic = ResearchTopic(question="Test", time_budget="quick", model_mode="same")

        async def slow_agent(topic):
            await asyncio.sleep(10)  # much longer than timeout
            return Findings(agent_id="slow", round=1, summary="S", key_points=["K"], perspective="P")

        fast = Findings(agent_id="fast", round=1, summary="S", key_points=["K"], perspective="P")

        agents = {
            "slow": slow_agent,
            "fast": AsyncMock(return_value=fast),
        }

        results = await orch.run_round(1, agents, topic)
        assert "slow" not in results
        assert "fast" in results
        assert "slow" in orch.failed_agents
        assert orch.failed_agents["slow"] == "timeout"

    @pytest.mark.asyncio
    async def test_failed_agents_skipped_in_subsequent_rounds(self, profiles, model_configs):
        """Agents that failed in Round 1 should not run in Round 2."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        orch.failed_agents["agent-a"] = "previous failure"
        topic = ResearchTopic(question="Test", time_budget="medium", model_mode="same")
        shared = SharedKnowledge(
            round_number=1,
            all_summaries={"agent-b": "Summary"},
            key_themes=[],
            areas_of_agreement=[],
            areas_of_disagreement=[],
            knowledge_gaps=[],
        )

        mock_fn = AsyncMock(return_value=IndividualReport(
            agent_id="b", title="R", perspective_summary="S", key_insights=["I"], analysis="A", full_text="F",
        ))

        agents = {
            "agent-a": AsyncMock(),  # should NOT be called
            "agent-b": mock_fn,
        }

        results = await orch.run_round(2, agents, topic, shared)
        assert "agent-a" not in results
        assert "agent-b" in results
        # agent-a's mock should not have been awaited.
        agents["agent-a"].assert_not_called()


class TestShareFindings:
    """Shared knowledge aggregation."""

    def test_basic_aggregation(self, mock_findings):
        """share_findings should produce SharedKnowledge with correct structure."""
        orch = Orchestrator()
        f1 = Findings(agent_id="a", round=1, summary="Summary A", key_points=["P1", "P2"], perspective="Per A", confidence=0.8)
        f2 = Findings(agent_id="b", round=1, summary="Summary B", key_points=["P3"], perspective="Per B", confidence=0.6)

        shared = orch.share_findings({"a": f1, "b": f2})
        assert isinstance(shared, SharedKnowledge)
        assert shared.round_number == 1
        assert shared.all_summaries == {"a": "Summary A", "b": "Summary B"}
        assert len(shared.key_themes) > 0
        assert len(shared.areas_of_agreement) > 0

    def test_empty_findings(self):
        """Empty findings should still produce a valid SharedKnowledge."""
        orch = Orchestrator()
        shared = orch.share_findings({})
        assert shared.round_number == 1
        assert shared.all_summaries == {}


class TestCollectFollowup:
    """Follow-up question collection."""

    @pytest.mark.asyncio
    async def test_collect_followup_questions(self, profiles, model_configs):
        """Collect follow-up questions from all active agents."""
        orch = Orchestrator(profiles=profiles, model_configs=model_configs)
        shared = SharedKnowledge(
            round_number=1,
            all_summaries={"a": "S"},
            key_themes=[],
            areas_of_agreement=[],
            areas_of_disagreement=[],
            knowledge_gaps=[],
        )

        agents = {
            "agent-a": AsyncMock(return_value=FollowUpQuestions(agent_id="a", questions=["Q1?"])),
            "agent-b": AsyncMock(return_value=FollowUpQuestions(agent_id="b", questions=["Q2?"])),
        }

        results = await orch.collect_followup_questions(agents, shared)
        assert len(results) == 2
        assert results["agent-a"].questions == ["Q1?"]

    @pytest.mark.asyncio
    async def test_failed_agents_skipped_in_followup(self):
        """Failed agents should be excluded from follow-up questions."""
        orch = Orchestrator()
        orch.failed_agents["agent-a"] = "error"
        shared = SharedKnowledge(round_number=1, all_summaries={}, key_themes=[], areas_of_agreement=[], areas_of_disagreement=[], knowledge_gaps=[])

        agents = {
            "agent-a": AsyncMock(),
            "agent-b": AsyncMock(return_value=FollowUpQuestions(agent_id="b", questions=["Q?"])),
        }

        results = await orch.collect_followup_questions(agents, shared)
        assert "agent-a" not in results
        assert "agent-b" in results
        agents["agent-a"].assert_not_called()


class TestCollectReports:
    """Report collection from agents."""

    @pytest.mark.asyncio
    async def test_uses_round_2_when_available(self):
        """If Round 2 results exist, collect_reports should return them directly."""
        orch = Orchestrator()
        round_2 = {
            "a": IndividualReport(agent_id="a", title="R", perspective_summary="S", key_insights=["I"], analysis="A", full_text="F"),
        }
        result = await orch.collect_reports({}, {}, round_2)
        assert result == round_2

    @pytest.mark.asyncio
    async def test_collects_from_agents_when_no_round_2(self, mock_findings):
        """Without Round 2, collect_reports converts Findings to IndividualReport directly."""
        orch = Orchestrator()
        r1 = {"agent-a": mock_findings}

        results = await orch.collect_reports({}, r1, {})
        assert "agent-a" in results
        report = results["agent-a"]
        assert report.agent_id == "agent-a"
        assert report.perspective_summary == "Test findings summary."
        assert report.key_insights == ["Key point 1", "Key point 2"]


class TestCompile:
    """Scribe compilation."""

    @pytest.mark.asyncio
    async def test_compile_success(self, mock_paper):
        """Compile should call the scribe with all reports."""
        orch = Orchestrator()
        reports = {
            "a": IndividualReport(agent_id="a", title="R", perspective_summary="S", key_insights=["I"], analysis="A", full_text="F"),
        }

        async def scribe(reports):
            return ResearchPaper(
                title="Paper",
                abstract=f"From {len(reports)} reports.",
                methodology_note="M",
                sections=[],
                synthesis="S",
                key_takeaways=["T"],
                conclusion="C",
            )

        paper = await orch.compile(reports, scribe)
        assert isinstance(paper, ResearchPaper)
        assert paper.title == "Paper"

    @pytest.mark.asyncio
    async def test_compile_fallback_on_failure(self):
        """Compile should return a minimal paper if the scribe fails."""
        orch = Orchestrator()

        async def failing_scribe(reports):
            raise RuntimeError("Scribe failed")

        paper = await orch.compile({}, failing_scribe)
        assert isinstance(paper, ResearchPaper)
        assert "Compilation failed" in paper.abstract


class TestHandleAgentFailure:
    """Error handling — graceful degradation."""

    def test_failure_logged(self):
        """Failed agents should be recorded in failed_agents dict."""
        orch = Orchestrator()
        orch.handle_agent_failure("agent-x", "connection error")
        assert "agent-x" in orch.failed_agents
        assert orch.failed_agents["agent-x"] == "connection error"

    def test_multiple_failures(self):
        """Multiple agents can fail independently."""
        orch = Orchestrator()
        orch.handle_agent_failure("a", "err1")
        orch.handle_agent_failure("b", "err2")
        assert len(orch.failed_agents) == 2

    def test_event_logged(self):
        """Failure should produce an agent_failed event."""
        orch = Orchestrator()
        orch.handle_agent_failure("agent-y", "timeout")
        events = [e for e in orch.events if e["event_type"] == "agent_failed"]
        assert len(events) == 1
        assert events[0]["agent_id"] == "agent-y"


class TestFullLifecycle:
    """Full session lifecycle with mock agents."""

    @pytest.mark.asyncio
    async def test_run_quick_mode(self, profiles, model_configs, mock_agent_factory, mock_scribe_factory):
        """Quick mode session should complete successfully."""
        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
            agent_factory=mock_agent_factory,
            scribe_factory=mock_scribe_factory,
        )

        output_path = await orch.run(
            "Test topic",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_test_output",
        )

        assert isinstance(output_path, Path)
        assert orch.state == "COMPLETE"
        assert len(orch.failed_agents) == 0

    @pytest.mark.asyncio
    async def test_run_medium_mode(self, profiles, model_configs, mock_agent_factory, mock_scribe_factory):
        """Medium mode (with Round 2) should complete successfully."""
        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
            agent_factory=mock_agent_factory,
            scribe_factory=mock_scribe_factory,
        )

        output_path = await orch.run(
            "Test topic",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_test_output",
        )

        assert isinstance(output_path, Path)
        assert orch.state == "COMPLETE"

    @pytest.mark.asyncio
    async def test_run_with_agent_failure(self, profiles, model_configs):
        """Session should continue when one agent fails (graceful degradation)."""

        def failing_factory(profile, model_name, **extra):
            async def fail_fn(*args, **kwargs):
                if profile.id == "agent-a":
                    raise RuntimeError("Agent A failure")
                # Agent B succeeds.
                if args and isinstance(args[0], ResearchTopic):
                    return Findings(agent_id=profile.id, round=1, summary="S", key_points=["K"], perspective="P")
                return IndividualReport(agent_id=profile.id, title="R", perspective_summary="S", key_insights=["I"], analysis="A", full_text="F")

            return fail_fn

        def scribe_factory(**extra):
            async def scribe(reports):
                return ResearchPaper(title="P", abstract="A", methodology_note="M", sections=[], synthesis="S", key_takeaways=["T"], conclusion="C")
            return scribe

        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
            agent_factory=failing_factory,
            scribe_factory=scribe_factory,
        )

        output_path = await orch.run(
            "Test topic",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_test_output",
        )

        assert isinstance(output_path, Path)
        assert orch.state == "COMPLETE"
        assert "agent-a" in orch.failed_agents
        assert "agent-b" not in orch.failed_agents

    @pytest.mark.asyncio
    async def test_event_logging(self, profiles, model_configs, mock_agent_factory, mock_scribe_factory):
        """Session events should be recorded throughout the lifecycle."""
        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
            agent_factory=mock_agent_factory,
            scribe_factory=mock_scribe_factory,
        )

        await orch.run(
            "Event test",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_test_output",
        )

        event_types = [e["event_type"] for e in orch.events]
        assert "session_start" in event_types
        assert "config_validated" in event_types
        assert "models_assigned" in event_types
        assert "round_start" in event_types
        assert "agent_start" in event_types
        assert "agent_complete" in event_types
        assert "collaboration_phase" in event_types
        assert "scribe_start" in event_types
        assert "scribe_end" in event_types
        assert "pdf_generated" in event_types
        assert "session_end" in event_types


class TestDryRun:
    """Dry-run preview mode."""

    def test_dry_run_shows_config(self, profiles, model_configs):
        """Dry-run should validate config and not execute any agents."""
        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
        )

        result = asyncio.run(orch.run(
            "Dry run topic",
            time_budget="medium",
            model_mode="same",
            dry_run=True,
            output_dir="/tmp/deepresearch_test_output",
        ))

        assert isinstance(result, Path)
        # No agents should have been created or executed.
        assert orch.failed_agents == {}
        assert orch.state == "CONFIGURING"  # never progressed past CONFIGURING


class TestStateTransitions:
    """Orchestrator lifecycle state transitions."""

    @pytest.mark.asyncio
    async def test_full_state_sequence(self, profiles, model_configs, mock_agent_factory, mock_scribe_factory):
        """State should follow the expected lifecycle sequence."""
        orch = Orchestrator(
            profiles=profiles,
            model_configs=model_configs,
            agent_factory=mock_agent_factory,
            scribe_factory=mock_scribe_factory,
        )

        assert orch.state == "IDLE"

        await orch.run(
            "State test",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_test_output",
        )

        assert orch.state == "COMPLETE"
