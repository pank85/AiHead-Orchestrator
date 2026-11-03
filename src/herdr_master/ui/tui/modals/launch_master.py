"""Modal screen to launch a new Herdr Master session."""

from typing import Optional

from pydantic import BaseModel
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from herdr_master.core.models import HarnessKind


class MasterLaunchParams(BaseModel):
    name: str
    harness: HarnessKind
    model: str
    initial_task: Optional[str] = None


class LaunchMasterModal(ModalScreen[Optional[MasterLaunchParams]]):
    """Modal to configure and launch a new Herdr Master orchestrator."""

    DEFAULT_CSS = """
    LaunchMasterModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.7);
    }
    .modal-box {
        width: 70;
        height: auto;
        background: #181818;
        border: thick cyan;
        padding: 1 2;
    }
    .modal-title {
        color: cyan;
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
    .button-bar {
        margin-top: 2;
        align: center middle;
    }
    .button-bar Button {
        margin: 0 1;
    }
    """

    HARNESS_OPTIONS = [(k.value.upper(), k.value) for k in HarnessKind]

    def __init__(self, default_name: str = "master-coordinator", **kwargs) -> None:
        super().__init__(**kwargs)
        self.default_name = default_name
        self._name_input = Input(value=default_name, placeholder="e.g. master-coordinator")
        self._harness_select = Select(self.HARNESS_OPTIONS, value="agy", allow_blank=False)
        self._model_input = Input(
            value="gemini-3.8-flash-high", placeholder="e.g. gemini-3.8-flash-high, xai/grok-4.7"
        )
        self._task_input = Input(placeholder="Optional initial task to dispatch immediately")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            yield Static("👑 Launch New Herdr Master — Multiple Jobs", classes="modal-title")
            yield Static(
                "Starts a dedicated Master Orchestrator session in a Herdr tab. "
                "The Master coordinates multiple independent jobs, isolates each job in its own "
                "Git worktree, spawns worker tabs, and lands them serially.",
                classes="modal-desc",
            )

            yield Label("Master Agent Name:", classes="field-label")
            yield self._name_input

            yield Label("Harness Kind:", classes="field-label")
            yield self._harness_select

            yield Label("Model Slug:", classes="field-label")
            yield self._model_input

            yield Label("Initial Task / Job (optional):", classes="field-label")
            yield self._task_input

            with Horizontal(classes="button-bar"):
                yield Button("Launch Master", id="btn-launch", variant="primary")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select == self._harness_select:
            val = str(event.value)
            if val == "pi":
                self._model_input.value = "xai/grok-4.7"
            elif val == "claude":
                self._model_input.value = "claude-3-7-sonnet"
            elif val == "opencode":
                self._model_input.value = "grok-4.7"
            else:
                self._model_input.value = "gemini-3.8-flash-high"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-launch":
            name = self._name_input.value.strip() or self.default_name
            harness = HarnessKind(self._harness_select.value)
            model = self._model_input.value.strip() or "gemini-3.8-flash-high"
            task = self._task_input.value.strip() or None
            self.dismiss(
                MasterLaunchParams(
                    name=name,
                    harness=harness,
                    model=model,
                    initial_task=task,
                )
            )
        elif event.button.id == "btn-cancel":
            self.dismiss(None)
