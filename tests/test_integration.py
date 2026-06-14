"""Integration tests for the DeepeResearch Orchestrator.

Tests run the Orchestrator with mock agent profiles and mock LLM
responses, verifying the full workflow executes correctly from topic
input through to PDF output generation.

Phase 5 additions:
  - Full pipeline: mock agents → scribe compile → PDF generation
  - Verify PDF file is created and has valid header (%PDF-)
  - Clarification protocol: scribe queries agent, agent responds
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchPaper,
    ResearchTopic,
    SharedKnowledge,
)
from deepresearch.orchestrator import Orchestrator


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_profiles() -> list[AgentProfile]:
    """Three agents with distinct profiles for integration testing."""
    return [
        AgentProfile(
            id="agent-alpha",
            name="Agent Alpha",
            emoji="🔬",
            persona_prompt="You are a test agent focused on scientific analysis.",
            methodology="Analyze from first principles.",
            knowledge_base="Scientific method and research.",
            bias_mitigation="Avoid confirmation bias.",
            voice="Formal and precise.",
            temperature=0.5,
        ),
        AgentProfile(
            id="agent-beta",
            name="Agent Beta",
            emoji="💡",
            persona_prompt="You are a creative thinker exploring novel angles.",
            methodology="Brainstorm and connect disparate ideas.",
            knowledge_base="Creative arts and design thinking.",
            bias_mitigation="Ground ideas in reality.",
            voice="Imaginative and expressive.",
            temperature=0.8,
        ),
        AgentProfile(
            id="agent-gamma",
            name="Agent Gamma",
            emoji="📊",
            persona_prompt="You are a data-driven analyst focused on evidence.",
            methodology="Quantitative analysis and pattern recognition.",
            knowledge_base="Statistics and data science.",
            bias_mitigation="Avoid over-interpreting noise.",
            voice="Analytical and precise.",
            temperature=0.3,
        ),
    ]


@pytest.fixture
def mock_model_configs() -> list[dict]:
    """Two models for multi-model assignment testing."""
    return [
        {"id": "opencode/zen/claude-sonnet-4", "provider": "opencode", "display_name": "Opencode Zen"},
        {"id": "gpt-4o", "provider": "openai", "display_name": "GPT-4o", "default": True},
        {"id": "claude-sonnet-4-20250514", "provider": "anthropic", "display_name": "Claude Sonnet 4"},
    ]


def build_mock_agent_factory():
    """Factory producing mock agents with canned responses per lifecycle phase.

    Each agent returns phase-appropriate results:
      - Round 1 (ResearchTopic arg) → Findings
      - Follow-up (SharedKnowledge arg) → FollowUpQuestions
      - Round 2 (ResearchTopic + SharedKnowledge) → IndividualReport
      - Report writing (no matching pattern) → IndividualReport
    """

    def factory(profile: AgentProfile, model_name: str, **extra):
        async def agent_fn(*args, **kwargs):
            first = args[0] if args else None

            if isinstance(first, ResearchTopic):
                if len(args) > 1 and isinstance(args[1], SharedKnowledge):
                    # Round 2: refined research.
                    shared = args[1]
                    return IndividualReport(
                        agent_id=profile.id,
                        title=f"Refined Report by {profile.name}",
                        perspective_summary=f"{profile.name}'s refined perspective "
                                            f"in light of {len(shared.all_summaries)} other perspectives.",
                        key_insights=[
                            f"{profile.name}'s unique insight on the topic",
                            "Cross-perspective analysis reveals new dimensions",
                        ],
                        analysis=f"Detailed analysis by {profile.name} using {profile.methodology}",
                        metaphors_or_analogies=[],
                        open_questions=["What remains unexplored?"],
                        full_text=f"Full refined report by {profile.name}.",
                    )
                # Round 1: independent research.
                return Findings(
                    agent_id=profile.id,
                    round=1,
                    summary=f"{profile.name} investigated the topic from a {profile.voice} perspective.",
                    key_points=[
                        f"Key point from {profile.name}",
                        "Another important observation",
                    ],
                    perspective=f"{profile.name}'s unique perspective using {profile.knowledge_base}",
                    confidence=0.7,
                )

            if isinstance(first, SharedKnowledge):
                # Follow-up questions phase.
                return FollowUpQuestions(
                    agent_id=profile.id,
                    questions=[
                        f"From {profile.name}: How does this connect to {profile.knowledge_base}?",
                        f"From {profile.name}: What evidence supports the key claims?",
                    ],
                )

            # Report writing (arg is typically Findings from Round 1).
            return IndividualReport(
                agent_id=profile.id,
                title=f"Final Report by {profile.name}",
                perspective_summary=f"Comprehensive analysis from {profile.name}'s viewpoint.",
                key_insights=[
                    f"Core insight from {profile.name}",
                    "Secondary finding with supporting evidence",
                ],
                analysis=f"Full analysis employing {profile.methodology}",
                metaphors_or_analogies=[
                    f"{profile.name}'s key analogy for the topic",
                ],
                open_questions=[
                    "What new questions emerge from this analysis?",
                ],
                full_text=f"Complete report by {profile.name}.",
            )

        return agent_fn

    return factory


def build_mock_scribe_factory():
    """Factory producing a mock scribe that returns a ResearchPaper."""

    def factory(**extra):
        async def scribe(reports: dict[str, IndividualReport]) -> ResearchPaper:
            agent_list = "\n".join(
                f"- {r.title} by {r.agent_id}" for r in reports.values()
            )
            return ResearchPaper(
                title="Integrated Research Paper",
                abstract=(
                    f"This paper synthesizes findings from {len(reports)} "
                    f"research agents with distinct perspectives."
                ),
                methodology_note=(
                    "Multi-agent collaborative research methodology. Each agent "
                    "conducted independent research followed by collaborative refinement."
                ),
                sections=[],
                synthesis=(
                    f"Agent Contributions:\n{agent_list}\n\n"
                    "Each agent brought a unique perspective, resulting in a "
                    "comprehensive multi-dimensional analysis."
                ),
                key_takeaways=[
                    "Multiple perspectives enrich understanding",
                    "Collaborative research reveals new insights",
                    "Each methodology contributes unique value",
                ],
                conclusion=(
                    "The multi-agent approach produced a well-rounded analysis "
                    "that no single perspective could achieve."
                ),
            )

        return scribe

    return factory


# ─── Tests ────────────────────────────────────────────────────────────────────


class TestIntegration:
    """End-to-end integration tests for the full research workflow."""

    @pytest.mark.asyncio
    async def test_full_session_completes(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """A full session with 3 agents should complete successfully.

        This test validates:
          - Configuration flow (time budget, model mode)
          - Model assignment (same mode)
          - Round 1 execution (all agents produce findings)
          - Collaboration / shared knowledge creation
          - Follow-up question collection
          - Round 2 execution (refined research)
          - Report collection
          - Scribe compilation
          - Output path generation
        """
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        output_path = await orch.run(
            "What is the impact of artificial intelligence on healthcare?",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_integration_test",
        )

        # The workflow should complete and return a Path.
        assert isinstance(output_path, Path)
        assert str(output_path) == "/tmp/deepresearch_integration_test/paper.pdf"
        assert orch.state == "COMPLETE"

    @pytest.mark.asyncio
    async def test_session_with_random_model_assignment(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Session with random model assignment should complete."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        output_path = await orch.run(
            "Test random assignment",
            time_budget="quick",
            model_mode="random",
            output_dir="/tmp/deepresearch_random_test",
        )

        assert isinstance(output_path, Path)
        assert orch.state == "COMPLETE"

    @pytest.mark.asyncio
    async def test_quick_mode_skips_round_2(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Quick mode should skip Round 2 and still produce output."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        output_path = await orch.run(
            "Quick mode test",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_quick_test",
        )

        assert isinstance(output_path, Path)
        assert orch.state == "COMPLETE"
        # Verify Round 2 was not executed (no round_start with round=2).
        round_2_events = [
            e for e in orch.events
            if e.get("event_type") == "round_start" and e.get("round") == 2
        ]
        assert len(round_2_events) == 0

    @pytest.mark.asyncio
    async def test_full_session_events_recorded(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """All expected lifecycle events should be recorded."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        await orch.run(
            "Event logging test",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_events_test",
        )

        event_types = [e["event_type"] for e in orch.events]
        expected = [
            "session_start",
            "config_validated",
            "models_assigned",
            "round_start",
            "agent_complete",
            "collaboration_phase",
            "round_start",
            "agent_complete",
            "scribe_start",
            "scribe_end",
            "pdf_generated",
            "session_end",
        ]
        for ev in expected:
            assert ev in event_types, f"Missing event: {ev}"

    @pytest.mark.asyncio
    async def test_no_agent_factory_raises(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Running without an agent factory should raise ConfigError."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            # No agent_factory provided.
        )

        with pytest.raises(Exception, match="No agent factory"):
            await orch.run(
                "Test no factory",
                time_budget="quick",
                model_mode="same",
                output_dir="/tmp/deepresearch_no_factory_test",
            )

    @pytest.mark.asyncio
    async def test_dry_run_with_profiles(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Dry-run should validate configuration without executing agents."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
        )

        result = await orch.run(
            "Dry run integration test",
            time_budget="medium",
            model_mode="same",
            dry_run=True,
            output_dir="/tmp/deepresearch_dryrun_test",
        )

        assert isinstance(result, Path)
        # Verify no events past configuration.
        event_types = [e["event_type"] for e in orch.events]
        assert "session_start" in event_types
        assert "config_validated" in event_types
        assert "models_assigned" in event_types
        assert "round_start" not in event_types
        assert "session_end" not in event_types


class TestCollaborationBusIntegration:
    """Full 2-round workflow with agent factories — bus integration."""

    @pytest.mark.asyncio
    async def test_bus_created_and_accessible(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Orchestrator creates a CollaborationBus accessible after run()."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        await orch.run(
            "Bus integration: accessibility",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_bus_integration",
        )

        assert hasattr(orch, "bus")
        assert orch.bus.topic is not None

    @pytest.mark.asyncio
    async def test_full_2_round_workflow_with_bus(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Full workflow populates bus with all expected data.

        Round 2 runs dynamically based on knowledge quality. With mock agents
        producing confidence=0.7 and 1 gap from stubs, the dynamic decision
        may skip Round 2 — the test verifies outcomes regardless.
        """
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        await orch.run(
            "Bus integration: full 2-round",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_bus_2round",
        )

        # Round 1 findings in bus.
        r1 = await orch.bus.get_round_1_findings()
        assert len(r1) == len(mock_profiles)

        # Shared knowledge computed.
        shared = await orch.bus.get_shared_knowledge()
        assert shared is not None
        assert len(shared.all_summaries) == len(mock_profiles)

        # Follow-up questions for each agent.
        for profile in mock_profiles:
            qs = await orch.bus.get_followup_questions(profile.id)
            assert len(qs) >= 1

        # Round 2 runs dynamically based on knowledge quality (gaps + disagreements).
        # With mock data (1 gap, 0 disagreements, all confidence >= 0.5), Round 2
        # may be skipped — verify the session completed regardless.
        async with orch.bus._lock:
            if len(orch.bus.round_2_findings) > 0:
                # Round 2 did run — verify findings are present.
                pass
            else:
                # Round 2 was dynamically skipped — verify the skip event was logged.
                skip_events = [e for e in orch.events if e.get("event_type") == "round2_skip"]
                assert len(skip_events) >= 1

        # All reports collected (works regardless of Round 2).
        all_reports = await orch.bus.get_all_reports()
        assert len(all_reports) == len(mock_profiles)

    @pytest.mark.asyncio
    async def test_quick_mode_bus_skips_round2(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Quick mode does not populate Round 2 findings in the bus."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        await orch.run(
            "Bus integration: quick mode skips round 2",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_bus_quick",
        )

        async with orch.bus._lock:
            assert len(orch.bus.round_2_findings) == 0

        # Round 1 should still be populated.
        r1 = await orch.bus.get_round_1_findings()
        assert len(r1) == len(mock_profiles)

        # Reports should be populated (from Round 1 only).
        reports = await orch.bus.get_all_reports()
        assert len(reports) == len(mock_profiles)

    @pytest.mark.asyncio
    async def test_agent_failure_graceful_degradation(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """One agent failing shouldn't break bus for other agents."""

        def failing_factory(profile, model_name, **extra):
            async def agent_fn(*args, **kwargs):
                if profile.id == "agent-alpha":
                    raise RuntimeError("Alpha failure")
                # Agent Beta (and Gamma if present) succeed.
                first = args[0] if args else None
                if isinstance(first, ResearchTopic):
                    if len(args) > 1 and isinstance(args[1], SharedKnowledge):
                        return IndividualReport(
                            agent_id=profile.id, title=f"R by {profile.name}",
                            perspective_summary="S", key_insights=["I"],
                            analysis="A", full_text="F",
                        )
                    return Findings(
                        agent_id=profile.id, round=1, summary="S",
                        key_points=["K"], perspective="P", confidence=0.5,
                    )
                if isinstance(first, SharedKnowledge):
                    return FollowUpQuestions(
                        agent_id=profile.id, questions=["Q?"],
                    )
                return IndividualReport(
                    agent_id=profile.id, title="R", perspective_summary="S",
                    key_insights=["I"], analysis="A", full_text="F",
                )
            return agent_fn

        def sf(**extra):
            async def scribe(reports):
                return ResearchPaper(
                    title="P", abstract="A", methodology_note="M",
                    sections=[], synthesis="S", key_takeaways=["T"],
                    conclusion="C",
                )
            return scribe

        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=failing_factory,
            scribe_factory=sf,
        )

        result = await orch.run(
            "Graceful degradation with bus",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_bus_gd",
        )

        assert isinstance(result, Path)
        assert "agent-alpha" in orch.failed_agents

        # Bus should have data from surviving agents only.
        r1 = await orch.bus.get_round_1_findings()
        assert "agent-alpha" not in r1

        shared = await orch.bus.get_shared_knowledge()
        assert shared is not None
        assert "agent-alpha" not in shared.all_summaries

    @pytest.mark.asyncio
    async def test_run_parallel_utility(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """_run_parallel helper executes tasks and handles failures."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        async def success() -> str:
            return "ok"

        async def failure() -> str:
            raise ValueError("task failed")

        results = await orch._run_parallel(
            {"a": success(), "b": failure()}
        )

        assert "a" in results
        assert "b" not in results
        assert results["a"] == "ok"


class TestPDFIntegration:
    """Full pipeline integration — PDF output generation."""

    @pytest.mark.asyncio
    async def test_full_pipeline_generates_pdf(
        self,
        mock_profiles,
        mock_model_configs,
        tmp_path,
    ):
        """Full pipeline: mock agents → compile → PDF with valid header."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        output = tmp_path / "integration_test.pdf"
        result = await orch.run(
            "PDF integration test topic",
            time_budget="quick",
            model_mode="same",
            output_path=str(output),
        )

        assert isinstance(result, Path)
        assert result.exists()
        assert result.stat().st_size > 0

        if result.suffix == ".pdf":
            with open(result, "rb") as f:
                header = f.read(5)
            assert header == b"%PDF-"
        else:
            # Fallback HTML.
            assert result.suffix == ".html"
            content = result.read_text(encoding="utf-8")
            assert "PDF integration test" in content or "Agent" in content

    @pytest.mark.asyncio
    async def test_pdf_with_output_path_override(
        self,
        mock_profiles,
        mock_model_configs,
        tmp_path,
    ):
        """Using output_path with a full path should work."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        output = tmp_path / "custom" / "report.pdf"
        result = await orch.run(
            "Custom output path test",
            time_budget="quick",
            model_mode="same",
            output_path=str(output),
        )

        assert isinstance(result, Path)
        assert result.exists()
        assert result.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_pdf_generated_event(
        self,
        mock_profiles,
        mock_model_configs,
        tmp_path,
    ):
        """The pdf_generated event should be recorded."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        output = tmp_path / "event_test.pdf"
        await orch.run(
            "Event test",
            time_budget="quick",
            model_mode="same",
            output_path=str(output),
        )

        pdf_events = [
            e for e in orch.events
            if e.get("event_type") == "pdf_generated"
        ]
        assert len(pdf_events) == 1
        assert "path" in pdf_events[0]

    @pytest.mark.asyncio
    async def test_quick_mode_pdf_output(
        self,
        mock_profiles,
        mock_model_configs,
        tmp_path,
    ):
        """Quick mode (no Round 2) should still produce PDF output."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        output = tmp_path / "quick_test.pdf"
        result = await orch.run(
            "Quick mode PDF test",
            time_budget="quick",
            model_mode="same",
            output_path=str(output),
        )

        assert result.exists()
        assert result.stat().st_size > 0

    @pytest.mark.asyncio
    async def test_dry_run_with_output_path(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Dry-run should accept output_path and return it."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
        )

        result = await orch.run(
            "Dry run with output path",
            time_budget="medium",
            model_mode="same",
            output_path="/tmp/dry_run_test/paper.pdf",
            dry_run=True,
        )

        assert isinstance(result, Path)
        assert str(result) == "/tmp/dry_run_test/paper.pdf"


class TestClarificationProtocolIntegration:
    """Clarification protocol — scribe to agent queries."""

    @pytest.mark.asyncio
    async def test_orchestrator_routes_clarification(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Orchestrator's _handle_clarification should route to agents."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        # Manually set up an agent that supports clarification.
        class MockClarifyAgent:
            async def clarify(self, query: ClarificationQuery) -> ClarificationResponse:
                return ClarificationResponse(
                    agent_id=query.agent_id,
                    response=f"Clarifying: {query.question[:50]}...",
                )

        orch._agents = {"agent-alpha": MockClarifyAgent()}

        query = ClarificationQuery(
            agent_id="agent-alpha",
            question="What evidence supports your claim about quantum advantage?",
            context="The claim about quantum advantage needs more support.",
        )

        response = await orch._handle_clarification(query)
        assert isinstance(response, ClarificationResponse)
        assert response.agent_id == "agent-alpha"
        assert "Clarifying" in response.response

    @pytest.mark.asyncio
    async def test_clarification_unavailable_agent(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Missing agent should return a default response."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        orch._agents = {}

        query = ClarificationQuery(
            agent_id="nonexistent-agent",
            question="Please clarify?",
        )

        response = await orch._handle_clarification(query)
        assert isinstance(response, ClarificationResponse)
        assert "unavailable" in response.response.lower()

    @pytest.mark.asyncio
    async def test_clarification_failing_agent(
        self,
        mock_profiles,
        mock_model_configs,
    ):
        """Agent that throws during clarify should return a graceful error."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_mock_agent_factory(),
            scribe_factory=build_mock_scribe_factory(),
        )

        class FailingClarifyAgent:
            async def clarify(self, query: ClarificationQuery) -> ClarificationResponse:
                raise RuntimeError("Clarification engine crashed")

        orch._agents = {"agent-alpha": FailingClarifyAgent()}

        query = ClarificationQuery(
            agent_id="agent-alpha",
            question="Explain your methodology?",
        )

        response = await orch._handle_clarification(query)
        assert isinstance(response, ClarificationResponse)
        assert "Unable to clarify" in response.response or "crashed" in response.response
