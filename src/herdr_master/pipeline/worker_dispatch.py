"""Fail-closed contracts for controlled worker dispatch.

This is an application-level cooperative boundary, not a sandbox. Option 1
enforces tiers only on the controlled dispatch path; direct agent launches are
not intercepted. A caller with independent shell or tool access can launch agents
outside this boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
)

from herdr_master.core.available_models import enabled_model_ids
from herdr_master.core.catalog import CatalogError, describe_herdr_agent, ensure_herdr_routable
from herdr_master.core.config import load_effective_models_state
from herdr_master.core.model_ratings import effective_worker_tier, lookup_rating


class WorkerDispatchError(RuntimeError):
    """Structured dispatch rejection; never implies a worker was started."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @field_validator("schema_version", "classifier_version", mode="before", check_fields=False)
    @classmethod
    def _strict_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value


class Difficulty(_Strict):
    cross_system_invariant: StrictBool
    security_or_data_loss: StrictBool
    novel_algorithm_or_ambiguous_architecture: StrictBool
    multiple_concurrent_state_boundaries: StrictBool


def classify_difficulty(facts: Difficulty | dict) -> bool:
    """Return very-hard from all four declared, independently reviewed facts."""
    try:
        facts = Difficulty.model_validate(facts)
    except ValidationError as exc:
        raise WorkerDispatchError("invalid_difficulty", str(exc)) from exc
    return (
        facts.security_or_data_loss
        or sum(
            (
                facts.cross_system_invariant,
                facts.novel_algorithm_or_ambiguous_architecture,
                facts.multiple_concurrent_state_boundaries,
            )
        )
        >= 2
    )


class WorkerTask(_Strict):
    id: StrictStr = Field(min_length=1)
    scope: StrictStr = Field(min_length=1)
    prompt: StrictStr = Field(min_length=1)
    kind: StrictStr = Field(min_length=1)
    model: StrictStr = Field(min_length=1)
    effort: StrictStr | None
    difficulty: Difficulty
    attempt: StrictInt = Field(ge=1)


class WorkerManifest(_Strict):
    schema_version: Literal[1]
    classifier_version: Literal[1]
    run_id: StrictStr = Field(min_length=1)
    workspace_id: StrictStr = Field(min_length=1)
    worktree: StrictStr = Field(min_length=1)
    mode: Literal["herdr", "native"]
    worker_mode: Literal["balanced", "quality", "fast", "frugal"] = "balanced"
    native_host: StrictStr | None
    parent_pane_id: StrictStr = Field(min_length=1)
    session: StrictStr = Field(min_length=1)
    socket_path: StrictStr = Field(min_length=1)
    snapshot_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    run_policy_path: StrictStr = Field(min_length=1)
    run_policy_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    tasks: list[WorkerTask] = Field(min_length=1)


class WorkerRoute(_Strict):
    kind: StrictStr = Field(min_length=1)
    model: StrictStr = Field(min_length=1)
    enabled: StrictBool


class WorkerRunPolicy(_Strict):
    schema_version: Literal[1]
    run_id: StrictStr = Field(min_length=1)
    worktree: StrictStr = Field(min_length=1)
    mode: Literal["herdr", "native"]
    native_host: StrictStr | None
    run_squad_path: StrictStr | None
    workers_override: StrictStr | None
    override_provenance: Literal["explicit", "none"]
    worker_pool: list[WorkerRoute]


class WorkerApproval(_Strict):
    schema_version: Literal[1]
    approver_type: Literal["human", "aihead"]
    approver: StrictStr = Field(min_length=1)
    manifest_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_digest: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


def canonical_digest(value: object) -> str:
    """SHA256 of UTF-8 canonical JSON (sorted keys, no whitespace)."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def policy_snapshot(worktree: Path) -> dict[str, object]:
    """Resolve current inherited model policy; hash this for manifest approval."""
    state = load_effective_models_state(worktree)
    return {
        "available": state.available.model_dump(mode="json") if state.available else None,
        "ratings": {
            h: {m: r.model_dump(mode="json") for m, r in models.items()}
            for h, models in state.ratings.items()
        },
    }


def create_run_policy(
    *,
    run_id: str,
    worktree: Path,
    mode: str,
    native_host: str | None,
    run_squad_path: Path | None,
    workers_override: str | None,
) -> WorkerRunPolicy:
    """Resolve run inputs exactly as squad creation does, without guessing overrides."""
    from herdr_master.pipeline.squad import resolve_squad

    worktree = Path(worktree).resolve(strict=True)
    squad_path = Path(run_squad_path).resolve(strict=True) if run_squad_path is not None else None
    resolved = resolve_squad(
        worktree,
        mode=mode,
        native_harness=native_host,
        run_squad_path=squad_path,
        overrides={"workers": workers_override} if workers_override is not None else None,
    )
    return WorkerRunPolicy(
        schema_version=1,
        run_id=run_id,
        worktree=str(worktree),
        mode=mode,
        native_host=native_host,
        run_squad_path=str(squad_path) if squad_path else None,
        workers_override=workers_override,
        override_provenance="explicit" if workers_override is not None else "none",
        worker_pool=[
            WorkerRoute(kind=b.kind.value, model=b.model, enabled=b.enabled)
            for b in resolved.squad.worker_pool
        ],
    )


def validate_run_policy(manifest: WorkerManifest, run_policy: WorkerRunPolicy) -> None:
    """Check frozen run identity, pool and explicit override provenance."""
    if not Path(manifest.run_policy_path).is_absolute():
        raise WorkerDispatchError("run_policy_unbound", "Run policy path must be absolute.")
    if (
        run_policy.run_id != manifest.run_id
        or canonical_digest(run_policy.model_dump(mode="json")) != manifest.run_policy_digest
    ):
        raise WorkerDispatchError("run_policy_unbound", "Run policy digest or run ID differs.")
    if (
        run_policy.worktree != manifest.worktree
        or run_policy.mode != manifest.mode
        or run_policy.native_host != manifest.native_host
    ):
        raise WorkerDispatchError("run_policy_unbound", "Run policy context differs from manifest.")
    routes = [(r.kind, r.model) for r in run_policy.worker_pool]
    if not routes or len(set(routes)) != len(routes):
        raise WorkerDispatchError("run_policy_unbound", "Run pool must be nonempty and unique.")
    if any(k != k.strip().lower() or m != m.strip() or not k or not m for k, m in routes):
        raise WorkerDispatchError("run_policy_unbound", "Run pool contains non-normalized routes.")
    if run_policy.override_provenance == "none":
        if run_policy.workers_override is not None:
            raise WorkerDispatchError("run_policy_unbound", "Override provenance is inconsistent.")
    else:
        if not run_policy.workers_override:
            raise WorkerDispatchError("run_policy_unbound", "Explicit workers override is missing.")
        from herdr_master.pipeline.squad import _workers_from_override

        try:
            parsed = _workers_from_override(
                run_policy.workers_override, mode=manifest.mode, native_harness=manifest.native_host
            )
            expected = [(b.kind.value, b.model) for b in parsed]
        except (ValueError, CatalogError) as exc:
            raise WorkerDispatchError(
                "run_policy_unbound", f"Invalid workers override: {exc}"
            ) from exc
        if expected != routes or not all(r.enabled for r in run_policy.worker_pool):
            raise WorkerDispatchError(
                "run_policy_unbound", "Workers override differs from resolved pool."
            )


def load_run_policy(manifest: WorkerManifest) -> WorkerRunPolicy:
    """Re-read and validate the approved run policy from its bound path."""
    try:
        policy = WorkerRunPolicy.model_validate(_read_json(Path(manifest.run_policy_path)))
        validate_run_policy(manifest, policy)
        resolved = create_run_policy(
            run_id=policy.run_id,
            worktree=Path(policy.worktree),
            mode=policy.mode,
            native_host=policy.native_host,
            run_squad_path=Path(policy.run_squad_path) if policy.run_squad_path else None,
            workers_override=policy.workers_override,
        )
        if resolved != policy:
            raise WorkerDispatchError("run_policy_unbound", "Resolved run pool changed; reapprove.")
        return policy
    except (OSError, ValueError, CatalogError, ValidationError, WorkerDispatchError) as exc:
        raise WorkerDispatchError("run_policy_unbound", f"Cannot bind run policy: {exc}") from exc


def validate_worker_assignment(
    manifest: WorkerManifest,
    approval: WorkerApproval,
    task_id: str,
    snapshot: dict[str, object],
    run_policy: WorkerRunPolicy,
) -> WorkerTask:
    """Pure approval and eligibility gate against one resolved policy snapshot."""
    validate_run_policy(manifest, run_policy)
    if len({task.id for task in manifest.tasks}) != len(manifest.tasks):
        raise WorkerDispatchError("duplicate_task", "Task IDs must be unique.")
    if not Path(manifest.worktree).is_absolute() or not Path(manifest.socket_path).is_absolute():
        raise WorkerDispatchError("invalid_path", "Worktree and socket paths must be absolute.")
    digest = canonical_digest(snapshot)
    if (
        digest != manifest.snapshot_digest
        or digest != approval.snapshot_digest
        or canonical_digest(manifest.model_dump(mode="json")) != approval.manifest_digest
    ):
        raise WorkerDispatchError(
            "approval_mismatch", "Manifest, approval or current policy digest differs; reapprove."
        )
    tasks = [task for task in manifest.tasks if task.id == task_id]
    if not tasks:
        raise WorkerDispatchError("unknown_task", f"No approved task {task_id!r}.")
    task = tasks[0]
    if manifest.mode != "herdr":
        raise WorkerDispatchError(
            "native_unsupported",
            "Native controlled dispatch lacks tool interception; "
            "use a supported controlled Herdr run.",
        )
    from herdr_master.core.available_models import AvailableModels
    from herdr_master.core.model_ratings import validate_ratings_map

    try:
        available = (
            AvailableModels.model_validate(snapshot["available"])
            if snapshot["available"] is not None
            else None
        )
        ratings = validate_ratings_map(snapshot["ratings"])
        if task.kind != task.kind.strip().lower() or task.model != task.model.strip():
            raise WorkerDispatchError(
                "invalid_binding", "Binding must use exact normalized harness and model IDs."
            )
        if not any(
            row.enabled and row.kind == task.kind and row.model == task.model
            for row in run_policy.worker_pool
        ):
            raise WorkerDispatchError(
                "not_in_worker_pool", "Model is not in the approved run pool."
            )
        allow = enabled_model_ids(available, "herdr", task.kind)
        if allow is not None and task.model not in allow:
            raise WorkerDispatchError(
                "not_allowed", "Model is absent from the effective allowlist."
            )
        ensure_herdr_routable(
            task.kind, task.model, task.effort, role="worker", require_effort=True
        )
        rating = lookup_rating(ratings, task.kind, task.model)
        if rating is None:
            raise WorkerDispatchError("unknown_tier", "No rating for exact worker binding.")
        tier = effective_worker_tier(rating)
        if tier not in (1, 2, 3):
            raise WorkerDispatchError("unknown_tier", "Worker tier is unknown.")
        from herdr_master.pipeline.squad import worker_tier_allowed

        very_hard = classify_difficulty(task.difficulty)
        if not worker_tier_allowed(tier, very_hard=very_hard, worker_mode=manifest.worker_mode):
            raise WorkerDispatchError(
                "ineligible_tier",
                "Tier 3 is not eligible for ordinary work unless very-hard facts qualify "
                f"the task or the approved worker_mode is quality (mode: {manifest.worker_mode}).",
            )
    except WorkerDispatchError:
        raise
    except (ValueError, TypeError, KeyError, CatalogError) as exc:
        raise WorkerDispatchError("invalid_policy", str(exc)) from exc
    return task


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkerDispatchError("invalid_file", f"Cannot read {path}: {exc}") from exc


async def _start_worker(
    manifest: WorkerManifest, approval: WorkerApproval, task_id: str
) -> dict[str, object]:
    from herdr_master.herdr.client import HerdrClient
    from herdr_master.pipeline.parent_contract import launch_and_prompt, resolve_parent_route

    parent = resolve_parent_route(manifest.parent_pane_id, delegated=True)
    client = HerdrClient(session=manifest.session, socket_path=Path(manifest.socket_path))
    snap = await client.get_snapshot()
    if not any(w.workspace_id == manifest.workspace_id for w in snap.workspaces):
        raise WorkerDispatchError("workspace_mismatch", "Approved workspace is absent from socket.")
    if not any(p.pane_id == parent and p.workspace_id == manifest.workspace_id for p in snap.panes):
        raise WorkerDispatchError("parent_mismatch", "Direct parent is not in approved workspace.")
    task = validate_worker_assignment(
        manifest,
        approval,
        task_id,
        policy_snapshot(Path(manifest.worktree)),
        load_run_policy(manifest),
    )
    descriptor = describe_herdr_agent(task.kind, task.model, task.effort, "worker")
    key = canonical_digest([manifest.run_id, task.id, task.attempt])
    directory = Path.home() / ".local" / "state" / "herdr-master" / "worker-dispatch"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(directory / key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise WorkerDispatchError(
            "duplicate_attempt", "Attempt already reserved; reconcile before retry."
        ) from exc
    with os.fdopen(fd, "w") as file:
        file.write(canonical_digest(manifest.model_dump(mode="json")))
    try:
        allocated = await client.create_tab(
            workspace_id=manifest.workspace_id, label="worker", focus=False, cwd=manifest.worktree
        )
        root = allocated.get("root_pane") or {}
        tab = allocated.get("tab") or {}
        pane_id = root.get("pane_id") or allocated.get("pane_id")
        tab_id = tab.get("tab_id") or root.get("tab_id")
        if not pane_id or not tab_id:
            raise WorkerDispatchError(
                "allocation_failed", "Missing allocated pane or tab; reconcile before retry."
            )
        validate_worker_assignment(
            manifest,
            approval,
            task_id,
            policy_snapshot(Path(manifest.worktree)),
            load_run_policy(manifest),
        )
        prefix = descriptor.get("prompt_prefix")
        prompt = f"{prefix}\n\n{task.prompt}" if prefix else task.prompt
        run_dir = await launch_and_prompt(
            client,
            pane_id=pane_id,
            tab_id=tab_id,
            socket_path=Path(manifest.socket_path),
            parent_pane_id=parent,
            delegated=True,
            name="worker-" + key[:16],
            kind=descriptor["kind"],
            args=descriptor["argv"],
            cwd=manifest.worktree,
            prompt=prompt,
        )
        return {
            "pane_id": pane_id,
            "tab_id": tab_id,
            "watch_run_dir": str(run_dir) if run_dir else None,
        }
    except WorkerDispatchError:
        raise
    except Exception as exc:
        raise WorkerDispatchError(
            "launch_failed", f"Start or prompt failed; reconcile reserved attempt: {exc}"
        ) from exc


def dispatch_worker(*, manifest_path: Path, task_id: str, approval_path: Path) -> dict[str, object]:
    """Validate and start the exact approved assignment through Herdr."""
    try:
        manifest = WorkerManifest.model_validate(_read_json(manifest_path))
        approval = WorkerApproval.model_validate(_read_json(approval_path))
    except ValidationError as exc:
        raise WorkerDispatchError("invalid_manifest", str(exc)) from exc
    try:
        validate_worker_assignment(
            manifest,
            approval,
            task_id,
            policy_snapshot(Path(manifest.worktree)),
            load_run_policy(manifest),
        )
        return asyncio.run(_start_worker(manifest, approval, task_id))
    except WorkerDispatchError:
        raise
    except Exception as exc:
        raise WorkerDispatchError("dispatch_failed", f"Controlled dispatch failed: {exc}") from exc
