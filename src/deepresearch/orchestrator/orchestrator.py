"""Orchestrator — Central coordinator for DeepResearch sessions.

Lifecycle:
    IDLE → CONFIGURING → ROUND1 → COLLABORATING → FOLLOWUP
    → REFINING → ROUND2 → COMPILING → OUTPUT → COMPLETE

The Orchestrator manages configuration, model assignment, parallel agent
execution, collaboration, and compilation. It accepts injectable callables
for agent execution, prompting, and scribe compilation, making it fully
testable without real LLM calls.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from collections.abc import Awaitable
from typing import Any, Callable

from rich.console import Console
from rich.prompt import Prompt

from deepresearch.collaboration import CollaborationBus
from deepresearch.config import ConfigError
from deepresearch.models import (
    AgentProfile,
    ClarificationQuery,
    ClarificationResponse,
    Findings,
    FollowUpQuestions,
    IndividualReport,
    ResearchPaper,
    ResearchTopic,
    SessionConfig,
    SharedKnowledge,
)
from deepresearch.output.pdf_generator import PDFGenerator

logger = logging.getLogger(__name__)
console = Console()

# Type alias for injectable callables.
# An AgentFunc is a coroutine that accepts dynamic arguments depending
# on the lifecycle phase (topic, shared knowledge, reports, etc.).
AgentFunc = Callable[..., Any]
PromptFunc = Callable[..., str]

# Maximum session wall-clock time in seconds (30 minutes).
MAX_SESSION_DURATION = 1800


class Orchestrator:
    """Central coordinator for DeepResearch sessions.

    Parameterising the Orchestrator with injectable factories allows:
      - Unit testing with mock agents (no LLM calls)
      - Future real agent implementations (Phase 3+) without changing
        the lifecycle logic
      - Custom prompt strategies for non-interactive (CLI) use
    """

    # Map time-budget keywords to human-readable descriptions.
    TIME_BUDGET_OPTIONS: dict[str, str] = {
        "quick": "Quick (2 minutes — fastest results)",
        "medium": "Standard (5 minutes — balanced)",
        "deep": "Deep (8 minutes — most thorough)",
    }

    # Map time-budget keywords to seconds.
    TIME_BUDGET_SECONDS: dict[str, int] = {
        "quick": 120,
        "medium": 300,
        "deep": 480,
    }

    # Custom time-budget keyword used when --minutes is provided.
    _CUSTOM_BUDGET_KEY = "custom"

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
        """Initialise the Orchestrator.

        Args:
            profiles_path: Override path to agent profiles YAML.
            models_path: Override path to model definitions YAML.
            profiles: Pre-loaded agent profiles (for testing, skips file load).
            model_configs: Pre-loaded model configs (for testing, skips file load).
            prompt_func: Injectable prompt function for interactive choices.
            agent_factory: Factory ``(profile, model_name) -> AgentFunc``.
            scribe_factory: Factory ``() -> AgentFunc`` for the scribe.
            event_bus: Per-session EventBus instance. If None, uses the
                global ``event_bus`` singleton.
        """
        self.profiles_path = profiles_path
        self.models_path = models_path
        self._profiles_override = profiles
        self._model_configs_override = model_configs
        self._prompt = prompt_func or self._default_prompt
        self._agent_factory = agent_factory
        self._scribe_factory = scribe_factory
        self._event_bus = event_bus

        # Initialise model_configs from override so that assign_models()
        # works even when called directly (e.g. in tests) before configure().
        self.model_configs = model_configs or []
        self.session_config: SessionConfig | None = None
        self.state: str = "IDLE"
        self.failed_agents: dict[str, str] = {}
        self.events: list[dict[str, Any]] = []
        self._session_start_time: datetime | None = None
        self._cancel_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Prompt helpers (overridable for testing / non-interactive mode)
    # ------------------------------------------------------------------

    @staticmethod
    def _default_prompt(message: str, **kwargs: Any) -> str:
        """Default interactive prompt via Rich."""
        return Prompt.ask(message, **kwargs)

    def _prompt_time_budget(self) -> str:
        """Interactively ask user for time budget."""
        console.print("\n[bold]Research Depth[/bold]")
        for key, desc in self.TIME_BUDGET_OPTIONS.items():
            console.print(f"  [cyan]{key}[/cyan] — {desc}")
        return self._prompt(
            "Select time budget",
            choices=list(self.TIME_BUDGET_OPTIONS),
            default="medium",
        )

    def _prompt_model_mode(self) -> str:
        """Interactively ask user for model assignment mode."""
        console.print("\n[bold]Model Assignment Mode[/bold]")
        console.print("  [cyan]same[/cyan]   — Use the same model for all agents")
        console.print("  [cyan]random[/cyan]  — Assign models randomly (deterministic per topic)")
        console.print("  [cyan]manual[/cyan]  — Pick a model for each agent individually")
        return self._prompt(
            "Select model mode",
            choices=["same", "random", "manual"],
            default="same",
        )

    def _prompt_for_model(
        self,
        profile: AgentProfile,
        available: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Interactively ask user which model to assign to a profile."""
        console.print(f"\n[bold]{profile.emoji} {profile.name}[/bold] ({profile.id})")
        for i, m in enumerate(available):
            default_mark = " [green](default)[/green]" if m.get("default") else ""
            console.print(f"  [cyan]{i}[/cyan] — {m['id']}{default_mark}")
        idx_str = self._prompt(
            f"Select model for {profile.name}",
            choices=[str(i) for i in range(len(available))],
            default="0",
        )
        return available[int(idx_str)]

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    async def configure(
        self,
        topic_str: str,
        **overrides: Any,
    ) -> SessionConfig:
        """Create a validated SessionConfig for a research session.

        Delegates to :func:`deepresearch.orchestrator.config.configure`.
        """
        from deepresearch.orchestrator.config import configure as _configure_impl
        return await _configure_impl(self, topic_str, **overrides)

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
        from deepresearch.orchestrator.config import assign_models as _assign_models_impl
        return await _assign_models_impl(self, mode, profiles, selected_model=selected_model, agent_models=agent_models)

    @property
    def _topic_seed(self) -> str:
        """Seed string for deterministic random assignment."""
        if self.session_config is not None:
            return self.session_config.topic.question
        return "default_seed"

    # ------------------------------------------------------------------
    # Round Execution (parallel with timeout protection)
    # ------------------------------------------------------------------

    async def run_round(
        self,
        round_num: int,
        agents: dict[str, AgentFunc],
        topic: ResearchTopic,
        shared: SharedKnowledge | None = None,
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
        timeout = self._get_timeout()
        tasks: dict[str, asyncio.Task[Any]] = {}

        for agent_id, agent_fn in agents.items():
            if agent_id in self.failed_agents:
                logger.debug("Skipping failed agent '%s' in round %d", agent_id, round_num)
                continue

            # Check cancellation before launching each agent task.
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Cancel event set — skipping remaining agents in round %d", round_num)
                self._log_event("round_cancelled", round=round_num, agent_id=agent_id)
                break

            # Publish start event BEFORE creating task so the dashboard
            # immediately shows the agent as "running" (🔄).
            agent_model = "unknown"
            if self.session_config and hasattr(self.session_config, "agent_models"):
                agent_model = self.session_config.agent_models.get(agent_id, "unknown")
            self._log_event("agent_start", agent_id=agent_id, round=round_num,
                            model=agent_model, timeout=timeout,
                            agent_state="researching")

            coro = agent_fn(topic, shared) if shared is not None else agent_fn(topic)
            tasks[agent_id] = asyncio.create_task(
                asyncio.wait_for(coro, timeout=timeout),
            )

        results: dict[str, Any] = {}
        for agent_id, task in tasks.items():
            # Check cancellation before awaiting each task result.
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Cancel event set — cancelling remaining tasks in round %d", round_num)
                for t in tasks.values():
                    if not t.done():
                        t.cancel()
                break
            try:
                result = await task
                results[agent_id] = result
                result_size = len(str(result)) if result else 0
                self._log_event("agent_complete", agent_id=agent_id,
                                round=round_num, result_chars=result_size,
                                status="success")
            except asyncio.TimeoutError:
                logger.warning("Agent '%s' timed out in Round %d (timeout=%ds)", agent_id, round_num, timeout)
                self.handle_agent_failure(agent_id, "timeout")
            except Exception as e:
                self.handle_agent_failure(agent_id, str(e))

        return results

    def _get_timeout(self) -> int:
        """Per-agent timeout in seconds based on session time budget."""
        if self.session_config is not None:
            # Half the total budget per round, minimum 30 s.
            return max(30, self.session_config.time_budget_seconds // 2)
        return 60

    # ------------------------------------------------------------------
    # Collaboration — aggregate findings into shared knowledge
    # ------------------------------------------------------------------

    def share_findings(self, round_1_results: dict[str, Findings]) -> SharedKnowledge:
        """Aggregate all Round 1 findings into a SharedKnowledge object.

        Uses basic extraction heuristics for themes, agreements,
        disagreements, and gaps. In Phase 4 these will be replaced with
        LLM-powered extraction for higher quality.
        """
        all_summaries = {aid: f.summary for aid, f in round_1_results.items()}
        all_key_points: list[str] = []
        for f in round_1_results.values():
            all_key_points.extend(f.key_points)

        shared = SharedKnowledge(
            round_number=1,
            all_summaries=all_summaries,
            key_themes=self._extract_themes(round_1_results),
            areas_of_agreement=self._extract_agreements(round_1_results),
            areas_of_disagreement=self._extract_disagreements(round_1_results),
            knowledge_gaps=self._extract_gaps(round_1_results),
        )
        self._log_event("collaboration_phase", shared_agent_count=len(round_1_results))
        return shared

    @staticmethod
    def _extract_themes(results: dict[str, Findings]) -> list[str]:
        """Extract common themes from findings (simple stub — Phase 4 improves)."""
        themes: set[str] = set()
        for f in results.values():
            for kp in f.key_points:
                words = kp.split()[:5]
                if words:
                    themes.add(" ".join(words))
        return list(themes)[:10]

    @staticmethod
    def _extract_agreements(results: dict[str, Findings]) -> list[str]:
        """Extract areas of agreement (stub)."""
        return ["Multiple perspectives identified on the core topic"]

    @staticmethod
    def _extract_disagreements(results: dict[str, Findings]) -> list[str]:
        """Extract areas of disagreement (stub)."""
        return []

    @staticmethod
    def _extract_gaps(results: dict[str, Findings]) -> list[str]:
        """Extract knowledge gaps (stub)."""
        return ["Further research needed for comprehensive understanding"]

    # ------------------------------------------------------------------
    # Follow-up Questions
    # ------------------------------------------------------------------

    async def collect_followup_questions(
        self,
        agents: dict[str, AgentFunc],
        shared: SharedKnowledge,
    ) -> dict[str, FollowUpQuestions]:
        """Each non-failed agent submits questions based on shared knowledge.

        Agent IDs are passed so each agent knows which other agents are
        available for targeted questions.
        """
        timeout = max(30, self._get_timeout() // 2)
        agent_ids = list(agents.keys())
        tasks: dict[str, asyncio.Task[Any]] = {}

        for agent_id, agent_fn in agents.items():
            if agent_id in self.failed_agents:
                continue
            # The dispatch wrapper accepts SharedKnowledge; we inject
            # agent_ids via kwargs so the agent can direct questions.
            async def _call_with_ids(_fn=agent_fn, _ids=agent_ids):
                return await _fn(shared, agent_ids=_ids)

            tasks[agent_id] = asyncio.create_task(
                asyncio.wait_for(_call_with_ids(), timeout=timeout),
            )

        results: dict[str, FollowUpQuestions] = {}
        for agent_id, task in tasks.items():
            try:
                results[agent_id] = await task
            except Exception as e:
                logger.warning("Failed to collect follow-up from %s: %s", agent_id, e)
        return results

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------

    async def collect_reports(
        self,
        agents: dict[str, AgentFunc],
        round_1: dict[str, Findings],
        round_2: dict[str, IndividualReport],
    ) -> dict[str, IndividualReport]:
        """Collect final individual reports from each agent.

        If Round 2 results are available they are returned directly.
        Otherwise, Round 1 findings are converted to IndividualReport
        directly — no extra LLM calls needed.
        """
        if round_2:
            return round_2

        results: dict[str, IndividualReport] = {}
        for agent_id, findings in round_1.items():
            if agent_id in self.failed_agents:
                continue
            results[agent_id] = IndividualReport(
                agent_id=agent_id,
                title=f"Report from {agent_id}",
                perspective_summary=findings.summary,
                key_insights=findings.key_points,
                analysis=findings.raw_response or findings.summary,
                full_text=findings.raw_response or findings.summary,
            )
        return results

    # ------------------------------------------------------------------
    # Compilation & PDF Generation
    # ------------------------------------------------------------------

    async def compile(
        self,
        reports: dict[str, IndividualReport],
        scribe: AgentFunc,
    ) -> ResearchPaper:
        """Call the scribe agent with all reports to produce the final paper.

        The method handles two scribe types:
          - A ``ScribeAgent`` instance (has a ``compile`` method) — calls
            ``.compile(reports, clarification_fn=…)``.
          - A plain async callable (mock/fallback scribe) — calls
            ``scribe(reports)`` directly.

        Falls back to a minimal paper if the scribe fails.
        """
        try:
            # Detect if scribe supports the clarification protocol.
            if hasattr(scribe, "compile"):
                from deepresearch.agents.scribe_agent import ScribeAgent

                if isinstance(scribe, ScribeAgent):
                    async def _scribe_status(status: str) -> None:
                        self._log_event("scribe_clarifying", step=status)
                    paper = await scribe.compile(
                        reports,
                        clarification_fn=self._handle_clarification,
                        status_callback=_scribe_status,
                    )
                else:
                    # Generic object with .compile method.
                    paper = await scribe.compile(reports)
            else:
                # Plain async callable (mock / fallback scribe).
                paper = await scribe(reports)

            self._log_event("scribe_end")
            logger.info("Scribe compilation successful — %d sections", len(paper.sections) if paper.sections else 0)
            return paper
        except Exception as e:
            logger.error("Scribe compilation failed: %s", e, exc_info=True)
            return ResearchPaper(
                title="Research Paper",
                abstract="Compilation failed — partial results available.",
                methodology_note="",
                sections=[],
                synthesis="",
                key_takeaways=[],
                conclusion="",
            )

    async def _handle_clarification(
        self,
        query: ClarificationQuery,
    ) -> ClarificationResponse:
        """Route a scribe's clarification query to the appropriate agent.

        Looks up the agent in ``self._agents`` (built during ``run()``)
        and calls its ``clarify`` method.  If the agent is unavailable
        or fails, returns a default response.
        """
        agent_id = query.agent_id
        agent = self._agents.get(agent_id) if hasattr(self, "_agents") else None

        if agent is None:
            return ClarificationResponse(
                agent_id=agent_id,
                response="Agent unavailable for clarification.",
            )

        try:
            if hasattr(agent, "clarify"):
                return await agent.clarify(query)

            # The agent might be a dispatch wrapper — try calling directly.
            return await agent(query)

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
    # Error Handling
    # ------------------------------------------------------------------

    def handle_agent_failure(self, agent_id: str, error: str) -> None:
        """Log the failure, mark the agent as failed, and continue."""
        self.failed_agents[agent_id] = error
        logger.warning("Agent '%s' failed: %s", agent_id, error)
        self._log_event("agent_failed", agent_id=agent_id, error=error)
        console.print(
            f"  [yellow]⚠ Agent '{agent_id}' failed: {error}[/yellow]"
            " — continuing with remaining agents",
        )

    # ------------------------------------------------------------------
    # Parallel execution helper
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_parallel(
        tasks: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute coroutines in parallel with proper error handling.

        Wraps ``asyncio.gather(return_exceptions=True)``. Failed tasks
        are excluded from the returned dict and logged as warnings.

        Args:
            tasks: ``{name: awaitable}`` mapping.

        Returns:
            ``{name: result}`` — only successful results.
        """
        names = list(tasks.keys())
        cors = list(tasks.values())
        gathered = await asyncio.gather(*cors, return_exceptions=True)

        results: dict[str, Any] = {}
        for name, outcome in zip(names, gathered):
            if isinstance(outcome, Exception):
                logger.warning("Task '%s' failed: %s", name, outcome)
            else:
                results[name] = outcome
        return results

    # ------------------------------------------------------------------
    # Session Lifecycle
    # ------------------------------------------------------------------

    async def run(
        self,
        topic: str,
        **overrides: Any,
    ) -> Path:
        """Run a full research session from topic to output.

        Overrides (passed from CLI args or tests):
            time_budget (str): ``"quick"``, ``"medium"``, ``"deep"``, or ``"custom"``.
            time_budget_seconds (int): Custom time budget in seconds (overrides
                ``time_budget`` when provided, sets budget to ``"custom"``).
            model_mode (str): ``"same"``, ``"random"``, or ``"manual"``.
            selected_model (str | None): Model ID to use for all agents
                when ``model_mode="same"`` (overrides default model).
            agent_models (dict[str, str] | None): Per-agent model mapping
                when ``model_mode="manual"`` (overrides interactive prompts).
            dry_run (bool): If ``True``, validate config and print preview
                without executing any agent calls.
            output_path (str): Path for the output PDF (e.g. ``"report.pdf"``).
            output_dir (str): Deprecated — use ``output_path`` instead.
            agent_factory (callable or ``None``): Per-run factory override.
            scribe_factory (callable or ``None``): Per-run scribe override.
            cancel_event (asyncio.Event | None): When set, the session
                should stop as soon as possible.  Checked before each
                agent task and in LLM retry loops.

        Returns:
            ``Path`` to the output PDF (or placeholder path in dry-run mode).
        """
        self._cancel_event = overrides.get("cancel_event")
        self._session_start_time = datetime.now()
        logger.info("Session started — topic: %s", topic)
        self._log_event("session_start", topic=topic)
        console.print("\n[bold]🚀 DeepeResearch — Multi-Agent Research System[/bold]")
        console.print(f"[yellow]Topic:[/yellow] {topic}")

        config = await self.configure(topic, **overrides)
        logger.info("Config validated — budget=%s, model_mode=%s",
                     config.topic.time_budget, config.topic.model_mode)
        # Resolve output path.
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

        # Resolve per-run factory overrides.
        agent_factory = overrides.get("agent_factory") or self._agent_factory
        scribe_factory = overrides.get("scribe_factory") or self._scribe_factory

        if agent_factory is None:
            raise ConfigError(
                "No agent factory provided. Pass an ``agent_factory`` to the "
                "Orchestrator or implement agent classes (Phase 3+).",
            )

        # Initialise the collaboration bus.
        self.bus = CollaborationBus()
        self.bus.topic = config.topic

        agents = self._build_agents(config, agent_factory)
        self._agents = agents  # Store for clarification routing.
        scribe_cb = self._make_stream_callback("scribe")
        scribe_model = overrides.get("scribe_model") or overrides.get("selected_model")
        scribe = self._build_scribe(
            scribe_factory,
            event_callback=scribe_cb,
            model_name=scribe_model,
        )

        # Propagate cancel_event to the scribe's LLM client.
        if self._cancel_event and hasattr(scribe, 'llm') and scribe.llm is not None:
            scribe.llm.cancel_event = self._cancel_event
        elif self._cancel_event and hasattr(scribe, '__wrapped__'):
            # ScribeAgent instance wrapped by dispatch — try to reach it.
            pass

        # Active agent IDs (excludes failed agents at each step).
        def active_agents() -> list[str]:
            return [aid for aid in agents if aid not in self.failed_agents]

        # ── Session-level timeout ──────────────────────────────────────
        session_timeout = min(
            MAX_SESSION_DURATION,
            config.time_budget_seconds * 4 + 300,  # generous: budget × 4 + 5min grace
        )

        # ── Session-level timeout wrapper ────────────────────────────
        logger.info("Starting _run_session — session_timeout=%ds, agents=%d", session_timeout, len(agents))
        try:
            await asyncio.wait_for(
                self._run_session(
                    agents=agents,
                    scribe=scribe,
                    active_agents=active_agents,
                    config=config,
                    output_path=output_path,
                    agent_factory=agent_factory,
                ),
                timeout=session_timeout,
            )
        except asyncio.TimeoutError:
            self.state = "OUTPUT"
            console.print(
                f"\n[yellow]⚠ Session timed out after {session_timeout}s "
                f"— partial results available[/yellow]"
            )
            live_agents = [aid for aid in agents if aid not in self.failed_agents]
            self._log_event("session_timeout", timeout=session_timeout,
                            running_agents=live_agents,
                            failed_agents=list(self.failed_agents.keys()))

        # ── Generate final output ──────────────────────────────────────
        pdf_path = await self._finalize_output(output_path)
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
        """Inner session execution (wrapped by session-level timeout)."""
        # ── Round 1: Independent Research ──────────────────────────────
        self.state = "ROUND1"
        console.print("\n[bold]Round 1:[/bold] Independent Research")
        console.print(f"  Running {len(agents)} agents in parallel...")
        self._log_event("round_start", round=1)
        round_1_results = await self.run_round(
            1,
            {aid: agents[aid] for aid in active_agents()},
            config.topic,
        )

        logger.info("Round 1 complete — %d/%d agents succeeded", len(round_1_results), len(agents))
        # Publish Round 1 findings to the collaboration bus.
        for agent_id, findings in round_1_results.items():
            await self.bus.publish_round_1(agent_id, findings)

        # ── Save Round 1 findings to files for reuse ───────────────────
        try:
            agents_dir = output_path.parent / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            for agent_id, findings in round_1_results.items():
                if findings is None:
                    continue
                agent_file = agents_dir / f"{agent_id}_round1.json"
                agent_file.write_text(
                    json.dumps({
                        "agent_id": findings.agent_id,
                        "round": findings.round,
                        "summary": findings.summary,
                        "key_points": findings.key_points,
                        "perspective": findings.perspective,
                        "confidence": findings.confidence,
                        "raw_response": findings.raw_response,
                    }, indent=2),
                    encoding="utf-8",
                )
            logger.info("Saved %d Round 1 findings to %s", len(round_1_results), agents_dir)
        except Exception as e:
            logger.warning("Failed to save Round 1 findings: %s", e)

        # ── Collaboration ──────────────────────────────────────────────
        self.state = "COLLABORATING"
        console.print("\n[bold]Collaboration:[/bold] Sharing findings across agents")
        shared = await self.bus.compute_shared_knowledge()
        logger.info("Collaboration complete — shared knowledge from %d agents", len(round_1_results))
        self._log_event("collaboration_phase", shared_agent_count=len(round_1_results))

        # ── Follow-up Questions ────────────────────────────────────────
        self.state = "FOLLOWUP"
        console.print("\n[bold]Follow-up:[/bold] Collecting follow-up questions")
        logger.info("Follow-up: collecting questions from %d agents", len(active_agents()))
        self._log_event("followup_start", active_agents=len(active_agents()))
        followup_results = await self.collect_followup_questions(
            {aid: agents[aid] for aid in active_agents()},
            shared,
        )
        logger.info("Follow-up complete — %d agents responded", len(followup_results))

        # Publish follow-up questions to the collaboration bus.
        qa_questions: dict[str, list[str]] = {}
        qa_targets: dict[str, list[str | None]] = {}
        for agent_id, questions in followup_results.items():
            if isinstance(questions, FollowUpQuestions):
                await self.bus.publish_followup(agent_id, questions.questions)
                if questions.questions:
                    qa_questions[agent_id] = list(questions.questions)
                    qa_targets[agent_id] = list(questions.target_agent_ids or [None] * len(questions.questions))
            else:
                logger.warning(
                    "Unexpected follow-up result type for agent '%s': %s",
                    agent_id,
                    type(questions).__name__,
                )

        # Log follow-up complete with Q&A data for dashboard visualization
        self._log_event(
            "followup_complete",
            results=len(followup_results),
            questions=qa_questions,
            targets=qa_targets,
        )

        # ── Refinement Phase (parallel) ────────────────────────────────
        # Give each agent their follow-up questions and let them refine
        # their findings with an additional web search if needed.
        # This happens BEFORE the Round 2 decision so refined findings
        # are used for both the Round 2 question and final reports.
        self.state = "REFINING"
        console.print("\n[bold]Refinement:[/bold] Agents refining findings from questions")
        self._log_event("refinement_start")

        async def _refine_agent(agent_id: str, followup: FollowUpQuestions):
            if not isinstance(followup, FollowUpQuestions) or not followup.questions:
                return None
            if agent_id in self.failed_agents:
                return None
            # Filter questions by target_agent_ids — only send questions
            # that target this agent or have no specific target.
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
                refined = await asyncio.wait_for(
                    agents[agent_id](targeted_followup),
                    timeout=max(30, self._get_timeout() // 2),
                )
                if refined and isinstance(refined, Findings) and (refined.summary or refined.key_points):
                    return (agent_id, refined)
            except Exception as e:
                logger.warning("Agent '%s' refinement failed: %s", agent_id, e)
            return None

        tasks = [_refine_agent(aid, fu) for aid, fu in followup_results.items()]
        results = await asyncio.gather(*tasks)
        _refined_count = 0
        for result in results:
            if result:
                agent_id, refined = result
                round_1_results[agent_id] = refined
                _refined_count += 1
                logger.info("Agent '%s' refined findings", agent_id)

        if _refined_count:
            console.print(f"  [dim]{_refined_count} agent(s) refined their findings[/dim]")
        self._log_event("refinement_complete", refined_agents=_refined_count)

        # ── Round 2: Refined Research ──────────────────────────────────
        # Dynamic decision: run Round 2 only if there are significant
        # knowledge gaps or low-confidence agents.
        _gap_threshold = 2
        _knowledge_gaps = len(shared.knowledge_gaps) if hasattr(shared, 'knowledge_gaps') else 0
        _disagreements = len(shared.areas_of_disagreement) if hasattr(shared, 'areas_of_disagreement') else 0
        _total_gaps = _knowledge_gaps + _disagreements

        _low_confidence_agents = sum(
            1 for f in round_1_results.values()
            if hasattr(f, 'confidence') and f.confidence < 0.5
        )

        _should_run_round_2 = _total_gaps >= _gap_threshold or _low_confidence_agents > 0

        if config.topic.time_budget in ("quick", self._CUSTOM_BUDGET_KEY) or not _should_run_round_2:
            # Skip Round 2.
            reason = ""
            if config.topic.time_budget == self._CUSTOM_BUDGET_KEY:
                reason = f"Custom mode ({config.time_budget_seconds}s)"
            elif config.topic.time_budget == "quick":
                reason = "Quick mode"
            else:
                reason = f"Sufficient agreement ({_total_gaps} gaps, {_low_confidence_agents} low-confidence agents)"
            console.print(f"\n[bold]{reason}:[/bold] Skipping Round 2")
            self._log_event("round2_skip", budget=config.topic.time_budget,
                            gaps=_total_gaps, low_confidence=_low_confidence_agents)
            round_2_results = {}
        else:
            self.state = "ROUND2"
            console.print(f"\n[bold]Round 2:[/bold] Refined Research with Shared Context "
                          f"({_total_gaps} gaps, {_low_confidence_agents} low-confidence agents)")
            self._log_event("round_start", round=2)
            round_2_results = await self.run_round(
                2,
                {aid: agents[aid] for aid in active_agents()},
                config.topic,
                shared,
            )

            # Publish Round 2 findings to the collaboration bus.
            for agent_id, findings in round_2_results.items():
                await self.bus.publish_round_2(agent_id, findings)

        # ── Collect Reports ────────────────────────────────────────────
        self.state = "COMPILING"
        console.print("\n[bold]Compilation:[/bold] Gathering final reports")
        reports = await self.collect_reports(
            {aid: agents[aid] for aid in active_agents()},
            round_1_results,
            round_2_results,
        )

        # Publish reports to the collaboration bus.
        for agent_id, report in reports.items():
            await self.bus.publish_report(agent_id, report)

        # ── Compile with Scribe ────────────────────────────────────────
        all_reports = await self.bus.get_all_reports()
        report_count = len(all_reports)
        total_chars = sum(len(str(r)) for r in all_reports.values())
        scribe_model = "unknown"
        if self.session_config:
            pass  # scribe model isn't stored in session_config
        self._log_event("scribe_start", report_count=report_count,
                        total_reports_chars=total_chars, model=scribe_model)
        logger.info("Scribe compiling paper from %d reports (%d chars)", report_count, total_chars)
        paper = await self.compile(all_reports, scribe)
        self._current_paper = paper

    async def _finalize_output(self, output_path: Path) -> Path:
        """Generate PDF (or HTML fallback) from compiled paper."""
        if not hasattr(self, "_current_paper") or self._current_paper is None:
            # Fallback: no paper available (timeout before compile).
            self._current_paper = ResearchPaper(
                title="Research Paper",
                abstract="Session ended before compilation — partial results.",
                methodology_note="",
                sections=[],
                synthesis="",
                key_takeaways=[],
                conclusion="",
            )

        self.state = "OUTPUT"
        paper = self._current_paper
        try:
            generator = PDFGenerator()
            pdf_path = generator.generate_pdf(paper, output_path)
            self._log_event("pdf_generated", path=str(pdf_path))
            console.print(f"\n[bold green]✓ PDF generated: {pdf_path}[/bold green]")
        except Exception as exc:
            logger.error("PDF generation failed: %s", exc)
            # Fallback: write HTML only.
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                generator = PDFGenerator()
                html = generator.generate_html_only(paper)
                html_path = output_path.with_suffix(".html")
                html_path.write_text(html, encoding="utf-8")
                pdf_path = html_path
                self._log_event("pdf_generated", path=str(html_path))
                console.print(
                    f"\n[yellow]⚠ PDF generation failed, HTML saved: "
                    f"{html_path}[/yellow]"
                )
            except Exception as html_exc:
                logger.error("HTML fallback also failed: %s", html_exc)
                # Write a minimal text file.
                fallback_path = output_path.with_suffix(".txt")
                fallback_path.write_text(
                    f"Title: {paper.title}\n\nAbstract: {paper.abstract}\n",
                    encoding="utf-8",
                )
                pdf_path = fallback_path
                console.print(
                    f"\n[yellow]⚠ All output formats failed, saved text: "
                    f"{fallback_path}[/yellow]"
                )

        self.state = "COMPLETE"
        self._log_event("session_end")
        agent_count = len(
            self.session_config.agent_profiles if self.session_config else []
        )
        console.print("\n[bold green]✓ Research complete![/bold green]")
        console.print(f"  Output: {pdf_path}")
        console.print(f"  Agents used: {agent_count}")
        if self.failed_agents:
            console.print(f"  [yellow]Failed agents: {len(self.failed_agents)}[/yellow]")
            for aid, err in self.failed_agents.items():
                console.print(f"    [dim]• {aid}: {err}[/dim]")

        self._log_event("pipeline_summary",
            total_agents=agent_count,
            failed_agents=list(self.failed_agents.keys()),
            state_history=[],
            elapsed=round((datetime.now() - self._session_start_time).total_seconds(), 1),
        )

        return Path(pdf_path)

    # ------------------------------------------------------------------
    # Agent / Scribe Construction
    # ------------------------------------------------------------------

    def _make_stream_callback(self, agent_id: str) -> Callable[[dict[str, Any]], Awaitable[None]]:
        """Create an event callback that streams agent output via the event bus.

        The returned async callable accepts stream chunks and publishes them
        as ``agent_output`` events so the dashboard can render live text.

        Also handles ``agent_state`` and ``search`` event types so the
        dashboard shows real-time state badges.
        """
        async def callback(data: dict[str, Any]) -> None:
            if data.get("type") == "stream":
                self._log_event(
                    "agent_output",
                    agent_id=agent_id,
                    text=data.get("text", ""),
                )
            if data.get("type") == "search":
                self._log_event(
                    "agent_output",
                    agent_id=agent_id,
                    text=f"\n[🔍 Searching: {data.get('query', '')}]\n",
                    agent_state="searching",
                )
            if data.get("type") == "agent_state":
                self._log_event(
                    "agent_output",
                    agent_id=agent_id,
                    agent_state=data.get("state", ""),
                    text="",
                )
        return callback

    def _build_agents(
        self,
        config: SessionConfig,
        factory: Callable[[AgentProfile, str], AgentFunc],
    ) -> dict[str, AgentFunc]:
        """Build agent callables via the injected factory.

        Each agent gets a stream callback so that LLM output chunks are
        published as ``agent_output`` events in real time.

        If ``self._cancel_event`` is set, it is propagated to each
        agent's LLM client so that ``cancel_event.is_set()`` is checked
        before every LLM call and retry.
        """
        agents: dict[str, AgentFunc] = {}
        for profile in config.agent_profiles:
            model_name = config.agent_models.get(profile.id, "")
            cb = self._make_stream_callback(profile.id)
            # Pass cancel_event through kwargs if the factory accepts it.
            try:
                agents[profile.id] = factory(
                    profile, model_name,
                    event_callback=cb,
                    cancel_event=getattr(self, '_cancel_event', None),
                )
            except TypeError:
                # Factory doesn't accept cancel_event — fallback.
                agents[profile.id] = factory(profile, model_name, event_callback=cb)
        return agents

    @staticmethod
    def _build_scribe(
        factory: Callable[..., AgentFunc] | None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        model_name: str | None = None,
    ) -> AgentFunc:
        """Build the scribe callable via the injected factory, or use default.

        Args:
            factory: Factory callable. May accept ``event_callback``
                and/or ``model_name`` kwargs.
            event_callback: Optional async callback for streaming output chunks.
            model_name: Optional model override for the scribe.

        Returns:
            An ``AgentFunc`` (async callable) that produces the final paper.
        """
        if factory is not None:
            try:
                return factory(event_callback=event_callback, model_name=model_name)
            except TypeError:
                # The factory may not accept one or both kwargs (e.g. mocks).
                try:
                    return factory(event_callback=event_callback)
                except TypeError:
                    return factory()
        return Orchestrator._default_scribe

    @staticmethod
    async def _default_scribe(reports: dict[str, IndividualReport]) -> ResearchPaper:
        """Default scribe — returns a minimal ResearchPaper stub.

        This is replaced in Phase 5 with a real LLM-based scribe agent.
        """
        agent_count = len(reports)
        return ResearchPaper(
            title="Research Paper",
            abstract=f"Synthesis of {agent_count} agent perspectives.",
            methodology_note="Multi-agent collaborative research methodology.",
            sections=[],
            synthesis="Synthesis placeholder — scribe agent not yet implemented.",
            key_takeaways=["Multi-perspective analysis completed."],
            conclusion="Conclusion placeholder.",
        )

    # ------------------------------------------------------------------
    # Dry-run Mode
    # ------------------------------------------------------------------

    def dry_run(
        self,
        topic_str: str,
        time_budget: str,
        model_mode: str,
        config: SessionConfig | None = None,
    ) -> dict[str, Any]:
        """Preview a session without executing any agents.

        Delegates to :func:`deepresearch.orchestrator.dry_run.dry_run`.
        """
        from deepresearch.orchestrator.dry_run import dry_run as _dry_run_impl
        return _dry_run_impl(self, topic_str, time_budget, model_mode, config=config)

    def _show_dry_run_table(
        self,
        topic_str: str,
        time_budget_label: str,
        time_budget_seconds: int,
        model_mode: str,
        rounds: int,
        agent_assignments: list[dict[str, Any]],
        estimated_cost: float,
        estimated_tokens: int,
    ) -> None:
        """Display dry-run preview as a Rich Table.

        Delegates to :func:`deepresearch.orchestrator.dry_run._show_dry_run_table`.
        """
        from deepresearch.orchestrator.dry_run import _show_dry_run_table as _show_table_impl
        _show_table_impl(
            topic_str, time_budget_label, time_budget_seconds,
            model_mode, rounds, agent_assignments,
            estimated_cost, estimated_tokens,
        )

    def _show_dry_run(self, config: SessionConfig) -> None:
        """Display configuration preview without executing any agents.

        Legacy method — delegates to :func:`deepresearch.orchestrator.dry_run.show_dry_run`.
        """
        from deepresearch.orchestrator.dry_run import show_dry_run as _show_dry_run_impl
        _show_dry_run_impl(self, config)

    # ------------------------------------------------------------------
    # Event Logging
    # ------------------------------------------------------------------

    def _log_event(self, event_type: str, **details: Any) -> None:
        """Record a session event for observability / testing.

        Delegates to :func:`deepresearch.orchestrator.events.log_event`.
        """
        from deepresearch.orchestrator.events import log_event as _log_event_impl
        _log_event_impl(self, event_type, **details)
