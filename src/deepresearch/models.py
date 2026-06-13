"""Pydantic v2 data models for DeepeResearch."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ResearchTopic(BaseModel):
    """The research topic and configuration provided by the user."""

    question: str
    time_budget: str = "medium"  # "quick" | "medium" | "deep" | "custom"
    model_mode: Literal["same", "random", "manual"] = "same"


class ModelConfig(BaseModel):
    """Configuration for a specific model assignment."""

    selected_model: str = "gpt-4o"
    temperature_override: float | None = None


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


class SharedKnowledge(BaseModel):
    """Aggregated knowledge shared with all agents after a round."""

    round_number: int
    all_summaries: dict[str, str]  # agent_id -> summary
    key_themes: list[str]
    areas_of_agreement: list[str]
    areas_of_disagreement: list[str]
    knowledge_gaps: list[str]


class FollowUpQuestions(BaseModel):
    """Questions an agent wants to explore further."""

    agent_id: str
    questions: list[str]


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
    generated_at: datetime = Field(default_factory=datetime.now)


class SessionConfig(BaseModel):
    """Complete session configuration for a research run."""

    topic: ResearchTopic
    agent_profiles: list[AgentProfile]
    agent_models: dict[str, str]  # agent_id -> model_name
    time_budget_seconds: int = Field(default=30, ge=1, le=3600)  # Up to 1 hour
