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
from herdr_master.cli.setup_tui import SECTIONS, SetupTuiApp

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

    # Initial screen lists all 6 sections on one screen.
    initial = tui.render(width=110, height=32)
    for _, sec_label in SECTIONS:
        assert sec_label in initial

    # 1. aihead options
    tui.handle_key("1")
    aihead_screen = tui.render(width=110, height=32)
    for label in ("Harness", "Model", "Effort", "Enabled", "Mode", "Split Pane", "Autonomy"):
        assert label in aihead_screen

    # 2. ergo options
    tui.handle_key("2")
    ergo_screen = tui.render(width=110, height=32)
    for label in ("Coordinator", "Scout", "Architect", "Reviewer"):
        assert label in ergo_screen

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
    for label in ("Override Mode", "Harness", "Model", "Effort"):
        assert label in master_screen

    # 6. worker-pool options
    tui.handle_key("6")
    pool_screen = tui.render(width=110, height=32)
    for label in ("worker-1 Harness", "worker-1 Model", "worker-1 Enabled", "+ Add Worker"):
        assert label in pool_screen


def test_tui_driven_without_terminal_reaches_save_functions_via_herdm_aihead_setup(
    tmp_path: Path, monkeypatch
):
    cfg = tmp_path / ".herdr-master.toml"
    cfg.write_text('[unrelated]\nvalue = "keep"\n', encoding="utf-8")

    # Drive TUI headlessly:
    # Section 1 (aihead): pick harness 'pi', pick model 'xai/grok-4.7', pick effort 'high',
    # toggle Enabled -> true.
    # Section 2 (ergo): pick Scout harness 'agy', model 'gemini-3.8-flash-low' (effort snaps 'low').
    # Section 6 (worker-pool): add a worker, then press 's' to save once.
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
        "6",
        "a",  # add worker
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
    assert data["models"]["default_squad"]["scout"] == "agy:gemini-3.8-flash-low:low"
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
    # Disable both workers in worker-pool and attempt to save -> validation error!
    tui.drive(
        [
            "6",
            "DOWN", "DOWN", "SPACE",  # worker-1 Enabled -> false
            "DOWN", "DOWN", "DOWN", "SPACE",  # worker-2 Enabled -> false
            "s",
        ]
    )
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


# ---------------------------------------------------------------------------
# Live catalog & CLI model discovery tests
# ---------------------------------------------------------------------------


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


def test_unified_ergo_config_is_used(tmp_path):
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


def test_master_cli_explicit_overrides_are_forwarded():
    with patch("herdr_master.cli.main.launch_master", new_callable=AsyncMock) as launch:
        launch.return_value = {"workspace_id": "new", "pane_id": "new:p1"}
        result = runner.invoke(
            aihead_app,
            [
                "--herdr-master",
                "test",
                "--harness",
                "pi",
                "--model",
                "xai/grok-4.7",
                "--effort",
                "high",
            ],
        )
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["harness"] == "pi"
    assert launch.await_args.kwargs["model"] == "xai/grok-4.7"
    assert launch.await_args.kwargs["effort"] == "high"
