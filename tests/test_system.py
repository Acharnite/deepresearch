"""Quick system health test.

Verifies all modules import correctly and the core types are available.
"""


def test_system_health():
    """Verify all critical modules import and core types are constructable."""
    from deepresearch.models import ResearchTopic, Findings, FollowUpQuestions, ResearchPaper
    from deepresearch.orchestrator import Orchestrator
    from deepresearch.agents import BaseAgent, ResearchAgent, ScribeAgent, AgentRegistry
    from deepresearch.collaboration import CollaborationBus
    from deepresearch.llm.client import LLMClient
    from deepresearch.output.pdf_generator import PDFGenerator
    from deepresearch.tools.web_search import WEB_SEARCH_TOOL

    # Verify core types
    topic = ResearchTopic(question="Test?")
    assert topic.question == "Test?"
    assert FollowUpQuestions(agent_id="a", questions=["q?"]).agent_id == "a"
    assert Orchestrator().state == "IDLE"
    assert LLMClient().model == "gpt-4o"
    assert WEB_SEARCH_TOOL["type"] == "function"
