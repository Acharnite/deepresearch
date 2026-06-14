"""Agent Registry — factory for creating agent instances.

The ``AgentRegistry`` is the single entry-point for constructing agents and
exposing the callable that the ``Orchestrator`` uses to dispatch lifecycle
calls.

Dispatch pattern
----------------
The orchestator's ``_build_agents`` expects ``agent_factory(profile, model_name)``
to return a *single callable* that accepts different argument signatures per
lifecycle phase:

- ``(ResearchTopic,)``                          → Round 1 → ``Findings``
- ``(SharedKnowledge,)``                        → Review   → ``FollowUpQuestions``
- ``(ResearchTopic, SharedKnowledge)``           → Round 2 → ``IndividualReport``
- ``(Findings,)``                                → Report   → ``IndividualReport``

The dispatcher wraps a ``ResearchAgent``, keeping internal state (round 1
findings, follow-up questions) so it can correctly sequence calls to the
agent's abstract methods.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, Callable

from deepresearch.agents.research_agent import ResearchAgent
from deepresearch.agents.scribe_agent import ScribeAgent
from deepresearch.llm.client import LLMClient
from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    ResearchTopic,
    SharedKnowledge,
)


class AgentRegistry:
    """Creates agent instances and exposes an Orchestrator-compatible factory.

    Usage::

        registry = AgentRegistry(llm_client)
        orchestrator = Orchestrator(
            ...,
            agent_factory=registry.agent_factory,
            scribe_factory=lambda: registry.create_scribe_agent(),
        )
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self.llm = llm_client

    # ------------------------------------------------------------------
    # Agent constructors
    # ------------------------------------------------------------------

    def create_research_agent(
        self,
        profile: AgentProfile,
        model_name: str = "gpt-4o",
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ResearchAgent:
        """Create a research agent with the given personality profile.

        A new ``LLMClient`` is created per agent so each can use a
        different model as assigned by the orchestrator.

        Args:
            profile: The agent personality profile.
            model_name: The LLM model to use.
            event_callback: Async callback for streaming output chunks.
        """
        llm = LLMClient(
            model=model_name,
            timeout=self.llm.timeout,
            event_callback=event_callback,
        )
        return ResearchAgent(profile=profile, llm_client=llm)

    def create_scribe_agent(
        self,
        model_name: str | None = None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ScribeAgent:
        """Create the scribe agent (no personality profile needed).

        The scribe must process ALL agent reports in a single prompt, which
        can take 2-5 minutes (especially with GPT-4o).  We give it a
        generous 5-minute timeout to avoid fallback to the minimal paper.

        Args:
            model_name: Optional model override. If None, uses the default LLMClient.
            event_callback: Async callback for streaming output chunks.
        """
        if model_name:
            # Scribe needs longer timeout — it processes all reports in one prompt.
            llm = LLMClient(model=model_name, timeout=300, event_callback=event_callback)
            return ScribeAgent(llm_client=llm)
        return ScribeAgent(llm_client=self.llm)

    # ------------------------------------------------------------------
    # Orchestrator-compatible factory
    # ------------------------------------------------------------------

    def agent_factory(
        self,
        profile: AgentProfile,
        model_name: str,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> Callable[..., Any]:
        """Factory for the Orchestrator.

        Returns a callable that wraps a ``ResearchAgent`` and dispatches
        to the appropriate lifecycle method based on argument types.

        Args:
            profile: The agent's personality profile.
            model_name: The LLM model to use for this agent.
            event_callback: Async callback for streaming output chunks.

        Returns:
            An ``AgentFunc`` — an async callable that handles all lifecycle
            phases via type-based dispatch.
        """
        agent = self.create_research_agent(profile, model_name, event_callback)

        # Internal state tracked across lifecycle calls.
        _round_1: Findings | None = None
        _questions: FollowUpQuestions | None = None

        async def dispatch(*args: Any) -> Any:
            nonlocal _round_1, _questions

            if len(args) == 1:
                first = args[0]
                if isinstance(first, ResearchTopic):
                    # Round 1 — independent research.
                    _round_1 = await agent.research_round_1(first)
                    return _round_1

                if isinstance(first, SharedKnowledge):
                    # Follow-up questions.
                    _questions = await agent.review_findings(first)
                    return _questions

                if isinstance(first, FollowUpQuestions):
                    # Refinement phase — refine findings from follow-up questions.
                    if _round_1 is not None:
                        _round_1 = await agent.refine_findings(first, _round_1)
                    return _round_1 or Findings(
                        agent_id=profile.id, round=1, summary="", key_points=[], perspective="",
                    )

                if isinstance(first, Findings):
                    # Report writing (no Round 2).
                    return await agent.write_report(first, None)

                # Handle ClarificationQuery for the scribe's clarification protocol
                if isinstance(first, ClarificationQuery):
                    if hasattr(agent, "clarify"):
                        return await agent.clarify(first)
                    # Fallback: agent doesn't support clarify
                    return ClarificationResponse(
                        agent_id=first.agent_id,
                        response="I cannot clarify this further with the available information.",
                    )

            if len(args) == 2 and isinstance(args[0], ResearchTopic) and isinstance(args[1], SharedKnowledge):
                # Round 2 — the orchestrator expects an IndividualReport here.
                questions = _questions or FollowUpQuestions(
                    agent_id=profile.id, questions=[]
                )
                r2 = await agent.research_round_2(
                    args[0], args[1], questions
                )
                return await agent.write_report(_round_1, r2)

            raise TypeError(
                f"Agent dispatcher received unrecognised arguments for "
                f"profile '{profile.id}': {args}"
            )

        return dispatch
