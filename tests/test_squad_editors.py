"""Unit and component tests for reusable SquadEditor and OrchestrationEditor widgets."""

from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, Select

from herdr_master.core.catalog import HarnessKind
from herdr_master.core.config import OrchestrationConfig
from herdr_master.core.models import EffortLevel, HarnessBinding, Squad
from herdr_master.ui.tui.modals.launch_ergo import ErgoLaunchParams, LaunchErgoModal
from herdr_master.ui.tui.modals.launch_orchestration import (
    LaunchOrchestrationModal,
    OrchestrationLaunchParams,
)
from herdr_master.ui.tui.widgets.orchestration_editor import OrchestrationEditor
from herdr_master.ui.tui.widgets.squad_editor import SquadEditor


class SquadEditorHostApp(App[None]):
    """Host test harness for SquadEditor."""

    def __init__(self, squad: Squad, **kwargs) -> None:
        super().__init__()
        self.squad = squad
        self.editor = SquadEditor(squad, **kwargs)
        self.changed_events: List[Squad] = []

    def compose(self) -> ComposeResult:
        yield self.editor

    def on_squad_editor_changed(self, event: SquadEditor.Changed) -> None:
        self.changed_events.append(event.squad)


class OrchestrationEditorHostApp(App[None]):
    """Host test harness for OrchestrationEditor."""

    def __init__(self, config: OrchestrationConfig | None = None, **kwargs) -> None:
        super().__init__()
        self.config = config
        self.editor = OrchestrationEditor(config, **kwargs)
        self.changed_events: List[OrchestrationConfig] = []

    def compose(self) -> ComposeResult:
        yield self.editor

    def on_orchestration_editor_changed(self, event: OrchestrationEditor.Changed) -> None:
        self.changed_events.append(event.config)


class DualEditorHostApp(App[None]):
    """Host test harness mounting both editors simultaneously (simulating multi-tab settings)."""

    def __init__(self, squad: Squad, orch_config: OrchestrationConfig) -> None:
        super().__init__()
        self.squad_editor = SquadEditor(squad)
        self.orch_editor = OrchestrationEditor(orch_config)
        self.squad_events: List[Squad] = []
        self.orch_events: List[OrchestrationConfig] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="tab-squad"):
            yield self.squad_editor
        with Vertical(id="tab-orchestration"):
            yield self.orch_editor

    def on_squad_editor_changed(self, event: SquadEditor.Changed) -> None:
        self.squad_events.append(event.squad)

    def on_orchestration_editor_changed(self, event: OrchestrationEditor.Changed) -> None:
        self.orch_events.append(event.config)


# --- SquadEditor Tests ---


@pytest.mark.asyncio
async def test_squad_editor_mounting_and_deep_copy():
    """SquadEditor mounts with correct roles and isolates changes from input squad."""
    original_squad = Squad()
    app = SquadEditorHostApp(original_squad)

    async with app.run_test() as pilot:
        editor = app.editor
        assert editor.squad is not original_squad
        assert editor.is_valid() is True

        # Initial selection is scout
        assert editor._selected_target == "scout"
        assert editor._harness_select.value == original_squad.scout.kind.value
        assert editor._model_input.value == original_squad.scout.model

        # Edit architect role
        editor.load_agent_into_editor("architect")
        editor._effort_select.value = "high"
        editor._model_input.value = "xai/grok-4.7"
        await pilot.pause()

        snapshot = editor.get_squad()
        assert snapshot.architect.model == "xai/grok-4.7"
        assert snapshot.architect.effort == EffortLevel.HIGH
        assert original_squad.architect.model != "xai/grok-4.7"


@pytest.mark.asyncio
async def test_squad_editor_worker_effort_omitted_and_pool_actions():
    """Workers omit effort; add, remove, and reorder worker pool actions work cleanly."""
    squad = Squad()
    app = SquadEditorHostApp(squad)

    async with app.run_test() as pilot:
        editor = app.editor

        # Select worker 0
        editor.load_agent_into_editor(("worker", 0))
        await pilot.pause()

        # Worker effort is hidden and displays architect note
        assert editor._effort_select.display is False
        assert editor._effort_note.display is True
        assert "chosen per task by the Architect" in str(editor._effort_note.render())

        # Add worker
        initial_count = len(editor.squad.worker_pool)
        await pilot.click("#btn-add-worker")
        await pilot.pause()
        assert len(editor.squad.worker_pool) == initial_count + 1

        # Reorder workers with up/down buttons
        editor.load_agent_into_editor(("worker", 1))
        await pilot.click("#btn-up-worker")
        await pilot.pause()
        assert editor._selected_target == ("worker", 0)

        # Remove worker
        await pilot.click("#btn-remove-worker")
        await pilot.pause()
        assert len(editor.squad.worker_pool) == initial_count

        # All workers in snapshot remain effort-free
        snapshot = editor.get_squad()
        assert all(w.effort is None for w in snapshot.worker_pool)


@pytest.mark.asyncio
async def test_squad_editor_validation_and_changed_messages():
    """SquadEditor validates inputs and posts Changed messages."""
    app = SquadEditorHostApp(Squad())

    async with app.run_test() as pilot:
        editor = app.editor
        assert editor.validate() is None

        # Blank model is invalid
        editor.load_agent_into_editor("scout")
        editor._model_input.value = "   "
        await pilot.pause()
        err = editor.validate()
        assert err is not None
        assert "Model is empty" in err
        assert editor.is_valid() is False

        # Correct model restores validity and posts Changed message
        editor._model_input.value = "gemini-3.8-flash-medium"
        await pilot.pause()
        assert editor.is_valid() is True
        assert len(app.changed_events) > 0


@pytest.mark.asyncio
async def test_squad_editor_responsive_layout_adaptation():
    """SquadEditor adapt_layout sets responsive classes correctly."""
    app = SquadEditorHostApp(Squad())

    async with app.run_test() as pilot:
        editor = app.editor

        editor.adapt_layout(120, 40)
        assert not editor.has_class("narrow")
        assert not editor.has_class("stacked-options")

        editor.adapt_layout(80, 24)
        assert editor.has_class("narrow")
        assert not editor.has_class("stacked-options")
        assert editor.has_class("short")

        editor.adapt_layout(60, 20)
        assert editor.has_class("stacked-options")


# --- OrchestrationEditor Tests ---


@pytest.mark.asyncio
async def test_orchestration_editor_defaults_and_harness_switch():
    """OrchestrationEditor initializes with native defaults and updates models on harness switch."""
    app = OrchestrationEditorHostApp()

    async with app.run_test() as pilot:
        editor = app.editor
        assert editor._harness_select.value == "agy"
        assert editor._scout_input.value == "flash_lite"
        assert editor._architect_input.value == "pro"
        assert editor._coder_input.value == "flash"
        assert editor._reviewer_input.value == "pro"
        assert editor.is_valid() is True

        # Switch to claude
        editor._harness_select.value = "claude"
        await pilot.pause()
        assert editor._scout_input.value == "claude-sonnet-4-6"
        assert editor._architect_input.value == "claude-opus-4-6"
        assert editor._coder_input.value == "claude-sonnet-4-6"
        assert editor._reviewer_input.value == "claude-opus-4-6"


@pytest.mark.asyncio
async def test_orchestration_editor_worker_pipe_adaptation_and_config():
    """OrchestrationEditor adapts pipe-separated coder input to worker_models list."""
    app = OrchestrationEditorHostApp()

    async with app.run_test() as pilot:
        editor = app.editor
        editor._coder_input.value = "flash | pro | gemini-3.8-flash"
        await pilot.pause()

        cfg = editor.get_config()
        assert isinstance(cfg, OrchestrationConfig)
        assert cfg.harness == HarnessKind.AGY
        assert cfg.worker_models == ["flash", "pro", "gemini-3.8-flash"]

        # Reloading config reflects in UI
        new_cfg = OrchestrationConfig(
            harness=HarnessKind.PI,
            scout_model="anthropic/claude-3-5-sonnet",
            architect_model="openai-codex/gpt-6.1-sol",
            worker_models=["anthropic/claude-3-5-sonnet", "xai/grok-4.7"],
            reviewer_model="xai/grok-4.7",
        )
        editor.reload_config(new_cfg)
        await pilot.pause()
        assert editor._harness_select.value == "pi"
        assert editor._coder_input.value == "anthropic/claude-3-5-sonnet | xai/grok-4.7"


@pytest.mark.asyncio
async def test_orchestration_editor_prohibits_worker_effort():
    """OrchestrationEditor rejects worker models that specify reasoning effort."""
    app = OrchestrationEditorHostApp()

    async with app.run_test() as pilot:
        editor = app.editor

        # Worker specifying effort fails validation
        editor._coder_input.value = "flash:high"
        await pilot.pause()
        err = editor.validate()
        assert err is not None
        assert "must not specify an effort level" in err
        assert editor.is_valid() is False

        # Removing effort restores validity
        editor._coder_input.value = "flash"
        await pilot.pause()
        assert editor.is_valid() is True


# --- Coexistence in Dual Host App ---


@pytest.mark.asyncio
async def test_dual_editor_coexistence_no_id_collisions():
    """SquadEditor and OrchestrationEditor can coexist in the same DOM without ID or event collisions."""
    app = DualEditorHostApp(Squad(), OrchestrationConfig())

    async with app.run_test() as pilot:
        # Both editors mounted and valid
        assert app.squad_editor.is_valid() is True
        assert app.orch_editor.is_valid() is True

        # Query widgets by scoped IDs
        assert pilot.app.screen.query_one("#squad-tree") is not None
        assert pilot.app.screen.query_one("#orch-harness") is not None
        assert pilot.app.screen.query_one("#orch-scout-model") is not None
        assert pilot.app.screen.query_one("#squad-harness-select") is not None

        initial_squad_events = len(app.squad_events)
        initial_orch_events = len(app.orch_events)

        # Mutate squad editor
        app.squad_editor.load_agent_into_editor("scout")
        app.squad_editor._model_input.value = "gemini-3.8-flash"
        await pilot.pause()
        assert len(app.squad_events) > initial_squad_events
        assert len(app.orch_events) == initial_orch_events

        # Mutate orchestration editor
        app.orch_editor._harness_select.value = "pi"
        await pilot.pause()
        assert len(app.orch_events) > initial_orch_events


# --- Modal Integration Tests ---


@pytest.mark.asyncio
async def test_launch_ergo_modal_integration():
    """LaunchErgoModal consumes SquadEditor and returns ErgoLaunchParams with edited squad."""
    app = App[ErgoLaunchParams | None]()
    results = []
    async with app.run_test(size=(110, 40)) as pilot:
        squad = Squad()
        modal = LaunchErgoModal(squad)
        app.push_screen(modal, results.append)
        await pilot.pause()

        # Modify squad via editor
        modal.squad_editor.load_agent_into_editor("scout")
        modal.squad_editor._model_input.value = "gemini-3.8-flash"
        modal._intent_input.load_text("Build user settings UI")
        modal._ws_input.value = "test-ws"
        await pilot.pause()

        # Start ergo
        await pilot.click("#btn-start-ergo")
        await pilot.pause()

        assert len(results) == 1
        res = results[0]
        assert isinstance(res, ErgoLaunchParams)
        assert res.intent == "Build user settings UI"
        assert res.workspace_label == "test-ws"
        assert res.squad.scout.model == "gemini-3.8-flash"


@pytest.mark.asyncio
async def test_launch_orchestration_modal_integration():
    """LaunchOrchestrationModal consumes OrchestrationEditor and returns OrchestrationLaunchParams."""
    app = App[OrchestrationLaunchParams | None]()
    results = []
    async with app.run_test(size=(90, 40)) as pilot:
        modal = LaunchOrchestrationModal()
        app.push_screen(modal, results.append)
        await pilot.pause()

        modal._intent_input.value = "Deploy orchestration workers"
        modal._workspace_label_input.value = "orch-ws"
        modal.editor._harness_select.value = "pi"
        await pilot.pause()

        await pilot.click("#btn-launch")
        await pilot.pause()

        assert len(results) == 1
        res = results[0]
        assert isinstance(res, OrchestrationLaunchParams)
        assert res.intent == "Deploy orchestration workers"
        assert res.workspace_label == "orch-ws"
        assert res.harness == HarnessKind.PI
        assert res.scout_model == "anthropic/claude-3-5-sonnet"
        assert res.architect_model == "openai-codex/gpt-6.1-sol"
