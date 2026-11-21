"""Controlled worker dispatch command; validation and launch stay together."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer

worker_app = typer.Typer(
    help=(
        "Prepare and dispatch an approved worker assignment through controlled dispatch only; "
        "direct agent launches are not intercepted."
    ),
    no_args_is_help=True,
)
manifest_app = typer.Typer(help="Inspect a worker manifest.", no_args_is_help=True)
approval_app = typer.Typer(help="Record an external approval.", no_args_is_help=True)
run_policy_app = typer.Typer(help="Inspect resolved run policy records.", no_args_is_help=True)
worker_app.add_typer(run_policy_app, name="run-policy")
worker_app.add_typer(manifest_app, name="manifest")
worker_app.add_typer(approval_app, name="approval")


def _write_new_json(path: Path, document: object) -> None:
    """Publish private JSON atomically without overwriting an existing path."""
    payload = (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(tmp, path)
    finally:
        os.unlink(tmp)


def _failure(exc: Exception) -> None:
    code = getattr(exc, "code", "invalid_input")
    sys.stdout.write(json.dumps({"ok": False, "code": code, "error": str(exc)}) + "\n")
    raise typer.Exit(1) from exc


def _load_manifest(path: Path):
    from herdr_master.pipeline.worker_dispatch import WorkerManifest

    return WorkerManifest.model_validate(json.loads(path.read_text(encoding="utf-8")))


def _inspect_manifest(manifest):
    from herdr_master.core.model_ratings import (
        effective_worker_tier,
        lookup_rating,
        validate_ratings_map,
    )
    from herdr_master.pipeline.squad import tier_preference_rank
    from herdr_master.pipeline.worker_dispatch import (
        WorkerApproval,
        WorkerDispatchError,
        canonical_digest,
        classify_difficulty,
        load_run_policy,
        policy_snapshot,
        validate_worker_assignment,
    )

    run_policy = load_run_policy(manifest)
    snapshot = policy_snapshot(Path(manifest.worktree))
    snapshot_digest = canonical_digest(snapshot)
    manifest_digest = canonical_digest(manifest.model_dump(mode="json"))
    if snapshot_digest != manifest.snapshot_digest:
        raise WorkerDispatchError("policy_drift", "Current policy differs from manifest snapshot.")
    # A temporary object solely for preflight; it is never emitted or persisted as approval.
    preflight = WorkerApproval.model_validate(
        {
            "schema_version": 1,
            "approver_type": "human",
            "approver": "preflight only",
            "manifest_digest": manifest_digest,
            "snapshot_digest": snapshot_digest,
        }
    )
    ratings = validate_ratings_map(snapshot["ratings"])
    tasks = []
    for task in manifest.tasks:
        validate_worker_assignment(manifest, preflight, task.id, snapshot, run_policy)
        rating = lookup_rating(ratings, task.kind, task.model)
        tier = effective_worker_tier(rating) if rating is not None else None
        tasks.append(
            {
                "id": task.id,
                "scope": task.scope,
                "kind": task.kind,
                "model": task.model,
                "worker_tier": tier,
                "tier_preference_rank": tier_preference_rank(tier, manifest.worker_mode)
                if tier is not None
                else None,
                "effort": task.effort,
                "very_hard": classify_difficulty(task.difficulty),
                "difficulty": task.difficulty.model_dump(mode="json"),
            }
        )
    return {
        "worker_mode": manifest.worker_mode,
        "enforcement_scope": "controlled_dispatch_only",
        "manifest_digest": manifest_digest,
        "snapshot_digest": snapshot_digest,
        "manifest": manifest.model_dump(mode="json"),
        "run_policy": run_policy.model_dump(mode="json"),
        "tasks": tasks,
    }


@worker_app.command("snapshot")
def snapshot_cmd(
    worktree: Annotated[Path, typer.Option("--worktree", help="Absolute project worktree.")],
    output: Annotated[
        Path, typer.Option("--output", help="New snapshot file (never overwritten).")
    ],
) -> None:
    """Capture current policy and print the digest for the manifest."""
    from herdr_master.pipeline.worker_dispatch import canonical_digest, policy_snapshot

    try:
        if not worktree.is_absolute() or not worktree.is_dir():
            raise ValueError("--worktree must be an existing absolute directory")
        snapshot = policy_snapshot(worktree)
        digest = canonical_digest(snapshot)
        _write_new_json(output, snapshot)
        sys.stdout.write(
            json.dumps({"ok": True, "snapshot_digest": digest, "output": str(output)}) + "\n"
        )
    except (OSError, ValueError) as exc:
        _failure(exc)


@run_policy_app.command("schema")
def run_policy_schema_cmd() -> None:
    """Print the required resolved run-policy JSON schema."""
    from herdr_master.pipeline.worker_dispatch import WorkerRunPolicy

    sys.stdout.write(json.dumps(WorkerRunPolicy.model_json_schema(), indent=2) + "\n")


@run_policy_app.command("create")
def run_policy_create_cmd(
    run_id: Annotated[str, typer.Option("--run-id", help="Exact existing run ID.")],
    worktree: Annotated[Path, typer.Option("--worktree", help="Absolute run worktree.")],
    mode: Annotated[str, typer.Option("--mode", help="Actual run mode: herdr or native.")],
    output: Annotated[Path, typer.Option("--output", help="New absolute run-policy file.")],
    native_host: Annotated[
        str | None, typer.Option("--native-host", help="Actual native host, if any.")
    ] = None,
    run_squad: Annotated[
        Path | None, typer.Option("--run-squad", help="Actual absolute frozen squad path, if any.")
    ] = None,
    workers: Annotated[
        str | None,
        typer.Option("--workers", help="Exact explicit workers override used by the run."),
    ] = None,
) -> None:
    """Resolve a record from actual run inputs; caller-supplied provenance is not authenticated."""
    from herdr_master.pipeline.worker_dispatch import (
        WorkerDispatchError,
        canonical_digest,
        create_run_policy,
    )

    try:
        if not run_id.strip() or mode not in ("herdr", "native"):
            raise ValueError("A nonblank --run-id and --mode herdr or native are required.")
        if not worktree.is_absolute() or not worktree.is_dir() or not output.is_absolute():
            raise ValueError(
                "--worktree must be an existing absolute directory and --output absolute."
            )
        if run_squad is not None and not run_squad.is_absolute():
            raise ValueError("--run-squad must be absolute.")
        policy = create_run_policy(
            run_id=run_id,
            worktree=worktree,
            mode=mode,
            native_host=native_host,
            run_squad_path=run_squad,
            workers_override=workers,
        )
        document = policy.model_dump(mode="json")
        _write_new_json(output, document)
        sys.stdout.write(
            json.dumps(
                {
                    "ok": True,
                    "run_policy_path": str(output),
                    "run_policy_digest": canonical_digest(document),
                    "provenance_authenticated": False,
                }
            )
            + "\n"
        )
    except (OSError, ValueError, WorkerDispatchError) as exc:
        _failure(exc)


@run_policy_app.command("digest")
def run_policy_digest_cmd(
    path: Annotated[Path, typer.Option("--file", help="Resolved run-policy JSON file.")],
) -> None:
    """Compute a record digest; full provenance validation needs a bound manifest."""
    from herdr_master.pipeline.worker_dispatch import WorkerRunPolicy, canonical_digest

    try:
        if not path.is_absolute():
            raise ValueError("Run policy file path must be absolute.")
        policy = WorkerRunPolicy.model_validate(json.loads(path.read_text(encoding="utf-8")))
        sys.stdout.write(
            json.dumps(
                {
                    "ok": True,
                    "run_policy_digest": canonical_digest(policy.model_dump(mode="json")),
                    "run_policy_path": str(path),
                    "provenance_validated": False,
                }
            )
            + "\n"
        )
    except (OSError, ValueError) as exc:
        _failure(exc)


@manifest_app.command("schema")
def manifest_schema_cmd() -> None:
    """Print the exact versioned manifest JSON schema; no approval is created."""
    from herdr_master.pipeline.worker_dispatch import WorkerManifest

    sys.stdout.write(json.dumps(WorkerManifest.model_json_schema(), indent=2) + "\n")


@manifest_app.command("validate")
def manifest_validate_cmd(
    manifest: Annotated[Path, typer.Option("--manifest", help="Manifest JSON to inspect.")],
) -> None:
    """Preflight all tasks against current policy; this does not approve them."""
    from herdr_master.pipeline.worker_dispatch import WorkerDispatchError

    try:
        result = _inspect_manifest(_load_manifest(manifest))
        sys.stdout.write(json.dumps({"ok": True, "approved": False, **result}) + "\n")
    except (OSError, ValueError, WorkerDispatchError) as exc:
        _failure(exc)


@approval_app.command("create")
def approval_create_cmd(
    manifest: Annotated[Path, typer.Option("--manifest")],
    manifest_digest: Annotated[
        str, typer.Option("--manifest-digest", help="Digest externally approved.")
    ],
    snapshot_digest: Annotated[
        str, typer.Option("--snapshot-digest", help="Policy digest externally approved.")
    ],
    approver_type: Annotated[
        str,
        typer.Option("--approver-type", help="human or aihead; self-reported, not authenticated."),
    ],
    approver: Annotated[
        str, typer.Option("--approver", help="Name of external approver; not authenticated.")
    ],
    output: Annotated[Path, typer.Option("--output", help="New approval file; never overwritten.")],
    confirm_approved: Annotated[
        bool,
        typer.Option(
            "--confirm-approved",
            help="Attest the named approver already approved both exact digests.",
        ),
    ] = False,
) -> None:
    """Record an existing external approval; this command does not authenticate it."""
    from herdr_master.pipeline.worker_dispatch import WorkerApproval, WorkerDispatchError

    try:
        if not confirm_approved:
            raise ValueError(
                "External approval of both digests must be confirmed with --confirm-approved."
            )
        if not approver.strip():
            raise ValueError("Approver must not be blank.")
        document = _load_manifest(manifest)
        inspected = _inspect_manifest(document)
        if (
            manifest_digest != inspected["manifest_digest"]
            or snapshot_digest != inspected["snapshot_digest"]
        ):
            raise WorkerDispatchError(
                "approval_mismatch",
                "Externally approved digests do not match the current manifest and policy.",
            )
        approval = WorkerApproval.model_validate(
            {
                "schema_version": 1,
                "approver_type": approver_type,
                "approver": approver,
                "manifest_digest": manifest_digest,
                "snapshot_digest": snapshot_digest,
            }
        )
        _write_new_json(output, approval.model_dump(mode="json"))
        sys.stdout.write(
            json.dumps({"ok": True, "output": str(output), "identity_authenticated": False}) + "\n"
        )
    except (OSError, ValueError, WorkerDispatchError) as exc:
        _failure(exc)


@worker_app.command("dispatch")
def dispatch_cmd(
    manifest: Annotated[Path, typer.Option("--manifest", help="Approved manifest path.")],
    task_id: Annotated[str, typer.Option("--task-id", help="Exact manifest task ID.")],
    approval: Annotated[Path, typer.Option("--approval", help="Approval record path.")],
) -> None:
    """Validate current policy and start the exact approved worker."""
    from herdr_master.pipeline.worker_dispatch import WorkerDispatchError, dispatch_worker

    try:
        result = dispatch_worker(manifest_path=manifest, task_id=task_id, approval_path=approval)
    except WorkerDispatchError as exc:
        sys.stdout.write(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}) + "\n")
        raise typer.Exit(1) from exc
    sys.stdout.write(json.dumps(result) + "\n")
