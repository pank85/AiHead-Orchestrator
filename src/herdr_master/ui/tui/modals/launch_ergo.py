"""Modal screen to launch a new Ergo Pipeline session."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from herdr_master.core.models import Squad


class ErgoLaunchParams(BaseModel):
    intent: str
    workspace_label: str
    customize_squad_first: bool = False


class LaunchErgoModal(ModalScreen[Optional[ErgoLaunchParams]]):
    """Modal to configure and launch a new Ergo cross-harness pipeline."""

    DEFAULT_CSS = """
    LaunchErgoModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    .modal-box {
        width: 76;
        height: auto;
        background: #181818;
        border: thick magenta;
        padding: 1 2;
    }
    .modal-title {
        color: magenta;
        text-style: bold;
        margin-bottom: 1;
    }
    .modal-desc {
        color: #888888;
        margin-bottom: 1;
    }
    .field-label {
        color: #aaaaaa;
        margin-top: 1;
    }
    .squad-summary {
        background: #121212;
        border: solid #333333;
        padding: 0 1;
        margin: 1 0;
    }
    .button-bar {
        margin-top: 2;
        align: center middle;
    }
    .button-bar Button {
        margin: 0 1;
    }
    """

    def __init__(self, squad: Squad, **kwargs) -> None:
        super().__init__(**kwargs)
        self.squad = squad
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        self.default_label = f"ergo-{ts}"
        self._intent_input = Input(
            placeholder="e.g. Implement distributed Redis cache for authentication service"
        )
        self._ws_input = Input(value=self.default_label, placeholder="Workspace label")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Static("⚡ Launch New Ergo Pipeline", classes="modal-title")
            yield Static(
                "Creates a dedicated workspace that orchestrates a new Ergo multi-role "
                "pipeline with separate Herdr tabs for each agent "
                "(Scout → Architect → Parallel Specialist Coders → Reviewer).",
                classes="modal-desc",
            )

            yield Label("Pipeline Intent / Feature Objective:", classes="field-label")
            yield self._intent_input

            yield Label("Dedicated Workspace Label:", classes="field-label")
            yield self._ws_input

            yield Label("Active Squad Summary:", classes="field-label")
            summary_text = (
                f"• Scout:     [cyan]{self.squad.scout.to_slug()}[/cyan]\n"
                f"• Architect: [cyan]{self.squad.architect.to_slug()}[/cyan]\n"
                f"• Pool:      [cyan]{len(self.squad.worker_pool)} worker models[/cyan]\n"
                f"• Reviewer:  [cyan]{self.squad.reviewer.to_slug()}[/cyan]"
            )
            yield Static(summary_text, classes="squad-summary")

            with Horizontal(classes="button-bar"):
                yield Button("Launch Pipeline", id="btn-launch", variant="success")
                yield Button("Customize Squad (c)", id="btn-customize", variant="primary")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-launch":
            intent = self._intent_input.value.strip()
            if not intent:
                intent = "Execute standard Ergo pipeline"
            ws_label = self._ws_input.value.strip() or self.default_label
            self.dismiss(
                ErgoLaunchParams(
                    intent=intent,
                    workspace_label=ws_label,
                    customize_squad_first=False,
                )
            )
        elif event.button.id == "btn-customize":
            intent = self._intent_input.value.strip() or "Execute standard Ergo pipeline"
            ws_label = self._ws_input.value.strip() or self.default_label
            self.dismiss(
                ErgoLaunchParams(
                    intent=intent,
                    workspace_label=ws_label,
                    customize_squad_first=True,
                )
            )
        elif event.button.id == "btn-cancel":
            self.dismiss(None)
