"""AiHead & skill setup single-screen TUI, --defaults, and launcher flags."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from typer.testing import CliRunner

from herdr_master.cli import aihead_setup
from herdr_master.cli.main import aihead_app, app
from herdr_master.cli.setup_catalog import (
    _parse_agy_models,
    _parse_opencode_models,
    _parse_pi_models,
    discover_model_catalog,
)
from herdr_master.cli.setup_tui import SECTIONS, SetupTuiApp, _herdr_effort_separate
from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import HerdrMasterConfig
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind

runner = CliRunner()


def _load(path: Path) -> dict:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def _offline_catalog(cfg=None, squad=None):
    return discover_model_catalog(cfg=cfg, squad=squad, probe_live=False)


def _pick_keys(app_obj: SetupTuiApp, option_label: str) -> list[str]:
    """Build keystrokes to select a matching choice in the currently focused picker."""
    return ["ENTER", *list(option_label), "ENTER"]


# ---------------------------------------------------------------------------
# Non-interactive --defaults --yes tests (must NOT open TUI)
# ---------------------------------------------------------------------------


def test_defaults_writes_expected_role_models(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    with patch("herdr_master.cli.aihead_setup.run_setup_tui") as tui_mock:
        result = runner.invoke(
            app, ["aihead-setup", "--defaults", "--yes", "--config", str(cfg)]
        )
    assert result.exit_code == 0, result.output
    tui_mock.assert_not_called()

    data = _load(cfg)
    squad = data["models"]["default_squad"]
    assert squad["scout"] == "opencode:opencode/beam-free:medium"
    assert squad["architect"] == "opencode:openai/gpt-6.1-sol:high"
    assert squad["reviewer"] == "opencode:openai/gpt-6.1-sol:medium"

    pool = data["models"]["worker_pool"]
    assert list(pool) == ["worker-1"]
    assert pool["worker-1"]["binding"] == "opencode:opencode/deepseek-v4.1-flash-free"

    orch = data["orchestration"]
    assert orch["harness"] == "opencode"
    assert orch["scout_model"] == "opencode/beam-free"
    assert orch["scout_effort"] == "medium"
    assert orch["architect_model"] == "openai/gpt-6.1-sol"
    assert orch["architect_effort"] == "high"
    assert orch["reviewer_model"] == "openai/gpt-6.1-sol"
    assert orch["reviewer_effort"] == "medium"
    assert orch["worker_models"] == ["opencode/deepseek-v4.1-flash-free"]


def test_defaults_overwrite_existing_config(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    cfg.write_text(
        """
[general]
default_session = "default"

[models.default_squad]
scout = "agy:gemini-3.8-flash:low"
architect = "pi:xai/grok-4.7:max"
reviewer = "agy:gemini-3.8-flash:low"

[models.worker_pool.worker-1]
binding = "agy:gemini-3.8-flash"

[ai_head.binding]
kind = "pi"
model = "openai-codex/gpt-6.1-sol"
effort = "max"
label = "ai-head"
""".lstrip(),
        encoding="utf-8",
    )

    with patch("herdr_master.cli.aihead_setup.run_setup_tui") as tui_mock:
        result = runner.invoke(
            app, ["--aihead-setup", "--defaults", "--yes", "--config", str(cfg)]
        )
    assert result.exit_code == 0, result.output
    tui_mock.assert_not_called()

    data = _load(cfg)
    squad = data["models"]["default_squad"]
    assert squad["scout"] == "opencode:opencode/beam-free:medium"
    assert squad["architect"] == "opencode:openai/gpt-6.1-sol:high"
    assert squad["reviewer"] == "opencode:openai/gpt-6.1-sol:medium"

    pool = data["models"]["worker_pool"]
    assert list(pool) == ["worker-1"]
    assert pool["worker-1"]["binding"] == "opencode:opencode/deepseek-v4.1-flash-free"

    orch = data["orchestration"]
    assert orch["harness"] == "opencode"
    assert orch["scout_model"] == "opencode/beam-free"
    assert orch["scout_effort"] == "medium"
    assert orch["architect_model"] == "openai/gpt-6.1-sol"
    assert orch["architect_effort"] == "high"
    assert orch["reviewer_model"] == "openai/gpt-6.1-sol"
    assert orch["reviewer_effort"] == "medium"
    assert orch["worker_models"] == ["opencode/deepseek-v4.1-flash-free"]

    # Unrelated existing sections survive untouched.
    binding = data["ai_head"]["binding"]
    assert binding["kind"] == "pi"
    assert binding["model"] == "openai-codex/gpt-6.1-sol"
    assert binding["effort"] == "max"
    assert binding["label"] == "ai-head"
    assert data["general"]["default_session"] == "default"


def test_root_flag_runs_the_same_wizard(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    result = runner.invoke(
        app, ["--aihead-setup", "--defaults", "--yes", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert (
        _load(cfg)["models"]["default_squad"]["scout"]
        == "opencode:opencode/beam-free:medium"
    )


def test_bare_invocation_and_aihead_help_exit_zero():
    bare = runner.invoke(app, [])
    assert bare.exit_code == 0, bare.output
    assert "--aihead" in bare.output

    help_result = runner.invoke(app, ["--aihead", "--help"])
    assert help_result.exit_code == 0, help_result.output


# ---------------------------------------------------------------------------
# Headless single-screen TUI tests (no real terminal required)
# ---------------------------------------------------------------------------


def test_tui_single_screen_renders_all_skills_and_options(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    tui = SetupTuiApp(config_path=cfg, catalog=_offline_catalog())

    # Initial screen lists every SECTIONS label on one screen.
    initial = tui.render(width=110, height=32)
    for _, sec_label in SECTIONS:
        assert sec_label in initial

    # 1. aihead options
    tui.handle_key("1")
    aihead_screen = tui.render(width=110, height=32)
    for label in ("Harness", "Model", "Effort", "Enabled", "Mode", "Split Pane", "Autonomy"):
        assert label in aihead_screen

    # 2. ergo options, including the worker pool that used to be its own section
    tui.handle_key("2")
    ergo_screen = tui.render(width=110, height=48)
    for label in ("Coordinator", "Scout", "Architect", "Reviewer", "Worker Pool"):
        assert label in ergo_screen
    ergo_rows = [row.key for row in tui._current_rows()]
    assert ergo_rows.index("ergo.reviewer.model") < ergo_rows.index("pool.0.kind")
    assert "pool.0.effort" not in ergo_rows
    assert "worker-pool" not in [sec_id for sec_id, _ in SECTIONS]

    # 3. orchestration options
    tui.handle_key("3")
    orch_screen = tui.render(width=110, height=32)
    for label in ("Native Host", "Harness", "Scout", "Worker 1 Model", "+ Add Worker"):
        assert label in orch_screen

    # 4. council options
    tui.handle_key("4")
    council_screen = tui.render(width=110, height=32)
    for label in ("Coordinator", "Scout", "council-1", "council-2", "+ Add Council Member"):
        assert label in council_screen

    # 5. herdr-master options
    tui.handle_key("5")
    master_screen = tui.render(width=110, height=32)
    for label in ("Override Mode", "Harness", "Model"):
        assert label in master_screen
    assert "Effort" not in master_screen

    # Worker pool rows live in Ergo; section 6 is Available.
    tui.handle_key("2")
    pool_rows = tui._current_rows()
    pool_labels = [row.label for row in pool_rows]
    for label in ("worker-1 Harness", "worker-1 Model", "worker-1 Enabled", "+ Add Worker"):
        assert label in pool_labels
    kind_idx = next(i for i, row in enumerate(pool_rows) if row.key == "pool.0.kind")
    tui.field_cursors["ergo"] = kind_idx
    assert "worker-1 Harness" in tui.render(width=110, height=32)
    tui.field_cursors["ergo"] = next(i for i, row in enumerate(pool_rows) if row.key == "pool.add")
    assert "+ Add Worker" in tui.render(width=110, height=32)


def test_tui_driven_without_terminal_reaches_save_functions_via_herdm_aihead_setup(
    tmp_path: Path, monkeypatch
):
    cfg = tmp_path / ".herdr-master.toml"
    cfg.write_text('[unrelated]\nvalue = "keep"\n', encoding="utf-8")

    # Drive TUI headlessly:
    # Section 1 (aihead): pick harness 'pi', pick model 'xai/grok-4.7', pick effort 'high',
    # toggle Enabled -> true.
    # Section 2 (ergo): pick Scout harness 'agy', model 'gemini-3.8-flash-low'.
    # The slug already encodes effort, so the binding stores effort=None.
    # Add a worker from Ergo, then press 's' to save once.
    keys = [
        "1",
        "ENTER", *"pi", "ENTER",
        "DOWN", "ENTER", *"xai/grok-4.7", "ENTER",
        "DOWN", "ENTER", *"high", "ENTER",
        "DOWN", "SPACE",  # Enabled -> true
        "2",
        "DOWN",  # skip Coordinator mode -> Scout Harness
        "ENTER", *"agy", "ENTER",
        "DOWN", "ENTER", *"gemini-3.8-flash-low", "ENTER",
        "a",  # add worker inside Ergo
        "s",  # save once
    ]

    def fake_run_setup_tui(config_path=None, **kwargs):
        app_obj = SetupTuiApp(
            config_path=config_path,
            catalog=_offline_catalog(),
            save_project_fn=kwargs.get("save_project_fn"),
            save_squad_fn=kwargs.get("save_squad_fn"),
        )
        return app_obj.drive(keys)

    monkeypatch.setattr("herdr_master.cli.aihead_setup.run_setup_tui", fake_run_setup_tui)

    with (
        patch.object(
            aihead_setup, "save_project_config", wraps=aihead_setup.save_project_config
        ) as save_cfg,
        patch.object(
            aihead_setup, "save_squad_as_default", wraps=aihead_setup.save_squad_as_default
        ) as save_squad,
    ):
        result = runner.invoke(app, ["aihead-setup", "--config", str(cfg)])

    assert result.exit_code == 0, result.output
    save_cfg.assert_called_once()
    save_squad.assert_called_once()

    data = _load(cfg)
    assert data["unrelated"]["value"] == "keep"
    assert data["ai_head"]["binding"]["kind"] == "pi"
    assert data["ai_head"]["binding"]["model"] == "xai/grok-4.7"
    assert data["ai_head"]["binding"]["effort"] == "high"
    assert data["ai_head"]["enabled"] is True
    assert data["models"]["default_squad"]["scout"] == "agy:gemini-3.8-flash-low"
    assert len(data["models"]["worker_pool"]) == 3


def test_tui_driven_via_aihead_setup_configures_all_skills_and_saves_once(
    tmp_path: Path, monkeypatch
):
    cfg = tmp_path / ".herdr-master.toml"
    cfg.write_text(
        """
[unrelated]
value = "keep"

[orchestration]
harness = "agy"
scout_model = "flash_lite"

[orchestration.harnesses.agy]
scout_model = "flash_lite"
custom = "keep-agy"

[orchestration.harnesses.opencode]
scout_model = "general"
architect_model = "general"
reviewer_model = "general"
worker_models = ["general"]
custom = "keep-opencode"
""".lstrip(),
        encoding="utf-8",
    )

    keys = [
        # Section 3 (orchestration): switch native harness to opencode
        "3",
        "ENTER", *"opencode", "ENTER",
        # Section 4 (council): set coordinator to pi:xai/grok-4.7:high
        "4",
        "ENTER", *"pi", "ENTER",
        "DOWN", "ENTER", *"xai/grok-4.7", "ENTER",
        "DOWN", "ENTER", *"high", "ENTER",
        # Section 5 (herdr-master): set custom coordinator pi:xai/grok-4.7:max
        "5",
        "DOWN", "ENTER", *"pi", "ENTER",
        "DOWN", "ENTER", *"xai/grok-4.7", "ENTER",
        "DOWN", "ENTER", *"max", "ENTER",
        # Save once
        "s",
    ]

    def fake_run_setup_tui(config_path=None, **kwargs):
        app_obj = SetupTuiApp(
            config_path=config_path,
            catalog=_offline_catalog(),
            save_project_fn=kwargs.get("save_project_fn"),
            save_squad_fn=kwargs.get("save_squad_fn"),
        )
        return app_obj.drive(keys)

    monkeypatch.setattr("herdr_master.cli.aihead_setup.run_setup_tui", fake_run_setup_tui)

    with patch.object(
        aihead_setup, "save_project_config", wraps=aihead_setup.save_project_config
    ) as save_cfg:
        result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])

    assert result.exit_code == 0, result.output
    save_cfg.assert_called_once()

    data = _load(cfg)
    assert data["unrelated"]["value"] == "keep"
    orch = data["orchestration"]
    assert orch["harness"] == "opencode"
    assert orch["harnesses"]["agy"]["custom"] == "keep-agy"
    assert orch["harnesses"]["opencode"]["custom"] == "keep-opencode"
    assert data["council"]["coordinator"]["kind"] == "pi"
    assert data["council"]["coordinator"]["model"] == "xai/grok-4.7"
    assert data["master_coordinator"]["kind"] == "pi"
    assert data["master_coordinator"]["model"] == "xai/grok-4.7"
    assert data["master_coordinator"]["effort"] == "max"


def test_tui_quit_discards_edits_and_never_writes(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")

    def fake_run_setup_tui(config_path=None, **kwargs):
        app_obj = SetupTuiApp(
            config_path=config_path,
            catalog=_offline_catalog(),
            save_project_fn=kwargs.get("save_project_fn"),
            save_squad_fn=kwargs.get("save_squad_fn"),
        )
        # Edit AiHead harness then quit without saving.
        return app_obj.drive(["1", "RIGHT", "q"])

    monkeypatch.setattr("herdr_master.cli.aihead_setup.run_setup_tui", fake_run_setup_tui)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert cfg.read_text(encoding="utf-8") == original


def test_tui_interrupt_leaves_file_untouched(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")

    def fake_run_setup_tui(config_path=None, **kwargs):
        app_obj = SetupTuiApp(config_path=config_path, catalog=_offline_catalog())
        return app_obj.drive(["1", "CTRL_C"])

    monkeypatch.setattr("herdr_master.cli.aihead_setup.run_setup_tui", fake_run_setup_tui)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code != 0
    assert cfg.read_text(encoding="utf-8") == original


def test_tui_non_tty_without_driver_exits_nonzero(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")

    with patch("herdr_master.cli.setup_tui.sys.stdin.isatty", return_value=False):
        result = runner.invoke(app, ["aihead-setup", "--config", str(cfg)])

    assert result.exit_code != 0
    assert "requires a terminal" in result.output.lower()
    assert cfg.read_text(encoding="utf-8") == original


def test_tui_validation_error_blocks_save_and_keeps_tui_open(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")

    tui = SetupTuiApp(config_path=cfg, catalog=_offline_catalog())
    # Disable both workers in the Ergo worker pool and attempt to save.
    tui.handle_key("2")
    rows = tui._current_rows()
    first = next(i for i, row in enumerate(rows) if row.key == "pool.0.enabled")
    second = next(i for i, row in enumerate(rows) if row.key == "pool.1.enabled")
    tui.drive(["DOWN"] * first + ["SPACE"] + ["DOWN"] * (second - first) + ["SPACE", "s"])
    assert tui.saved is False
    assert tui.done is False
    assert tui.status_is_error is True
    assert "enabled worker" in tui.status_message.lower()
    assert cfg.read_text(encoding="utf-8") == original


def test_failed_atomic_replace_preserves_file(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")

    def fake_run_setup_tui(config_path=None, **kwargs):
        app_obj = SetupTuiApp(
            config_path=config_path,
            catalog=_offline_catalog(),
            save_project_fn=kwargs.get("save_project_fn"),
            save_squad_fn=kwargs.get("save_squad_fn"),
        )
        return app_obj.drive(["s"])

    monkeypatch.setattr("herdr_master.cli.aihead_setup.run_setup_tui", fake_run_setup_tui)
    with patch("herdr_master.core.config.os.replace", side_effect=OSError("disk failure")):
        result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    # save() catches OSError inside TUI and stays open if keys end; file remains untouched.
    assert result.exit_code == 0
    assert cfg.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".config.toml.*.tmp"))


def test_setup_cwd_anchors_config(tmp_path: Path, monkeypatch):
    def fake_run_setup_tui(config_path=None, **kwargs):
        app_obj = SetupTuiApp(
            config_path=config_path,
            catalog=_offline_catalog(),
            save_project_fn=kwargs.get("save_project_fn"),
            save_squad_fn=kwargs.get("save_squad_fn"),
        )
        return app_obj.drive(["s"])

    monkeypatch.setattr("herdr_master.cli.aihead_setup.run_setup_tui", fake_run_setup_tui)
    result = runner.invoke(aihead_app, ["--setup", "--cwd", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".herdr-master.toml").is_file()


def test_tui_preserves_extra_worker_and_binding_metadata(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        """
[ai_head.binding]
kind = "pi"
model = "xai/grok-4.7"
custom = "preserve"

[models.worker_pool.worker-1]
binding = "pi:xai/grok-4.7"
custom = "keep"
""".lstrip(),
        encoding="utf-8",
    )
    tui = SetupTuiApp(config_path=cfg, catalog=_offline_catalog())
    tui.drive(["s"])
    assert tui.saved is True
    data = _load(cfg)
    assert data["ai_head"]["binding"]["custom"] == "preserve"
    assert data["models"]["worker_pool"]["worker-1"]["custom"] == "keep"


def test_ergo_custom_coordinator_writes_and_clears(tmp_path: Path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("", encoding="utf-8")

    tui = SetupTuiApp(config_path=cfg, catalog=_offline_catalog())
    # Toggle Coordinator mode to Custom, pick pi + xai/grok-4.7 + high, then save.
    tui.drive(
        [
            "2",
            "ENTER", "DOWN", "ENTER",  # Coordinator -> Custom
            "DOWN", "ENTER", *"pi", "ENTER",
            "DOWN", "ENTER", *"xai/grok-4.7", "ENTER",
            "DOWN", "ENTER", *"high", "ENTER",
            "s",
        ]
    )
    assert tui.saved is True
    coordinator = _load(cfg)["ergo"]["coordinator"]
    assert coordinator["kind"] == "pi"
    assert coordinator["model"] == "xai/grok-4.7"
    assert coordinator["effort"] == "high"

    # Toggle back to Same as Scout -> clears [ergo].
    tui2 = SetupTuiApp(config_path=cfg, catalog=_offline_catalog())
    tui2.drive(["2", "RIGHT", "s"])
    assert tui2.saved is True
    assert "ergo" not in _load(cfg)


def test_available_section_toggles_one_harness_and_leaves_others_unrestricted(
    tmp_path: Path,
):
    cfg = tmp_path / ".herdr-master.toml"
    catalog = _offline_catalog()
    tui = SetupTuiApp(config_path=cfg, catalog=catalog)
    assert "1-6" in tui.status_message
    assert "1-7" not in tui.status_message

    discovered = [opt.value for opt in catalog.models("agy", mode="herdr", current=None)]
    assert len(discovered) >= 2
    toggled = discovered[0]

    # Visit native and a second harness, then return. View choices must not save.
    tui.drive(["6", "RIGHT", "LEFT", "DOWN", "RIGHT", "LEFT"])
    assert tui.current_section == "models"
    assert tui.dirty is False
    assert tui.cfg.models.available is None
    assert tui.available_view_mode == "herdr"
    assert tui.available_view_harness == "agy"
    assert tui._section_summary("models") == "unrestricted"

    rows = tui._current_rows()
    assert [row.key for row in rows[:2]] == [
        "available.view.mode",
        "available.view.harness",
    ]
    assert rows[2].key == "available.all"
    assert rows[2].label == "All"
    assert rows[2].current_value is True

    model_toggles = [row for row in rows if row.key.startswith("available.toggle.")]
    assert [row.key for row in model_toggles] == [
        f"available.toggle.{index}" for index in range(len(discovered))
    ]
    assert all(row.label == discovered[index] for index, row in enumerate(model_toggles))
    assert toggled not in "".join(row.key for row in rows)

    tui.drive(["DOWN", "DOWN", "SPACE", "s"])
    assert tui.saved is True
    rerendered = tui._current_rows()
    assert rerendered[2].current_value is False
    assert rerendered[2].value_text == "false"
    saved = tui.cfg.models.available
    assert saved is not None
    assert saved.native is None
    assert set(saved.herdr or {}) == {"agy"}
    assert saved.herdr["agy"] == [model for model in discovered if model != toggled]
    assert tui._section_summary("models") == f"{len(discovered) - 1} enabled"

    data = _load(cfg)
    available = data["models"]["available"]
    assert set(available) == {"herdr"}
    assert set(available["herdr"]) == {"agy"}
    assert available["herdr"]["agy"] == [model for model in discovered if model != toggled]


def test_available_section_toggle_all_disables_and_enables(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    catalog = _offline_catalog()
    tui = SetupTuiApp(config_path=cfg, catalog=catalog)

    discovered = [opt.value for opt in catalog.models("agy", mode="herdr", current=None)]
    assert len(discovered) >= 2

    # Navigate to section 6, press ["DOWN", "DOWN"] to land cursor on "All" (available.all). Current value is True.
    tui.drive(["6", "DOWN", "DOWN"])
    assert tui.current_section == "models"
    rows = tui._current_rows()
    assert rows[2].key == "available.all"
    assert rows[2].label == "All"
    assert rows[2].current_value is True
    assert rows[2].value_text == "true"

    # Press "SPACE": toggles "All" to False. Status shows "Updated available.all".
    tui.drive(["SPACE"])
    assert "Updated available.all" in tui.status_message

    # Verify all individual model rows in _current_rows() now have current_value is False and value_text == "false".
    rows = tui._current_rows()
    assert rows[2].current_value is False
    assert rows[2].value_text == "false"
    model_toggles = [row for row in rows if row.key.startswith("available.toggle.")]
    assert len(model_toggles) == len(discovered)
    assert all(row.current_value is False for row in model_toggles)
    assert all(row.value_text == "false" for row in model_toggles)

    # Press "s" to save.
    tui.drive(["s"])
    # Verify tui.saved is True.
    assert tui.saved is True
    # Verify tui.cfg.models.available.herdr["agy"] == [].
    assert tui.cfg.models.available is not None
    assert tui.cfg.models.available.herdr["agy"] == []
    # Verify section summary shows "0 enabled".
    assert tui._section_summary("models") == "0 enabled"
    # Verify saved TOML contains [models.available.herdr] agy = [].
    data = _load(cfg)
    assert data["models"]["available"]["herdr"]["agy"] == []

    # Next, press "SPACE" again on "All": toggles "All" to True.
    tui.done = False
    tui.drive(["SPACE"])
    assert "Updated available.all" in tui.status_message
    rows = tui._current_rows()
    assert rows[2].current_value is True
    assert rows[2].value_text == "true"

    # Verify all individual model rows now have current_value is True and value_text == "true".
    model_toggles = [row for row in rows if row.key.startswith("available.toggle.")]
    assert len(model_toggles) == len(discovered)
    assert all(row.current_value is True for row in model_toggles)
    assert all(row.value_text == "true" for row in model_toggles)

    # Press "s" to save.
    tui.drive(["s"])
    assert tui.saved is True
    # Verify tui.cfg.models.available.herdr["agy"] == list(discovered).
    assert tui.cfg.models.available.herdr["agy"] == list(discovered)
    # Verify section summary shows f"{len(discovered)} enabled".
    assert tui._section_summary("models") == f"{len(discovered)} enabled"
    data = _load(cfg)
    assert data["models"]["available"]["herdr"]["agy"] == list(discovered)


def test_available_section_individual_toggle_syncs_all_state(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    catalog = _offline_catalog()
    tui = SetupTuiApp(config_path=cfg, catalog=catalog)

    discovered = [opt.value for opt in catalog.models("agy", mode="herdr", current=None)]
    assert len(discovered) >= 2

    # Navigate to section 6 ("models")
    tui.drive(["6"])
    rows = tui._current_rows()
    assert rows[2].key == "available.all"
    assert rows[2].current_value is True
    assert rows[2].value_text == "true"

    # Move down to first individual model toggle: index 3 (available.toggle.0)
    # Cursor starts at 0 -> 1 -> 2 -> 3
    tui.drive(["DOWN", "DOWN", "DOWN"])
    rows = tui._current_rows()
    assert rows[3].key == "available.toggle.0"
    assert rows[3].current_value is True

    # When all models are enabled, disabling one model turns "All" row value_text and current_value to False.
    tui.drive(["SPACE"])
    rows = tui._current_rows()
    assert rows[3].current_value is False
    assert rows[3].value_text == "false"
    assert rows[2].key == "available.all"
    assert rows[2].current_value is False
    assert rows[2].value_text == "false"

    # Toggling that model back to enabled turns "All" row back to True.
    tui.drive(["SPACE"])
    rows = tui._current_rows()
    assert rows[3].current_value is True
    assert rows[3].value_text == "true"
    assert rows[2].key == "available.all"
    assert rows[2].current_value is True
    assert rows[2].value_text == "true"


def test_available_section_all_toggle_with_empty_catalog(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    catalog = _offline_catalog()
    catalog.herdr_models["agy"] = []
    tui = SetupTuiApp(config_path=cfg, catalog=catalog)

    # Navigate to section 6 ("models")
    tui.drive(["6"])
    rows = tui._current_rows()
    assert len(rows) == 3
    assert [row.key for row in rows] == [
        "available.view.mode",
        "available.view.harness",
        "available.all",
    ]
    # Verify "All" row displays safely
    assert rows[2].key == "available.all"
    assert rows[2].label == "All"
    assert rows[2].current_value is True
    assert rows[2].value_text == "true"

    # Move cursor to "All" (index 2)
    tui.drive(["DOWN", "DOWN"])
    # Verify toggling does not crash
    tui.drive(["SPACE"])
    assert "Updated available.all" in tui.status_message
    rows = tui._current_rows()
    assert rows[2].current_value is False
    assert rows[2].value_text == "false"

    # Toggle again
    tui.drive(["SPACE"])
    assert "Updated available.all" in tui.status_message
    # Save does not crash
    tui.drive(["s"])
    assert tui.saved is True


def test_allowlist_hides_disabled_models_and_keeps_current(tmp_path: Path):
    seeded = HerdrMasterConfig()
    seeded.models.available = AvailableModels(
        herdr={
            "pi": ["openai-codex/gpt-6.1-sol"],
            "agy": ["gemini-3.8-flash"],
        },
        native={"agy": ["flash"]},
    )
    catalog = _offline_catalog(seeded)
    tui = SetupTuiApp(
        config_path=tmp_path / ".herdr-master.toml",
        catalog=catalog,
        cfg=seeded,
    )

    sentinel = catalog.models("claude", mode="herdr", current="sonnet")
    original_models = tui.catalog.models

    def _models(kind, mode="herdr", current=None):
        if kind == "claude" and mode == "herdr":
            return sentinel
        return original_models(kind, mode=mode, current=current)

    tui.catalog.models = _models
    filtered_claude, hint = tui._filtered_model_options(
        "claude", mode="herdr", current="sonnet"
    )
    assert filtered_claude is sentinel
    assert hint == ""
    tui.catalog.models = original_models

    tui.handle_key("1")
    rows = tui._current_rows()
    harness = next(row for row in rows if row.key == "aihead.kind")
    assert [opt.value for opt in harness.options] == [
        opt.value for opt in catalog.harnesses(mode="herdr")
    ]
    model = next(row for row in rows if row.key == "aihead.model")
    model_ids = [opt.value for opt in model.options]
    assert tui.cfg.ai_head.model in model_ids
    assert "xai/grok-4.7" not in model_ids
    assert "xai/grok-4.7" in [
        opt.value for opt in catalog.models("pi", mode="herdr", current=tui.cfg.ai_head.model)
    ]

    tui.handle_key("2")
    scout = next(row for row in tui._current_rows() if row.key == "ergo.scout.model")
    scout_ids = [opt.value for opt in scout.options]
    assert tui.squad.scout.model in scout_ids
    assert "gemini-3.8-flash" in scout_ids
    assert "gemini-3.8-flash-low" not in scout_ids

    tui.handle_key("3")
    orch = next(row for row in tui._current_rows() if row.key == "orch.scout_model")
    orch_ids = [opt.value for opt in orch.options]
    assert tui.cfg.orchestration.scout_model in orch_ids
    assert "flash" in orch_ids
    assert "pro" not in orch_ids
    native_harness = next(row for row in tui._current_rows() if row.key == "orch.harness")
    assert [opt.value for opt in native_harness.options] == [
        opt.value for opt in catalog.harnesses(mode="native")
    ]

    picker_cases = (
        (
            "4",
            "council.coord.model",
            "herdr",
            tui.cfg.council.coordinator.kind.value,
            tui.cfg.council.coordinator.model,
        ),
        (
            "5",
            "master.model",
            "herdr",
            tui.master_coordinator.kind.value,
            tui.master_coordinator.model,
        ),
        (
            "2",
            "pool.0.model",
            "herdr",
            tui.squad.worker_pool[0].kind.value,
            tui.squad.worker_pool[0].model,
        ),
    )
    for section, key, mode, kind, current in picker_cases:
        tui.handle_key(section)
        row = next(item for item in tui._current_rows() if item.key == key)
        expected, _empty = tui._filtered_model_options(kind, mode=mode, current=current)
        assert [opt.value for opt in row.options] == [opt.value for opt in expected]
        assert current in [opt.value for opt in row.options]


def test_empty_allowlist_keeps_current_and_sets_hint(tmp_path: Path):
    seeded = HerdrMasterConfig()
    seeded.models.available = AvailableModels(herdr={"pi": []})
    tui = SetupTuiApp(
        config_path=tmp_path / ".herdr-master.toml",
        catalog=_offline_catalog(seeded),
        cfg=seeded,
    )
    tui.handle_key("1")
    row = next(item for item in tui._current_rows() if item.key == "aihead.model")
    assert row.hint == "no models enabled in Available"
    assert [opt.value for opt in row.options] == [tui.cfg.ai_head.model]


def test_automode_space_persists_without_shifting_enabled(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    tui = SetupTuiApp(config_path=cfg, catalog=_offline_catalog())
    tui.handle_key("1")
    rows = tui._current_rows()
    assert [row.key for row in rows[:4]] == [
        "aihead.kind",
        "aihead.model",
        "aihead.effort",
        "aihead.enabled",
    ]
    assert [row.label for row in rows[:4]] == ["Harness", "Model", "Effort", "Enabled"]
    skip_at = next(
        index for index, row in enumerate(rows) if row.key == "aihead.dangerously_skip_permissions"
    )
    assert rows[skip_at + 1].key == "aihead.automode"
    assert rows[skip_at + 1].label == "Automode"

    # Same 1 → Harness → Model → Effort → Enabled path, then continue to Automode.
    tui.drive(["DOWN", "DOWN", "DOWN"])
    assert tui._current_row().key == "aihead.enabled"
    while tui._current_row().key != "aihead.automode":
        tui.handle_key("DOWN")
        assert tui._current_row().key != "aihead.enabled"
    tui.drive(["SPACE", "s"])

    assert tui.saved is True
    assert tui.cfg.ai_head.automode is True
    assert tui.cfg.ai_head.enabled is False
    data = _load(cfg)
    assert data["ai_head"]["automode"] is True
    assert data["ai_head"]["enabled"] is False


# ---------------------------------------------------------------------------
# Live catalog & CLI model discovery tests
# ---------------------------------------------------------------------------


def test_encoded_agy_effort_row_is_hidden_and_not_persisted(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "config.toml"
    tui = SetupTuiApp(config_path=cfg, catalog=_offline_catalog())
    assert tui.squad.scout.model == "gemini-3.8-flash-medium"
    assert tui.squad.scout.effort is None

    ergo_keys = [row.key for row in tui._section_rows("ergo")]
    assert "ergo.scout.effort" not in ergo_keys
    assert "ergo.architect.effort" in ergo_keys
    assert "ergo.reviewer.effort" in ergo_keys
    assert "orch.scout_effort" in [row.key for row in tui._section_rows("orchestration")]

    for model in (
        "gemini-3.8-flash-high",
        "gemini-3.8-flash-medium",
        "gemini-3.8-flash-low",
    ):
        tui._apply_field_value("ergo.scout.model", model)
        assert tui.squad.scout.model == model
        assert tui.squad.scout.effort is None
        assert "ergo.scout.effort" not in [row.key for row in tui._section_rows("ergo")]

    tui.squad.scout.model = "claude-opus-5-5-medium"
    tui.squad.scout.effort = EffortLevel.MEDIUM
    tui._clear_redundant_herdr_efforts()
    assert tui.squad.scout.effort is None
    assert "ergo.scout.effort" not in [row.key for row in tui._section_rows("ergo")]

    tui._apply_field_value("ergo.scout.model", "gemini-3.8-flash")
    assert tui.squad.scout.effort is not None
    flash_effort = next(row for row in tui._section_rows("ergo") if row.key == "ergo.scout.effort")
    assert flash_effort.current_value == tui.squad.scout.effort.value
    assert all(opt.value is not None for opt in flash_effort.options)

    for kind, model in (
        (HarnessKind.PI, "vendor/model-high"),
        (HarnessKind.OPENCODE, "opencode/model-low"),
        (HarnessKind.CLAUDE, "claude-opus-5-5-medium"),
        (HarnessKind.HERMES, "openai-codex/gpt-6.1-terra"),
        (HarnessKind.AGY, "gemini-3.8-flash"),
    ):
        binding = HarnessBinding(kind=kind, model=model, effort=EffortLevel.HIGH)
        rows = tui._binding_rows(kind.value, kind.value, binding)
        assert any(row.key.endswith(".effort") for row in rows)
        assert binding.effort == EffortLevel.HIGH
        assert _herdr_effort_separate(kind.value, model) is True

    monkeypatch.setattr(
        "herdr_master.cli.setup_tui._allowed_efforts",
        lambda kind, model, mode="herdr": [] if kind == "agy" else ["low", "medium", "high", "max"],
    )
    encoded = HarnessBinding(
        kind=HarnessKind.AGY, model="custom-agy-model", effort=EffortLevel.LOW
    )
    assert _herdr_effort_separate("agy", "custom-agy-model") is False
    hidden = tui._binding_rows("agy", "agy", encoded)
    assert all(not row.key.endswith(".effort") for row in hidden)
    tui._reconcile_binding(encoded)
    assert encoded.effort is None

    tui.squad.scout.kind = HarnessKind.AGY
    tui.squad.scout.model = "claude-opus-5-5-medium"
    tui.squad.scout.effort = EffortLevel.MEDIUM
    assert tui.save() is True
    assert _load(cfg)["models"]["default_squad"]["scout"] == "agy:claude-opus-5-5-medium"
    assert _load(cfg)["models"]["worker_pool"]["worker-1"]["binding"].startswith("agy:")


def test_live_model_parsers_and_catalog_modes():
    pi_out = (
        "provider  model  context  max-out  thinking  images\n"
        "litellm   free-model  200K  4.1K  yes  yes\n"
        "deepseek  deepseek-flash  1M  384K  yes  yes\n"
        "openai    gpt-4.1  1.0M  32.8K  no  yes\n"
    )
    pi_opts = _parse_pi_models(pi_out)
    assert [o.value for o in pi_opts] == ["deepseek/deepseek-flash", "openai/gpt-4.1"]

    agy_out = (
        "Fetching available models...\n"
        "gemini-4-argon-medium\tGemini 4 Argon (Medium)\n"
        "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)\n"
    )
    agy_opts = _parse_agy_models(agy_out)
    assert [o.value for o in agy_opts] == ["gemini-4-argon-medium", "gemini-3.8-flash-low"]

    oc_opts = _parse_opencode_models("opencode/beam-free\n\nopenai/gpt-6.1-sol\n")
    assert [o.value for o in oc_opts] == ["opencode/beam-free", "openai/gpt-6.1-sol"]

    cat = _offline_catalog()
    assert [o.value for o in cat.models("agy", mode="native")] == ["flash_lite", "flash", "pro"]
    herdr_agy = [o.value for o in cat.models("agy", mode="herdr")]
    assert "flash_lite" not in herdr_agy
    assert "gemini-3.8-flash-medium" in herdr_agy
    assert [o.value for o in cat.efforts("agy", "gemini-3.8-flash-medium", allow_none=False)] == [
        "medium"
    ]
    assert [o.value for o in cat.efforts("agy", "gemini-3.8-flash", allow_none=False)] == [
        "low",
        "medium",
        "high",
    ]


# ---------------------------------------------------------------------------
# Existing launcher & CLI flag tests
# ---------------------------------------------------------------------------


def test_master_binding_uses_config_and_explicit_override(tmp_path):
    from herdr_master.pipeline.launcher import resolve_master_binding

    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[master_coordinator]\nkind = "pi"\nmodel = "xai/grok-4.7"\neffort = "high"\n'
    )
    assert resolve_master_binding(config_path=cfg)[1] == "xai/grok-4.7"
    assert resolve_master_binding(model="xai/grok-4.8", config_path=cfg)[1] == "xai/grok-4.8"


def test_unified_ergo_config_is_used(tmp_path, monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    cfg = tmp_path / "config.toml"
    cfg.write_text('[models.default_squad]\nscout = "pi:xai/grok-4.7:high"\n')
    with patch("herdr_master.cli.main.launch_ergo", new_callable=AsyncMock) as launch:
        launch.return_value = {"workspace_id": "new", "pane_id": "new:p1"}
        result = runner.invoke(aihead_app, ["--ergo", "test", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["squad"].scout.model == "xai/grok-4.7"


def test_setup_cannot_be_combined_with_a_launch(tmp_path):
    cfg = tmp_path / "config.toml"
    result = runner.invoke(aihead_app, ["--setup", "--ergo", "task", "--config", str(cfg)])
    assert result.exit_code != 0
    assert not cfg.exists()


def test_skill_launch_rejects_missing_config(tmp_path):
    with patch("herdr_master.cli.main.launch_ergo", new_callable=AsyncMock) as launch:
        result = runner.invoke(
            aihead_app, ["--ergo", "test", "--config", str(tmp_path / "missing")]
        )
    assert result.exit_code != 0
    launch.assert_not_called()


def test_master_cli_explicit_overrides_are_forwarded(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    with patch("herdr_master.cli.main.launch_master", new_callable=AsyncMock) as launch:
        launch.return_value = {"workspace_id": "new", "pane_id": "new:p1"}
        result = runner.invoke(
            aihead_app,
            [
                "--herdr-master",
                "--harness",
                "pi",
                "--model",
                "xai/grok-4.7",
                "--effort",
                "high",
            ],
        )
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["initial_task"] is None
    assert launch.await_args.kwargs["harness"] == "pi"
    assert launch.await_args.kwargs["model"] == "xai/grok-4.7"
    assert launch.await_args.kwargs["effort"] == "high"
