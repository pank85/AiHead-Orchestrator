"""Dedicated Ergo Pipeline View widget with role cards, worker pool, and DAG."""

from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Static

from herdr_master.core.models import PipelinePhase
from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.dag import TaskDAGWidget


class RoleCardWidget(Static):
    """Card displaying binding, state, artifact, and error for a single fixed role."""

    DEFAULT_CSS = """
    RoleCardWidget {
        width: 1fr;
        height: auto;
        margin: 0;
        padding: 0 1;
        background: #181818;
        border-left: solid #333333;
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
        text.append(f"{self.role_name.upper()} ", style="bold cyan")
        text.append(f"[{badge}]\n", style=f"bold {badge_color}")
        text.append(f"{binding.to_slug()}\n", style="bold white")
        text.append(artifact, style="dim")
        if warning:
            text.append(f"\n{warning}", style="bold yellow")

        self.update(text)


class WorkerPoolStripWidget(Static):
    """Strip displaying ordered worker catalog rows with assigned task chips."""

    DEFAULT_CSS = """
    WorkerPoolStripWidget {
        height: auto;
        margin: 1 0 0 0;
        background: #141414;
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
            title="[bold cyan]Worker Pool[/bold cyan]",
            box=None,
            pad_edge=False,
            title_justify="left",
            border_style="dim",
            expand=True,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("#", width=2, style="bold")
        table.add_column("Binding Slug", ratio=2)
        table.add_column("Capability Label", ratio=1)
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


class PipelineView(VerticalScroll):
    """The Ergo cockpit view featuring roles, worker pool, and task DAG."""

    DEFAULT_CSS = """
    PipelineView {
        height: 1fr;
        padding: 0 1;
    }
    .cards-container {
        height: auto;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._scout_card = RoleCardWidget("Scout", app_state)
        self._architect_card = RoleCardWidget("Architect", app_state)
        self._reviewer_card = RoleCardWidget("Reviewer", app_state)
        self._pool_strip = WorkerPoolStripWidget(app_state)
        self._dag = TaskDAGWidget(app_state.pipeline_run)

    def compose(self) -> ComposeResult:
        with Horizontal(classes="cards-container"):
            yield self._scout_card
            yield self._architect_card
            yield self._reviewer_card
        yield self._pool_strip
        yield self._dag

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self._scout_card.update_state(app_state)
        self._architect_card.update_state(app_state)
        self._reviewer_card.update_state(app_state)
        self._pool_strip.update_state(app_state)
        self._dag.update_run(app_state.pipeline_run)
