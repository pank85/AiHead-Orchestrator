"""Modal screen for Squad configuration and Ergo Pipeline launching."""

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Union

from pydantic import BaseModel
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Tree

from herdr_master.core.catalog import CatalogError, worker_route_note
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.pipeline.squad import save_squad_as_default, validate_squad
from herdr_master.ui.tui.state import (
    binding_launch_preview,
    catalog_worker_template,
    effort_select_options,
    harness_select_options,
    normalize_worker_order,
    project_config_file,
    scout_summary,
    squad_source_line,
    validate_binding_for_editor,
)


class ErgoLaunchParams(BaseModel):
    intent: str
    workspace_label: str
    squad: Optional[Squad] = None
    start_pipeline: bool = True
    customize_squad_first: bool = False


class LaunchErgoModal(ModalScreen[Optional[ErgoLaunchParams]]):
    """2-column Squad Settings & Ergo Pipeline Launcher.

    The tree root and pool overview are read-only. Worker effort stays unset.
    Cancellation discards the deep copy and does not write app state.
    """

    DEFAULT_CSS = """
    LaunchErgoModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    .modal-shell {
        width: 96%;
        height: 100%;
        max-height: 100%;
        background: #141414;
        border: thick magenta;
        padding: 0 1;
    }
    .modal-header {
        height: 2;
        max-height: 2;
    }
    .modal-title {
        color: magenta;
        text-style: bold;
    }
    .modal-subtitle {
        color: #888888;
    }
    .top-section {
        height: 1fr;
        border: solid #2a2a2a;
    }
    .left-tree-col {
        width: 36;
        height: 100%;
        border-right: solid #333333;
        background: #161616;
    }
    .col-header {
        color: #aaaaaa;
        background: #1f1f1f;
        text-style: bold;
    }
    .squad-tree {
        height: 1fr;
        background: #161616;
    }
    .right-editor-col {
        width: 1fr;
        height: 100%;
        padding: 0 1;
        background: #181818;
    }
    .editor-fields {
        height: 1fr;
    }
    .editor-heading {
        color: cyan;
        text-style: bold;
    }
    .field-label {
        color: #aaaaaa;
    }
    .worker-pool-buttons {
        height: auto;
    }
    .worker-pool-buttons Button {
        margin-right: 1;
    }
    .status-msg {
        height: 1;
        max-height: 2;
    }
    .bottom-section {
        height: 8;
        min-height: 8;
        max-height: 8;
        border: solid #2a2a2a;
        background: #181818;
        padding: 0 1;
    }
    .prompt-row {
        height: 3;
    }
    .prompt-col {
        width: 1fr;
    }
    .ws-col {
        width: 28;
        margin-left: 1;
    }
    .action-button-bar {
        height: 3;
        align: center middle;
    }
    .action-button-bar Button {
        margin: 0 1;
    }
    """

    def __init__(
        self,
        squad: Squad,
        *,
        sources: Optional[dict] = None,
        mode: str = "herdr",
        native_harness: Optional[str] = None,
        project_path: Optional[Path] = None,
        config_error: Optional[str] = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.squad = squad.model_copy(deep=True)
        self.sources = dict(sources or {})
        self.mode = mode
        self.native_harness = native_harness
        self.project_path = project_path
        self.config_error = config_error
        self._submitting = False
        self.status_text = ""
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        self.default_label = f"ergo-{ts}"

        self._selected_target: Union[str, Tuple[str, int]] = "scout"
        self._is_updating_fields = False
        self._rebuilding_tree = False

        scout = self.squad.scout
        effort_value = scout.effort.value if scout.effort else "none"
        effort_options = effort_select_options(scout.kind.value, scout.model, mode=mode)
        if effort_value not in {item[1] for item in effort_options}:
            effort_value = "none"
        template = catalog_worker_template(0)

        self._summary = Static("", id="squad-summary", classes="modal-subtitle")
        self._editor_heading = Label("Scout Configuration", classes="editor-heading")
        self._harness_select = Select(
            harness_select_options(mode=mode),
            value=scout.kind.value,
            allow_blank=False,
        )
        self._model_input = Input(
            value=scout.model,
            placeholder="Explicit model. Blank input is rejected.",
        )
        self._effort_label = Label("Reasoning Effort:", classes="field-label")
        self._effort_select = Select(effort_options, value=effort_value, allow_blank=False)
        self._effort_note = Label("", classes="field-label")
        self._role_label_header = Label("Role / Worker Name:", classes="field-label")
        self._label_input = Input(value=scout.label or "", placeholder="Optional worker label")
        self._desc_label = Label("Capability Info (for Architect):", classes="field-label")
        self._description_input = Input(
            value="",
            placeholder=template.description or "Capability note for the Architect",
        )
        self._status_label = Static("", id="squad-status", classes="status-msg")
        self._preview = Static("", id="launch-preview", classes="status-msg")

        self._intent_input = Input(
            placeholder="e.g. Implement distributed Redis cache for authentication service"
        )
        self._ws_input = Input(value=self.default_label, placeholder="Workspace label")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-shell"):
            with Vertical(classes="modal-header"):
                yield Static("⚙️ Squad Settings & Ergo Pipeline Launcher", classes="modal-title")
                yield self._summary

            with Horizontal(classes="top-section"):
                with Vertical(classes="left-tree-col"):
                    yield Label("Squad Topology:", classes="col-header")
                    yield Tree("👑 Herdr-Master Root", id="squad-tree", classes="squad-tree")

                with Vertical(classes="right-editor-col"):
                    with VerticalScroll(classes="editor-fields"):
                        yield self._editor_heading

                        yield Label("Harness Kind:", classes="field-label")
                        yield self._harness_select

                        yield Label("Model:", classes="field-label")
                        yield self._model_input

                        yield self._effort_label
                        yield self._effort_select
                        yield self._effort_note

                        yield self._role_label_header
                        yield self._label_input

                        yield self._desc_label
                        yield self._description_input

                    with Horizontal(id="worker-pool-bar", classes="worker-pool-buttons"):
                        yield Button("+ Add Worker", id="btn-add-worker", variant="success")
                        yield Button("- Remove Worker", id="btn-remove-worker", variant="error")
                        yield Button("↑ Up", id="btn-up-worker")
                        yield Button("↓ Down", id="btn-down-worker")

                    yield self._status_label

            with Vertical(classes="bottom-section"):
                yield self._preview
                with Horizontal(classes="prompt-row"):
                    with Vertical(classes="prompt-col"):
                        yield Label("⚡ Ergo Feature Objective / Prompt:", classes="field-label")
                        yield self._intent_input
                    with Vertical(classes="ws-col"):
                        yield Label("Dedicated Workspace Label:", classes="field-label")
                        yield self._ws_input

                with Horizontal(classes="action-button-bar"):
                    yield Button("⚡ Start Ergo Pipeline", id="btn-start-ergo", variant="success")
                    yield Button("💾 Save Default", id="btn-save-default", variant="warning")
                    yield Button("Cancel", id="btn-cancel", variant="default")

    def on_mount(self) -> None:
        self._rebuild_tree()
        self._load_agent_into_editor("scout")
        self._refresh_summary()

    def _set_status(self, text: str) -> None:
        self.status_text = text
        self._status_label.update(text)

    def _editor_locked(self) -> bool:
        return self._selected_target in ("root", "pool_overview")

    def _set_editor_enabled(self, enabled: bool) -> None:
        self._harness_select.disabled = not enabled
        self._model_input.disabled = not enabled
        self._effort_select.disabled = not enabled
        self._label_input.disabled = not enabled
        self._description_input.disabled = not enabled

    def _refresh_summary(self) -> None:
        if self.config_error:
            self._summary.update(self.config_error)
        else:
            self._summary.update(
                f"{squad_source_line(self.sources, self.mode)} · {scout_summary(self.squad)}"
            )
        self._preview.update(
            binding_launch_preview(
                self.squad.scout,
                role="scout",
                mode=self.mode,
                native_harness=self.native_harness,
            )
        )

    def _refresh_worker_note(self, binding: HarnessBinding) -> None:
        try:
            note = worker_route_note(
                binding.kind.value,
                binding.model,
                mode=self.mode,
                native_harness=self.native_harness,
            )
        except CatalogError as exc:
            note = str(exc)
        self._effort_note.update("Reasoning effort is chosen per task by the Architect. " + note)

    def _set_effort_options(self, kind: str, model: str, current: Optional[str]) -> None:
        options = effort_select_options(kind, model, mode=self.mode)
        values = {item[1] for item in options}
        selected = current if current in values else "none"
        if selected not in values:
            selected = options[0][1]
        self._effort_select.set_options(options)
        self._effort_select.value = selected

    def _rebuild_tree(self) -> None:
        """Reconstruct the interactive squad tree on the left."""
        tree = self.query_one("#squad-tree", Tree)
        self._rebuilding_tree = True
        try:
            tree.clear()
            tree.root.label = "👑 [bold cyan]Herdr-Master Root[/bold cyan]"
            tree.root.data = {"type": "root"}

            scout_sub = f"{self.squad.scout.kind.value}:{self.squad.scout.model}"
            tree.root.add_leaf(
                f"● [bold]Scout[/bold] [dim]({scout_sub})[/dim]",
                data={"type": "role", "role": "scout"},
            )

            arch_sub = f"{self.squad.architect.kind.value}:{self.squad.architect.model}"
            tree.root.add_leaf(
                f"● [bold]Architect[/bold] [dim]({arch_sub})[/dim]",
                data={"type": "role", "role": "architect"},
            )

            pool_node = tree.root.add(
                f"📦 [bold]Worker Pool[/bold] [dim]({len(self.squad.worker_pool)} coders)[/dim]",
                data={"type": "pool"},
            )
            pool_node.expand()

            for idx, worker in enumerate(self.squad.worker_pool):
                w_sub = f"{worker.kind.value}:{worker.model}"
                w_name = worker.label or f"worker-{idx + 1}"
                state = "" if worker.enabled else " [red]disabled[/red]"
                desc_text = (
                    f" • [italic dim]{worker.description[:26]}...[/italic dim]"
                    if worker.description
                    else ""
                )
                pool_node.add_leaf(
                    f"↳ [bold]{w_name}[/bold] [dim]({w_sub})[/dim]{state}{desc_text}",
                    data={"type": "worker", "index": idx},
                )

            rev_sub = f"{self.squad.reviewer.kind.value}:{self.squad.reviewer.model}"
            tree.root.add_leaf(
                f"● [bold]Reviewer[/bold] [dim]({rev_sub})[/dim]",
                data={"type": "role", "role": "reviewer"},
            )
            tree.root.expand()
        finally:
            self._rebuilding_tree = False

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if self._rebuilding_tree or self._is_updating_fields:
            return
        data = event.node.data or {}
        node_type = data.get("type", "root")
        if node_type == "role":
            self._load_agent_into_editor(data.get("role", "scout"))
        elif node_type == "worker":
            self._load_agent_into_editor(("worker", data.get("index", 0)))
        elif node_type == "pool":
            self._load_agent_into_editor("pool_overview")
        else:
            self._load_agent_into_editor("root")

    def _load_agent_into_editor(self, target: Union[str, Tuple[str, int]]) -> None:
        """Populate the editor. Root and pool overview stay read-only."""
        self._selected_target = target
        self._is_updating_fields = True
        pool_bar = self.query_one("#worker-pool-bar", Horizontal)
        try:
            with self.prevent(Select.Changed), self.prevent(Input.Changed):
                if target in ("root", "pool_overview"):
                    self._set_editor_enabled(False)
                    self._effort_label.display = False
                    self._effort_select.display = False
                    self._effort_note.display = True
                    self._desc_label.display = False
                    self._description_input.display = False
                    pool_bar.display = target == "pool_overview"
                    if target == "root":
                        self._editor_heading.update("👑 Herdr-Master Squad Overview (read-only)")
                        self._effort_note.update(
                            "Root overview does not edit Scout. Select a role to change it."
                        )
                    else:
                        self._editor_heading.update("📦 Worker Pool Overview (read-only)")
                        self._effort_note.update(
                            "Pool overview does not edit Scout or a worker. Select a worker."
                        )
                    return

                self._set_editor_enabled(True)
                if target in ("scout", "architect", "reviewer"):
                    self._effort_label.display = True
                    self._effort_select.display = True
                    self._effort_note.display = False
                    self._desc_label.display = False
                    self._description_input.display = False
                    self._role_label_header.update("Role Identifier:")
                    pool_bar.display = False
                    binding = getattr(self.squad, target)
                    titles = {
                        "scout": "Scout Role Configuration",
                        "architect": "Architect Role Configuration",
                        "reviewer": "Reviewer Role Configuration",
                    }
                    self._editor_heading.update(titles[target])
                    self._populate_fields(binding)
                    return

                self._effort_label.display = False
                self._effort_select.display = False
                self._effort_note.display = True
                self._desc_label.display = True
                self._description_input.display = True
                self._role_label_header.update("Worker Identifier / Name:")
                pool_bar.display = True
                if isinstance(target, tuple) and target[0] == "worker":
                    idx = target[1]
                    if 0 <= idx < len(self.squad.worker_pool):
                        worker = self.squad.worker_pool[idx]
                        w_name = worker.label or f"worker-{idx + 1}"
                        self._editor_heading.update(f"Worker Pool: {w_name} (Position #{idx + 1})")
                        self._populate_fields(worker)
                        self._refresh_worker_note(worker)
        finally:
            self._is_updating_fields = False
            self._refresh_summary()

    def _populate_fields(self, binding: HarnessBinding) -> None:
        self._harness_select.value = binding.kind.value
        self._model_input.value = binding.model
        current = binding.effort.value if binding.effort else "none"
        self._set_effort_options(binding.kind.value, binding.model, current)
        self._label_input.value = binding.label or ""
        self._description_input.value = binding.description or ""

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._is_updating_fields or self._editor_locked():
            return
        self._apply_current_editor_changes()

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._is_updating_fields or self._editor_locked():
            return
        self._apply_current_editor_changes()

    def _parse_editor(self) -> Tuple[Optional[HarnessBinding], Optional[str]]:
        if self._editor_locked():
            return None, None
        model = self._model_input.value.strip()
        if not model:
            return None, "Model is empty. Refusing to invent a model."
        try:
            kind = HarnessKind(str(self._harness_select.value))
        except ValueError:
            return None, "Unknown harness. Refusing to substitute agy."
        label = self._label_input.value.strip() or None
        description = self._description_input.value.strip() or None
        if isinstance(self._selected_target, tuple):
            idx = self._selected_target[1]
            existing = (
                self.squad.worker_pool[idx] if 0 <= idx < len(self.squad.worker_pool) else None
            )
            binding = HarnessBinding(
                kind=kind,
                model=model,
                effort=None,
                label=label,
                description=description,
                enabled=existing.enabled if existing else True,
                order=existing.order if existing else idx,
            )
            return binding, None
        effort_text = str(self._effort_select.value)
        effort = None if effort_text == "none" else EffortLevel(effort_text)
        existing_role = getattr(self.squad, str(self._selected_target))
        binding = HarnessBinding(
            kind=kind,
            model=model,
            effort=effort,
            label=label,
            description=existing_role.description,
            enabled=existing_role.enabled,
            order=existing_role.order,
        )
        return binding, None

    def _apply_current_editor_changes(self) -> Optional[str]:
        """Write the editor into the deep-copied squad. Root and pool do not write."""
        if self._editor_locked():
            return None
        binding, error = self._parse_editor()
        if error or binding is None:
            if error:
                self._set_status(f"[red]{error}[/red]")
            return error
        target = self._selected_target
        role = "coder" if isinstance(target, tuple) else str(target)
        catalog_error = validate_binding_for_editor(
            binding,
            role=role,
            mode=self.mode,
            native_harness=self.native_harness,
        )
        if catalog_error:
            self._set_status(f"[red]{catalog_error}[/red]")
            return catalog_error
        if target == "scout":
            self.squad.scout = binding
        elif target == "architect":
            self.squad.architect = binding
        elif target == "reviewer":
            self.squad.reviewer = binding
        elif isinstance(target, tuple) and target[0] == "worker":
            idx = target[1]
            if 0 <= idx < len(self.squad.worker_pool):
                self.squad.worker_pool[idx] = binding
                self._refresh_worker_note(binding)
        self._rebuild_tree()
        self._refresh_summary()
        return None

    def _validate_for_action(self) -> Optional[str]:
        if self.config_error:
            return self.config_error
        if not self._editor_locked() and not self._model_input.value.strip():
            return "Model is empty. Refusing to invent a model."
        error = self._apply_current_editor_changes()
        if error:
            return error
        normalize_worker_order(self.squad)
        errors = validate_squad(self.squad)
        if errors:
            return errors[0]
        for role in ("scout", "architect", "reviewer"):
            message = validate_binding_for_editor(
                getattr(self.squad, role),
                role=role,
                mode=self.mode,
                native_harness=self.native_harness,
            )
            if message:
                return message
        for worker in self.squad.worker_pool:
            message = validate_binding_for_editor(
                worker,
                role="coder",
                mode=self.mode,
                native_harness=self.native_harness,
            )
            if message:
                return message
        return None

    def _normalize_after_pool_edit(self) -> None:
        normalize_worker_order(self.squad)
        self._rebuild_tree()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "btn-add-worker":
            idx = len(self.squad.worker_pool)
            self.squad.worker_pool.append(catalog_worker_template(idx))
            self._normalize_after_pool_edit()
            self._load_agent_into_editor(("worker", idx))
            self._set_status(f"[green]Added worker #{idx + 1} from the catalog template[/green]")

        elif btn_id == "btn-remove-worker":
            if isinstance(self._selected_target, tuple) and self._selected_target[0] == "worker":
                idx = self._selected_target[1]
                if 0 <= idx < len(self.squad.worker_pool):
                    if len(self.squad.worker_pool) == 1:
                        self._set_status("[red]The worker pool cannot be empty.[/red]")
                        return
                    self.squad.worker_pool.pop(idx)
                    self._normalize_after_pool_edit()
                    new_idx = max(0, min(idx, len(self.squad.worker_pool) - 1))
                    self._load_agent_into_editor(("worker", new_idx))
                    self._set_status("[yellow]Removed worker from pool[/yellow]")

        elif btn_id == "btn-up-worker":
            if isinstance(self._selected_target, tuple) and self._selected_target[0] == "worker":
                idx = self._selected_target[1]
                if idx > 0:
                    pool = self.squad.worker_pool
                    pool[idx - 1], pool[idx] = pool[idx], pool[idx - 1]
                    self._normalize_after_pool_edit()
                    self._load_agent_into_editor(("worker", idx - 1))

        elif btn_id == "btn-down-worker":
            if isinstance(self._selected_target, tuple) and self._selected_target[0] == "worker":
                idx = self._selected_target[1]
                if idx < len(self.squad.worker_pool) - 1:
                    pool = self.squad.worker_pool
                    pool[idx + 1], pool[idx] = pool[idx], pool[idx + 1]
                    self._normalize_after_pool_edit()
                    self._load_agent_into_editor(("worker", idx + 1))

        elif btn_id == "btn-save-default":
            error = self._validate_for_action()
            if error:
                self._set_status(f"[red]Validation Error: {error}[/red]")
                return
            try:
                path = save_squad_as_default(
                    self.squad,
                    project_config_file(self.project_path),
                )
            except Exception as exc:
                self._set_status(f"[red]Error saving default: {exc}[/red]")
                return
            self._set_status(f"[bold green]Saved default: {path}[/bold green]")

        elif btn_id == "btn-start-ergo":
            if self._submitting:
                return
            error = self._validate_for_action()
            if error:
                self._set_status(f"[red]Validation Error: {error}[/red]")
                return
            self._submitting = True
            self.dismiss(
                ErgoLaunchParams(
                    intent=self._intent_input.value.strip() or "Execute standard Ergo pipeline",
                    workspace_label=self._ws_input.value.strip() or self.default_label,
                    squad=self.squad.model_copy(deep=True),
                    start_pipeline=True,
                )
            )

        elif btn_id == "btn-cancel":
            self.dismiss(None)


ErgoSettingsModal = LaunchErgoModal
