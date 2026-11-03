"""Modal screen for Squad configuration and Ergo Pipeline launching."""

from datetime import datetime
from typing import Optional, Tuple, Union

from pydantic import BaseModel
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static, Tree

from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.pipeline.squad import save_squad_as_default, validate_squad


class ErgoLaunchParams(BaseModel):
    intent: str
    workspace_label: str
    squad: Optional[Squad] = None
    start_pipeline: bool = True
    customize_squad_first: bool = False


class LaunchErgoModal(ModalScreen[Optional[ErgoLaunchParams]]):
    """2-column Squad Settings & Ergo Pipeline Launcher.

    Layout:
    - Top 3/4 (height: 3fr):
      - Left column: Agents Tree (Root Master, Scout, Architect, Worker Pool, Reviewer).
      - Right column: Fully editable agent configuration for the selected agent.
    - Bottom 1/4 (height: 1fr):
      - Prompt input field for Ergo feature objective.
      - Workspace label input.
      - Action buttons (Start Ergo Pipeline, Save as Default Squad, Cancel).
    """

    DEFAULT_CSS = """
    LaunchErgoModal {
        align: center middle;
        background: rgba(0, 0, 0, 0.75);
    }
    .modal-shell {
        width: 96%;
        height: 42;
        background: #141414;
        border: thick magenta;
        padding: 0 1;
    }
    .modal-header {
        height: 2;
    }
    .modal-title {
        color: magenta;
        text-style: bold;
    }
    .modal-subtitle {
        color: #888888;
    }
    .top-section {
        height: 27;
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
    }
    .bottom-section {
        height: 9;
        border: solid #2a2a2a;
        background: #181818;
        padding: 0 1;
    }
    .prompt-row {
        height: 4;
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

    HARNESS_OPTIONS = [(k.value.upper(), k.value) for k in HarnessKind]
    EFFORT_OPTIONS = [("None", "none")] + [(e.value.capitalize(), e.value) for e in EffortLevel]

    def __init__(self, squad: Squad, **kwargs) -> None:
        super().__init__(**kwargs)
        self.squad = squad.model_copy(deep=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        self.default_label = f"ergo-{ts}"

        # Current editor selection: 'scout' | 'architect' | 'reviewer' | ('worker', idx) | 'root'
        self._selected_target: Union[str, Tuple[str, int]] = "scout"
        self._is_updating_fields: bool = False

        # Right Column Editor widgets
        self._editor_heading = Label("Scout Configuration", classes="editor-heading")
        self._harness_select = Select(self.HARNESS_OPTIONS, value="agy", allow_blank=False)
        self._model_input = Input(
            value="gemini-3.8-flash-high", placeholder="e.g. gemini-3.8-flash-high"
        )
        self._effort_select = Select(self.EFFORT_OPTIONS, value="high", allow_blank=False)
        self._label_input = Input(value="scout", placeholder="Optional capability label")
        self._status_label = Static("", classes="status-msg")

        # Bottom Section widgets
        self._intent_input = Input(
            placeholder="e.g. Implement distributed Redis cache for authentication service"
        )
        self._ws_input = Input(value=self.default_label, placeholder="Workspace label")

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-shell"):
            # Header
            with Vertical(classes="modal-header"):
                yield Static("⚙️ Squad Settings & Ergo Pipeline Launcher", classes="modal-title")
                yield Static(
                    "Select an agent on the left to edit harness kind, model, and effort. "
                    "Save as project default or start the Ergo pipeline below.",
                    classes="modal-subtitle",
                )

            # Top Section: 3/4 height (height: 3fr)
            with Horizontal(classes="top-section"):
                # Left Column: Tree
                with Vertical(classes="left-tree-col"):
                    yield Label("Squad Topology:", classes="col-header")
                    yield Tree("👑 Herdr-Master Root", id="squad-tree", classes="squad-tree")

                # Right Column: Editor
                with VerticalScroll(classes="right-editor-col"):
                    yield self._editor_heading

                    yield Label("Harness Kind:", classes="field-label")
                    yield self._harness_select

                    yield Label("Model Slug:", classes="field-label")
                    yield self._model_input

                    yield Label("Reasoning Effort:", classes="field-label")
                    yield self._effort_select

                    yield Label("Capability / Specialty Label:", classes="field-label")
                    yield self._label_input

                    with Horizontal(id="worker-pool-bar", classes="worker-pool-buttons"):
                        yield Button("+ Add Worker", id="btn-add-worker", variant="success")
                        yield Button("- Remove Worker", id="btn-remove-worker", variant="error")
                        yield Button("↑ Up", id="btn-up-worker")
                        yield Button("↓ Down", id="btn-down-worker")

                    yield self._status_label

            # Bottom Section: 1/4 height (height: 1fr)
            with Vertical(classes="bottom-section"):
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

    def _rebuild_tree(self) -> None:
        """Reconstruct the interactive squad tree on the left."""
        tree = self.query_one("#squad-tree", Tree)
        tree.clear()
        tree.root.label = "👑 [bold cyan]Herdr-Master Root[/bold cyan]"
        tree.root.data = {"type": "root"}

        # 1. Scout
        scout_sub = f"{self.squad.scout.kind.value}:{self.squad.scout.model}"
        scout_label = f"● [bold]Scout[/bold] [dim]({scout_sub})[/dim]"
        tree.root.add_leaf(scout_label, data={"type": "role", "role": "scout"})

        # 2. Architect
        arch_sub = f"{self.squad.architect.kind.value}:{self.squad.architect.model}"
        arch_label = f"● [bold]Architect[/bold] [dim]({arch_sub})[/dim]"
        tree.root.add_leaf(arch_label, data={"type": "role", "role": "architect"})

        # 3. Worker Pool Subtree
        pool_node = tree.root.add(
            f"📦 [bold]Worker Pool[/bold] [dim]({len(self.squad.worker_pool)} coders)[/dim]",
            data={"type": "pool"},
        )
        pool_node.expand()

        for idx, worker in enumerate(self.squad.worker_pool):
            w_sub = f"{worker.kind.value}:{worker.model}"
            w_name = worker.label or f"worker-{idx + 1}"
            w_text = f"↳ [bold]{w_name}[/bold] [dim]({w_sub})[/dim]"
            pool_node.add_leaf(w_text, data={"type": "worker", "index": idx})

        # 4. Reviewer
        rev_sub = f"{self.squad.reviewer.kind.value}:{self.squad.reviewer.model}"
        rev_label = f"● [bold]Reviewer[/bold] [dim]({rev_sub})[/dim]"
        tree.root.add_leaf(rev_label, data={"type": "role", "role": "reviewer"})

        tree.root.expand()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        """Handle tree node selection on the left column."""
        data = event.node.data or {}
        node_type = data.get("type", "root")

        if node_type == "role":
            role = data.get("role", "scout")
            self._load_agent_into_editor(role)
        elif node_type == "worker":
            idx = data.get("index", 0)
            self._load_agent_into_editor(("worker", idx))
        elif node_type == "pool":
            if self.squad.worker_pool:
                self._load_agent_into_editor(("worker", 0))
            else:
                self._load_agent_into_editor("pool_overview")
        else:
            self._load_agent_into_editor("root")

    def _load_agent_into_editor(self, target: Union[str, Tuple[str, int]]) -> None:
        """Populate right column editor with the selected agent's settings."""
        self._selected_target = target
        self._is_updating_fields = True

        pool_bar = self.query_one("#worker-pool-bar", Horizontal)

        try:
            if target == "scout":
                self._editor_heading.update("Scout Role Configuration")
                self._populate_fields(self.squad.scout)
                pool_bar.display = False
            elif target == "architect":
                self._editor_heading.update("Architect Role Configuration")
                self._populate_fields(self.squad.architect)
                pool_bar.display = False
            elif target == "reviewer":
                self._editor_heading.update("Reviewer Role Configuration")
                self._populate_fields(self.squad.reviewer)
                pool_bar.display = False
            elif isinstance(target, tuple) and target[0] == "worker":
                idx = target[1]
                if 0 <= idx < len(self.squad.worker_pool):
                    worker = self.squad.worker_pool[idx]
                    w_name = worker.label or f"worker-{idx + 1}"
                    self._editor_heading.update(f"Worker Pool: {w_name} (Position #{idx + 1})")
                    self._populate_fields(worker)
                pool_bar.display = True
            elif target == "root":
                self._editor_heading.update("👑 Herdr-Master Squad Overview")
                self._populate_fields(self.squad.scout)
                pool_bar.display = False
            else:
                self._editor_heading.update("📦 Worker Pool Overview")
                pool_bar.display = True
        finally:
            self._is_updating_fields = False

    def _populate_fields(self, binding: HarnessBinding) -> None:
        self._harness_select.value = binding.kind.value
        self._model_input.value = binding.model
        self._effort_select.value = binding.effort.value if binding.effort else "none"
        self._label_input.value = binding.label or ""

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._is_updating_fields:
            return
        self._apply_current_editor_changes()

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._is_updating_fields:
            return
        self._apply_current_editor_changes()

    def _apply_current_editor_changes(self) -> None:
        """Apply right column values into the in-memory Squad object."""
        try:
            harness_str = str(self._harness_select.value)
            hkind = HarnessKind(harness_str)
        except Exception:
            hkind = HarnessKind.AGY

        model_str = self._model_input.value.strip() or "gemini-3.8-flash-high"
        effort_str = str(self._effort_select.value)
        effort = None if effort_str == "none" else EffortLevel(effort_str)
        label_str = self._label_input.value.strip() or None

        target = self._selected_target
        if target == "scout":
            self.squad.scout.kind = hkind
            self.squad.scout.model = model_str
            self.squad.scout.effort = effort
            self.squad.scout.label = label_str
        elif target == "architect":
            self.squad.architect.kind = hkind
            self.squad.architect.model = model_str
            self.squad.architect.effort = effort
            self.squad.architect.label = label_str
        elif target == "reviewer":
            self.squad.reviewer.kind = hkind
            self.squad.reviewer.model = model_str
            self.squad.reviewer.effort = effort
            self.squad.reviewer.label = label_str
        elif isinstance(target, tuple) and target[0] == "worker":
            idx = target[1]
            if 0 <= idx < len(self.squad.worker_pool):
                w = self.squad.worker_pool[idx]
                w.kind = hkind
                w.model = model_str
                w.effort = effort
                w.label = label_str

        # Update left tree representation
        self._rebuild_tree()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "btn-add-worker":
            idx = len(self.squad.worker_pool)
            new_worker = HarnessBinding.from_slug(
                "agy:gemini-3.8-flash-high", label=f"worker-{idx + 1}", order=idx
            )
            self.squad.worker_pool.append(new_worker)
            self._rebuild_tree()
            self._load_agent_into_editor(("worker", idx))
            self._status_label.update(f"[green]✓ Added new worker #{idx + 1}[/green]")

        elif btn_id == "btn-remove-worker":
            if isinstance(self._selected_target, tuple) and self._selected_target[0] == "worker":
                idx = self._selected_target[1]
                if 0 <= idx < len(self.squad.worker_pool):
                    self.squad.worker_pool.pop(idx)
                    self._rebuild_tree()
                    new_idx = max(0, min(idx, len(self.squad.worker_pool) - 1))
                    if self.squad.worker_pool:
                        self._load_agent_into_editor(("worker", new_idx))
                    else:
                        self._load_agent_into_editor("scout")
                    self._status_label.update("[yellow]Removed worker from pool[/yellow]")

        elif btn_id == "btn-up-worker":
            if isinstance(self._selected_target, tuple) and self._selected_target[0] == "worker":
                idx = self._selected_target[1]
                if idx > 0:
                    self.squad.worker_pool[idx - 1], self.squad.worker_pool[idx] = (
                        self.squad.worker_pool[idx],
                        self.squad.worker_pool[idx - 1],
                    )
                    self._rebuild_tree()
                    self._load_agent_into_editor(("worker", idx - 1))

        elif btn_id == "btn-down-worker":
            if isinstance(self._selected_target, tuple) and self._selected_target[0] == "worker":
                idx = self._selected_target[1]
                if idx < len(self.squad.worker_pool) - 1:
                    self.squad.worker_pool[idx + 1], self.squad.worker_pool[idx] = (
                        self.squad.worker_pool[idx],
                        self.squad.worker_pool[idx + 1],
                    )
                    self._rebuild_tree()
                    self._load_agent_into_editor(("worker", idx + 1))

        elif btn_id == "btn-save-default":
            errors = validate_squad(self.squad)
            if errors:
                self._status_label.update(f"[red]Validation Error: {errors[0]}[/red]")
                return
            try:
                save_squad_as_default(self.squad)
                self._status_label.update(
                    "[bold green]✓ Saved squad as default in .herdr-master.toml[/bold green]"
                )
            except Exception as e:
                self._status_label.update(f"[red]Error saving default: {e}[/red]")

        elif btn_id == "btn-start-ergo":
            errors = validate_squad(self.squad)
            if errors:
                self._status_label.update(f"[red]Validation Error: {errors[0]}[/red]")
                return
            intent = self._intent_input.value.strip() or "Execute standard Ergo pipeline"
            ws_label = self._ws_input.value.strip() or self.default_label
            self.dismiss(
                ErgoLaunchParams(
                    intent=intent,
                    workspace_label=ws_label,
                    squad=self.squad,
                    start_pipeline=True,
                )
            )

        elif btn_id == "btn-cancel":
            self.dismiss(None)


# Backward compatibility alias
ErgoSettingsModal = LaunchErgoModal
