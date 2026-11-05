"""Orchestration launcher for Herdr Master and Ergo Pipeline sessions."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from herdr_master.core.catalog import (
    CatalogError,
    build_agent_args,
    default_squad_data,
    describe_herdr_agent,
    get_harness,
    normalize_effort,
)
from herdr_master.core.context import HerdrContext, resolve_herdr_context
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.bootstrap import build_bootstrap_prompt
from herdr_master.pipeline.squad import format_allowed_coder_models

# Herdr agent names are RPC identifiers, not display labels: [a-z][a-z0-9_-]{0,31}.
ORCHESTRATOR_NAME = "ergo-orchestrator"


class LaunchContextError(RuntimeError):
    """Explicit session cannot adopt the inherited workspace."""


def ensure_agy_trusted(cwd: str) -> None:
    """Ensure cwd is in agy's trustedWorkspaces to bypass startup trust dialogs."""
    settings_path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if not settings_path.exists():
        return
    try:
        with open(settings_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        trusted = data.get("trustedWorkspaces", [])
        resolved_cwd = str(Path(cwd).resolve())
        if resolved_cwd not in trusted:
            trusted.append(resolved_cwd)
            data["trustedWorkspaces"] = trusted
            with open(settings_path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
    except Exception:
        return


async def ensure_herdr_server(socket_path: Path, session_name: str = "default") -> None:
    """Ensure the Herdr server daemon is running and reachable for this session."""
    if socket_path.exists():
        try:
            test_client = HerdrClient(session=session_name, socket_path=socket_path, timeout=2.0)
            await test_client.ping()
            return
        except Exception:
            try:
                socket_path.unlink()
            except Exception:
                pass

    cmd = (
        ["herdr", "server"]
        if session_name == "default"
        else ["herdr", "--session", session_name, "server"]
    )
    await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    for _ in range(30):
        await asyncio.sleep(0.2)
        if socket_path.exists():
            try:
                test_client = HerdrClient(
                    session=session_name, socket_path=socket_path, timeout=1.0
                )
                await test_client.ping()
                return
            except Exception:
                pass
    raise RuntimeError(
        f"Failed to start Herdr server for session '{session_name}'; "
        f"socket not responding at {socket_path}"
    )


async def _auto_confirm_trust(
    client: HerdrClient, pane_id: str, stop_event: asyncio.Event
) -> None:
    """Background watcher that presses Enter if an agent displays a trust prompt."""
    for _ in range(20):
        if stop_event.is_set():
            break
        await asyncio.sleep(0.5)
        try:
            pane_out = await client.read_pane(pane_id)
            text = pane_out.get("text", "").lower()
            if any(
                key in text
                for key in ["trust", "permission", "confirm", "proceed", "[y/n]", "allow"]
            ):
                await client.send_keys(pane_id, "enter")
                await asyncio.sleep(0.5)
        except Exception:
            pass


def resolve_launch_context(session: str | None = None) -> HerdrContext:
    """Adopt the inherited session, or fail before a different server is used.

    Omitted ``session`` preserves the inherited socket and session name. An
    explicit different session inside an inherited workspace is refused so a
    workspace id is not reused on another server. This wraps
    :func:`resolve_herdr_context`; it does not reimplement environment lookup.
    """
    inherited = resolve_herdr_context(explicit_session=None)
    selected = session.strip() if isinstance(session, str) else None
    if not selected:
        return inherited
    if inherited.is_inside_herdr and selected != inherited.session_name:
        workspace = inherited.workspace_id or "(none)"
        raise LaunchContextError(
            f"Refusing session '{selected}' inside inherited workspace {workspace} "
            f"on session '{inherited.session_name}' (socket {inherited.socket_path}). "
            "Selecting a different session would reuse this workspace id on another "
            "server. Omit --session to adopt the inherited session and socket."
        )
    if inherited.is_inside_herdr and selected == inherited.session_name:
        return inherited
    return resolve_herdr_context(explicit_session=selected)


def _effort_text(effort: EffortLevel | str | None) -> str | None:
    if effort is None:
        return None
    if isinstance(effort, EffortLevel):
        return effort.value
    return str(effort)


def _kind_name(harness: HarnessKind | str | None) -> str | None:
    if harness is None:
        return None
    if isinstance(harness, HarnessKind):
        return harness.value
    text = str(harness).strip()
    return text or None


def resolve_coordinator_binding(
    harness: HarnessKind | str | None = None,
    model: str | None = None,
    effort: EffortLevel | str | None = None,
) -> tuple[HarnessKind, str, EffortLevel | None]:
    """Resolve a master coordinator from the catalog Scout unless overridden.

    A different harness has no product binding and requires an explicit model.
    """
    product = default_squad_data()["scout"]
    product_kind = str(product["kind"])
    product_model = str(product["model"])
    product_effort = str(product["effort"])
    kind_name = _kind_name(harness)
    model_text = model.strip() if isinstance(model, str) else None
    if model is not None and not model_text:
        raise CatalogError("--model is empty. Refusing to invent a model.")
    if kind_name is None and model_text is None and effort is None:
        return HarnessKind(product_kind), product_model, EffortLevel(product_effort)

    spec = get_harness(kind_name or product_kind)
    if not spec.supports_herdr:
        raise CatalogError(
            f"Harness '{spec.kind}' cannot launch a Herdr coordinator. "
            "Choose a harness with Herdr support and pass --model."
        )
    if model_text is None:
        if spec.kind != product_kind:
            raise CatalogError(
                f"Harness {spec.kind} has no product coordinator binding. "
                "Pass an explicit --model. Refusing to invent a per-harness default."
            )
        model_text = product_model
    effort_text = normalize_effort(_effort_text(effort))
    if effort_text is None and spec.kind == product_kind and model_text == product_model:
        effort_text = product_effort
    effort_level = EffortLevel(effort_text) if effort_text else None
    return HarnessKind(spec.kind), model_text, effort_level


def _build_agent_args(
    harness: str,
    model: str,
    effort: EffortLevel | None = None,
) -> list[str]:
    """Catalog argv with ``--kind <kind> --`` prepended.

    The catalog returns only the post-``--`` tail. Callers that start an agent
    pass this full list as ``args`` and the same kind as ``kind``.
    """
    kind = get_harness(harness).kind
    effort_text = effort.value if isinstance(effort, EffortLevel) else effort
    tail = build_agent_args(kind, model, effort_text)
    return ["--kind", kind, "--", *tail]


def _host_launch(kind: str, model: str, effort: EffortLevel | None, role: str) -> dict[str, Any]:
    effort_text = effort.value if isinstance(effort, EffortLevel) else effort
    descriptor = describe_herdr_agent(kind, model, effort_text, role)
    descriptor["launch_args"] = ["--kind", descriptor["kind"], "--", *descriptor["argv"]]
    return descriptor


def _binding_payload(binding: HarnessBinding) -> dict[str, Any]:
    return {
        "kind": binding.kind.value,
        "model": binding.model,
        "effort": binding.effort.value if binding.effort else None,
        "label": binding.label,
        "description": binding.description,
        "enabled": binding.enabled,
        "order": binding.order,
    }


def _squad_payload(squad: Squad) -> dict[str, Any]:
    return {
        "scout": _binding_payload(squad.scout),
        "architect": _binding_payload(squad.architect),
        "reviewer": _binding_payload(squad.reviewer),
        "worker_pool": [_binding_payload(worker) for worker in squad.worker_pool],
    }


def _with_prompt_prefix(prompt: str, descriptor: dict[str, Any]) -> str:
    prefix = descriptor.get("prompt_prefix")
    if not prefix:
        return prompt
    return f"{prefix}\n\n{prompt}"


def _isolated_session_name(session: str | None, fallback: str) -> str:
    if session and session != "default":
        return session
    return fallback


def _session_socket(session_name: str) -> Path:
    root = Path.home() / ".config" / "herdr"
    if session_name == "default":
        return root / "herdr.sock"
    return root / "sessions" / session_name / "herdr.sock"


def _adopt_client(client: HerdrClient, ctx: HerdrContext) -> HerdrClient:
    sock = getattr(client, "socket_path", None)
    if isinstance(sock, Path) and sock != Path(ctx.socket_path):
        return HerdrClient(session=ctx.session_name, socket_path=ctx.socket_path)
    return client


@dataclass
class _Allocated:
    client: HerdrClient
    session_name: str
    socket_path: Path
    workspace_id: Optional[str]
    pane_id: Optional[str]
    tab_id: Optional[str]
    owns_workspace: bool


def _pane_id(payload: dict[str, Any]) -> Optional[str]:
    root = payload.get("root_pane") if isinstance(payload.get("root_pane"), dict) else {}
    return root.get("pane_id") or payload.get("pane_id")


def _tab_id(payload: dict[str, Any]) -> Optional[str]:
    tab = payload.get("tab") if isinstance(payload.get("tab"), dict) else {}
    root = payload.get("root_pane") if isinstance(payload.get("root_pane"), dict) else {}
    return tab.get("tab_id") or root.get("tab_id")


async def _create_owned_workspace(
    active: HerdrClient,
    *,
    session_name: str,
    socket_path: Path,
    workspace_label: str,
    cwd: str,
    focus: bool,
) -> _Allocated:
    created = await active.create_workspace(
        label=workspace_label,
        focus=focus,
        cwd=cwd,
    )
    return _Allocated(
        client=active,
        session_name=session_name,
        socket_path=socket_path,
        workspace_id=(created.get("workspace") or {}).get("workspace_id"),
        pane_id=_pane_id(created),
        tab_id=_tab_id(created),
        owns_workspace=True,
    )


async def _allocate_launch_target(
    client: HerdrClient,
    ctx: HerdrContext,
    *,
    session: str | None,
    isolated_name: str,
    tab_label: str,
    workspace_label: str,
    cwd: str,
    fresh_workspace: bool = False,
    focus: bool = True,
) -> _Allocated:
    """Adopt an inside workspace, or provision an isolated outside session.

    Inside launches never close existing tabs. ``owns_workspace`` is true only
    for a workspace this call created. Reusing ``snap.workspaces[0]`` does not
    mark that workspace owned. ``fresh_workspace`` creates a new workspace in
    the current session and leaves the caller's workspace untouched. Failures
    propagate to the caller.
    """
    if fresh_workspace and ctx.is_inside_herdr:
        active = _adopt_client(client, ctx)
        return await _create_owned_workspace(
            active,
            session_name=str(ctx.session_name),
            socket_path=Path(ctx.socket_path),
            workspace_label=workspace_label,
            cwd=cwd,
            focus=focus,
        )

    if ctx.is_inside_herdr:
        active = _adopt_client(client, ctx)
        tab_res = await active.create_tab(
            workspace_id=ctx.workspace_id,
            label=tab_label,
            focus=focus,
            cwd=cwd,
        )
        return _Allocated(
            client=active,
            session_name=str(ctx.session_name),
            socket_path=Path(ctx.socket_path),
            workspace_id=ctx.workspace_id,
            pane_id=_pane_id(tab_res),
            tab_id=_tab_id(tab_res),
            owns_workspace=False,
        )

    session_name = _isolated_session_name(session, isolated_name)
    socket_path = _session_socket(session_name)
    await ensure_herdr_server(socket_path, session_name=session_name)
    active = HerdrClient(session=session_name, socket_path=socket_path)
    snap = await active.get_snapshot()
    if fresh_workspace or not snap.workspaces:
        return await _create_owned_workspace(
            active,
            session_name=session_name,
            socket_path=socket_path,
            workspace_label=workspace_label,
            cwd=cwd,
            focus=focus,
        )
    workspace_id = snap.workspaces[0].workspace_id
    tab_res = await active.create_tab(
        workspace_id=workspace_id,
        label=tab_label,
        focus=focus,
        cwd=cwd,
    )
    return _Allocated(
        client=active,
        session_name=session_name,
        socket_path=socket_path,
        workspace_id=workspace_id,
        pane_id=_pane_id(tab_res),
        tab_id=_tab_id(tab_res),
        owns_workspace=False,
    )


async def _start_and_prompt(
    allocated: _Allocated,
    *,
    name: str,
    kind: str,
    args: List[str],
    prompt: str,
    cwd: str,
    focus: bool = True,
) -> None:
    """Start one agent and deliver one prompt. Do not retry or delete resources.

    ``args`` is the agent argv after ``--``. ``kind`` is a separate RPC field.
    """
    if kind == "agy":
        ensure_agy_trusted(cwd)
    stop_event = asyncio.Event()
    watcher = asyncio.create_task(
        _auto_confirm_trust(allocated.client, allocated.pane_id or "", stop_event)
    )
    try:
        await allocated.client.start_agent(
            name=name,
            kind=kind,
            pane_id=allocated.pane_id or "",
            args=args,
            timeout_ms=30000,
        )
    finally:
        stop_event.set()
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass
    await allocated.client.prompt_agent(allocated.pane_id or "", prompt)
    if not focus:
        return
    try:
        if allocated.workspace_id:
            await allocated.client.focus_workspace(allocated.workspace_id)
        if allocated.tab_id:
            await allocated.client.focus_tab(allocated.tab_id)
    except Exception:
        return


def _handoff_context(
    *,
    entrypoint: str,
    intent: str | None,
    allocated: _Allocated,
    cwd: str,
    squad: Squad | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "entrypoint": entrypoint,
        "intent": intent,
        "origin_cwd": cwd,
        "session": allocated.session_name,
        "socket": str(allocated.socket_path),
        "workspace_id": allocated.workspace_id,
        "pane_id": allocated.pane_id,
        "tab_id": allocated.tab_id,
        "owns_workspace": allocated.owns_workspace,
    }
    if squad is not None:
        context["squad"] = _squad_payload(squad)
        context["allowed_coder_models"] = format_allowed_coder_models(squad)
    return context


async def launch_master(
    client: HerdrClient,
    name: str = "master-coordinator",
    harness: HarnessKind | None = None,
    model: str | None = None,
    initial_task: str | None = None,
    cwd: str | None = None,
    session: str | None = None,
    *,
    effort: EffortLevel | None = None,
    fresh_workspace: bool = False,
    focus: bool = True,
) -> Dict[str, Any]:
    """Launch a Herdr Master coordinator with the canonical skill body.

    No-override calls use the catalog Scout medium route. Inside Herdr, the
    inherited workspace is adopted and existing tabs are left open unless
    ``fresh_workspace`` is set. Outside Herdr, an isolated session is
    provisioned and marked owned.
    """
    kind, model_slug, effort_level = resolve_coordinator_binding(harness, model, effort)
    descriptor = _host_launch(kind.value, model_slug, effort_level, "master")
    ctx = resolve_launch_context(session)
    target_cwd = str(Path(cwd or os.getcwd()).resolve())
    allocated = await _allocate_launch_target(
        client,
        ctx,
        session=session,
        isolated_name=f"master-{name}",
        tab_label=name,
        workspace_label=name,
        cwd=target_cwd,
        fresh_workspace=fresh_workspace,
        focus=focus,
    )
    if not allocated.pane_id:
        raise RuntimeError(f"Could not allocate pane in workspace {allocated.workspace_id}")
    prompt = _with_prompt_prefix(
        build_bootstrap_prompt(
            "herdr-master",
            _handoff_context(
                entrypoint="herdr-master",
                intent=initial_task,
                allocated=allocated,
                cwd=target_cwd,
            ),
        ),
        descriptor,
    )
    await _start_and_prompt(
        allocated,
        name=name,
        kind=descriptor["kind"],
        args=descriptor["argv"],
        prompt=prompt,
        cwd=target_cwd,
        focus=focus,
    )
    return {
        "session": allocated.session_name,
        "socket": str(allocated.socket_path),
        "workspace_id": allocated.workspace_id,
        "pane_id": allocated.pane_id,
        "tab_id": allocated.tab_id,
        "agent_name": name,
        "owns_workspace": allocated.owns_workspace,
        "origin_cwd": target_cwd,
        "harness": descriptor["kind"],
        "model": model_slug,
        "effort": effort_level.value if effort_level else None,
    }


async def launch_ergo(
    client: HerdrClient,
    intent: str,
    workspace_label: str,
    squad: Squad,
    cwd: str | None = None,
    session: str | None = None,
    *,
    fresh_workspace: bool = False,
    focus: bool = True,
) -> Dict[str, Any]:
    """Hand an Ergo job to the canonical skill via the Scout binding.

    The bootstrap host is named Ergo orchestrator, not Scout. Python does not
    run later phases and does not close adopted tabs or unrelated workspaces.
    ``scout_pane_id`` remains as a deprecated alias of ``pane_id``.
    """
    scout_effort = squad.scout.effort
    descriptor = _host_launch(
        squad.scout.kind.value,
        squad.scout.model,
        scout_effort,
        "scout",
    )
    ctx = resolve_launch_context(session)
    target_cwd = str(Path(cwd or os.getcwd()).resolve())
    allocated = await _allocate_launch_target(
        client,
        ctx,
        session=session,
        isolated_name=workspace_label,
        tab_label=ORCHESTRATOR_NAME,
        workspace_label=workspace_label,
        cwd=target_cwd,
        fresh_workspace=fresh_workspace,
        focus=focus,
    )
    if not allocated.pane_id:
        raise RuntimeError(
            f"Could not allocate orchestrator pane in workspace {allocated.workspace_id}"
        )
    prompt = _with_prompt_prefix(
        build_bootstrap_prompt(
            "ergo",
            _handoff_context(
                entrypoint="ergo",
                intent=intent,
                allocated=allocated,
                cwd=target_cwd,
                squad=squad,
            ),
        ),
        descriptor,
    )
    await _start_and_prompt(
        allocated,
        name=ORCHESTRATOR_NAME,
        kind=descriptor["kind"],
        args=descriptor["argv"],
        prompt=prompt,
        cwd=target_cwd,
        focus=focus,
    )
    return {
        "session": allocated.session_name,
        "socket": str(allocated.socket_path),
        "workspace_id": allocated.workspace_id,
        "pane_id": allocated.pane_id,
        "orchestrator_pane_id": allocated.pane_id,
        "scout_pane_id": allocated.pane_id,
        "tab_id": allocated.tab_id,
        "agent_name": ORCHESTRATOR_NAME,
        "owns_workspace": allocated.owns_workspace,
        "origin_cwd": target_cwd,
        "intent": intent,
        "harness": descriptor["kind"],
        "model": squad.scout.model,
        "effort": scout_effort.value if scout_effort else None,
    }
