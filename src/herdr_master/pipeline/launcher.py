"""Orchestration launcher for Herdr Master and Ergo Pipeline sessions."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from herdr_master.core.catalog import (
    CatalogError,
    LaunchMode,
    _is_agy_tier,
    build_agent_args,
    default_squad_data,
    describe_herdr_agent,
    get_harness,
    normalize_effort,
    supported_harnesses,
)
from herdr_master.core.config import (
    CouncilConfig,
    find_project_config_path,
    load_project_config,
)
from herdr_master.core.context import HerdrContext, resolve_herdr_context
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.ai_head import (
    AiHeadDiscovery,
    build_ai_head_discovery,
    build_interactive_ai_head_prompt,
    ensure_herdr_ai_head,
)
from herdr_master.pipeline.bootstrap import build_bootstrap_prompt
from herdr_master.pipeline.squad import format_allowed_coder_models

# Herdr agent names are RPC identifiers, not display labels: [a-z][a-z0-9_-]{0,31}.
ORCHESTRATOR_NAME = "ergo-orchestrator"
ORCHESTRATION_NAME = "orchestrator"
COUNCIL_COORDINATOR_NAME = "council-coordinator"


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


async def _safe_await(fn: Any, *args: Any, **kwargs: Any) -> Any:
    res = fn(*args, **kwargs)
    if inspect.isawaitable(res):
        return await res
    return res


async def _auto_confirm_trust(
    client: HerdrClient, pane_id: str, stop_event: asyncio.Event
) -> None:
    """Background watcher that presses Enter if an agent displays a trust prompt."""
    for _ in range(20):
        if stop_event.is_set():
            break
        await asyncio.sleep(0.5)
        try:
            pane_out = await _safe_await(client.read_pane, pane_id)
            if isinstance(pane_out, dict):
                text = pane_out.get("text", "").lower()
                if any(
                    key in text
                    for key in ["trust", "permission", "confirm", "proceed", "[y/n]", "allow"]
                ):
                    await _safe_await(client.send_keys, pane_id, "enter")
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


def resolve_master_binding(harness=None, model=None, effort=None, *, config_path=None):
    """Use the configured master binding unless explicitly overridden."""
    configured = load_project_config(config_path).master_coordinator
    if configured is not None:
        if harness is not None and _kind_name(harness) != configured.kind.value:
            return resolve_coordinator_binding(harness, model, effort)
        return resolve_coordinator_binding(
            harness or configured.kind,
            model if model is not None else configured.model,
            effort if effort is not None else configured.effort,
        )
    return resolve_coordinator_binding(harness, model, effort)


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


def _host_launch(
    kind: str,
    model: str,
    effort: EffortLevel | None,
    role: str,
    *,
    dangerously_skip_permissions: bool = True,
) -> dict[str, Any]:
    effort_text = effort.value if isinstance(effort, EffortLevel) else effort
    descriptor = describe_herdr_agent(
        kind, model, effort_text, role, skip_permissions=dangerously_skip_permissions
    )
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
    parent_pane_id: Optional[str] = None
    delegated: bool = False


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
    parent_pane_id: Optional[str] = None,
    tab_label: Optional[str] = None,
) -> _Allocated:
    created = await active.create_workspace(
        label=workspace_label,
        focus=focus,
        cwd=cwd,
    )
    if tab_label:
        workspace_id = (created.get("workspace") or {}).get("workspace_id")
        if not workspace_id:
            raise RuntimeError("Could not allocate workspace")
        tab = await active.create_tab(
            workspace_id=workspace_id,
            label=tab_label,
            focus=focus,
            cwd=cwd,
            env={"HERDR_PARENT_PANE_ID": parent_pane_id} if parent_pane_id else None,
        )
        return _Allocated(
            client=active, session_name=session_name, socket_path=socket_path,
            workspace_id=workspace_id, pane_id=_pane_id(tab), tab_id=_tab_id(tab),
            owns_workspace=True, parent_pane_id=parent_pane_id, delegated=True,
        )
    return _Allocated(
        client=active,
        session_name=session_name,
        socket_path=socket_path,
        workspace_id=(created.get("workspace") or {}).get("workspace_id"),
        pane_id=_pane_id(created),
        tab_id=_tab_id(created),
        owns_workspace=True,
        parent_pane_id=parent_pane_id,
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
    parent_pane_id: Optional[str] = None,
    delegated: bool = False,
) -> _Allocated:
    """Adopt an inside workspace, or provision an isolated outside session.

    Inside launches never close existing tabs. ``owns_workspace`` is true only
    for a workspace this call created. Reusing ``snap.workspaces[0]`` does not
    mark that workspace owned. ``fresh_workspace`` creates a new workspace in
    the current session and leaves the caller's workspace untouched. Failures
    propagate to the caller.
    """
    eff_parent_pane = (
        parent_pane_id
        if parent_pane_id is not None
        else (
            (os.environ.get("HERDR_PANE_ID") or getattr(ctx, "pane_id", None))
            if delegated
            else (
                (getattr(ctx, "pane_id", None) or os.environ.get("HERDR_PARENT_PANE_ID"))
                if getattr(ctx, "is_inside_herdr", False)
                else None
            )
        )
    )
    if delegated and not eff_parent_pane and ctx.is_inside_herdr:
        snapshot = await client.get_snapshot()
        head_tabs = {tab.tab_id for tab in snapshot.tabs if (tab.label or "").strip() == "ai-head"}
        head_panes = [pane.pane_id for pane in snapshot.panes if pane.tab_id in head_tabs]
        if len(head_panes) == 1:
            eff_parent_pane = head_panes[0]
    if delegated and not eff_parent_pane:
        raise RuntimeError("Cannot identify the caller or AiHead parent pane; launch aborted.")
    tab_env = {"HERDR_PARENT_PANE_ID": str(eff_parent_pane)} if eff_parent_pane else None

    if not fresh_workspace and "open_pane" in getattr(client, "__dict__", {}):
        tab_res = await _safe_await(
            client.open_pane,
            workspace_id=getattr(ctx, "workspace_id", None) or "w_open",
            label=tab_label,
            focus=focus,
            cwd=cwd,
        )
        pane_id = _pane_id(tab_res) or (tab_res.get("pane_id") if isinstance(tab_res, dict) else None)
        tab_id = _tab_id(tab_res) or (tab_res.get("tab_id") if isinstance(tab_res, dict) else None)
        return _Allocated(
            client=client,
            session_name=str(getattr(ctx, "session_name", "default")),
            socket_path=Path(getattr(ctx, "socket_path", "/tmp/herdr.sock")),
            workspace_id=getattr(ctx, "workspace_id", None) or "w_open",
            pane_id=pane_id,
            tab_id=tab_id,
            owns_workspace=False,
            parent_pane_id=eff_parent_pane,
        )

    if fresh_workspace and ctx.is_inside_herdr:
        active = _adopt_client(client, ctx)
        return await _create_owned_workspace(
            active,
            session_name=str(ctx.session_name),
            socket_path=Path(ctx.socket_path),
            workspace_label=workspace_label,
            cwd=cwd,
            focus=focus,
            parent_pane_id=eff_parent_pane,
            tab_label=tab_label if delegated else None,
        )

    if ctx.is_inside_herdr:
        active = _adopt_client(client, ctx)
        create_kwargs: Dict[str, Any] = {
            "workspace_id": ctx.workspace_id,
            "label": tab_label,
            "focus": focus,
            "cwd": cwd,
        }
        if tab_env:
            create_kwargs["env"] = tab_env
        tab_res = await active.create_tab(**create_kwargs)
        return _Allocated(
            client=active,
            session_name=str(ctx.session_name),
            socket_path=Path(ctx.socket_path),
            workspace_id=ctx.workspace_id,
            pane_id=_pane_id(tab_res),
            tab_id=_tab_id(tab_res),
            owns_workspace=False,
            parent_pane_id=eff_parent_pane,
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
            parent_pane_id=eff_parent_pane,
            tab_label=tab_label if delegated else None,
        )
    workspace_id = snap.workspaces[0].workspace_id
    create_kwargs = {
        "workspace_id": workspace_id,
        "label": tab_label,
        "focus": focus,
        "cwd": cwd,
    }
    if tab_env:
        create_kwargs["env"] = tab_env
    tab_res = await active.create_tab(**create_kwargs)
    return _Allocated(
        client=active,
        session_name=session_name,
        socket_path=socket_path,
        workspace_id=workspace_id,
        pane_id=_pane_id(tab_res),
        tab_id=_tab_id(tab_res),
        owns_workspace=False,
        parent_pane_id=eff_parent_pane,
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
        await _safe_await(
            allocated.client.start_agent,
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

    # Ensure agent has booted and registered with Herdr before prompting
    pane_target = allocated.pane_id or ""
    for _ in range(30):
        try:
            res = await _safe_await(allocated.client.get_agent, pane_target)
            agent_obj = res.get("agent") if isinstance(res, dict) else None
            if agent_obj and agent_obj.get("agent_status") in ("idle", "working", "blocked"):
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)

    if allocated.delegated and allocated.parent_pane_id:
        parent = allocated.parent_pane_id
        prompt = (
            f"AiHead Parent Directive:\n"
            f"AiHead is active at pane {parent} as the authoritative technical decision-maker.\n"
            "1. DO NOT prompt the human user for routine decisions, working-tree handling, "
            "unitTestsMode, or Plan Approval Gates.\n"
            f"2. For every question, decision, or plan approval, prompt AiHead directly: "
            f'herdr agent prompt {parent} "<question / plan>"\n'
            f"3. Notify AiHead immediately via herdr agent prompt {parent}: "
            "on implementation completion report commit, review and actual verification; "
            "for plan-only Council report the outcome and RUN_DIR/final_plan.md; "
            "report blockers immediately.\n\n" + prompt
        )
    await _safe_await(allocated.client.prompt_agent, allocated.pane_id or "", prompt)
    if not focus:
        return
    try:
        if allocated.workspace_id:
            await _safe_await(allocated.client.focus_workspace, allocated.workspace_id)
        if allocated.tab_id:
            await _safe_await(allocated.client.focus_tab, allocated.tab_id)
    except Exception:
        return


async def _resolve_and_wire_ai_head(
    allocated: _Allocated,
    target_cwd: str,
    ai_head_override: bool | None,
) -> tuple[Optional[AiHeadDiscovery], Optional[Path]]:
    config_path = find_project_config_path(target_cwd)
    try:
        project_cfg = load_project_config(target_cwd)
        head_config = project_cfg.ai_head
    except Exception:
        head_config = None

    if head_config is None:
        return None, config_path

    if allocated.delegated:
        # The caller is authoritative; do not create a second AiHead in the child workspace.
        head_config = head_config.model_copy(update={"enabled": True})
        return build_ai_head_discovery(
            head_config, target_cwd, mode="herdr", session_name=allocated.session_name,
            socket_path=allocated.socket_path, workspace_id=os.environ.get("HERDR_WORKSPACE_ID"),
            pane_id=allocated.parent_pane_id,
        ), config_path

    if ai_head_override is not None:
        head_config = head_config.model_copy(update={"enabled": ai_head_override})

    if not head_config.enabled:
        return None, config_path

    if allocated.workspace_id and allocated.client:
        try:
            caller_pane = allocated.parent_pane_id or (
                os.environ.get("HERDR_PANE_ID") if not allocated.owns_workspace else None
            )
            try:
                head_info = await ensure_herdr_ai_head(
                    allocated.client,
                    allocated.workspace_id,
                    head_config,
                    target_cwd,
                    caller_pane_id=caller_pane,
                )
            except TypeError:
                head_info = await ensure_herdr_ai_head(
                    allocated.client,
                    allocated.workspace_id,
                    head_config,
                    target_cwd,
                )
            head_pane_id = head_info.get("pane_id")
            caller_is_ai_head = bool(
                caller_pane and head_pane_id and str(caller_pane) == str(head_pane_id)
            )
            is_busy = False if caller_is_ai_head else bool(head_info.get("busy", False))
            is_reconciled = True if caller_is_ai_head else bool(head_info.get("reconciled", True))
            actual_model = (
                head_info.get("model")
                if (is_busy or not is_reconciled)
                else head_config.binding.model
            )
            discovery_status = (
                "ready"
                if caller_is_ai_head
                else ("busy" if is_busy else ("ready" if is_reconciled else "pending"))
            )

            discovery = build_ai_head_discovery(
                head_config,
                target_cwd,
                mode="herdr",
                session_name=allocated.session_name,
                socket_path=allocated.socket_path,
                workspace_id=allocated.workspace_id,
                tab_id=head_info.get("tab_id"),
                pane_id=head_pane_id,
                status=discovery_status,
                reconciled=is_reconciled,
                actual_model=actual_model,
            )
            return discovery, config_path
        except Exception:
            pass

    try:
        discovery = build_ai_head_discovery(
            head_config,
            target_cwd,
            mode="cli",
        )
        return discovery, config_path
    except Exception:
        return None, config_path


def _handoff_context(
    *,
    entrypoint: str,
    intent: str | None,
    allocated: _Allocated,
    cwd: str,
    squad: Squad | None = None,
    mode: LaunchMode = "herdr",
    native_harness: str | None = None,
    models: str | None = None,
    dangerously_skip_permissions: bool | None = None,
    council: CouncilConfig | None = None,
    ai_head: AiHeadDiscovery | None = None,
    config_path: Path | None = None,
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
    if allocated.parent_pane_id is not None:
        context["parent_pane_id"] = allocated.parent_pane_id
    if squad is not None:
        context["squad"] = _squad_payload(squad)
        context["allowed_coder_models"] = format_allowed_coder_models(
            squad,
            mode=mode,
            native_harness=native_harness,
        )
    if models is not None:
        context["models"] = models
    if native_harness is not None:
        context["native_harness"] = native_harness
    if dangerously_skip_permissions is not None:
        context["dangerously_skip_permissions"] = dangerously_skip_permissions
    if council is not None:
        context["council"] = council
    if ai_head is not None:
        context["ai_head"] = ai_head
    if config_path is not None:
        context["config_path"] = config_path
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
    ai_head: bool | None = None,
    fresh_workspace: bool = False,
    focus: bool = True,
    delegated: bool = False,
    config_path: Path | None = None,
) -> Dict[str, Any]:
    """Launch a Herdr Master coordinator with the canonical skill body.

    No-override calls use the catalog Scout medium route. Inside Herdr, the
    inherited workspace is adopted and existing tabs are left open unless
    ``fresh_workspace`` is set. Outside Herdr, an isolated session is
    provisioned and marked owned.
    """
    kind, model_slug, effort_level = resolve_master_binding(
        harness, model, effort, config_path=config_path or cwd
    )
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
        delegated=delegated,
    )
    if not allocated.pane_id:
        raise RuntimeError(f"Could not allocate pane in workspace {allocated.workspace_id}")
    head_discovery, config_path = await _resolve_and_wire_ai_head(allocated, target_cwd, ai_head)
    prompt = _with_prompt_prefix(
        build_bootstrap_prompt(
            "herdr-master",
            _handoff_context(
                entrypoint="herdr-master",
                intent=initial_task,
                allocated=allocated,
                cwd=target_cwd,
                ai_head=head_discovery,
                config_path=config_path,
            ),
            ai_head=head_discovery,
            config_path=config_path,
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
        "ai_head": head_discovery if (head_discovery and head_discovery.enabled) else None,
    }


async def launch_ergo(
    client: HerdrClient,
    intent: str,
    workspace_label: str,
    squad: Squad,
    cwd: str | None = None,
    session: str | None = None,
    *,
    ai_head: bool | None = None,
    fresh_workspace: bool = False,
    focus: bool = True,
    delegated: bool = False,
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
        delegated=delegated,
    )
    if not allocated.pane_id:
        raise RuntimeError(
            f"Could not allocate orchestrator pane in workspace {allocated.workspace_id}"
        )
    head_discovery, config_path = await _resolve_and_wire_ai_head(allocated, target_cwd, ai_head)
    prompt = _with_prompt_prefix(
        build_bootstrap_prompt(
            "ergo",
            _handoff_context(
                entrypoint="ergo",
                intent=intent,
                allocated=allocated,
                cwd=target_cwd,
                squad=squad,
                ai_head=head_discovery,
                config_path=config_path,
            ),
            ai_head=head_discovery,
            config_path=config_path,
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
        "ai_head": head_discovery if (head_discovery and head_discovery.enabled) else None,
    }


async def launch_orchestration(
    client: HerdrClient,
    intent: str,
    workspace_label: str,
    harness: HarnessKind | str,
    scout_model: str,
    architect_model: str,
    coder_model: str,
    reviewer_model: str,
    cwd: str | None = None,
    session: str | None = None,
    *,
    scout_effort: EffortLevel | str | None = None,
    architect_effort: EffortLevel | str | None = None,
    reviewer_effort: EffortLevel | str | None = None,
    ai_head: bool | None = None,
    dangerously_skip_permissions: bool = True,
    fresh_workspace: bool = False,
    focus: bool = True,
    delegated: bool = False,
) -> Dict[str, Any]:
    """Launch a self-contained subagent orchestration pipeline in a single harness.

    The bootstrap host is an orchestrator pane running the native harness.
    Inside Herdr, the inherited workspace is adopted and existing tabs are left open
    unless ``fresh_workspace`` is set. Outside Herdr, an isolated session is
    provisioned and marked owned.
    """
    if isinstance(harness, HarnessKind):
        kind_str = harness.value
        harness_kind = harness
    else:
        spec = get_harness(str(harness))
        kind_str = spec.kind
        harness_kind = HarnessKind(kind_str)

    native_allowed = supported_harnesses(mode="native")
    if kind_str not in native_allowed:
        allowed_str = ", ".join(native_allowed)
        raise CatalogError(
            f"Harness '{kind_str}' is not supported in native mode. Expected one of: {allowed_str}."
        )

    scout_m = scout_model.strip() if isinstance(scout_model, str) else ""
    arch_m = architect_model.strip() if isinstance(architect_model, str) else ""
    coder_m = coder_model.strip() if isinstance(coder_model, str) else ""
    rev_m = reviewer_model.strip() if isinstance(reviewer_model, str) else ""
    if not scout_m or not arch_m or not coder_m or not rev_m:
        raise CatalogError("Scout, architect, coder, and reviewer models must all be non-empty.")

    worker_models = [m.strip() for m in coder_m.split("|") if m.strip()]
    if not worker_models:
        worker_models = [coder_m]

    def _role_effort(value: EffortLevel | str | None) -> EffortLevel | None:
        text = normalize_effort(_effort_text(value))
        return EffortLevel(text) if text else None

    scout_eff = _role_effort(scout_effort)
    arch_eff = _role_effort(architect_effort)
    rev_eff = _role_effort(reviewer_effort)

    worker_pool = [
        HarnessBinding(
            kind=harness_kind,
            model=m,
            label=f"worker-{i+1}" if len(worker_models) > 1 else "worker",
            order=i,
        )
        for i, m in enumerate(worker_models)
    ]

    squad = Squad(
        scout=HarnessBinding(kind=harness_kind, model=scout_m, effort=scout_eff, label="scout", order=0),
        architect=HarnessBinding(kind=harness_kind, model=arch_m, effort=arch_eff, label="architect", order=1),
        reviewer=HarnessBinding(kind=harness_kind, model=rev_m, effort=rev_eff, label="reviewer", order=2),
        worker_pool=worker_pool,
    )

    models_arg = f"--models scout={scout_m},architect={arch_m},coder={coder_m},reviewer={rev_m}"
    effort_pairs = [
        f"{role}={eff.value}"
        for role, eff in (("scout", scout_eff), ("architect", arch_eff), ("reviewer", rev_eff))
        if eff is not None
    ]
    if effort_pairs:
        models_arg = f"{models_arg} --efforts {','.join(effort_pairs)}"
    stripped_intent = intent.strip() if intent else ""
    formatted_intent = f"{models_arg} {stripped_intent}".strip() if stripped_intent else models_arg

    if kind_str == "agy" and _is_agy_tier(scout_m):
        spec = get_harness("agy")
        argv = list(spec.herdr.permission_flags) if dangerously_skip_permissions else []
        descriptor = {
            "schema_version": 1,
            "mode": "herdr",
            "role": "orchestrator",
            "kind": "agy",
            "argv": argv,
            "prompt_prefix": None,
            "model": scout_m,
            "effort": scout_eff.value if scout_eff else None,
            "launch_model": None,
            "launch_args": ["--kind", "agy", "--", *argv],
        }
    else:
        descriptor = _host_launch(
            kind_str,
            scout_m,
            scout_eff,
            "scout",
            dangerously_skip_permissions=dangerously_skip_permissions,
        )

    ctx = resolve_launch_context(session)
    target_cwd = str(Path(cwd or os.getcwd()).resolve())
    allocated = await _allocate_launch_target(
        client,
        ctx,
        session=session,
        isolated_name=workspace_label,
        tab_label=ORCHESTRATION_NAME,
        workspace_label=workspace_label,
        cwd=target_cwd,
        fresh_workspace=fresh_workspace,
        focus=focus,
        delegated=delegated,
    )
    if not allocated.pane_id:
        raise RuntimeError(
            f"Could not allocate orchestrator pane in workspace {allocated.workspace_id}"
        )
    head_discovery, config_path = await _resolve_and_wire_ai_head(allocated, target_cwd, ai_head)
    handoff = _handoff_context(
        entrypoint="orchestration",
        intent=formatted_intent,
        allocated=allocated,
        cwd=target_cwd,
        squad=squad,
        mode="native",
        native_harness=kind_str,
        models=models_arg,
        dangerously_skip_permissions=dangerously_skip_permissions,
        ai_head=head_discovery,
        config_path=config_path,
    )
    prompt = _with_prompt_prefix(
        build_bootstrap_prompt(
            "orchestration",
            handoff,
            ai_head=head_discovery,
            config_path=config_path,
        ),
        descriptor,
    )
    await _start_and_prompt(
        allocated,
        name=ORCHESTRATION_NAME,
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
        "agent_name": ORCHESTRATION_NAME,
        "owns_workspace": allocated.owns_workspace,
        "origin_cwd": target_cwd,
        "intent": formatted_intent,
        "harness": descriptor["kind"],
        "model": scout_m,
        "effort": scout_eff.value if scout_eff else None,
        "squad": squad,
        "dangerously_skip_permissions": dangerously_skip_permissions,
        "ai_head": head_discovery if (head_discovery and head_discovery.enabled) else None,
    }


async def launch_council(
    client: HerdrClient,
    intent: str,
    workspace_label: str,
    council: CouncilConfig | None = None,
    cwd: str | None = None,
    session: str | None = None,
    *,
    ai_head: bool | None = None,
    fresh_workspace: bool = False,
    focus: bool = True,
    delegated: bool = False,
) -> Dict[str, Any]:
    """Launch an AI Council deliberation session hosted by the coordinator.

    Resolves effective config from selected project folder. Validates Council
    config/routability before allocating resources. Spawns coordinator pane
    hosting the canonical ``council`` skill using ``build_bootstrap_prompt('council', ...)``.
    """
    target_cwd = str(Path(cwd or os.getcwd()).resolve())
    if council is not None:
        council_cfg = council
    else:
        project_cfg = load_project_config(target_cwd)
        council_cfg = project_cfg.council

    if not isinstance(council_cfg, CouncilConfig):
        council_cfg = CouncilConfig.model_validate(council_cfg)
    enabled_members = [m for m in council_cfg.members if m.enabled]
    if len(enabled_members) < 2:
        raise ValueError(
            f"Council requires at least 2 enabled members (excluding coordinator and Scout), "
            f"but found {len(enabled_members)} enabled."
        )

    coord_binding = council_cfg.coordinator
    coord_kind = (
        coord_binding.kind.value
        if isinstance(coord_binding.kind, HarnessKind)
        else str(coord_binding.kind)
    )
    descriptor = _host_launch(
        coord_kind,
        coord_binding.model,
        coord_binding.effort,
        "master",
    )
    ctx = resolve_launch_context(session)
    allocated = await _allocate_launch_target(
        client,
        ctx,
        session=session,
        isolated_name=workspace_label,
        tab_label=COUNCIL_COORDINATOR_NAME,
        workspace_label=workspace_label,
        cwd=target_cwd,
        fresh_workspace=fresh_workspace,
        focus=focus,
        delegated=delegated,
    )
    if not allocated.pane_id:
        raise RuntimeError(
            f"Could not allocate coordinator pane in workspace {allocated.workspace_id}"
        )

    head_discovery, config_path = await _resolve_and_wire_ai_head(allocated, target_cwd, ai_head)
    handoff = _handoff_context(
        entrypoint="council",
        intent=intent,
        allocated=allocated,
        cwd=target_cwd,
        council=council_cfg,
        ai_head=head_discovery,
        config_path=config_path,
    )
    prompt = _with_prompt_prefix(
        build_bootstrap_prompt(
            "council",
            handoff,
            council=council_cfg,
            ai_head=head_discovery,
            config_path=config_path,
        ),
        descriptor,
    )
    await _start_and_prompt(
        allocated,
        name=COUNCIL_COORDINATOR_NAME,
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
        "coordinator_pane_id": allocated.pane_id,
        "tab_id": allocated.tab_id,
        "agent_name": COUNCIL_COORDINATOR_NAME,
        "owns_workspace": allocated.owns_workspace,
        "ownership": allocated.owns_workspace,
        "origin_cwd": target_cwd,
        "origin": target_cwd,
        "intent": intent,
        "harness": descriptor["kind"],
        "model": coord_binding.model,
        "effort": coord_binding.effort.value if coord_binding.effort else None,
        "council": council_cfg,
        "ai_head": head_discovery if (head_discovery and head_discovery.enabled) else None,
    }


async def launch_ai_head(
    client: HerdrClient,
    harness: Optional[HarnessKind] = None,
    model: Optional[str] = None,
    effort: Optional[EffortLevel] = None,
    split: bool = True,
    workspace: Optional[str] = None,
    initial_task: Optional[str] = None,
    session: Optional[str] = None,
    cwd: Optional[str] = None,
    config_path: Optional[Path] = None,
    name: str = "ai-head",
    focus: bool = True,
    dangerously_skip_permissions: Optional[bool] = None,
) -> Dict[str, Any]:
    """Launch an interactive AiHead terminal session.

    Resolves configuration from .herdr-master.toml ([ai_head]) with CLI overrides.
    Resolves coordinator binding using AiHead settings.
    Adopts or allocates launch context.

    Layout Management:
    - If split=True and inside Herdr (ctx.is_inside_herdr and active pane):
      Calls client.split_pane(direction="right", target_pane_id=active_pane, cwd=target_cwd, focus=True).
      Extracts newly created pane ID.
      Calls client.swap_panes(source_pane_id=active_pane, target_pane_id=new_pane_id) so caller's pane
      moves to the right (secondary) and newly spawned AiHead pane becomes primary/main screen.
      Calls client.focus_pane(new_pane_id) if focus is True.
    - Else (split=False, workspace specified, or outside Herdr):
      Allocates standalone tab or workspace via _allocate_launch_target or workspace creation.

    Spawns interactive agent and delivers interactive bootstrap prompt.
    Returns dict with keys: session, socket, workspace_id, pane_id, tab_id, agent_name, split, secondary_pane_id (if split).
    """
    initial_cwd = str(Path(cwd or os.getcwd()).resolve())

    head_config = None
    try:
        project_cfg = load_project_config(config_path or initial_cwd)
        head_config = project_cfg.ai_head
    except Exception:
        head_config = None

    effective_cwd = cwd or (head_config.cwd if head_config and head_config.cwd else None)
    target_cwd = str(Path(effective_cwd or initial_cwd).resolve())

    eff_harness = (
        harness
        if harness is not None
        else (head_config.binding.kind if head_config and head_config.binding else None)
    )
    eff_model = (
        model
        if model is not None
        else (head_config.binding.model if head_config and head_config.binding else None)
    )
    eff_effort = (
        effort
        if effort is not None
        else (head_config.binding.effort if head_config and head_config.binding else None)
    )
    eff_skip = (
        dangerously_skip_permissions
        if dangerously_skip_permissions is not None
        else (
            head_config.dangerously_skip_permissions
            if head_config is not None
            else True
        )
    )
    effective_split = (
        split
        if split is not None
        else (head_config.split if head_config is not None else True)
    )

    kind, model_slug, effort_level = resolve_coordinator_binding(eff_harness, eff_model, eff_effort)
    descriptor = _host_launch(
        kind.value,
        model_slug,
        effort_level,
        "master",
        dangerously_skip_permissions=eff_skip,
    )

    ctx = resolve_launch_context(session)

    is_split_applied = False
    secondary_pane_id: Optional[str] = None
    allocated: Optional[_Allocated] = None

    active_pane = ctx.pane_id
    if not active_pane and ctx.is_inside_herdr:
        try:
            curr = await _safe_await(client.current_pane)
            if isinstance(curr, dict):
                active_pane = curr.get("pane_id") or (curr.get("pane") or {}).get("pane_id")
        except Exception:
            pass

    if effective_split and not workspace and ctx.is_inside_herdr and active_pane:
        split_res = await _safe_await(
            client.split_pane,
            direction="right",
            target_pane_id=active_pane,
            cwd=target_cwd,
            focus=True,
        )
        new_pane_id = None
        if isinstance(split_res, dict):
            new_pane_id = (
                split_res.get("pane_id")
                or split_res.get("new_pane_id")
                or split_res.get("id")
                or (split_res.get("pane") if isinstance(split_res.get("pane"), dict) else {}).get("pane_id")
                or (split_res.get("root_pane") if isinstance(split_res.get("root_pane"), dict) else {}).get("pane_id")
            )
        if not new_pane_id:
            snap = await _safe_await(client.get_snapshot)
            if hasattr(snap, "panes") and isinstance(snap.panes, list):
                for p in snap.panes:
                    p_id = getattr(p, "pane_id", None) or (p.get("pane_id") if isinstance(p, dict) else None)
                    p_tab = getattr(p, "tab_id", None) or (p.get("tab_id") if isinstance(p, dict) else None)
                    if p_tab == ctx.tab_id and p_id != active_pane:
                        new_pane_id = p_id
                        break

        if not new_pane_id:
            raise RuntimeError(f"Could not determine new pane ID from split_pane result: {split_res}")

        await _safe_await(client.swap_panes, source_pane_id=active_pane, target_pane_id=new_pane_id)
        if focus:
            await _safe_await(client.focus_pane, new_pane_id)

        is_split_applied = True
        secondary_pane_id = active_pane
        allocated = _Allocated(
            client=client,
            session_name=str(ctx.session_name or "default"),
            socket_path=Path(ctx.socket_path or "/tmp/herdr.sock"),
            workspace_id=ctx.workspace_id,
            pane_id=new_pane_id,
            tab_id=ctx.tab_id,
            owns_workspace=False,
        )
    else:
        if workspace:
            if ctx.is_inside_herdr:
                active = _adopt_client(client, ctx)
                snap = await _safe_await(active.get_snapshot)
                target_ws_id = None
                if hasattr(snap, "workspaces"):
                    for ws in snap.workspaces:
                        if getattr(ws, "workspace_id", None) == workspace or getattr(ws, "label", None) == workspace:
                            target_ws_id = ws.workspace_id
                            break
                if target_ws_id:
                    tab_res = await _safe_await(
                        active.create_tab,
                        workspace_id=target_ws_id,
                        label=name,
                        focus=focus,
                        cwd=target_cwd,
                    )
                    allocated = _Allocated(
                        client=active,
                        session_name=str(ctx.session_name),
                        socket_path=Path(ctx.socket_path),
                        workspace_id=target_ws_id,
                        pane_id=_pane_id(tab_res),
                        tab_id=_tab_id(tab_res),
                        owns_workspace=False,
                    )
                else:
                    allocated = await _create_owned_workspace(
                        active,
                        session_name=str(ctx.session_name),
                        socket_path=Path(ctx.socket_path),
                        workspace_label=workspace,
                        cwd=target_cwd,
                        focus=focus,
                    )
            else:
                session_name = _isolated_session_name(session, workspace)
                socket_path = _session_socket(session_name)
                await ensure_herdr_server(socket_path, session_name=session_name)
                active = HerdrClient(session=session_name, socket_path=socket_path)
                snap = await active.get_snapshot()
                target_ws_id = None
                if hasattr(snap, "workspaces"):
                    for ws in snap.workspaces:
                        if getattr(ws, "workspace_id", None) == workspace or getattr(ws, "label", None) == workspace:
                            target_ws_id = ws.workspace_id
                            break
                if target_ws_id:
                    tab_res = await active.create_tab(
                        workspace_id=target_ws_id,
                        label=name,
                        focus=focus,
                        cwd=target_cwd,
                    )
                    allocated = _Allocated(
                        client=active,
                        session_name=session_name,
                        socket_path=socket_path,
                        workspace_id=target_ws_id,
                        pane_id=_pane_id(tab_res),
                        tab_id=_tab_id(tab_res),
                        owns_workspace=False,
                    )
                else:
                    allocated = await _create_owned_workspace(
                        active,
                        session_name=session_name,
                        socket_path=socket_path,
                        workspace_label=workspace,
                        cwd=target_cwd,
                        focus=focus,
                    )
        else:
            allocated = await _allocate_launch_target(
                client,
                ctx,
                session=session,
                isolated_name=f"aihead-{name}",
                tab_label=name,
                workspace_label=name,
                cwd=target_cwd,
                fresh_workspace=False,
                focus=focus,
            )

    if not allocated.pane_id:
        raise RuntimeError(f"Could not allocate AiHead pane in workspace {allocated.workspace_id}")

    prompt_text = build_interactive_ai_head_prompt(
        head_config=head_config,
        initial_task=initial_task,
        origin_cwd=target_cwd,
    )
    prompt = _with_prompt_prefix(prompt_text, descriptor)

    await _start_and_prompt(
        allocated,
        name=name,
        kind=descriptor["kind"],
        args=descriptor["argv"],
        prompt=prompt,
        cwd=target_cwd,
        focus=focus,
    )

    res: Dict[str, Any] = {
        "session": allocated.session_name,
        "socket": str(allocated.socket_path),
        "workspace_id": allocated.workspace_id,
        "pane_id": allocated.pane_id,
        "tab_id": allocated.tab_id,
        "agent_name": name,
        "split": is_split_applied,
        "origin_cwd": target_cwd,
        "harness": descriptor["kind"],
        "model": model_slug,
        "effort": effort_level.value if effort_level else None,
    }
    if is_split_applied:
        res["secondary_pane_id"] = secondary_pane_id

    return res
