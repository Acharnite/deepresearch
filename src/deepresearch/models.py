"""Pydantic v2 data models for DeepResearch."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SourceReference(BaseModel):
    """A web source used during research."""

    url: str
    title: str = ""
    snippet: str = ""
    accessed_at: str = ""
    engine: str = ""  # "google", "arxiv", etc.


class ResearchTopic(BaseModel):
    """The research topic and configuration provided by the user."""

    question: str
    time_budget: str = "medium"  # "quick" | "medium" | "deep" | "custom"
    model_mode: Literal["same", "random", "manual"] = "same"


class AgentProfile(BaseModel):
    """Definition of an agent's personality and methodology."""

    id: str
    name: str
    emoji: str
    persona_prompt: str
    methodology: str
    knowledge_base: str
    bias_mitigation: str
    voice: str
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class Findings(BaseModel):
    """Results from a single agent's research round."""

    agent_id: str
    round: int
    summary: str
    key_points: list[str]
    perspective: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    raw_response: str | None = None
    sources: list[SourceReference] = []


class SharedKnowledge(BaseModel):
    """Aggregated knowledge shared with all agents after a round."""

    round_number: int
    all_summaries: dict[str, str]  # agent_id -> summary
    key_themes: list[str]
    areas_of_agreement: list[str]
    areas_of_disagreement: list[str]
    knowledge_gaps: list[str]
    round_history: list = []  # list[SharedKnowledge] snapshots from prior rounds


class FollowUpQuestions(BaseModel):
    """Questions an agent wants to explore further.

    ``target_agent_ids`` is an optional parallel list — one entry per
    question.  When set, a question is only sent to the specified agent.
    When ``None`` or when a particular entry is ``None``, the question
    goes to all agents (backward-compatible default).
    """

    agent_id: str
    questions: list[str]
    target_agent_ids: list[str | None] | None = None


class PaperSection(BaseModel):
    """A section within a research paper."""

    heading: str
    source_agent_id: str | None = None
    content: str
    subsections: list["PaperSection"] = []


class IndividualReport(BaseModel):
    """A single agent's final report after research rounds."""

    agent_id: str
    title: str
    perspective_summary: str
    key_insights: list[str]
    analysis: str
    metaphors_or_analogies: list[str] = []
    open_questions: list[str] = []
    full_text: str
    sections: list[PaperSection] = []
    sources: list[SourceReference] = []


class ClarificationQuery(BaseModel):
    """A question from the scribe to an agent."""

    agent_id: str
    question: str
    context: str | None = None


class ClarificationResponse(BaseModel):
    """An agent's response to a clarification query."""

    agent_id: str
    response: str


class ResearchPaper(BaseModel):
    """The final compiled research paper."""

    title: str
    abstract: str
    methodology_note: str
    sections: list[PaperSection]
    synthesis: str
    key_takeaways: list[str]
    conclusion: str
    appendices: list[PaperSection] = []
    references: list[SourceReference] = []
    generated_at: datetime = Field(default_factory=datetime.now)


class SessionConfig(BaseModel):
    """Complete session configuration for a research run."""

    topic: ResearchTopic
    agent_profiles: list[AgentProfile]
    agent_models: dict[str, str]  # agent_id -> model_name
    time_budget_seconds: int = Field(default=30, ge=1, le=3600)  # Up to 1 hour
    max_rounds: int = Field(default=4, ge=1, le=10)  # Max research rounds
    output_language: str = "English"  # Output language for the compiled paper
