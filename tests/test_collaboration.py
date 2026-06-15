"""Tests for the CollaborationBus — shared knowledge repository.

Covers:
  - Publish and read Round 1 findings (thread-safe, echo prevention)
  - Shared knowledge computation (themes, agreements, disagreements, gaps)
  - Follow-up question publish / retrieve per-agent
  - Round 2 findings publish
  - Report publish and retrieve all
  - Clarification query/response pairs
  - Empty state handling
  - Thread safety: concurrent writes don't corrupt data
  - Integration with Orchestrator's full 2-round workflow
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from deepresearch.collaboration import CollaborationBus
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
def bus() -> CollaborationBus:
    return CollaborationBus()


@pytest.fixture
def sample_topic() -> ResearchTopic:
    return ResearchTopic(
        question="What is the impact of AI on healthcare?",
        time_budget="medium",
        model_mode="same",
    )


@pytest.fixture
def finding_a() -> Findings:
    return Findings(
        agent_id="agent-alpha",
        round=1,
        summary=(
            "AI transforms healthcare through diagnostic imaging and "
            "personalized treatment plans. Further research is needed "
            "on ethical implications."
        ),
        key_points=[
            "AI improves diagnostic accuracy in medical imaging",
            "Personalized treatment plans reduce costs",
            "Ethical concerns around patient data privacy",
        ],
        perspective=(
            "AI in healthcare offers transformative potential but raises "
            "important ethical questions that require further investigation."
        ),
        confidence=0.8,
    )


@pytest.fixture
def finding_b() -> Findings:
    return Findings(
        agent_id="agent-beta",
        round=1,
        summary=(
            "The limitations of current AI systems in healthcare include "
            "data bias and lack of regulatory frameworks. Unclear how "
            "these will be resolved."
        ),
        key_points=[
            "AI improves diagnostic accuracy in medical imaging",
            "Data bias remains a significant challenge",
            "Regulatory frameworks are not keeping pace",
        ],
        perspective=(
            "However, the hype around AI in healthcare obscures "
            "significant limitations and flaws in current approaches."
        ),
        confidence=0.6,
    )


@pytest.fixture
def finding_c() -> Findings:
    return Findings(
        agent_id="agent-gamma",
        round=1,
        summary=(
            "Investment in AI healthcare startups reached record levels. "
            "There are knowledge gaps about long-term outcomes."
        ),
        key_points=[
            "AI improves diagnostic accuracy in medical imaging",
            "Investment in AI healthcare is booming",
            "Long-term outcomes remain insufficiently studied",
        ],
        perspective=(
            "The economic and market-driven aspects of AI in healthcare "
            "are important but under-examined."
        ),
        confidence=0.7,
    )


@pytest.fixture
def mock_profiles() -> list[AgentProfile]:
    return [
        AgentProfile(
            id="agent-alpha",
            name="Agent Alpha",
            emoji="🔬",
            persona_prompt="Scientific analyst.",
            methodology="First principles.",
            knowledge_base="Science.",
            bias_mitigation="Avoid bias.",
            voice="Formal.",
            temperature=0.5,
        ),
        AgentProfile(
            id="agent-beta",
            name="Agent Beta",
            emoji="💡",
            persona_prompt="Creative thinker.",
            methodology="Brainstorming.",
            knowledge_base="Arts.",
            bias_mitigation="Ground in reality.",
            voice="Expressive.",
            temperature=0.8,
        ),
    ]


@pytest.fixture
def mock_model_configs() -> list[dict]:
    return [
        {
            "id": "gpt-4o",
            "provider": "openai",
            "display_name": "GPT-4o",
            "default": True,
        },
    ]


# ─── CollaborationBus Tests ──────────────────────────────────────────────────


class TestPublishRound1:
    """Publishing and reading Round 1 findings."""

    @pytest.mark.asyncio
    async def test_publish_and_read(self, bus, finding_a, finding_b):
        """Round 1 findings can be published and retrieved."""
        await bus.publish_round_1("agent-alpha", finding_a)
        await bus.publish_round_1("agent-beta", finding_b)

        all_findings = await bus.get_round_1_findings()
        assert len(all_findings) == 2
        assert all_findings["agent-alpha"].agent_id == "agent-alpha"
        assert all_findings["agent-beta"].agent_id == "agent-beta"

    @pytest.mark.asyncio
    async def test_publish_echo_prevention(self, bus, finding_a):
        """Agent cannot publish findings under another agent's ID.

        Echo prevention: if ``agent_id`` doesn't match ``findings.agent_id``
        the bus ignores the write.
        """
        await bus.publish_round_1("agent-beta", finding_a)  # mismatched
        all_findings = await bus.get_round_1_findings()
        assert "agent-beta" not in all_findings
        assert len(all_findings) == 0

    @pytest.mark.asyncio
    async def test_read_returns_copy(self, bus, finding_a):
        """get_round_1_findings returns a dict copy, not a reference."""
        await bus.publish_round_1("agent-alpha", finding_a)
        retrieved = await bus.get_round_1_findings()
        # Mutating the returned dict should not affect the bus.
        retrieved.clear()
        still_there = await bus.get_round_1_findings()
        assert len(still_there) == 1


class TestSharedKnowledge:
    """Shared knowledge computation from Round 1 findings."""

    @pytest.mark.asyncio
    async def test_basic_computation(self, bus, finding_a, finding_b):
        """compute_shared_knowledge produces a valid SharedKnowledge."""
        await bus.publish_round_1("agent-alpha", finding_a)
        await bus.publish_round_1("agent-beta", finding_b)

        shared = await bus.compute_shared_knowledge()

        assert isinstance(shared, SharedKnowledge)
        assert shared.round_number == 1
        assert "agent-alpha" in shared.all_summaries
        assert "agent-beta" in shared.all_summaries

    @pytest.mark.asyncio
    async def test_key_themes_extracted(self, bus, finding_a, finding_b):
        """Key themes are extracted from agents' key points."""
        await bus.publish_round_1("agent-alpha", finding_a)
        await bus.publish_round_1("agent-beta", finding_b)

        shared = await bus.compute_shared_knowledge()

        assert len(shared.key_themes) > 0
        # At least one theme should reference diagnostic accuracy.
        theme_text = " ".join(shared.key_themes).lower()
        assert "diagnostic" in theme_text or "ai" in theme_text

    @pytest.mark.asyncio
    async def test_areas_of_agreement(self, bus, finding_a, finding_b):
        """Shared key points across agents appear as agreement."""
        await bus.publish_round_1("agent-alpha", finding_a)
        await bus.publish_round_1("agent-beta", finding_b)

        shared = await bus.compute_shared_knowledge()

        # Both agents share "AI improves diagnostic accuracy..."
        agreement_text = " ".join(shared.areas_of_agreement).lower()
        assert "diagnostic accuracy" in agreement_text

    @pytest.mark.asyncio
    async def test_areas_of_disagreement(self, bus, finding_a, finding_b):
        """Disagreements detected from perspective language."""
        await bus.publish_round_1("agent-alpha", finding_a)
        await bus.publish_round_1("agent-beta", finding_b)

        shared = await bus.compute_shared_knowledge()

        # Agent Beta's perspective uses "however" → should trigger disagreement.
        # Note: at least one disagreement marker in Beta's perspective.
        assert len(shared.areas_of_disagreement) > 0

    @pytest.mark.asyncio
    async def test_knowledge_gaps_detected(self, bus, finding_a, finding_b):
        """Knowledge gaps extracted from summary text markers."""
        await bus.publish_round_1("agent-alpha", finding_a)
        await bus.publish_round_1("agent-beta", finding_b)

        shared = await bus.compute_shared_knowledge()

        assert len(shared.knowledge_gaps) > 0
        gap_text = " ".join(shared.knowledge_gaps).lower()
        # "further research" in agent-alpha's summary.
        assert "further research" in gap_text or "unclear" in gap_text

    @pytest.mark.asyncio
    async def test_single_agent(self, bus, finding_a):
        """Single agent findings still produce valid shared knowledge."""
        await bus.publish_round_1("agent-alpha", finding_a)

        shared = await bus.compute_shared_knowledge()

        assert len(shared.all_summaries) == 1
        assert len(shared.key_themes) > 0
        # Single agent → no disagreements possible.
        assert len(shared.areas_of_disagreement) == 0

    @pytest.mark.asyncio
    async def test_empty_findings(self, bus):
        """Empty bus produces valid SharedKnowledge with defaults."""
        shared = await bus.compute_shared_knowledge()

        assert shared.all_summaries == {}
        assert len(shared.key_themes) == 0
        assert len(shared.knowledge_gaps) >= 1  # default gap message

    @pytest.mark.asyncio
    async def test_get_shared_knowledge_before_compute(self, bus):
        """get_shared_knowledge returns None before compute."""
        result = await bus.get_shared_knowledge()
        assert result is None

    @pytest.mark.asyncio
    async def test_get_shared_knowledge_after_compute(self, bus, finding_a):
        """get_shared_knowledge returns the cached result after compute."""
        await bus.publish_round_1("agent-alpha", finding_a)
        await bus.compute_shared_knowledge()

        cached = await bus.get_shared_knowledge()
        assert cached is not None
        assert cached.round_number == 1


class TestFollowUpQuestions:
    """Follow-up question publish and retrieve."""

    @pytest.mark.asyncio
    async def test_publish_and_read(self, bus):
        """Follow-up questions can be published and retrieved per agent."""
        await bus.publish_followup("agent-alpha", ["Q1?", "Q2?"])
        await bus.publish_followup("agent-beta", ["Q3?"])

        qs_a = await bus.get_followup_questions("agent-alpha")
        qs_b = await bus.get_followup_questions("agent-beta")
        qs_unknown = await bus.get_followup_questions("unknown")

        assert qs_a == ["Q1?", "Q2?"]
        assert qs_b == ["Q3?"]
        assert qs_unknown == []

    @pytest.mark.asyncio
    async def test_publish_overwrites(self, bus):
        """Publishing again overwrites previous questions."""
        await bus.publish_followup("agent-alpha", ["Old Q"])
        await bus.publish_followup("agent-alpha", ["New Q"])

        qs = await bus.get_followup_questions("agent-alpha")
        assert qs == ["New Q"]

    @pytest.mark.asyncio
    async def test_read_returns_copy(self, bus):
        """get_followup_questions returns a list copy."""
        await bus.publish_followup("agent-alpha", ["Q1"])
        retrieved = await bus.get_followup_questions("agent-alpha")
        retrieved.append("Q2")  # mutate the return value
        still = await bus.get_followup_questions("agent-alpha")
        assert still == ["Q1"]  # unchanged


class TestRound2:
    """Round 2 findings publish."""

    @pytest.mark.asyncio
    async def test_publish_round_2(self, bus):
        """Round 2 findings can be published."""
        f2 = Findings(
            agent_id="agent-alpha",
            round=2,
            summary="Deeper findings.",
            key_points=["Refined insight"],
            perspective="Evolved perspective.",
            confidence=0.9,
        )
        await bus.publish_round_2("agent-alpha", f2)
        # Currently no dedicated read method — round_2_findings is stored
        # but consumed indirectly via shared knowledge updates in Phase 5.
        # We verify via the internal dict (direct attribute access for test).
        async with bus._lock:
            assert "agent-alpha" in bus.round_2_findings


class TestReports:
    """Individual report publish and retrieve."""

    @pytest.mark.asyncio
    async def test_publish_and_get_all(self, bus):
        """Reports can be published and all retrieved."""
        r1 = IndividualReport(
            agent_id="agent-alpha",
            title="Alpha Report",
            perspective_summary="Summary",
            key_insights=["Insight"],
            analysis="Analysis",
            full_text="Full text",
        )
        r2 = IndividualReport(
            agent_id="agent-beta",
            title="Beta Report",
            perspective_summary="Summary",
            key_insights=["Insight"],
            analysis="Analysis",
            full_text="Full text",
        )

        await bus.publish_report("agent-alpha", r1)
        await bus.publish_report("agent-beta", r2)

        all_reports = await bus.get_all_reports()
        assert len(all_reports) == 2
        assert all_reports["agent-alpha"].title == "Alpha Report"
        assert all_reports["agent-beta"].title == "Beta Report"

    @pytest.mark.asyncio
    async def test_get_all_returns_copy(self, bus):
        """get_all_reports returns a dict copy."""
        r1 = IndividualReport(
            agent_id="agent-alpha",
            title="R",
            perspective_summary="S",
            key_insights=[],
            analysis="A",
            full_text="F",
        )
        await bus.publish_report("agent-alpha", r1)
        retrieved = await bus.get_all_reports()
        retrieved.clear()
        still = await bus.get_all_reports()
        assert len(still) == 1


class TestClarifications:
    """Clarification query/response pairs."""

    @pytest.mark.asyncio
    async def test_add_and_get(self, bus):
        """Clarification pairs can be added and retrieved."""
        query = ClarificationQuery(
            agent_id="agent-alpha",
            question="What is your evidence?",
            context="Section 3 analysis",
        )
        response = ClarificationResponse(
            agent_id="agent-alpha",
            response="The evidence comes from multiple peer-reviewed studies.",
        )

        await bus.add_clarification(query, response)
        clarifications = await bus.get_clarifications()

        assert len(clarifications) == 1
        assert clarifications[0]["agent_id"] == "agent-alpha"
        assert clarifications[0]["query"].question == "What is your evidence?"
        assert "peer-reviewed" in clarifications[0]["response"].response

    @pytest.mark.asyncio
    async def test_multiple_clarifications(self, bus):
        """Multiple clarification pairs accumulate."""
        for i in range(3):
            q = ClarificationQuery(
                agent_id=f"agent-{i}",
                question=f"Q{i}",
                context=None,
            )
            r = ClarificationResponse(
                agent_id=f"agent-{i}",
                response=f"R{i}",
            )
            await bus.add_clarification(q, r)

        cls = await bus.get_clarifications()
        assert len(cls) == 3

    @pytest.mark.asyncio
    async def test_empty_clarifications(self, bus):
        """No clarifications returns empty list."""
        cls = await bus.get_clarifications()
        assert cls == []


class TestEmptyState:
    """Bus handles empty state gracefully."""

    @pytest.mark.asyncio
    async def test_topic_is_none(self, bus):
        """Topic is None before being set."""
        assert bus.topic is None

    @pytest.mark.asyncio
    async def test_topic_can_be_set(self, bus, sample_topic):
        """Topic can be set after configuration."""
        bus.topic = sample_topic
        assert bus.topic.question == "What is the impact of AI on healthcare?"

    @pytest.mark.asyncio
    async def test_empty_round_1_findings(self, bus):
        """No findings returns empty dict."""
        result = await bus.get_round_1_findings()
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_reports(self, bus):
        """No reports returns empty dict."""
        result = await bus.get_all_reports()
        assert result == {}


class TestThreadSafety:
    """Concurrent access doesn't corrupt data."""

    @pytest.mark.asyncio
    async def test_concurrent_publishes(self, bus):
        """Multiple concurrent publishes don't lose data."""

        async def publish(agent_id: str, i: int):
            f = Findings(
                agent_id=agent_id,
                round=1,
                summary=f"Summary {i}",
                key_points=[f"Point {i}"],
                perspective=f"Perspective {i}",
                confidence=0.5,
            )
            await bus.publish_round_1(agent_id, f)

        agents = [f"agent-{i}" for i in range(20)]
        await asyncio.gather(*[publish(a, i) for i, a in enumerate(agents)])

        all_findings = await bus.get_round_1_findings()
        assert len(all_findings) == 20

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self, bus, finding_a):
        """Concurrent reads during writes don't corrupt."""

        async def writer():
            for i in range(10):
                f = Findings(
                    agent_id=f"w{i}",
                    round=1,
                    summary=f"S{i}",
                    key_points=[f"K{i}"],
                    perspective=f"P{i}",
                    confidence=0.5,
                )
                await bus.publish_round_1(f"w{i}", f)
                await asyncio.sleep(0.001)

        async def reader():
            for _ in range(10):
                _ = await bus.get_round_1_findings()
                await asyncio.sleep(0.001)

        await asyncio.gather(writer(), reader())

        all_findings = await bus.get_round_1_findings()
        assert len(all_findings) == 10


# ─── Integration with Orchestrator ──────────────────────────────────────────


def build_bus_aware_mock_agent_factory():
    """Factory producing mock agents compatible with bus-integrated orchestrator.

    Same pattern as ``test_integration.build_mock_agent_factory`` but
    explicitly verifies that the bus is populated at each stage.
    """

    def factory(profile: AgentProfile, model_name: str, **kwargs):
        async def agent_fn(*args, **kwargs):
            first = args[0] if args else None

            if isinstance(first, ResearchTopic):
                if len(args) > 1 and isinstance(args[1], SharedKnowledge):
                    return IndividualReport(
                        agent_id=profile.id,
                        title=f"Refined Report by {profile.name}",
                        perspective_summary=f"{profile.name}'s refined perspective",
                        key_insights=["Cross-perspective analysis insight"],
                        analysis=f"Analysis by {profile.name}",
                        metaphors_or_analogies=[],
                        open_questions=["What remains unexplored?"],
                        full_text=f"Full report by {profile.name}.",
                    )
                return Findings(
                    agent_id=profile.id,
                    round=1,
                    summary=f"{profile.name} researched the topic.",
                    key_points=[
                        f"Key point from {profile.name}",
                        "Another important observation",
                    ],
                    perspective=f"{profile.name}'s unique perspective.",
                    confidence=0.7,
                )

            if isinstance(first, SharedKnowledge):
                return FollowUpQuestions(
                    agent_id=profile.id,
                    questions=[
                        f"Question from {profile.name}",
                    ],
                )

            return IndividualReport(
                agent_id=profile.id,
                title=f"Report by {profile.name}",
                perspective_summary="Summary",
                key_insights=["Insight"],
                analysis="Analysis",
                metaphors_or_analogies=[],
                open_questions=[],
                full_text="Full text",
            )

        return agent_fn

    return factory


def build_bus_scribe_factory():
    """Factory for a scribe that returns a basic ResearchPaper."""

    def factory(**kwargs):
        async def scribe(reports: dict[str, IndividualReport]) -> ResearchPaper:
            return ResearchPaper(
                title="Integrated Paper",
                abstract=f"Synthesis of {len(reports)} agents.",
                methodology_note="Multi-agent methodology.",
                sections=[],
                synthesis="Synthesis text.",
                key_takeaways=["Takeaway"],
                conclusion="Conclusion.",
            )

        return scribe

    return factory


class TestOrchestratorBusIntegration:
    """Full Orchestrator workflow with CollaborationBus integration."""

    @pytest.mark.asyncio
    async def test_bus_created_during_run(self, mock_profiles, mock_model_configs):
        """Orchestrator creates a CollaborationBus during run()."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_bus_aware_mock_agent_factory(),
            scribe_factory=build_bus_scribe_factory(),
        )

        await orch.run(
            "Integration test topic",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_bus_test",
        )

        assert hasattr(orch, "bus")
        assert orch.bus is not None
        assert orch.bus.topic is not None

    @pytest.mark.asyncio
    async def test_bus_populated_after_round_1(self, mock_profiles, mock_model_configs):
        """Round 1 findings are published to the bus."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_bus_aware_mock_agent_factory(),
            scribe_factory=build_bus_scribe_factory(),
        )

        await orch.run(
            "Test bus population",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_bus_population_test",
        )

        r1 = await orch.bus.get_round_1_findings()
        assert len(r1) == len(mock_profiles)
        for profile in mock_profiles:
            assert profile.id in r1

    @pytest.mark.asyncio
    async def test_shared_knowledge_computed(self, mock_profiles, mock_model_configs):
        """Shared knowledge is computed and stored in the bus."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_bus_aware_mock_agent_factory(),
            scribe_factory=build_bus_scribe_factory(),
        )

        await orch.run(
            "Test shared knowledge",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_sk_test",
        )

        shared = await orch.bus.get_shared_knowledge()
        assert shared is not None
        assert len(shared.all_summaries) == len(mock_profiles)
        assert len(shared.key_themes) > 0

    @pytest.mark.asyncio
    async def test_followup_questions_in_bus(self, mock_profiles, mock_model_configs):
        """Follow-up questions are published to the bus."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_bus_aware_mock_agent_factory(),
            scribe_factory=build_bus_scribe_factory(),
        )

        await orch.run(
            "Test followup in bus",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_followup_test",
        )

        for profile in mock_profiles:
            qs = await orch.bus.get_followup_questions(profile.id)
            # At least one question per agent.
            assert len(qs) >= 1

    @pytest.mark.asyncio
    async def test_reports_in_bus(self, mock_profiles, mock_model_configs):
        """Individual reports are published to the bus."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_bus_aware_mock_agent_factory(),
            scribe_factory=build_bus_scribe_factory(),
        )

        await orch.run(
            "Test reports in bus",
            time_budget="medium",
            model_mode="same",
            output_dir="/tmp/deepresearch_reports_test",
        )

        all_reports = await orch.bus.get_all_reports()
        assert len(all_reports) == len(mock_profiles)
        for profile in mock_profiles:
            assert profile.id in all_reports

    @pytest.mark.asyncio
    async def test_quick_mode_skips_round_2_in_bus(
        self, mock_profiles, mock_model_configs
    ):
        """Quick mode does not produce Round 2 findings in the bus."""
        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=build_bus_aware_mock_agent_factory(),
            scribe_factory=build_bus_scribe_factory(),
        )

        await orch.run(
            "Quick mode bus test",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_quick_bus_test",
        )

        async with orch.bus._lock:
            assert len(orch.bus.round_2_findings) == 0

    @pytest.mark.asyncio
    async def test_graceful_degradation_with_bus(
        self, mock_profiles, mock_model_configs
    ):
        """Agent failure in Round 1 doesn't break bus integration."""

        def failing_factory(profile: AgentProfile, model_name: str, **extra):
            async def agent_fn(*args, **kwargs):
                if profile.id == "agent-alpha":
                    raise RuntimeError("Agent Alpha failure")
                first = args[0] if args else None
                if isinstance(first, ResearchTopic):
                    return Findings(
                        agent_id=profile.id,
                        round=1,
                        summary="S",
                        key_points=["K"],
                        perspective="P",
                        confidence=0.5,
                    )
                if isinstance(first, SharedKnowledge):
                    return FollowUpQuestions(
                        agent_id=profile.id,
                        questions=["Follow-up Q?"],
                    )
                return IndividualReport(
                    agent_id=profile.id,
                    title="R",
                    perspective_summary="S",
                    key_insights=[],
                    analysis="A",
                    full_text="F",
                )

            return agent_fn

        def sf(**extra):
            async def scribe(reports):
                return ResearchPaper(
                    title="P",
                    abstract="A",
                    methodology_note="M",
                    sections=[],
                    synthesis="S",
                    key_takeaways=["T"],
                    conclusion="C",
                )

            return scribe

        orch = Orchestrator(
            profiles=mock_profiles,
            model_configs=mock_model_configs,
            agent_factory=failing_factory,
            scribe_factory=sf,
        )

        output_path = await orch.run(
            "Graceful degradation bus test",
            time_budget="quick",
            model_mode="same",
            output_dir="/tmp/deepresearch_gd_bus_test",
        )

        assert isinstance(output_path, Path)
        assert "agent-alpha" in orch.failed_agents
        # Bus should only contain results from the successful agent.
        r1 = await orch.bus.get_round_1_findings()
        assert "agent-alpha" not in r1
        assert "agent-beta" in r1
