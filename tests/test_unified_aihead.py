from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app, app
from herdr_master.core.config import HerdrMasterConfig
from herdr_master.core.context import HerdrContext
from herdr_master.pipeline.launcher import (
    _allocate_launch_target,
    _resolve_and_wire_ai_head,
    _start_and_prompt,
)

runner = CliRunner()


def test_tui_dispatches():
    with patch("herdr_master.cli.main.tui") as cockpit:
        result = runner.invoke(aihead_app, ["--tui"])
    assert result.exit_code == 0, result.output
    cockpit.assert_called_once_with(session=None, demo=False)


def test_herdm_ergo_warns_and_runs():
    with patch("herdr_master.cli.main.launch_ergo", new_callable=AsyncMock) as launch:
        launch.return_value = {"workspace_id": "w", "pane_id": "p"}
        result = runner.invoke(app, ["ergo", "test", "--no-attach"])
    assert result.exit_code == 0, result.output
    assert "[deprecated] use aihead --ergo ..." in result.stderr
    launch.assert_awaited_once()


@pytest.mark.asyncio
async def test_delegation_creates_background_workspace_and_forwards_parent(monkeypatch):
    monkeypatch.setenv("HERDR_PANE_ID", "caller:p1")
    client = AsyncMock()
    client.create_workspace.return_value = {"workspace": {"workspace_id": "new"}}
    client.create_tab.return_value = {
        "root_pane": {"pane_id": "new:p1"}, "tab": {"tab_id": "new:t1"}
    }
    client.get_agent.return_value = {"agent": {"agent_status": "idle"}}
    ctx = HerdrContext(is_inside_herdr=True, session_name="default", socket_path=Path("/tmp/a"),
                       workspace_id="caller", pane_id="old")
    allocated = await _allocate_launch_target(
        client, ctx, session=None, isolated_name="ergo", tab_label="ergo", workspace_label="ergo",
        cwd="/tmp", fresh_workspace=True, focus=False, delegated=True,
    )
    assert allocated.workspace_id == "new"
    assert allocated.pane_id == "new:p1"
    assert client.create_workspace.await_args.kwargs["focus"] is False
    assert client.create_tab.await_args.kwargs["env"] == {"HERDR_PARENT_PANE_ID": "caller:p1"}
    with patch("herdr_master.pipeline.launcher.ensure_herdr_ai_head") as create_head:
        discovery, _ = await _resolve_and_wire_ai_head(allocated, "/tmp", None)
    assert discovery.pane_id == "caller:p1"
    create_head.assert_not_called()
    await _start_and_prompt(allocated, name="ergo", kind="opencode", args=[], prompt="task",
                            cwd="/tmp", focus=False)
    prompt = client.prompt_agent.await_args.args[1]
    assert "AiHead Parent Directive:" in prompt
    assert "herdr agent prompt caller:p1" in prompt
    client.focus_workspace.assert_not_called()


def test_ergo_flag_launches_and_focuses_workspace(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    with (
        patch("herdr_master.cli.main.launch_ergo", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.os.execvp") as execvp,
    ):
        launch.return_value = {"workspace_id": "new", "pane_id": "new:p1", "session": "default"}
        result = runner.invoke(aihead_app, ["--ergo", "test"])
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["delegated"] is False
    assert launch.await_args.kwargs["fresh_workspace"] is True
    assert launch.await_args.kwargs["focus"] is True
    execvp.assert_not_called()


@pytest.mark.asyncio
async def test_missing_caller_uses_unique_aihead_pane(monkeypatch):
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    client = AsyncMock()
    client.get_snapshot.return_value = SimpleNamespace(
        tabs=[SimpleNamespace(tab_id="t", label="ai-head")],
        panes=[SimpleNamespace(tab_id="t", pane_id="head:p1")],
    )
    client.create_workspace.return_value = {"workspace": {"workspace_id": "new"}}
    client.create_tab.return_value = {"root_pane": {"pane_id": "new:p1"}}
    ctx = HerdrContext(is_inside_herdr=True, session_name="default", socket_path=Path("/tmp/a"))
    await _allocate_launch_target(
        client, ctx, session=None, isolated_name="ergo", tab_label="ergo", workspace_label="ergo",
        cwd="/tmp", fresh_workspace=True, focus=False, delegated=True,
    )
    assert client.create_tab.await_args.kwargs["env"] == {"HERDR_PARENT_PANE_ID": "head:p1"}


@pytest.mark.asyncio
async def test_missing_parent_stops_without_creating_workspace(monkeypatch):
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    client = AsyncMock()
    client.get_snapshot.return_value = SimpleNamespace(tabs=[], panes=[])
    ctx = HerdrContext(is_inside_herdr=True, session_name="default", socket_path=Path("/tmp/a"))
    with pytest.raises(RuntimeError, match="parent pane"):
        await _allocate_launch_target(
            client, ctx, session=None, isolated_name="ergo", tab_label="ergo",
            workspace_label="ergo", cwd="/tmp", fresh_workspace=True, focus=False, delegated=True,
        )
    client.create_workspace.assert_not_called()


@pytest.mark.parametrize("skill", ["council", "orchestration", "herdr-master"])
def test_other_skills_use_fresh_focused_workspace(skill, monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    function = "launch_master" if skill == "herdr-master" else f"launch_{skill}"
    args = [f"--{skill}"] if skill == "herdr-master" else [f"--{skill}", "test"]
    with (
        patch(f"herdr_master.cli.main.{function}", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.os.execvp") as execvp,
    ):
        launch.return_value = {"workspace_id": "new", "pane_id": "new:p1", "session": "default"}
        result = runner.invoke(aihead_app, args)
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["delegated"] is False
    assert launch.await_args.kwargs["fresh_workspace"] is True
    assert launch.await_args.kwargs["focus"] is True
    if skill == "herdr-master":
        assert launch.await_args.kwargs["initial_task"] is None
    execvp.assert_not_called()


def test_skill_outside_herdr_starts_in_terminal(monkeypatch):
    for var in ("HERDR_ENV", "HERDR_SOCKET_PATH", "HERDR_WORKSPACE_ID", "HERDR_PANE_ID"):
        monkeypatch.delenv(var, raising=False)
    with (
        patch("shutil.which", return_value="/usr/bin/herdr"),
        patch("herdr_master.cli.main.launch_master", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.os.execvp") as execvp,
    ):
        launch.return_value = {
            "session": "master-session",
            "workspace_id": "new",
            "pane_id": "new:p1",
        }
        result = runner.invoke(aihead_app, ["--herdr-master"])
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["fresh_workspace"] is True
    assert launch.await_args.kwargs["focus"] is True
    assert launch.await_args.kwargs["delegated"] is False
    execvp.assert_called_once_with("herdr", ["herdr", "--session", "master-session"])


@pytest.mark.parametrize(
    "args",
    [
        ["--ergo", "test", "--no-attach"],
        ["--herdr-master", "--no-attach"],
        ["--setup", "--no-attach"],
        ["--tui", "--no-attach"],
        ["--ergo", "   "],
    ],
)
def test_skill_flags_reject_no_attach_and_blank_objective(args):
    result = runner.invoke(aihead_app, args)
    assert result.exit_code != 0


def test_orchestration_uses_native_defaults_when_config_is_empty(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    with (
        patch("herdr_master.cli.main.load_project_config", return_value=HerdrMasterConfig()),
        patch("herdr_master.cli.main.launch_orchestration", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.os.execvp"),
    ):
        launch.return_value = {"workspace_id": "new", "pane_id": "new:p1"}
        result = runner.invoke(aihead_app, ["--orchestration", "test"])
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["scout_model"] == "flash_lite"
    assert launch.await_args.kwargs["architect_model"] == "pro"
    assert launch.await_args.kwargs["coder_model"] == "flash"
    assert launch.await_args.kwargs["reviewer_model"] == "pro"
