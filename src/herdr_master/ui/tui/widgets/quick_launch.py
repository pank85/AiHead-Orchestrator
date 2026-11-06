"""Compact quick-launch toolbar for the primary orchestration actions."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Static


class QuickLaunchBar(Static):
    """Compact launch actions with short, visible descriptions."""

    DEFAULT_CSS = """
    QuickLaunchBar {
        height: 4;
        padding: 0;
    }
    QuickLaunchBar .launch-container {
        height: 4;
    }
    QuickLaunchBar .launch-action {
        width: 1fr;
        height: 4;
    }
    QuickLaunchBar .launch-description {
        height: 1;
        text-align: center;
        color: #aaaaaa;
    }
    QuickLaunchBar Button {
        width: 1fr;
        min-width: 0;
        margin: 0;
    }
    """

    class LaunchMasterRequested(Message):
        """User requested to launch a new Herdr Master."""

    class LaunchErgoRequested(Message):
        """User requested to launch a new Ergo Pipeline."""

    class LaunchOrchestrationRequested(Message):
        """User requested to launch a single-harness Orchestration pipeline."""

    class OpenSettingsRequested(Message):
        """User requested to configure squad/settings."""

    def compose(self) -> ComposeResult:
        with Horizontal(classes="launch-container"):
            with Vertical(classes="launch-action"):
                yield Button(
                    "New Master",
                    id="btn-new-master",
                    variant="primary",
                    tooltip="[n] Coordinate multiple independent jobs in isolated Git worktrees.",
                )
                yield Static("Coordinate ongoing jobs", classes="launch-description")
            with Vertical(classes="launch-action"):
                yield Button(
                    "New Ergo",
                    id="btn-new-ergo",
                    variant="success",
                    tooltip="[e] Run one job through Scout, Architect, Coders, and Reviewer.",
                )
                yield Static("Run one planned pipeline", classes="launch-description")
            with Vertical(classes="launch-action"):
                yield Button(
                    "Orchestrate",
                    id="btn-new-orchestration",
                    variant="success",
                    tooltip="[o] Run Scout -> Architect -> Coders -> Reviewer with subagents in a single harness.",
                )
                yield Static("Single-harness subagents", classes="launch-description")
            with Vertical(classes="launch-action"):
                yield Button(
                    "Squad Settings",
                    id="btn-settings",
                    variant="warning",
                    tooltip="[s/c] Configure harnesses, models, reasoning effort, and workers.",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new-master":
            self.post_message(self.LaunchMasterRequested())
        elif event.button.id == "btn-new-ergo":
            self.post_message(self.LaunchErgoRequested())
        elif event.button.id == "btn-new-orchestration":
            self.post_message(self.LaunchOrchestrationRequested())
        elif event.button.id == "btn-settings":
            self.post_message(self.OpenSettingsRequested())
