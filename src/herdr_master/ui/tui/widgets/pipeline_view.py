"""Dedicated Ergo Pipeline View widget with Role Cards, Worker Pool, DAG, and Gates."""

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Static

from herdr_master.core.models import PipelinePhase
from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.dag import TaskDAGWidget
from herdr_master.ui.tui.widgets.quick_launch import QuickLaunchBar


class RoleCardWidget(Static):
    """Card displaying binding, state, artifact, and error for a single fixed role."""

    DEFAULT_CSS = """
    RoleCardWidget {
        width: 1fr;
        height: auto;
        margin: 0 1;
        padding: 0 1;
        background: #181818;
        border: solid #333333;
    }
    """

    def __init__(self, role_name: str, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.role_name = role_name
        self.app_state = app_state

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self.refresh_card()

    def on_mount(self) -> None:
        self.refresh_card()

    def refresh_card(self) -> None:
        squad = self.app_state.squad
        run = self.app_state.pipeline_run
        phase = run.phase if run else PipelinePhase.PREFLIGHT

        if self.role_name.lower() == "scout":
            binding = squad.scout
            artifact = "RUN_DIR/context.md"
            active = phase in (PipelinePhase.SCOUT, PipelinePhase.PREFLIGHT)
            badge_color = "cyan" if active else "green"
            badge = "ACTIVE" if active else "READY"
            warning = None
        elif self.role_name.lower() == "architect":
            binding = squad.architect
            artifact = "RUN_DIR/plan.md"
            active = phase == PipelinePhase.ARCHITECT
            badge_color = "cyan" if active else "green"
            badge = "ACTIVE" if active else "READY"
            warning = None
        else:  # Reviewer
            binding = squad.reviewer
            artifact = "review.md"
            active = phase in (PipelinePhase.REVIEW, PipelinePhase.FIX_LOOP)
            badge_color = "cyan" if active else "dim"
            badge = "ACTIVE" if active else "STANDBY"
            warning = (
                "⚠️ Same model family as Architect"
                if squad.has_reviewer_model_family_conflict()
                else None
            )

        text = Text()
        text.append(f"👑 {self.role_name.upper()}\n", style="bold cyan")
        text.append(f"Binding: {binding.to_slug()}\n", style="bold white")
        text.append(f"Status:  [{badge}]\n", style=f"bold {badge_color}")
        text.append(f"Output:  {artifact}\n", style="dim")
        if warning:
            text.append(f"{warning}\n", style="bold yellow")

        self.update(Panel(text, border_style=badge_color))


class WorkerPoolStripWidget(Static):
    """Strip displaying ordered worker catalog rows with assigned task chips."""

    DEFAULT_CSS = """
    WorkerPoolStripWidget {
        height: auto;
        margin: 1 0;
        background: #141414;
        border: solid #333333;
        padding: 0 1;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self.refresh_strip()

    def on_mount(self) -> None:
        self.refresh_strip()

    def refresh_strip(self) -> None:
        squad = self.app_state.squad
        run = self.app_state.pipeline_run

        table = Table(
            title="[bold cyan]Ordered Specialist Worker Pool[/bold cyan]",
            title_justify="left",
            border_style="dim",
            expand=True,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("#", width=4, style="bold")
        table.add_column("Binding Slug", width=36)
        table.add_column("Capability Label", width=22)
        table.add_column("Assigned Tasks", style="bold green")

        for idx, worker in enumerate(squad.worker_pool):
            assigned_chips: list[str] = []
            if run and run.tasks:
                for t in run.tasks:
                    if t.binding and t.binding.to_slug() == worker.to_slug():
                        assigned_chips.append(f"[{t.task_id}]")

            chips_str = " ".join(assigned_chips) if assigned_chips else "[dim]Unassigned[/dim]"
            label_str = worker.label or f"worker_{idx}"
            table.add_row(str(idx + 1), worker.to_slug(), label_str, chips_str)

        self.update(table)


class ApprovalGateBar(Static):
    """Action bar for Approval Gate (Proceed / Edit Plan / Abort)."""

    DEFAULT_CSS = """
    ApprovalGateBar {
        height: 3;
        margin-top: 1;
        background: #1c1c1c;
        border-top: solid cyan;
        padding: 0 1;
        align: center middle;
    }
    ApprovalGateBar Button {
        margin: 0 2;
    }
    """

    def __init__(self, is_enabled: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.is_enabled = is_enabled

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Button(
                "Proceed (Approval Gate)",
                id="btn-proceed",
                variant="success",
                disabled=not self.is_enabled,
            )
            yield Button("Edit Plan", id="btn-edit-plan", variant="primary")
            yield Button("Abort Pipeline", id="btn-abort", variant="error")


class PipelineView(VerticalScroll):
    """The complete Ergo cockpit view featuring roles, pool, DAG, and gates."""

    DEFAULT_CSS = """
    PipelineView {
        height: 1fr;
        padding: 1;
    }
    .cards-container {
        height: auto;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._quick_launch = QuickLaunchBar()
        self._scout_card = RoleCardWidget("Scout", app_state)
        self._architect_card = RoleCardWidget("Architect", app_state)
        self._reviewer_card = RoleCardWidget("Reviewer", app_state)
        self._pool_strip = WorkerPoolStripWidget(app_state)
        self._dag = TaskDAGWidget(app_state.pipeline_run)
        self._gate_bar = ApprovalGateBar(is_enabled=len(app_state.squad.worker_pool) > 0)

    def compose(self) -> ComposeResult:
        yield self._quick_launch
        with Horizontal(classes="cards-container"):
            yield self._scout_card
            yield self._architect_card
            yield self._reviewer_card
        yield self._pool_strip
        yield self._dag
        yield self._gate_bar

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self._scout_card.update_state(app_state)
        self._architect_card.update_state(app_state)
        self._reviewer_card.update_state(app_state)
        self._pool_strip.update_state(app_state)
        self._dag.update_run(app_state.pipeline_run)
