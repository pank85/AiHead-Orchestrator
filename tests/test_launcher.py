"""Launcher adopts inherited sessions and hands off the canonical skill."""

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from herdr_master.core.catalog import CatalogError
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.core.skill_resources import load_skill_body
from herdr_master.pipeline.bootstrap import BOOTSTRAP_SENTINEL
from herdr_master.pipeline.launcher import (
    ORCHESTRATOR_NAME,
    LaunchContextError,
    _build_agent_args,
    ensure_agy_trusted,
    launch_ergo,
    launch_master,
    resolve_launch_context,
)

SCOUT_ARGS = [
    "--kind",
    "agy",
    "--",
    "--model",
    "gemini-3.8-flash-medium",
    "--effort",
    "medium",
    "--dangerously-skip-permissions",
]
ARCHITECT_ARGS = [
    "--kind",
    "pi",
    "--",
    "--provider",
    "openai-codex",
    "--model",
    "gpt-6.1-sol",
    "--thinking",
    "max",
]
REVIEWER_ARGS = [
    "--kind",
    "pi",
    "--",
    "--provider",
    "xai",
    "--model",
    "grok-4.7",
    "--thinking",
    "high",
]
OLD_RECON = "Perform reconnaissance on the following objective"


class FakeCtx:
    def __init__(self, **kwargs):
        self.is_inside_herdr = kwargs.get("is_inside_herdr", False)
        self.session_name = kwargs.get("session_name", "default")
        self.socket_path = kwargs.get("socket_path", Path("/tmp/herdr.sock"))
        self.workspace_id = kwargs.get("workspace_id")
        self.pane_id = kwargs.get("pane_id")
        self.tab_id = kwargs.get("tab_id")


def _payload(prompt: str) -> dict:
    marker = f"{BOOTSTRAP_SENTINEL}\n```json\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n```", start)
    return json.loads(prompt[start:end])


def _inside(session="alpha"):
    return FakeCtx(
        is_inside_herdr=True,
        session_name=session,
        socket_path=Path(f"/tmp/sessions/{session}/herdr.sock"),
        workspace_id="w9",
        pane_id="w9:p1",
    )


def _client():
    client = AsyncMock()
    client.create_tab.return_value = {
        "root_pane": {"pane_id": "w9:p2"},
        "tab": {"tab_id": "w9:t2"},
    }
    async def start_agent(**kwargs):
        # Mirror Herdr's RPC validation; a permissive AsyncMock hid invalid names.
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", kwargs["name"]) is None:
            raise ValueError(
                "agent name must start with a lowercase letter and contain only "
                "lowercase letters, digits, '-' or '_' (1-32 characters)"
            )
        return {"type": "agent_started"}

    client.start_agent.side_effect = start_agent
    client.prompt_agent.return_value = {"type": "ok"}
    return client


def _rpc_args(cli_args: list[str]) -> list[str]:
    return cli_args[cli_args.index("--") + 1 :]


def test_build_agent_args_product_routes():
    assert _build_agent_args("agy", "gemini-3.8-flash-medium", EffortLevel.MEDIUM) == SCOUT_ARGS
    assert _build_agent_args("pi", "openai-codex/gpt-6.1-sol", EffortLevel.MAX) == ARCHITECT_ARGS
    assert _build_agent_args("pi", "xai/grok-4.7", EffortLevel.HIGH) == REVIEWER_ARGS
    bare = _build_agent_args("pi", "xai/grok-4.7")
    assert "--thinking" not in bare
    assert "litellm" not in SCOUT_ARGS + ARCHITECT_ARGS + REVIEWER_ARGS
    with pytest.raises(CatalogError):
        _build_agent_args("agy", "gemini-3.8-flash-medium", EffortLevel.HIGH)


def test_ensure_agy_trusted_appends_cwd(tmp_path, monkeypatch):
    settings = tmp_path / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"trustedWorkspaces": ["/tmp/test"]}))
    monkeypatch.setattr("herdr_master.pipeline.launcher.Path.home", lambda: tmp_path)
    target = tmp_path / "repo"
    target.mkdir()
    ensure_agy_trusted(str(target))
    data = json.loads(settings.read_text())
    assert str(target.resolve()) in data["trustedWorkspaces"]


def test_resolve_launch_context_preserves_and_rejects(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_SOCKET_PATH", "/tmp/sessions/alpha/herdr.sock")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w9")
    inherited = resolve_launch_context(None)
    assert inherited.session_name == "alpha"
    assert inherited.socket_path == Path("/tmp/sessions/alpha/herdr.sock")
    same = resolve_launch_context("alpha")
    assert same.socket_path == inherited.socket_path
    with pytest.raises(LaunchContextError, match="beta"):
        resolve_launch_context("beta")
    monkeypatch.delenv("HERDR_ENV")
    monkeypatch.delenv("HERDR_SOCKET_PATH")
    monkeypatch.delenv("HERDR_SOCKET", raising=False)
    outside = resolve_launch_context("beta")
    assert outside.session_name == "beta"
    assert outside.is_inside_herdr is False


@pytest.mark.asyncio
async def test_launch_master_no_override_uses_catalog_scout():
    client = _client()
    ctx = _inside()
    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_herdr_server", new_callable=AsyncMock) as srv,
    ):
        res = await launch_master(client=client, initial_task="Audit codebase", cwd="/tmp/repo")
    assert res["pane_id"] == "w9:p2"
    assert res["owns_workspace"] is False
    assert res["session"] == "alpha"
    assert res["harness"] == "agy"
    assert res["model"] == "gemini-3.8-flash-medium"
    assert client.start_agent.await_args.kwargs["kind"] == "agy"
    assert client.start_agent.await_args.kwargs["args"] == _rpc_args(SCOUT_ARGS)
    prompt = client.prompt_agent.await_args.args[1]
    assert prompt.startswith(load_skill_body("herdr-master").rstrip("\n"))
    assert prompt != "/herdr-master Audit codebase"
    assert _payload(prompt)["intent"] == "Audit codebase"
    assert _payload(prompt)["entrypoint"] == "herdr-master"
    client.close_tab.assert_not_called()
    srv.assert_not_called()


@pytest.mark.asyncio
async def test_launch_master_claude_uses_prompt_prefix_not_effort_flag():
    client = _client()
    ctx = _inside()
    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        await launch_master(
            client=client,
            harness=HarnessKind.CLAUDE,
            model="claude-3-opus",
            effort=EffortLevel.HIGH,
            initial_task="Audit codebase",
            cwd="/tmp/repo",
        )
    args = client.start_agent.await_args.kwargs["args"]
    prompt = client.prompt_agent.await_args.args[1]
    assert client.start_agent.await_args.kwargs["kind"] == "claude"
    assert args[0] != "--kind"
    assert "--effort" not in args
    assert "--thinking" not in args
    assert "--dangerously-skip-permissions" in args
    assert prompt.startswith("think harder\n\n")


@pytest.mark.asyncio
async def test_launch_ergo_adopts_workspace_and_names_orchestrator():
    client = _client()
    ctx = _inside()
    intent = 'build "cache"\nand $(rm -rf /)'
    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_herdr_server", new_callable=AsyncMock) as srv,
    ):
        res = await launch_ergo(
            client=client,
            intent=intent,
            workspace_label="ergo-cache",
            squad=Squad(),
            cwd="/tmp/repo",
        )
    assert res["workspace_id"] == "w9"
    assert res["pane_id"] == res["orchestrator_pane_id"] == res["scout_pane_id"] == "w9:p2"
    assert res["owns_workspace"] is False
    assert res["session"] == "alpha"
    assert res["socket"] == "/tmp/sessions/alpha/herdr.sock"
    assert res["agent_name"] == ORCHESTRATOR_NAME == "ergo-orchestrator"
    assert client.start_agent.await_args.kwargs["name"] == ORCHESTRATOR_NAME
    assert client.start_agent.await_args.kwargs["kind"] == "agy"
    assert client.start_agent.await_args.kwargs["args"] == _rpc_args(SCOUT_ARGS)
    assert client.create_tab.await_args.kwargs["label"] == ORCHESTRATOR_NAME
    prompt = client.prompt_agent.await_args.args[1]
    body = load_skill_body("ergo").rstrip("\n")
    payload = _payload(prompt)
    assert prompt.startswith(body)
    assert OLD_RECON not in prompt[len(body) :]
    assert payload["intent"] == intent
    assert payload["owns_workspace"] is False
    assert payload["squad"]["scout"]["model"] == "gemini-3.8-flash-medium"
    assert payload["squad"]["scout"]["effort"] == "medium"
    assert payload["squad"]["architect"]["model"] == "openai-codex/gpt-6.1-sol"
    assert payload["squad"]["architect"]["effort"] == "max"
    assert payload["squad"]["reviewer"]["model"] == "xai/grok-4.7"
    assert payload["squad"]["reviewer"]["effort"] == "high"
    assert payload["squad"]["worker_pool"][0]["model"] == "gemini-3.8-flash"
    assert payload["squad"]["worker_pool"][0]["effort"] is None
    assert payload["squad"]["worker_pool"][1]["model"] == "xai/grok-4.7"
    assert "worktree" not in payload
    assert "run_dir" not in payload
    assert "allowed_coder_models" in payload
    client.create_workspace.assert_not_called()
    client.close_tab.assert_not_called()
    client.close_workspace.assert_not_called()
    srv.assert_not_called()


@pytest.mark.asyncio
async def test_launch_ergo_rejects_other_session_before_allocation():
    client = _client()
    ctx = _inside("alpha")
    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_herdr_server", new_callable=AsyncMock) as srv,
        patch("herdr_master.pipeline.launcher.HerdrClient") as ctor,
    ):
        with pytest.raises(LaunchContextError):
            await launch_ergo(
                client=client,
                intent="ship it",
                workspace_label="ergo-cache",
                squad=Squad(),
                session="beta",
            )
    client.create_tab.assert_not_called()
    client.create_workspace.assert_not_called()
    client.start_agent.assert_not_called()
    srv.assert_not_called()
    ctor.assert_not_called()


@pytest.mark.asyncio
async def test_launch_ergo_rejects_bad_binding_before_allocation():
    client = _client()
    ctx = _inside()
    squad = Squad()
    squad.scout = HarnessBinding(kind=HarnessKind.AGY, model="flash", effort=EffortLevel.MEDIUM)
    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        with pytest.raises(CatalogError):
            await launch_ergo(
                client=client,
                intent="ship it",
                workspace_label="ergo-cache",
                squad=squad,
            )
    client.create_tab.assert_not_called()
    client.start_agent.assert_not_called()


@pytest.mark.asyncio
async def test_launch_ergo_outside_uses_wrapper_and_does_not_close():
    client = _client()
    client.create_workspace.return_value = {
        "workspace": {"workspace_id": "w_new"},
        "root_pane": {"pane_id": "w_new:p1"},
        "tab": {"tab_id": "w_new:t1"},
    }
    snap = AsyncMock()
    snap.workspaces = []
    client.get_snapshot.return_value = snap
    ctx = FakeCtx(is_inside_herdr=False, session_name="default")
    squad = Squad()
    squad.scout = HarnessBinding(
        kind=HarnessKind.PI,
        model="openai-codex/gpt-6.1-sol",
        effort=EffortLevel.MAX,
    )
    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_herdr_server", new_callable=AsyncMock),
        patch("herdr_master.pipeline.launcher.HerdrClient", return_value=client),
    ):
        res = await launch_ergo(
            client=client,
            intent="Build distributed worker",
            workspace_label="ergo-worker",
            squad=squad,
            cwd="/tmp/repo",
        )
    assert res["session"] == "ergo-worker"
    assert res["owns_workspace"] is True
    assert res["pane_id"] == res["scout_pane_id"] == "w_new:p1"
    assert client.start_agent.await_args.kwargs["kind"] == "pi"
    assert client.start_agent.await_args.kwargs["args"] == _rpc_args(ARCHITECT_ARGS)
    assert client.start_agent.await_args.kwargs["name"] == ORCHESTRATOR_NAME
    client.close_tab.assert_not_called()
    client.close_workspace.assert_not_called()


@pytest.mark.asyncio
async def test_inside_fresh_workspace_does_not_touch_inherited_workspace():
    client = _client()
    client.create_workspace.return_value = {
        "workspace": {"workspace_id": "w_new"},
        "root_pane": {"pane_id": "w_new:p1"},
        "tab": {"tab_id": "w_new:t1"},
    }
    ctx = _inside()
    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        res = await launch_ergo(
            client=client,
            intent="Deploy service",
            workspace_label="ergo-fresh",
            squad=Squad(),
            cwd="/tmp/repo",
            fresh_workspace=True,
            focus=False,
        )
    assert res["workspace_id"] == "w_new"
    assert res["owns_workspace"] is True
    assert res["session"] == "alpha"
    client.create_workspace.assert_awaited_once()
    assert client.create_workspace.await_args.kwargs["focus"] is False
    assert client.create_workspace.await_args.kwargs["label"] == "ergo-fresh"
    client.create_tab.assert_not_called()
    client.focus_workspace.assert_not_called()
    client.focus_tab.assert_not_called()
    assert client.start_agent.await_args.kwargs["args"] == _rpc_args(SCOUT_ARGS)


@pytest.mark.asyncio
async def test_outside_reused_workspace_is_not_closed():
    client = _client()
    old_ws = AsyncMock()
    old_ws.workspace_id = "w_old"
    old_tab = AsyncMock()
    old_tab.tab_id = "w_old:t1"
    snap = AsyncMock()
    snap.workspaces = [old_ws]
    snap.tabs = [old_tab]
    client.get_snapshot.return_value = snap
    client.create_tab.return_value = {
        "root_pane": {"pane_id": "w_old:p2"},
        "tab": {"tab_id": "w_old:t2"},
    }
    ctx = FakeCtx(is_inside_herdr=False)
    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_herdr_server", new_callable=AsyncMock),
        patch("herdr_master.pipeline.launcher.HerdrClient", return_value=client),
    ):
        res = await launch_ergo(
            client=client,
            intent="ship it",
            workspace_label="ergo-worker",
            squad=Squad(),
            cwd="/tmp/repo",
        )
    assert res["workspace_id"] == "w_old"
    assert res["owns_workspace"] is False
    payload = _payload(client.prompt_agent.await_args.args[1])
    assert payload["workspace_id"] == "w_old"
    assert payload["owns_workspace"] is False
    client.create_workspace.assert_not_called()
    client.close_tab.assert_not_called()
    client.close_workspace.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_and_start_failures_do_not_delete_or_retry():
    client = _client()
    ctx = _inside()
    client.prompt_agent.side_effect = RuntimeError("prompt failed")
    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        with pytest.raises(RuntimeError, match="prompt failed"):
            await launch_ergo(
                client=client,
                intent="ship it",
                workspace_label="ergo-cache",
                squad=Squad(),
                cwd="/tmp/repo",
            )
    assert client.prompt_agent.await_count == 1
    client.close_tab.assert_not_called()
    client.close_workspace.assert_not_called()

    client = _client()
    client.start_agent.side_effect = RuntimeError("start failed")
    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        with pytest.raises(RuntimeError, match="start failed"):
            await launch_master(client=client, cwd="/tmp/repo")
    client.prompt_agent.assert_not_called()
    client.close_tab.assert_not_called()

    client = _client()
    client.create_tab.side_effect = RuntimeError("alloc")
    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        with pytest.raises(RuntimeError, match="alloc"):
            await launch_ergo(
                client=client,
                intent="ship it",
                workspace_label="ergo-cache",
                squad=Squad(),
            )
    client.start_agent.assert_not_called()
    client.prompt_agent.assert_not_called()
