"""Confirmation modal dialog for destructive or landing operations."""

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen[bool]):
    """Modal dialog prompting the operator for confirmation."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    .confirm-dialog {
        width: 60;
        height: auto;
        background: #1e1e1e;
        border: thick cyan;
        padding: 1 2;
    }
    .confirm-title {
        text-align: center;
        text-style: bold;
        color: cyan;
        margin-bottom: 1;
    }
    .confirm-buttons {
        align: center middle;
        margin-top: 1;
    }
    .confirm-buttons Button {
        margin: 0 1;
    }
    """

    def __init__(self, title: str, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.title_text = title
        self.message_text = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="confirm-dialog"):
            yield Static(self.title_text, classes="confirm-title")
            yield Static(self.message_text)
            with Horizontal(classes="confirm-buttons"):
                yield Button("Confirm", id="btn-confirm", variant="primary")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)
