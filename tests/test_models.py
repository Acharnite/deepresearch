"""Tests for Pydantic data models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from deepresearch.config.session import TimeBudget
from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    PaperSection,
    ResearchPaper,
    ResearchTopic,
    SessionConfig,
    SharedKnowledge,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def sample_topic() -> ResearchTopic:
    return ResearchTopic(question="What is quantum computing?")


@pytest.fixture
def sample_profile() -> AgentProfile:
    return AgentProfile(
        id="test_agent",
        name="Test Agent",
        emoji="🧪",
        persona_prompt="You are a test agent.",
        methodology="Test methodology.",
        knowledge_base="Test knowledge.",
        bias_mitigation="Test bias awareness.",
        voice="Test voice.",
        temperature=0.5,
    )


@pytest.fixture
def sample_findings() -> Findings:
    return Findings(
        agent_id="test_agent",
        round=1,
        summary="Test findings summary.",
        key_points=["Point 1", "Point 2"],
        perspective="A test perspective.",
        confidence=0.8,
    )


@pytest.fixture
def sample_shared_knowledge() -> SharedKnowledge:
    return SharedKnowledge(
        round_number=1,
        all_summaries={"agent_a": "Summary A", "agent_b": "Summary B"},
        key_themes=["Theme 1", "Theme 2"],
        areas_of_agreement=["Agreement 1"],
        areas_of_disagreement=["Disagreement 1"],
        knowledge_gaps=["Gap 1"],
    )


@pytest.fixture
def sample_paper_section() -> PaperSection:
    return PaperSection(
        heading="Introduction",
        content="This is the introduction.",
    )


@pytest.fixture
def sample_report() -> IndividualReport:
    return IndividualReport(
        agent_id="test_agent",
        title="Test Report",
        perspective_summary="A summary of perspectives.",
        key_insights=["Insight 1", "Insight 2"],
        analysis="Detailed analysis here.",
        full_text="Full text of the report.",
    )


# ─── TimeBudget Tests ─────────────────────────────────────────────────────────


class TestTimeBudget:
    """Edge case tests for the TimeBudget frozen dataclass."""

    def test_from_keyword_quick(self) -> None:
        """``from_keyword("quick")`` returns (quick, 240s, 2 rounds)."""
        budget = TimeBudget.from_keyword("quick")
        assert budget.keyword == "quick"
        assert budget.seconds == 240
        assert budget.max_rounds == 2

    def test_from_keyword_medium(self) -> None:
        """``from_keyword("medium")`` returns (medium, 420s, 3 rounds)."""
        budget = TimeBudget.from_keyword("medium")
        assert budget.keyword == "medium"
        assert budget.seconds == 420
        assert budget.max_rounds == 3

    def test_from_keyword_deep(self) -> None:
        """``from_keyword("deep")`` returns (deep, 660s, 5 rounds)."""
        budget = TimeBudget.from_keyword("deep")
        assert budget.keyword == "deep"
        assert budget.seconds == 660
        assert budget.max_rounds == 5

    def test_from_keyword_custom(self) -> None:
        """``from_keyword("custom")`` returns (custom, 600s, 4 rounds)."""
        budget = TimeBudget.from_keyword("custom")
        assert budget.keyword == "custom"
        assert budget.seconds == 600
        assert budget.max_rounds == 4

    def test_from_keyword_case_insensitive(self) -> None:
        """Keywords are case-insensitive (``"QUICK"`` → quick)."""
        budget = TimeBudget.from_keyword("QUICK")
        assert budget.keyword == "quick"

    def test_from_keyword_invalid_raises(self) -> None:
        """An unknown keyword raises ``ValueError``."""
        with pytest.raises(ValueError, match="Unknown time budget"):
            TimeBudget.from_keyword("nonexistent")

    def test_from_minutes_normal(self) -> None:
        """``from_minutes(5)`` produces 300 seconds."""
        budget = TimeBudget.from_minutes(5)
        assert budget.keyword == "custom"
        assert budget.seconds == 300
        assert budget.max_rounds == 4

    def test_zero_seconds(self) -> None:
        """``TimeBudget`` accepts 0 seconds (minimal boundary)."""
        budget = TimeBudget(keyword="custom", seconds=0, max_rounds=1)
        assert budget.seconds == 0
        assert budget.max_rounds == 1

    def test_negative_seconds(self) -> None:
        """``TimeBudget`` accepts negative seconds (no validation at dataclass level)."""
        budget = TimeBudget(keyword="custom", seconds=-100, max_rounds=4)
        assert budget.seconds == -100

    def test_large_value_24h(self) -> None:
        """``TimeBudget`` accepts 86400 seconds (24 hours)."""
        budget = TimeBudget(keyword="custom", seconds=86400, max_rounds=10)
        assert budget.seconds == 86400
        assert budget.max_rounds == 10

    def test_from_minutes_zero(self) -> None:
        """``from_minutes(0)`` produces 0 seconds."""
        budget = TimeBudget.from_minutes(0)
        assert budget.seconds == 0

    def test_from_minutes_negative(self) -> None:
        """``from_minutes(-5)`` produces -300 seconds."""
        budget = TimeBudget.from_minutes(-5)
        assert budget.seconds == -300

    def test_from_minutes_large(self) -> None:
        """``from_minutes(1440)`` = 86400 seconds (24 hours)."""
        budget = TimeBudget.from_minutes(1440)
        assert budget.seconds == 86400

    def test_frozen_dataclass_immutable(self) -> None:
        """TimeBudget is frozen — attribute assignment raises."""
        budget = TimeBudget.from_keyword("quick")
        with pytest.raises(AttributeError):
            budget.seconds = 999  # type: ignore[misc]


# ─── ResearchTopic Tests ─────────────────────────────────────────────────────


class TestResearchTopic:
    def test_valid_minimal(self):
        topic = ResearchTopic(question="What is AI?")
        assert topic.question == "What is AI?"
        assert topic.time_budget == "medium"
        assert topic.model_mode == "same"

    def test_valid_full(self):
        topic = ResearchTopic(
            question="What is AI?",
            time_budget="deep",
            model_mode="random",
        )
        assert topic.question == "What is AI?"
        assert topic.time_budget == "deep"
        assert topic.model_mode == "random"

    def test_invalid_time_budget(self):
        # Now a free-form string — "extreme" is allowed (treated as custom).
        topic = ResearchTopic(question="Test", time_budget="extreme")  # type: ignore
        assert topic.time_budget == "extreme"

    def test_invalid_model_mode(self):
        with pytest.raises(ValidationError):
            ResearchTopic(question="Test", model_mode="invalid")  # type: ignore


# ─── AgentProfile Tests ──────────────────────────────────────────────────────


class TestAgentProfile:
    def test_valid(self, sample_profile):
        assert sample_profile.id == "test_agent"
        assert sample_profile.temperature == 0.5

    def test_temperature_bounds(self):
        with pytest.raises(ValidationError):
            AgentProfile(
                id="bad",
                name="Bad",
                emoji="❌",
                persona_prompt="X",
                methodology="X",
                knowledge_base="X",
                bias_mitigation="X",
                voice="X",
                temperature=2.1,
            )

    def test_temperature_min(self):
        with pytest.raises(ValidationError):
            AgentProfile(
                id="bad",
                name="Bad",
                emoji="❌",
                persona_prompt="X",
                methodology="X",
                knowledge_base="X",
                bias_mitigation="X",
                voice="X",
                temperature=-0.1,
            )

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            AgentProfile(id="incomplete", name="Incomplete", emoji="❌")  # type: ignore


# ─── Findings Tests ──────────────────────────────────────────────────────────


class TestFindings:
    def test_valid(self, sample_findings):
        assert sample_findings.agent_id == "test_agent"
        assert sample_findings.round == 1
        assert sample_findings.confidence == 0.8

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            Findings(
                agent_id="a",
                round=1,
                summary="S",
                key_points=["P1"],
                perspective="P",
                confidence=1.5,
            )

    def test_defaults(self):
        f = Findings(
            agent_id="a",
            round=1,
            summary="S",
            key_points=["P1"],
            perspective="P",
        )
        assert f.confidence == 0.5
        assert f.raw_response is None

    def test_serialization_roundtrip(self, sample_findings):
        data = sample_findings.model_dump()
        restored = Findings.model_validate(data)
        assert restored == sample_findings


# ─── SharedKnowledge Tests ───────────────────────────────────────────────────


class TestSharedKnowledge:
    def test_valid(self, sample_shared_knowledge):
        assert sample_shared_knowledge.round_number == 1
        assert "agent_a" in sample_shared_knowledge.all_summaries

    def test_serialization_roundtrip(self, sample_shared_knowledge):
        data = sample_shared_knowledge.model_dump()
        restored = SharedKnowledge.model_validate(data)
        assert restored == sample_shared_knowledge


# ─── FollowUpQuestions Tests ─────────────────────────────────────────────────


class TestFollowUpQuestions:
    def test_valid(self):
        fq = FollowUpQuestions(agent_id="a", questions=["Q1?", "Q2?"])
        assert len(fq.questions) == 2

    def test_empty_questions(self):
        fq = FollowUpQuestions(agent_id="a", questions=[])
        assert fq.questions == []


# ─── PaperSection Tests ──────────────────────────────────────────────────────


class TestPaperSection:
    def test_valid(self, sample_paper_section):
        assert sample_paper_section.heading == "Introduction"
        assert sample_paper_section.subsections == []

    def test_with_subsections(self):
        sub = PaperSection(heading="Sub", content="Sub content")
        section = PaperSection(
            heading="Main",
            content="Main content",
            subsections=[sub],
        )
        assert len(section.subsections) == 1
        assert section.subsections[0].heading == "Sub"

    def test_serialization_roundtrip(self, sample_paper_section):
        data = sample_paper_section.model_dump()
        restored = PaperSection.model_validate(data)
        assert restored == sample_paper_section


# ─── IndividualReport Tests ──────────────────────────────────────────────────


class TestIndividualReport:
    def test_valid(self, sample_report):
        assert sample_report.agent_id == "test_agent"
        assert sample_report.title == "Test Report"
        assert sample_report.sections == []

    def test_with_sections(self):
        section = PaperSection(heading="Intro", content="Content")
        report = IndividualReport(
            agent_id="a",
            title="Report",
            perspective_summary="PS",
            key_insights=["I1"],
            analysis="A",
            full_text="FT",
            sections=[section],
        )
        assert len(report.sections) == 1
        assert report.sections[0].heading == "Intro"

    def test_serialization_roundtrip(self, sample_report):
        data = sample_report.model_dump()
        restored = IndividualReport.model_validate(data)
        assert restored == sample_report


# ─── ClarificationQuery Tests ────────────────────────────────────────────────


class TestClarificationQuery:
    def test_valid(self):
        cq = ClarificationQuery(agent_id="a", question="What do you mean?")
        assert cq.question == "What do you mean?"
        assert cq.context is None

    def test_with_context(self):
        cq = ClarificationQuery(
            agent_id="a",
            question="What do you mean?",
            context="Regarding the third point...",
        )
        assert cq.context == "Regarding the third point..."


# ─── ClarificationResponse Tests ─────────────────────────────────────────────


class TestClarificationResponse:
    def test_valid(self):
        cr = ClarificationResponse(agent_id="a", response="I meant X.")
        assert cr.response == "I meant X."


# ─── ResearchPaper Tests ─────────────────────────────────────────────────────


class TestResearchPaper:
    def test_valid(self):
        section = PaperSection(heading="Intro", content="Content")
        paper = ResearchPaper(
            title="Test Paper",
            abstract="Abstract text.",
            methodology_note="Methodology note.",
            sections=[section],
            synthesis="Synthesis text.",
            key_takeaways=["Takeaway 1"],
            conclusion="Conclusion text.",
        )
        assert paper.title == "Test Paper"
        assert isinstance(paper.generated_at, datetime)

    def test_serialization_roundtrip(self):
        section = PaperSection(heading="Intro", content="Content")
        paper = ResearchPaper(
            title="Test Paper",
            abstract="Abstract.",
            methodology_note="Method.",
            sections=[section],
            synthesis="Synthesis.",
            key_takeaways=["T1"],
            conclusion="Conclusion.",
        )
        data = paper.model_dump()
        restored = ResearchPaper.model_validate(data)
        assert restored.title == paper.title
        assert len(restored.sections) == 1


# ─── SessionConfig Tests ─────────────────────────────────────────────────────


class TestSessionConfig:
    def test_valid(self, sample_topic, sample_profile):
        config = SessionConfig(
            topic=sample_topic,
            agent_profiles=[sample_profile],
            agent_models={"test_agent": "gpt-4o"},
        )
        assert config.topic.question == "What is quantum computing?"
        assert config.time_budget_seconds == 30

    def test_time_budget_limits(self):
        topic = ResearchTopic(question="Test")
        profile = AgentProfile(
            id="a",
            name="A",
            emoji="🔍",
            persona_prompt="P",
            methodology="M",
            knowledge_base="K",
            bias_mitigation="B",
            voice="V",
            temperature=0.5,
        )
        with pytest.raises(ValidationError):
            SessionConfig(
                topic=topic,
                agent_profiles=[profile],
                agent_models={"a": "gpt-4o"},
                time_budget_seconds=0,
            )

    def test_time_budget_upper_limit(self):
        topic = ResearchTopic(question="Test")
        profile = AgentProfile(
            id="a",
            name="A",
            emoji="🔍",
            persona_prompt="P",
            methodology="M",
            knowledge_base="K",
            bias_mitigation="B",
            voice="V",
            temperature=0.5,
        )
        with pytest.raises(ValidationError):
            SessionConfig(
                topic=topic,
                agent_profiles=[profile],
                agent_models={"a": "gpt-4o"},
                time_budget_seconds=3601,  # Exceeds new max of 3600
            )

    def test_serialization_roundtrip(self, sample_topic, sample_profile):
        config = SessionConfig(
            topic=sample_topic,
            agent_profiles=[sample_profile],
            agent_models={"test_agent": "gpt-4o"},
        )
        data = config.model_dump()
        restored = SessionConfig.model_validate(data)
        assert restored.topic.question == config.topic.question
        assert len(restored.agent_profiles) == 1
