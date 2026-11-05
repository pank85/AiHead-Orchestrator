"""Machine-readable catalog and squad resolution commands.

Register ``catalog_app`` on the root Typer app from the composition root.
This module does not import ``herdr_master.cli.main``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from herdr_master.core.catalog import CatalogError, catalog_document, qualify_binding_slug
from herdr_master.core.skill_resources import SkillResourceError
from herdr_master.core.slug import InvalidBindingSlugError, parse_binding
from herdr_master.pipeline.squad import (
    SquadValidationError,
    describe_agent,
    parse_squad_overrides,
    resolve_frozen_squad,
    resolve_squad,
    save_run_squad,
)

catalog_app = typer.Typer(
    help="Inspect the harness catalog and resolve squads.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    sys.stdout.flush()


def _fail(message: str, code: int = 1) -> None:
    text = message.strip()
    sys.stderr.write(text + "\n")
    sys.stderr.flush()
    _emit({"ok": False, "error": text})
    raise typer.Exit(code)


def _guard(callback):
    try:
        callback()
    except (
        CatalogError,
        SquadValidationError,
        InvalidBindingSlugError,
        SkillResourceError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValueError,
    ) as err:
        _fail(str(err))


@catalog_app.command("show")
def show_cmd(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
) -> None:
    """Show harness identities, the product squad, and canonical skill paths."""

    def run() -> None:
        payload = catalog_document()
        if json_output:
            _emit(payload)
            return
        skills = ",".join(payload["canonical_skills"])
        sys.stdout.write(f"default_mode={payload['default_mode']} skills={skills}\n")

    _guard(run)


@catalog_app.command("squad")
def squad_cmd(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
    mode: Annotated[str, typer.Option("--mode", help="herdr or native.")] = "herdr",
    harness: Annotated[
        Optional[str],
        typer.Option("--harness", help="Native host. Required for --mode native."),
    ] = None,
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML. Absent model fields inherit the catalog."),
    ] = None,
    run_squad: Annotated[
        Optional[Path],
        typer.Option("--run-squad", help="Run squad.toml. Omitted fields inherit lower layers."),
    ] = None,
    models: Annotated[
        Optional[str],
        typer.Option("--models", help="Compact role:binding list. Individual flags win."),
    ] = None,
    scout: Annotated[Optional[str], typer.Option("--scout")] = None,
    architect: Annotated[Optional[str], typer.Option("--architect")] = None,
    workers: Annotated[Optional[str], typer.Option("--workers")] = None,
    coder: Annotated[
        Optional[str],
        typer.Option("--coder", "--coders", help="Alias for --workers."),
    ] = None,
    reviewer: Annotated[Optional[str], typer.Option("--reviewer")] = None,
    from_json: Annotated[
        Optional[str],
        typer.Option("--from-json", help="Frozen squad JSON path, or - for stdin."),
    ] = None,
    write_snapshot: Annotated[
        Optional[Path],
        typer.Option("--write-snapshot", help="Absolute path to write a run squad snapshot."),
    ] = None,
) -> None:
    """Resolve the product squad, project overrides, a run snapshot, or a frozen document."""

    def run() -> None:
        if from_json is not None:
            extras = [
                project,
                run_squad,
                models,
                scout,
                architect,
                workers,
                coder,
                reviewer,
                harness,
            ]
            if any(extras):
                _fail(
                    "Do not combine --from-json with --project, --run-squad, --models, "
                    "--harness, or role flags. The document is frozen and is not re-merged."
                )
            if from_json == "-":
                raw_text = sys.stdin.read()
            else:
                raw_text = Path(from_json).read_text(encoding="utf-8")
            document = json.loads(raw_text)
            resolved = resolve_frozen_squad(document)
        else:
            worker_spec = workers
            if coder is not None:
                if worker_spec is not None and worker_spec != coder:
                    _fail("--workers and --coder disagree. Pass only one worker override.")
                worker_spec = worker_spec or coder
            overrides = parse_squad_overrides(
                models=models,
                scout=scout,
                architect=architect,
                workers=worker_spec,
                reviewer=reviewer,
            )
            resolved = resolve_squad(
                project,
                mode=mode,
                native_harness=harness,
                run_squad_path=run_squad,
                overrides=overrides or None,
            )
        if write_snapshot is not None:
            if not write_snapshot.is_absolute():
                _fail("--write-snapshot requires an absolute path.")
            save_run_squad(
                resolved.squad,
                write_snapshot,
                mode=resolved.mode,
                native_harness=resolved.native_harness,
            )
        payload = resolved.to_dict()
        if write_snapshot is not None:
            payload["snapshot_path"] = str(write_snapshot)
        if json_output:
            _emit(payload)
            return
        sys.stdout.write(
            f"mode={resolved.mode} scout={resolved.squad.scout.to_slug()} "
            f"workers={len(resolved.squad.worker_pool)}\n"
        )

    _guard(run)


@catalog_app.command("agent")
def agent_cmd(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
    mode: Annotated[str, typer.Option("--mode", help="herdr or native.")] = "herdr",
    harness: Annotated[
        Optional[str],
        typer.Option("--harness", help="Native host. Required for --mode native."),
    ] = None,
    binding: Annotated[
        Optional[str],
        typer.Option("--binding", help="kind:model[:effort] or a native name."),
    ] = None,
    role: Annotated[
        Optional[str],
        typer.Option("--role", help="scout, architect, coder, reviewer, master, or watcher."),
    ] = None,
) -> None:
    """Resolve one binding to Herdr argv or a native invocation descriptor."""

    def run() -> None:
        if not binding or not role:
            _fail("--binding and --role are required.")
        qualified = qualify_binding_slug(binding, mode=mode, native_harness=harness)
        parsed = parse_binding(qualified)
        payload = describe_agent(parsed, mode=mode, role=role, native_harness=harness)
        if json_output:
            _emit(payload)
            return
        sys.stdout.write(
            f"mode={payload.get('mode')} role={payload.get('role')} kind={payload.get('kind')}\n"
        )

    _guard(run)
