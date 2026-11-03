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


async def ensure_herdr_server(socket_path: Path) -> None:
    """Ensure the Herdr server daemon is running and reachable."""
    if socket_path.exists():
        try:
            test_client = HerdrClient(socket_path=socket_path, timeout=2.0)
            await test_client.ping()
            return
        except Exception:
            try:
                socket_path.unlink()
            except Exception:
                pass

    # Start headless herdr server
    await asyncio.create_subprocess_exec(
        "herdr",
        "server",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    for _ in range(25):
        await asyncio.sleep(0.2)
        if socket_path.exists():
            try:
                test_client = HerdrClient(socket_path=socket_path, timeout=1.0)
                await test_client.ping()
                return
            except Exception:
                pass
    raise RuntimeError(f"Failed to start Herdr server; socket not responding at {socket_path}")


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
) -> Dict[str, Any]:
    """Launch a Herdr Master session.

    Rules:
    1. Outside Herdr:
       - Ensures Herdr daemon is active.
       - Creates a new Herdr workspace and runs the master in its root pane.
    2. Inside Herdr:
       - If workspace has no other tabs (tab_count <= 1), works in this workspace.
       - Creates a focused tab for the master and runs it.
    3. Guarantees:
       - Registers cwd in agy trustedWorkspaces to avoid blocking.
       - Auto-handles interactive trust prompts so the agent never hangs.
       - Sends the `/herdr-master` prompt as soon as the agent is ready.
    """
    ctx = resolve_herdr_context(explicit_session=client.session)
    target_cwd = cwd or os.getcwd()

    if not ctx.is_inside_herdr:
        await ensure_herdr_server(ctx.socket_path)
        ws_res = await client.create_workspace(
            label=name,
            focus=True,
            cwd=target_cwd,
        )
        target_ws_id = ws_res.get("workspace", {}).get("workspace_id")
        target_pane_id = ws_res.get("root_pane", {}).get("pane_id")
    else:
        target_ws_id = ctx.workspace_id
        tab_res = await client.create_tab(
            workspace_id=ctx.workspace_id,
            label=name,
            focus=True,
            cwd=target_cwd,
        )
        target_pane_id = tab_res.get("root_pane", {}).get("pane_id") or tab_res.get("pane_id")

    if not target_pane_id:
        raise RuntimeError(f"Could not allocate pane in workspace {target_ws_id}")

    harness_str = harness.value if isinstance(harness, HarnessKind) else str(harness)
    if harness_str == "agy":
        ensure_agy_trusted(target_cwd)

    args = _build_agent_args(harness_str, model)

    stop_event = asyncio.Event()
    watcher_task = asyncio.create_task(
        _auto_confirm_trust(client, target_pane_id, stop_event)
    )

    try:
        await client.start_agent(
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
    await client.prompt_agent(target_pane_id, prompt_cmd)

    return {
        "workspace_id": target_ws_id,
        "pane_id": target_pane_id,
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
) -> Dict[str, Any]:
    """Launch an Ergo multi-role pipeline workspace.

    Rules:
    1. Outside Herdr:
       - Ensures Herdr daemon is active.
       - Creates a dedicated workspace for the Ergo pipeline.
    2. Inside Herdr:
       - If workspace has no other tabs (tab_count <= 1), works in this workspace.
       - If workspace already has multiple tabs, creates a dedicated workspace.
    3. Launches Scout agent, handles trust prompts, and dispatches feature objective.
    """
    ctx = resolve_herdr_context(explicit_session=client.session)
    target_cwd = cwd or os.getcwd()

    target_ws_id: Optional[str] = None
    scout_pane_id: Optional[str] = None

    if not ctx.is_inside_herdr:
        await ensure_herdr_server(ctx.socket_path)
        ws_res = await client.create_workspace(
            label=workspace_label,
            focus=True,
            cwd=target_cwd,
        )
        target_ws_id = ws_res.get("workspace", {}).get("workspace_id")
        scout_pane_id = ws_res.get("root_pane", {}).get("pane_id")
    else:
        # Check current workspace tab count
        snap = await client.get_snapshot()
        current_ws = next((w for w in snap.workspaces if w.workspace_id == ctx.workspace_id), None)
        has_other_tabs = current_ws.tab_count > 1 if current_ws else False

        if not has_other_tabs:
            # Workspace without other tabs: work right here!
            target_ws_id = ctx.workspace_id
            tab_res = await client.create_tab(
                workspace_id=ctx.workspace_id,
                label="scout",
                focus=True,
                cwd=target_cwd,
            )
            scout_pane_id = tab_res.get("root_pane", {}).get("pane_id") or tab_res.get("pane_id")
        else:
            # Workspace has other tabs: create a dedicated workspace
            ws_res = await client.create_workspace(
                label=workspace_label,
                focus=True,
                cwd=target_cwd,
            )
            target_ws_id = ws_res.get("workspace", {}).get("workspace_id")
            scout_pane_id = ws_res.get("root_pane", {}).get("pane_id")

    if not scout_pane_id:
        raise RuntimeError(f"Could not allocate Scout pane in workspace {target_ws_id}")

    scout_kind = squad.scout.kind.value
    scout_model = squad.scout.model
    if scout_kind == "agy":
        ensure_agy_trusted(target_cwd)

    args = _build_agent_args(scout_kind, scout_model)

    stop_event = asyncio.Event()
    watcher_task = asyncio.create_task(
        _auto_confirm_trust(client, scout_pane_id, stop_event)
    )

    try:
        await client.start_agent(
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
    await client.prompt_agent(scout_pane_id, prompt_cmd)

    return {
        "workspace_id": target_ws_id,
        "scout_pane_id": scout_pane_id,
        "intent": intent,
    }
