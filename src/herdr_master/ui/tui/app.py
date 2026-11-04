"""Textual Terminal User Interface (TUI) Application for Herdr-Master."""

import asyncio
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal

from herdr_master.core.context import resolve_herdr_context
from herdr_master.core.models import MasterSession, PipelinePhase, PipelineRun
from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.discovery import SessionInventory, discover_clients
from herdr_master.herdr.snapshot import AgentSnapshot
from herdr_master.pipeline.launcher import (
    LaunchContextError,
    launch_ergo,
    launch_master,
    resolve_launch_context,
)
from herdr_master.pipeline.squad import resolve_squad
from herdr_master.ui.tui.messages import TargetSelected
from herdr_master.ui.tui.modals.config_drawer import ConfigDrawer
from herdr_master.ui.tui.modals.launch_ergo import ErgoLaunchParams, LaunchErgoModal
from herdr_master.ui.tui.modals.launch_master import LaunchMasterModal, MasterLaunchParams
from herdr_master.ui.tui.modals.project_folder import (
    initial_project_folder,
    validate_project_folder,
)
from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.deck import CenterDeck
from herdr_master.ui.tui.widgets.footer import ActionFooter
from herdr_master.ui.tui.widgets.header_bar import HeaderBar
from herdr_master.ui.tui.widgets.quick_launch import QuickLaunchBar
from herdr_master.ui.tui.widgets.workspaces import (
    WorkspaceBrowser,
    WorkspaceTarget,
    workspace_agents,
)


def _result_pane(result: dict) -> Optional[str]:
    """Generic pane key. The deprecated scout_pane_id alias is not a pane."""
    pane = result.get("pane_id") or result.get("orchestrator_pane_id")
    if not pane:
        return None
    return str(pane)


class HerdrMasterApp(App):
    """Herdr-Master interactive terminal management cockpit."""

    TITLE = "HERDR-MASTER Cockpit"

    BINDINGS = [
        Binding("n", "new_master", "New Master"),
        Binding("e", "new_ergo", "New Ergo"),
        Binding("s", "open_settings", "Settings"),
        Binding("c", "open_settings", "Settings"),
        Binding("o", "open_in_herdr", "Open in Herdr", priority=False),
        Binding("q", "quit", "Quit"),
    ]

    CSS = """
    Screen {
        background: #101010;
        color: #ffffff;
    }
    .main-body {
        height: 1fr;
        width: 100%;
    }
    """

    def __init__(
        self,
        session: Optional[str] = None,
        demo: bool = False,
        project_path: Optional[Path] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._session_argument = session
        self.session_name = self._requested_session()
        self.socket_path: Optional[Path] = None
        self.demo = demo
        self.project_path = Path(project_path) if project_path else None
        self.config_error: Optional[str] = None
        self.squad_sources: dict[str, str] = {}
        self.squad_mode = "herdr"
        self.native_harness: Optional[str] = None
        self._launch_pending = False
        self._inventory_pending = False
        self._navigation_pending = False
        if demo:
            self.app_state = AppState.create_demo_state()
            self.squad_sources = {
                key: "product_default" for key in ("scout", "architect", "reviewer", "workers")
            }
        else:
            self.app_state = self._load_live_state()
        self._settings_squad = self.app_state.squad.model_copy(deep=True)
        self.client: Optional[HerdrClient] = None
        self._header = HeaderBar(self.app_state)
        self._deck = CenterDeck(self.app_state)
        self._footer = ActionFooter()

    def _load_live_state(self) -> AppState:
        """Resolve the project squad. Invalid settings must not become a silent default."""
        try:
            resolved = resolve_squad(self.project_path)
        except Exception as exc:
            self.config_error = (
                f"Invalid project squad settings: {exc}. "
                "Fix .herdr-master.toml before launching. "
                "Refusing to launch product defaults."
            )
            return self._state(branch_status="INVALID CONFIG", socket_connected=False)
        self.squad_sources = dict(resolved.sources)
        self.squad_mode = resolved.mode
        self.native_harness = resolved.native_harness
        return self._state(squad=resolved.squad)

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(classes="main-body"):
            yield self._deck
        yield self._footer

    async def on_mount(self) -> None:
        """Initialize socket connection and poll live state if not in demo mode."""
        if self.config_error:
            self.notify(self.config_error, severity="error", timeout=8)
        if not self.demo:
            if self._requested_session() is None:
                self._bind_launch_client(resolve_launch_context(None))
            else:
                self.client = HerdrClient(session=self.session_name)
            self._schedule_inventory_refresh()
            self.set_interval(3.0, self._schedule_inventory_refresh)

    def _schedule_inventory_refresh(self) -> None:
        self.run_worker(self._refresh_live_snapshot(), group="workspace-inventory")

    async def _refresh_live_snapshot(self) -> None:
        """Refresh all discovered sockets without blocking terminal interaction."""
        if self.demo or self.client is None or self._inventory_pending:
            return
        self._inventory_pending = True
        try:
            previous = {item.socket_path: item for item in self.app_state.live_sessions}

            async def fetch(client: HerdrClient) -> SessionInventory:
                path = Path(client.socket_path)
                inventory = SessionInventory(
                    session_name=client.session or self.session_name or "default", socket_path=path
                )
                try:
                    inventory.snapshot = await asyncio.wait_for(client.get_snapshot(), timeout=2.0)
                except Exception as exc:
                    inventory.error = str(exc)[:140] or type(exc).__name__
                    if path in previous:
                        inventory.snapshot = previous[path].snapshot
                return inventory

            clients = await asyncio.to_thread(discover_clients, self.client)
            inventories = await asyncio.gather(*(fetch(client) for client in clients))
            self.app_state.live_sessions = list(inventories)
            self.app_state.socket_connected = any(not item.error for item in inventories)
            self.app_state.active_agents_count = sum(
                agent.agent_status in {"working", "idle", "blocked"}
                for item in inventories
                if item.snapshot and not item.error
                for agent in item.snapshot.agents
            )
            primary = next(
                (item for item in inventories if item.socket_path == Path(self.client.socket_path)),
                None,
            )
            if primary and primary.snapshot and not primary.error:
                snapshot = primary.snapshot
                workspace = next(
                    (
                        ws
                        for ws in snapshot.workspaces
                        if ws.workspace_id == snapshot.focused_workspace_id
                    ),
                    next(iter(snapshot.workspaces), None),
                )
                if workspace:
                    self.app_state.workspace_id = workspace.workspace_id
                    self.app_state.workspace_label = workspace.label or workspace.workspace_id
            self._update_all_widgets()
        finally:
            self._inventory_pending = False

    def action_open_in_herdr(self) -> None:
        """Open selected workspace or pane in Herdr."""
        if len(self.screen_stack) > 1:
            return
        if self.demo:
            self.notify("Demo mode: live terminal navigation is disabled.", severity="warning")
            return
        try:
            browser = self.query_one(WorkspaceBrowser)
            browser.request_open()
        except Exception:
            pass

    def on_workspace_browser_open_requested(
        self, event: WorkspaceBrowser.OpenRequested
    ) -> None:
        event.stop()
        if not self._navigation_pending:
            self.run_worker(self._open_in_herdr(event.target), group="agent-navigation")

    async def _open_in_herdr(self, target: WorkspaceTarget) -> None:
        """Focus the exact socket/workspace/tab/pane; hand off safely to Herdr attach."""
        if self.demo:
            self.notify("Demo mode: live terminal navigation is disabled.", severity="warning")
            return
        if self._navigation_pending:
            return
        if len(self.screen_stack) > 1:
            return
        self._navigation_pending = True
        try:
            inventory = next(
                (
                    item
                    for item in self.app_state.live_sessions
                    if Path(item.socket_path) == Path(target.socket_path)
                ),
                None,
            )
            if not inventory or inventory.error or not inventory.snapshot:
                self.notify("Session is offline. Wait for it to reconnect.", severity="warning")
                return

            snapshot = inventory.snapshot
            ws = next(
                (w for w in snapshot.workspaces if w.workspace_id == target.workspace_id),
                None,
            )
            if not ws:
                self.notify("Workspace has closed or no longer exists.", severity="warning")
                return

            agents = workspace_agents(inventory, target.workspace_id)
            if not agents:
                self.notify("Workspace has no open panes to open.", severity="warning")
                return

            target_pane: Optional[AgentSnapshot] = None
            target_tab_id: Optional[str] = None
            if target.pane_id is not None:
                target_pane = next((p for p in agents if p.pane_id == target.pane_id), None)
                if not target_pane:
                    self.notify("This pane has closed. Select a current agent.", severity="warning")
                    return
                if target_pane.tab_id:
                    tab = next((t for t in snapshot.tabs if t.tab_id == target_pane.tab_id), None)
                    if tab is None:
                        self.notify(
                            f"Pane references missing tab '{target_pane.tab_id}'. "
                            "Cannot open in Herdr.",
                            severity="warning",
                        )
                        return
                    if tab.workspace_id != target.workspace_id:
                        self.notify(
                            f"Pane references tab '{tab.tab_id}' belonging to another workspace "
                            f"'{tab.workspace_id}'. Cannot open in Herdr.",
                            severity="warning",
                        )
                        return
                    target_tab_id = tab.tab_id
                else:
                    target_tab_id = None

                raw_pane = next((p for p in snapshot.panes if p.pane_id == target.pane_id), None)
                if raw_pane is not None:
                    if raw_pane.workspace_id and raw_pane.workspace_id != target.workspace_id:
                        self.notify(
                            "Pane workspace ownership is contradictory. Cannot open in Herdr.",
                            severity="warning",
                        )
                        return
                    if (
                        raw_pane.tab_id
                        and target_pane.tab_id
                        and raw_pane.tab_id != target_pane.tab_id
                    ):
                        self.notify(
                            "Pane tab ownership is contradictory. Cannot open in Herdr.",
                            severity="warning",
                        )
                        return

            context = resolve_herdr_context()
            same_socket = (
                context.is_inside_herdr
                and context.socket_path is not None
                and Path(context.socket_path) == Path(target.socket_path)
            )

            if not same_socket:
                try:
                    resolved_target_context = resolve_herdr_context(
                        explicit_session=inventory.session_name
                    )
                    target_ctx_socket = (
                        Path(resolved_target_context.socket_path)
                        if resolved_target_context.socket_path
                        else None
                    )
                except Exception:
                    target_ctx_socket = None

                if target_ctx_socket is None or target_ctx_socket != Path(inventory.socket_path):
                    self.notify(
                        f"Cannot attach to nonstandard socket for session "
                        f"'{inventory.session_name}'. Open it through its owning Herdr client.",
                        severity="warning",
                    )
                    return

            client = HerdrClient(
                session=inventory.session_name,
                socket_path=target.socket_path,
                timeout=2.0,
            )
            await client.focus_workspace(target.workspace_id)
            if target.pane_id is not None and target_pane is not None:
                if target_tab_id is not None:
                    await client.focus_tab(target_tab_id)
                await client.focus_pane(target_pane.pane_id)

            if not same_socket:
                self.exit(result={"action": "attach", "session": inventory.session_name})
            else:
                if target.pane_id is None:
                    self.notify(f"Focused workspace {ws.label or ws.workspace_id}")
                else:
                    pane_name = (
                        target_pane.name or target_pane.agent or target_pane.pane_id
                        if target_pane
                        else target.pane_id
                    )
                    self.notify(f"Focused {pane_name}")
        except Exception as exc:
            self.notify(f"Could not open in Herdr: {exc}", severity="error")
        finally:
            self._navigation_pending = False

    def _update_all_widgets(self) -> None:
        self._header.update_state(self.app_state)
        self._deck.update_state(self.app_state)

    def _requested_session(self) -> Optional[str]:
        """Explicit session, or None when the operator omitted it.

        Blank text is omitted. Do not invent ``default`` here.
        """
        raw = self._session_argument
        if not isinstance(raw, str):
            return None
        text = raw.strip()
        return text or None

    def _state(self, **kwargs):
        if self.session_name:
            kwargs.setdefault("session_name", self.session_name)
        return AppState(**kwargs)

    def _bind_launch_client(self, ctx) -> None:
        """Point status and launch at the resolved session socket."""
        self.session_name = ctx.session_name
        self.socket_path = Path(ctx.socket_path)
        self.app_state.session_name = ctx.session_name
        client = self.client
        if (
            client is not None
            and getattr(client, "session", None) == ctx.session_name
            and Path(getattr(client, "socket_path", "")) == self.socket_path
        ):
            return
        self.client = HerdrClient(session=ctx.session_name, socket_path=self.socket_path)

    def _context_for_launch(self):
        """Resolve omitted session as None. An explicit mismatch raises first."""
        ctx = resolve_launch_context(self._requested_session())
        self._bind_launch_client(ctx)
        return ctx

    def _begin_launch(self, worker, name: str) -> None:
        if self._launch_pending:
            worker.close()
            self.notify(
                "Launch already pending. Wait for the current launch to finish.",
                severity="warning",
            )
            return
        self._launch_pending = True
        self.run_worker(worker, name=name)

    def _exit_launch(self, ctx, session_name: str, workspace_id: Optional[str]) -> None:
        """Keep this multi-project cockpit open; agent navigation remains explicit."""
        self.notify(
            f"Launch is in workspace {workspace_id or session_name}. This cockpit stays open.",
            severity="information",
        )
        self._schedule_inventory_refresh()

    def action_toggle_focus(self) -> None:
        """Focus the CenterDeck."""
        self._deck.focus()

    def action_new_master(self) -> None:
        """Open the Launch Herdr Master dialog."""

        async def _on_master_launch(params: MasterLaunchParams) -> None:
            try:
                project_path = validate_project_folder(
                    str(params.project_path or initial_project_folder(self.project_path))
                )
                resolve_squad(project_path)
                if self.demo or self.client is None:
                    self._record_demo_master(params)
                    return
                ctx = self._context_for_launch()
                result = await launch_master(
                    client=self.client,
                    name=params.name,
                    harness=params.harness,
                    model=params.model,
                    initial_task=params.initial_task,
                    session=ctx.session_name,
                    effort=params.effort,
                    cwd=str(project_path),
                    fresh_workspace=True,
                    focus=False,
                )
                pane = _result_pane(result)
                session_name = result.get("session")
                if not pane or not session_name:
                    self.notify(
                        "Launch result missing pane_id or session. No master was recorded.",
                        severity="error",
                    )
                    return
                self._record_live_master(params, result, pane)
                self.notify(f"Master active in pane {pane}", severity="information")
                self._exit_launch(ctx, str(session_name), result.get("workspace_id"))
            except LaunchContextError as exc:
                self.notify(str(exc), severity="error")
            except Exception as exc:
                self.notify(f"Herdr launch error: {exc}", severity="error")
            finally:
                self._launch_pending = False

        def _on_modal_dismiss(params: Optional[MasterLaunchParams]) -> None:
            if params is not None:
                self._begin_launch(_on_master_launch(params), "launch_master")

        self.push_screen(
            LaunchMasterModal(config_error=self.config_error, project_path=self.project_path),
            _on_modal_dismiss,
        )

    def _record_demo_master(self, params: MasterLaunchParams) -> None:
        recorded = {}
        if self.session_name:
            recorded["herdr_session"] = self.session_name
        master = MasterSession(
            session_id=f"master-{params.name}",
            workspace_id=self.app_state.workspace_id or "default",
            **recorded,
            pane_id="p-master",
            harness=params.harness,
            role="root-orchestrator",
            metadata={
                "model": params.model,
                "effort": params.effort,
                "demo": True,
                "socket_path": str(Path("/demo/herdr.sock")),
            },
        )
        self.app_state.masters[master.session_id] = master
        self.app_state.selected_target_id = master.session_id
        self._update_all_widgets()

    def _record_live_master(self, params: MasterLaunchParams, result: dict, pane: str) -> None:
        master = MasterSession(
            session_id=f"master-{params.name}",
            herdr_session=str(result.get("session") or self.session_name),
            workspace_id=str(result.get("workspace_id") or ""),
            pane_id=pane,
            harness=params.harness,
            role="root-orchestrator",
            metadata={
                "model": result.get("model"),
                "effort": result.get("effort"),
                "pane_id": pane,
                "project_path": str(
                    params.project_path or initial_project_folder(self.project_path)
                ),
                "socket_path": str(result.get("socket") or "").strip(),
            },
        )
        self.app_state.masters[master.session_id] = master
        self.app_state.selected_target_id = master.session_id
        self._update_all_widgets()

    def action_new_ergo(self) -> None:
        """Open the 2-column Squad Settings & Ergo Pipeline Launcher."""
        self.action_open_settings()

    def action_open_settings(self) -> None:
        """Open the 2-column Squad Settings & Ergo Pipeline Launcher dialog."""

        async def _on_ergo_launch(params: ErgoLaunchParams) -> None:
            try:
                if not params.start_pipeline:
                    if params.squad is not None:
                        self.app_state.squad = params.squad
                        self._settings_squad = params.squad.model_copy(deep=True)
                        self._update_all_widgets()
                    return
                project_path = validate_project_folder(
                    str(params.project_path or initial_project_folder(self.project_path))
                )
                resolved = resolve_squad(project_path)
                if self.demo or self.client is None:
                    self.notify("Demo mode does not launch an orchestrator.", severity="warning")
                    return
                ctx = self._context_for_launch()
                result = await launch_ergo(
                    client=self.client,
                    intent=params.intent,
                    workspace_label=params.workspace_label,
                    squad=params.squad or resolved.squad,
                    cwd=str(project_path),
                    session=ctx.session_name,
                    fresh_workspace=True,
                    focus=False,
                )
                pane = _result_pane(result)
                session_name = result.get("session")
                if not pane or not session_name:
                    self.notify(
                        "Launch result missing pane_id. "
                        "No run was recorded and no attach was started.",
                        severity="error",
                    )
                    return
                workspace_id = result.get("workspace_id")
                self.app_state.squad = params.squad or resolved.squad
                self.app_state.workspace_label = params.workspace_label
                self.app_state.pipeline_run = PipelineRun(
                    run_id=f"ergo-{params.workspace_label}",
                    intent=params.intent,
                    squad=self.app_state.squad,
                    phase=PipelinePhase.PREFLIGHT,
                    workspace_id=str(workspace_id) if workspace_id else None,
                    metadata={
                        "pane_id": pane,
                        "orchestrator_pane_id": result.get("orchestrator_pane_id") or pane,
                        "session": session_name,
                        "agent_name": result.get("agent_name"),
                        "project_path": str(project_path),
                    },
                )
                self._update_all_widgets()
                self.notify(f"Ergo orchestrator pane {pane}", severity="information")
                self._exit_launch(ctx, str(session_name), workspace_id)
            except LaunchContextError as exc:
                self.notify(str(exc), severity="error")
            except Exception as exc:
                self.notify(f"Ergo launch error: {exc}", severity="error")
            finally:
                if params.start_pipeline:
                    self._launch_pending = False

        def _on_modal_dismiss(params: Optional[ErgoLaunchParams]) -> None:
            if params is None:
                return
            if not params.start_pipeline:
                self.run_worker(_on_ergo_launch(params), name="apply_squad")
                return
            self._begin_launch(_on_ergo_launch(params), "launch_ergo")

        self.push_screen(
            LaunchErgoModal(
                self._settings_squad,
                sources=self.squad_sources,
                mode=self.squad_mode,
                native_harness=self.native_harness,
                project_path=self.project_path,
                config_error=self.config_error,
            ),
            _on_modal_dismiss,
        )

    def on_quick_launch_bar_launch_master_requested(
        self, event: QuickLaunchBar.LaunchMasterRequested
    ) -> None:
        self.action_new_master()

    def on_quick_launch_bar_launch_ergo_requested(
        self, event: QuickLaunchBar.LaunchErgoRequested
    ) -> None:
        self.action_new_ergo()

    def on_quick_launch_bar_open_settings_requested(
        self, event: QuickLaunchBar.OpenSettingsRequested
    ) -> None:
        self.action_open_settings()

    def action_open_config(self) -> None:
        """Open the configuration drawer modal."""
        target = self.app_state.selected_target_id or "scout"

        def _on_drawer_closed(result):
            if result is not None:
                new_squad, is_default = result
                self.app_state.squad = new_squad
                if self.app_state.pipeline_run:
                    self.app_state.pipeline_run.squad = new_squad
                self._update_all_widgets()
                self.notify(
                    f"Squad configuration saved {'(default)' if is_default else ''}",
                    severity="information",
                )

        self.push_screen(
            ConfigDrawer(
                self.app_state.squad,
                target=target,
                mode=self.squad_mode,
                native_harness=self.native_harness,
                project_path=self.project_path,
            ),
            _on_drawer_closed,
        )

    def on_target_selected(self, message: TargetSelected) -> None:
        """Update selected target across tree and deck."""
        self.app_state.selected_target_id = message.target_id
        self._deck.update_state(self.app_state)
