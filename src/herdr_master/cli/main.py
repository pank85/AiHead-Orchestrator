"""CLI interface for herdr-master (herdm)."""

import asyncio
import os
import shutil
import signal
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

try:
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
except Exception:
    pass

import typer
from rich.console import Console

from herdr_master.cli.aihead_setup import run_aihead_setup
from herdr_master.cli.catalog import catalog_app
from herdr_master.cli.skills import install_skills as run_install_skills
from herdr_master.cli.skills import skills_app
from herdr_master.core.catalog import CatalogError, supported_harnesses
from herdr_master.core.config import CouncilConfig, find_project_config_path, load_project_config
from herdr_master.core.context import HerdrContext
from herdr_master.core.slug import InvalidBindingSlugError
from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.socket import HerdrConnectionError
from herdr_master.pipeline.council import CouncilError
from herdr_master.pipeline.launcher import (
    COUNCIL_COORDINATOR_NAME,
    LaunchContextError,
    ensure_herdr_server,
    launch_ai_head,
    launch_council,
    launch_ergo,
    launch_master,
    launch_orchestration,
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
    invoke_without_command=True,
    no_args_is_help=False,
)
# Console-script entry for `aihead`. One callback, no nested command, and never
# the headless `herdm aihead-cli` decision path.
aihead_app = typer.Typer(
    name="aihead",
    help=(
        "Launch an interactive AiHead session in a Herdr workspace. "
        "Adopts an existing workspace when one is already available; otherwise "
        "starts Herdr and opens a new workspace."
    ),
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=True,
    subcommand_metavar="",
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
    CouncilError,
    ValueError,
)


def _abort(message: str) -> None:
    typer.echo(str(message), err=True)
    raise typer.Exit(1)


@app.callback()
def root(
    ctx: typer.Context,
    aihead: bool = typer.Option(
        False,
        "--aihead",
        help="Launch an interactive AiHead session (same as `herdm aihead`).",
    ),
    aihead_setup: bool = typer.Option(
        False,
        "--aihead-setup",
        help="Configure per-role AiHead models (same as `herdm aihead-setup`).",
    ),
    task: Optional[str] = typer.Option(
        None, "--task", "-t", help="Initial instruction for `--aihead`."
    ),
    harness: Optional[str] = typer.Option(
        None, "--harness", help="Harness override for `--aihead`."
    ),
    model: Optional[str] = typer.Option(None, "--model", help="Model override for `--aihead`."),
    effort: Optional[str] = typer.Option(
        None, "--effort", help="Reasoning effort override for `--aihead`."
    ),
    cwd: Optional[Path] = typer.Option(
        None, "--cwd", "--folder", "-C", help="Initial folder for `--aihead`."
    ),
    attach: bool = typer.Option(
        True, "--attach/--no-attach", help="Attach to Herdr if outside Herdr."
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", help="Optional path to .herdr-master.toml."
    ),
    defaults: bool = typer.Option(
        False, "--defaults", help="Apply `--aihead-setup` defaults without prompting."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the `--aihead-setup` confirmation."),
) -> None:
    """Root launcher flags. Subcommands keep their existing behaviour."""
    if aihead_setup:
        run_aihead_setup(defaults=defaults, yes=yes, config=config)
        raise typer.Exit(0)
    if aihead:
        run_aihead(
            harness=harness,
            model=model,
            effort=effort,
            task=task,
            cwd=cwd,
            attach=attach,
            config=config,
        )
        raise typer.Exit(0)
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


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
    ai_head: Optional[bool] = typer.Option(
        None,
        "--ai-head/--no-ai-head",
        help="Enable or disable AiHead autonomous decisions.",
    ),
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
            ai_head=ai_head,
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


@pipeline_app.command("orchestration")
@app.command("orchestration")
def run_orchestration(
    intent: str = typer.Argument(..., help="Feature intent or objective"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Dedicated workspace label"),
    session: Optional[str] = typer.Option(None, "--session", help=_SESSION_HELP),
    attach: bool = typer.Option(
        True, "--attach/--no-attach", help="Attach to Herdr if outside Herdr"
    ),
    harness: Optional[str] = typer.Option(
        None, "--harness", help="Native harness (agy, pi, claude, opencode)."
    ),
    models: Optional[str] = typer.Option(
        None,
        "--models",
        help="Compact role models, e.g. scout=...,architect=...,coder=...,reviewer=...",
    ),
    scout: Optional[str] = typer.Option(None, "--scout", help="Scout model override."),
    architect: Optional[str] = typer.Option(
        None, "--architect", help="Architect model override."
    ),
    workers: Optional[str] = typer.Option(
        None, "--workers", help="Worker models (pipe-separated)."
    ),
    coder: Optional[str] = typer.Option(
        None, "--coder", "--coders", help="Alias for --workers."
    ),
    reviewer: Optional[str] = typer.Option(None, "--reviewer", help="Reviewer model override."),
    dangerously_skip_permissions: bool = typer.Option(
        True,
        "--skip-permissions/--no-skip-permissions",
        help="Skip interactive permissions prompts.",
    ),
    ai_head: Optional[bool] = typer.Option(
        None,
        "--ai-head/--no-ai-head",
        help="Enable or disable AiHead autonomous decisions.",
    ),
):
    """Launch a self-contained native orchestration pipeline in a single harness."""
    if coder is not None and workers is not None and workers != coder:
        _abort("--workers and --coder disagree. Pass only one worker override.")
    try:
        ctx = resolve_launch_context(session)
        project_cfg = load_project_config()
        orch_cfg = project_cfg.orchestration

        eff_harness = harness or orch_cfg.harness.value

        parsed_models: dict[str, str] = {}
        if models:
            for pair in models.split(","):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    parsed_models[k.strip().lower()] = v.strip()

        default_scout = orch_cfg.scout_model or "gemini-3.8-flash"
        default_arch = orch_cfg.architect_model or "gemini-3.8-flash"
        default_coder = " | ".join(orch_cfg.worker_models) if orch_cfg.worker_models else "gemini-3.8-flash"
        default_rev = orch_cfg.reviewer_model or "gemini-3.8-flash"

        eff_scout = scout or parsed_models.get("scout") or default_scout
        eff_architect = architect or parsed_models.get("architect") or default_arch
        eff_coder = (
            workers
            or coder
            or parsed_models.get("coder")
            or parsed_models.get("workers")
            or default_coder
        )
        eff_reviewer = reviewer or parsed_models.get("reviewer") or default_rev

        if not eff_scout or not eff_architect or not eff_coder or not eff_reviewer:
            _abort("Scout, architect, coder, and reviewer models must all be non-empty.")
    except _RESOLVE_ERRORS as err:
        _abort(str(err))

    client = HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)
    ws_label = label or f"orchestration-{datetime.now().strftime('%Y%m%d-%H%M')}"

    async def _run():
        return await launch_orchestration(
            client=client,
            intent=intent,
            workspace_label=ws_label,
            harness=eff_harness,
            scout_model=eff_scout,
            architect_model=eff_architect,
            coder_model=eff_coder,
            reviewer_model=eff_reviewer,
            scout_effort=orch_cfg.scout_effort,
            architect_effort=orch_cfg.architect_effort,
            reviewer_effort=orch_cfg.reviewer_effort,
            session=session,
            ai_head=ai_head,
            dangerously_skip_permissions=dangerously_skip_permissions,
        )

    console.print(f"[bold cyan]🚀 Launching native orchestration[/bold cyan] ('{intent}')...")
    res = asyncio.run(_run())
    ws_id = res.get("workspace_id")
    pane_id = res.get("pane_id") or res.get("orchestrator_pane_id")
    target_session = res.get("session", ctx.session_name)
    console.print(
        f"[green]✓[/green] Orchestration started in session [cyan]{target_session}[/cyan], "
        f"workspace [cyan]{ws_id}[/cyan], pane [cyan]{pane_id}[/cyan]"
    )

    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


@pipeline_app.command("council")
@app.command("council")
def run_council(
    intent: str = typer.Argument(..., help="Specifications, plan intent, or objective"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Dedicated workspace label"),
    session: Optional[str] = typer.Option(None, "--session", help=_SESSION_HELP),
    attach: bool = typer.Option(
        True, "--attach/--no-attach", help="Attach to Herdr if outside Herdr"
    ),
    ai_head: Optional[bool] = typer.Option(
        None,
        "--ai-head/--no-ai-head",
        help="Enable or disable AiHead autonomous decisions.",
    ),
    roster: Optional[str] = typer.Option(
        None,
        "--roster",
        help="Comma-separated member roster override (e.g. member-1,member-2).",
    ),
):
    """Launch an AI Council deliberation session hosted by the coordinator."""
    try:
        ctx = resolve_launch_context(session)
        project_cfg = load_project_config()
        council_cfg = project_cfg.council

        if roster:
            members = [s.strip() for s in roster.split(",") if s.strip()]
            if len(members) < 2:
                _abort("Council requires at least 2 members.")
            council_cfg = CouncilConfig.model_validate(
                {**council_cfg.model_dump(), "members": members}
            )
    except _RESOLVE_ERRORS as err:
        _abort(str(err))

    client = HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)
    ws_label = label or f"council-{datetime.now().strftime('%Y%m%d-%H%M')}"

    async def _run():
        return await launch_council(
            client=client,
            intent=intent,
            workspace_label=ws_label,
            council=council_cfg,
            session=session,
            ai_head=ai_head,
        )

    console.print(f"[bold cyan]🏛️ Launching AI Council[/bold cyan] ('{intent}')...")
    res = asyncio.run(_run())
    ws_id = res.get("workspace_id")
    pane_id = res.get("pane_id") or res.get("coordinator_pane_id")
    target_session = res.get("session", ctx.session_name)
    console.print(
        f"[green]✓[/green] AI Council coordinator started in session [cyan]{target_session}[/cyan], "
        f"workspace [cyan]{ws_id}[/cyan], pane [cyan]{pane_id}[/cyan]"
    )

    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


@app.command("aihead")
@app.command("head")
def run_aihead(
    harness: Optional[str] = typer.Option(
        None,
        "--harness",
        help="Interactive harness override (agy, pi, claude, opencode, hermes).",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model slug override.",
    ),
    effort: Optional[str] = typer.Option(
        None,
        "--effort",
        help="Reasoning effort override (low, medium, high, max).",
    ),
    dangerously_skip_permissions: Optional[bool] = typer.Option(
        None,
        "--skip-permissions/--no-skip-permissions",
        help="Override AiHead dangerously_skip_permissions. Omitted uses saved config, then True.",
    ),
    split: Optional[bool] = typer.Option(
        None,
        "--split/--no-split",
        help="Split active Herdr terminal screen so AiHead becomes primary and active pane becomes secondary.",
    ),
    secondary: bool = typer.Option(
        False,
        "--secondary",
        help="Enforce screen split with active pane becoming secondary.",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Target workspace label or ID (creates standalone tab/workspace).",
    ),
    task: Optional[str] = typer.Option(
        None,
        "--task",
        "-t",
        help="Initial instruction or objective for AiHead.",
    ),
    session: Optional[str] = typer.Option(
        None,
        "--session",
        help=_SESSION_HELP,
    ),
    attach: bool = typer.Option(
        True,
        "--attach/--no-attach",
        help="Attach to Herdr if outside Herdr",
    ),
    cwd: Optional[Path] = typer.Option(
        None,
        "--cwd",
        "--folder",
        "-C",
        help="Initial folder/directory for AiHead to work in.",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Optional path to .herdr-master.toml configuration file.",
    ),
):
    """Launch an interactive AiHead terminal session."""
    try:
        ctx = resolve_launch_context(session)
        project_cfg = load_project_config(config)
        head_cfg = project_cfg.ai_head

        eff_harness = (
            harness
            if harness is not None
            else (head_cfg.binding.kind.value if head_cfg.binding and head_cfg.binding.kind else None)
        )
        eff_model = (
            model
            if model is not None
            else (head_cfg.binding.model if head_cfg.binding else None)
        )
        eff_effort = (
            effort
            if effort is not None
            else (head_cfg.binding.effort.value if head_cfg.binding and head_cfg.binding.effort else None)
        )

        if secondary:
            effective_split = True
        elif split is not None:
            effective_split = split
        else:
            effective_split = head_cfg.split if head_cfg is not None else True

        kind, model_slug, effort_level = resolve_coordinator_binding(eff_harness, eff_model, eff_effort)
    except _RESOLVE_ERRORS as err:
        _abort(str(err))

    eff_skip = (
        dangerously_skip_permissions
        if dangerously_skip_permissions is not None
        else head_cfg.dangerously_skip_permissions
    )
    client = HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)

    async def _run():
        return await launch_ai_head(
            client=client,
            harness=kind,
            model=model_slug,
            effort=effort_level,
            dangerously_skip_permissions=eff_skip,
            split=effective_split,
            workspace=workspace,
            initial_task=task,
            session=session,
            cwd=str(cwd.resolve()) if cwd else None,
            config_path=config,
        )

    shown = f"{kind.value} {model_slug}"
    if effort_level is not None:
        shown = f"{shown} effort {effort_level.value}"
    console.print(f"[bold cyan]🤖 Launching AiHead[/bold cyan] ({shown})...")
    res = asyncio.run(_run())

    if isinstance(res, dict):
        ws_id = res.get("workspace_id")
        pane_id = res.get("pane_id")
        target_session = res.get("session", ctx.session_name)
        is_split = res.get("split", False)
        sec_pane = res.get("secondary_pane_id")
    else:
        ws_id = None
        pane_id = None
        target_session = ctx.session_name
        is_split = False
        sec_pane = None

    if is_split and sec_pane:
        console.print(
            f"[green]✓[/green] AiHead active in session [cyan]{target_session}[/cyan], "
            f"workspace [cyan]{ws_id}[/cyan], primary pane [cyan]{pane_id}[/cyan] "
            f"(split layout, secondary pane [cyan]{sec_pane}[/cyan])"
        )
    else:
        console.print(
            f"[green]✓[/green] AiHead active in session [cyan]{target_session}[/cyan], "
            f"workspace [cyan]{ws_id}[/cyan], pane [cyan]{pane_id}[/cyan]"
        )

    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


@app.command("aihead-setup")
def aihead_setup_cmd(
    defaults: bool = typer.Option(
        False, "--defaults", help="Apply defaults without prompting."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    config: Optional[Path] = typer.Option(
        None, "--config", help="Target .herdr-master.toml (defaults to the project file)."
    ),
) -> None:
    """Configure per-role AiHead harnesses, models, and efforts."""
    run_aihead_setup(defaults=defaults, yes=yes, config=config)


@app.command("aihead-cli")
def aihead_cli_cmd(
    request_file: Optional[Path] = typer.Option(
        None, "--request-file", help="Path to JSON file containing AiHeadDecisionRequest"
    ),
    use_stdin: bool = typer.Option(False, "--stdin", help="Read JSON request from stdin"),
    output_file: Optional[Path] = typer.Option(
        None, "--output-file", help="Path to write structured JSON decision artifact"
    ),
    question: Optional[str] = typer.Option(
        None, "--question", "-q", help="Technical question requiring decision"
    ),
    context: Optional[str] = typer.Option(
        None, "--context", "-c", help="Background context or constraints"
    ),
    choices: Optional[str] = typer.Option(
        None, "--choices", help="Comma-separated choices or options"
    ),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Correlation run ID"),
    timeout: float = typer.Option(60.0, "--timeout", help="Execution timeout in seconds"),
    config_path: Optional[Path] = typer.Option(None, "--config", help="Project config file"),
    discover: bool = typer.Option(
        False, "--discover", help="Print AiHead discovery JSON and exit"
    ),
):
    """Execute real, bounded headless AiHead decisions directly (100% Herdr-free)."""
    import json
    import sys
    from herdr_master.pipeline.ai_head import (
        AiHeadDecisionRequest,
        build_ai_head_discovery,
        execute_cli_decision_sync,
    )

    head_config = load_project_config(config_path).ai_head

    if discover:
        discovery = build_ai_head_discovery(head_config, str(os.getcwd()), mode="cli")
        typer.echo(discovery.model_dump_json(indent=2))
        return

    req: Optional[AiHeadDecisionRequest] = None
    if use_stdin:
        try:
            raw = sys.stdin.read()
            data = json.loads(raw)
            req = AiHeadDecisionRequest.model_validate(data)
        except Exception as err:
            _abort(f"Failed to parse stdin request: {err}")
    elif request_file:
        try:
            with open(request_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            req = AiHeadDecisionRequest.model_validate(data)
        except Exception as err:
            _abort(f"Failed to read request file '{request_file}': {err}")
    elif question:
        parsed_choices = [c.strip() for c in choices.split(",") if c.strip()] if choices else None
        req = AiHeadDecisionRequest(
            question=question,
            context=context or "",
            choices=parsed_choices,
            run_id=run_id or "cli-run",
            origin_cwd=str(os.getcwd()),
            artifact_path=str(output_file) if output_file else None,
        )
    else:
        _abort("Must supply request via --request-file, --stdin, or --question.")

    if output_file and req and not req.artifact_path:
        req = req.model_copy(update={"artifact_path": str(output_file)})

    if not head_config.enabled:
        head_config = head_config.model_copy(update={"enabled": True})

    try:
        reply = execute_cli_decision_sync(
            head_config=head_config,
            request=req,
            timeout_seconds=timeout,
        )
    except Exception as err:
        _abort(f"AiHead decision execution failed: {err}")

    if output_file:
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(reply.model_dump_json(indent=2))
        except Exception as err:
            _abort(f"Failed to write output artifact to '{output_file}': {err}")

    if reply.status == "decided":
        console.print(f"[bold green]Decided:[/bold green] {reply.decision}")
        if reply.rationale:
            console.print(f"[dim]Rationale:[/dim] {reply.rationale}")
    else:
        console.print(f"[bold yellow]Needs User:[/bold yellow] {reply.question or 'Escalated'}")
        if reply.rationale:
            console.print(f"[dim]Rationale:[/dim] {reply.rationale}")


council_state_app = typer.Typer(
    name="council-state",
    help="Internal state-gate CLI for AI Council deliberations.",
)
app.add_typer(council_state_app, name="council-state")


@council_state_app.command("init")
def council_init_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory for council deliberation"),
    roster: Optional[str] = typer.Option(
        None, "--roster", help="Comma-separated member roster or JSON list"
    ),
    specifications: str = typer.Option(
        "", "--specifications", "--specs", help="Deliberation specifications or goal"
    ),
    spec_file: Optional[Path] = typer.Option(
        None, "--spec-file", help="Path to specification document"
    ),
    coordinator: Optional[str] = typer.Option(
        None, "--coordinator", help="Coordinator binding slug"
    ),
    scout: Optional[str] = typer.Option(None, "--scout", help="Scout binding slug"),
):
    """Initialize a new Council deliberation run with pinned roster and specifications."""
    import json
    from herdr_master.pipeline import council

    parsed_roster = None
    if roster:
        if roster.strip().startswith("["):
            try:
                parsed_roster = json.loads(roster)
            except Exception:
                parsed_roster = [s.strip() for s in roster.split(",") if s.strip()]
        else:
            parsed_roster = [s.strip() for s in roster.split(",") if s.strip()]

    try:
        council.init(
            run_dir=run_dir,
            roster=parsed_roster,
            specifications=specifications,
            spec_file=spec_file,
            coordinator=coordinator,
            scout=scout,
        )
        console.print(f"[green]✓[/green] Council run initialized at [cyan]{run_dir}[/cyan]")
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("record-scout")
def council_record_scout_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    context_file: Path = typer.Option(
        ..., "--context-file", "--context", help="Path to Scout context.md"
    ),
):
    """Record Scout codebase discovery context prerequisite."""
    from herdr_master.pipeline import council

    try:
        council.record_scout_context(run_dir=run_dir, context_file=context_file)
        console.print(f"[green]✓[/green] Scout context recorded from [cyan]{context_file}[/cyan]")
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("submit-initial-plan")
def council_submit_initial_plan_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    member: str = typer.Option(..., "--member", "--member-id", help="Member ID"),
    plan: Path = typer.Option(..., "--plan", "--plan-file", help="Path to draft plan"),
):
    """Submit an independently drafted initial plan (Round 0) from a council member."""
    from herdr_master.pipeline import council

    try:
        council.submit_initial_plan(run_dir=run_dir, member_id=member, plan_file=plan)
        console.print(f"[green]✓[/green] Initial plan recorded for member [cyan]{member}[/cyan]")
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("submit-plan")
def council_submit_plan_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    member: str = typer.Option(..., "--member", "--member-id", help="Member ID"),
    plan: Path = typer.Option(..., "--plan", "--plan-file", help="Path to plan file"),
    revision: int = typer.Option(0, "--revision", "-r", help="Revision number (0 for initial)"),
    adopted_plan: Optional[str] = typer.Option(
        None, "--adopted-plan", help="ID of adopted peer plan"
    ),
):
    """Submit a draft (revision=0) or revised (revision>0) plan for a council member."""
    from herdr_master.pipeline import council

    try:
        council.submit_plan(
            run_dir=run_dir,
            member_id=member,
            plan_file=plan,
            revision=revision,
            adopted_plan_id=adopted_plan,
        )
        console.print(
            f"[green]✓[/green] Plan revision {revision} recorded for member [cyan]{member}[/cyan]"
        )
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("open-round")
def council_open_round_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    round_number: int = typer.Option(..., "--round", "--round-number", help="Round number (0-5)"),
):
    """Open Round 0 drafting or feedback cycle 1 through 5. Enforces cycle caps."""
    from herdr_master.pipeline import council

    try:
        council.open_round(run_dir=run_dir, round_number=round_number)
        console.print(f"[green]✓[/green] Round {round_number} opened")
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("submit-feedback")
def council_submit_feedback_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    round_number: int = typer.Option(..., "--round", "--round-number", help="Round number (1-5)"),
    member: str = typer.Option(..., "--member", "--member-id", help="Member ID"),
    feedback: Path = typer.Option(..., "--feedback", "--feedback-file", help="Path to feedback file"),
    selected_plan: str = typer.Option(
        ..., "--selected-plan", "--supported-plan-id", help="Supported candidate plan ID"
    ),
):
    """Submit a member's cross-review feedback and explicit supported plan choice."""
    from herdr_master.pipeline import council

    try:
        council.submit_feedback(
            run_dir=run_dir,
            round_number=round_number,
            member_id=member,
            feedback_file=feedback,
            supported_plan_id=selected_plan,
        )
        console.print(f"[green]✓[/green] Feedback recorded for member [cyan]{member}[/cyan]")
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("submit-unified-feedback")
def council_submit_unified_feedback_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    round_number: int = typer.Option(..., "--round", "--round-number", help="Round number (1-5)"),
    unified_feedback: Path = typer.Option(
        ...,
        "--unified-feedback",
        "--feedback",
        "--feedback-file",
        help="Path to unified feedback document",
    ),
):
    """Record coordinator unified feedback synthesizing member reviews for a round."""
    from herdr_master.pipeline import council

    try:
        council.submit_unified_feedback(
            run_dir=run_dir,
            round_number=round_number,
            unified_feedback_file=unified_feedback,
        )
        console.print(
            f"[green]✓[/green] Unified feedback recorded for round [cyan]{round_number}[/cyan]"
        )
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("check-consensus")
@council_state_app.command("consensus")
def council_check_consensus_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    round_number: int = typer.Option(..., "--round", "--round-number", help="Round number (1-5)"),
):
    """Evaluate whether all enabled members explicitly support the exact same plan revision."""
    from herdr_master.pipeline import council

    try:
        res = council.check_consensus(run_dir=run_dir, round_number=round_number)
        if res.get("consensus"):
            plan_id = res.get("consensus_plan_id")
            console.print(
                f"[bold green]✓ Unanimous consensus reached on plan '{plan_id}'![/bold green]"
            )
        else:
            console.print(
                f"[yellow]No consensus reached in round {round_number}. Deliberation continues.[/yellow]"
            )
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("submit-vote")
@council_state_app.command("vote")
def council_submit_vote_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    member: str = typer.Option(..., "--member", "--member-id", help="Member ID"),
    ballot: str = typer.Option(
        ..., "--ballot", "--voted-plan-id", help="Voted candidate plan ID"
    ),
):
    """Submit a ballot in the fallback 1-round vote after 5 cycles without consensus."""
    from herdr_master.pipeline import council

    try:
        council.submit_vote(run_dir=run_dir, member_id=member, voted_plan_id=ballot)
        console.print(f"[green]✓[/green] Ballot recorded for member [cyan]{member}[/cyan]")
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("finalize")
def council_finalize_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    output: Optional[Path] = typer.Option(
        None, "--output", "--output-file", help="Path to write final_plan.md"
    ),
):
    """Finalize the Council deliberation and generate final_plan.md artifact."""
    from herdr_master.pipeline import council

    try:
        res = council.finalize(run_dir=run_dir, output_file=output)
        out_path = res.get("final_plan_file") or res.get("output_file")
        outcome = res.get("outcome")
        winning = res.get("winning_plan") or res.get("winning_plan_id")
        console.print(
            f"[bold green]✓ Council deliberation finalized![/bold green] "
            f"Outcome: [cyan]{outcome}[/cyan], Plan: [cyan]{winning}[/cyan], Output: [cyan]{out_path}[/cyan]"
        )
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@council_state_app.command("status")
def council_status_cmd(
    run_dir: Path = typer.Option(..., "--run-dir", help="Run directory"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Output JSON status"),
):
    """Inspect the current status and artifact provenance of a Council deliberation."""
    import json
    from herdr_master.pipeline import council

    try:
        res = council.get_status(run_dir=run_dir)
        if json_output:
            typer.echo(json.dumps(res, indent=2))
        else:
            phase = res.get("phase")
            active_round = res.get("active_round")
            console.print(
                f"Council Run Phase: [cyan]{phase}[/cyan], Active Round: [cyan]{active_round}[/cyan]"
            )
    except (council.CouncilError, ValueError, FileNotFoundError) as err:
        _abort(str(err))


@app.command("config")
def config_cmd(
    path: Optional[str] = typer.Option(None, "--path", "-p", help="Project path or config file"),
    json_output: bool = typer.Option(True, "--json/--no-json", help="Output JSON format"),
) -> None:
    """Show effective project configuration (read-only discovery)."""
    from herdr_master.core.config import load_project_config

    try:
        cfg = load_project_config(path)
        if json_output:
            typer.echo(cfg.model_dump_json(indent=2))
        else:
            console.print(cfg)
    except Exception as err:
        _abort(str(err))



@app.command()
def tui(
    session: Optional[str] = typer.Option(None, "--session", help=_SESSION_HELP),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Run TUI in demo mode with pre-populated Ergo pipeline data",
    ),
):
    """Launch the interactive terminal management cockpit (AiHeadDev)."""
    import shutil
    # `aihead` is the herdr-master console script. Do not treat it as AiHeadDev.
    executable = shutil.which("aiheaddev")
    if executable:
        argv = [executable]
        if session:
            argv.extend(["--session", session])
        if demo:
            argv.append("--demo")
        os.execvp(executable, argv)

    # Check if module is importable via current python
    try:
        import importlib.util
        if importlib.util.find_spec("aiheaddev") is not None:
            cmd = [sys.executable, "-m", "aiheaddev.cli"]
            if session:
                cmd.extend(["--session", session])
            if demo:
                cmd.append("--demo")
            os.execvp(sys.executable, cmd)
    except Exception:
        pass

    console.print(
        "[bold red]AiHead-Cockpit is not installed.[/bold red]\n"
        "The TUI cockpit has been decoupled into an independent distribution.\n\n"
        "Install it via:\n"
        "  [cyan]pip install aiheaddev[/cyan] (or [cyan]uv pip install aiheaddev[/cyan])\n"
        "Repository: https://github.com/pank85/AiHead-Cockpit"
    )
    raise typer.Exit(code=1)


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
        help="Overwrite existing non-Hermes files or links; Hermes unowned targets are always protected",
    ),
) -> None:
    """Install canonical skills across all supported harnesses.

    The installed set comes from the canonical skill catalog. Council skills are
    included when that catalog contains them; this command does not hard-code a
    skill list.
    """
    run_install_skills(force=force)


_AIHEAD_WORKSPACE_LABEL = "ai-head"
_PROBE_TIMEOUT_SECONDS = 1.0
_INHERITED_HERDR_KEYS = (
    "HERDR_ENV",
    "HERDR_SOCKET_PATH",
    "HERDR_SOCKET",
    "HERDR_WORKSPACE_ID",
    "HERDR_PANE_ID",
    "HERDR_TAB_ID",
)
_PROBE_ABSENCE = (HerdrConnectionError, TimeoutError, ConnectionError, OSError)


@contextmanager
def _present_selected_session(ctx: HerdrContext, workspace_id: Optional[str]):
    """Present the selected session as inherited for one launch_ai_head call.

    Outside Herdr, launch_ai_head provisions an isolated session. Standalone
    aihead must open AiHead in the already selected session, so that session is
    visible as the inherited context only while the launcher runs.
    """
    if ctx.is_inside_herdr:
        yield
        return
    saved = {key: os.environ.get(key) for key in _INHERITED_HERDR_KEYS}
    os.environ["HERDR_ENV"] = "1"
    os.environ["HERDR_SOCKET_PATH"] = str(ctx.socket_path)
    os.environ.pop("HERDR_SOCKET", None)
    if workspace_id:
        os.environ["HERDR_WORKSPACE_ID"] = workspace_id
    else:
        os.environ.pop("HERDR_WORKSPACE_ID", None)
    os.environ.pop("HERDR_PANE_ID", None)
    os.environ.pop("HERDR_TAB_ID", None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _workspace_id_of(workspace: Any) -> Optional[str]:
    workspace_id = getattr(workspace, "workspace_id", None)
    if workspace_id is None and isinstance(workspace, dict):
        workspace_id = workspace.get("workspace_id")
    if workspace_id:
        return str(workspace_id)
    return None


def _workspace_id_from_snapshot(ctx: HerdrContext, snapshot: Any) -> Optional[str]:
    workspaces = getattr(snapshot, "workspaces", None)
    if workspaces is None and isinstance(snapshot, dict):
        workspaces = snapshot.get("workspaces")
    ids = [wid for wid in (_workspace_id_of(item) for item in list(workspaces or [])) if wid]
    if ctx.workspace_id and ctx.workspace_id in ids:
        return str(ctx.workspace_id)
    focused = getattr(snapshot, "focused_workspace_id", None)
    if focused is None and isinstance(snapshot, dict):
        focused = snapshot.get("focused_workspace_id")
    if focused and str(focused) in ids:
        return str(focused)
    if ids:
        return ids[0]
    return None


def _created_workspace_id(created: Any) -> Optional[str]:
    if not isinstance(created, dict):
        return None
    nested = created.get("workspace")
    if isinstance(nested, dict) and nested.get("workspace_id"):
        return str(nested["workspace_id"])
    if created.get("workspace_id"):
        return str(created["workspace_id"])
    return None


async def _probe_selected_session(ctx: HerdrContext) -> tuple[bool, Optional[str]]:
    """Return whether the selected daemon is live and which workspace to adopt.

    Connection absence or timeout means the daemon is not usable. RPC and
    programming errors propagate so they are not disguised as absence.
    """
    socket_path = Path(ctx.socket_path)
    if not ctx.is_inside_herdr and not socket_path.exists():
        return False, None
    client = HerdrClient(
        session=ctx.session_name,
        socket_path=socket_path,
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
        snapshot = await client.get_snapshot()
    except _PROBE_ABSENCE:
        return False, None
    return True, _workspace_id_from_snapshot(ctx, snapshot)


def _require_herdr_executable(purpose: str) -> None:
    if shutil.which("herdr") is None:
        _abort(
            "The herdr executable is not on PATH; cannot "
            f"{purpose}. Install Herdr and retry. "
            "Refusing to allocate panes or fall back to a headless turn."
        )


def _effective_aihead_cwd(cli_cwd: Optional[Path], configured_cwd: Optional[str]) -> Path:
    if cli_cwd is not None:
        candidate = cli_cwd
    elif configured_cwd:
        candidate = Path(configured_cwd)
    else:
        candidate = Path.cwd()
    resolved = candidate.expanduser().resolve()
    if not resolved.is_dir():
        raise FileNotFoundError(
            f"AiHead working directory does not exist or is not a directory: {resolved}"
        )
    return resolved


def _effective_split(
    secondary: bool,
    split: Optional[bool],
    configured_split: Optional[bool],
) -> bool:
    if secondary:
        return True
    if split is not None:
        return split
    if configured_split is not None:
        return configured_split
    return True


def _config_path_for_launch(config: Optional[Path], cwd: Optional[Path]) -> Optional[Path]:
    if config is not None:
        return config
    if cwd is not None:
        return find_project_config_path(cwd)
    return find_project_config_path()


async def _dispatch_standalone_aihead(
    *,
    ctx: HerdrContext,
    kind: Any,
    model_slug: str,
    effort_level: Any,
    dangerously_skip_permissions: bool,
    effective_split: bool,
    workspace: Optional[str],
    task: Optional[str],
    session: Optional[str],
    attach: bool,
    effective_cwd: Path,
    config_path: Optional[Path],
) -> Any:
    """Adopt or create one workspace, then launch interactive AiHead there.

    This path never calls the headless decision executor and never starts
    AiHeadDev.
    """
    daemon_live, existing_workspace = await _probe_selected_session(ctx)
    must_start = not daemon_live
    must_create = existing_workspace is None and workspace is None
    if must_start or (not ctx.is_inside_herdr and attach):
        purpose = "start the Herdr server" if must_start else "attach to the Herdr session"
        _require_herdr_executable(purpose)
    if must_start or must_create:
        await ensure_herdr_server(Path(ctx.socket_path), session_name=ctx.session_name)

    adopted_workspace = existing_workspace
    if must_create:
        client = HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)
        created = await client.create_workspace(
            label=_AIHEAD_WORKSPACE_LABEL,
            focus=True,
            cwd=str(effective_cwd),
        )
        adopted_workspace = _created_workspace_id(created)
        if not adopted_workspace:
            _abort(
                "Herdr workspace create returned no workspace_id. Refusing to launch AiHead."
            )

    if workspace is not None:
        launch_workspace: Optional[str] = workspace
    elif ctx.is_inside_herdr and not must_create:
        # Keep the current pane so the existing split layout still applies.
        launch_workspace = None
    else:
        launch_workspace = adopted_workspace
    launch_session = session if ctx.is_inside_herdr else ctx.session_name
    present_id = None if ctx.is_inside_herdr else (adopted_workspace or launch_workspace)

    shown = f"{kind.value} {model_slug}"
    if effort_level is not None:
        shown = f"{shown} effort {effort_level.value}"
    console.print(f"[bold cyan]🤖 Launching AiHead[/bold cyan] ({shown})...")
    with _present_selected_session(ctx, present_id):
        return await launch_ai_head(
            client=HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path),
            harness=kind,
            model=model_slug,
            effort=effort_level,
            dangerously_skip_permissions=dangerously_skip_permissions,
            split=effective_split,
            workspace=launch_workspace,
            initial_task=task,
            session=launch_session,
            cwd=str(effective_cwd),
            config_path=config_path,
        )


def _print_standalone_launch_result(res: Any, fallback_session: str) -> str:
    if isinstance(res, dict):
        ws_id = res.get("workspace_id")
        pane_id = res.get("pane_id")
        target_session = res.get("session", fallback_session)
        is_split = res.get("split", False)
        sec_pane = res.get("secondary_pane_id")
    else:
        ws_id = None
        pane_id = None
        target_session = fallback_session
        is_split = False
        sec_pane = None

    if is_split and sec_pane:
        console.print(
            f"[green]✓ AiHead launched in split layout![/green]\n"
            f"  Session: {target_session}\n"
            f"  Workspace: {ws_id}\n"
            f"  Primary Pane (AiHead): {pane_id}\n"
            f"  Secondary Pane (active): {sec_pane}"
        )
    else:
        console.print(
            f"[green]✓ AiHead launched successfully![/green]\n"
            f"  Session: {target_session}\n"
            f"  Workspace: {ws_id}\n"
            f"  Pane: {pane_id}"
        )
    return str(target_session)


@aihead_app.callback(invoke_without_command=True)
def launch_standalone_aihead(
    harness: Optional[str] = typer.Option(
        None,
        "--harness",
        help="Interactive harness override (agy, pi, claude, opencode, hermes).",
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Model slug override.",
    ),
    effort: Optional[str] = typer.Option(
        None,
        "--effort",
        help="Reasoning effort override (low, medium, high, max).",
    ),
    dangerously_skip_permissions: Optional[bool] = typer.Option(
        None,
        "--skip-permissions/--no-skip-permissions",
        help="Override AiHead dangerously_skip_permissions. Omitted uses saved config, then True.",
    ),
    split: Optional[bool] = typer.Option(
        None,
        "--split/--no-split",
        help=(
            "Split the active Herdr screen so AiHead is primary "
            "and the active pane is secondary."
        ),
    ),
    secondary: bool = typer.Option(
        False,
        "--secondary",
        help="Enforce screen split with active pane becoming secondary.",
    ),
    workspace: Optional[str] = typer.Option(
        None,
        "--workspace",
        "-w",
        help="Target workspace label or ID (creates standalone tab/workspace).",
    ),
    task: Optional[str] = typer.Option(
        None,
        "--task",
        "-t",
        help="Initial instruction or objective for AiHead.",
    ),
    session: Optional[str] = typer.Option(
        None,
        "--session",
        help=_SESSION_HELP,
    ),
    attach: bool = typer.Option(
        True,
        "--attach/--no-attach",
        help="Attach to Herdr if outside Herdr",
    ),
    cwd: Optional[Path] = typer.Option(
        None,
        "--cwd",
        "--folder",
        "-C",
        help="Initial folder/directory for AiHead to work in.",
    ),
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        help="Optional path to .herdr-master.toml configuration file.",
    ),
) -> None:
    """Launch an interactive AiHead session in a Herdr workspace."""
    try:
        ctx = resolve_launch_context(session)
    except _RESOLVE_ERRORS as err:
        _abort(str(err))

    if config is not None and not Path(config).expanduser().exists():
        _abort(f"Configuration file not found: {config}")
    try:
        project_cfg = load_project_config(config if config is not None else cwd)
    except tomllib.TOMLDecodeError as err:
        _abort(f"Invalid AiHead configuration: {err}")
    except _RESOLVE_ERRORS as err:
        _abort(str(err))
    except Exception as err:
        _abort(f"Invalid AiHead configuration: {err}")

    head_cfg = project_cfg.ai_head
    try:
        eff_harness = (
            harness
            if harness is not None
            else (
                head_cfg.binding.kind.value
                if head_cfg.binding and head_cfg.binding.kind
                else None
            )
        )
        eff_model = (
            model if model is not None else (head_cfg.binding.model if head_cfg.binding else None)
        )
        eff_effort = (
            effort
            if effort is not None
            else (
                head_cfg.binding.effort.value
                if head_cfg.binding and head_cfg.binding.effort
                else None
            )
        )
        kind, model_slug, effort_level = resolve_coordinator_binding(
            eff_harness,
            eff_model,
            eff_effort,
        )
        eff_skip = (
            dangerously_skip_permissions
            if dangerously_skip_permissions is not None
            else head_cfg.dangerously_skip_permissions
        )
        effective_cwd = _effective_aihead_cwd(cwd, head_cfg.cwd if head_cfg else None)
    except _RESOLVE_ERRORS as err:
        _abort(str(err))

    config_path = _config_path_for_launch(config, cwd)
    try:
        res = asyncio.run(
            _dispatch_standalone_aihead(
                ctx=ctx,
                kind=kind,
                model_slug=model_slug,
                effort_level=effort_level,
                dangerously_skip_permissions=eff_skip,
                effective_split=_effective_split(
                    secondary,
                    split,
                    head_cfg.split if head_cfg else None,
                ),
                workspace=workspace,
                task=task,
                session=session,
                attach=attach,
                effective_cwd=effective_cwd,
                config_path=config_path,
            )
        )
    except typer.Exit:
        raise
    except _RESOLVE_ERRORS as err:
        _abort(str(err))
    except Exception as err:
        _abort(f"AiHead launch failed: {err}")

    target_session = _print_standalone_launch_result(res, ctx.session_name)
    if not ctx.is_inside_herdr and attach:
        console.print(
            f"[yellow]Attaching to isolated Herdr session '{target_session}'...[/yellow]"
        )
        os.execvp("herdr", ["herdr", "--session", target_session])


if __name__ == "__main__":
    app()
