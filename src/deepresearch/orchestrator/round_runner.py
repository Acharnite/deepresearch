"""Round execution, agent lifecycle, and failure handling.

Extracted from the Orchestrator god class — all method bodies are preserved
as-is with only ``self.xxx`` → ``self._orch.xxx`` adjustments for fields
that remain on the Orchestrator.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from rich.console import Console

from deepresearch.agents.registry import Phase
from deepresearch.config import ConfigError
from deepresearch.constants import MAX_SESSION_DURATION
from deepresearch.observability.tracing import tracer
from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchTopic,
    SessionConfig,
    SharedKnowledge,
)

logger = logging.getLogger(__name__)
console = Console()


class RoundRunner:
    """Executes research rounds, manages agent lifecycle, and handles failures.

    Routes timeout calculations through the Orchestrator
    (``self._orch._get_round_timeout()``) so monkey-patching by tests works.
    Holds a back-reference to the ``Orchestrator`` for shared state like
    ``failed_agents`` and ``_cancel_event``.
    """

    def __init__(self, orch: Any, event_bus: Any | None) -> None:
        self._orch = orch
        self._event_bus = event_bus

    # ------------------------------------------------------------------
    # Convenience access to config (still owned by Orchestrator)
    # ------------------------------------------------------------------

    @property
    def _config(self) -> SessionConfig | None:
        return self._orch.session_config

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    async def assign_models(
        self,
        mode: str,
        profiles: list[AgentProfile],
        selected_model: str | None = None,
        agent_models: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Assign LLM models to agent profiles.

        Delegates to :func:`deepresearch.orchestrator.config.assign_models`.
        """
        from deepresearch.orchestrator.config import (
            assign_models as _assign_models_impl,
        )

        return await _assign_models_impl(
            self._orch,
            mode,
            profiles,
            selected_model=selected_model,
            agent_models=agent_models,
        )

    # ------------------------------------------------------------------
    # Round Execution (parallel with timeout protection)
    # ------------------------------------------------------------------

    async def run_round(
        self,
        round_num: int,
        agents: dict[str, Any],
        topic: ResearchTopic,
        shared: SharedKnowledge | None = None,
        start_time: float | None = None,
    ) -> dict[str, Any]:
        """Execute all agents in parallel with individual timeout protection.

        Args:
            round_num: Round number (1 or 2) — used for event logging.
            agents: ``{agent_id: coroutine_fn}``. Each function is called
                with ``(topic,)`` for Round 1 or ``(topic, shared)`` for
                Round 2.
            topic: The research topic.
            shared: Shared knowledge (``None`` for Round 1).

        Returns:
            ``{agent_id: result}`` — only successful results are included.
            Failed agents are recorded via :meth:`handle_agent_failure`.
        """
        with tracer.start_as_current_span(
            f"round.{round_num}",
            attributes={
                "round.num": round_num,
            },
        ) as _:
            timeout = self._orch._get_round_timeout()
            tasks: dict[str, asyncio.Task[Any]] = {}
            for agent_id, agent_fn in agents.items():
                if agent_id in self._orch.failed_agents:
                    logger.debug(
                        "Skipping failed agent '%s' in round %d", agent_id, round_num
                    )
                    continue

                # Check cancellation before launching each agent task.
                if self._orch._cancel_event and self._orch._cancel_event.is_set():
                    logger.info(
                        "Cancel event set — skipping remaining agents in round %d",
                        round_num,
                    )
                    if self._event_bus:
                        await self._event_bus.publish(
                            {"event_type": "round_cancelled", "round": round_num, "agent_id": agent_id}
                        )
                    break

                # Publish start event BEFORE creating task so the dashboard
                # immediately shows the agent as "running" (🔄).
                agent_model = "unknown"
                if self._config and hasattr(self._config, "agent_models"):
                    agent_model = self._config.agent_models.get(agent_id, "unknown")
                if self._event_bus:
                    await self._event_bus.publish(
                        {
                            "event_type": "agent_start",
                            "agent_id": agent_id,
                            "round": round_num,
                            "model": agent_model,
                            "timeout": timeout,
                            "agent_state": "researching",
                        }
                    )

                coro = (
                    agent_fn(Phase.ROUND_2, topic=topic, shared=shared)
                    if shared is not None
                    else agent_fn(Phase.INITIAL_ROUND, topic=topic)
                )
                tasks[agent_id] = asyncio.create_task(
                    asyncio.wait_for(coro, timeout=timeout),
                )

            results: dict[str, Any] = {}
            # Track agents that need retry: agent_id -> agent_fn (or None if task failed before we can reuse)
            retry_tasks: dict[str, Any | None] = {}

            for agent_id, task in tasks.items():
                # Check cancellation before awaiting each task result.
                if self._orch._cancel_event and self._orch._cancel_event.is_set():
                    logger.info(
                        "Cancel event set — cancelling remaining tasks in round %d",
                        round_num,
                    )
                    for t in tasks.values():
                        if not t.done():
                            t.cancel()
                    break
                try:
                    result = await task
                    result_size = len(str(result)) if result else 0

                    if self._is_empty_result(result):
                        logger.warning(
                            "Agent '%s' returned empty/meaningless result (%d chars), will retry",
                            agent_id,
                            result_size,
                        )
                        # Don't mark as failed yet — add to retry list
                        retry_tasks[agent_id] = agents.get(agent_id)
                    else:
                        results[agent_id] = result
                        if self._event_bus:
                            await self._event_bus.publish(
                                {
                                    "event_type": "agent_complete",
                                    "agent_id": agent_id,
                                    "round": round_num,
                                    "result_chars": result_size,
                                    "status": "success",
                                }
                            )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Agent '%s' timed out in Round %d (timeout=%ds), will retry",
                        agent_id,
                        round_num,
                        timeout,
                    )
                    retry_tasks[agent_id] = agents.get(agent_id)
                except asyncio.CancelledError:
                    logger.info(
                        "Agent '%s' cancelled in Round %d",
                        agent_id,
                        round_num,
                    )
                    break
                except Exception as e:
                    logger.warning(
                        "Agent '%s' failed in Round %d: %s, will retry",
                        agent_id,
                        round_num,
                        e,
                    )
                    retry_tasks[agent_id] = agents.get(agent_id)

                # ── Safety timeout check after each agent ────────────────────
                # Only enforce MAX_SESSION_DURATION as safety net — budget is an estimate
                if start_time is not None:
                    if time.monotonic() - start_time > MAX_SESSION_DURATION:
                        logger.warning("Safety timeout reached in round %d — stopping", round_num)
                        break

            # ── Retry failed agents once ────────────────────────────────
            for agent_id in list(retry_tasks.keys()):
                agent_fn = retry_tasks[agent_id]
                if agent_fn is None or agent_id in self._orch.failed_agents:
                    # Already marked as failed by a concurrent path — skip
                    if agent_fn is not None and agent_id not in self._orch.failed_agents:
                        # Agent fn available but task failed catastrophically — still mark
                        await self.handle_agent_failure(agent_id, "retry_unavailable")
                    continue


                logger.info("Retrying agent '%s' (attempt 2/2)", agent_id)
                if self._event_bus:
                    await self._event_bus.publish(
                        {"event_type": "agent_retry", "agent_id": agent_id, "round": round_num}
                    )

                # Publish retry start event so the dashboard shows "Retrying..."
                if self._event_bus:
                    await self._event_bus.publish(
                        {
                            "event_type": "agent_start",
                            "agent_id": agent_id,
                            "round": round_num,
                            "model": "unknown",
                            "timeout": timeout,
                            "agent_state": "retrying",
                        }
                    )

                try:
                    coro = (
                        agent_fn(Phase.ROUND_2, topic=topic, shared=shared)
                        if shared is not None
                        else agent_fn(Phase.INITIAL_ROUND, topic=topic)
                    )
                    result = await asyncio.wait_for(coro, timeout=timeout)
                    result_size = len(str(result)) if result else 0

                    if self._is_empty_result(result):
                        await self.handle_agent_failure(agent_id, "empty_result (retry failed)")
                    else:
                        results[agent_id] = result
                        if self._event_bus:
                            await self._event_bus.publish(
                                {
                                    "event_type": "agent_complete",
                                    "agent_id": agent_id,
                                    "round": round_num,
                                    "result_chars": result_size,
                                    "status": "success",
                                    "attempt": 2,
                                }
                            )
                        logger.info("Agent '%s' succeeded on retry", agent_id)
                except asyncio.TimeoutError:
                    await self.handle_agent_failure(agent_id, "timeout (retry failed)")
                except Exception as e:
                    await self.handle_agent_failure(agent_id, f"{e} (retry failed)")

            return results

    @staticmethod
    def _is_empty_result(result: Any) -> bool:
        """Check if a research result is effectively empty (no substantive content).

        Returns ``True`` when the result should trigger a retry — handles
        ``Findings`` (summary/key_points) and ``IndividualReport``
        (perspective_summary/key_insights).
        """
        if result is None:
            return True
        # Findings model
        if hasattr(result, "key_points") and result.key_points:
            return False
        if (
            hasattr(result, "summary")
            and result.summary
            and len(result.summary.strip()) > 20
        ):
            return False
        # IndividualReport model
        if hasattr(result, "key_insights") and result.key_insights:
            return False
        if (
            hasattr(result, "perspective_summary")
            and result.perspective_summary
            and len(result.perspective_summary.strip()) > 20
        ):
            return False
        return True

    async def _run_round_n(
        self,
        round_num: int,
        agents: dict[str, Any],
        topic: ResearchTopic,
        shared: SharedKnowledge,
        prev_round: dict[str, Any],
        start_time: float | None = None,
    ) -> dict[str, Any]:
        """Run a Round N (N >= 3) for all agents.

        Dispatches with (topic, shared, round_num, prev_findings) so the
        registry routes to research_round_n.
        """
        with tracer.start_as_current_span(
            f"round.{round_num}",
            attributes={
                "round.num": round_num,
            },
        ) as _:
            timeout = self._orch._get_round_timeout()
            tasks: dict[str, asyncio.Task[Any]] = {}
            for agent_id, agent_fn in agents.items():
                if agent_id in self._orch.failed_agents:
                    continue
                if self._orch._cancel_event and self._orch._cancel_event.is_set():
                    break

                prev_findings = prev_round.get(agent_id)
                if prev_findings is None:
                    logger.warning(
                        "No previous findings for agent '%s' in round %d", agent_id, round_num
                    )
                    continue
                # Convert IndividualReport to Findings if needed (R2 dispatch wraps)
                if isinstance(prev_findings, IndividualReport):
                    prev_findings = Findings(
                        agent_id=prev_findings.agent_id,
                        round=round_num - 1,
                        summary=prev_findings.perspective_summary,
                        key_points=prev_findings.key_insights,
                        perspective=prev_findings.analysis,
                        confidence=0.7,
                    )

                if self._event_bus:
                    await self._event_bus.publish(
                        {
                            "event_type": "agent_start",
                            "agent_id": agent_id,
                            "round": round_num,
                            "model": self._config.agent_models.get(agent_id, "unknown")
                            if self._config
                            else "unknown",
                            "timeout": timeout,
                            "agent_state": "researching",
                        }
                    )

                coro = agent_fn(
                    Phase.ROUND_N,
                    topic=topic,
                    shared=shared,
                    round_num=round_num,
                    prev_findings=prev_findings,
                )
                tasks[agent_id] = asyncio.create_task(
                    asyncio.wait_for(coro, timeout=timeout),
                )

            results: dict[str, Any] = {}
            retry_tasks: dict[str, Any | None] = {}

            for agent_id, task in tasks.items():
                try:
                    result = await task
                    result_size = len(str(result)) if result else 0

                    if self._is_empty_result(result):
                        logger.warning(
                            "Agent '%s' returned empty result in round %d, will retry",
                            agent_id,
                            round_num,
                        )
                        retry_tasks[agent_id] = agents.get(agent_id)
                    else:
                        results[agent_id] = result
                        if self._event_bus:
                            await self._event_bus.publish(
                                {
                                    "event_type": "agent_complete",
                                    "agent_id": agent_id,
                                    "round": round_num,
                                    "result_chars": result_size,
                                    "status": "success",
                                }
                            )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Agent '%s' timed out in Round %d (timeout=%ds), will retry",
                        agent_id,
                        round_num,
                        timeout,
                    )
                    retry_tasks[agent_id] = agents.get(agent_id)
                except asyncio.CancelledError:
                    logger.info(
                        "Agent '%s' cancelled in round %d",
                        agent_id,
                        round_num,
                    )
                    break
                except Exception as e:
                    logger.warning(
                        "Agent '%s' failed in Round %d: %s, will retry",
                        agent_id,
                        round_num,
                        e,
                    )
                    retry_tasks[agent_id] = agents.get(agent_id)

                # ── Safety timeout check after each agent ────────────────────
                # Only enforce MAX_SESSION_DURATION as safety net — budget is an estimate
                if start_time is not None:
                    if time.monotonic() - start_time > MAX_SESSION_DURATION:
                        logger.warning("Safety timeout reached in round %d — stopping", round_num)
                        break

            # ── Retry failed agents once ────────────────────────────────
            for agent_id in list(retry_tasks.keys()):
                agent_fn = retry_tasks[agent_id]
                if agent_fn is None or agent_id in self._orch.failed_agents:
                    if agent_fn is not None and agent_id not in self._orch.failed_agents:
                        await self.handle_agent_failure(agent_id, "retry_unavailable")
                    continue

                logger.info("Retrying agent '%s' (attempt 2/2)", agent_id)
                if self._event_bus:
                    await self._event_bus.publish(
                        {"event_type": "agent_retry", "agent_id": agent_id, "round": round_num}
                    )

                prev_findings = prev_round.get(agent_id)
                if prev_findings is None:
                    await self.handle_agent_failure(agent_id, "no_previous_findings (retry)")
                    continue
                if isinstance(prev_findings, IndividualReport):
                    prev_findings = Findings(
                        agent_id=prev_findings.agent_id,
                        round=round_num - 1,
                        summary=prev_findings.perspective_summary,
                        key_points=prev_findings.key_insights,
                        perspective=prev_findings.analysis,
                        confidence=0.7,
                    )

                if self._event_bus:
                    await self._event_bus.publish(
                        {
                            "event_type": "agent_start",
                            "agent_id": agent_id,
                            "round": round_num,
                            "model": "unknown",
                            "timeout": timeout,
                            "agent_state": "retrying",
                        }
                    )

                try:
                    coro = agent_fn(
                        Phase.ROUND_N,
                        topic=topic,
                        shared=shared,
                        round_num=round_num,
                        prev_findings=prev_findings,
                    )
                    result = await asyncio.wait_for(coro, timeout=timeout)
                    result_size = len(str(result)) if result else 0

                    if self._is_empty_result(result):
                        await self.handle_agent_failure(agent_id, "empty_result (retry failed)")
                    else:
                        results[agent_id] = result
                        if self._event_bus:
                            await self._event_bus.publish(
                                {
                                    "event_type": "agent_complete",
                                    "agent_id": agent_id,
                                    "round": round_num,
                                    "result_chars": result_size,
                                    "status": "success",
                                    "attempt": 2,
                                }
                            )
                        logger.info("Agent '%s' succeeded on retry", agent_id)
                except asyncio.TimeoutError:
                    await self.handle_agent_failure(agent_id, "timeout (retry failed)")
                except Exception as e:
                    await self.handle_agent_failure(agent_id, f"{e} (retry failed)")

            return results

    # ------------------------------------------------------------------
    # Follow-up Questions
    # ------------------------------------------------------------------

    async def collect_followup_questions(
        self,
        agents: dict[str, Any],
        shared: SharedKnowledge,
    ) -> dict[str, FollowUpQuestions]:
        """Each non-failed agent submits questions based on shared knowledge.

        Agent IDs are passed so each agent knows which other agents are
        available for targeted questions.
        """
        timeout = max(30, self._orch._get_round_timeout() // 2)
        agent_ids = list(agents.keys())
        tasks: dict[str, asyncio.Task[Any]] = {}

        for agent_id, agent_fn in agents.items():
            if agent_id in self._orch.failed_agents:
                continue

            # Phase.REVIEW dispatch with agent_ids so the agent knows
            # which peers it can direct questions at.
            async def _call_with_ids(_fn=agent_fn, _ids=agent_ids):
                return await _fn(
                    Phase.REVIEW, shared=shared, agent_ids=_ids
                )

            tasks[agent_id] = asyncio.create_task(
                asyncio.wait_for(_call_with_ids(), timeout=timeout),
            )

        results: dict[str, FollowUpQuestions] = {}
        for agent_id, task in tasks.items():
            try:
                result = await task
                if result is None:
                    logger.warning("Agent '%s' returned None follow-up", agent_id)
                    continue
                if hasattr(result, "questions") and not result.questions:
                    logger.warning(
                        "Agent '%s' returned empty follow-up questions", agent_id
                    )
                    continue
                results[agent_id] = result
            except asyncio.TimeoutError:
                logger.warning("Agent '%s' follow-up timed out", agent_id)
            except Exception as e:
                logger.warning("Failed to collect follow-up from %s: %s", agent_id, e)
        return results

    # ------------------------------------------------------------------
    # Clarification callbacks
    # ------------------------------------------------------------------

    async def _handle_clarification(
        self,
        query: ClarificationQuery,
    ) -> ClarificationResponse:
        """Route a scribe's clarification query to the appropriate agent.

        Looks up the agent in ``self._orch._agents`` (built during ``run()``)
        and calls its ``clarify`` method.  If the agent is unavailable
        or fails, returns a default response.
        """
        agent_id = query.agent_id
        agent = self._orch._agents.get(agent_id) if hasattr(self._orch, "_agents") else None

        if agent is None:
            return ClarificationResponse(
                agent_id=agent_id,
                response="Agent unavailable for clarification.",
            )

        try:
            if hasattr(agent, "clarify"):
                return await agent.clarify(query)

            # The agent might be a dispatch wrapper — call with Phase.CLARIFY.
            return await agent(Phase.CLARIFY, query=query)

        except Exception as exc:
            logger.warning(
                "Clarification request to agent '%s' failed: %s",
                agent_id,
                exc,
            )
            return ClarificationResponse(
                agent_id=agent_id,
                response=f"Unable to clarify: {exc}",
            )

    # ------------------------------------------------------------------
    # Refinement phase
    # ------------------------------------------------------------------

    async def _run_refinement(
        self,
        agents: dict[str, Any],
        followup_results: dict[str, FollowUpQuestions],
        active_agents: Callable[[], list[str]],
        start_time: float | None = None,
    ) -> dict[str, Findings]:
        """Run refinement phase for all agents based on follow-up questions."""
        refined: dict[str, Findings] = {}

        async def _refine_agent(agent_id: str, followup: FollowUpQuestions):
            if not isinstance(followup, FollowUpQuestions) or not followup.questions:
                return None
            if agent_id in self._orch.failed_agents:
                return None
            # Filter questions by target_agent_ids
            targeted_questions: list[str] = []
            targets = followup.target_agent_ids or [None] * len(followup.questions)
            for q, target in zip(followup.questions, targets):
                if target is None or target == agent_id:
                    targeted_questions.append(q)
            if not targeted_questions:
                return None
            targeted_followup = FollowUpQuestions(
                agent_id=followup.agent_id,
                questions=targeted_questions,
            )
            try:
                refined_result = await asyncio.wait_for(
                    agents[agent_id](
                        Phase.REFINEMENT, followup=targeted_followup
                    ),
                    timeout=max(30, self._orch._get_round_timeout() // 2),
                )
                if (
                    refined_result
                    and isinstance(refined_result, Findings)
                    and (refined_result.summary or refined_result.key_points)
                ):
                    return (agent_id, refined_result)
            except Exception as e:
                logger.warning("Agent '%s' refinement failed: %s", agent_id, e)
            return None

        tasks = [_refine_agent(aid, fu) for aid, fu in followup_results.items()]
        results = await asyncio.gather(*tasks)

        # ── Safety timeout check after refinement ──────────────────────────
        # Only enforce MAX_SESSION_DURATION as safety net — budget is an estimate
        if start_time is not None:
            if time.monotonic() - start_time > MAX_SESSION_DURATION:
                logger.warning("Safety timeout reached after refinement phase — stopping")

        for result in results:
            if result:
                agent_id, refined_findings = result
                refined[agent_id] = refined_findings
                logger.info("Agent '%s' refined findings", agent_id)

        if refined:
            console.print(
                f"  [dim]{len(refined)} agent(s) refined their findings[/dim]"
            )
        return refined

    # ------------------------------------------------------------------
    # Error Handling
    # ------------------------------------------------------------------

    async def handle_agent_failure(self, agent_id: str, error: str) -> None:
        """Log the failure, mark the agent as failed, and continue."""
        self._orch.failed_agents[agent_id] = error
        logger.warning("Agent '%s' failed: %s", agent_id, error)
        if self._event_bus:
            await self._event_bus.publish(
                {"event_type": "agent_failed", "agent_id": agent_id, "error": error}
            )
        console.print(
            f"  [yellow]⚠ Agent '{agent_id}' failed: {error}[/yellow]"
            " — continuing with remaining agents",
        )

    # ------------------------------------------------------------------
    # Agent Construction
    # ------------------------------------------------------------------

    def _build_agents(
        self,
        config: SessionConfig,
        factory: Callable[[AgentProfile, str], Any],
    ) -> dict[str, Any]:
        """Build agent callables via the injected factory.

        Each agent gets a stream callback so that LLM output chunks are
        published as ``agent_output`` events in real time.

        If ``self._orch._cancel_event`` is set, it is propagated to each
        agent's LLM client so that ``cancel_event.is_set()`` is checked
        before every LLM call and retry.
        """
        agents: dict[str, Any] = {}
        for profile in config.agent_profiles:
            model_name = config.agent_models.get(profile.id, "")
            cb = self._orch.scribe_comp._make_stream_callback(profile.id)
            # Pass cancel_event through kwargs if the factory accepts it.
            try:
                agents[profile.id] = factory(
                    profile,
                    model_name,
                    event_callback=cb,
                    cancel_event=getattr(self._orch, "_cancel_event", None),
                )
            except TypeError:
                # Factory doesn't accept cancel_event — fallback.
                agents[profile.id] = factory(profile, model_name, event_callback=cb)
        return agents
