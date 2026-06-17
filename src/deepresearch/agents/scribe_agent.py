"""ScribeAgent — neutral academic compiler for DeepeResearch.

The scribe receives individual reports from all research agents and
synthesises them into a coherent, well-structured research paper.
It operates at a lower temperature (0.3) and uses a neutral persona
to ensure balanced coverage of all agent perspectives.

The scribe also supports a **clarification protocol**: when it encounters
ambiguous, contradictory, or insufficiently supported claims, it can ask
specific agents for clarification before finalising the paper.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine

from deepresearch.agents.base_agent import BaseAgent
from deepresearch.llm.client import LLMClient, LLMError
from deepresearch.models import (
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchPaper,
    ResearchTopic,
    SharedKnowledge,
)

logger = logging.getLogger(__name__)

# Maximum clarification rounds per agent per session.
_MAX_CLARIFICATION_ROUNDS = 5

_SCRIBE_SYSTEM_PROMPT = (
    "You are a professional research editor and synthesiser. Your role is to "
    "compile individual agent reports into a coherent, well-structured research "
    "paper. You maintain a neutral, academic tone and give fair weight to all "
    "perspectives presented by the agents.\n\n"
    "Structure the paper with:\n"
    "- A clear title reflecting the research topic\n"
    "- An abstract summarising key findings across all perspectives\n"
    "- A methodology note explaining the multi-agent approach\n"
    '- Per-agent sections titled exactly with each agent\'s real ID and name (e.g., "Curious Teen", not invented titles). Do NOT rename agents or create new perspective names.\n'
    "- A synthesis section connecting the perspectives and identifying "
    "themes, agreements, and disagreements\n"
    "- Key takeaways\n"
    "- A conclusion\n"
    "- Appendices for detailed analyses when appropriate"
)

_COMPILE_FORMAT = """
Respond with valid JSON **only** — no markdown fences, no explanation:
{
  "title": "Research Paper Title",
  "abstract": "Comprehensive abstract synthesising all perspectives.",
  "methodology_note": "Multi-agent research methodology description.",
  "sections": [
    {
      "heading": "Introduction",
      "source_agent_id": null,
      "content": "Section content here.",
      "subsections": []
    },
    {
      "heading": "Curious Teen Perspective",
      "source_agent_id": "curious-teen",
      "content": "Content from the curious teen agent's findings.",
      "subsections": [
        {
          "heading": "Subsection",
          "source_agent_id": "curious-teen",
          "content": "Subsection content.",
          "subsections": []
        }
      ]
    }
  ],
  "synthesis": "Cross-cutting synthesis connecting all perspectives.",
  "key_takeaways": ["Takeaway 1", "Takeaway 2", "Takeaway 3"],
  "conclusion": "Final conclusion drawing everything together.",
  "appendices": []
}
"""

_CLARIFY_FORMAT = """
Respond with valid JSON **only**:
{
  "response": "Clear, concise answer about compilation decisions."
}
"""


class ScribeAgent(BaseAgent):
    """Neutral academic compiler that produces the final research paper.

    Unlike ResearchAgent instances the scribe does **not** have a personality
    profile — it uses a fixed neutral prompt at temperature 0.3.
    The five BaseAgent lifecycle methods that do not apply to the scribe
    raise ``NotImplementedError``.
    """

    def __init__(self, llm_client: LLMClient) -> None:
        # The scribe has no personality profile.
        super().__init__(profile=None, llm_client=llm_client)
        self._system_prompt: str = _SCRIBE_SYSTEM_PROMPT

    # ------------------------------------------------------------------
    # Scribe-specific API
    # ------------------------------------------------------------------

    async def compile(
        self,
        reports: dict[str, IndividualReport],
        topic: str = "",
        clarification_fn: Callable[
            [ClarificationQuery], Coroutine[Any, Any, ClarificationResponse]
        ]
        | None = None,  # noqa: E501
        status_callback: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        language: str = "English",
    ) -> ResearchPaper:
        """Synthesise all agent reports into a final research paper.

        The scribe first compiles the paper from the reports, then
        optionally executes a clarification protocol if a
        ``clarification_fn`` is provided. The clarifications are
        identified by prompting the LLM to flag ambiguous or
        contradictory claims.

        Args:
            reports: Mapping of ``agent_id → IndividualReport`` from every
                agent that completed the research lifecycle.
            topic: The original research topic string, used to keep the paper focused
                during clarification revisions.
            clarification_fn: An async callable that accepts a
                ``ClarificationQuery`` and returns a
                ``ClarificationResponse``. Typically wired to the
                orchestrator's ``_handle_clarification``.
            status_callback: Optional async callable that receives status
                update strings (e.g. "identifying_claims", "asking_agent",
                "recompiling") during the clarification protocol.
            language: The language for the final paper (default "English").

        Returns:
            A structured ``ResearchPaper`` with all required sections.
        """
        logger.info(
            "Scribe compile starting — %d reports, ~%d chars, language=%s",
            len(reports),
            sum(len(str(r)) for r in reports.values()),
            language,
        )
        reports_text = self._format_reports(reports)
        logger.debug("Scribe formatted reports: %d chars in prompt", len(reports_text))
        agent_names = list(reports.keys())

        # Build system prompt with language instruction.
        system_prompt = self._system_prompt
        if language and language.lower() != "english":
            system_prompt += (
                f"\n\n**IMPORTANT: Compile the ENTIRE paper in {language}.** "
                f"All section headings, abstract, synthesis, key takeaways, "
                f"conclusion, and content must be written in {language}. "
                f"Agent names may remain in their original form."
            )

        user_prompt = (
            "# Compile Research Paper\n\n"
            f"The following are individual reports from {len(reports)} "
            f"research agents. Synthesise them into a coherent paper.\n\n"
            f"**IMPORTANT: Use EXACTLY these agent names for section headings: {agent_names}**\n"
            f"**Do NOT invent new agent names, titles, or perspective names.**\n\n"
            f"{reports_text}\n\n"
            "Use the EXACT agent names from the reports above. "
            "Each agent section must be titled with the agent's real name. "
            "Highlight areas of agreement and disagreement."
        )
        user_prompt += _COMPILE_FORMAT

        try:
            logger.debug(
                "Scribe calling LLM (stream=%s)...",
                hasattr(self.llm, "generate_stream"),
            )
            response = await self.llm.generate_stream(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
            )
            logger.debug("Scribe LLM response received: %d chars", len(response))
        except LLMError as exc:
            logger.error(
                "Scribe LLM call failed, returning minimal paper. Error: %s", exc
            )
            return self._fallback_paper(reports)
        except Exception as e:
            logger.error(
                "Scribe compilation failed: %s", str(e)[:500], exc_info=True
            )
            return self._fallback_paper(reports)

        data = self._try_parse_json(response, "compile")

        paper = ResearchPaper(
            title=data.get("title", "Research Paper"),
            abstract=data.get("abstract", ""),
            methodology_note=data.get("methodology_note", ""),
            sections=self._parse_sections(data.get("sections", [])),
            synthesis=data.get("synthesis", ""),
            key_takeaways=data.get("key_takeaways", []),
            conclusion=data.get("conclusion", ""),
            appendices=self._parse_sections(data.get("appendices", [])),
        )

        # ── Retry if compilation returned empty ─────────────────────
        if paper is None or not paper.sections:
            logger.warning("Scribe compilation returned empty, retrying once...")
            try:
                response = await self.llm.generate_stream(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.3,
                )
                data = self._try_parse_json(response, "compile_retry")
                paper = ResearchPaper(
                    title=data.get("title", "Research Paper"),
                    abstract=data.get("abstract", ""),
                    methodology_note=data.get("methodology_note", ""),
                    sections=self._parse_sections(data.get("sections", [])),
                    synthesis=data.get("synthesis", ""),
                    key_takeaways=data.get("key_takeaways", []),
                    conclusion=data.get("conclusion", ""),
                    appendices=self._parse_sections(data.get("appendices", [])),
                )
            except Exception as e:
                logger.error(
                    "Scribe compilation failed on retry: %s", str(e)[:500], exc_info=True
                )

            if paper is None or not paper.sections:
                logger.warning("Scribe compilation failed after retry, using fallback")
                return ResearchPaper(
                    title="Research Paper",
                    abstract="Compilation failed — partial results available.",
                    methodology_note="",
                    sections=[],
                    synthesis="",
                    key_takeaways=[],
                    conclusion="",
                )

        # ── Clarification Protocol ──────────────────────────────────
        if clarification_fn is not None:
            paper = await self._run_clarification_protocol(
                paper, reports, clarification_fn, status_callback, topic, language
            )

        return paper

    async def _run_clarification_protocol(
        self,
        paper: ResearchPaper,
        reports: dict[str, IndividualReport],
        clarification_fn: Callable[
            [ClarificationQuery], Coroutine[Any, Any, ClarificationResponse]
        ],
        status_callback: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        topic: str = "",
        language: str = "English",
    ) -> ResearchPaper:
        """Run the clarification protocol on a draft paper.

        The scribe reviews the paper for ambiguous or contradictory
        claims, formulates clarification questions to specific agents,
        and incorporates the responses (up to
        ``_MAX_CLARIFICATION_ROUNDS`` per agent).

        Clarification requests to agents are fired concurrently — the
        scribe identifies claims one by one (fast LLM call), fires agent
        clarifications as background tasks, and only waits + recompiles
        at the end.  This allows multiple agents to be clarified in
        parallel.
        """
        clarifications_per_agent: dict[str, int] = {}
        total_rounds = 0
        # Hard cap total rounds to prevent runaway clarification loops.
        max_total_rounds = min(_MAX_CLARIFICATION_ROUNDS * len(reports), 5)
        _asked_claims: set[str] = set()
        # Track per-agent clarification count to prevent same-agent repetition
        _asked_agents: dict[str, int] = {}
        _consecutive_empties = 0

        # Time budget: stop clarification after 3 minutes.
        start_time = time.monotonic()
        max_clarification_seconds = 180  # 3 minutes

        # (claim, agent_id, task) tuples for concurrent clarification.
        pending: list[tuple[str, str, asyncio.Task[str | None]]] = []

        while total_rounds < max_total_rounds:
            # Time budget check.
            elapsed = time.monotonic() - start_time
            if elapsed > max_clarification_seconds:
                logger.info(
                    "Clarification time budget exceeded (%.0fs), stopping",
                    elapsed,
                )
                break

            # Ask the scribe LLM to identify claims needing clarification.
            if status_callback:
                await status_callback("identifying_claims")
            try:
                query_data = await self._identify_clarification_needs(
                    paper, reports, clarifications_per_agent, _asked_claims, topic
                )
            except LLMError as _exc:
                logger.warning(
                    "Clarification identification failed (LLM error), stopping: %s",
                    _exc,
                )
                break
            except Exception as _exc:
                logger.warning(
                    "Clarification identification failed, stopping: %s", _exc
                )
                break
            if query_data is None:
                break  # No more clarifications needed.

            agent_id = query_data.get("agent_id", "")
            claim = query_data.get("claim", "")
            context = query_data.get("context", "")

            if not agent_id or not claim:
                break

            # Skip claims already asked about.
            if claim in _asked_claims:
                logger.warning("Claim already asked, skipping: %s", claim[:60])
                continue
            _asked_claims.add(claim)

            # Enforce per-agent round limit.
            if clarifications_per_agent.get(agent_id, 0) >= _MAX_CLARIFICATION_ROUNDS:
                logger.info(
                    "Max clarifications reached for agent '%s' — skipping",
                    agent_id,
                )
                continue

            # Skip if agent already answered > 2 clarifications.
            if agent_id in _asked_agents and _asked_agents[agent_id] >= 2:
                logger.info(
                    "Agent '%s' already answered 2 clarifications, skipping",
                    agent_id,
                )
                continue

            # Fire clarification as a concurrent task (don't await).
            if status_callback:
                await status_callback(f"asking_agent:{agent_id}")
            task = asyncio.create_task(
                self._clarify_claim(claim, agent_id, context, clarification_fn)
            )
            pending.append((claim, agent_id, task))
            total_rounds += 1

        # Collect all non-empty clarifications first.
        collected: list[tuple[str, str, str]] = []  # (agent_id, claim, response)
        for claim, agent_id, task in pending:
            try:
                response = await task
            except Exception as _exc:
                logger.warning(
                    "Clarification from agent '%s' failed: %s", agent_id, _exc
                )
                continue

            # Skip empty responses.
            if response is None or not response.strip():
                _consecutive_empties += 1
                logger.info(
                    "Empty clarification from '%s' (%d consecutive), skipping recompilation",
                    agent_id,
                    _consecutive_empties,
                )
                if _consecutive_empties >= 3:
                    logger.info("3 consecutive empty clarifications, stopping protocol")
                    break
                continue

            _consecutive_empties = 0  # Reset on successful response.

            # Track per-agent clarification count to prevent same-agent repetition.
            _asked_agents[agent_id] = _asked_agents.get(agent_id, 0) + 1

            clarifications_per_agent[agent_id] = (
                clarifications_per_agent.get(agent_id, 0) + 1
            )
            collected.append((agent_id, claim, response))

        # Single recompile with all collected clarifications.
        if collected:
            if status_callback:
                await status_callback("recompiling")
            try:
                paper = await self._recompile_all_clarifications(
                    paper, collected, topic, language
                )
            except LLMError as _exc:
                logger.warning(
                    "Recompilation with clarifications failed, stopping: %s", _exc
                )

        # Log why the clarification protocol stopped.
        elapsed = time.monotonic() - start_time
        logger.info(
            "Clarification protocol stopped: total_rounds=%d/%d, time_budget=%.0fs, "
            "consecutive_empties=%d, asked_claims=%d",
            total_rounds,
            max_total_rounds,
            elapsed,
            _consecutive_empties,
            len(_asked_claims),
        )

        return paper

    async def _identify_clarification_needs(
        self,
        paper: ResearchPaper,
        reports: dict[str, IndividualReport],
        clarifications_per_agent: dict[str, int],
        asked_claims: set[str] | None = None,
        topic: str = "",
    ) -> dict[str, str] | None:
        """Ask the scribe LLM to identify claims needing clarification.

        Returns a dict with ``agent_id``, ``claim``, and ``context``,
        or ``None`` if no further clarifications are needed.

        Args:
            paper: The current draft paper.
            reports: Original agent reports.
            clarifications_per_agent: Tracks per-agent clarification round counts.
            asked_claims: Set of claims already clarified — the LLM is told
                not to ask about these again.
        """
        max_per_agent = _MAX_CLARIFICATION_ROUNDS

        clarification_status = (
            "Current clarification rounds per agent:\n"
            + "\n".join(
                f"  - {aid}: {count}/{max_per_agent}"
                for aid, count in clarifications_per_agent.items()
            )
            or "  (none yet)"
        )

        prompt = (
            "# Identify Clarification Needs\n\n"
            "Review the compiled paper and the original agent reports below. "
            "Identify the **single most important** claim that is ambiguous, "
            "contradictory across agents, or lacks sufficient support, and "
            "that has NOT already been clarified.\n\n"
        )
        if topic:
            prompt += (
                f"**IMPORTANT: This paper is about '{topic}'. ALL content — including "
                f"clarifications — must stay strictly on-topic. Do NOT include discussions "
                f"about methodology, tools, or how agents found information. Only flag "
                f"claims directly relevant to the research topic.**\n\n"
            )
        prompt += (
            "If all claims are sufficiently clear and supported, respond "
            'with: {"needs_clarification": false}\n\n'
            "Otherwise respond with:\n"
            "{\n"
            '  "needs_clarification": true,\n'
            '  "agent_id": "id-of-agent-to-ask",\n'
            '  "claim": "The specific claim that needs clarification",\n'
            '  "context": "Why this needs clarification"\n'
            "}\n\n"
            f"{clarification_status}\n\n"
        )

        # Add already-asked claims so the LLM doesn't repeat them.
        if asked_claims:
            prompt += "\nAlready clarified claims:\n"
            for c in asked_claims:
                prompt += f"- {c}\n"
            prompt += "\nDo NOT ask about any of these again.\n\n"

        prompt += (
            f"## Compiled Paper (Draft)\n"
            f"Title: {paper.title}\n"
            f"Abstract: {paper.abstract}\n"
            f"Synthesis: {paper.synthesis}\n"
            f"Conclusion: {paper.conclusion}\n\n"
            "## Agent Reports\n"
            f"{self._format_reports(reports)}\n"
        )
        prompt += """
Respond with valid JSON **only** — no markdown fences, no explanation.
"""

        try:
            response = await self.llm.generate_stream(
                system_prompt=self._system_prompt,
                user_prompt=prompt,
                temperature=0.3,
            )
        except LLMError:
            logger.warning("Clarification identification LLM call failed")
            return None

        data = self._try_parse_json(response, "clarify_identify")
        if not data.get("needs_clarification"):
            return None

        return {
            "agent_id": data.get("agent_id", ""),
            "claim": data.get("claim", ""),
            "context": data.get("context", ""),
        }

    async def _clarify_claim(
        self,
        claim: str,
        agent_id: str,
        context: str,
        clarification_fn: Callable[
            [ClarificationQuery], Coroutine[Any, Any, ClarificationResponse]
        ],
    ) -> str | None:
        """Ask an agent for clarification on a specific claim.

        Args:
            claim: The specific claim that needs clarification.
            agent_id: The agent to ask.
            context: Context explaining why clarification is needed.
            clarification_fn: The async callable that routes the query.

        Returns:
            The agent's response string, or ``None`` on failure.
        """
        query = ClarificationQuery(
            agent_id=agent_id,
            question=f'Regarding the claim: "{claim}"\n\n'
            f"Context: {context}\n\n"
            f"Please clarify or provide more detail.",
            context=context,
        )
        try:
            response = await clarification_fn(query)
            return response.response
        except Exception as exc:
            logger.warning(
                "Clarification request to agent '%s' failed: %s",
                agent_id,
                exc,
            )
            return None

    async def _recompile_with_clarification(
        self,
        paper: ResearchPaper,
        agent_id: str,
        claim: str,
        clarification: str,
        topic: str = "",
        language: str = "English",
    ) -> ResearchPaper:
        """Re-compile the paper incorporating a clarification response.

        Sends the existing draft plus the new clarification back to the
        LLM for a targeted revision.

        Args:
            paper: Current draft paper.
            agent_id: Agent that provided clarification.
            claim: The claim being clarified.
            clarification: The clarification response.
            topic: Original research topic to keep revisions focused.
            language: Language for the paper (default "English").
        """
        # Build language-aware system prompt.
        system_prompt = self._system_prompt
        if language and language.lower() != "english":
            system_prompt += (
                f"\n\n**IMPORTANT: The ENTIRE paper must be written in {language}.** "
                f"All section headings, abstract, synthesis, key takeaways, "
                f"conclusion, and content must be written in {language}. "
                f"Agent names may remain in their original form."
            )

        prompt = (
            "# Revise Paper with Clarification\n\n"
            "Below is the current draft paper and a clarification "
            "received from one of the research agents. Revise the paper "
            "to incorporate this new information where relevant.\n\n"
        )
        if topic:
            prompt += (
                f"**REMEMBER: This paper is about '{topic}'. ALL content — including "
                f"clarifications — must stay strictly on-topic. Do NOT include discussions "
                f"about methodology, tools, or how agents found information. Only include "
                f"findings relevant to the research topic. Do NOT shift focus to the "
                f"clarification process itself.**\n\n"
            )
        prompt += (
            f"## Clarification From Agent '{agent_id}'\n"
            f'On claim: "{claim}"\n'
            f'Response: "{clarification}"\n\n'
            f"## Current Draft\n"
            f"Title: {paper.title}\n"
            f"Abstract: {paper.abstract}\n"
            f"Synthesis: {paper.synthesis}\n"
            f"Conclusion: {paper.conclusion}\n"
        )
        prompt += _COMPILE_FORMAT

        try:
            response = await self.llm.generate_stream(
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.3,
            )
        except LLMError:
            logger.warning(
                "Re-compilation after clarification failed — keeping current draft"
            )
            return paper

        data = self._try_parse_json(response, "recompile")
        if not data:
            return paper

        return ResearchPaper(
            title=data.get("title", paper.title),
            abstract=data.get("abstract", paper.abstract),
            methodology_note=data.get("methodology_note", paper.methodology_note),
            sections=self._parse_sections(
                data.get("sections", [s.model_dump() for s in paper.sections])
            ),
            synthesis=data.get("synthesis", paper.synthesis),
            key_takeaways=data.get("key_takeaways", paper.key_takeaways),
            conclusion=data.get("conclusion", paper.conclusion),
            appendices=self._parse_sections(
                data.get("appendices", [s.model_dump() for s in paper.appendices])
            ),
        )

    async def _recompile_all_clarifications(
        self,
        paper: ResearchPaper,
        collected: list[tuple[str, str, str]],  # (agent_id, claim, response)
        topic: str = "",
        language: str = "English",
    ) -> ResearchPaper:
        """Re-compile the paper incorporating multiple clarification responses.

        Sends the existing draft plus all collected clarifications back to the
        LLM for a single targeted revision.
        """
        # Build language-aware system prompt.
        system_prompt = self._system_prompt
        if language and language.lower() != "english":
            system_prompt += (
                f"\n\n**IMPORTANT: The ENTIRE paper must be written in {language}.** "
                f"All section headings, abstract, synthesis, key takeaways, "
                f"conclusion, and content must be written in {language}. "
                f"Agent names may remain in their original form."
            )

        prompt = (
            "# Revise Paper with Clarifications\n\n"
            "Below is the current draft paper and multiple clarifications "
            "received from research agents. Revise the paper "
            "to incorporate all this new information where relevant.\n\n"
        )
        if topic:
            prompt += (
                f"**REMEMBER: This paper is about '{topic}'. ALL content — including "
                f"clarifications — must stay strictly on-topic. Do NOT include discussions "
                f"about methodology, tools, or how agents found information. Only include "
                f"findings relevant to the research topic. Do NOT shift focus to the "
                f"clarification process itself.**\n\n"
            )
        prompt += "## Clarifications\n\n"
        for agent_id, claim, response in collected:
            prompt += f"### Agent '{agent_id}' on claim: \"{claim}\"\n"
            prompt += f"Response: \"{response}\"\n\n"
        prompt += (
            f"## Current Draft\n"
            f"Title: {paper.title}\n"
            f"Abstract: {paper.abstract}\n"
            f"Synthesis: {paper.synthesis}\n"
            f"Conclusion: {paper.conclusion}\n"
        )
        prompt += _COMPILE_FORMAT

        try:
            response = await self.llm.generate_stream(
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.3,
            )
        except LLMError:
            logger.warning(
                "Re-compilation after clarifications failed — keeping current draft"
            )
            return paper

        data = self._try_parse_json(response, "recompile_all")
        if not data:
            return paper

        return ResearchPaper(
            title=data.get("title", paper.title),
            abstract=data.get("abstract", paper.abstract),
            methodology_note=data.get("methodology_note", paper.methodology_note),
            sections=self._parse_sections(
                data.get("sections", [s.model_dump() for s in paper.sections])
            ),
            synthesis=data.get("synthesis", paper.synthesis),
            key_takeaways=data.get("key_takeaways", paper.key_takeaways),
            conclusion=data.get("conclusion", paper.conclusion),
            appendices=self._parse_sections(
                data.get("appendices", [s.model_dump() for s in paper.appendices])
            ),
        )

    # ------------------------------------------------------------------
    # Unused abstract methods (scribe is not a research agent)
    # ------------------------------------------------------------------

    async def research_round_1(self, topic: ResearchTopic) -> Findings:
        raise NotImplementedError("ScribeAgent does not perform research rounds.")

    async def review_findings(self, shared: SharedKnowledge) -> FollowUpQuestions:
        raise NotImplementedError("ScribeAgent does not review findings.")

    async def research_round_2(
        self,
        topic: ResearchTopic,
        shared: SharedKnowledge,
        questions: FollowUpQuestions,
    ) -> Findings:
        raise NotImplementedError("ScribeAgent does not perform research rounds.")

    async def research_round_n(
        self,
        topic: ResearchTopic,
        shared: SharedKnowledge,
        round_num: int,
        prev_findings: Findings,
    ) -> Findings:
        raise NotImplementedError("ScribeAgent does not perform research rounds.")

    async def write_report(
        self, round_1: Findings, round_2: Findings | None
    ) -> IndividualReport:
        raise NotImplementedError("ScribeAgent does not write individual reports.")

    async def clarify(self, query: ClarificationQuery) -> ClarificationResponse:
        """Answer questions about compilation decisions."""
        user_prompt = (
            f"The following clarification has been requested:\n\n"
            f'"{query.question}"\n\n'
            "Please provide a clear response about your compilation decision."
        )
        user_prompt += _CLARIFY_FORMAT
        try:
            response = await self.llm.generate_stream(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                temperature=0.3,
            )
        except LLMError:
            logger.warning("Scribe clarify LLM call failed")
            return ClarificationResponse(
                agent_id="scribe",
                response="Unable to answer at this time.",
            )

        data = self._try_parse_json(response, "clarify")
        return ClarificationResponse(
            agent_id="scribe",
            response=data.get("response", ""),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_reports(reports: dict[str, IndividualReport]) -> str:
        """Format agent reports into a structured text block for the prompt."""
        parts: list[str] = []
        for agent_id, report in reports.items():
            sections_text = ""
            for sec in report.sections:
                sections_text += f"\n### {sec.heading}\n{sec.content}\n"
            parts.append(
                f"---\n"
                f"## Report from: {agent_id}\n"
                f"**Title:** {report.title}\n"
                f"**Perspective Summary:** {report.perspective_summary}\n"
                f"**Key Insights:**\n"
                + "\n".join(f"- {i}" for i in report.key_insights)
                + f"\n**Analysis:**\n{report.analysis}\n"
                + (
                    "\n**Open Questions:**\n"
                    + "\n".join(f"- {q}" for q in report.open_questions)
                    if report.open_questions
                    else ""
                )
                + sections_text
            )
        return "\n".join(parts)

    @staticmethod
    def _fallback_paper(reports: dict[str, IndividualReport]) -> ResearchPaper:
        """Return a minimal paper when the LLM call fails entirely."""
        return ResearchPaper(
            title="Research Paper",
            abstract=f"Synthesis of {len(reports)} agent perspectives "
            f"(scribe compilation fell back to minimal output).",
            methodology_note="Multi-agent collaborative research methodology.",
            sections=[],
            synthesis="Scribe compilation encountered an error — "
            "partial results are available in individual reports.",
            key_takeaways=[f"Analysis from {len(reports)} research agents."],
            conclusion="Compilation incomplete due to scribe error.",
        )
