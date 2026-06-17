"""Concrete ResearchAgent that fulfils the BaseAgent contract.

Each ResearchAgent wraps an LLMClient and uses the prompt builders from
``deepresearch.utils.prompts`` plus JSON format instructions to produce
structured outputs for every lifecycle phase.
"""

from __future__ import annotations

import logging

from deepresearch.agents.base_agent import BaseAgent
from deepresearch.llm.client import LLMClient, LLMError
from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchTopic,
    SharedKnowledge,
)
from deepresearch.utils.prompts import (
    build_agent_system_prompt,
    build_clarify_prompt,
    build_refine_prompt,
    build_review_prompt,
    build_round_1_prompt,
    build_round_2_prompt,
    build_round_n_prompt,
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
  ],
  "target_agent_ids": ["agent-id-or-null", "agent-id-or-null"]
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
        """Initial research pass — with web search capability.

        At most 2 LLM calls total: one with tools, one fallback without.
        """
        await self._log_agent_state("researching")
        user_prompt = build_round_1_prompt(topic.question, topic.time_budget)
        user_prompt += _ROUND_1_FORMAT

        from deepresearch.tools.web_search import WEB_SEARCH_TOOL

        response = None
        try:
            response = await self.llm.generate_with_tools(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                tools=[WEB_SEARCH_TOOL],
                temperature=self.profile.temperature,
                max_tokens=getattr(self.llm, 'max_tokens', None) or 4096,
            )
        except LLMError:
            logger.warning(
                "LLM call with tools failed for agent '%s', retrying without tools",
                self.profile.id,
            )

        # One fallback without tools if first attempt failed or produced empty
        if response is None:
            try:
                response = await self._generate_with_retry(user_prompt)
            except LLMError:
                logger.error(
                    "All LLM attempts failed for agent '%s'", self.profile.id
                )
                response = ""

        data = self._try_parse_json(response, "research_round_1")

        # One retry for empty/invalid JSON (max 2 total LLM calls per round)
        if not data.get("summary") and not data.get("key_points"):
            logger.warning(
                "Empty/invalid response from agent '%s', retrying once",
                self.profile.id,
            )
            try:
                response2 = await self._generate_with_retry(user_prompt)
                data2 = self._try_parse_json(response2, "research_round_1")
                if data2.get("summary") or data2.get("key_points"):
                    data = data2
            except LLMError:
                logger.error(
                    "Retry also failed for agent '%s'", self.profile.id
                )

        return Findings(
            agent_id=self.profile.id,
            round=1,
            summary=data.get("summary", ""),
            key_points=data.get("key_points", []),
            perspective=data.get("perspective", ""),
            confidence=float(data.get("confidence", 0.5)),
            raw_response=response or "",
        )

    async def review_findings(
        self,
        shared: SharedKnowledge,
        agent_ids: list[str] | None = None,
    ) -> FollowUpQuestions:
        """Review aggregated shared knowledge and pose follow-up questions.

        Args:
            shared: Shared knowledge from all agents.
            agent_ids: List of active agent IDs — used to instruct the
                LLM which agents are available to target.
        """
        await self._log_agent_state("questioning")
        user_prompt = build_review_prompt(shared)
        if agent_ids:
            user_prompt += (
                "\n\n## Available Agents\n"
                "You may direct questions to specific agents by their ID. "
                "Use null for questions that apply to all agents.\n"
                f"Agent IDs: {agent_ids}\n"
            )
        user_prompt += _REVIEW_FORMAT
        response = await self._generate_with_retry(user_prompt)
        data = self._try_parse_json(response, "review_findings")
        return FollowUpQuestions(
            agent_id=self.profile.id,
            questions=data.get("questions", []),
            target_agent_ids=data.get("target_agent_ids"),
        )

    async def refine_findings(
        self,
        questions: FollowUpQuestions,
        current_findings: Findings | None = None,
    ) -> Findings:
        """Refine findings based on follow-up questions from other agents.

        Uses web search to answer the questions and produce updated findings.
        If refinement fails or produces empty results, the current findings
        are returned unchanged.
        """
        if not questions.questions:
            return current_findings or Findings(
                agent_id=self.profile.id,
                round=1,
                summary="",
                key_points=[],
                perspective="",
            )

        await self._log_agent_state("refining")
        user_prompt = build_refine_prompt(
            questions=questions.questions,
            current_summary=(current_findings.summary if current_findings else ""),
            current_key_points=(
                current_findings.key_points if current_findings else []
            ),
        )
        user_prompt += _ROUND_1_FORMAT

        from deepresearch.tools.web_search import WEB_SEARCH_TOOL

        try:
            response = await self.llm.generate_with_tools(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                tools=[WEB_SEARCH_TOOL],
                temperature=self.profile.temperature,
                max_tokens=getattr(self.llm, 'max_tokens', None) or 4096,
            )
        except LLMError:
            logger.warning(
                "LLM call with tools failed for refinement of agent '%s', retrying without tools",
                self.profile.id,
            )
            response = await self._generate_with_retry(user_prompt)

        data = self._try_parse_json(response, "refine_findings")
        if not data.get("summary") and not data.get("key_points"):
            logger.warning(
                "Empty refinement response from agent '%s', keeping current findings",
                self.profile.id,
            )
            return current_findings or Findings(
                agent_id=self.profile.id,
                round=1,
                summary="",
                key_points=[],
                perspective="",
            )

        return Findings(
            agent_id=self.profile.id,
            round=1,
            summary=data.get(
                "summary", current_findings.summary if current_findings else ""
            ),
            key_points=data.get(
                "key_points", current_findings.key_points if current_findings else []
            ),
            perspective=data.get(
                "perspective", current_findings.perspective if current_findings else ""
            ),
            confidence=float(
                data.get(
                    "confidence",
                    current_findings.confidence if current_findings else 0.5,
                )
            ),
            raw_response=response,
        )

    async def research_round_2(
        self,
        topic: ResearchTopic,
        shared: SharedKnowledge,
        questions: FollowUpQuestions,
    ) -> Findings:
        """Deeper research after seeing shared context and follow-up questions."""
        await self._log_agent_state("researching")
        user_prompt = build_round_2_prompt(topic.question, shared, questions.questions)
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

    async def research_round_n(
        self,
        topic: ResearchTopic,
        shared: SharedKnowledge,
        round_num: int,
        prev_findings: Findings,
    ) -> Findings:
        """Research round for N >= 3 with anti-repetition prompting."""
        await self._log_agent_state("researching")
        max_rounds = getattr(topic, "max_rounds", 4)
        user_prompt = build_round_n_prompt(
            topic.question, shared, round_num, max_rounds, prev_findings
        )
        user_prompt += _ROUND_2_FORMAT

        from deepresearch.tools.web_search import WEB_SEARCH_TOOL

        try:
            response = await self.llm.generate_with_tools(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                tools=[WEB_SEARCH_TOOL],
                temperature=self.profile.temperature,
                max_tokens=getattr(self.llm, 'max_tokens', None) or 4096,
            )
        except LLMError:
            logger.warning(
                "LLM call with tools failed for agent '%s' round %d, retrying without tools",
                self.profile.id,
                round_num,
            )
            response = await self._generate_with_retry(user_prompt)

        data = self._try_parse_json(response, f"research_round_{round_num}")
        if not data.get("summary") and not data.get("key_points"):
            logger.warning(
                "Empty response from agent '%s' in round %d, keeping previous findings",
                self.profile.id,
                round_num,
            )
            return Findings(
                agent_id=self.profile.id,
                round=round_num,
                summary=prev_findings.summary,
                key_points=prev_findings.key_points,
                perspective=prev_findings.perspective,
                confidence=prev_findings.confidence,
                raw_response=prev_findings.raw_response,
            )
        return Findings(
            agent_id=self.profile.id,
            round=round_num,
            summary=data.get("summary", prev_findings.summary),
            key_points=data.get("key_points", prev_findings.key_points),
            perspective=data.get("perspective", prev_findings.perspective),
            confidence=float(data.get("confidence", prev_findings.confidence)),
            raw_response=response,
        )

    async def write_report(
        self, round_1: Findings, round_2: Findings | None
    ) -> IndividualReport:
        """Consolidate findings into a final individual report."""
        await self._log_agent_state("writing")
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
        """Answer a clarification question from the scribe — with web search."""
        await self._log_agent_state("answering")
        user_prompt = build_clarify_prompt(query.question)
        user_prompt += _CLARIFY_FORMAT
        user_prompt += "\nUse the web_search tool if you need up-to-date information."
        try:
            from deepresearch.tools.web_search import WEB_SEARCH_TOOL

            response = await self.llm.generate_with_tools(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                tools=[WEB_SEARCH_TOOL],
                temperature=self.profile.temperature,
                max_tokens=getattr(self.llm, 'max_tokens', None) or 2048,
            )
        except LLMError:
            logger.warning(
                "Clarify with tools failed for '%s', retrying without", self.profile.id
            )
            response = await self._generate_with_retry(user_prompt)
        data = self._try_parse_json(response, "clarify")
        return ClarificationResponse(
            agent_id=self.profile.id,
            response=data.get("response", ""),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _log_agent_state(self, state: str) -> None:
        """Send an agent state update through the LLM client's event callback."""
        if self.llm and hasattr(self.llm, "event_callback") and self.llm.event_callback:
            try:
                await self.llm.event_callback({"type": "agent_state", "state": state})
            except Exception:
                pass  # Fire-and-forget — don't disrupt the agent.

    async def _generate_with_retry(self, user_prompt: str) -> str:
        """Call the LLM, retrying **once** on failure with a stricter instruction."""
        try:
            return await self.llm.generate_stream(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                temperature=self.profile.temperature,
                max_tokens=getattr(self.llm, 'max_tokens', None),
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
                max_tokens=getattr(self.llm, 'max_tokens', None),
            )
