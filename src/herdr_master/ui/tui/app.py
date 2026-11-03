"""Textual Terminal User Interface (TUI) Application for Herdr-Master."""

from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal

from herdr_master.core.models import MasterSession, PipelinePhase, PipelineRun, WorkerState
from herdr_master.core.state_machine import WorkerEvent, transition_worker_state
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.launcher import launch_ergo, launch_master
from herdr_master.ui.tui.messages import (
    TargetSelected,
)
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
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session_name = session or "default"
        self.demo = demo
        if demo:
            self.app_state = AppState.create_demo_state()
        else:
            self.app_state = AppState(session_name=self.session_name)
        self.client: Optional[HerdrClient] = None
        self._header = HeaderBar(self.app_state)
        self._deck = CenterDeck(self.app_state)
        self._footer = ActionFooter()

    def compose(self) -> ComposeResult:
        yield self._header
        with Horizontal(classes="main-body"):
            yield self._deck
        yield self._footer

    async def on_mount(self) -> None:
        """Initialize socket connection and poll live state if not in demo mode."""
        if not self.demo:
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
                [a for a in snapshot.agents if a.agent_status != "done"]
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

    # --- Action Handlers ---

    def action_toggle_focus(self) -> None:
        """Focus the CenterDeck."""
        self._deck.focus()

    def action_new_master(self) -> None:
        """Open the Launch Herdr Master dialog."""

        async def _on_master_launch(params: MasterLaunchParams) -> None:
            self.notify(
                f"Launching Herdr Master: {params.name} ({params.model})",
                severity="information",
            )
            target_ws_id = self.app_state.workspace_id or "default"
            target_pane_id = "p-master"
            if not self.demo and self.client:
                try:
                    res = await launch_master(
                        client=self.client,
                        name=params.name,
                        harness=params.harness,
                        model=params.model,
                        initial_task=params.initial_task,
                    )
                    target_ws_id = res.get("workspace_id", target_ws_id)
                    target_pane_id = res.get("pane_id", target_pane_id)
                    self.notify(
                        f"Master active in pane {target_pane_id}",
                        severity="information",
                    )
                except Exception as e:
                    self.notify(f"Herdr launch error: {e}", severity="error")

            master_session = MasterSession(
                session_id=f"master-{params.name}",
                herdr_session=self.session_name,
                workspace_id=target_ws_id,
                pane_id=target_pane_id,
                harness=params.harness,
                role="root-orchestrator",
            )
            self.app_state.masters[master_session.session_id] = master_session
            self.app_state.selected_target_id = master_session.session_id
            self._update_all_widgets()

        def _on_modal_dismiss(params: Optional[MasterLaunchParams]) -> None:
            if params is not None:
                self.run_worker(_on_master_launch(params), name="launch_master")

        self.push_screen(LaunchMasterModal(), _on_modal_dismiss)

    def action_new_ergo(self) -> None:
        """Open the 2-column Squad Settings & Ergo Pipeline Launcher."""
        self.action_open_settings()

    def action_open_settings(self) -> None:
        """Open the 2-column Squad Settings & Ergo Pipeline Launcher dialog."""

        async def _on_ergo_launch(params: ErgoLaunchParams) -> None:
            if params.squad:
                self.app_state.squad = params.squad
                self._update_all_widgets()

            if not params.start_pipeline:
                return

            self.notify(
                f"Launching Ergo Pipeline: '{params.intent}'",
                severity="information",
            )
            run_id = f"ergo-{params.workspace_label}"
            self.app_state.pipeline_run = PipelineRun(
                run_id=run_id,
                intent=params.intent,
                squad=self.app_state.squad,
                phase=PipelinePhase.SCOUT,
            )
            if not self.demo and self.client:
                try:
                    res = await launch_ergo(
                        client=self.client,
                        intent=params.intent,
                        workspace_label=params.workspace_label,
                        squad=self.app_state.squad,
                    )
                    self.app_state.workspace_id = res.get("workspace_id")
                    self.app_state.workspace_label = params.workspace_label
                    self.notify(
                        f"Ergo pipeline active in workspace '{params.workspace_label}'",
                        severity="information",
                    )
                except Exception as e:
                    self.notify(f"Ergo launch error: {e}", severity="error")

            self.app_state.active_deck_mode = 3
            self._update_all_widgets()

        def _on_modal_dismiss(params: Optional[ErgoLaunchParams]) -> None:
            if params is not None:
                self.run_worker(_on_ergo_launch(params), name="launch_ergo")

        self.push_screen(LaunchErgoModal(self.app_state.squad), _on_modal_dismiss)

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

        self.push_screen(ConfigDrawer(self.app_state.squad, target=target), _on_drawer_closed)

    def action_prompt_agent(self) -> None:
        """Prompt the currently selected agent."""
        target = self.app_state.selected_target_id or "active-agent"

        def _on_prompt_submitted(prompt_text: Optional[str]):
            if prompt_text:
                self.notify(f"Dispatched prompt to {target}", severity="information")
                # Append to output stream
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
                except Exception as e:
                    self.notify(f"Review transition error: {e}", severity="error")

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
            w for w in self.app_state.workers.values() if w.state == WorkerState.APPROVED
        ]
        if not approved_workers:
            self.notify("No workers in APPROVED state ready for landing.", severity="warning")
            return

        def _on_confirm_squash(confirmed: bool):
            if confirmed:
                for w in approved_workers:
                    w.state = WorkerState.LANDED
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

    # --- Message Handlers ---

    def on_target_selected(self, message: TargetSelected) -> None:
        """Update selected target across tree and deck."""
        self.app_state.selected_target_id = message.target_id
        self._deck.update_state(self.app_state)
