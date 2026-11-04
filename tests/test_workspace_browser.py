"""Workspace inventory, compact interactions, and exact-socket navigation."""

import asyncio
import socket
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

import pytest

from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.discovery import SessionInventory, discover_clients
from herdr_master.herdr.snapshot import SessionSnapshot
from herdr_master.ui.tui.app import HerdrMasterApp
from herdr_master.ui.tui.widgets.workspaces import (
    WorkspaceBrowser,
    WorkspaceTarget,
    workspace_agents,
)


def inventory(path=Path("/session/herdr.sock"), name="one"):
    return SessionInventory(
        session_name=name,
        socket_path=path,
        snapshot=SessionSnapshot(
            protocol=22,
            focused_workspace_id="w1",
            workspaces=[{"workspace_id": "w1", "label": "Project", "agent_status": "working"}],
            tabs=[{"tab_id": "t1", "workspace_id": "w1", "label": "Implement"}],
            panes=[
                {"pane_id": "p1", "tab_id": "t1", "agent": "pi", "agent_status": "working"},
                {"pane_id": "p2", "tab_id": "t1"},
            ],
            agents=[{"pane_id": "p1", "agent": "pi", "name": "Builder"}],
        ),
    )


def test_discovery_uses_only_existing_sockets_and_preserves_primary(tmp_path):
    paths = [tmp_path / "herdr.sock", tmp_path / "sessions" / "other" / "herdr.sock"]
    sockets = []
    try:
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            sock = socket.socket(socket.AF_UNIX)
            sock.bind(str(path))
            sockets.append(sock)
        fake = tmp_path / "sessions" / "not-a-socket" / "herdr.sock"
        fake.parent.mkdir()
        fake.write_text("not a socket")
        primary = HerdrClient(session="current", socket_path=paths[0])
        clients = discover_clients(primary, tmp_path)
        assert clients[0] is primary
        assert [(client.session, client.socket_path) for client in clients] == [
            ("current", paths[0]),
            ("other", paths[1]),
        ]
    finally:
        for sock in sockets:
            sock.close()


def test_inventory_merges_panes_agents_and_tab_ownership():
    agents = workspace_agents(inventory(), "w1")
    assert len(agents) == 2
    assert agents[0].name == "Builder"
    assert agents[0].agent_status == "working"
    assert agents[0].tab_id == "t1"
    assert agents[1].agent_status == "terminal"
    assert not workspace_agents(inventory(), "absent")


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (110, 32)])
async def test_workspace_expansion_click_navigation_and_refresh_preserve_cursor(size):
    app = HerdrMasterApp(demo=True)
    app.app_state.live_sessions = [inventory()]
    app._navigate_to_agent = AsyncMock()
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        browser = app.query_one(WorkspaceBrowser)
        tree = browser._tree
        workspace = tree.root.children[0]
        assert not workspace.is_expanded
        assert "running" in workspace.label.plain
        await pilot.click("#workspace-tree", offset=(12, 0))
        await pilot.pause()
        assert workspace.is_expanded
        agent = workspace.children[0]
        assert "Builder" in agent.label.plain
        await pilot.click("#workspace-tree", offset=(12, 1))
        await pilot.pause()
        app._navigate_to_agent.assert_awaited_once_with(
            WorkspaceTarget("one", Path("/session/herdr.sock"), "w1", "t1", "p1")
        )
        browser.update_state(app.app_state)
        assert tree.root.children[0] is workspace
        assert workspace.is_expanded
        assert tree.cursor_node is agent
        assert tree.region.bottom <= size[1] - 1
        assert tree.size.height >= 10
        await pilot.press("3", "0")
        assert app.app_state.active_deck_mode == 0
        assert browser.display


@pytest.mark.asyncio
async def test_refresh_surfaces_offline_sessions_and_keeps_last_snapshot():
    app = HerdrMasterApp(demo=True)
    app.demo = False
    app._update_all_widgets = Mock()
    first, second = inventory(), inventory(Path("/other/herdr.sock"), "two")
    app.app_state.live_sessions = [first, second]
    primary = HerdrClient(session=first.session_name, socket_path=first.socket_path)
    other = HerdrClient(session=second.session_name, socket_path=second.socket_path)
    primary.get_snapshot = AsyncMock(return_value=first.snapshot)
    other.get_snapshot = AsyncMock(side_effect=ConnectionError("Session disappeared"))
    app.client = primary
    with patch("herdr_master.ui.tui.app.discover_clients", return_value=[primary, other]):
        await app._refresh_live_snapshot()
    assert app.app_state.socket_connected
    assert app.app_state.live_sessions[1].error == "Session disappeared"
    assert app.app_state.live_sessions[1].snapshot == second.snapshot
    assert app.app_state.workspace_label == "Project"
    assert not app._inventory_pending


@pytest.mark.asyncio
async def test_refresh_does_not_overlap():
    app = HerdrMasterApp(demo=True)
    app.demo = False
    app.client = Mock()
    app._inventory_pending = True
    with patch("herdr_master.ui.tui.app.discover_clients") as discover:
        await app._refresh_live_snapshot()
        discover.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "inside,same_socket,attach", [(True, True, False), (True, False, True), (False, False, True)]
)
async def test_agent_navigation_uses_exact_socket_and_focuses_pane(inside, same_socket, attach):
    app = HerdrMasterApp(demo=True)
    app.demo = False
    session = inventory()
    app.app_state.live_sessions = [session]
    app.notify = Mock()
    app.exit = Mock()
    client = Mock()
    client.focus_workspace = AsyncMock()
    client.focus_tab = AsyncMock()
    client.focus_pane = AsyncMock()
    context = Mock(
        is_inside_herdr=inside,
        socket_path=session.socket_path if same_socket else Path("/elsewhere"),
    )
    with (
        patch("herdr_master.ui.tui.app.HerdrClient", return_value=client) as factory,
        patch("herdr_master.ui.tui.app.resolve_herdr_context", return_value=context),
    ):
        await app._navigate_to_agent(WorkspaceTarget("one", session.socket_path, "w1", "t1", "p1"))
    factory.assert_called_once_with(session="one", socket_path=session.socket_path, timeout=2.0)
    assert client.mock_calls == [
        call.focus_workspace("w1"),
        call.focus_tab("t1"),
        call.focus_pane("p1"),
    ]
    if attach:
        app.exit.assert_called_once_with(result={"action": "attach", "session": "one"})
    else:
        app.exit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["offline", "closed", "demo", "rpc-error"])
async def test_navigation_safely_handles_unavailable_targets(scenario):
    app = HerdrMasterApp(demo=True)
    app.demo = scenario == "demo"
    session = inventory()
    if scenario == "offline":
        session.error = "offline"
    app.app_state.live_sessions = [session]
    app.notify = Mock()
    app.exit = Mock()
    client = Mock(focus_workspace=AsyncMock(side_effect=RuntimeError("pane gone")))
    with patch("herdr_master.ui.tui.app.HerdrClient", return_value=client) as factory:
        await app._navigate_to_agent(
            WorkspaceTarget(
                "one", session.socket_path, "w1", "t1", "absent" if scenario == "closed" else "p1"
            )
        )
    app.exit.assert_not_called()
    app.notify.assert_called_once()
    assert not app._navigation_pending
    if scenario != "rpc-error":
        factory.assert_not_called()


@pytest.mark.asyncio
async def test_focus_pane_protocol_contract():
    client = HerdrClient(socket_path="/unused/herdr.sock")
    client._rpc = AsyncMock(return_value={"result": {"ok": True}})
    assert await client.focus_pane("p1") == {"ok": True}
    client._rpc.assert_awaited_once_with("pane.focus", {"pane_id": "p1"})


@pytest.mark.asyncio
async def test_refresh_bounds_unresponsive_socket():
    app = HerdrMasterApp(demo=True)
    app.demo = False
    app._update_all_widgets = Mock()
    app.client = HerdrClient(session="one", socket_path="/session/herdr.sock")

    async def slow_snapshot():
        await asyncio.sleep(10)

    app.client.get_snapshot = slow_snapshot
    with patch("herdr_master.ui.tui.app.discover_clients", return_value=[app.client]):
        await asyncio.wait_for(app._refresh_live_snapshot(), timeout=3)
    assert app.app_state.live_sessions[0].error == "TimeoutError"
    assert not app.app_state.socket_connected
