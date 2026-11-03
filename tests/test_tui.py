"""Unit and Pilot tests for Herdr-Master Textual TUI Cockpit."""

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
