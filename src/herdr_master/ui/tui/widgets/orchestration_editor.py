"""Reusable OrchestrationEditor widget for single-harness orchestration pipelines."""

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Label, Select, Static

from herdr_master.core.catalog import get_harness, native_binding_error
from herdr_master.core.config import (
    DEFAULT_ORCHESTRATION_MODELS,
    HerdrMasterConfig,
    OrchestrationConfig,
    load_project_config,
)
from herdr_master.core.models import HarnessKind
from herdr_master.core.slug import parse_binding
from herdr_master.ui.tui.state import harness_select_options

DEFAULT_MODELS: Dict[str, Dict[str, str]] = DEFAULT_ORCHESTRATION_MODELS


@contextmanager
def _ensure_active_app():
    """Ensure an active Textual app context is present for headless widget testing."""
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


class OrchestrationEditor(Widget):
    """Reusable Textual widget for editing single-harness Orchestration configuration.

    Edits native harness selection and role models for Scout, Architect, Workers, and Reviewer.
    Workers are displayed as a pipe-separated string and omit reasoning effort.
    """

    DEFAULT_CSS = """
    OrchestrationEditor {
        height: auto;
        layout: vertical;
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
    .status-msg {
        height: 2;
        max-height: 2;
        color: #ff6666;
    }
    """

    class Changed(Message):
        """Posted when orchestration configuration is edited."""

        def __init__(self, config: OrchestrationConfig) -> None:
            super().__init__()
            self.config = config

    def __init__(
        self,
        config: Optional[OrchestrationConfig] = None,
        *,
        project_path: Optional[Path] = None,
        default_harness: Optional[Union[HarnessKind, str]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.project_path = project_path
        self._syncing = False
        self._programmatic_models: Dict[str, str] = {}

        if config is not None:
            self._config = config.model_copy(deep=True)
        else:
            initial_harness = (
                HarnessKind(default_harness)
                if default_harness
                else HarnessKind.AGY
            )
            self._config = OrchestrationConfig(harness=initial_harness)

        options = harness_select_options(mode="native")
        initial_val = self._config.harness.value
        option_values = {val for _, val in options}
        if initial_val not in option_values and options:
            initial_val = options[0][1]

        with _ensure_active_app():
            self._harness_select = Select(
                options,
                value=initial_val,
                allow_blank=False,
                id="orch-harness",
            )
            self._scout_input = Input(
                value=self._config.scout_model or "",
                id="orch-scout-model",
                placeholder="e.g. flash_lite",
            )
            self._architect_input = Input(
                value=self._config.architect_model or "",
                id="orch-architect-model",
                placeholder="e.g. pro",
            )
            worker_str = " | ".join(self._config.worker_models) if self._config.worker_models else ""
            self._coder_input = Input(
                value=worker_str,
                id="orch-coder-model",
                placeholder="e.g. flash (or flash|pro for multiple)",
            )
            self._reviewer_input = Input(
                value=self._config.reviewer_model or "",
                id="orch-reviewer-model",
                placeholder="e.g. pro",
            )
            self._status_label = Static("", id="orch-status", classes="status-msg")

            # Apply defaults if fields are empty
            self._apply_harness_defaults(initial_val, force=not (
                self._config.scout_model
                or self._config.architect_model
                or self._config.worker_models
                or self._config.reviewer_model
            ))

    def compose(self) -> ComposeResult:
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

        yield self._status_label

    def set_status(self, text: str) -> None:
        """Update status line."""
        self._status_label.update(text)

    def _get_project_model(self, harness: str, role: str) -> Optional[str]:
        if not self.project_path:
            return None
        try:
            cfg = load_project_config(self.project_path)
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

    def on_select_changed(self, event: Select.Changed) -> None:
        if self._syncing or not self.is_mounted:
            return
        if event.select == self._harness_select:
            harness = str(event.value)
            self._apply_harness_defaults(harness, force=False)
            self._notify_changed()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._syncing or not self.is_mounted:
            return
        self._notify_changed()

    def _notify_changed(self) -> None:
        try:
            cfg = self.get_config()
            self.post_message(self.Changed(cfg))
        except Exception:
            pass

    def reload_for_project(self, project_path: Optional[Path]) -> None:
        """Reload project overrides when project folder changes."""
        self.project_path = project_path
        harness = str(self._harness_select.value)
        self._apply_harness_defaults(harness, force=False)
        self._notify_changed()

    def reload_config(
        self,
        config: OrchestrationConfig,
        *,
        project_path: Optional[Path] = None,
    ) -> None:
        """Reload orchestration config and update inputs."""
        self.project_path = project_path
        self._config = config.model_copy(deep=True)
        self._syncing = True
        try:
            self._harness_select.value = config.harness.value
            self._scout_input.value = config.scout_model or ""
            self._architect_input.value = config.architect_model or ""
            self._coder_input.value = " | ".join(config.worker_models) if config.worker_models else ""
            self._reviewer_input.value = config.reviewer_model or ""
            self.set_status("")
        finally:
            self._syncing = False
        self._notify_changed()

    def get_config(self) -> OrchestrationConfig:
        """Construct an OrchestrationConfig snapshot from editor fields."""
        harness_val = str(self._harness_select.value)
        try:
            harness_kind = HarnessKind(harness_val)
        except ValueError:
            harness_kind = HarnessKind.AGY

        raw_coders = self._coder_input.value.strip()
        worker_models = [m.strip() for m in raw_coders.split("|") if m.strip()] if raw_coders else []

        try:
            return OrchestrationConfig(
                harness=harness_kind,
                scout_model=self._scout_input.value.strip() or None,
                architect_model=self._architect_input.value.strip() or None,
                worker_models=worker_models,
                reviewer_model=self._reviewer_input.value.strip() or None,
            )
        except Exception:
            return OrchestrationConfig.model_construct(
                harness=harness_kind,
                scout_model=self._scout_input.value.strip() or None,
                architect_model=self._architect_input.value.strip() or None,
                worker_models=worker_models,
                reviewer_model=self._reviewer_input.value.strip() or None,
            )

    def validate(self) -> Optional[str]:
        """Validate all fields and native compatibility. Return actionable error or None."""
        harness_val = str(self._harness_select.value)
        try:
            kind = HarnessKind(harness_val)
        except ValueError:
            return f"Unsupported harness: {harness_val}"

        spec = get_harness(kind.value)
        if not spec.supports_native:
            return f"Harness '{kind.value}' does not support native orchestration."

        scout_m = self._scout_input.value.strip()
        arch_m = self._architect_input.value.strip()
        coder_m = self._coder_input.value.strip()
        rev_m = self._reviewer_input.value.strip()

        if not scout_m or not arch_m or not coder_m or not rev_m:
            return "All models (Scout, Architect, Coder, Reviewer) must be specified."

        # Check workers for prohibited effort levels
        worker_models = [m.strip() for m in coder_m.split("|") if m.strip()]
        for m in worker_models:
            if ":" in m and m.split(":")[-1].lower() in (
                "low",
                "medium",
                "high",
                "max",
                "xhigh",
            ):
                return (
                    f"Worker model '{m}' must not specify an effort level "
                    "(the Architect decides worker effort)."
                )

        # Check native binding errors
        for role, model in (
            ("scout", scout_m),
            ("architect", arch_m),
            ("reviewer", rev_m),
        ):
            err = native_binding_error(kind.value, model, role=role, native_harness=kind.value)
            if err:
                return err

        for worker in worker_models:
            err = native_binding_error(kind.value, worker, role="coder", native_harness=kind.value)
            if err:
                return err

        return None

    def is_valid(self) -> bool:
        """Return True if orchestration configuration is currently valid."""
        return self.validate() is None
