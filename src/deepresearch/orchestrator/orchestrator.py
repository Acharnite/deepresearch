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
import time
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
        "quick": "Quick (~3 min — fastest results)",
        "medium": "Standard (~6 min — balanced)",
        "deep": "Deep (~10 min — most thorough)",
    }

    # Map time-budget keywords to seconds.
    TIME_BUDGET_SECONDS: dict[str, int] = {
        "quick": 240,
        "medium": 420,
        "deep": 660,
    }

    # Custom time-budget keyword used when --minutes is provided.
    _CUSTOM_BUDGET_KEY = "custom"

    # Max rounds by budget keyword.
    _MAX_ROUNDS_BY_BUDGET: dict[str, int] = {
        "quick": 2,
        "medium": 3,
        "deep": 5,
        "custom": 4,
    }

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
        self._pdf_underweight: bool = False

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
        console.print(
            "  [cyan]random[/cyan]  — Assign models randomly (deterministic per topic)"
        )
        console.print(
            "  [cyan]manual[/cyan]  — Pick a model for each agent individually"
        )
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
        from deepresearch.orchestrator.config import (
            assign_models as _assign_models_impl,
        )

        return await _assign_models_impl(
            self,
            mode,
            profiles,
            selected_model=selected_model,
            agent_models=agent_models,
        )

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
                logger.debug(
                    "Skipping failed agent '%s' in round %d", agent_id, round_num
                )
                continue

            # Check cancellation before launching each agent task.
            if self._cancel_event and self._cancel_event.is_set():
                logger.info(
                    "Cancel event set — skipping remaining agents in round %d",
                    round_num,
                )
                self._log_event("round_cancelled", round=round_num, agent_id=agent_id)
                break

            # Publish start event BEFORE creating task so the dashboard
            # immediately shows the agent as "running" (🔄).
            agent_model = "unknown"
            if self.session_config and hasattr(self.session_config, "agent_models"):
                agent_model = self.session_config.agent_models.get(agent_id, "unknown")
            self._log_event(
                "agent_start",
                agent_id=agent_id,
                round=round_num,
                model=agent_model,
                timeout=timeout,
                agent_state="researching",
            )

            coro = agent_fn(topic, shared) if shared is not None else agent_fn(topic)
            tasks[agent_id] = asyncio.create_task(
                asyncio.wait_for(coro, timeout=timeout),
            )

        results: dict[str, Any] = {}
        # Track agents that need retry: agent_id -> agent_fn (or None if task failed before we can reuse)
        retry_tasks: dict[str, AgentFunc | None] = {}

        for agent_id, task in tasks.items():
            # Check cancellation before awaiting each task result.
            if self._cancel_event and self._cancel_event.is_set():
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

                # Check for empty/meaningless results
                is_empty = False
                if result is None:
                    is_empty = True
                elif hasattr(result, "summary") and not result.summary:
                    is_empty = True
                elif hasattr(result, "key_points") and not result.key_points:
                    is_empty = True

                if is_empty and result_size < 200:
                    logger.warning(
                        "Agent '%s' returned empty/meaningless result (%d chars), will retry",
                        agent_id,
                        result_size,
                    )
                    # Don't mark as failed yet — add to retry list
                    retry_tasks[agent_id] = agents.get(agent_id)
                else:
                    results[agent_id] = result
                    self._log_event(
                        "agent_complete",
                        agent_id=agent_id,
                        round=round_num,
                        result_chars=result_size,
                        status="success",
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "Agent '%s' timed out in Round %d (timeout=%ds), will retry",
                    agent_id,
                    round_num,
                    timeout,
                )
                retry_tasks[agent_id] = agents.get(agent_id)
            except Exception as e:
                logger.warning(
                    "Agent '%s' failed in Round %d: %s, will retry",
                    agent_id,
                    round_num,
                    e,
                )
                retry_tasks[agent_id] = agents.get(agent_id)

        # ── Retry failed agents once ────────────────────────────────
        for agent_id in list(retry_tasks.keys()):
            agent_fn = retry_tasks[agent_id]
            if agent_fn is None or agent_id in self.failed_agents:
                # Already marked as failed by a concurrent path — skip
                if agent_fn is not None and agent_id not in self.failed_agents:
                    # Agent fn available but task failed catastrophically — still mark
                    self.handle_agent_failure(agent_id, "retry_unavailable")
                continue

            logger.info("Retrying agent '%s' (attempt 2/2)", agent_id)
            self._log_event("agent_retry", agent_id=agent_id, round=round_num)

            # Publish retry start event so the dashboard shows "Retrying..."
            self._log_event(
                "agent_start",
                agent_id=agent_id,
                round=round_num,
                model="unknown",
                timeout=timeout,
                agent_state="retrying",
            )

            try:
                coro = (
                    agent_fn(topic, shared)
                    if shared is not None
                    else agent_fn(topic)
                )
                result = await asyncio.wait_for(coro, timeout=timeout)
                result_size = len(str(result)) if result else 0

                # Check result quality again
                is_empty = False
                if result is None:
                    is_empty = True
                elif hasattr(result, "summary") and not result.summary:
                    is_empty = True
                elif hasattr(result, "key_points") and not result.key_points:
                    is_empty = True

                if is_empty and result_size < 200:
                    self.handle_agent_failure(agent_id, "empty_result (retry failed)")
                else:
                    results[agent_id] = result
                    self._log_event(
                        "agent_complete",
                        agent_id=agent_id,
                        round=round_num,
                        result_chars=result_size,
                        status="success",
                        attempt=2,
                    )
                    logger.info("Agent '%s' succeeded on retry", agent_id)
            except asyncio.TimeoutError:
                self.handle_agent_failure(agent_id, "timeout (retry failed)")
            except Exception as e:
                self.handle_agent_failure(agent_id, f"{e} (retry failed)")

        return results

    def _get_timeout(self) -> int:
        """Per-agent timeout based on session budget, rounds, and scribe reservation.

        Scribe gets 25% of budget (min 60s). Agents split the remaining 75%.
        """
        if self.session_config is None:
            return 120
        b = self.session_config.time_budget_seconds
        m = self.session_config.max_rounds
        scribe_budget = max(60, int(b * 0.25))
        agent_budget = b - scribe_budget
        per_round = max(90, int(agent_budget / m))
        return per_round

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
    # Convergence Detection
    # ------------------------------------------------------------------

    @staticmethod
    def _total_gaps(shared: SharedKnowledge) -> int:
        """Count total gaps (knowledge_gaps + disagreements)."""
        return len(shared.knowledge_gaps) + len(shared.areas_of_disagreement)

    @staticmethod
    def _compute_gap_delta(
        round_history: list[SharedKnowledge],
    ) -> float:
        """Compute gap delta between last two rounds.

        Positive value = gaps decreasing (progress).
        Negative or zero = stagnation (should stop).

        Requires 2 consecutive rounds of non-decreasing gaps to trigger.
        Returns -1.0 if not enough data to decide.
        """
        if len(round_history) < 3:
            return -1.0  # Not enough data, continue

        d1 = Orchestrator._total_gaps(round_history[-2]) - Orchestrator._total_gaps(
            round_history[-1]
        )
        d2 = Orchestrator._total_gaps(round_history[-3]) - Orchestrator._total_gaps(
            round_history[-2]
        )
        # Only stop if 2 consecutive non-decreasing gap deltas
        return d1 if d1 <= 0 and d2 <= 0 else -1.0

    @staticmethod
    def _diminishing_returns(
        round_history: list[SharedKnowledge],
    ) -> bool:
        """Detect diminishing returns: 2 consecutive non-decreasing gap deltas."""
        if len(round_history) < 3:
            return False

        d1 = Orchestrator._total_gaps(round_history[-2]) - Orchestrator._total_gaps(
            round_history[-1]
        )
        d2 = Orchestrator._total_gaps(round_history[-3]) - Orchestrator._total_gaps(
            round_history[-2]
        )
        return d1 <= 0 and d2 <= 0

    @staticmethod
    def _converged_by_confidence(
        round_history: list[SharedKnowledge],
    ) -> bool:
        """Check if confidence has converged across agents.

        Returns True when mean confidence >= 0.7 for 2+ rounds.
        Note: We don't track per-agent confidence in SharedKnowledge,
        so this is a stub that returns False for now.
        The convergence is detected via gap delta instead.
        """
        return False  # Stub — confidence tracking requires per-agent data in SharedKnowledge

    async def _should_continue(
        self,
        round_num: int,
        round_history: list[SharedKnowledge],
        start_time: float,
    ) -> bool:
        """Evaluate whether to continue with another research round.

        Priority order:
        1. Cancel event — user-initiated cancellation
        2. Emergency timeout (30 min absolute max — safety net only)
        3. Max rounds — hard safety cap
        4. Trend convergence — gaps no longer decreasing
        5. Diminishing returns — 2 consecutive non-decreasing gap deltas
        6. Confidence convergence
        """
        # 1. Cancel event
        if self._cancel_event and self._cancel_event.is_set():
            logger.info("Cancel event set — stopping rounds")
            return False

        # 2. Emergency timeout (30 min absolute max — safety net only)
        if time.monotonic() - start_time > MAX_SESSION_DURATION:
            logger.warning("Emergency timeout (30 min) reached — stopping")
            return False

        # 3. Max rounds — hard safety cap
        if self.session_config is not None:
            if round_num >= self.session_config.max_rounds:
                logger.info(
                    "Max rounds reached (%d) — stopping", self.session_config.max_rounds
                )
                return False

        # 4. Trend convergence — gaps no longer decreasing
        gaps = self._compute_gap_delta(round_history)
        if gaps is not None and gaps >= 0:
            logger.info("Gap delta %.2f >= 0 — convergence detected, stopping", gaps)
            return False

        # 5. Diminishing returns — 2 consecutive non-decreasing gap deltas
        if self._diminishing_returns(round_history):
            logger.info("Diminishing returns detected — stopping")
            return False

        # 6. Confidence convergence
        if self._converged_by_confidence(round_history):
            logger.info("Confidence convergence detected — stopping")
            return False

        return True

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

        Note: With the new round loop, this method is called less
        frequently — the loop handles R2+ report collection inline.
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

    async def collect_reports_variable(
        self,
        agents: dict[str, AgentFunc],
        round_results: dict[int, dict[str, Any]],
    ) -> dict[str, IndividualReport]:
        """Collect reports from variable-length round results.

        Args:
            agents: Agent callables.
            round_results: {round_num: {agent_id: result}} — results may be
                Findings (R1) or IndividualReport (R2+ wrapped by dispatch).

        Returns:
            Mapping of agent_id → IndividualReport for all active agents.
        """
        latest_round = max(round_results.keys()) if round_results else 1
        results: dict[str, IndividualReport] = {}
        for agent_id in agents:
            if agent_id in self.failed_agents:
                continue
            latest = round_results.get(latest_round, {}).get(agent_id)
            if isinstance(latest, IndividualReport):
                results[agent_id] = latest
            elif isinstance(latest, Findings):
                results[agent_id] = IndividualReport(
                    agent_id=agent_id,
                    title=f"Report from {agent_id}",
                    perspective_summary=latest.summary,
                    key_insights=latest.key_points,
                    analysis=latest.raw_response or latest.summary,
                    full_text=latest.raw_response or latest.summary,
                )
        return results

    # ------------------------------------------------------------------
    # Compilation & PDF Generation
    # ------------------------------------------------------------------

    async def compile(
        self,
        reports: dict[str, IndividualReport],
        scribe: AgentFunc,
        topic: str = "",
    ) -> ResearchPaper:
        """Call the scribe agent with all reports to produce the final paper.

        The method handles two scribe types:
          - A ``ScribeAgent`` instance (has a ``compile`` method) — calls
            ``.compile(reports, clarification_fn=…)``.
          - A plain async callable (mock/fallback scribe) — calls
            ``scribe(reports)`` directly.

        Falls back to a minimal paper if the scribe fails.

        Args:
            reports: Mapping of agent_id → IndividualReport from every agent.
            scribe: The scribe agent callable or ScribeAgent instance.
            topic: The original research topic string (optional).
        """
        # Determine output language from session config.
        output_language = "English"
        if self.session_config:
            output_language = getattr(self.session_config, "output_language", "English")

        try:
            # Detect if scribe supports the clarification protocol.
            if hasattr(scribe, "compile"):
                from deepresearch.agents.scribe_agent import ScribeAgent

                if isinstance(scribe, ScribeAgent):

                    async def _scribe_status(status: str) -> None:
                        # Emit CLARIFYING state when scribe enters clarification protocol
                        if status in ("identifying_claims",) or status.startswith("asking_agent:"):
                            if self.state != "CLARIFYING":
                                self.state = "CLARIFYING"
                        self._log_event("scribe_clarifying", step=status)

                    paper = await scribe.compile(
                        reports,
                        topic=topic,
                        clarification_fn=self._handle_clarification,
                        status_callback=_scribe_status,
                        language=output_language,
                    )
                else:
                    # Generic object with .compile method.
                    paper = await scribe.compile(reports)
            else:
                # Plain async callable (mock / fallback scribe).
                paper = await scribe(reports)

            self._log_event("scribe_end")
            logger.info(
                "Scribe compilation successful — %d sections",
                len(paper.sections) if paper.sections else 0,
            )
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
        logger.info(
            "Config validated — budget=%s, model_mode=%s",
            config.topic.time_budget,
            config.topic.model_mode,
        )
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
        if self._cancel_event and hasattr(scribe, "llm") and scribe.llm is not None:
            scribe.llm.cancel_event = self._cancel_event
        elif self._cancel_event and hasattr(scribe, "__wrapped__"):
            # ScribeAgent instance wrapped by dispatch — try to reach it.
            pass

        # Active agent IDs (excludes failed agents at each step).
        def active_agents() -> list[str]:
            return [aid for aid in agents if aid not in self.failed_agents]

        # ── Session start ────────────────────────────────────────────────
        logger.info(
            "Starting _run_session — agents=%d",
            len(agents),
        )
        await self._run_session(
            agents=agents,
            scribe=scribe,
            active_agents=active_agents,
            config=config,
            output_path=output_path,
            agent_factory=agent_factory,
        )

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
        """Inner session execution (wrapped by session-level timeout).

        Round flow:
          R1 → Collab → Followup → Refinement → (compute shared) → check converge
          R2 → (compute shared) → check converge
          R3 → ...
        """
        start_time = time.monotonic()
        round_num = 1
        round_results: dict[int, dict[str, Any]] = {}
        round_history: list[SharedKnowledge] = []
        latest_shared: SharedKnowledge | None = None

        while round_num <= (config.max_rounds if config.max_rounds else 4):
            # ── Cancel check ──────────────────────────────────────────
            if self._cancel_event and self._cancel_event.is_set():
                logger.info("Cancel event set — aborting round loop")
                break

            # ── Time budget check ────────────────────────────────────
            if time.monotonic() - start_time > min(
                MAX_SESSION_DURATION, config.time_budget_seconds
            ):
                logger.info("Time budget exceeded — stopping after round %d", round_num - 1)
                break

            # ── Run round ─────────────────────────────────────────────
            self.state = f"ROUND{round_num}"
            console.print(
                f"\n[bold]Round {round_num}:[/bold] "
                + ("Independent Research" if round_num == 1 else "Refined Research")
            )
            self._log_event("round_start", round=round_num)

            if round_num == 1:
                results = await self.run_round(
                    1,
                    {aid: agents[aid] for aid in active_agents()},
                    config.topic,
                )
            elif round_num == 2:
                assert latest_shared is not None
                results = await self.run_round(
                    round_num,
                    {aid: agents[aid] for aid in active_agents()},
                    config.topic,
                    latest_shared,
                )
            else:
                # R3+ — dispatch via (topic, shared, round_num, prev_findings)
                assert latest_shared is not None
                prev_round = round_results.get(round_num - 1, {})
                results = await self._run_round_n(
                    round_num,
                    {aid: agents[aid] for aid in active_agents()},
                    config.topic,
                    latest_shared,
                    prev_round,
                )

            round_results[round_num] = results

            # ── Check if ALL agents failed — stop research ──────────
            if not results:
                logger.error(
                    "ALL agents failed in round %d — stopping research",
                    round_num,
                )
                self._log_event("all_agents_failed", round=round_num)
                console.print("[red]All agents failed — stopping research[/red]")
                break

            logger.info(
                "Round %d complete — %d/%d agents succeeded",
                round_num,
                len(results),
                len(agents),
            )

            # Publish round findings to the collaboration bus.
            for agent_id, findings in results.items():
                await self.bus.publish_round(agent_id, round_num, findings)

            # Save R1 findings to files.
            if round_num == 1:
                self._save_round_findings(results, output_path, round_num)

            # ── Collab / Followup / Refinement (once after R1) ────────
            if round_num == 1:
                # Collaboration phase
                self.state = "COLLABORATING"
                console.print(
                    "\n[bold]Collaboration:[/bold] Sharing findings across agents"
                )
                latest_shared = await self.bus.compute_shared_knowledge()
                round_history.append(latest_shared)
                self._log_event(
                    "collaboration_phase", shared_agent_count=len(results)
                )

                # Follow-up questions
                self.state = "FOLLOWUP"
                console.print(
                    "\n[bold]Follow-up:[/bold] Collecting follow-up questions"
                )
                self._log_event("followup_start", active_agents=len(active_agents()))
                followup_results = await self.collect_followup_questions(
                    {aid: agents[aid] for aid in active_agents()},
                    latest_shared,
                )
                # Build questions + targets dicts for the SSE event.
                # Replace None targets with "All" so the frontend never
                # sees raw JSON null values.
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
                self._log_event(
                    "followup_complete",
                    results=len(followup_results),
                    questions=questions_dict,
                    targets=targets_dict,
                )

                # Refinement phase
                self.state = "REFINING"
                console.print(
                    "\n[bold]Refinement:[/bold] Agents refining findings"
                )
                self._log_event("refinement_start")
                refined = await self._run_refinement(
                    agents, followup_results, active_agents
                )
                for agent_id, refined_findings in refined.items():
                    results[agent_id] = refined_findings
                self._log_event("refinement_complete", refined_agents=len(refined))

            # ── Compute shared knowledge after EVERY round ─────────────
            # For R2+ (not R1 which did collab above), re-compute shared
            # knowledge from the latest bus data.
            if round_num > 1:
                latest_shared = await self.bus.compute_shared_knowledge()
                if latest_shared:
                    latest_shared.round_number = round_num
                    latest_shared.round_history = [s for s in round_history]
                    round_history.append(latest_shared)

            # ── Check convergence ──────────────────────────────────────
            if not await self._should_continue(round_num + 1, round_history, start_time):
                logger.info("Convergence check: stopping after round %d", round_num)
                self._log_event("round_skip", round=round_num, reason="convergence")
                break

            round_num += 1

        # ── Collect Reports ────────────────────────────────────────────
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
                    agent_id=agent_id,
                    title=f"Report from {agent_id}",
                    perspective_summary=latest_result.summary,
                    key_insights=latest_result.key_points,
                    analysis=latest_result.raw_response or latest_result.summary,
                    full_text=latest_result.raw_response or latest_result.summary,
                )

        for agent_id, report in reports.items():
            await self.bus.publish_report(agent_id, report)

        # ── Compile with Scribe ────────────────────────────────────────
        all_reports = await self.bus.get_all_reports()
        self._log_event(
            "scribe_start",
            report_count=len(all_reports),
            total_reports_chars=sum(len(str(r)) for r in all_reports.values()),
            model="unknown",
        )
        paper = await self.compile(all_reports, scribe, topic=config.topic.question)
        self._current_paper = paper

    async def _run_round_n(
        self,
        round_num: int,
        agents: dict[str, AgentFunc],
        topic: ResearchTopic,
        shared: SharedKnowledge,
        prev_round: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a Round N (N >= 3) for all agents.

        Dispatches with (topic, shared, round_num, prev_findings) so the
        registry routes to research_round_n.
        """
        timeout = self._get_timeout()
        tasks: dict[str, asyncio.Task[Any]] = {}
        for agent_id, agent_fn in agents.items():
            if agent_id in self.failed_agents:
                continue
            if self._cancel_event and self._cancel_event.is_set():
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

            self._log_event(
                "agent_start",
                agent_id=agent_id,
                round=round_num,
                model=self.session_config.agent_models.get(agent_id, "unknown")
                if self.session_config
                else "unknown",
                timeout=timeout,
                agent_state="researching",
            )

            coro = agent_fn(topic, shared, round_num, prev_findings)
            tasks[agent_id] = asyncio.create_task(
                asyncio.wait_for(coro, timeout=timeout),
            )

        results: dict[str, Any] = {}
        retry_tasks: dict[str, AgentFunc | None] = {}

        for agent_id, task in tasks.items():
            try:
                result = await task
                result_size = len(str(result)) if result else 0

                # Check for empty/meaningless results
                is_empty = False
                if result is None:
                    is_empty = True
                elif hasattr(result, "summary") and not result.summary:
                    is_empty = True
                elif hasattr(result, "key_points") and not result.key_points:
                    is_empty = True

                if is_empty and result_size < 200:
                    logger.warning(
                        "Agent '%s' returned empty result in round %d, will retry",
                        agent_id,
                        round_num,
                    )
                    retry_tasks[agent_id] = agents.get(agent_id)
                else:
                    results[agent_id] = result
                    self._log_event(
                        "agent_complete",
                        agent_id=agent_id,
                        round=round_num,
                        result_chars=result_size,
                        status="success",
                    )
            except asyncio.TimeoutError:
                logger.warning(
                    "Agent '%s' timed out in Round %d (timeout=%ds), will retry",
                    agent_id,
                    round_num,
                    timeout,
                )
                retry_tasks[agent_id] = agents.get(agent_id)
            except Exception as e:
                logger.warning(
                    "Agent '%s' failed in Round %d: %s, will retry",
                    agent_id,
                    round_num,
                    e,
                )
                retry_tasks[agent_id] = agents.get(agent_id)

        # ── Retry failed agents once ────────────────────────────────
        for agent_id in list(retry_tasks.keys()):
            agent_fn = retry_tasks[agent_id]
            if agent_fn is None or agent_id in self.failed_agents:
                if agent_fn is not None and agent_id not in self.failed_agents:
                    self.handle_agent_failure(agent_id, "retry_unavailable")
                continue

            logger.info("Retrying agent '%s' (attempt 2/2)", agent_id)
            self._log_event("agent_retry", agent_id=agent_id, round=round_num)

            prev_findings = prev_round.get(agent_id)
            if prev_findings is None:
                self.handle_agent_failure(agent_id, "no_previous_findings (retry)")
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

            self._log_event(
                "agent_start",
                agent_id=agent_id,
                round=round_num,
                model="unknown",
                timeout=timeout,
                agent_state="retrying",
            )

            try:
                coro = agent_fn(topic, shared, round_num, prev_findings)
                result = await asyncio.wait_for(coro, timeout=timeout)
                result_size = len(str(result)) if result else 0

                is_empty = False
                if result is None:
                    is_empty = True
                elif hasattr(result, "summary") and not result.summary:
                    is_empty = True
                elif hasattr(result, "key_points") and not result.key_points:
                    is_empty = True

                if is_empty and result_size < 200:
                    self.handle_agent_failure(agent_id, "empty_result (retry failed)")
                else:
                    results[agent_id] = result
                    self._log_event(
                        "agent_complete",
                        agent_id=agent_id,
                        round=round_num,
                        result_chars=result_size,
                        status="success",
                        attempt=2,
                    )
                    logger.info("Agent '%s' succeeded on retry", agent_id)
            except asyncio.TimeoutError:
                self.handle_agent_failure(agent_id, "timeout (retry failed)")
            except Exception as e:
                self.handle_agent_failure(agent_id, f"{e} (retry failed)")

        return results

    async def _run_refinement(
        self,
        agents: dict[str, AgentFunc],
        followup_results: dict[str, FollowUpQuestions],
        active_agents: Callable[[], list[str]],
    ) -> dict[str, Findings]:
        """Run refinement phase for all agents based on follow-up questions."""
        refined: dict[str, Findings] = {}

        async def _refine_agent(agent_id: str, followup: FollowUpQuestions):
            if not isinstance(followup, FollowUpQuestions) or not followup.questions:
                return None
            if agent_id in self.failed_agents:
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
                    agents[agent_id](targeted_followup),
                    timeout=max(30, self._get_timeout() // 2),
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

    async def _compute_round_shared_knowledge(
        self,
        round_num: int,
        results: dict[str, Any],
        round_history: list[SharedKnowledge],
    ) -> SharedKnowledge | None:
        """Compute SharedKnowledge for a given round's results.

        For R2+ rounds, the bus receives findings and computes shared knowledge.
        """
        # Update the bus with this round's findings
        for agent_id, findings in results.items():
            if isinstance(findings, Findings):
                await self.bus.publish_round(agent_id, round_num, findings)

        # Re-compute shared knowledge from ALL accumulated findings
        shared = await self.bus.compute_shared_knowledge()
        if shared:
            shared.round_number = round_num
            shared.round_history = [s for s in round_history]
        return shared

    def _save_round_findings(
        self,
        results: dict[str, Any],
        output_path: Path,
        round_num: int,
    ) -> None:
        """Save round findings to JSON files for reuse."""
        try:
            agents_dir = output_path.parent / "agents"
            agents_dir.mkdir(parents=True, exist_ok=True)
            for agent_id, findings in results.items():
                if findings is None:
                    continue
                agent_file = agents_dir / f"{agent_id}_round{round_num}.json"
                agent_file.write_text(
                    json.dumps(
                        {
                            "agent_id": getattr(findings, "agent_id", agent_id),
                            "round": round_num,
                            "summary": getattr(findings, "summary", ""),
                            "key_points": getattr(findings, "key_points", []),
                            "perspective": getattr(findings, "perspective", ""),
                            "confidence": getattr(findings, "confidence", 0.5),
                            "raw_response": getattr(findings, "raw_response", None),
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            logger.info(
                "Saved %d round %d findings to %s",
                len(results),
                round_num,
                agents_dir,
            )
        except Exception as e:
            logger.warning("Failed to save round %d findings: %s", round_num, e)

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
        # Determine output language for PDF font selection.
        output_language = "English"
        if self.session_config:
            output_language = getattr(self.session_config, "output_language", "English")
        try:
            generator = PDFGenerator()
            pdf_path = generator.generate_pdf(paper, output_path, language=output_language)
            self._log_event("pdf_generated", path=str(pdf_path))
            console.print(f"\n[bold green]✓ PDF generated: {pdf_path}[/bold green]")
            # Verify PDF size — mark as underweight if < 12KB
            PDF_MIN_HEALTHY_BYTES = 20_000
            try:
                pdf_size = output_path.stat().st_size
                if pdf_size < PDF_MIN_HEALTHY_BYTES:
                    logger.warning(
                        "PDF too small (%d bytes) — marking as underweight", pdf_size
                    )
                    self._pdf_underweight = True
                    self._log_event(
                        "pdf_underweight",
                        size=pdf_size,
                        threshold=PDF_MIN_HEALTHY_BYTES,
                    )
                else:
                    self._pdf_underweight = False
            except OSError:
                self._pdf_underweight = True
        except Exception as exc:
            logger.error("PDF generation failed: %s", exc)
            # Fallback: write HTML only.
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                generator = PDFGenerator()
                html = generator.generate_html_only(paper, language=output_language)
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
            console.print(
                f"  [yellow]Failed agents: {len(self.failed_agents)}[/yellow]"
            )
            for aid, err in self.failed_agents.items():
                console.print(f"    [dim]• {aid}: {err}[/dim]")

        self._log_event(
            "pipeline_summary",
            total_agents=agent_count,
            failed_agents=list(self.failed_agents.keys()),
            state_history=[],
            elapsed=round(
                (datetime.now() - self._session_start_time).total_seconds(), 1
            ),
        )

        return Path(pdf_path)

    # ------------------------------------------------------------------
    # Agent / Scribe Construction
    # ------------------------------------------------------------------

    def _make_stream_callback(
        self, agent_id: str
    ) -> Callable[[dict[str, Any]], Awaitable[None]]:
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
                    profile,
                    model_name,
                    event_callback=cb,
                    cancel_event=getattr(self, "_cancel_event", None),
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
        from deepresearch.orchestrator.dry_run import (
            _show_dry_run_table as _show_table_impl,
        )

        _show_table_impl(
            topic_str,
            time_budget_label,
            time_budget_seconds,
            model_mode,
            rounds,
            agent_assignments,
            estimated_cost,
            estimated_tokens,
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
