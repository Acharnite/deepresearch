"""Concrete ResearchAgent that fulfils the BaseAgent contract.

Each ResearchAgent wraps an LLMClient and uses the prompt builders from
``deepresearch.utils.prompts`` plus JSON format instructions to produce
structured outputs for every lifecycle phase.
"""

from __future__ import annotations

import logging
from typing import Any

from deepresearch.agents.base_agent import BaseAgent
from deepresearch.llm.client import LLMClient, LLMError
from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    PaperSection,
    ResearchTopic,
    SharedKnowledge,
)
from deepresearch.utils.prompts import (
    build_agent_system_prompt,
    build_clarify_prompt,
    build_review_prompt,
    build_round_1_prompt,
    build_round_2_prompt,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON format instructions appended to user prompts so the LLM returns
# structured output that can be parsed into our Pydantic models.
# ---------------------------------------------------------------------------

_ROUND_1_FORMAT = """
Respond with valid JSON **only** — no markdown, no explanation:
{
  "summary": "Concise summary of your findings (2-3 paragraphs).",
  "key_points": ["Key insight 1", "Key insight 2", "Key insight 3"],
  "perspective": "Your unique perspective on this topic.",
  "confidence": 0.8
}
"""

_REVIEW_FORMAT = """
Respond with valid JSON **only**:
{
  "questions": [
    "What specific aspect needs deeper investigation?",
    "How does X relate to Y based on other agents' findings?"
  ]
}
"""

_ROUND_2_FORMAT = """
Respond with valid JSON **only**:
{
  "summary": "Refined summary of your deeper research.",
  "key_points": ["Refined point 1", "Refined point 2"],
  "perspective": "Your evolved perspective after reviewing shared knowledge.",
  "confidence": 0.85
}
"""

_REPORT_FORMAT = """
Respond with valid JSON **only**:
{
  "title": "Your Individual Research Report Title",
  "perspective_summary": "High-level summary of your perspective.",
  "key_insights": ["Insight 1", "Insight 2"],
  "analysis": "Detailed analysis text (2-4 paragraphs).",
  "metaphors_or_analogies": ["Compelling analogy 1"],
  "open_questions": ["Unresolved question 1"],
  "full_text": "Complete report text.",
  "sections": [
    {
      "heading": "Section Title",
      "source_agent_id": null,
      "content": "Section content here.",
      "subsections": []
    }
  ]
}
"""

_CLARIFY_FORMAT = """
Respond with valid JSON **only**:
{
  "response": "Clear, concise answer to the clarification question."
}
"""


class ResearchAgent(BaseAgent):
    """Concrete research agent driven by an LLM.

    Uses the agent's personality profile to build a system prompt and
    injects JSON format instructions into every user prompt so returned
    text can be parsed into the corresponding Pydantic model.

    Error handling:
        If the LLM returns invalid JSON the agent retries **once** with a
        stricter instruction.  If that also fails a fallback (empty / safe)
        model instance is returned so the research session can continue.
    """

    def __init__(self, profile: AgentProfile, llm_client: LLMClient) -> None:
        super().__init__(profile, llm_client)
        self._system_prompt: str = build_agent_system_prompt(profile)

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    async def research_round_1(self, topic: ResearchTopic) -> Findings:
        """Initial research pass — with web search capability."""
        user_prompt = build_round_1_prompt(topic.question, topic.time_budget)
        user_prompt += _ROUND_1_FORMAT

        from deepresearch.tools.web_search import WEB_SEARCH_TOOL

        try:
            response = await self.llm.generate_with_tools(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                tools=[WEB_SEARCH_TOOL],
                temperature=self.profile.temperature,
            )
        except LLMError:
            logger.warning(
                "LLM call with tools failed for agent '%s', retrying without tools",
                self.profile.id,
            )
            response = await self._generate_with_retry(user_prompt)

        data = self._try_parse_json(response, "research_round_1")
        return Findings(
            agent_id=self.profile.id,
            round=1,
            summary=data.get("summary", ""),
            key_points=data.get("key_points", []),
            perspective=data.get("perspective", ""),
            confidence=float(data.get("confidence", 0.5)),
            raw_response=response,
        )

    async def review_findings(self, shared: SharedKnowledge) -> FollowUpQuestions:
        """Review aggregated shared knowledge and pose follow-up questions."""
        user_prompt = build_review_prompt(shared)
        user_prompt += _REVIEW_FORMAT
        response = await self._generate_with_retry(user_prompt)
        data = self._try_parse_json(response, "review_findings")
        return FollowUpQuestions(
            agent_id=self.profile.id,
            questions=data.get("questions", []),
        )

    async def research_round_2(
        self,
        topic: ResearchTopic,
        shared: SharedKnowledge,
        questions: FollowUpQuestions,
    ) -> Findings:
        """Deeper research after seeing shared context and follow-up questions."""
        user_prompt = build_round_2_prompt(
            topic.question, shared, questions.questions
        )
        user_prompt += _ROUND_2_FORMAT
        response = await self._generate_with_retry(user_prompt)
        data = self._try_parse_json(response, "research_round_2")
        return Findings(
            agent_id=self.profile.id,
            round=2,
            summary=data.get("summary", ""),
            key_points=data.get("key_points", []),
            perspective=data.get("perspective", ""),
            confidence=float(data.get("confidence", 0.5)),
            raw_response=response,
        )

    async def write_report(
        self, round_1: Findings, round_2: Findings | None
    ) -> IndividualReport:
        """Consolidate findings into a final individual report."""
        r1_text = (
            f"Round 1 findings: {round_1.summary if round_1 else 'N/A'}\n"
            f"Key points: {round_1.key_points if round_1 else []}"
        )
        r2_text = (
            f"Round 2 findings: {round_2.summary if round_2 else 'N/A'}\n"
            f"Key points: {round_2.key_points if round_2 else []}"
        )
        user_prompt = (
            "# Final Individual Report\n\n"
            "Based on your research findings, produce a comprehensive "
            "individual report covering your unique perspective.\n\n"
            f"## {r1_text}\n\n"
            f"## {r2_text}\n\n"
            "Structure your report with a clear title, summary, analysis, "
            "key insights, and open questions."
        )
        user_prompt += _REPORT_FORMAT
        response = await self._generate_with_retry(user_prompt)
        data = self._try_parse_json(response, "write_report")
        sections = self._parse_sections(data.get("sections", []))
        return IndividualReport(
            agent_id=self.profile.id,
            title=data.get("title", "Research Report"),
            perspective_summary=data.get("perspective_summary", ""),
            key_insights=data.get("key_insights", []),
            analysis=data.get("analysis", ""),
            metaphors_or_analogies=data.get("metaphors_or_analogies", []),
            open_questions=data.get("open_questions", []),
            full_text=data.get("full_text", ""),
            sections=sections,
        )

    async def clarify(self, query: ClarificationQuery) -> ClarificationResponse:
        """Answer a clarification question from the scribe."""
        user_prompt = build_clarify_prompt(query.question)
        user_prompt += _CLARIFY_FORMAT
        response = await self._generate_with_retry(user_prompt)
        data = self._try_parse_json(response, "clarify")
        return ClarificationResponse(
            agent_id=self.profile.id,
            response=data.get("response", ""),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _generate_with_retry(self, user_prompt: str) -> str:
        """Call the LLM, retrying **once** on failure with a stricter instruction."""
        try:
            return await self.llm.generate_stream(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                temperature=self.profile.temperature,
            )
        except LLMError:
            logger.warning(
                "LLM call failed for agent '%s', retrying with stricter JSON "
                "instruction",
                self.profile.id,
            )
            return await self.llm.generate_stream(
                system_prompt=self._system_prompt
                + "\n\nYou MUST respond with valid JSON only — no markdown, "
                  "no explanation, no code fences.",
                user_prompt=user_prompt,
                temperature=self.profile.temperature,
            )

    def _try_parse_json(self, response: str, context: str) -> dict[str, Any]:
        """Parse JSON from an LLM response, returning ``{}`` on failure."""
        try:
            return self.llm.parse_json_response(response)
        except LLMError:
            logger.warning(
                "Failed to parse JSON in %s for agent '%s', using fallback",
                context,
                self.profile.id,
            )
            return {}

    @staticmethod
    def _parse_sections(raw: list[dict[str, Any]]) -> list[PaperSection]:
        """Deep-parse a list of section dicts into PaperSection objects."""
        sections: list[PaperSection] = []
        for item in raw:
            subs = [
                PaperSection(**s) for s in item.get("subsections", []) if isinstance(s, dict)
            ]
            sections.append(
                PaperSection(
                    heading=item.get("heading", ""),
                    source_agent_id=item.get("source_agent_id"),
                    content=item.get("content", ""),
                    subsections=subs,
                )
            )
        return sections
