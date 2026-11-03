"""Prompt dialog modal for sending instructions to a worker or role."""

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class PromptDialog(ModalScreen[Optional[str]]):
    """Modal dialog allowing operator to prompt an active agent pane."""

    DEFAULT_CSS = """
    PromptDialog {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    .prompt-box {
        width: 70;
        height: auto;
        background: #1e1e1e;
        border: thick magenta;
        padding: 1 2;
    }
    .prompt-title {
        color: magenta;
        text-style: bold;
        margin-bottom: 1;
    }
    .prompt-buttons {
        margin-top: 1;
        align: right middle;
    }
    .prompt-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, target_name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.target_name = target_name
        self._input = Input(placeholder="Enter prompt instructions...")

    def compose(self) -> ComposeResult:
        with Vertical(classes="prompt-box"):
            yield Static(f"Prompt Agent: [bold]{self.target_name}[/bold]", classes="prompt-title")
            yield self._input
            with Horizontal(classes="prompt-buttons"):
                yield Button("Send Prompt", id="btn-send", variant="success")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-send":
            self.dismiss(self._input.value.strip())
        else:
            self.dismiss(None)
