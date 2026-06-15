"""Abstract base class for all DeepeResearch agents.

Defines the contract that research agents and the scribe agent must fulfill
across the research lifecycle.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

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

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base for all research agents.

    Each agent has a personality profile (optional, e.g. for the scribe)
    and an LLM client for generating responses.
    """

    def __init__(self, profile: AgentProfile | None, llm_client: LLMClient) -> None:
        self.profile = profile
        self.llm = llm_client

    @abstractmethod
    async def research_round_1(self, topic: ResearchTopic) -> Findings:
        """Initial independent research pass."""

    @abstractmethod
    async def review_findings(self, shared: SharedKnowledge) -> FollowUpQuestions:
        """Review shared knowledge and formulate follow-up questions."""

    @abstractmethod
    async def research_round_2(
        self,
        topic: ResearchTopic,
        shared: SharedKnowledge,
        questions: FollowUpQuestions,
    ) -> Findings:
        """Deeper research round with shared context and follow-up questions."""

    @abstractmethod
    async def write_report(
        self, round_1: Findings, round_2: Findings | None
    ) -> IndividualReport:
        """Produce a final consolidated individual report."""

    @abstractmethod
    async def clarify(self, query: ClarificationQuery) -> ClarificationResponse:
        """Answer a clarification question from the scribe."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _try_parse_json(self, response: str, context: str) -> dict[str, Any]:
        """Parse JSON from an LLM response, returning ``{}`` on failure."""
        try:
            return self.llm.parse_json_response(response)
        except LLMError:
            logger.warning(
                "Failed to parse JSON in %s for agent '%s', using fallback",
                context,
                getattr(self, "profile", type(self).__name__),
            )
            return {}

    @staticmethod
    def _parse_sections(raw: list[dict[str, Any]]) -> list[PaperSection]:
        """Deep-parse a list of section dicts into PaperSection objects."""
        sections: list[PaperSection] = []
        for item in raw:
            subs = [
                PaperSection(**s)
                for s in item.get("subsections", [])
                if isinstance(s, dict)
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
