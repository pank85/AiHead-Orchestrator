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


@pytest.mark.asyncio
async def test_launch_council_delivers_canonical_skill_and_bootstrap():
    from herdr_master.core.config import CouncilConfig
    from herdr_master.pipeline.launcher import launch_council, COUNCIL_COORDINATOR_NAME

    client = _client()
    ctx = _inside()
    council_cfg = CouncilConfig()

    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        res = await launch_council(
            client=client,
            intent="architect the cache layer",
            workspace_label="council-cache",
            council=council_cfg,
            cwd="/tmp/repo",
        )

    assert res["agent_name"] == COUNCIL_COORDINATOR_NAME
    assert res["workspace_id"] == "w9"
    assert res["pane_id"] == "w9:p2"
    assert res["coordinator_pane_id"] == "w9:p2"
    assert res["tab_id"] == "w9:t2"
    assert res["owns_workspace"] is False
    assert res["ownership"] is False
    assert res["origin_cwd"] == "/tmp/repo"
    assert res["origin"] == "/tmp/repo"
    assert res["intent"] == "architect the cache layer"
    assert res["council"] == council_cfg

    # Check start_agent
    start_call = client.start_agent.await_args.kwargs
    assert start_call["name"] == COUNCIL_COORDINATOR_NAME
    assert start_call["pane_id"] == "w9:p2"

    # Check prompt
    prompt = client.prompt_agent.await_args.args[1]
    assert prompt.startswith(load_skill_body("council").rstrip("\n"))
    payload = _payload(prompt)
    assert payload["entrypoint"] == "council"
    assert payload["intent"] == "architect the cache layer"
    assert payload["council"]["max_rounds"] == 5
    assert len(payload["council"]["members"]) == 2


@pytest.mark.asyncio
async def test_launch_council_rejects_malformed_roster_before_allocation():
    from herdr_master.core.config import CouncilConfig, CouncilMember
    from herdr_master.pipeline.launcher import launch_council

    client = _client()
    ctx = _inside()

    # Create unvalidated config with only 1 enabled member
    bad_cfg = CouncilConfig.model_construct(
        coordinator=HarnessBinding.from_slug("agy:gemini-3.8-flash", label="coordinator"),
        scout=HarnessBinding.from_slug("agy:gemini-3.8-flash", label="scout"),
        members=[
            CouncilMember.from_slug("agy:gemini-3.8-flash", label="m1", enabled=True),
            CouncilMember.from_slug("agy:gemini-3.8-flash", label="m2", enabled=False),
        ],
    )

    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx):
        with pytest.raises(ValueError, match="at least 2 enabled members"):
            await launch_council(
                client=client,
                intent="deliberate",
                workspace_label="council-bad",
                council=bad_cfg,
                cwd="/tmp/repo",
            )

    client.create_tab.assert_not_called()
    client.start_agent.assert_not_called()


@pytest.mark.asyncio
async def test_launch_ergo_ai_head_tri_state():
    client = _client()
    ctx = _inside()

    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head",
            AsyncMock(return_value={"tab_id": "w9:t_head", "pane_id": "w9:p_head", "enabled": True}),
        ),
    ):
        # Enabled
        res_enabled = await launch_ergo(
            client=client,
            intent="feature",
            workspace_label="ergo-head",
            squad=Squad(),
            cwd="/tmp/repo",
            ai_head=True,
        )
        assert res_enabled["ai_head"] is not None
        assert res_enabled["ai_head"].enabled is True
        payload_enabled = _payload(client.prompt_agent.await_args.args[1])
        assert "ai_head" in payload_enabled
        assert payload_enabled["ai_head"]["enabled"] is True

        # Disabled
        res_disabled = await launch_ergo(
            client=client,
            intent="feature",
            workspace_label="ergo-head-off",
            squad=Squad(),
            cwd="/tmp/repo",
            ai_head=False,
        )
        assert res_disabled["ai_head"] is None
        payload_disabled = _payload(client.prompt_agent.await_args.args[1])
        assert "ai_head" not in payload_disabled


@pytest.mark.asyncio
async def test_launch_ergo_ai_head_busy_propagates_status_and_actual_model():
    client = _client()
    ctx = _inside()

    head_info = {
        "tab_id": "w:t",
        "pane_id": "w:p",
        "busy": True,
        "reconciled": False,
        "model": "old-running-model",
        "enabled": True,
    }

    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head",
            AsyncMock(return_value=head_info),
        ),
    ):
        res = await launch_ergo(
            client=client,
            intent="feature",
            workspace_label="ergo-head-busy",
            squad=Squad(),
            cwd="/tmp/repo",
            ai_head=True,
        )
        discovery = res["ai_head"]
        assert discovery is not None
        assert discovery.status == "busy"
        assert discovery.reconciled is False
        assert discovery.model == "old-running-model"



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "head_info,status,reconciled,model_is_bound,busy_text",
    [
        ({"busy": False, "reconciled": True, "status": "ready"}, "ready", True, True, False),
        ({"busy": False, "reconciled": False, "model": "old"}, "pending", False, False, True),
        ({"busy": True, "reconciled": True, "model": "old"}, "busy", True, False, True),
    ],
)
async def test_launch_ergo_ai_head_state_reaches_bootstrap_instructions(
    head_info, status, reconciled, model_is_bound, busy_text
):
    client = _client()
    ctx = _inside()
    info = {"tab_id": "w:t", "pane_id": "w:p", "enabled": True, **head_info}

    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head",
            AsyncMock(return_value=info),
        ),
    ):
        res = await launch_ergo(
            client=client,
            intent="feature",
            workspace_label="ergo-head-state",
            squad=Squad(),
            cwd="/tmp/repo",
            ai_head=True,
        )
    discovery = res["ai_head"]
    assert discovery.status == status
    assert discovery.reconciled is reconciled
    if model_is_bound:
        from herdr_master.core.config import AiHeadConfig

        assert discovery.model == AiHeadConfig().binding.model
    else:
        assert discovery.model == "old"
    instructions = _payload(client.prompt_agent.await_args.args[1])["ai_head"]["instructions"]
    assert ("Do not consult AiHead" in instructions) is busy_text
