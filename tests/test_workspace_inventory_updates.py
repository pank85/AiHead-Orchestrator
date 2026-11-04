"""Inventory reconciliation must distinguish servers and avoid stale live badges."""

from pathlib import Path

import pytest

from herdr_master.herdr.discovery import SessionInventory
from herdr_master.herdr.snapshot import SessionSnapshot
from herdr_master.ui.tui.app import HerdrMasterApp
from herdr_master.ui.tui.widgets.workspaces import WorkspaceBrowser


def session(name):
    return SessionInventory(
        session_name=name,
        socket_path=Path(f"/{name}/herdr.sock"),
        snapshot=SessionSnapshot(
            protocol=22,
            workspaces=[{"workspace_id": "w1", "label": name}],
            agents=[
                {"pane_id": "p1", "workspace_id": "w1", "agent": "pi", "agent_status": "working"}
            ],
        ),
    )


@pytest.mark.asyncio
async def test_same_ids_across_sessions_and_removed_panes():
    app = HerdrMasterApp(demo=True)
    first, second = session("one"), session("two")
    app.app_state.live_sessions = [first, second]
    async with app.run_test() as pilot:
        await pilot.pause()
        browser = app.query_one(WorkspaceBrowser)
        nodes = browser._tree.root.children
        assert len(nodes) == 2
        assert nodes[0].children[0].data.socket_path != nodes[1].children[0].data.socket_path
        nodes[0].expand()
        first.snapshot.agents.clear()
        second.snapshot.workspaces.clear()
        second.snapshot.agents.clear()
        browser.update_state(app.app_state)
        assert nodes[0].is_expanded
        assert not nodes[0].children
        assert len(browser._pane_nodes) == 0
        assert len(browser._tree.root.children) == 2
        assert "empty" in browser._tree.root.children[1].label.plain


@pytest.mark.asyncio
async def test_disconnected_snapshot_is_visibly_stale_then_recovers():
    app = HerdrMasterApp(demo=True)
    first = session("one")
    app.app_state.live_sessions = [first]
    async with app.run_test() as pilot:
        await pilot.pause()
        browser = app.query_one(WorkspaceBrowser)
        node = browser._tree.root.children[0]
        node.expand()
        first.error = "Connection refused"
        browser.update_state(app.app_state)
        assert "offline" in node.label.plain
        assert "stale" in node.children[0].label.plain
        assert "Connection refused" in browser._summary.tooltip
        first.error = None
        browser.update_state(app.app_state)
        assert node.is_expanded
        assert "running" in node.label.plain
        assert "running" in node.children[0].label.plain
        assert browser._summary.tooltip is None
