"""Agent Registry — factory for creating agent instances.

The ``AgentRegistry`` is the single entry-point for constructing agents and
exposing the callable that the ``Orchestrator`` uses to dispatch lifecycle
calls.

Dispatch pattern
----------------
The orchestator's ``_build_agents`` expects ``agent_factory(profile, model_name)``
to return a *single callable* that accepts a ``Phase`` enum plus keyword
arguments.  Instead of the fragile ``isinstance``-based dispatch, the
registry maintains a handler map (``_HANDLERS``) that routes each phase to
the correct agent method.
"""

from __future__ import annotations

from collections.abc import Awaitable
from enum import Enum
from typing import Any, Callable

from deepresearch.agents.research_agent import ResearchAgent
from deepresearch.agents.scribe_agent import ScribeAgent
from deepresearch.llm.client import LLMClient
from deepresearch.llm.tracker import TokenTracker
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
from deepresearch.web.settings_manager import settings_manager


class Phase(Enum):
    """Lifecycle phases for agent dispatch.

    Each phase maps to a specific agent method call in ``_HANDLERS``.
    """

    INITIAL_ROUND = "initial_round"
    REVIEW = "review"
    REFINEMENT = "refinement"
    REPORT = "report"
    CLARIFY = "clarify"
    ROUND_2 = "round_2"
    ROUND_N = "round_n"
    CROSS_CHECK = "cross_check"
    RED_TEAM = "red_team"
    SCRIBE_COMPILE = "scribe_compile"


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

    _HANDLERS: dict[Phase, Callable] = {}

    def dispatch(self, phase: Phase, **kwargs: Any) -> Any:
        """Look up the handler for *phase* and call it with *kwargs*."""
        handler = self._HANDLERS.get(phase)
        if handler is None:
            raise KeyError(
                f"No handler registered for phase {phase!r}. "
                f"Available: {list(self._HANDLERS)}"
            )
        return handler(self, **kwargs)

    def __init__(
        self,
        llm_client: LLMClient,
        token_tracker: TokenTracker | None = None,
    ) -> None:
        self.llm = llm_client
        self.token_tracker = token_tracker

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
            max_tokens=settings_manager.get_max_tokens(),
            tracker=self.token_tracker,
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
            llm = LLMClient(
                model=model_name,
                timeout=300,
                event_callback=event_callback,
                tracker=self.token_tracker,
            )
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
        cancel_event: Any | None = None,
    ) -> Callable[..., Any]:
        """Factory for the Orchestrator.

        Returns a callable that wraps a ``ResearchAgent`` and dispatches
        to the appropriate lifecycle method based on argument types.

        Args:
            profile: The agent's personality profile.
            model_name: The LLM model to use for this agent.
            event_callback: Async callback for streaming output chunks.
            cancel_event: Optional ``asyncio.Event`` for force-stop
                cancellation.  Stored on the LLM client so all calls
                check it automatically.

        Returns:
            An ``AgentFunc`` — an async callable that handles all lifecycle
            phases via type-based dispatch.
        """
        agent = self.create_research_agent(profile, model_name, event_callback)
        # Propagate cancel_event to the agent's LLM client.
        if cancel_event is not None and agent.llm is not None:
            agent.llm.cancel_event = cancel_event

        # Internal state tracked across lifecycle calls.
        _round_1: Findings | None = None
        _questions: FollowUpQuestions | None = None

        async def _dispatch_state(state: str) -> None:
            """Send a state update through the agent's LLM event callback."""
            if (
                agent.llm
                and hasattr(agent.llm, "event_callback")
                and agent.llm.event_callback
            ):
                try:
                    await agent.llm.event_callback(
                        {"type": "agent_state", "state": state}
                    )
                except Exception:
                    pass  # Fire-and-forget.

        # ── Phase → state string map ──────────────────────────────────────
        _PHASE_STATE_MAP: dict[Phase, str] = {
            Phase.INITIAL_ROUND: "researching",
            Phase.REVIEW: "questioning",
            Phase.REFINEMENT: "refining",
            Phase.REPORT: "writing",
            Phase.CLARIFY: "answering",
            Phase.ROUND_2: "researching",
            Phase.ROUND_N: "researching",
        }

        async def agent_func(phase: Phase, **kwargs: Any) -> Any:
            """Phase-based dispatch — replaces the old isinstance chain.

            The ``Orchestrator`` calls this function with a ``Phase`` enum
            and keyword arguments.  Phase-specific state (round-1 findings,
            follow-up questions) is managed through the closure and passed
            along to the dispatch handlers.
            """
            nonlocal _round_1, _questions

            # 1. Emit state transition if applicable.
            state = _PHASE_STATE_MAP.get(phase)
            if state:
                await _dispatch_state(state)

            # 2. Inject closure-managed state for phases that need it.
            if phase == Phase.REFINEMENT:
                kwargs.setdefault("prior_findings", _round_1)
            elif phase == Phase.ROUND_2:
                kwargs.setdefault(
                    "questions",
                    _questions or FollowUpQuestions(agent_id=profile.id, questions=[]),
                )
                kwargs.setdefault("round_1_findings", _round_1)

            # 3. Dispatch via the handler map.
            result = await self.dispatch(
                phase,
                agent=agent,
                profile=profile,
                **kwargs,
            )

            # 4. Track lifecycle state for subsequent phases.
            if phase in (Phase.INITIAL_ROUND, Phase.REFINEMENT):
                _round_1 = result
            elif phase == Phase.REVIEW:
                _questions = result

            return result

        return agent_func


# ── Phase Handler Registration ───────────────────────────────────────────
# Each handler receives ``(self, **kwargs)`` from ``AgentRegistry.dispatch``.
# The ``agent`` kwarg is the ``ResearchAgent`` instance; phase-specific
# kwargs (``topic``, ``shared``, ``followup``, etc.) are forwarded from the
# orchestrator via the ``agent_func`` closure.


async def _handle_initial_round(
    registry: AgentRegistry,
    *,
    agent: ResearchAgent,
    topic: ResearchTopic,
    **kwargs: Any,
) -> Findings:
    """Phase.INITIAL_ROUND — call ``agent.research_round_1``."""
    return await agent.research_round_1(topic)


async def _handle_review(
    registry: AgentRegistry,
    *,
    agent: ResearchAgent,
    shared: SharedKnowledge,
    **kwargs: Any,
) -> FollowUpQuestions:
    """Phase.REVIEW — call ``agent.review_findings``.

    The orchestrator passes ``agent_ids`` via kwargs so agents know which
    peers they can direct questions at.
    """
    agent_ids = kwargs.get("agent_ids")
    if agent_ids is not None:
        return await agent.review_findings(shared, agent_ids=agent_ids)
    return await agent.review_findings(shared)


async def _handle_refinement(
    registry: AgentRegistry,
    *,
    agent: ResearchAgent,
    followup: FollowUpQuestions,
    **kwargs: Any,
) -> Findings:
    """Phase.REFINEMENT — call ``agent.refine_findings``.

    ``prior_findings`` is injected by the ``agent_func`` closure from its
    internal state (``_round_1``).
    """
    prior_findings: Findings | None = kwargs.get("prior_findings")
    if prior_findings is not None:
        return await agent.refine_findings(followup, prior_findings)
    return Findings(
        agent_id=agent.profile.id,
        round=1,
        summary="",
        key_points=[],
        perspective="",
    )


async def _handle_report(
    registry: AgentRegistry,
    *,
    agent: ResearchAgent,
    findings: Findings,
    **kwargs: Any,
) -> IndividualReport:
    """Phase.REPORT — call ``agent.write_report`` (no Round 2)."""
    return await agent.write_report(findings, None)


async def _handle_clarify(
    registry: AgentRegistry,
    *,
    agent: ResearchAgent,
    query: ClarificationQuery,
    **kwargs: Any,
) -> ClarificationResponse:
    """Phase.CLARIFY — call ``agent.clarify``."""
    if hasattr(agent, "clarify"):
        return await agent.clarify(query)
    # Fallback: agent doesn't support clarify
    return ClarificationResponse(
        agent_id=query.agent_id,
        response="I cannot clarify this further with the available information.",
    )


async def _handle_round_2(
    registry: AgentRegistry,
    *,
    agent: ResearchAgent,
    topic: ResearchTopic,
    shared: SharedKnowledge,
    **kwargs: Any,
) -> IndividualReport:
    """Phase.ROUND_2 — research round 2 then write report.

    ``questions`` and ``round_1_findings`` are injected by the ``agent_func``
    closure from its internal state.
    """
    questions: FollowUpQuestions = kwargs.get(
        "questions",
        FollowUpQuestions(agent_id=agent.profile.id, questions=[]),
    )
    round_1_findings: Findings | None = kwargs.get("round_1_findings")
    r2 = await agent.research_round_2(topic, shared, questions)
    return await agent.write_report(round_1_findings, r2)


async def _handle_round_n(
    registry: AgentRegistry,
    *,
    agent: ResearchAgent,
    topic: ResearchTopic,
    shared: SharedKnowledge,
    round_num: int,
    prev_findings: Findings,
    **kwargs: Any,
) -> IndividualReport:
    """Phase.ROUND_N — deep iterative research (R3+)."""
    r_n = await agent.research_round_n(topic, shared, round_num, prev_findings)
    return await agent.write_report(prev_findings, r_n)


# Build the handler map.
AgentRegistry._HANDLERS = {
    Phase.INITIAL_ROUND: _handle_initial_round,
    Phase.REVIEW: _handle_review,
    Phase.REFINEMENT: _handle_refinement,
    Phase.REPORT: _handle_report,
    Phase.CLARIFY: _handle_clarify,
    Phase.ROUND_2: _handle_round_2,
    Phase.ROUND_N: _handle_round_n,
}
