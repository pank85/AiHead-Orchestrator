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
from herdr_master.core.config import load_project_config
from herdr_master.core.slug import InvalidBindingSlugError
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.council import CouncilError
from herdr_master.pipeline.launcher import (
    COUNCIL_COORDINATOR_NAME,
    LaunchContextError,
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
            council_cfg = council_cfg.model_copy(update={"members": members})
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
    """Install the /herdr-master, /ergo, /orchestration, and /council skills across all harnesses."""
    run_install_skills(force=force)


if __name__ == "__main__":
    app()
