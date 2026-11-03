"""Configuration drawer modal for editing role bindings, model slugs, and worker pool."""

from typing import Optional, Tuple

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static

from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.pipeline.squad import (
    save_squad_as_default,
    validate_squad,
)


class ConfigDrawer(ModalScreen[Optional[Tuple[Squad, bool]]]):
    """Modal drawer for configuring roles, harness bindings, effort levels, and worker pool."""

    DEFAULT_CSS = """
    ConfigDrawer {
        align: right top;
        background: rgba(0, 0, 0, 0.6);
    }
    .drawer-panel {
        width: 65;
        height: 100%;
        background: #181818;
        border-left: thick cyan;
        padding: 1 2;
    }
    .drawer-title {
        color: cyan;
        text-style: bold;
        margin-bottom: 1;
    }
    .field-row {
        margin: 1 0;
    }
    .field-label {
        color: #aaaaaa;
        margin-bottom: 0;
    }
    .pool-list {
        height: 8;
        margin: 1 0;
        background: #121212;
        border: solid #333333;
    }
    .button-bar {
        margin-top: 1;
        align: center middle;
    }
    .button-bar Button {
        margin: 0 1;
    }
    .error-label {
        color: red;
        margin: 1 0;
    }
    """

    HARNESS_OPTIONS = [(k.value.upper(), k.value) for k in HarnessKind]
    EFFORT_OPTIONS = [("None", "none")] + [(e.value.capitalize(), e.value) for e in EffortLevel]

    def __init__(self, squad: Squad, target: Optional[str] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.squad = squad.model_copy(deep=True)
        self.target = target or "scout"

        role_choices = [
            ("Scout", "scout"),
            ("Architect", "architect"),
            ("Reviewer", "reviewer"),
            ("Worker Pool", "pool"),
        ]
        self._role_select = Select(
            role_choices,
            value="pool" if "pool" in self.target or "coder" in self.target else self.target,
            allow_blank=False,
        )
        self._harness_select = Select(self.HARNESS_OPTIONS, value="agy", allow_blank=False)
        self._model_input = Input(placeholder="Model slug, e.g. gemini-4-argon-high")
        self._effort_select = Select(self.EFFORT_OPTIONS, value="none", allow_blank=False)
        self._label_input = Input(placeholder="Optional capability label")
        self._error_label = Static("", classes="error-label")
        self._pool_option_list = OptionList(classes="pool-list")
        self._selected_pool_index: int = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="drawer-panel"):
            yield Static("⚙️ Squad & Model Configuration", classes="drawer-title")

            yield Label("Target Role / Section:", classes="field-label")
            yield self._role_select

            with Vertical(id="pool-mgmt-container"):
                yield Label("Worker Pool Entries:", classes="field-label")
                yield self._pool_option_list
                with Horizontal():
                    yield Button("Add", id="btn-add-worker", variant="success")
                    yield Button("Remove", id="btn-remove-worker", variant="error")
                    yield Button("Up", id="btn-up-worker")
                    yield Button("Down", id="btn-down-worker")

            yield Label("Harness Kind:", classes="field-label")
            yield self._harness_select

            yield Label("Model Slug:", classes="field-label")
            yield self._model_input

            yield Label("Effort Level:", classes="field-label")
            yield self._effort_select

            yield Label("Capability Label:", classes="field-label")
            yield self._label_input

            yield Button("Apply Changes", id="btn-apply-binding", variant="primary")
            yield self._error_label

            with Horizontal(classes="button-bar"):
                yield Button("Save Run", id="btn-save-run", variant="success")
                yield Button("Save Default", id="btn-save-default", variant="warning")
                yield Button("Cancel", id="btn-cancel", variant="default")

    def on_mount(self) -> None:
        self._refresh_pool_list()
        self._load_current_target_fields()

    def _refresh_pool_list(self) -> None:
        self._pool_option_list.clear_options()
        for idx, w in enumerate(self.squad.worker_pool):
            lbl = w.label or f"worker_{idx}"
            self._pool_option_list.add_option(f"[{idx + 1}] {w.to_slug()} ({lbl})")
        if self.squad.worker_pool:
            idx = min(self._selected_pool_index, len(self.squad.worker_pool) - 1)
            self._pool_option_list.highlighted = idx

    def _load_current_target_fields(self) -> None:
        mode = self._role_select.value
        pool_container = self.query_one("#pool-mgmt-container")

        if mode == "scout":
            pool_container.display = False
            self._populate_fields(self.squad.scout)
        elif mode == "architect":
            pool_container.display = False
            self._populate_fields(self.squad.architect)
        elif mode == "reviewer":
            pool_container.display = False
            self._populate_fields(self.squad.reviewer)
        else:  # pool
            pool_container.display = True
            if self.squad.worker_pool and self._selected_pool_index < len(self.squad.worker_pool):
                self._populate_fields(self.squad.worker_pool[self._selected_pool_index])

    def _populate_fields(self, binding: HarnessBinding) -> None:
        self._harness_select.value = binding.kind.value
        self._model_input.value = binding.model
        self._effort_select.value = binding.effort.value if binding.effort else "none"
        self._label_input.value = binding.label or ""

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select == self._role_select:
            self._load_current_target_fields()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._selected_pool_index = event.option_index
        self._load_current_target_fields()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id

        if bid == "btn-apply-binding":
            self._apply_current_fields()
        elif bid == "btn-add-worker":
            new_worker = HarnessBinding.from_slug(
                "agy:gemini-3.8-flash-high", label="new_worker", order=len(self.squad.worker_pool)
            )
            self.squad.worker_pool.append(new_worker)
            self._selected_pool_index = len(self.squad.worker_pool) - 1
            self._refresh_pool_list()
            self._load_current_target_fields()
        elif bid == "btn-remove-worker":
            pool_len = len(self.squad.worker_pool)
            if pool_len > 1 and self._selected_pool_index < pool_len:
                self.squad.worker_pool.pop(self._selected_pool_index)
                self._selected_pool_index = max(0, self._selected_pool_index - 1)
                self._refresh_pool_list()
                self._load_current_target_fields()
        elif bid == "btn-up-worker":
            idx = self._selected_pool_index
            if idx > 0:
                self.squad.worker_pool[idx - 1], self.squad.worker_pool[idx] = (
                    self.squad.worker_pool[idx],
                    self.squad.worker_pool[idx - 1],
                )
                self._selected_pool_index -= 1
                self._refresh_pool_list()
        elif bid == "btn-down-worker":
            idx = self._selected_pool_index
            if idx < len(self.squad.worker_pool) - 1:
                self.squad.worker_pool[idx + 1], self.squad.worker_pool[idx] = (
                    self.squad.worker_pool[idx],
                    self.squad.worker_pool[idx + 1],
                )
                self._selected_pool_index += 1
                self._refresh_pool_list()
        elif bid == "btn-save-run":
            self._save_and_dismiss(is_default=False)
        elif bid == "btn-save-default":
            self._save_and_dismiss(is_default=True)
        elif bid == "btn-cancel":
            self.dismiss(None)

    def _apply_current_fields(self) -> None:
        try:
            kind = HarnessKind(str(self._harness_select.value))
            model = self._model_input.value.strip()
            if not model:
                self._error_label.update("[red]Model slug cannot be empty[/red]")
                return

            effort_val = str(self._effort_select.value)
            effort = EffortLevel(effort_val) if effort_val != "none" else None
            label = self._label_input.value.strip() or None

            updated = HarnessBinding(
                kind=kind,
                model=model,
                effort=effort,
                label=label,
            )

            mode = self._role_select.value
            if mode == "scout":
                self.squad.scout = updated
            elif mode == "architect":
                self.squad.architect = updated
            elif mode == "reviewer":
                self.squad.reviewer = updated
            else:  # pool
                pool = self.squad.worker_pool
                if pool and self._selected_pool_index < len(pool):
                    updated.order = self._selected_pool_index
                    self.squad.worker_pool[self._selected_pool_index] = updated
                    self._refresh_pool_list()

            self._error_label.update("[green]Applied successfully[/green]")
        except Exception as e:
            self._error_label.update(f"[red]Error: {e}[/red]")

    def _save_and_dismiss(self, is_default: bool) -> None:
        self._apply_current_fields()
        errors = validate_squad(self.squad)
        if errors:
            self._error_label.update(f"[red]{errors[0]}[/red]")
            return

        if is_default:
            try:
                save_squad_as_default(self.squad)
            except Exception as e:
                self._error_label.update(f"[red]Failed to save default: {e}[/red]")
                return

        self.dismiss((self.squad, is_default))
