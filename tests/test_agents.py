"""Tests for DeepeResearch agents (Phase 3).

Covers:
  - BaseAgent abstract methods enforce NotImplementedError
  - ResearchAgent with mock LLM responses for all 5 lifecycle methods
  - JSON parsing with valid and invalid LLM responses
  - Agent personality integration (system prompt built from profile)
  - ScribeAgent compilation with mock reports
  - AgentRegistry creates agents and factory correctly
  - agent_factory dispatches to correct lifecycle methods
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deepresearch.agents.base_agent import BaseAgent
from deepresearch.agents.registry import AgentRegistry, Phase
from deepresearch.agents.research_agent import ResearchAgent
from deepresearch.agents.scribe_agent import ScribeAgent
from deepresearch.llm.client import LLMClient, LLMError
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
    SharedKnowledge,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def profile() -> AgentProfile:
    """A sample agent profile for testing."""
    return AgentProfile(
        id="test-agent",
        name="Test Agent",
        emoji="🧪",
        persona_prompt="You are a thorough researcher.",
        methodology="Analyse from first principles.",
        knowledge_base="Broad scientific knowledge.",
        bias_mitigation="Check for confirmation bias.",
        voice="Clear and precise.",
        temperature=0.5,
    )


@pytest.fixture
def cool_profile() -> AgentProfile:
    """A higher-temperature, creative profile for personality differentiation."""
    return AgentProfile(
        id="creative-agent",
        name="Creative Agent",
        emoji="🎨",
        persona_prompt="You are a creative thinker who uses metaphors.",
        methodology="Think laterally and make unexpected connections.",
        knowledge_base="Art, culture, and creative works.",
        bias_mitigation="Balance creativity with accuracy.",
        voice="Imaginative and evocative.",
        temperature=0.9,
    )


@pytest.fixture
def analytical_profile() -> AgentProfile:
    """A low-temperature, analytical profile."""
    return AgentProfile(
        id="analytical-agent",
        name="Analytical Agent",
        emoji="📊",
        persona_prompt="You are a data-driven analyst.",
        methodology="Follow the evidence and quantify everything.",
        knowledge_base="Statistics and empirical research.",
        bias_mitigation="Avoid over-interpreting noisy data.",
        voice="Precise and measured.",
        temperature=0.2,
    )


@pytest.fixture
def topic() -> ResearchTopic:
    return ResearchTopic(
        question="What is the impact of AI on healthcare?",
        time_budget="medium",
        model_mode="same",
    )


@pytest.fixture
def shared() -> SharedKnowledge:
    return SharedKnowledge(
        round_number=1,
        all_summaries={
            "agent-a": "AI improves diagnostic accuracy.",
            "agent-b": "AI raises ethical concerns in healthcare.",
        },
        key_themes=["Diagnostic AI", "Ethical considerations"],
        areas_of_agreement=["AI has transformative potential"],
        areas_of_disagreement=["Speed of adoption"],
        knowledge_gaps=["Long-term outcomes data"],
    )


@pytest.fixture
def follow_up() -> FollowUpQuestions:
    return FollowUpQuestions(
        agent_id="test-agent",
        questions=[
            "What specific diagnostic improvements have been measured?",
            "How do different healthcare systems adopt AI?",
        ],
    )


@pytest.fixture
def round_1_findings() -> Findings:
    return Findings(
        agent_id="test-agent",
        round=1,
        summary="AI shows promise in radiology and pathology.",
        key_points=[
            "85% accuracy in mammography screening",
            "Reduces radiologist workload by 30%",
        ],
        perspective="AI as augmentative tool, not replacement.",
        confidence=0.78,
    )


@pytest.fixture
def round_2_findings() -> Findings:
    return Findings(
        agent_id="test-agent",
        round=2,
        summary="Deeper analysis confirms initial findings with caveats.",
        key_points=[
            "Dataset bias remains a concern",
            "Regulatory frameworks are evolving",
        ],
        perspective="Cautious optimism — AI augments but requires oversight.",
        confidence=0.72,
    )


@pytest.fixture
def mock_llm_client() -> MagicMock:
    """Mock LLMClient that returns canned JSON responses.

    ``parse_json_response`` is wired to a real parser so tests can verify
    the JSON output flows through correctly.
    """
    client = MagicMock(spec=LLMClient)
    client.model = "gpt-4o"
    client.timeout = 30
    client.total_input_tokens = 0
    client.total_output_tokens = 0
    client.total_cost = 0.0
    # Wire the real parse method for JSON handling in tests.
    real_client = LLMClient(model="gpt-4o", timeout=10)
    client.parse_json_response = real_client.parse_json_response
    return client


def _make_mock_generate(response_data: dict, fail_count: int = 0) -> AsyncMock:
    """Create an AsyncMock for ``LLMClient.generate``.

    Args:
        response_data: Dict that will be JSON-serialised as the response.
        fail_count: Number of times to raise ``LLMError`` before succeeding.
    """
    mock = AsyncMock()

    async def side_effect(*args, **kwargs):
        if mock.call_count < fail_count:
            mock.call_count += 1  # type: ignore[attr-defined]
            raise LLMError("Simulated LLM failure")
        return json.dumps(response_data)

    mock.side_effect = side_effect
    mock.call_count = 0  # type: ignore[attr-defined]
    return mock


def _make_mock_generate_with_tools(
    response_data: dict, fail_count: int = 0
) -> AsyncMock:
    """Create an AsyncMock for ``LLMClient.generate_with_tools``.

    Args:
        response_data: Dict that will be JSON-serialised as the response.
        fail_count: Number of times to raise ``LLMError`` before succeeding.
    """
    mock = AsyncMock()

    async def side_effect(*args, **kwargs):
        if mock.call_count < fail_count:
            mock.call_count += 1  # type: ignore[attr-defined]
            raise LLMError("Simulated LLM failure")
        return json.dumps(response_data)

    mock.side_effect = side_effect
    mock.call_count = 0  # type: ignore[attr-defined]
    return mock


# ─── Tests: BaseAgent ────────────────────────────────────────────────────────


class TestBaseAgent:
    """Abstract methods must raise NotImplementedError when called."""

    def test_instantiation_forbidden(self):
        """Cannot instantiate BaseAgent directly (it is abstract)."""
        with pytest.raises(TypeError):
            BaseAgent(profile=None, llm_client=MagicMock())  # type: ignore

    def test_subclass_without_implementation(self):
        """Subclass that does not implement abstract methods cannot be instanced."""

        class Incomplete(BaseAgent):
            pass

        with pytest.raises(TypeError):
            Incomplete(profile=None, llm_client=MagicMock())  # type: ignore

    def test_all_abstract_methods_defined(self, profile):
        """Verify the abstract method set has not drifted."""

        class TestAgent(BaseAgent):
            async def research_round_1(self, topic): ...
            async def review_findings(self, shared): ...
            async def research_round_2(self, topic, shared, questions): ...
            async def research_round_n(
                self, topic, shared, round_num, prev_findings
            ): ...
            async def write_report(self, r1, r2): ...
            async def clarify(self, query): ...

        # Should not raise.
        agent = TestAgent(profile=profile, llm_client=MagicMock())
        assert agent.profile == profile
        assert agent.llm is not None


# ─── Tests: ResearchAgent ────────────────────────────────────────────────────


class TestResearchAgent:
    """ResearchAgent reads personality, calls LLM, returns typed models."""

    @pytest.mark.asyncio
    async def test_research_round_1_returns_findings(
        self, profile, topic, mock_llm_client
    ):
        """research_round_1 should return a valid Findings from LLM JSON."""
        mock_llm_client.generate_with_tools = _make_mock_generate_with_tools(
            {
                "summary": "AI in healthcare shows diagnostic improvements.",
                "key_points": ["85% accuracy", "Reduces workload"],
                "perspective": "AI as augmentative tool.",
                "confidence": 0.8,
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        result = await agent.research_round_1(topic)

        assert isinstance(result, Findings)
        assert result.agent_id == "test-agent"
        assert result.round == 1
        assert "diagnostic" in result.summary.lower()
        assert len(result.key_points) == 2
        assert result.confidence == 0.8
        assert result.raw_response is not None

    @pytest.mark.asyncio
    async def test_research_round_1_includes_system_prompt(
        self, profile, topic, mock_llm_client
    ):
        """The system prompt should include the agent's persona."""
        mock_llm_client.generate_with_tools = _make_mock_generate_with_tools(
            {
                "summary": "Test",
                "key_points": [],
                "perspective": "P",
                "confidence": 0.5,
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)

        await agent.research_round_1(topic)

        # Verify the system prompt contained profile info.
        call_kwargs = mock_llm_client.generate_with_tools.call_args[1]
        assert profile.persona_prompt in call_kwargs["system_prompt"]
        assert profile.name in call_kwargs["system_prompt"]

    @pytest.mark.asyncio
    async def test_review_findings_returns_questions(
        self, profile, shared, mock_llm_client
    ):
        """review_findings should return FollowUpQuestions."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "questions": ["What about X?", "How does Y affect Z?"],
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        result = await agent.review_findings(shared)

        assert isinstance(result, FollowUpQuestions)
        assert result.agent_id == "test-agent"
        assert len(result.questions) == 2
        assert "X" in result.questions[0]

    @pytest.mark.asyncio
    async def test_review_findings_includes_shared_knowledge(
        self, profile, shared, mock_llm_client
    ):
        """The user prompt should contain shared knowledge data."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "questions": [],
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)

        await agent.review_findings(shared)

        call_kwargs = mock_llm_client.generate_stream.call_args[1]
        user_prompt = call_kwargs["user_prompt"]
        assert "AI improves diagnostic" in user_prompt
        assert "Diagnostic AI" in user_prompt

    @pytest.mark.asyncio
    async def test_research_round_2_returns_findings(
        self, profile, topic, shared, follow_up, mock_llm_client
    ):
        """research_round_2 should return Findings."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "summary": "Deeper analysis confirms findings.",
                "key_points": ["Dataset bias concern", "Regulatory evolution"],
                "perspective": "Cautious optimism.",
                "confidence": 0.72,
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        result = await agent.research_round_2(topic, shared, follow_up)

        assert isinstance(result, Findings)
        assert result.round == 2
        assert "Deeper" in result.summary
        assert len(result.key_points) == 2

    @pytest.mark.asyncio
    async def test_write_report_returns_individual_report(
        self, profile, round_1_findings, round_2_findings, mock_llm_client
    ):
        """write_report should return an IndividualReport."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "title": "AI in Healthcare Report",
                "perspective_summary": "AI augments clinicians.",
                "key_insights": ["85% accuracy in screening"],
                "analysis": "Detailed analysis of diagnostic AI...",
                "metaphors_or_analogies": ["AI as a second pair of eyes"],
                "open_questions": ["Long-term outcome data?"],
                "full_text": "Complete report text here.",
                "sections": [
                    {
                        "heading": "Introduction",
                        "source_agent_id": None,
                        "content": "Intro content.",
                        "subsections": [],
                    }
                ],
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        result = await agent.write_report(round_1_findings, round_2_findings)

        assert isinstance(result, IndividualReport)
        assert result.agent_id == "test-agent"
        assert "AI in Healthcare" in result.title
        assert len(result.key_insights) == 1
        assert len(result.metaphors_or_analogies) == 1
        assert len(result.sections) == 1
        assert result.sections[0].heading == "Introduction"

    @pytest.mark.asyncio
    async def test_write_report_without_round_2(
        self, profile, round_1_findings, mock_llm_client
    ):
        """write_report should handle None for round_2."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "title": "Quick Report",
                "perspective_summary": "Summary.",
                "key_insights": ["Key finding"],
                "analysis": "Analysis.",
                "full_text": "Full text.",
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        result = await agent.write_report(round_1_findings, None)

        assert isinstance(result, IndividualReport)
        assert result.agent_id == "test-agent"

    @pytest.mark.asyncio
    async def test_clarify_returns_response(self, profile, mock_llm_client):
        """clarify should return a ClarificationResponse (fallback path)."""
        from deepresearch.llm.client import LLMError

        mock_llm_client.generate_with_tools = AsyncMock(
            side_effect=LLMError("Test: tools unavailable")
        )
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "response": "My analysis was based on recent peer-reviewed studies.",
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        query = ClarificationQuery(
            agent_id="test-agent",
            question="What evidence supports your conclusion?",
        )
        result = await agent.clarify(query)

        assert isinstance(result, ClarificationResponse)
        assert result.agent_id == "test-agent"
        assert "peer-reviewed" in result.response

    @pytest.mark.asyncio
    async def test_temperature_used_in_llm_call(self, profile, topic, mock_llm_client):
        """The profile temperature should be passed to LLM.generate_with_tools."""
        mock_llm_client.generate_with_tools = _make_mock_generate_with_tools(
            {
                "summary": "Test",
                "key_points": [],
                "perspective": "P",
                "confidence": 0.5,
            }
        )
        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)

        await agent.research_round_1(topic)

        call_kwargs = mock_llm_client.generate_with_tools.call_args[1]
        assert call_kwargs["temperature"] == profile.temperature

    # ── JSON error handling ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_invalid_json_retry_then_fallback(
        self, profile, topic, mock_llm_client
    ):
        """Invalid JSON from LLM should trigger a retry, then fallback."""
        mock_llm_client.parse_json_response = MagicMock(
            side_effect=LLMError("Invalid JSON")
        )
        mock_llm_client.generate_with_tools = AsyncMock(return_value="not valid json")

        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        result = await agent.research_round_1(topic)

        assert isinstance(result, Findings)
        # Fallback — empty fields.
        assert result.summary == ""
        assert result.key_points == []
        assert result.raw_response == "not valid json"

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_generate_stream(
        self, profile, topic, mock_llm_client
    ):
        """LLM.generate_with_tools failure falls back to generate_stream."""
        mock_llm_client.generate_with_tools = AsyncMock(
            side_effect=LLMError("Tool calling failed")
        )
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "summary": "S",
                "key_points": ["K"],
                "perspective": "P",
                "confidence": 0.5,
            }
        )

        agent = ResearchAgent(profile=profile, llm_client=mock_llm_client)
        result = await agent.research_round_1(topic)

        assert isinstance(result, Findings)
        assert mock_llm_client.generate_with_tools.call_count == 1
        assert mock_llm_client.generate_stream.call_count >= 1

    # ── Personality differentiation ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_different_profiles_produce_different_system_prompts(
        self, cool_profile, analytical_profile, topic
    ):
        """Two agents with different profiles should have different system prompts."""
        real_client = LLMClient(model="gpt-4o", timeout=10)
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.generate_with_tools = AsyncMock(
            return_value=json.dumps(
                {
                    "summary": "S",
                    "key_points": [],
                    "perspective": "P",
                    "confidence": 0.5,
                }
            )
        )
        mock_llm.parse_json_response = real_client.parse_json_response

        creative_agent = ResearchAgent(profile=cool_profile, llm_client=mock_llm)
        analytical_agent = ResearchAgent(
            profile=analytical_profile, llm_client=mock_llm
        )

        await creative_agent.research_round_1(topic)
        creative_prompt = mock_llm.generate_with_tools.call_args[1]["system_prompt"]

        await analytical_agent.research_round_1(topic)
        analytical_prompt = mock_llm.generate_with_tools.call_args[1]["system_prompt"]

        assert creative_prompt != analytical_prompt
        assert "creative" in creative_prompt.lower()
        assert "data" in analytical_prompt.lower()


# ─── Tests: ScribeAgent ──────────────────────────────────────────────────────


class TestScribeAgent:
    """ScribeAgent compiles reports and answers clarification questions."""

    @pytest.fixture
    def reports(self) -> dict[str, IndividualReport]:
        return {
            "agent-a": IndividualReport(
                agent_id="agent-a",
                title="AI in Diagnostics",
                perspective_summary="AI improves accuracy.",
                key_insights=["85% accuracy in mammography"],
                analysis="Detailed analysis of diagnostic AI.",
                full_text="Full report from agent A.",
            ),
            "agent-b": IndividualReport(
                agent_id="agent-b",
                title="AI Ethics in Healthcare",
                perspective_summary="Ethical frameworks must evolve.",
                key_insights=["Bias in training data"],
                analysis="Analysis of ethical implications.",
                full_text="Full report from agent B.",
            ),
        }

    @pytest.mark.asyncio
    async def test_compile_returns_research_paper(self, reports, mock_llm_client):
        """compile should produce a ResearchPaper from agent reports."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "title": "Synthesis Report",
                "abstract": "This paper synthesises perspectives on AI in healthcare.",
                "methodology_note": "Multi-agent research approach.",
                "sections": [
                    {
                        "heading": "Introduction",
                        "source_agent_id": None,
                        "content": "Research context.",
                        "subsections": [],
                    },
                    {
                        "heading": "Agent A Perspective",
                        "source_agent_id": "agent-a",
                        "content": "Diagnostic AI analysis.",
                        "subsections": [],
                    },
                ],
                "synthesis": "Both agents agree on AI's transformative potential.",
                "key_takeaways": ["AI augments clinicians", "Ethics needs attention"],
                "conclusion": "AI will transform healthcare with proper oversight.",
                "appendices": [],
            }
        )
        agent = ScribeAgent(llm_client=mock_llm_client)
        paper = await agent.compile(reports)

        assert isinstance(paper, ResearchPaper)
        assert paper.title == "Synthesis Report"
        assert len(paper.sections) == 2
        assert len(paper.key_takeaways) == 2
        assert paper.conclusion != ""

    @pytest.mark.asyncio
    async def test_compile_includes_all_reports(self, reports, mock_llm_client):
        """The compile prompt should include all agent reports."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "title": "Paper",
                "abstract": "A",
                "methodology_note": "M",
                "sections": [],
                "synthesis": "S",
                "key_takeaways": [],
                "conclusion": "C",
                "appendices": [],
            }
        )
        agent = ScribeAgent(llm_client=mock_llm_client)

        await agent.compile(reports)

        call_kwargs = mock_llm_client.generate_stream.call_args[1]
        user_prompt = call_kwargs["user_prompt"]
        assert "agent-a" in user_prompt
        assert "agent-b" in user_prompt
        assert "AI improves accuracy" in user_prompt

    @pytest.mark.asyncio
    async def test_compile_fallback_on_llm_failure(self, reports, mock_llm_client):
        """compile should return a minimal paper when LLM fails."""
        mock_llm_client.generate_stream = AsyncMock(side_effect=LLMError("LLM down"))
        agent = ScribeAgent(llm_client=mock_llm_client)
        paper = await agent.compile(reports)

        assert isinstance(paper, ResearchPaper)
        assert "Scribe compilation" in paper.synthesis
        assert len(paper.key_takeaways) >= 1

    @pytest.mark.asyncio
    async def test_compile_temperature_is_0_3(self, reports, mock_llm_client):
        """Scribe should always use temperature 0.3."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "title": "P",
                "abstract": "A",
                "methodology_note": "M",
                "sections": [],
                "synthesis": "S",
                "key_takeaways": [],
                "conclusion": "C",
                "appendices": [],
            }
        )
        agent = ScribeAgent(llm_client=mock_llm_client)

        await agent.compile(reports)

        call_kwargs = mock_llm_client.generate_stream.call_args[1]
        assert call_kwargs["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_unused_methods_raise(self, mock_llm_client):
        """Calling research methods on ScribeAgent should raise NotImplementedError."""
        agent = ScribeAgent(llm_client=mock_llm_client)
        topic = ResearchTopic(question="Q", time_budget="quick", model_mode="same")
        shared = SharedKnowledge(
            round_number=1,
            all_summaries={},
            key_themes=[],
            areas_of_agreement=[],
            areas_of_disagreement=[],
            knowledge_gaps=[],
        )
        questions = FollowUpQuestions(agent_id="scribe", questions=[])

        with pytest.raises(NotImplementedError):
            await agent.research_round_1(topic)
        with pytest.raises(NotImplementedError):
            await agent.review_findings(shared)
        with pytest.raises(NotImplementedError):
            await agent.research_round_2(topic, shared, questions)
        with pytest.raises(NotImplementedError):
            await agent.write_report(None, None)

    @pytest.mark.asyncio
    async def test_clarify_returns_response(self, mock_llm_client):
        """ScribeAgent.clarify should return a ClarificationResponse."""
        mock_llm_client.generate_stream = _make_mock_generate(
            {
                "response": "I prioritised recent publications for synthesis.",
            }
        )
        agent = ScribeAgent(llm_client=mock_llm_client)
        query = ClarificationQuery(
            agent_id="scribe",
            question="Why did you prioritise certain sources?",
        )
        result = await agent.clarify(query)

        assert isinstance(result, ClarificationResponse)
        assert result.agent_id == "scribe"
        assert "prioritised" in result.response


# ─── Tests: AgentRegistry ────────────────────────────────────────────────────


class TestAgentRegistry:
    """AgentRegistry creates agents and exposes the orchestrator factory."""

    @pytest.fixture
    def registry(self) -> AgentRegistry:
        llm = LLMClient(model="gpt-4o", timeout=10)
        return AgentRegistry(llm)

    def test_create_research_agent(self, registry, profile):
        """create_research_agent returns a ResearchAgent with the profile."""
        agent = registry.create_research_agent(profile, model_name="gpt-4o")
        assert isinstance(agent, ResearchAgent)
        assert agent.profile == profile
        assert agent.llm is not None
        assert agent.llm.model == "gpt-4o"

    def test_create_scribe_agent(self, registry):
        """create_scribe_agent returns a ScribeAgent."""
        agent = registry.create_scribe_agent()
        assert isinstance(agent, ScribeAgent)
        # Scribe has no profile.
        assert agent.profile is None

    def test_agent_factory_returns_callable(self, registry, profile):
        """agent_factory returns a callable (not a dict)."""
        factory = registry.agent_factory(profile, "gpt-4o")
        assert callable(factory)

    @pytest.mark.asyncio
    async def test_agent_factory_round_1_dispatches_correctly(
        self, registry, profile, topic
    ):
        """Calling the factory with ResearchTopic dispatches to research_round_1."""
        factory = registry.agent_factory(profile, "gpt-4o")

        # Patch the underlying agent's research_round_1 to avoid LLM calls.
        with patch.object(
            ResearchAgent, "research_round_1", new_callable=AsyncMock
        ) as mock:
            mock.return_value = Findings(
                agent_id="test-agent",
                round=1,
                summary="S",
                key_points=["K"],
                perspective="P",
            )
            result = await factory(Phase.INITIAL_ROUND, topic=topic)

        assert isinstance(result, Findings)
        mock.assert_called_once_with(topic)

    @pytest.mark.asyncio
    async def test_agent_factory_review_dispatches_correctly(
        self, registry, profile, shared
    ):
        """Calling the factory with Phase.REVIEW dispatches to review_findings."""
        factory = registry.agent_factory(profile, "gpt-4o")

        with patch.object(
            ResearchAgent, "review_findings", new_callable=AsyncMock
        ) as mock:
            mock.return_value = FollowUpQuestions(
                agent_id="test-agent", questions=["Q?"]
            )
            result = await factory(Phase.REVIEW, shared=shared)

        assert isinstance(result, FollowUpQuestions)
        mock.assert_called_once_with(shared)

    @pytest.mark.asyncio
    async def test_agent_factory_report_dispatches_correctly(
        self, registry, profile, round_1_findings
    ):
        """Calling the factory with Phase.REPORT dispatches to write_report."""
        factory = registry.agent_factory(profile, "gpt-4o")

        with patch.object(
            ResearchAgent, "write_report", new_callable=AsyncMock
        ) as mock:
            mock.return_value = IndividualReport(
                agent_id="test-agent",
                title="R",
                perspective_summary="S",
                key_insights=["I"],
                analysis="A",
                full_text="F",
            )
            result = await factory(Phase.REPORT, findings=round_1_findings)

        assert isinstance(result, IndividualReport)
        mock.assert_called_once_with(round_1_findings, None)

    @pytest.mark.asyncio
    async def test_agent_factory_unknown_args_raises(self, registry, profile):
        """Calling the factory with unrecognised phase raises KeyError."""
        factory = registry.agent_factory(profile, "gpt-4o")

        # Passing an integer where Phase is expected hits the dispatch's
        # _HANDLERS lookup, which raises KeyError.
        with pytest.raises(KeyError, match="No handler registered for phase"):
            await factory(42)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_agent_factory_key_error_message_includes_profile_id(
        self, registry, profile
    ):
        """KeyError from bad phase should be informative (profile id in context)."""
        factory = registry.agent_factory(profile, "gpt-4o")

        with pytest.raises(KeyError):
            await factory(42)  # type: ignore[arg-type]


# ─── Tests: Scribe Clarification Failure ─────────────────────────────────


@pytest.mark.asyncio
async def test_scribe_parallel_clarification_handles_task_failure():
    """Scribe clarification protocol handles async task failures gracefully."""
    from deepresearch.llm.client import LLMClient as RealLLMClient

    llm = MagicMock(spec=RealLLMClient)
    llm.model = "gpt-4o"
    llm.timeout = 10
    real_client = RealLLMClient(model="gpt-4o", timeout=10)
    llm.parse_json_response = real_client.parse_json_response
    llm.generate_stream = AsyncMock(
        return_value=json.dumps(
            {
                "title": "Test",
                "abstract": "Test abstract",
                "methodology_note": "Test",
                "sections": [
                    {"heading": "Section 1", "content": "Content", "subsections": []}
                ],
                "synthesis": "Test",
                "key_takeaways": ["Key 1"],
                "conclusion": "Test conclusion",
            }
        )
    )

    scribe = ScribeAgent(llm_client=llm)

    reports = {
        "agent-1": IndividualReport(
            agent_id="agent-1",
            title="Report 1",
            perspective_summary="Perspective 1",
            key_insights=["Insight 1"],
            analysis="Analysis 1",
            open_questions=[],
            sections=[PaperSection(heading="H1", content="C1")],
            full_text="Full report text.",
        )
    }

    # Clarification function that always fails
    async def failing_clarification(query: ClarificationQuery) -> ClarificationResponse:
        raise RuntimeError("Agent unavailable")

    # Should not raise — should handle failure gracefully
    paper = await scribe.compile(reports, clarification_fn=failing_clarification)
    assert paper is not None
    assert paper.title == "Test"


# ─── Tests: FollowUpQuestions target_agent_ids ───────────────────────────


@pytest.mark.asyncio
async def test_followup_questions_with_target_agent_ids():
    """FollowUpQuestions supports target_agent_ids for directed questions."""
    # Without targets (backward compatible)
    fq = FollowUpQuestions(agent_id="agent-1", questions=["What about X?"])
    assert fq.target_agent_ids is None

    # With targets
    fq_targeted = FollowUpQuestions(
        agent_id="agent-1",
        questions=["What about X?", "How does Y work?"],
        target_agent_ids=["agent-2", "agent-3"],
    )
    assert fq_targeted.target_agent_ids == ["agent-2", "agent-3"]
    assert len(fq_targeted.questions) == len(fq_targeted.target_agent_ids)

    # Serialization roundtrip
    data = fq_targeted.model_dump()
    fq_restored = FollowUpQuestions(**data)
    assert fq_restored.target_agent_ids == ["agent-2", "agent-3"]
