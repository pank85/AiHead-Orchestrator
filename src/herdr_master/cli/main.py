"""CLI interface for herdr-master (herdm)."""

import asyncio
import os
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console

from herdr_master.core.config import load_project_config, squad_from_project_config
from herdr_master.core.context import resolve_herdr_context
from herdr_master.core.models import HarnessKind
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.launcher import launch_ergo, launch_master

app = typer.Typer(
    name="herdm",
    help="👑 Universal multi-master, multi-worker cross-harness AI orchestrator built on Herdr.",
    add_completion=True,
)
master_app = typer.Typer(help="Manage and launch Herdr Master orchestrators (Multiple Jobs).")
pipeline_app = typer.Typer(help="Manage and run cross-harness Ergo pipelines (Single Major Job).")

app.add_typer(master_app, name="master")
app.add_typer(pipeline_app, name="pipeline")

console = Console()


@app.command()
def init(
    target_branch: str = typer.Option("develop", help="Target integration branch"),
    session: str = typer.Option(
        "default",
        help="Persistent Herdr session (defaults to active existing or 'default')",
    ),
):
    """Initialize .herdr-master workspace configuration and git develop branch."""
    console.print("[green]✓[/green] Initializing Herdr-Master in current workspace...")
    console.print(f"  • Target integration branch: [cyan]{target_branch}[/cyan]")
    console.print(f"  • Herdr session: [cyan]{session}[/cyan]")
    console.print("[green]✓[/green] Ready. Use 'herdm master start' or '/herdr-master' in a pane.")


@app.command()
def status():
    """Show current active masters, workers, and pipelines."""
    console.print("[bold cyan]Herdr-Master Status Snapshot[/bold cyan]")
    ctx = resolve_herdr_context()
    env_status = "Active (inside Herdr)" if ctx.is_inside_herdr else "Outside Herdr"
    console.print(f"  • Herdr Environment: {env_status}")
    console.print(f"  • Socket: [cyan]{ctx.socket_path}[/cyan]")
    console.print("Run [yellow]herdm tui[/yellow] to launch the interactive terminal dashboard.")


@master_app.command("start")
def master_start(
    name: str = typer.Option("master-coordinator", help="Master orchestrator agent name"),
    harness: str = typer.Option("agy", help="Harness kind: agy, pi, claude, opencode"),
    model: str = typer.Option("gemini-3.8-flash-high", help="Model slug"),
    task: Optional[str] = typer.Option(None, "--task", "-t", help="Initial task/job for master"),
    session: str = typer.Option("default", help="Herdr session name"),
    attach: bool = typer.Option(
        True, "--attach/--no-attach", help="Attach to Herdr if outside Herdr"
    ),
):
    """Launch a new Herdr Master orchestrator for multiple jobs."""
    ctx = resolve_herdr_context(explicit_session=session)
    client = HerdrClient(session=session, socket_path=ctx.socket_path)

    async def _run():
        hkind = HarnessKind(harness.lower())
        return await launch_master(
            client=client,
            name=name,
            harness=hkind,
            model=model,
            initial_task=task,
        )

    console.print(f"[bold cyan]👑 Launching Herdr Master[/bold cyan] ({name} on {model})...")
    res = asyncio.run(_run())
    ws_id = res.get("workspace_id")
    pane_id = res.get("pane_id")
    target_session = res.get("session", session)
    console.print(
        f"[green]✓[/green] Master active in session [cyan]{target_session}[/cyan], "
        f"workspace [cyan]{ws_id}[/cyan], pane [cyan]{pane_id}[/cyan]"
    )

    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


@pipeline_app.command("ergo")
@app.command("ergo")
def run_ergo(
    intent: str = typer.Argument(..., help="Feature intent or objective"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Dedicated workspace label"),
    session: str = typer.Option("default", help="Herdr session name"),
    attach: bool = typer.Option(
        True, "--attach/--no-attach", help="Attach to Herdr if outside Herdr"
    ),
):
    """Launch a new Ergo pipeline for a single major job."""
    ctx = resolve_herdr_context(explicit_session=session)
    client = HerdrClient(session=session, socket_path=ctx.socket_path)
    squad = squad_from_project_config(load_project_config())

    ws_label = label or f"ergo-{datetime.now().strftime('%Y%m%d-%H%M')}"

    async def _run():
        return await launch_ergo(
            client=client,
            intent=intent,
            workspace_label=ws_label,
            squad=squad,
            session=session if session != "default" else None,
        )

    console.print(f"[bold magenta]⚡ Launching Ergo Pipeline[/bold magenta] ('{intent}')...")
    res = asyncio.run(_run())
    ws_id = res.get("workspace_id")
    pane_id = res.get("scout_pane_id")
    target_session = res.get("session", session)
    console.print(
        f"[green]✓[/green] Ergo Scout active in session [cyan]{target_session}[/cyan], "
        f"workspace [cyan]{ws_id}[/cyan], pane [cyan]{pane_id}[/cyan]"
    )

    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


@app.command()
def tui(
    session: str = typer.Option(
        "default",
        help="Persistent Herdr session (defaults to active existing or 'default')",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Run TUI in demo mode with pre-populated Ergo pipeline data",
    ),
):
    """Launch the interactive terminal management dashboard (TUI)."""
    from herdr_master.ui.tui.app import HerdrMasterApp

    tui_app = HerdrMasterApp(session=session, demo=demo)
    result = tui_app.run()
    if isinstance(result, dict) and result.get("action") == "attach":
        target_session = result.get("session", session)
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])
    elif isinstance(result, dict) and result.get("action") == "done":
        ws_id = result.get("workspace_id")
        console.print(f"[green]✓[/green] Active in workspace [cyan]{ws_id}[/cyan]")


if __name__ == "__main__":
    app()
