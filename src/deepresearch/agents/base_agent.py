"""Abstract base class for all DeepeResearch agents.

Defines the contract that research agents and the scribe agent must fulfill
across the research lifecycle.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from deepresearch.llm.client import LLMClient
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
