"""CLI entry point for DeepeResearch.

Provides commands for running research sessions and viewing
available profiles and models.
"""

import argparse
import asyncio
import logging
import sys
import threading
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from deepresearch import __version__
from deepresearch.agents.registry import AgentRegistry
from deepresearch.config import (
    ConfigError,
    load_agent_profiles,
    load_model_config,
    validate_all,
)
from deepresearch.llm.client import LLMClient
from deepresearch.orchestrator import Orchestrator

console = Console()

# Set up minimal logging for CLI users.
logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="deepresearch",
        description="Multi-agent AI research system for multi-perspective research papers",
        epilog="Example: deepresearch run 'Quantum Computing in Healthcare' --quick",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- run command ---
    run_parser = subparsers.add_parser("run", help="Run a research session")
    run_parser.add_argument("topic", type=str, help="Research topic or question")
    run_parser.add_argument(
        "-t",
        "--time",
        type=int,
        default=30,
        help="Time budget in minutes (1-480, default: 30)",
    )
    run_parser.add_argument(
        "--minutes",
        type=int,
        default=None,
        help="Custom time budget in minutes (overrides --quick/--medium/--deep)",
    )
    run_parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode — fastest results (short time budget)",
    )
    run_parser.add_argument(
        "--deep",
        action="store_true",
        help="Deep mode — most thorough investigation",
    )
    run_parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        choices=range(1, 11),
        metavar="[1-10]",
        help="Maximum research rounds (1-10, default: budget-based)",
    )
    run_parser.add_argument(
        "--random-models",
        action="store_true",
        help="Assign models to agents randomly",
    )
    run_parser.add_argument(
        "--manual-models",
        action="store_true",
        help="Prompt for per-agent model selection",
    )
    run_parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Model ID to use (e.g., 'opencode/go/deepseek-v4-flash' or 'gpt-4o')",
    )
    run_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for deterministic model assignment (not yet implemented)",
    )
    run_parser.add_argument(
        "-o",
        "--output",
        type=str,
        default="./output",
        help="Output directory path (default: ./output)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration without executing LLM calls",
    )
    run_parser.add_argument(
        "--web",
        action="store_true",
        help="Launch web dashboard server alongside the session",
    )
    run_parser.add_argument(
        "--web-host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the web dashboard (default: 0.0.0.0)",
    )
    run_parser.add_argument(
        "--web-port",
        type=int,
        default=8080,
        help="Port to bind the web dashboard (default: 8080)",
    )
    run_parser.add_argument(
        "--web-max-concurrent",
        type=int,
        default=3,
        choices=range(1, 11),
        metavar="[1-10]",
        help="Max concurrent sessions for web dashboard (1-10, default: 3)",
    )

    # --- serve subcommand ---
    serve_parser = subparsers.add_parser("serve", help="Start the web dashboard server")
    serve_parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host to bind the web server (default: 0.0.0.0)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to bind the web server (default: 8080)",
    )
    serve_parser.add_argument(
        "--max-concurrent",
        type=int,
        default=3,
        choices=range(1, 11),
        metavar="[1-10]",
        help="Max concurrent research sessions (1-10, default: 3)",
    )

    # --- profiles subcommand ---
    profiles_parser = subparsers.add_parser("profiles", help="Manage agent profiles")
    profiles_sub = profiles_parser.add_subparsers(
        dest="profiles_command", help="Profiles commands"
    )
    profiles_sub.add_parser("list", help="List available agent profiles")

    # --- models subcommand ---
    models_parser = subparsers.add_parser("models", help="Manage LLM models")
    models_sub = models_parser.add_subparsers(
        dest="models_command", help="Models commands"
    )
    models_sub.add_parser("list", help="List available LLM models")

    return parser


def _resolve_time_budget(args: argparse.Namespace) -> str:
    """Convert CLI flags to a time-budget keyword.

    Precedence: ``--minutes N`` > ``--quick`` / ``--deep`` > ``--time N`` > default.
    """
    if args.minutes is not None:
        return "custom"
    if args.quick:
        return "quick"
    if args.deep:
        return "deep"
    # Map minutes to budget keyword.
    if args.time < 10:
        return "quick"
    if args.time < 25:
        return "medium"
    return "deep"


def _resolve_time_budget_seconds(args: argparse.Namespace) -> int | None:
    """Return custom time budget seconds if ``--minutes`` is set."""
    if args.minutes is not None:
        return max(60, min(args.minutes * 60, 3600))  # Clamp 1min-1hr
    return None


def _resolve_model_mode(args: argparse.Namespace) -> str:
    """Convert CLI flags to model-mode keyword (default ``"same"``)."""
    if args.random_models:
        return "random"
    if args.manual_models:
        return "manual"
    return "same"


def _validate_configs_before_run() -> list[str]:
    """Validate all configuration files before starting a session.

    Returns a list of error messages (empty = all good).
    """
    errors: list[str] = []
    try:
        profiles = load_agent_profiles()
        model_configs = load_model_config()
    except ConfigError as e:
        errors.append(str(e))
        return errors

    # Run comprehensive validation.
    validation_errors = validate_all(profiles=profiles, models=model_configs)
    errors.extend(validation_errors)
    return errors


def _create_progress() -> Progress:
    """Create a Rich Progress instance configured for DeepResearch."""
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    )


def cmd_run(args: argparse.Namespace) -> int:
    """Execute the 'run' command using the Orchestrator."""
    console.print(
        "[bold green]DeepeResearch[/bold green] — Multi-Agent Research System"
    )
    console.print(f"Topic: [yellow]{args.topic}[/yellow]")
    console.print(f"Output: {args.output}")

    # Resolve time budget.
    time_budget = _resolve_time_budget(args)
    time_budget_seconds = _resolve_time_budget_seconds(args)
    model_mode = _resolve_model_mode(args)

    # Resolve output path.
    output = Path(args.output)
    if output.suffix:
        output_path = str(output)
    else:
        output_path = str(output / "paper.pdf")

    # Validate configs before anything else.
    if not args.dry_run:
        console.print("\n[dim]Validating configuration...[/dim]")
        config_errors = _validate_configs_before_run()
        if config_errors:
            console.print("[red]Configuration validation failed:[/red]")
            for err in config_errors:
                console.print(f"  [red]• {err}[/red]")
            return 1

    # Wire up the real agent implementations (Phase 3).
    try:
        llm_client = LLMClient(model=args.model) if args.model else LLMClient()
        registry = AgentRegistry(llm_client)
    except Exception as e:
        console.print(f"[red]Failed to initialise agent system:[/red] {e}")
        return 1

    orchestrator = Orchestrator(
        agent_factory=registry.agent_factory,
        scribe_factory=lambda event_callback=None, model_name=None: (
            registry.create_scribe_agent(
                model_name=model_name, event_callback=event_callback
            )
        ),
    )

    # ── Dry-run mode ────────────────────────────────────────────────
    if args.dry_run:
        console.print("\n[bold]Dry-run mode[/bold] — validating configuration...")
        try:
            run_kwargs = dict(
                time_budget=time_budget,
                model_mode=model_mode,
                dry_run=True,
                output_path=output_path,
                max_rounds=args.rounds,
            )
            if args.model:
                run_kwargs["selected_model"] = args.model
            if time_budget_seconds is not None:
                run_kwargs["time_budget_seconds"] = time_budget_seconds
            asyncio.run(orchestrator.run(args.topic, **run_kwargs))
            return 0
        except ConfigError as e:
            console.print(f"[red]✗ Configuration error:[/red] {e}")
            return 1
        except FileNotFoundError as e:
            console.print(f"[red]✗ File not found:[/red] {e}")
            return 1
        except Exception as e:
            console.print(f"[red]✗ Dry-run failed:[/red] {e}")
            return 1

    # ── Web dashboard server (optional) ─────────────────────────────
    if args.web:
        from deepresearch.web.server import run_server

        url = f"http://{args.web_host}:{args.web_port}"
        console.print(f"[dim]Starting web dashboard on {url}...[/dim]")
        server_thread = threading.Thread(
            target=run_server,
            kwargs={
                "host": args.web_host,
                "port": args.web_port,
                "max_concurrent": args.web_max_concurrent,
            },
            daemon=True,
        )
        server_thread.start()
        webbrowser.open(url)

    # ── Full session with progress ──────────────────────────────────
    try:
        with _create_progress() as progress:
            session_task = progress.add_task(
                "[cyan]Researching...",
                total=100,
            )
            progress.update(session_task, advance=5, description="[cyan]Configuring...")

            run_kwargs = dict(
                time_budget=time_budget,
                model_mode=model_mode,
                output_path=output_path,
                max_rounds=args.rounds,
            )
            if args.model:
                run_kwargs["selected_model"] = args.model
            if time_budget_seconds is not None:
                run_kwargs["time_budget_seconds"] = time_budget_seconds
            result = asyncio.run(orchestrator.run(args.topic, **run_kwargs))

            progress.update(session_task, completed=100, description="[green]Complete!")

        console.print(
            f"\n[bold green]✓ Session complete![/bold green] Output: {result}"
        )
        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Session cancelled by user[/yellow]")
        return 130

    except FileNotFoundError as e:
        console.print(f"\n[red]✗ File not found:[/red] {e}")
        console.print("  [dim]Check that the config file path is correct.[/dim]")
        return 1

    except ConfigError as e:
        console.print(f"\n[red]✗ Configuration error:[/red] {e}")
        return 1

    except ConnectionError as e:
        console.print(f"\n[red]✗ Connection error:[/red] {e}")
        console.print(
            "  [dim]Check your API key and network connection. "
            "Ensure the LLM API is accessible.[/dim]"
        )
        return 1

    except Exception as e:
        console.print(f"\n[red]✗ Session failed:[/red] {e}")
        logger = logging.getLogger(__name__)
        logger.debug("Session failed with exception", exc_info=True)
        return 1


def cmd_profiles_list(args: argparse.Namespace) -> int:
    """List available agent profiles."""
    try:
        profiles = load_agent_profiles()
    except FileNotFoundError as e:
        console.print(f"[red]Profiles file not found:[/red] {e}")
        return 1
    except ConfigError as e:
        console.print(f"[red]Error loading profiles:[/red] {e}")
        return 1
    except Exception as e:
        console.print(f"[red]Unexpected error loading profiles:[/red] {e}")
        return 1

    table = Table(title=f"Agent Profiles ({len(profiles)})")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Emoji")
    table.add_column("Temperature", justify="center")

    for p in profiles:
        table.add_row(p.id, p.name, p.emoji, str(p.temperature))

    console.print(table)
    return 0


def cmd_models_list(args: argparse.Namespace) -> int:
    """List available LLM models."""
    try:
        models = load_model_config()
    except FileNotFoundError as e:
        console.print(f"[red]Models file not found:[/red] {e}")
        return 1
    except ConfigError as e:
        console.print(f"[red]Error loading models:[/red] {e}")
        return 1
    except Exception as e:
        console.print(f"[red]Unexpected error loading models:[/red] {e}")
        return 1

    table = Table(title=f"Available Models ({len(models)})")
    table.add_column("ID", style="cyan")
    table.add_column("Provider", style="green")
    table.add_column("Display Name")
    table.add_column("Default", justify="center")

    for m in models:
        is_default = m.get("default", False)
        table.add_row(
            m["id"],
            m.get("provider", "unknown"),
            m.get("display_name", m["id"]),
            "✓" if is_default else "",
        )

    console.print(table)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the web dashboard server without running a session."""
    from deepresearch.web.server import run_server

    console.print("[bold green]DeepeResearch Dashboard[/bold green]")
    console.print(f"Starting web server at http://{args.host}:{args.port}")
    console.print(f"Max concurrent sessions: {args.max_concurrent}")
    console.print("Press Ctrl+C to stop the server")
    try:
        run_server(host=args.host, port=args.port, max_concurrent=args.max_concurrent)
        return 0
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/yellow]")
        return 0


def main() -> int:
    """Main entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        return cmd_run(args)
    elif args.command == "serve":
        return cmd_serve(args)
    elif args.command == "profiles":
        if args.profiles_command == "list":
            return cmd_profiles_list(args)
        else:
            parser.parse_args(["profiles", "--help"])
            return 1
    elif args.command == "models":
        if args.models_command == "list":
            return cmd_models_list(args)
        else:
            parser.parse_args(["models", "--help"])
            return 1
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
