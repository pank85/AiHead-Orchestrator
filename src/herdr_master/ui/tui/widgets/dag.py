"""Task DAG widget rendering planned tasks and dependency edges."""

from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from herdr_master.core.models import PipelineRun
from herdr_master.ui.tui.theme import STATE_COLORS, STATE_ICONS


class TaskDAGWidget(Static):
    """Renders task dependency graph (DAG) and file ownership matrix."""

    DEFAULT_CSS = """
    TaskDAGWidget {
        height: auto;
        padding: 0 1;
        background: #141414;
        border: solid #333333;
    }
    """

    def __init__(self, run: PipelineRun | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.run = run

    def update_run(self, run: PipelineRun | None) -> None:
        self.run = run
        self.refresh_dag()

    def on_mount(self) -> None:
        self.refresh_dag()

    def refresh_dag(self) -> None:
        if not self.run or not self.run.tasks:
            self.update(Text("No active pipeline plan loaded.", style="dim italic"))
            return

        table = Table(
            title="[bold cyan]Ergo Pipeline Task DAG[/bold cyan]",
            title_justify="left",
            border_style="dim",
            expand=True,
            show_header=True,
            header_style="bold magenta",
        )
        table.add_column("Task ID", style="bold white", width=12)
        table.add_column("Status", width=16)
        table.add_column("Dependency", width=18)
        table.add_column("Assigned Specialist", width=28)
        table.add_column("Files", style="dim")

        for task in self.run.tasks:
            color = STATE_COLORS.get(task.status, "white")
            icon = STATE_ICONS.get(task.status, "•")
            status_text = Text(f"{icon} {task.status.value}", style=color)

            dep_style = "green" if task.dependency == "INDEPENDENT" else "yellow"
            dep_text = Text(task.dependency, style=dep_style)

            binding_str = task.binding.to_slug() if task.binding else "Pending assignment"
            persona = f" ({task.expert_persona})" if task.expert_persona else ""
            specialist_text = Text(f"{binding_str}{persona}")

            files_str = ", ".join(task.files[:3])
            if len(task.files) > 3:
                files_str += f" (+{len(task.files) - 3} more)"

            table.add_row(task.task_id, status_text, dep_text, specialist_text, files_str)

        self.update(table)
