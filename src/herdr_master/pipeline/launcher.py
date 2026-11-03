"""Orchestration launcher for Herdr Master and Ergo Pipeline sessions."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from herdr_master.core.context import resolve_herdr_context
from herdr_master.core.models import HarnessKind, Squad
from herdr_master.herdr.client import HerdrClient


def ensure_agy_trusted(cwd: str) -> None:
    """Ensure cwd is in agy's trustedWorkspaces to bypass startup trust dialogs."""
    settings_path = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    if not settings_path.exists():
        return
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        trusted = data.get("trustedWorkspaces", [])
        resolved_cwd = str(Path(cwd).resolve())
        if resolved_cwd not in trusted:
            trusted.append(resolved_cwd)
            data["trustedWorkspaces"] = trusted
            with open(settings_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
    except Exception:
        pass


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

    # Start headless herdr server (named session or default)
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
                k in text
                for k in ["trust", "permission", "confirm", "proceed", "[y/n]", "allow"]
            ):
                await client.send_keys(pane_id, "enter")
                await asyncio.sleep(0.5)
        except Exception:
            pass


def _build_agent_args(harness: str, model: str) -> List[str]:
    """Construct canonical harness arguments."""
    if harness == "agy":
        return ["--model", model, "--effort", "high", "--dangerously-skip-permissions"]
    elif harness == "pi":
        if "/" in model:
            prov, mod = model.split("/", 1)
            return ["--provider", prov, "--model", mod, "--thinking", "high"]
        return ["--model", model, "--thinking", "high"]
    elif harness == "claude":
        return ["--model", model, "--dangerously-skip-permissions"]
    elif harness == "opencode":
        return ["--model", model]
    return ["--model", model]


async def launch_master(
    client: HerdrClient,
    name: str = "master-coordinator",
    harness: HarnessKind = HarnessKind.AGY,
    model: str = "gemini-3.8-flash-high",
    initial_task: Optional[str] = None,
    cwd: Optional[str] = None,
    session: Optional[str] = None,
) -> Dict[str, Any]:
    """Launch a Herdr Master session.

    Rules:
    1. Outside Herdr:
       - Uses an isolated dedicated session so other Herdr workspaces are hidden.
       - Ensures Herdr daemon is active for this session.
       - Creates a workspace and runs master in its root pane.
    2. Inside Herdr:
       - If workspace has no other tabs (tab_count <= 1), works in this workspace.
       - Creates a focused tab for the master and runs it.
    3. Guarantees:
       - Auto-focuses the workspace and tab so the user sees the active tabs.
       - Registers cwd in agy trustedWorkspaces to avoid blocking.
       - Auto-handles interactive trust prompts so the agent never hangs.
       - Sends `/herdr-master` prompt as soon as the agent is ready.
    """
    ctx = resolve_herdr_context(explicit_session=client.session or session)
    target_cwd = cwd or os.getcwd()

    target_ws_id: Optional[str] = None
    target_pane_id: Optional[str] = None
    target_tab_id: Optional[str] = None
    active_client = client

    if not ctx.is_inside_herdr:
        # Isolated dedicated session outside Herdr so unrelated workspaces are hidden
        session_name = (
            session
            if session and session != "default"
            else f"master-{name}"
        )
        target_socket = Path.home() / ".config" / "herdr" / "sessions" / session_name / "herdr.sock"
        await ensure_herdr_server(target_socket, session_name=session_name)
        active_client = HerdrClient(session=session_name, socket_path=target_socket)

        snap = await active_client.get_snapshot()
        if not snap.workspaces:
            ws_res = await active_client.create_workspace(
                label=name,
                focus=True,
                cwd=target_cwd,
            )
            target_ws_id = ws_res.get("workspace", {}).get("workspace_id")
            target_pane_id = ws_res.get("root_pane", {}).get("pane_id")
            target_tab_id = (
                ws_res.get("tab", {}).get("tab_id")
                or ws_res.get("root_pane", {}).get("tab_id")
            )
        else:
            target_ws_id = snap.workspaces[0].workspace_id
            tab_res = await active_client.create_tab(
                workspace_id=target_ws_id,
                label=name,
                focus=True,
                cwd=target_cwd,
            )
            target_pane_id = tab_res.get("root_pane", {}).get("pane_id") or tab_res.get("pane_id")
            target_tab_id = tab_res.get("tab", {}).get("tab_id")
    else:
        session_name = ctx.session_name
        target_ws_id = ctx.workspace_id
        snap = await active_client.get_snapshot()
        existing_tabs = [
            t.tab_id
            for t in snap.tabs
            if t.workspace_id == target_ws_id
        ]
        tab_res = await active_client.create_tab(
            workspace_id=ctx.workspace_id,
            label=name,
            focus=True,
            cwd=target_cwd,
        )
        target_pane_id = tab_res.get("root_pane", {}).get("pane_id") or tab_res.get("pane_id")
        target_tab_id = tab_res.get("tab", {}).get("tab_id")

    if not target_pane_id:
        raise RuntimeError(f"Could not allocate pane in workspace {target_ws_id}")

    harness_str = harness.value if isinstance(harness, HarnessKind) else str(harness)
    if harness_str == "agy":
        ensure_agy_trusted(target_cwd)

    args = _build_agent_args(harness_str, model)

    stop_event = asyncio.Event()
    watcher_task = asyncio.create_task(
        _auto_confirm_trust(active_client, target_pane_id, stop_event)
    )

    try:
        await active_client.start_agent(
            name=name,
            kind=harness_str,
            pane_id=target_pane_id,
            args=args,
            timeout_ms=30000,
        )
    finally:
        stop_event.set()
        watcher_task.cancel()

    # Prompt the master
    prompt_cmd = f"/herdr-master {initial_task}" if initial_task else "/herdr-master"
    await active_client.prompt_agent(target_pane_id, prompt_cmd)

    # Focus the workspace and tab so the user sees the active tabs!
    try:
        if target_ws_id:
            await active_client.focus_workspace(target_ws_id)
        if target_tab_id:
            await active_client.focus_tab(target_tab_id)
    except Exception:
        pass

    # Once work has started, close the rest of the tabs of this workspace
    if ctx.is_inside_herdr and existing_tabs:
        for old_tab_id in existing_tabs:
            if old_tab_id != target_tab_id:
                try:
                    await active_client.close_tab(old_tab_id)
                except Exception:
                    pass

    return {
        "session": session_name,
        "workspace_id": target_ws_id,
        "pane_id": target_pane_id,
        "tab_id": target_tab_id,
        "agent_name": name,
        "harness": harness_str,
        "model": model,
    }


async def launch_ergo(
    client: HerdrClient,
    intent: str,
    workspace_label: str,
    squad: Squad,
    cwd: Optional[str] = None,
    session: Optional[str] = None,
) -> Dict[str, Any]:
    """Launch an Ergo multi-role pipeline workspace.

    Rules:
    1. Outside Herdr:
       - Uses an isolated dedicated session so other Herdr workspaces are hidden.
       - Ensures Herdr daemon is active for this session.
       - Creates a dedicated workspace for the Ergo pipeline.
    2. Inside Herdr:
       - If workspace has no other tabs (tab_count <= 1), works in this workspace.
       - If workspace already has multiple tabs, creates a dedicated workspace.
    3. Guarantees:
       - Focuses workspace and Scout tab so the user sees all pipeline tabs.
       - Launches Scout agent, handles trust prompts, and dispatches feature objective.
    """
    ctx = resolve_herdr_context(explicit_session=client.session or session)
    target_cwd = cwd or os.getcwd()

    target_ws_id: Optional[str] = None
    scout_pane_id: Optional[str] = None
    scout_tab_id: Optional[str] = None
    active_client = client

    if not ctx.is_inside_herdr:
        # Isolated dedicated session outside Herdr so unrelated workspaces are hidden
        session_name = (
            session
            if session and session != "default"
            else workspace_label
        )
        target_socket = Path.home() / ".config" / "herdr" / "sessions" / session_name / "herdr.sock"
        await ensure_herdr_server(target_socket, session_name=session_name)
        active_client = HerdrClient(session=session_name, socket_path=target_socket)

        snap = await active_client.get_snapshot()
        if not snap.workspaces:
            ws_res = await active_client.create_workspace(
                label=workspace_label,
                focus=True,
                cwd=target_cwd,
            )
            scout_ws_id = ws_res.get("workspace", {}).get("workspace_id")
            target_ws_id = scout_ws_id
            scout_pane_id = ws_res.get("root_pane", {}).get("pane_id")
            scout_tab_id = (
                ws_res.get("tab", {}).get("tab_id")
                or ws_res.get("root_pane", {}).get("tab_id")
            )
        else:
            target_ws_id = snap.workspaces[0].workspace_id
            tab_res = await active_client.create_tab(
                workspace_id=target_ws_id,
                label="scout",
                focus=True,
                cwd=target_cwd,
            )
            scout_pane_id = tab_res.get("root_pane", {}).get("pane_id") or tab_res.get("pane_id")
            scout_tab_id = tab_res.get("tab", {}).get("tab_id")
    else:
        session_name = ctx.session_name
        target_ws_id = ctx.workspace_id
        snap = await active_client.get_snapshot()
        existing_tabs = [
            t.tab_id
            for t in snap.tabs
            if t.workspace_id == target_ws_id
        ]
        tab_res = await active_client.create_tab(
            workspace_id=target_ws_id,
            label="scout",
            focus=True,
            cwd=target_cwd,
        )
        scout_pane_id = tab_res.get("root_pane", {}).get("pane_id") or tab_res.get("pane_id")
        scout_tab_id = tab_res.get("tab", {}).get("tab_id")

    if not scout_pane_id:
        raise RuntimeError(f"Could not allocate Scout pane in workspace {target_ws_id}")

    scout_kind = squad.scout.kind.value
    scout_model = squad.scout.model
    if scout_kind == "agy":
        ensure_agy_trusted(target_cwd)

    args = _build_agent_args(scout_kind, scout_model)

    stop_event = asyncio.Event()
    watcher_task = asyncio.create_task(
        _auto_confirm_trust(active_client, scout_pane_id, stop_event)
    )

    try:
        await active_client.start_agent(
            name="scout",
            kind=scout_kind,
            pane_id=scout_pane_id,
            args=args,
            timeout_ms=30000,
        )
    finally:
        stop_event.set()
        watcher_task.cancel()

    # Prompt Scout
    prompt_cmd = (
        f"Perform reconnaissance on the following objective: {intent}. "
        "Document existing architecture, dependencies, and entrypoints into RUN_DIR/context.md."
    )
    await active_client.prompt_agent(scout_pane_id, prompt_cmd)

    # Focus the workspace and tab so the user sees the active tabs!
    try:
        if target_ws_id:
            await active_client.focus_workspace(target_ws_id)
        if scout_tab_id:
            await active_client.focus_tab(scout_tab_id)
    except Exception:
        pass

    # Once work has started, close the rest of the tabs of this workspace
    if ctx.is_inside_herdr and existing_tabs:
        for old_tab_id in existing_tabs:
            if old_tab_id != scout_tab_id:
                try:
                    await active_client.close_tab(old_tab_id)
                except Exception:
                    pass

    return {
        "session": session_name,
        "workspace_id": target_ws_id,
        "scout_pane_id": scout_pane_id,
        "tab_id": scout_tab_id,
        "intent": intent,
    }
