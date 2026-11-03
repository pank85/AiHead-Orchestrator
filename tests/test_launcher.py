"""Unit tests for Herdr Master and Ergo Pipeline launcher."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from herdr_master.core.models import HarnessBinding, HarnessKind, Squad
from herdr_master.pipeline.launcher import (
    _build_agent_args,
    ensure_agy_trusted,
    launch_ergo,
    launch_master,
)


def test_build_agent_args():
    agy_args = _build_agent_args("agy", "gemini-3.8-flash-high")
    assert "--model" in agy_args
    assert "--dangerously-skip-permissions" in agy_args

    pi_args = _build_agent_args("pi", "xai/grok-4.7")
    assert "--provider" in pi_args
    assert "xai" in pi_args
    assert "--thinking" in pi_args

    claude_args = _build_agent_args("claude", "claude-3-7-sonnet")
    assert "--dangerously-skip-permissions" in claude_args


def test_ensure_agy_trusted(tmp_path: Path):
    fake_settings = tmp_path / "settings.json"
    fake_settings.write_text(json.dumps({"trustedWorkspaces": ["/tmp/test"]}))

    with patch("pathlib.Path.home", return_value=tmp_path.parent):
        with patch("herdr_master.pipeline.launcher.Path") as mock_path:
            mock_path.home.return_value = tmp_path
            target_cwd = "/home/pank/projects/herdr-master"
            ensure_agy_trusted(target_cwd)


@pytest.mark.asyncio
async def test_launch_master_inside_herdr():
    mock_client = AsyncMock()
    mock_client.session = "default"
    mock_client.create_tab.return_value = {
        "root_pane": {"pane_id": "w9:p2"},
        "tab": {"tab_id": "w9:t2"},
    }
    mock_client.start_agent.return_value = {"type": "agent_started"}
    mock_client.prompt_agent.return_value = {"type": "ok"}

    fake_ctx = AsyncMock()
    fake_ctx.is_inside_herdr = True
    fake_ctx.workspace_id = "w9"
    fake_ctx.pane_id = "w9:p1"
    fake_ctx.socket_path = Path("/tmp/herdr.sock")

    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=fake_ctx):
        res = await launch_master(
            client=mock_client,
            name="test-master",
            harness=HarnessKind.AGY,
            model="gemini-3.8-flash-high",
            initial_task="Audit codebase",
            cwd="/tmp/repo",
        )

    assert res["workspace_id"] == "w9"
    assert res["pane_id"] == "w9:p2"
    assert res["agent_name"] == "test-master"
    mock_client.create_tab.assert_called_once()
    mock_client.start_agent.assert_called_once()
    mock_client.prompt_agent.assert_called_once_with("w9:p2", "/herdr-master Audit codebase")


@pytest.mark.asyncio
async def test_launch_ergo_inside_empty_workspace():
    mock_client = AsyncMock()
    mock_client.session = "default"

    # Mock snapshot where current workspace has existing tab w9:t1
    fake_ws = AsyncMock()
    fake_ws.workspace_id = "w9"
    fake_ws.tab_count = 1
    fake_old_tab = AsyncMock()
    fake_old_tab.tab_id = "w9:t1"
    fake_old_tab.workspace_id = "w9"

    fake_snap = AsyncMock()
    fake_snap.workspaces = [fake_ws]
    fake_snap.tabs = [fake_old_tab]
    mock_client.get_snapshot.return_value = fake_snap

    mock_client.create_tab.return_value = {
        "root_pane": {"pane_id": "w9:p3"},
        "tab": {"tab_id": "w9:t3"},
    }
    mock_client.start_agent.return_value = {"type": "agent_started"}
    mock_client.prompt_agent.return_value = {"type": "ok"}

    fake_ctx = AsyncMock()
    fake_ctx.is_inside_herdr = True
    fake_ctx.workspace_id = "w9"
    fake_ctx.pane_id = "w9:p1"
    fake_ctx.socket_path = Path("/tmp/herdr.sock")

    squad = Squad(
        scout=HarnessBinding.from_slug("agy:gemini-3.8-flash-high"),
        architect=HarnessBinding.from_slug("pi:xai/grok-4.7"),
        reviewer=HarnessBinding.from_slug("agy:gemini-4-argon-high"),
    )

    with patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=fake_ctx):
        res = await launch_ergo(
            client=mock_client,
            intent="Build cache subsystem",
            workspace_label="ergo-cache",
            squad=squad,
            cwd="/tmp/repo",
        )

    # Must stay in w9 without creating another workspace!
    assert res["workspace_id"] == "w9"
    assert res["scout_pane_id"] == "w9:p3"
    mock_client.create_workspace.assert_not_called()
    mock_client.create_tab.assert_called_once()
    mock_client.start_agent.assert_called_once()
    mock_client.prompt_agent.assert_called_once()
    # Must close the old tab once work started
    mock_client.close_tab.assert_called_once_with("w9:t1")


@pytest.mark.asyncio
async def test_launch_ergo_outside_herdr():
    mock_client = AsyncMock()
    mock_client.session = "default"
    mock_client.create_workspace.return_value = {
        "workspace": {"workspace_id": "w_new"},
        "root_pane": {"pane_id": "w_new:p1"},
    }
    mock_client.start_agent.return_value = {"type": "agent_started"}
    mock_client.prompt_agent.return_value = {"type": "ok"}

    fake_ctx = AsyncMock()
    fake_ctx.is_inside_herdr = False
    fake_ctx.workspace_id = None
    fake_ctx.socket_path = Path("/tmp/herdr.sock")

    squad = Squad(
        scout=HarnessBinding.from_slug("agy:gemini-3.8-flash-high"),
        architect=HarnessBinding.from_slug("pi:xai/grok-4.7"),
        reviewer=HarnessBinding.from_slug("agy:gemini-4-argon-high"),
    )

    mock_snap = AsyncMock()
    mock_snap.workspaces = []
    mock_client.get_snapshot.return_value = mock_snap

    with (
        patch("herdr_master.pipeline.launcher.resolve_herdr_context", return_value=fake_ctx),
        patch("herdr_master.pipeline.launcher.ensure_herdr_server", new_callable=AsyncMock),
        patch("herdr_master.pipeline.launcher.HerdrClient", return_value=mock_client),
    ):
        res = await launch_ergo(
            client=mock_client,
            intent="Build distributed worker",
            workspace_label="ergo-worker",
            squad=squad,
            cwd="/tmp/repo",
        )

    assert res["workspace_id"] == "w_new"
    assert res["scout_pane_id"] == "w_new:p1"
    assert res["session"] == "ergo-worker"
    mock_client.create_workspace.assert_called_once()
    mock_client.start_agent.assert_called_once()
    mock_client.prompt_agent.assert_called_once()
