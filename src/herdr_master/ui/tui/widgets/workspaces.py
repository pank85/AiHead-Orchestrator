"""Live, clickable workspace/agent control panel."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static, Tree

from herdr_master.herdr.discovery import SessionInventory
from herdr_master.herdr.snapshot import AgentSnapshot
from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.quick_launch import QuickLaunchBar


@dataclass(frozen=True)
class WorkspaceTarget:
    session_name: str
    socket_path: Path
    workspace_id: str
    tab_id: Optional[str] = None
    pane_id: Optional[str] = None


BADGES = {
    "working": ("● running", "green"),
    "blocked": ("! blocked", "yellow"),
    "idle": ("○ idle", "cyan"),
    "done": ("✓ done", "dim"),
    "unknown": ("? unknown", "dim"),
    "terminal": ("› terminal", "dim"),
}


def status_label(status: Optional[str]) -> Text:
    label, style = BADGES.get(status or "unknown", BADGES["unknown"])
    return Text(label, style=style)


def workspace_agents(inventory: SessionInventory, workspace_id: str) -> list[AgentSnapshot]:
    """Merge pane metadata with agents; include ordinary terminals and deduplicate."""
    snapshot = inventory.snapshot
    if snapshot is None:
        return []
    tab_workspaces = {tab.tab_id: tab.workspace_id for tab in snapshot.tabs}
    agents = {}
    for pane in snapshot.panes:
        workspace = pane.workspace_id or tab_workspaces.get(pane.tab_id)
        if workspace == workspace_id:
            agents[pane.pane_id] = AgentSnapshot(
                pane_id=pane.pane_id,
                workspace_id=workspace,
                tab_id=pane.tab_id,
                agent=pane.agent,
                agent_status=pane.agent_status if pane.agent else "terminal",
                cwd=pane.cwd,
                focused=pane.focused,
            )
    for agent in snapshot.agents:
        previous = agents.get(agent.pane_id)
        workspace = (
            agent.workspace_id
            or tab_workspaces.get(agent.tab_id)
            or (previous.workspace_id if previous else None)
        )
        if workspace == workspace_id:
            values = agent.model_dump(exclude_none=True)
            values["workspace_id"] = workspace
            agents[agent.pane_id] = (
                previous.model_copy(update=values) if previous else agent.model_copy(update=values)
            )
    return list(agents.values())


class WorkspaceBrowser(Vertical):
    """Workspaces expand in place; selecting an agent requests real pane focus."""

    DEFAULT_CSS = """
    WorkspaceBrowser { height: 1fr; padding: 0 1; }
    #workspace-summary { height: auto; min-height: 1; color: #aaaaaa; }
    #workspace-tree { height: 1fr; background: #111111; padding: 0; }
    """

    class AgentSelected(Message):
        def __init__(self, target: WorkspaceTarget) -> None:
            super().__init__()
            self.target = target

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._summary = Static("Discovering Herdr sessions…", id="workspace-summary")
        self._tree: Tree[WorkspaceTarget] = Tree("Workspaces", id="workspace-tree")
        self._tree.show_root = False
        self._workspace_nodes = {}
        self._pane_nodes = {}

    def compose(self) -> ComposeResult:
        yield QuickLaunchBar()
        yield self._summary
        yield self._tree

    def on_mount(self) -> None:
        self.update_state(self.app_state)
        self._tree.focus()

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        inventories = app_state.live_sessions
        seen_workspaces = set()
        seen_panes = set()
        live_count = 0
        workspace_count = 0
        agent_count = 0
        for inventory in inventories:
            socket_key = str(inventory.socket_path)
            snapshot = inventory.snapshot
            if not inventory.error:
                live_count += 1
            if snapshot is None or not snapshot.workspaces:
                key = (socket_key, "")
                seen_workspaces.add(key)
                label = Text(
                    f"{'× offline' if inventory.error else '○ empty'} · {inventory.session_name}"
                    f" · {inventory.error or 'No open workspaces'}",
                    style="dim",
                )
                if key not in self._workspace_nodes:
                    self._workspace_nodes[key] = self._tree.root.add_leaf(label)
                else:
                    self._workspace_nodes[key].set_label(label)
                continue
            for workspace in snapshot.workspaces:
                workspace_count += 1
                key = (socket_key, workspace.workspace_id)
                seen_workspaces.add(key)
                agents = workspace_agents(inventory, workspace.workspace_id)
                agent_count += sum(agent.agent is not None for agent in agents)
                statuses = {agent.agent_status for agent in agents} | {workspace.agent_status}
                status = next(
                    (
                        value
                        for value in ("working", "blocked", "idle", "done")
                        if value in statuses
                    ),
                    "unknown",
                )
                label = Text("× offline", style="red") if inventory.error else status_label(status)
                label.append(f"  {workspace.label or workspace.workspace_id}", style="bold white")
                pane_word = "pane" if len(agents) == 1 else "panes"
                label.append(
                    f" · {inventory.session_name} · {len(agents)} {pane_word}", style="dim"
                )
                if inventory.error:
                    label.append(" · stale", style="red")
                target = WorkspaceTarget(
                    inventory.session_name, inventory.socket_path, workspace.workspace_id
                )
                if key not in self._workspace_nodes:
                    self._workspace_nodes[key] = self._tree.root.add(label, data=target)
                else:
                    self._workspace_nodes[key].set_label(label)
                node = self._workspace_nodes[key]
                # Reconcile in place: polling must not reset expansion, cursor, or scroll.
                for agent in agents:
                    pane_key = (*key, agent.pane_id)
                    seen_panes.add(pane_key)
                    tab = next((t for t in snapshot.tabs if t.tab_id == agent.tab_id), None)
                    label = (
                        Text("× stale", style="red")
                        if inventory.error
                        else status_label(agent.agent_status)
                    )
                    label.append(f"  {agent.name or agent.agent or 'Terminal'}", style="bold")
                    label.append(f" · {tab.label if tab and tab.label else agent.tab_id or 'tab'}")
                    label.append(f" · {agent.pane_id}", style="dim")
                    if agent.cwd:
                        label.append(f" · {agent.cwd}", style="dim")
                    target = WorkspaceTarget(
                        inventory.session_name,
                        inventory.socket_path,
                        workspace.workspace_id,
                        agent.tab_id,
                        agent.pane_id,
                    )
                    if pane_key not in self._pane_nodes:
                        self._pane_nodes[pane_key] = node.add_leaf(label, data=target)
                    else:
                        self._pane_nodes[pane_key].set_label(label)
                        self._pane_nodes[pane_key].data = target
        for key in list(self._pane_nodes):
            if key not in seen_panes:
                self._pane_nodes.pop(key).remove()
        for key in list(self._workspace_nodes):
            if key not in seen_workspaces:
                self._workspace_nodes.pop(key).remove()
        prefix = "DEMO · " if app_state.inventory_demo else ""
        if not inventories:
            summary = "No session data yet. Waiting for Herdr…"
        else:
            summary = (
                f"{live_count}/{len(inventories)} sessions online · {workspace_count} workspaces"
                f" · {agent_count} agents · Click workspace to expand; click agent to open"
            )
        self._summary.update(Text(prefix + summary))
        errors = [f"{item.session_name}: {item.error}" for item in inventories if item.error]
        self._summary.tooltip = "\n".join(errors) or None

    def on_tree_node_selected(self, event: Tree.NodeSelected[WorkspaceTarget]) -> None:
        event.stop()
        target = event.node.data
        if target and target.pane_id:
            self.post_message(self.AgentSelected(target))
