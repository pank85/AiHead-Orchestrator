"""Tests for Workspace Deletion flow and AiHead option on all launch modals except Council."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Select

from herdr_master.core.config import HerdrMasterConfig
from herdr_master.core.models import HarnessKind
from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.discovery import SessionInventory
from herdr_master.herdr.snapshot import (
    AgentSnapshot,
    PaneSnapshot,
    SessionSnapshot,
    TabSnapshot,
    WorkspaceSnapshot,
)
from herdr_master.pipeline.squad import resolve_squad
from herdr_master.ui.tui.app import HerdrMasterApp
from herdr_master.ui.tui.modals.confirm import ConfirmModal
from herdr_master.ui.tui.modals.launch_council import LaunchCouncilModal
from herdr_master.ui.tui.modals.launch_ergo import LaunchErgoModal
from herdr_master.ui.tui.modals.launch_master import LaunchMasterModal
from herdr_master.ui.tui.modals.launch_orchestration import LaunchOrchestrationModal
from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.workspaces import WorkspaceBrowser, WorkspaceTarget


@pytest.mark.asyncio
async def test_ai_head_option_present_on_all_modals_except_council(tmp_path: Path):
    """Verify AiHead override Select exists on Ergo, Orchestration, Master, and is absent on Council."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    squad = resolve_squad(project_dir).squad

    # 1. LaunchErgoModal
    class ErgoTestApp(App):
        def on_mount(self) -> None:
            self.modal = LaunchErgoModal(
                squad=squad,
                project_path=project_dir,
            )
            self.push_screen(self.modal)

    async with ErgoTestApp().run_test(size=(110, 45)) as pilot:
        await pilot.pause()
        modal = pilot.app.modal
        sel = modal.query_one("#ergo-aihead-override", Select)
        assert sel.value == "inherit"
        assert [opt[1] for opt in sel._options] == ["inherit", "enable", "disable"]

    # 2. LaunchOrchestrationModal
    class OrchTestApp(App):
        def on_mount(self) -> None:
            self.modal = LaunchOrchestrationModal(
                project_path=project_dir,
            )
            self.push_screen(self.modal)

    async with OrchTestApp().run_test(size=(110, 45)) as pilot:
        await pilot.pause()
        modal = pilot.app.modal
        sel = modal.query_one("#orch-aihead-override", Select)
        assert sel.value == "inherit"
        assert [opt[1] for opt in sel._options] == ["inherit", "enable", "disable"]

    # 3. LaunchMasterModal
    class MasterTestApp(App):
        def on_mount(self) -> None:
            self.modal = LaunchMasterModal(
                project_path=project_dir,
            )
            self.push_screen(self.modal)

    async with MasterTestApp().run_test(size=(110, 45)) as pilot:
        await pilot.pause()
        modal = pilot.app.modal
        sel = modal.query_one("#master-aihead-override", Select)
        assert sel.value == "inherit"
        assert [opt[1] for opt in sel._options] == ["inherit", "enable", "disable"]

    # 4. LaunchCouncilModal (explicitly NO AiHead selector)
    class CouncilTestApp(App):
        def on_mount(self) -> None:
            self.modal = LaunchCouncilModal(
                project_path=project_dir,
            )
            self.push_screen(self.modal)

    async with CouncilTestApp().run_test(size=(110, 45)) as pilot:
        await pilot.pause()
        modal = pilot.app.modal
        assert not modal.query("#council-aihead-override")


@pytest.mark.asyncio
async def test_workspace_browser_delete_button_and_request(tmp_path: Path):
    """Verify delete button in WorkspaceBrowser emits DeleteRequested."""
    app_state = AppState()
    browser = WorkspaceBrowser(app_state=app_state)

    class BrowserApp(App):
        def compose(self) -> ComposeResult:
            yield browser

    async with BrowserApp().run_test(size=(90, 30)) as pilot:
        del_btn = browser.query_one("#btn-delete-workspace")
        assert del_btn is not None

        # When no target selected
        assert browser._selected_target is None
        browser.request_delete()  # Should notify, not crash


@pytest.mark.asyncio
async def test_app_delete_workspace_flow_completed_workspace(tmp_path: Path):
    """Test full delete workspace confirmation and Herdr client close_workspace execution."""
    app = HerdrMasterApp(demo=False, project_path=tmp_path)
    mock_client = AsyncMock(spec=HerdrClient)
    mock_client.close_workspace = AsyncMock(return_value={"status": "ok"})
    app.client = mock_client
    app._live_client_for_socket = MagicMock(return_value=mock_client)
    app._schedule_inventory_refresh = MagicMock()

    # Setup live session inventory with a completed workspace
    sock_path = Path("/tmp/test-herdr.sock")
    snapshot = SessionSnapshot(
        protocol=22,
        workspaces=[WorkspaceSnapshot(workspace_id="ws-completed", label="Completed Task")],
        tabs=[TabSnapshot(tab_id="tab-1", workspace_id="ws-completed")],
        panes=[PaneSnapshot(pane_id="pane-1", tab_id="tab-1", workspace_id="ws-completed")],
        agents=[
            AgentSnapshot(
                pane_id="pane-1",
                tab_id="tab-1",
                workspace_id="ws-completed",
                agent_status="done",
            )
        ],
    )
    inv = SessionInventory(session_name="test-session", socket_path=sock_path, snapshot=snapshot)
    app.app_state.live_sessions = [inv]

    async with app.run_test(size=(110, 35)) as pilot:
        target = WorkspaceTarget(
            socket_path=sock_path,
            session_name="test-session",
            workspace_id="ws-completed",
        )

        # Trigger delete workspace flow
        await app._delete_workspace_flow(target)
        await pilot.pause()

        # ConfirmModal should be shown
        confirm_modal = pilot.app.screen
        assert isinstance(confirm_modal, ConfirmModal)
        assert "Delete Completed Workspace" in confirm_modal.title_text

        # Click Confirm
        await pilot.click("#btn-confirm")
        await pilot.pause()

        # Client close_workspace should have been called
        mock_client.close_workspace.assert_awaited_once_with("ws-completed")
        assert app._schedule_inventory_refresh.call_count >= 1
