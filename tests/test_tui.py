"""Unit and Pilot tests for Herdr-Master Textual TUI Cockpit."""

import pytest

from herdr_master.ui.tui.app import HerdrMasterApp
from herdr_master.ui.tui.modals.config_drawer import ConfigDrawer
from herdr_master.ui.tui.widgets.deck import CenterDeck
from herdr_master.ui.tui.widgets.footer import ActionFooter
from herdr_master.ui.tui.widgets.header_bar import HeaderBar
from herdr_master.ui.tui.widgets.hierarchy import HierarchyTree
from herdr_master.ui.tui.widgets.pipeline_view import PipelineView


@pytest.mark.asyncio
async def test_tui_app_mount_and_components():
    """Verify all core cockpit widgets mount and render in demo mode."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test() as pilot:
        # Check core widgets exist
        assert pilot.app.query_one(HeaderBar) is not None
        assert pilot.app.query_one(HierarchyTree) is not None
        assert pilot.app.query_one(CenterDeck) is not None
        assert pilot.app.query_one(ActionFooter) is not None
        assert pilot.app.query_one(PipelineView) is not None

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
async def test_config_drawer_modal_open():
    """Verify pressing 'c' opens ConfigDrawer modal screen."""
    app = HerdrMasterApp(demo=True)
    async with app.run_test(size=(100, 45)) as pilot:
        await pilot.press("c")
        # Top screen should now be ConfigDrawer
        assert isinstance(pilot.app.screen, ConfigDrawer)
        # Dismiss with Cancel button
        await pilot.click("#btn-cancel")
        assert not isinstance(pilot.app.screen, ConfigDrawer)
