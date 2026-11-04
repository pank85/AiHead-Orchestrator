"""Unit and Pilot tests for Herdr-Master Textual TUI Cockpit."""

from pathlib import Path

import pytest
from textual.widgets import Tree

from herdr_master.ui.tui.app import HerdrMasterApp
from herdr_master.ui.tui.modals.launch_ergo import LaunchErgoModal
from herdr_master.ui.tui.modals.launch_master import LaunchMasterModal
from herdr_master.ui.tui.widgets.deck import CenterDeck
from herdr_master.ui.tui.widgets.footer import ActionFooter
from herdr_master.ui.tui.widgets.header_bar import HeaderBar
from herdr_master.ui.tui.widgets.hierarchy import HierarchyTree
from herdr_master.ui.tui.widgets.pipeline_view import PipelineView
from herdr_master.ui.tui.widgets.quick_launch import QuickLaunchBar


@pytest.mark.asyncio
async def test_tui_app_mount_and_components():
    """Verify first page has no left column, and full-width cockpit mounts cleanly."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test() as pilot:
        # Check core widgets exist
        assert pilot.app.query_one(HeaderBar) is not None
        assert pilot.app.query_one(CenterDeck) is not None
        assert pilot.app.query_one(ActionFooter) is not None
        assert pilot.app.query_one(PipelineView) is not None

        # Verify HierarchyTree is NOT on the first page!
        assert len(pilot.app.query(HierarchyTree)) == 0

        # Verify initial deck mode is 3 (Pipeline)
        assert pilot.app.app_state.active_deck_mode == 3


@pytest.mark.asyncio
async def test_deck_mode_switching_keys():
    """Verify keys 1, 2, 3, 4 switch deck views properly."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test() as pilot:
        deck = pilot.app.query_one(CenterDeck)

        # Switch to Mode 1 (Output)
        await pilot.press("1")
        assert pilot.app.app_state.active_deck_mode == 1
        assert deck._output_view.display is True
        assert deck._pipeline_view.display is False

        # Switch to Mode 2 (Diff)
        await pilot.press("2")
        assert pilot.app.app_state.active_deck_mode == 2
        assert deck._diff_view.display is True
        assert deck._output_view.display is False

        # Switch to Mode 4 (Mailbox)
        await pilot.press("4")
        assert pilot.app.app_state.active_deck_mode == 4
        assert deck._mailbox_view.display is True

        # Switch back to Mode 3 (Pipeline)
        await pilot.press("3")
        assert pilot.app.app_state.active_deck_mode == 3
        assert deck._pipeline_view.display is True


@pytest.mark.asyncio
async def test_settings_modal_open_and_tree():
    """Verify pressing 'c' or 's' opens 2-column LaunchErgoModal with squad tree."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test(size=(110, 45)) as pilot:
        await pilot.press("c")
        # Top screen should now be LaunchErgoModal (Settings)
        assert isinstance(pilot.app.screen, LaunchErgoModal)

        # Check that the left column contains the squad tree
        tree = pilot.app.screen.query_one("#squad-tree", Tree)
        assert tree is not None
        assert "Herdr-Master Root" in str(tree.root.label)

        # Dismiss with Cancel button
        await pilot.click("#btn-cancel")
        assert not isinstance(pilot.app.screen, LaunchErgoModal)


@pytest.mark.asyncio
async def test_launch_ergo_modal_worker_dynamic_effort_and_description():
    """Verify worker pool hides effort select, shows effort note, and exposes description."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test(size=(110, 45)) as pilot:
        await pilot.press("e")
        modal = pilot.app.screen
        assert isinstance(modal, LaunchErgoModal)

        # Initially Scout is selected: effort select visible, description hidden
        assert modal._effort_select.display is True
        assert modal._effort_note.display is False
        assert modal._description_input.display is False

        # Select Worker 0: effort select hidden, dynamic effort note visible, description visible
        modal._load_agent_into_editor(("worker", 0))
        assert modal._effort_select.display is False
        assert modal._effort_note.display is True
        assert modal._description_input.display is True
        assert "Senior dev" in modal._description_input.value

        # Select Scout again: returns to standard role editor
        modal._load_agent_into_editor("scout")
        assert modal._effort_select.display is True
        assert modal._effort_note.display is False
        assert modal._description_input.display is False

        await pilot.click("#btn-cancel")


@pytest.mark.asyncio
async def test_launch_master_modal_and_quick_launch():
    """Verify clicking #btn-new-master or pressing 'n' opens LaunchMasterModal."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test(size=(110, 45)) as pilot:
        quick_bar = pilot.app.query_one(QuickLaunchBar)
        assert quick_bar is not None

        # Press 'n' to open LaunchMasterModal
        await pilot.press("n")
        assert isinstance(pilot.app.screen, LaunchMasterModal)
        await pilot.click("#btn-cancel")
        assert not isinstance(pilot.app.screen, LaunchMasterModal)

        # Click button on QuickLaunchBar
        await pilot.click("#btn-new-master")
        assert isinstance(pilot.app.screen, LaunchMasterModal)
        await pilot.click("#btn-cancel")
        assert not isinstance(pilot.app.screen, LaunchMasterModal)


@pytest.mark.asyncio
async def test_launch_ergo_modal_and_quick_launch():
    """Verify clicking #btn-new-ergo, #btn-settings, or pressing 'e' opens LaunchErgoModal."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test(size=(110, 45)) as pilot:
        # Press 'e' to open LaunchErgoModal
        await pilot.press("e")
        assert isinstance(pilot.app.screen, LaunchErgoModal)
        await pilot.click("#btn-cancel")
        assert not isinstance(pilot.app.screen, LaunchErgoModal)

        # Click New Ergo button on QuickLaunchBar
        await pilot.click("#btn-new-ergo")
        assert isinstance(pilot.app.screen, LaunchErgoModal)
        await pilot.click("#btn-cancel")
        assert not isinstance(pilot.app.screen, LaunchErgoModal)

        # Click Configure Squad Settings button on QuickLaunchBar
        await pilot.click("#btn-settings")
        assert isinstance(pilot.app.screen, LaunchErgoModal)

        # Verify bottom section widgets exist
        assert pilot.app.screen.query_one("#btn-start-ergo") is not None
        assert pilot.app.screen.query_one("#btn-save-default") is not None

        await pilot.click("#btn-cancel")
        assert not isinstance(pilot.app.screen, LaunchErgoModal)


@pytest.mark.asyncio
async def test_launch_master_submits_to_app_state():
    """Verify launching a master correctly stores MasterSession in app_state.masters."""
    from herdr_master.core.models import HarnessKind
    from herdr_master.ui.tui.modals.launch_master import MasterLaunchParams

    app = HerdrMasterApp(demo=True)
    async with app.run_test(size=(110, 45)) as pilot:
        await pilot.press("n")
        modal = pilot.app.screen
        modal.dismiss(
            MasterLaunchParams(
                name="test-coord",
                harness=HarnessKind.AGY,
                model="gemini-3.8-flash-high",
            )
        )
        await pilot.pause()
        assert "master-test-coord" in pilot.app.app_state.masters
        master = pilot.app.app_state.masters["master-test-coord"]
        assert master.harness == HarnessKind.AGY


@pytest.mark.asyncio
async def test_tui_launch_ergo_outside_herdr_exits_with_attach():
    """Verify launching Ergo outside Herdr triggers exit with attach result."""
    from unittest.mock import AsyncMock, patch

    from herdr_master.core.context import HerdrContext
    from herdr_master.ui.tui.modals.launch_ergo import ErgoLaunchParams

    mock_client = AsyncMock()
    mock_client.session = "default"

    fake_ctx = HerdrContext(
        is_inside_herdr=False,
        session_name="default",
        socket_path=Path("/tmp/herdr.sock"),
    )
    fake_res = {
        "session": "ergo-worker",
        "workspace_id": "w_test",
        "scout_pane_id": "w_test:p1",
        "tab_id": "w_test:t1",
    }

    app = HerdrMasterApp(demo=False)
    app.client = mock_client

    with (
        patch("herdr_master.ui.tui.app.resolve_herdr_context", return_value=fake_ctx),
        patch("herdr_master.ui.tui.app.launch_ergo", new_callable=AsyncMock, return_value=fake_res),
    ):
        async with app.run_test(size=(110, 45)) as pilot:
            await pilot.press("e")
            modal = pilot.app.screen
            modal.dismiss(
                ErgoLaunchParams(
                    intent="Deploy service",
                    workspace_label="worker",
                    start_pipeline=True,
                )
            )
            await pilot.pause()
            assert app.return_value == {"action": "attach", "session": "ergo-worker"}


@pytest.mark.asyncio
async def test_tui_launch_ergo_inside_herdr_exits_with_done():
    """Verify launching Ergo inside Herdr triggers exit with done result."""
    from unittest.mock import AsyncMock, patch

    from herdr_master.core.context import HerdrContext
    from herdr_master.ui.tui.modals.launch_ergo import ErgoLaunchParams

    mock_client = AsyncMock()
    mock_client.session = "default"

    fake_ctx = HerdrContext(
        is_inside_herdr=True,
        session_name="default",
        workspace_id="w9",
        pane_id="w9:p1",
        socket_path=Path("/tmp/herdr.sock"),
    )
    fake_res = {
        "session": "default",
        "workspace_id": "w9",
        "scout_pane_id": "w9:p2",
        "tab_id": "w9:t2",
    }

    app = HerdrMasterApp(demo=False)
    app.client = mock_client

    with (
        patch("herdr_master.ui.tui.app.resolve_herdr_context", return_value=fake_ctx),
        patch("herdr_master.ui.tui.app.launch_ergo", new_callable=AsyncMock, return_value=fake_res),
    ):
        async with app.run_test(size=(110, 45)) as pilot:
            await pilot.press("e")
            modal = pilot.app.screen
            modal.dismiss(
                ErgoLaunchParams(
                    intent="Deploy service",
                    workspace_label="worker",
                    start_pipeline=True,
                )
            )
            await pilot.pause()
            assert app.return_value == {
                "action": "done",
                "session": "default",
                "workspace_id": "w9",
            }
