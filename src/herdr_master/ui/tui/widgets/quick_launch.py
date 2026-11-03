"""Quick launch bar with two prominent buttons: New Herdr Master and New Ergo Pipeline."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Static


class QuickLaunchBar(Static):
    """Prominent launch hub presenting the two primary orchestration paths."""

    DEFAULT_CSS = """
    QuickLaunchBar {
        height: auto;
        margin: 0 0 1 0;
        padding: 0;
    }
    .launch-container {
        height: auto;
    }
    .launch-card {
        width: 1fr;
        height: auto;
        margin: 0 1;
        padding: 0 1;
        background: #181818;
        border: solid #2a2a2a;
    }
    .launch-card:hover {
        border: solid cyan;
    }
    .launch-card-ergo {
        width: 1fr;
        height: auto;
        margin: 0 1;
        padding: 0 1;
        background: #181818;
        border: solid #2a2a2a;
    }
    .launch-card-ergo:hover {
        border: solid magenta;
    }
    .card-content {
        margin-bottom: 1;
    }
    .card-actions {
        align: center middle;
        margin-bottom: 0;
    }
    """

    class LaunchMasterRequested(Message):
        """User requested to launch a new Herdr Master."""

    class LaunchErgoRequested(Message):
        """User requested to launch a new Ergo Pipeline."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="launch-container"):
            # Card 1: New Herdr Master
            with Vertical(classes="launch-card"):
                content = Text()
                content.append("👑 New Herdr Master\n", style="bold cyan")
                content.append(
                    "A main herd coordinator that orchestrates multiple jobs across "
                    "isolated Git worktrees and delegates to worker tabs.\n",
                    style="dim",
                )
                yield Static(content, classes="card-content")
                with Horizontal(classes="card-actions"):
                    yield Button(
                        "👑 New Herdr Master",
                        id="btn-new-master",
                        variant="primary",
                    )

            # Card 2: New Ergo Pipeline
            with Vertical(classes="launch-card-ergo"):
                content = Text()
                content.append("⚡ New Ergo Pipeline\n", style="bold magenta")
                content.append(
                    "A new workspace that orchestrates a new Ergo pipeline with "
                    "multiple Herdr tabs, one for each agent.\n",
                    style="dim",
                )
                yield Static(content, classes="card-content")
                with Horizontal(classes="card-actions"):
                    yield Button(
                        "⚡ New Ergo Pipeline",
                        id="btn-new-ergo",
                        variant="success",
                    )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new-master":
            self.post_message(self.LaunchMasterRequested())
        elif event.button.id == "btn-new-ergo":
            self.post_message(self.LaunchErgoRequested())
