"""Modal screen for single-harness orchestration launching."""

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from pydantic import BaseModel
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, Static

from herdr_master.core.config import load_project_config
from herdr_master.core.models import HarnessKind
from herdr_master.core.slug import parse_binding
from herdr_master.ui.tui.modals.project_folder import ProjectFolderField, initial_project_folder
from herdr_master.ui.tui.state import harness_select_options


DEFAULT_MODELS: Dict[str, Dict[str, str]] = {
    "agy": {
        "scout": "flash_lite",
        "architect": "pro",
        "coder": "flash",
        "reviewer": "pro",
    },
    "claude": {
        "scout": "claude-sonnet-4-6",
        "architect": "claude-opus-4-6",
        "coder": "claude-sonnet-4-6",
        "reviewer": "claude-opus-4-6",
    },
    "pi": {
        "scout": "anthropic/claude-3-5-sonnet",
        "architect": "openai-codex/gpt-6.1-sol",
        "coder": "anthropic/claude-3-5-sonnet",
        "reviewer": "xai/grok-4.7",
    },
    "opencode": {
        "scout": "general",
        "architect": "general",
        "coder": "general",
        "reviewer": "general",
    },
}


@contextmanager
def _ensure_active_app():
    """Ensure an active Textual app context is present.

    Textual's reactive watcher on Input selection calls `self.app.clear_selection()`.
    When instantiated in headless scripts or unit tests without an active app or
    with an app lacking a mounted screen, this context manager installs a safe dummy
    App to prevent NoActiveAppError or ScreenStackError.
    """
    from textual._context import active_app

    try:
        current = active_app.get()
        try:
            _ = current.screen
            yield
            return
        except Exception:
            pass
    except LookupError:
        pass

    class _HeadlessApp(App):
        def clear_selection(self) -> None:
            pass

    dummy = _HeadlessApp()
    token = active_app.set(dummy)
    try:
        yield
    finally:
        active_app.reset(token)


class OrchestrationLaunchParams(BaseModel):
    """Parameters for launching a single-harness orchestration pipeline."""

    intent: str
    workspace_label: str
    harness: HarnessKind
    scout_model: str
    architect_model: str
    coder_model: str
    reviewer_model: str
    dangerously_skip_permissions: bool = True
    project_path: Optional[Path] = None


class LaunchOrchestrationModal(ModalScreen[Optional[OrchestrationLaunchParams]]):
    """Modal screen for configuring and launching a single-harness orchestration run.

    Provides directory selection, single native harness selection (agy, pi, claude, opencode),
    squad model inputs for Scout, Architect, Coder, and Reviewer with per-harness defaults,
    and a toggle for permission skipping.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    DEFAULT_CSS = """
    LaunchOrchestrationModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    .modal-box {
        width: 76;
        max-width: 95%;
        height: 100%;
        max-height: 100%;
        background: #181818;
        border: thick #00aa66;
        padding: 0 1;
    }
    .modal-fields {
        height: 1fr;
    }
    .modal-title {
        color: #00dd88;
        text-style: bold;
        margin-bottom: 0;
    }
    .modal-desc {
        color: #888888;
        margin-bottom: 1;
    }
    .field-label {
        color: #aaaaaa;
        margin-top: 1;
        height: 1;
    }
    .model-row {
        height: auto;
        margin-top: 1;
    }
    .model-col {
        width: 1fr;
        height: auto;
    }
    .model-col-right {
        margin-left: 1;
    }
    .model-col .field-label {
        margin-top: 0;
    }
    #orch-skip-permissions {
        margin-top: 1;
        margin-bottom: 1;
        height: auto;
    }
    .status-msg {
        height: 2;
        max-height: 2;
        color: #ff6666;
    }
    .button-bar {
        height: 3;
        align: center middle;
    }
    .button-bar Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        *,
        project_path: Optional[Path] = None,
        config_error: Optional[str] = None,
        default_intent: str = "",
        default_harness: Optional[Union[HarnessKind, str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project_path = initial_project_folder(project_path)
        self.config_error = config_error
        self._submitting = False
        self.status_text = ""
        self._syncing = False
        self._programmatic_models: Dict[str, str] = {}

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.default_label = f"orch-{ts}"

        with _ensure_active_app():
            self._project_folder = ProjectFolderField(self.project_path)
            self._project_folder.tooltip = (
                "Project root directory for the orchestration pipeline."
            )

            self._intent_input = Input(
                value=default_intent,
                placeholder="Task or goal for single-harness pipeline…",
                id="orch-intent",
            )
            self._workspace_label_input = Input(
                value=self.default_label,
                placeholder=self.default_label,
                id="orch-workspace-label",
            )

            options = harness_select_options(mode="native")
            initial_harness = (
                str(
                    default_harness.value
                    if isinstance(default_harness, HarnessKind)
                    else default_harness
                )
                if default_harness
                else "agy"
            )
            option_values = {val for _, val in options}
            if initial_harness not in option_values and options:
                initial_harness = options[0][1]

            self._harness_select = Select(
                options,
                value=initial_harness,
                allow_blank=False,
                id="orch-harness",
            )

            self._scout_input = Input(id="orch-scout-model", placeholder="e.g. flash_lite")
            self._architect_input = Input(id="orch-architect-model", placeholder="e.g. pro")
            self._coder_input = Input(
                id="orch-coder-model",
                placeholder="e.g. flash (or flash|pro for multiple)",
            )
            self._reviewer_input = Input(id="orch-reviewer-model", placeholder="e.g. pro")

            self._skip_permissions_checkbox = Checkbox(
                "Dangerously skip permissions (--dangerously-skip-permissions)",
                value=True,
                id="orch-skip-permissions",
            )

            self._status_label = Static("", id="orch-status", classes="status-msg")
            self._apply_harness_defaults(initial_harness, force=True)

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-box"):
            with VerticalScroll(classes="modal-fields"):
                yield Static("⚡ Launch Single-Harness Orchestration", classes="modal-title")
                yield Static(
                    "Self-contained subagent pipeline in a single harness. "
                    "Scout -> Architect -> Coders -> Reviewer.",
                    classes="modal-desc",
                )

                yield self._project_folder

                yield Label("Intent / Goal:", classes="field-label")
                yield self._intent_input

                yield Label("Workspace Label:", classes="field-label")
                yield self._workspace_label_input

                yield Label("Native Harness:", classes="field-label")
                yield self._harness_select

                with Horizontal(classes="model-row"):
                    with Vertical(classes="model-col"):
                        yield Label("Scout Model:", classes="field-label")
                        yield self._scout_input
                    with Vertical(classes="model-col model-col-right"):
                        yield Label("Architect Model:", classes="field-label")
                        yield self._architect_input

                with Horizontal(classes="model-row"):
                    with Vertical(classes="model-col"):
                        yield Label("Coder Model(s):", classes="field-label")
                        yield self._coder_input
                    with Vertical(classes="model-col model-col-right"):
                        yield Label("Reviewer Model:", classes="field-label")
                        yield self._reviewer_input

                yield self._skip_permissions_checkbox

            yield self._status_label
            with Horizontal(classes="button-bar"):
                yield Button("Launch Orchestration", id="btn-launch", variant="success")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def on_mount(self) -> None:
        if self.config_error:
            self._set_status(self.config_error)

    def _set_status(self, text: str) -> None:
        self.status_text = text
        self._status_label.update(text)

    def _get_project_model(self, harness: str, role: str) -> Optional[str]:
        try:
            path = self._project_folder.selected_path()
            cfg = load_project_config(path)
            if cfg.models and cfg.models.default_squad:
                slug = cfg.models.default_squad.get(role)
                if slug:
                    b = parse_binding(slug)
                    if b.kind.value == harness:
                        return b.model
        except Exception:
            pass
        return None

    def _get_default_model(self, harness: str, role: str) -> str:
        project_model = self._get_project_model(harness, role)
        if project_model:
            return project_model
        return DEFAULT_MODELS.get(harness, {}).get(role, "general")

    def _apply_harness_defaults(self, harness: str, force: bool = False) -> None:
        with _ensure_active_app():
            self._syncing = True
            try:
                for role in ("scout", "architect", "coder", "reviewer"):
                    widget = getattr(self, f"_{role}_input")
                    new_model = self._get_default_model(harness, role)
                    prev_default = self._programmatic_models.get(role, "")
                    current_val = widget.value.strip()
                    if force or current_val == prev_default or not current_val:
                        with self.prevent(Input.Changed):
                            widget.value = new_model
                        self._programmatic_models[role] = new_model
            finally:
                self._syncing = False

    def on_project_folder_field_changed(self, event: ProjectFolderField.Changed) -> None:
        event.stop()
        self.config_error = None
        self._set_status("")
        try:
            self.project_path = self._project_folder.selected_path()
            harness = str(self._harness_select.value)
            self._apply_harness_defaults(harness, force=False)
        except (ValueError, OSError) as exc:
            self._set_status(str(exc))

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._syncing:
            return
        if event.select == self._harness_select:
            harness = str(event.value)
            self._apply_harness_defaults(harness, force=False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
            return
        if event.button.id != "btn-launch" or self._submitting:
            return

        intent = self._intent_input.value.strip()
        if not intent:
            self._set_status("Intent is required.")
            return

        try:
            project_path = self._project_folder.selected_path()
        except (ValueError, OSError) as exc:
            self._set_status(f"Invalid project folder: {exc}")
            return

        label = self._workspace_label_input.value.strip() or self.default_label

        harness_val = str(self._harness_select.value)
        try:
            harness_kind = HarnessKind(harness_val)
        except ValueError:
            self._set_status(f"Unsupported harness: {harness_val}")
            return

        scout_m = self._scout_input.value.strip()
        arch_m = self._architect_input.value.strip()
        coder_m = self._coder_input.value.strip()
        rev_m = self._reviewer_input.value.strip()

        if not scout_m or not arch_m or not coder_m or not rev_m:
            self._set_status("All models (Scout, Architect, Coder, Reviewer) must be specified.")
            return

        skip_perms = bool(self._skip_permissions_checkbox.value)

        self._submitting = True
        params = OrchestrationLaunchParams(
            intent=intent,
            workspace_label=label,
            harness=harness_kind,
            scout_model=scout_m,
            architect_model=arch_m,
            coder_model=coder_m,
            reviewer_model=rev_m,
            dangerously_skip_permissions=skip_perms,
            project_path=project_path,
        )
        self.dismiss(params)

    def action_cancel(self) -> None:
        self.dismiss(None)
