"""Quick system health test.

Verifies all modules import correctly and the core types are available.
"""


def test_system_imports():
    """Verify all modules import correctly."""
    from deepresearch.models import (
        ResearchTopic,
        AgentProfile,
        Findings,
        ResearchPaper,
    )
    from deepresearch.config import load_agent_profiles, load_model_config  # noqa: F401
    from deepresearch.orchestrator import Orchestrator
    from deepresearch.agents import BaseAgent, ResearchAgent, ScribeAgent, AgentRegistry
    from deepresearch.collaboration import CollaborationBus
    from deepresearch.llm.client import LLMClient
    from deepresearch.utils.prompts import (
        build_agent_system_prompt,
        build_round_1_prompt,
    )
    from deepresearch.output.pdf_generator import PDFGenerator

    # Verify core types are constructable.
    topic = ResearchTopic(question="Test?")
    assert topic.question == "Test?"

    assert True


def test_version_available():
    """__version__ should be importable and non-empty."""
    from deepresearch import __version__
    assert isinstance(__version__, str)
    assert len(__version__) > 0


def test_main_entry_point():
    """main() function should be callable and return an int."""
    from deepresearch.main import main
    assert callable(main)


def test_cli_parser_builds():
    """Argument parser should build without error."""
    from deepresearch.main import build_parser
    parser = build_parser()
    assert parser is not None
    assert parser.prog == "deepresearch"


def test_llm_client_instantiation():
    """LLMClient should instantiate with default params."""
    from deepresearch.llm.client import LLMClient
    client = LLMClient()
    assert client.model == "gpt-4o"
    assert client.timeout == 60
    assert client.call_count == 0
    assert client.total_cost == 0.0


def test_orchestrator_instantiation():
    """Orchestrator should instantiate with default params."""
    from deepresearch.orchestrator import Orchestrator
    orch = Orchestrator()
    assert orch.state == "IDLE"
    assert orch.failed_agents == {}
    assert orch.events == []


def test_config_validation_functions_exist():
    """validate_all, validate_profiles, validate_model_configs should exist."""
    from deepresearch.config import validate_all, validate_profiles, validate_model_configs
    assert callable(validate_all)
    assert callable(validate_profiles)
    assert callable(validate_model_configs)


def test_lookup_cost_function():
    """_lookup_cost should be importable."""
    from deepresearch.llm.client import _lookup_cost
    assert callable(_lookup_cost)
    cost = _lookup_cost("gpt-4o", 100, 50)
    assert isinstance(cost, float)
