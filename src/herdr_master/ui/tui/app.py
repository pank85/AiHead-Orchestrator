"""Textual Terminal User Interface (TUI) Application for Herdr-Master."""

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal

from herdr_master.core.models import MasterSession, PipelinePhase, PipelineRun, WorkerState
from herdr_master.core.state_machine import WorkerEvent, transition_worker_state
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.launcher import (
    LaunchContextError,
    launch_ergo,
    launch_master,
    resolve_launch_context,
)
from herdr_master.pipeline.squad import resolve_squad
from herdr_master.ui.tui.messages import TargetSelected
from herdr_master.ui.tui.modals.config_drawer import ConfigDrawer
from herdr_master.ui.tui.modals.confirm import ConfirmModal
from herdr_master.ui.tui.modals.launch_ergo import ErgoLaunchParams, LaunchErgoModal
from herdr_master.ui.tui.modals.launch_master import LaunchMasterModal, MasterLaunchParams
from herdr_master.ui.tui.modals.prompt_dialog import PromptDialog
from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.deck import CenterDeck
from herdr_master.ui.tui.widgets.footer import ActionFooter
from herdr_master.ui.tui.widgets.header_bar import HeaderBar
from herdr_master.ui.tui.widgets.quick_launch import QuickLaunchBar


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
        Binding("1", "deck_mode(1)", "Output"),
        Binding("2", "deck_mode(2)", "Diff"),
        Binding("3", "deck_mode(3)", "Pipeline"),
        Binding("4", "deck_mode(4)", "Mailbox"),
        Binding("p", "prompt_agent", "Prompt"),
        Binding("r", "review_worker", "Review"),
        Binding("m", "squash_land", "Squash"),
        Binding("k", "kill_worker", "Kill"),
        Binding("d", "deck_mode(2)", "Diff"),
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
        if demo:
            self.app_state = AppState.create_demo_state()
            self.squad_sources = {
                key: "product_default" for key in ("scout", "architect", "reviewer", "workers")
            }
        else:
            self.app_state = self._load_live_state()
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
            await self._refresh_live_snapshot()

    async def _refresh_live_snapshot(self) -> None:
        """Query live Herdr session snapshot."""
        if self.client is None:
            return
        try:
            snapshot = await self.client.get_snapshot()
            self.app_state.socket_connected = True
            self.app_state.active_agents_count = len(
                [agent for agent in snapshot.agents if agent.agent_status != "done"]
            )
            if snapshot.workspaces:
                first_ws = snapshot.workspaces[0]
                self.app_state.workspace_id = first_ws.workspace_id
                self.app_state.workspace_label = first_ws.label or first_ws.workspace_id
            self._update_all_widgets()
        except Exception:
            self.app_state.socket_connected = False
            self._update_all_widgets()

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
        if self.config_error or self._launch_pending:
            worker.close()
            if self.config_error:
                self.notify(self.config_error, severity="error")
            else:
                self.notify(
                    "Launch already pending. Wait for the current launch to finish.",
                    severity="warning",
                )
            return
        self._launch_pending = True
        self.run_worker(worker, name=name)

    def _exit_launch(self, ctx, session_name: str, workspace_id: Optional[str]) -> None:
        if not ctx.is_inside_herdr:
            self.exit(result={"action": "attach", "session": session_name})
            return
        self.exit(
            result={
                "action": "done",
                "session": session_name,
                "workspace_id": workspace_id,
            }
        )

    def action_toggle_focus(self) -> None:
        """Focus the CenterDeck."""
        self._deck.focus()

    def action_new_master(self) -> None:
        """Open the Launch Herdr Master dialog."""

        async def _on_master_launch(params: MasterLaunchParams) -> None:
            try:
                if self.config_error:
                    self.notify(self.config_error, severity="error")
                    return
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
            LaunchMasterModal(config_error=self.config_error),
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
            metadata={"model": params.model, "effort": params.effort, "demo": True},
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
                        self._update_all_widgets()
                    return
                if self.config_error:
                    self.notify(self.config_error, severity="error")
                    return
                if self.demo or self.client is None:
                    self.notify("Demo mode does not launch an orchestrator.", severity="warning")
                    return
                ctx = self._context_for_launch()
                result = await launch_ergo(
                    client=self.client,
                    intent=params.intent,
                    workspace_label=params.workspace_label,
                    squad=params.squad or self.app_state.squad,
                    session=ctx.session_name,
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
                if params.squad is not None:
                    self.app_state.squad = params.squad
                if workspace_id:
                    self.app_state.workspace_id = str(workspace_id)
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
                    },
                )
                self.app_state.active_deck_mode = 3
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
                self.app_state.squad,
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

    def action_deck_mode(self, mode: int) -> None:
        """Switch center deck view mode (1..4)."""
        self.app_state.active_deck_mode = mode
        self._deck.switch_mode(mode)

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

    def action_prompt_agent(self) -> None:
        """Prompt the currently selected agent."""
        target = self.app_state.selected_target_id or "active-agent"

        def _on_prompt_submitted(prompt_text: Optional[str]):
            if prompt_text:
                self.notify(f"Dispatched prompt to {target}", severity="information")
                current = self.app_state.output_scrollback.get(target, "")
                self.app_state.output_scrollback[target] = (
                    f"{current}\n> {prompt_text}\n[Processing...]"
                )
                self._deck.update_state(self.app_state)

        self.push_screen(PromptDialog(target_name=target), _on_prompt_submitted)

    def action_review_worker(self) -> None:
        """Trigger master review on selected worker."""
        target = self.app_state.selected_target_id
        if not target or target not in self.app_state.workers:
            self.notify("Select an active worker to review", severity="warning")
            return

        worker = self.app_state.workers[target]
        if worker.state not in (WorkerState.DONE, WorkerState.NEEDS_REVIEW):
            self.notify(
                f"Worker {target} is {worker.state.value}. Review requires DONE or NEEDS_REVIEW.",
                severity="warning",
            )
            return

        def _on_confirm_review(confirmed: bool):
            if confirmed:
                try:
                    worker.state = transition_worker_state(
                        worker.state,
                        WorkerEvent.REVIEW_APPROVED,
                    )
                    self.notify(
                        f"Worker {target} marked APPROVED for landing.", severity="information"
                    )
                    self._update_all_widgets()
                except Exception as exc:
                    self.notify(f"Review transition error: {exc}", severity="error")

        self.push_screen(
            ConfirmModal(
                title=f"Review Worker {target}",
                message=f"Approve worktree changes on branch '{worker.branch}'?",
            ),
            _on_confirm_review,
        )

    def action_squash_land(self) -> None:
        """Initiate serial squash merge onto develop branch."""
        approved_workers = [
            worker
            for worker in self.app_state.workers.values()
            if worker.state == WorkerState.APPROVED
        ]
        if not approved_workers:
            self.notify("No workers in APPROVED state ready for landing.", severity="warning")
            return

        def _on_confirm_squash(confirmed: bool):
            if confirmed:
                for worker in approved_workers:
                    worker.state = WorkerState.LANDED
                self.notify(
                    f"Successfully landed {len(approved_workers)} worker(s) onto develop.",
                    severity="information",
                )
                self._update_all_widgets()

        self.push_screen(
            ConfirmModal(
                title="Confirm Squash Landing",
                message=(
                    f"Squash land {len(approved_workers)} approved worker branch(es) "
                    f"to '{self.app_state.target_branch}'?"
                ),
            ),
            _on_confirm_squash,
        )

    def action_kill_worker(self) -> None:
        """Terminate selected worker tab."""
        target = self.app_state.selected_target_id
        if not target or target not in self.app_state.workers:
            self.notify("Select a worker to kill", severity="warning")
            return

        def _on_confirm_kill(confirmed: bool):
            if confirmed:
                self.app_state.workers[target].state = WorkerState.FAILED
                self.notify(
                    f"Worker {target} terminated. Worktree preserved.",
                    severity="warning",
                )
                self._update_all_widgets()

        self.push_screen(
            ConfirmModal(
                title=f"Kill Worker {target}",
                message=f"Close tab for {target}? Uncommitted worktrees will be preserved.",
            ),
            _on_confirm_kill,
        )

    def on_target_selected(self, message: TargetSelected) -> None:
        """Update selected target across tree and deck."""
        self.app_state.selected_target_id = message.target_id
        self._deck.update_state(self.app_state)
