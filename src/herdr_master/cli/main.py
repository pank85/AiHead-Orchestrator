"""CLI interface for herdr-master (herdm)."""

import asyncio
import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
except Exception:
    pass

import typer
from rich.console import Console

from herdr_master.cli.catalog import catalog_app
from herdr_master.cli.skills import install_skills as run_install_skills
from herdr_master.cli.skills import skills_app
from herdr_master.core.catalog import CatalogError, supported_harnesses
from herdr_master.core.slug import InvalidBindingSlugError
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.launcher import (
    LaunchContextError,
    launch_ergo,
    launch_master,
    resolve_coordinator_binding,
    resolve_launch_context,
)
from herdr_master.pipeline.squad import (
    SquadValidationError,
    parse_squad_overrides,
    resolve_squad,
)

app = typer.Typer(
    name="herdm",
    help="👑 Universal multi-master, multi-worker cross-harness AI orchestrator built on Herdr.",
    add_completion=True,
)
master_app = typer.Typer(help="Manage and launch Herdr Master orchestrators (Multiple Jobs).")
pipeline_app = typer.Typer(help="Manage and run cross-harness Ergo pipelines (Single Major Job).")

app.add_typer(master_app, name="master")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(skills_app, name="skills")
app.add_typer(catalog_app, name="catalog")

console = Console()

PLUGIN_ID = "devswha.herdr-master"
_HARNESS_HELP = (
    "Coordinator harness ("
    + ", ".join(supported_harnesses(mode="herdr"))
    + "). Omit to use the catalog Scout "
    "(agy gemini-3.8-flash-medium, effort medium). Any other harness requires --model."
)
_MODEL_HELP = (
    "Model slug. Omit with the catalog Scout harness to use gemini-3.8-flash-medium. "
    "Required for any other harness."
)
_EFFORT_HELP = "Reasoning effort: low, medium, high, or max (xhigh aliases to max)."
_SESSION_HELP = (
    "Herdr session name. Omit to adopt the inherited session and socket, "
    "or to provision an isolated session outside Herdr."
)
_RESOLVE_ERRORS = (
    CatalogError,
    SquadValidationError,
    InvalidBindingSlugError,
    LaunchContextError,
    FileNotFoundError,
)


def _abort(message: str) -> None:
    typer.echo(str(message), err=True)
    raise typer.Exit(1)


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
    from herdr_master.core.context import resolve_herdr_context

    console.print("[bold cyan]Herdr-Master Status Snapshot[/bold cyan]")
    ctx = resolve_herdr_context()
    env_status = "Active (inside Herdr)" if ctx.is_inside_herdr else "Outside Herdr"
    console.print(f"  • Herdr Environment: {env_status}")
    console.print(f"  • Socket: [cyan]{ctx.socket_path}[/cyan]")
    console.print("Run [yellow]herdm tui[/yellow] to launch the interactive terminal dashboard.")


@master_app.command("start")
def master_start(
    name: str = typer.Option("master-coordinator", help="Master orchestrator agent name"),
    harness: Optional[str] = typer.Option(None, "--harness", help=_HARNESS_HELP),
    model: Optional[str] = typer.Option(None, "--model", help=_MODEL_HELP),
    effort: Optional[str] = typer.Option(None, "--effort", help=_EFFORT_HELP),
    task: Optional[str] = typer.Option(None, "--task", "-t", help="Initial task/job for master"),
    session: Optional[str] = typer.Option(None, "--session", help=_SESSION_HELP),
    attach: bool = typer.Option(
        True, "--attach/--no-attach", help="Attach to Herdr if outside Herdr"
    ),
):
    """Launch a new Herdr Master orchestrator for multiple jobs."""
    try:
        kind, model_slug, effort_level = resolve_coordinator_binding(harness, model, effort)
        ctx = resolve_launch_context(session)
    except _RESOLVE_ERRORS as err:
        _abort(str(err))
    client = HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)

    async def _run():
        return await launch_master(
            client=client,
            name=name,
            harness=kind,
            model=model_slug,
            initial_task=task,
            session=session,
            effort=effort_level,
        )

    shown = f"{kind.value} {model_slug}"
    if effort_level is not None:
        shown = f"{shown} effort {effort_level.value}"
    console.print(f"[bold cyan]👑 Launching Herdr Master[/bold cyan] ({name} on {shown})...")
    res = asyncio.run(_run())
    ws_id = res.get("workspace_id")
    pane_id = res.get("pane_id")
    target_session = res.get("session", ctx.session_name)
    console.print(
        f"[green]✓[/green] Master active in session [cyan]{target_session}[/cyan], "
        f"workspace [cyan]{ws_id}[/cyan], pane [cyan]{pane_id}[/cyan]"
    )

    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


def _resolve_ergo_once(
    *,
    models: Optional[str],
    scout: Optional[str],
    architect: Optional[str],
    workers: Optional[str],
    coder: Optional[str],
    reviewer: Optional[str],
    run_squad: Optional[Path],
):
    if coder is not None and workers is not None and workers != coder:
        _abort("--workers and --coder disagree. Pass only one worker override.")
    overrides = parse_squad_overrides(
        models=models,
        scout=scout,
        architect=architect,
        workers=workers or coder,
        reviewer=reviewer,
    )
    return resolve_squad(
        None,
        mode="herdr",
        run_squad_path=run_squad,
        overrides=overrides or None,
    )


@pipeline_app.command("ergo")
@app.command("ergo")
def run_ergo(
    intent: str = typer.Argument(..., help="Feature intent or objective"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Dedicated workspace label"),
    session: Optional[str] = typer.Option(None, "--session", help=_SESSION_HELP),
    attach: bool = typer.Option(
        True, "--attach/--no-attach", help="Attach to Herdr if outside Herdr"
    ),
    models: Optional[str] = typer.Option(
        None,
        "--models",
        help="Compact role:binding list. Individual flags win.",
    ),
    scout: Optional[str] = typer.Option(None, "--scout", help="Scout binding override."),
    architect: Optional[str] = typer.Option(
        None, "--architect", help="Architect binding override."
    ),
    workers: Optional[str] = typer.Option(None, "--workers", help="Worker-pool override."),
    coder: Optional[str] = typer.Option(
        None, "--coder", "--coders", help="Alias for --workers."
    ),
    reviewer: Optional[str] = typer.Option(None, "--reviewer", help="Reviewer binding override."),
    run_squad: Optional[str] = typer.Option(None, "--run-squad", help="Run squad.toml."),
):
    """Launch an Ergo orchestrator for a single major job."""
    try:
        resolved = _resolve_ergo_once(
            models=models,
            scout=scout,
            architect=architect,
            workers=workers,
            coder=coder,
            reviewer=reviewer,
            run_squad=Path(run_squad) if run_squad else None,
        )
        ctx = resolve_launch_context(session)
    except _RESOLVE_ERRORS as err:
        _abort(str(err))
    client = HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)
    ws_label = label or f"ergo-{datetime.now().strftime('%Y%m%d-%H%M')}"

    async def _run():
        return await launch_ergo(
            client=client,
            intent=intent,
            workspace_label=ws_label,
            squad=resolved.squad,
            session=session,
        )

    console.print(f"[bold magenta]⚡ Launching Ergo orchestrator[/bold magenta] ('{intent}')...")
    res = asyncio.run(_run())
    ws_id = res.get("workspace_id")
    pane_id = res.get("pane_id") or res.get("orchestrator_pane_id") or res.get("scout_pane_id")
    target_session = res.get("session", ctx.session_name)
    console.print(
        f"[green]✓[/green] Ergo orchestrator started in session [cyan]{target_session}[/cyan], "
        f"workspace [cyan]{ws_id}[/cyan], pane [cyan]{pane_id}[/cyan]"
    )

    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


@app.command()
def tui(
    session: Optional[str] = typer.Option(None, "--session", help=_SESSION_HELP),
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


def _workspace_id(result: dict) -> Optional[str]:
    workspace = result.get("workspace")
    if isinstance(workspace, dict) and workspace.get("workspace_id"):
        return str(workspace["workspace_id"])
    if result.get("workspace_id"):
        return str(result["workspace_id"])
    return None


@app.command("workspace")
def workspace_cmd(
    label: str = typer.Option("herdr-master", "--label", help="Workspace label."),
    cwd: Optional[str] = typer.Option(
        None,
        "--cwd",
        help="Absolute working directory for the new workspace.",
    ),
    session: Optional[str] = typer.Option(None, "--session", help=_SESSION_HELP),
):
    """Create a Herdr workspace and open the cockpit. Does not launch Ergo."""
    if cwd is None:
        target_cwd = os.getcwd()
    else:
        path = Path(cwd)
        if not path.is_absolute():
            _abort("--cwd must be an absolute path.")
        target_cwd = str(path)
    try:
        ctx = resolve_launch_context(session)
    except LaunchContextError as err:
        _abort(str(err))
    client = HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)
    console.print(f"Creating new Herdr workspace: {label} at {target_cwd}")

    async def _create() -> dict:
        return await client.create_workspace(label=label, focus=True, cwd=target_cwd)

    created = asyncio.run(_create())
    ws_id = _workspace_id(created)
    if not ws_id:
        _abort("Workspace create returned no workspace_id. Refusing to open the cockpit.")
    console.print(f"Opening Herdr-Master Cockpit tab in workspace {ws_id}...")
    completed = subprocess.run(
        [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            PLUGIN_ID,
            "--entrypoint",
            "cockpit",
            "--workspace",
            ws_id,
            "--placement",
            "tab",
            "--focus",
        ],
        check=False,
    )
    if completed.returncode != 0:
        _abort("Failed to open the Herdr-Master cockpit pane.")


@app.command("install")
def install_cmd(
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        "-f",
        help="Overwrite or update existing symlinks",
    ),
) -> None:
    """Install standalone /herdr-master and /ergo skills across all 4 agent harnesses."""
    run_install_skills(force=force)


if __name__ == "__main__":
    app()
