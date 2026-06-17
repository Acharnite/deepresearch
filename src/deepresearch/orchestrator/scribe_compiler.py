"""Scribe compilation, output generation, and prompt helpers.

Extracted from the Orchestrator god class — all method bodies are preserved
as-is with only ``self.xxx`` → ``self._orch.xxx`` adjustments for fields
that remain on the Orchestrator.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from rich.console import Console
from rich.prompt import Prompt

from deepresearch.constants import PDF_MIN_HEALTHY_BYTES
from deepresearch.models import (
    IndividualReport,
    ResearchPaper,
    SessionConfig,
)
from deepresearch.observability.tracing import tracer

logger = logging.getLogger(__name__)
console = Console()


class ScribeCompiler:
    """Scribe compilation, PDF generation, stream callbacks, and prompt helpers.

    Holds a back-reference to the ``Orchestrator`` (``self._orch``) for
    shared state like ``session_config``, ``_cancel_event``, and
    ``failed_agents``.
    """

    # Map time-budget keywords to human-readable descriptions.
    TIME_BUDGET_OPTIONS: dict[str, str] = {
        "quick": "Quick (~3 min — fastest results)",
        "medium": "Standard (~6 min — balanced)",
        "deep": "Deep (~10 min — most thorough)",
    }

    # Custom time-budget keyword used when --minutes is provided.
    _CUSTOM_BUDGET_KEY = "custom"

    def __init__(self, orch: Any, prompt_func: Callable[..., str] | None = None) -> None:
        self._orch = orch
        self._event_bus = orch._event_bus
        self._prompt = prompt_func or self._default_prompt

    # ------------------------------------------------------------------
    # Helper: convenience access to config
    # ------------------------------------------------------------------

    @property
    def _config(self) -> SessionConfig | None:
        return self._orch.session_config

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
        profile: Any,
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
    # Compilation & PDF Generation
    # ------------------------------------------------------------------

    async def compile(
        self,
        reports: dict[str, IndividualReport],
        scribe: Any,
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
        with tracer.start_as_current_span(
            "scribe.compile",
            attributes={
                "report.count": len(reports),
            },
        ) as _:
            # Determine output language from session config.
            output_language = "English"
            if self._config:
                output_language = getattr(self._config, "output_language", "English")

            try:
                # Detect if scribe supports the clarification protocol.
                if hasattr(scribe, "compile"):
                    from deepresearch.agents.scribe_agent import ScribeAgent

                    if isinstance(scribe, ScribeAgent):

                        async def _scribe_status(status: str) -> None:
                            # Emit CLARIFYING state when scribe enters clarification protocol
                            if status in ("identifying_claims",) or status.startswith("asking_agent:"):
                                if self._orch.state != "CLARIFYING":
                                    self._orch.state = "CLARIFYING"
                            if self._event_bus:
                                await self._event_bus.publish(
                                    {"event_type": "scribe_clarifying", "step": status}
                                )

                        paper = await scribe.compile(
                            reports,
                            topic=topic,
                            clarification_fn=self._orch.round_runner._handle_clarification,
                            status_callback=_scribe_status,
                            language=output_language,
                        )
                    else:
                        # Generic object with .compile method.
                        paper = await scribe.compile(reports)
                else:
                    # Plain async callable (mock / fallback scribe).
                    paper = await scribe(reports)

                if self._event_bus:
                    await self._event_bus.publish({"event_type": "scribe_end"})
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

    # ------------------------------------------------------------------
    # Scribe / Agent Construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_scribe(
        factory: Callable[..., Any] | None,
        event_callback: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        model_name: str | None = None,
    ) -> Any:
        """Build the scribe callable via the injected factory, or use default.

        Args:
            factory: Factory callable. May accept ``event_callback``
                and/or ``model_name`` kwargs.
            event_callback: Optional async callback for streaming output chunks.
            model_name: Optional model override for the scribe.

        Returns:
            An async callable that produces the final paper.
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
        return ScribeCompiler._default_scribe

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
    # Stream callbacks
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
            if self._event_bus is None:
                return
            if data.get("type") == "stream":
                await self._event_bus.publish(
                    {
                        "event_type": "agent_output",
                        "agent_id": agent_id,
                        "text": data.get("text", ""),
                    }
                )
            if data.get("type") == "search":
                await self._event_bus.publish(
                    {
                        "event_type": "agent_output",
                        "agent_id": agent_id,
                        "text": f"\n[🔍 Searching: {data.get('query', '')}]\n",
                        "agent_state": "searching",
                    }
                )
            if data.get("type") == "agent_state":
                await self._event_bus.publish(
                    {
                        "event_type": "agent_output",
                        "agent_id": agent_id,
                        "agent_state": data.get("state", ""),
                        "text": "",
                    }
                )

        return callback

    # ------------------------------------------------------------------
    # Output Finalization
    # ------------------------------------------------------------------

    async def _finalize_output(self, output_path: Path) -> Path:
        """Generate PDF (or HTML fallback) from compiled paper."""
        if not hasattr(self._orch, "_current_paper") or self._orch._current_paper is None:
            # Fallback: no paper available (timeout before compile).
            self._orch._current_paper = ResearchPaper(
                title="Research Paper",
                abstract="Session ended before compilation — partial results.",
                methodology_note="",
                sections=[],
                synthesis="",
                key_takeaways=[],
                conclusion="",
            )

        self._orch.state = "OUTPUT"
        paper = self._orch._current_paper
        # Determine output language for PDF font selection.
        output_language = "English"
        if self._config:
            output_language = getattr(self._config, "output_language", "English")
        try:
            from deepresearch.output.pdf_generator import PDFGenerator

            generator = PDFGenerator()
            pdf_path = generator.generate_pdf(paper, output_path, language=output_language)
            if self._event_bus:
                await self._event_bus.publish({"event_type": "pdf_generated", "path": str(pdf_path)})
            console.print(f"\n[bold green]✓ PDF generated: {pdf_path}[/bold green]")
            # Verify PDF size — mark as underweight if below threshold
            try:
                pdf_size = output_path.stat().st_size
                if pdf_size < PDF_MIN_HEALTHY_BYTES:
                    logger.warning(
                        "PDF too small (%d bytes) — marking as underweight", pdf_size
                    )
                    self._orch._pdf_underweight = True
                    if self._event_bus:
                        await self._event_bus.publish(
                            {
                                "event_type": "pdf_underweight",
                                "size": pdf_size,
                                "threshold": PDF_MIN_HEALTHY_BYTES,
                            }
                        )
                else:
                    self._orch._pdf_underweight = False
            except OSError:
                self._orch._pdf_underweight = True
        except Exception as exc:
            logger.error("PDF generation failed: %s", exc)
            # Fallback: write HTML only.
            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                from deepresearch.output.pdf_generator import PDFGenerator

                generator = PDFGenerator()
                html = generator.generate_html_only(paper, language=output_language)
                html_path = output_path.with_suffix(".html")
                html_path.write_text(html, encoding="utf-8")
                pdf_path = html_path
                if self._event_bus:
                    await self._event_bus.publish({"event_type": "pdf_generated", "path": str(html_path)})
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

        self._orch.state = "COMPLETE"
        if self._event_bus:
            await self._event_bus.publish({"event_type": "session_end"})
        agent_count = len(
            self._config.agent_profiles if self._config else []
        )
        console.print("\n[bold green]✓ Research complete![/bold green]")
        console.print(f"  Output: {pdf_path}")
        console.print(f"  Agents used: {agent_count}")
        if self._orch.failed_agents:
            console.print(
                f"  [yellow]Failed agents: {len(self._orch.failed_agents)}[/yellow]"
            )
            for aid, err in self._orch.failed_agents.items():
                console.print(f"    [dim]• {aid}: {err}[/dim]")

        if self._event_bus:
            await self._event_bus.publish(
                {
                    "event_type": "pipeline_summary",
                    "total_agents": agent_count,
                    "failed_agents": list(self._orch.failed_agents.keys()),
                    "state_history": [],
                    "elapsed": round(
                        (datetime.now() - self._orch._session_start_time).total_seconds(), 1
                    ),
                }
            )

        return Path(pdf_path)
