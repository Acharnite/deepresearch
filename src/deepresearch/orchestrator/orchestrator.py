"""Orchestrator — Thin lifecycle coordinator for DeepResearch sessions.

Delegates round execution to ``RoundRunner``, convergence detection to
``SessionState``, timeout calculations to ``TimeoutCalculator``, and
scribe/PDF work to ``ScribeCompiler``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from collections.abc import Awaitable
from typing import Any, Callable

from rich.console import Console

from deepresearch.collaboration import CollaborationBus
from deepresearch.config import ConfigError
from deepresearch.config.session import SessionConfig
from deepresearch.constants import MAX_SESSION_DURATION
from deepresearch.observability.tracing import tracer
from deepresearch.models import (
    AgentProfile,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchTopic,
    SharedKnowledge,
)
from deepresearch.orchestrator.round_runner import RoundRunner
from deepresearch.orchestrator.scribe_compiler import ScribeCompiler
from deepresearch.orchestrator.session_state import SessionState
from deepresearch.orchestrator.timeout_calculator import TimeoutCalculator

logger = logging.getLogger(__name__)
console = Console()

AgentFunc = Callable[..., Any]
PromptFunc = Callable[..., str]


class Orchestrator:
    """Central coordinator for DeepResearch sessions.

    Injectable factories allow unit testing with mock agents (no LLM calls),
    future real agent implementations, and custom prompt strategies.
    """

    def __init__(
        self,
        *,
        profiles_path: str | Path | None = None,
        models_path: str | Path | None = None,
        profiles: list[AgentProfile] | None = None,
        model_configs: list[dict[str, Any]] | None = None,
        prompt_func: PromptFunc | None = None,
        agent_factory: Callable[[AgentProfile, str], AgentFunc] | None = None,
        scribe_factory: Callable[[], AgentFunc] | None = None,
        event_bus: Any = None,
    ) -> None:
        self.profiles_path = profiles_path
        self.models_path = models_path
        self._profiles_override = profiles
        self._model_configs_override = model_configs
        self._prompt = prompt_func or ScribeCompiler._default_prompt
        self._agent_factory = agent_factory
        self._scribe_factory = scribe_factory
        self._event_bus = event_bus

        self.model_configs = model_configs or []
        self.session_config: SessionConfig | None = None
        self.state: str = "IDLE"
        self.failed_agents: dict[str, str] = {}
        self._session_start_time: datetime | None = None
        self._cancel_event: asyncio.Event | None = None
        self._pdf_underweight: bool = False

        # ── Collaborators ────────────────────────────────────────────
        self.timeout_calc = TimeoutCalculator(self.session_config)
        self.state_tracker = SessionState("", None)
        self.round_runner = RoundRunner(self, self._event_bus)
        self.scribe_comp = ScribeCompiler(self, self._prompt)

    # ------------------------------------------------------------------
    # Backward-compat wrappers for config.py
    # ------------------------------------------------------------------

    @property
    def _topic_seed(self) -> str:
        return self.state_tracker.topic_seed

    @property
    def _CUSTOM_BUDGET_KEY(self) -> str:
        return ScribeCompiler._CUSTOM_BUDGET_KEY

    @property
    def TIME_BUDGET_OPTIONS(self) -> dict[str, str]:
        return ScribeCompiler.TIME_BUDGET_OPTIONS

    @staticmethod
    def _default_prompt(message: str, **kwargs: Any) -> str:
        return ScribeCompiler._default_prompt(message, **kwargs)

    def _prompt_time_budget(self) -> str:
        return self.scribe_comp._prompt_time_budget()

    def _prompt_model_mode(self) -> str:
        return self.scribe_comp._prompt_model_mode()

    def _prompt_for_model(
        self, profile: AgentProfile, available: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return self.scribe_comp._prompt_for_model(profile, available)

    # ------------------------------------------------------------------
    # Backward-compat wrappers for test code — delegate to collaborators
    # ------------------------------------------------------------------

    def _get_round_timeout(self) -> int:
        return self.timeout_calc.get_round_timeout()

    async def run_round(
        self, round_num: int, agents: dict[str, AgentFunc], topic: ResearchTopic,
        shared: SharedKnowledge | None = None, start_time: float | None = None,
    ) -> dict[str, Any]:
        return await self.round_runner.run_round(
            round_num, agents, topic, shared=shared, start_time=start_time,
        )

    async def collect_followup_questions(
        self, agents: dict[str, AgentFunc], shared: SharedKnowledge,
    ) -> dict[str, FollowUpQuestions]:
        return await self.round_runner.collect_followup_questions(agents, shared)

    async def _handle_clarification(self, query: Any) -> Any:
        return await self.round_runner._handle_clarification(query)

    async def handle_agent_failure(self, agent_id: str, error: str) -> None:
        await self.round_runner.handle_agent_failure(agent_id, error)

    async def _finalize_output(self, output_path: Path) -> Path:
        return await self.scribe_comp._finalize_output(output_path)

    async def compile(
        self, reports: dict[str, IndividualReport], scribe: AgentFunc, topic: str = "",
    ) -> ResearchPaper:
        return await self.scribe_comp.compile(reports, scribe, topic=topic)

    async def assign_models(
        self,
        mode: str,
        profiles: list[AgentProfile],
        selected_model: str | None = None,
        agent_models: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Assign LLM models to agent profiles."""
        return await self.round_runner.assign_models(
            mode, profiles, selected_model=selected_model, agent_models=agent_models
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    async def configure(
        self, topic_str: str, **overrides: Any
    ) -> SessionConfig:
        """Create a validated SessionConfig for a research session."""
        from deepresearch.orchestrator.config import configure as _configure_impl

        config = await _configure_impl(self, topic_str, **overrides)
        self.timeout_calc._config = config
        self.state_tracker.topic = config.topic
        return config

    # ------------------------------------------------------------------
    # Session Lifecycle
    # ------------------------------------------------------------------

    async def run(self, topic: str, **overrides: Any) -> Path:
        """Run a full research session from topic to output."""
        self._cancel_event = overrides.get("cancel_event")
        self._session_start_time = datetime.now()
        logger.info("Session started — topic: %s", topic)
        if self._event_bus:
            await self._event_bus.publish({"event_type": "session_start", "topic": topic})
        console.print("\n[bold]🚀 DeepeResearch — Multi-Agent Research System[/bold]")
        console.print(f"[yellow]Topic:[/yellow] {topic}")

        config = await self.configure(topic, **overrides)
        logger.info(
            "Config validated — budget=%s, model_mode=%s",
            config.topic.time_budget, config.topic.model_mode,
        )

        if "output_path" in overrides:
            output_path = Path(overrides["output_path"])
        else:
            output_dir = Path(overrides.get("output_dir", "./output"))
            output_path = output_dir / "paper.pdf"

        if overrides.get("dry_run"):
            self.dry_run(
                topic_str=topic,
                time_budget=config.topic.time_budget,
                model_mode=config.topic.model_mode,
                config=config,
            )
            return output_path

        agent_factory = overrides.get("agent_factory") or self._agent_factory
        scribe_factory = overrides.get("scribe_factory") or self._scribe_factory

        if agent_factory is None:
            raise ConfigError(
                "No agent factory provided. Pass an ``agent_factory`` to the "
                "Orchestrator or implement agent classes (Phase 3+)."
            )

        self.bus = CollaborationBus()
        self.bus.topic = config.topic

        agents = self.round_runner._build_agents(config, agent_factory)
        self._agents = agents
        scribe_cb = self.scribe_comp._make_stream_callback("scribe")
        scribe_model = overrides.get("scribe_model") or overrides.get("selected_model")
        scribe = self.scribe_comp._build_scribe(
            scribe_factory, event_callback=scribe_cb, model_name=scribe_model,
        )

        if self._cancel_event and hasattr(scribe, "llm") and scribe.llm is not None:
            scribe.llm.cancel_event = self._cancel_event

        def active_agents() -> list[str]:
            return [aid for aid in agents if aid not in self.failed_agents]

        logger.info("Starting _run_session — agents=%d", len(agents))
        await self._run_session(
            agents=agents, scribe=scribe, active_agents=active_agents,
            config=config, output_path=output_path, agent_factory=agent_factory,
        )

        pdf_path = await self.scribe_comp._finalize_output(output_path)
        return pdf_path

    async def _run_session(
        self,
        agents: dict[str, AgentFunc],
        scribe: AgentFunc,
        active_agents: Callable[[], list[str]],
        config: SessionConfig,
        output_path: Path,
        agent_factory: Any,
    ) -> None:
        """Inner session execution loop — R1 → Collab → Followup → R2 → … → converge."""
        start_time = time.monotonic()
        round_num = 1
        round_results: dict[int, dict[str, Any]] = {}
        round_history: list[SharedKnowledge] = []
        latest_shared: SharedKnowledge | None = None

        max_r = (
            config.budget.max_rounds
            if hasattr(config, 'budget') else getattr(config, 'max_rounds', 4)
        )
        budget_secs = (
            config.budget.seconds
            if hasattr(config, 'budget') else getattr(config, 'time_budget_seconds', 300)
        )

        with tracer.start_as_current_span(
            "session.run",
            attributes={
                "session.topic": config.topic.question[:100],
                "session.budget": config.budget.keyword if hasattr(config, 'budget') else "",
                "session.max_rounds": max_r or 0,
            },
        ) as _:
            while round_num <= (max_r if max_r else 4):
                if self._cancel_event and self._cancel_event.is_set():
                    logger.info("Cancel event set — aborting round loop")
                    break

                if time.monotonic() - start_time > min(MAX_SESSION_DURATION, budget_secs):
                    logger.info("Time budget exceeded — stopping")
                    if self._cancel_event:
                        self._cancel_event.set()
                    break

                self.state = f"ROUND{round_num}"
                console.print(
                    f"\n[bold]Round {round_num}:[/bold] "
                    + ("Independent Research" if round_num == 1 else "Refined Research")
                )
                if self._event_bus:
                    await self._event_bus.publish({"event_type": "round_start", "round": round_num})

                if round_num == 1:
                    results = await self.round_runner.run_round(
                        1, {aid: agents[aid] for aid in active_agents()},
                        config.topic, start_time=start_time,
                    )
                elif round_num == 2:
                    assert latest_shared is not None
                    results = await self.round_runner.run_round(
                        round_num, {aid: agents[aid] for aid in active_agents()},
                        config.topic, latest_shared, start_time=start_time,
                    )
                else:
                    assert latest_shared is not None
                    prev_round = round_results.get(round_num - 1, {})
                    results = await self.round_runner._run_round_n(
                        round_num, {aid: agents[aid] for aid in active_agents()},
                        config.topic, latest_shared, prev_round, start_time=start_time,
                    )

                round_results[round_num] = results

                if not results:
                    logger.error("ALL agents failed in round %d — stopping", round_num)
                    if self._event_bus:
                        await self._event_bus.publish({"event_type": "all_agents_failed", "round": round_num})
                    console.print("[red]All agents failed — stopping research[/red]")
                    break

                logger.info(
                    "Round %d complete — %d/%d agents succeeded",
                    round_num, len(results), len(agents),
                )

                for agent_id, findings in results.items():
                    await self.bus.publish_round(agent_id, round_num, findings)

                if round_num == 1:
                    self.state_tracker.save_round_findings(results, output_path, round_num)

                if round_num == 1:
                    self.state = "COLLABORATING"
                    console.print("\n[bold]Collaboration:[/bold] Sharing findings across agents")
                    latest_shared = await self.bus.compute_shared_knowledge()
                    round_history.append(latest_shared)
                    if self._event_bus:
                        await self._event_bus.publish(
                            {"event_type": "collaboration_phase", "shared_agent_count": len(results)}
                        )

                    self.state = "FOLLOWUP"
                    console.print("\n[bold]Follow-up:[/bold] Collecting follow-up questions")
                    if self._event_bus:
                        await self._event_bus.publish({"event_type": "followup_start", "active_agents": len(active_agents())})
                    followup_results = await self.round_runner.collect_followup_questions(
                        {aid: agents[aid] for aid in active_agents()}, latest_shared,
                    )
                    questions_dict: dict[str, list[str]] = {}
                    targets_dict: dict[str, list[str | None]] = {}
                    for agent_id, fu in followup_results.items():
                        if isinstance(fu, FollowUpQuestions):
                            await self.bus.publish_followup(agent_id, fu.questions)
                            questions_dict[agent_id] = fu.questions
                            raw_targets = fu.target_agent_ids or [None] * len(fu.questions)
                            targets_dict[agent_id] = [
                                t if t is not None else "All" for t in raw_targets
                            ]
                    if self._event_bus:
                        await self._event_bus.publish(
                            {"event_type": "followup_complete", "results": len(followup_results),
                             "questions": questions_dict, "targets": targets_dict}
                        )

                    self.state = "REFINING"
                    console.print("\n[bold]Refinement:[/bold] Agents refining findings")
                    if self._event_bus:
                        await self._event_bus.publish({"event_type": "refinement_start"})
                    refined = await self.round_runner._run_refinement(
                        agents, followup_results, active_agents, start_time=start_time,
                    )
                    for agent_id, refined_findings in refined.items():
                        results[agent_id] = refined_findings
                    if self._event_bus:
                        await self._event_bus.publish({"event_type": "refinement_complete", "refined_agents": len(refined)})

                if round_num > 1:
                    latest_shared = await self.bus.compute_shared_knowledge()
                    if latest_shared:
                        latest_shared.round_number = round_num
                        latest_shared.round_history = [s for s in round_history]
                        round_history.append(latest_shared)

                if not await self.state_tracker.should_continue(
                    self._cancel_event, self.session_config,
                    round_num + 1, round_history, start_time,
                ):
                    logger.info("Convergence check: stopping after round %d", round_num)
                    if self._event_bus:
                        await self._event_bus.publish(
                            {"event_type": "round_skip", "round": round_num, "reason": "convergence"}
                        )
                    break

                round_num += 1

            self.state = "COMPILING"
            console.print("\n[bold]Compilation:[/bold] Gathering final reports")
            reports: dict[str, IndividualReport] = {}
            latest_round = max(round_results.keys()) if round_results else 1
            for agent_id in active_agents():
                if agent_id in self.failed_agents:
                    continue
                latest_result = round_results.get(latest_round, {}).get(agent_id)
                if isinstance(latest_result, IndividualReport):
                    reports[agent_id] = latest_result
                elif isinstance(latest_result, Findings):
                    reports[agent_id] = IndividualReport(
                        agent_id=agent_id, title=f"Report from {agent_id}",
                        perspective_summary=latest_result.summary,
                        key_insights=latest_result.key_points,
                        analysis=latest_result.raw_response or latest_result.summary,
                        full_text=latest_result.raw_response or latest_result.summary,
                    )

            for agent_id, report in reports.items():
                await self.bus.publish_report(agent_id, report)

            all_reports = await self.bus.get_all_reports()
            if self._event_bus:
                await self._event_bus.publish({
                    "event_type": "scribe_start", "report_count": len(all_reports),
                    "total_reports_chars": sum(len(str(r)) for r in all_reports.values()),
                    "model": "unknown",
                })
            paper = await self.scribe_comp.compile(all_reports, scribe, topic=config.topic.question)
            self._current_paper = paper

    # ------------------------------------------------------------------
    # Dry-run Mode
    # ------------------------------------------------------------------

    def dry_run(
        self, topic_str: str, time_budget: str, model_mode: str,
        config: SessionConfig | None = None,
    ) -> dict[str, Any]:
        """Preview a session without executing any agents."""
        from deepresearch.orchestrator.dry_run import dry_run as _dry_run_impl
        return _dry_run_impl(self, topic_str, time_budget, model_mode, config=config)

    def _show_dry_run_table(
        self, topic_str: str, time_budget_label: str, time_budget_seconds: int,
        model_mode: str, rounds: int, agent_assignments: list[dict[str, Any]],
        estimated_cost: float, estimated_tokens: int,
    ) -> None:
        from deepresearch.orchestrator.dry_run import _show_dry_run_table as _t
        _t(topic_str, time_budget_label, time_budget_seconds, model_mode,
           rounds, agent_assignments, estimated_cost, estimated_tokens)

    def _show_dry_run(self, config: SessionConfig) -> None:
        from deepresearch.orchestrator.dry_run import show_dry_run as _s
        _s(self, config)
