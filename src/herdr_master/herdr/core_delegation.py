"""Minimal opt-in single-hop Core delegation helpers."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from herdr_master.herdr import core_client


class CoreDelegationError(Exception):
    """Raised when single-hop Core delegation fails or has an ambiguous outcome."""


@dataclass(frozen=True)
class CoreDelegationResult:
    """Verified outcome of a single-hop Core child spawn."""

    address: str
    state: str
    reason: str | None = None


@dataclass(frozen=True)
class CoreOneChildReport:
    """Verified outcome of a one-child Core delegation and wait workflow."""

    parent: str
    child: str
    exit_code: int | None
    wait_reason: str | None


async def delegate_core_child(
    argv: list[str],
    *,
    cwd: str | Path,
    name: str | None = None,
    labels: dict[str, str] | None = None,
    parent: str | None = None,
    allow_standalone: bool = False,
    wait: bool = False,
    timeout: float | None = None,
    executable: str = "hc",
) -> CoreDelegationResult:
    """Spawn a single-hop child terminal on herdr-core without retrying ambiguous outcomes."""
    if not argv or not all(isinstance(arg, str) and arg for arg in argv):
        raise CoreDelegationError("argv must be a non-empty list of non-empty strings")
    cwd_str = str(cwd).strip()
    if not cwd_str:
        raise CoreDelegationError("cwd must be non-empty")
    effective_timeout = (60.0 if wait else 10.0) if timeout is None else timeout
    if effective_timeout <= 0:
        raise CoreDelegationError("timeout must be positive")

    resolved_parent = (
        parent.strip()
        if isinstance(parent, str)
        else os.environ.get("HC_TERMINAL_ID", "").strip()
    )
    if allow_standalone:
        if isinstance(parent, str) and parent.strip():
            raise CoreDelegationError("cannot combine explicit parent with allow_standalone=True")
        resolved_parent = ""
    elif not resolved_parent:
        raise CoreDelegationError(
            "Core child delegation requires a direct parent (HC_TERMINAL_ID or explicit parent)"
        )

    try:
        result = await core_client.terminal_run(
            argv,
            cwd=cwd_str,
            name=name,
            labels=labels,
            parent=resolved_parent or None,
            no_parent=allow_standalone,
            wait=wait,
            timeout=effective_timeout,
            executable=executable,
        )
    except core_client.CoreClientError as exc:
        raise CoreDelegationError(
            f"Core child spawn failed or outcome unknown; inspect before retrying: {exc}"
        ) from exc

    if not isinstance(result, dict):
        raise CoreDelegationError(
            "Core child spawn returned non-object result; inspect before retrying"
        )
    address = result.get("address")
    if not isinstance(address, str) or not address.strip():
        raise CoreDelegationError(
            "Core child spawn outcome unknown (missing address); inspect before retrying"
        )
    reason = result.get("reason")
    if reason == "spawn_pending":
        raise CoreDelegationError(
            f"Core child spawn is pending for {address}; inspect before retrying"
        )
    state = result.get("state")
    if state not in ("live", "finished"):
        raise CoreDelegationError(
            f"Core child {address} reported unexpected state {state!r}; inspect before retrying"
        )
    return CoreDelegationResult(
        address=address,
        state=state,
        reason=reason if isinstance(reason, str) else None,
    )


async def send_core_child(
    target: str,
    text: str,
    *,
    timeout: float = 30.0,
    executable: str = "hc",
) -> Any:
    """Send a single message to a Core child terminal without retrying ambiguous sends."""
    if not isinstance(target, str) or not target.strip():
        raise CoreDelegationError("target must be a non-empty terminal address")
    if not isinstance(text, str) or not text:
        raise CoreDelegationError("text must be non-empty")
    if timeout <= 0:
        raise CoreDelegationError("timeout must be positive")
    try:
        return await core_client.terminal_send(
            target,
            text,
            timeout=timeout,
            executable=executable,
        )
    except core_client.CoreClientError as exc:
        raise CoreDelegationError(
            f"Core send to {target} failed or outcome unknown; inspect before retrying: {exc}"
        ) from exc


async def run_core_one_child_workflow(
    argv: list[str],
    *,
    cwd: str | Path,
    name: str | None = None,
    parent: str | None = None,
    wait_timeout: float = 30.0,
    executable: str = "hc",
) -> CoreOneChildReport:
    """Delegate work to one owned Core child terminal and wait for its completion."""
    if wait_timeout <= 0:
        raise CoreDelegationError("wait_timeout must be positive")
    resolved_parent = (
        parent.strip()
        if isinstance(parent, str)
        else os.environ.get("HC_TERMINAL_ID", "").strip()
    )
    if not resolved_parent:
        raise CoreDelegationError(
            "One-child Core workflow requires a direct parent (HC_TERMINAL_ID or explicit parent)"
        )
    spawned = await delegate_core_child(
        argv,
        cwd=cwd,
        name=name,
        labels={"aihead.role": "worker", "aihead.parent": resolved_parent},
        parent=resolved_parent,
        allow_standalone=False,
        wait=False,
        executable=executable,
    )
    try:
        wait_res = await core_client.request(
            "terminal.wait",
            {"terminal": spawned.address, "exit": True, "timeout_ms": int(wait_timeout * 1000)},
            timeout=wait_timeout + 5.0,
            executable=executable,
        )
    except core_client.CoreClientError as exc:
        raise CoreDelegationError(
            f"Core wait for child {spawned.address} failed; inspect before retrying: {exc}"
        ) from exc
    if not isinstance(wait_res, dict):
        raise CoreDelegationError("Core terminal.wait returned non-object result")
    reason = wait_res.get("reason")
    if reason != "exit":
        raise CoreDelegationError(
            f"Core child {spawned.address} did not exit cleanly (reason={reason!r})"
        )
    try:
        info = await core_client.request(
            "terminal.get",
            {"terminal": spawned.address},
            timeout=5.0,
            executable=executable,
        )
    except core_client.CoreClientError as exc:
        raise CoreDelegationError(
            f"Core terminal.get for {spawned.address} failed after exit: {exc}"
        ) from exc
    if not isinstance(info, dict):
        raise CoreDelegationError("Core terminal.get returned non-object result")
    exit_code = info.get("exit_code")
    if type(exit_code) is not int or exit_code != 0:
        raise CoreDelegationError(
            f"Core child {spawned.address} failed with exit_code={exit_code!r}"
        )
    return CoreOneChildReport(
        parent=resolved_parent,
        child=spawned.address,
        exit_code=exit_code,
        wait_reason=reason,
    )


async def delegate_core_task(
    objective: str,
    *,
    cwd: str | Path,
    harness: str = "pi",
    model: str | None = None,
    effort: str | None = None,
    config_path: Path | None = None,
    name: str | None = None,
    parent: str | None = None,
    timeout: float = 10.0,
    executable: str = "hc",
) -> CoreDelegationResult:
    """Spawn an owned Core worker (pi only) non-blocking with catalog-resolved binding."""
    from herdr_master.core.available_models import enabled_model_ids
    from herdr_master.core.catalog import CatalogError, build_agent_args
    from herdr_master.core.config import (
        HerdrMasterConfig,
        find_project_config_path,
        load_project_config,
    )
    from herdr_master.core.models import HarnessKind
    from herdr_master.pipeline.squad import resolve_squad

    if not isinstance(objective, str) or not objective.strip():
        raise CoreDelegationError("objective must be a non-empty string")
    if harness != "pi":
        raise CoreDelegationError(
            f"Core delegation only supports harness 'pi', got {harness!r}"
        )
    pi_exe = shutil.which("pi")
    if not pi_exe:
        raise CoreDelegationError("pi executable not found on PATH")
    resolved_parent = (
        parent.strip()
        if isinstance(parent, str)
        else os.environ.get("HC_TERMINAL_ID", "").strip()
    )
    if not resolved_parent:
        raise CoreDelegationError(
            "Core task delegation requires a direct parent (HC_TERMINAL_ID or explicit parent)"
        )

    effective_cfg_path = config_path or find_project_config_path(Path(cwd))
    try:
        cfg = (
            load_project_config(effective_cfg_path)
            if effective_cfg_path is not None
            else HerdrMasterConfig()
        )
        resolved_squad = resolve_squad(
            effective_cfg_path or Path(cwd),
            mode="core",
        )
    except Exception as exc:
        raise CoreDelegationError(f"Invalid Core worker configuration: {exc}") from exc

    enabled_workers = [w for w in resolved_squad.squad.worker_pool if w.enabled]
    pi_workers = [w for w in enabled_workers if w.kind == HarnessKind.PI and w.model]
    allowed_pi = enabled_model_ids(cfg.models.available, "core", HarnessKind.PI)
    if allowed_pi is not None:
        pi_workers = [w for w in pi_workers if w.model in allowed_pi]
    if not pi_workers:
        raise CoreDelegationError(
            "No permitted enabled Pi worker binding in resolved Core squad"
        )
    if model:
        matches = [
            w
            for w in pi_workers
            if w.model == model or f"{w.kind.value}:{w.model}" == model
        ]
        if not matches:
            raise CoreDelegationError(
                f"Model {model!r} is not a permitted enabled Pi worker in resolved Core squad"
            )
        worker = matches[0]
    else:
        worker = pi_workers[0]
    worker_model = worker.model
    worker_effort = effort or (worker.effort.value if worker.effort is not None else None)
    try:
        agent_args = build_agent_args(
            HarnessKind.PI,
            worker_model,
            worker_effort,
            skip_permissions=cfg.ai_head.dangerously_skip_permissions,
        )
    except CatalogError as exc:
        raise CoreDelegationError(f"Invalid Pi worker binding: {exc}") from exc
    prompt = (
        f"Report your completion to your direct parent terminal {resolved_parent} using:\n"
        f'hc send {resolved_parent} "REPORT: <summary>"\n\n'
        f"Task:\n{objective}"
    )
    return await delegate_core_child(
        [pi_exe, *agent_args, "--", prompt],
        cwd=cwd,
        name=name,
        labels={"aihead.role": "worker", "aihead.parent": resolved_parent},
        parent=resolved_parent,
        allow_standalone=False,
        wait=False,
        timeout=timeout,
        executable=executable,
    )
