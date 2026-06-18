"""Concrete ResearchAgent that fulfils the BaseAgent contract.

Each ResearchAgent wraps an LLMClient and uses prompt builders plus
YAML-based JSON format instructions to produce structured outputs
for every lifecycle phase.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from deepresearch.agents.base_agent import BaseAgent
from deepresearch.llm.client import LLMClient, LLMError
from prompts import PromptTemplate  # type: ignore[import-untyped]
from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchTopic,
    SharedKnowledge,
    SourceReference,
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
from prompts import prompts as _default_prompts

logger = logging.getLogger(__name__)


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

    def __init__(
        self,
        profile: AgentProfile,
        llm_client: LLMClient,
        prompt_tmpl: PromptTemplate | None = None,
    ) -> None:
        super().__init__(profile, llm_client)
        self._system_prompt: str = build_agent_system_prompt(profile)
        self._prompts: PromptTemplate = prompt_tmpl or _default_prompts

    # ------------------------------------------------------------------
    # Lifecycle methods
    # ------------------------------------------------------------------

    async def research_round_1(self, topic: ResearchTopic) -> Findings:
        """Initial research pass — with web search capability.

        At most 2 LLM calls total: one with tools, one fallback without.
        """
        await self._log_agent_state("researching")
        user_prompt = build_round_1_prompt(topic.question, topic.time_budget)
        user_prompt += self._prompts.get("research", "round_1_format")

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
            sources=self._extract_sources(),
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
        user_prompt += self._prompts.get("research", "review_format")
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
        user_prompt += self._prompts.get("research", "round_1_format")

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
            sources=self._extract_sources(
                getattr(current_findings, "sources", []) if current_findings else []
            ),
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
        user_prompt += self._prompts.get("research", "round_2_format")
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
        user_prompt += self._prompts.get("research", "round_2_format")

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
            sources=self._extract_sources(prev_findings.sources),
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
        user_prompt += self._prompts.get("research", "report_format")
        response = await self._generate_with_retry(user_prompt)
        data = self._try_parse_json(response, "write_report")
        sections = self._parse_sections(data.get("sections", []))

        # Collect sources from both rounds
        all_sources = list(round_1.sources) if round_1 and round_1.sources else []
        if round_2 and round_2.sources:
            seen_urls = {s.url for s in all_sources}
            for src in round_2.sources:
                if src.url not in seen_urls:
                    seen_urls.add(src.url)
                    all_sources.append(src)

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
            sources=all_sources,
        )

    async def clarify(self, query: ClarificationQuery) -> ClarificationResponse:
        """Answer a clarification question from the scribe — with web search."""
        await self._log_agent_state("answering")
        user_prompt = build_clarify_prompt(query.question)
        user_prompt += self._prompts.get("research", "clarify_format")
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

    def _extract_sources(
        self, existing: list[SourceReference] | None = None,
    ) -> list[SourceReference]:
        """Extract SourceReference objects from the last tool call results.

        Merges with any ``existing`` sources (deduplicating by URL).
        """
        existing = existing or []
        seen_urls: set[str] = {s.url for s in existing}
        now = datetime.now(timezone.utc).isoformat()
        sources = list(existing)

        if not self.llm or not hasattr(self.llm, "last_tool_results"):
            return sources

        for result in self.llm.last_tool_results:
            url = result.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append(
                    SourceReference(
                        url=url,
                        title=result.get("title", ""),
                        snippet=result.get("snippet", ""),
                        accessed_at=now,
                    )
                )
        return sources

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
