"""Essential tests for CLI entrypoints, catalog rate CLI, and per-harness skill composition."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app
from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import HerdrMasterConfig, load_project_config, save_project_config
from herdr_master.core.model_ratings import ModelRating
from herdr_master.core.skill_resources import load_skill_body, render_skill_for_harness


def test_cli_help_and_core_backend_guard(monkeypatch) -> None:
    runner = CliRunner()
    for args in (
        ["--help"],
        ["catalog", "--help"],
        ["worker", "--help"],
        ["skills", "--help"],
    ):
        res = runner.invoke(aihead_app, args)
        assert res.exit_code == 0, f"{args} failed: {res.output}"

    # Inside a Core terminal, implicit legacy launch must fail closed
    monkeypatch.setenv("HC_TERMINAL_SOCKET", "/tmp/core-test.sock")
    res = runner.invoke(aihead_app, ["--no-attach"])
    assert res.exit_code != 0
    assert "backend core" in res.output.lower()


def test_catalog_rate_worker_tier_cli_and_mutual_exclusion(isolated_home: Path) -> None:
    runner = CliRunner()
    project_dir = isolated_home / "proj"
    project_dir.mkdir()
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(herdr={"pi": ["openai/gpt-6.1-sol"]})
    cfg.models.ratings = {
        "pi": {
            "openai/gpt-6.1-sol": ModelRating(
                intelligence=93,
                coding=94,
                agentic=92,
                speed=70,
                cost_efficiency=65,
                context_handling=95,
                family="openai",
            )
        }
    }
    cfg_path = save_project_config(cfg, project_dir / ".herdr-master.toml")

    # Lock worker tier without locking scores
    r_lock = runner.invoke(
        aihead_app,
        [
            "catalog",
            "rate",
            "--project",
            str(cfg_path),
            "--binding",
            "pi:openai/gpt-6.1-sol",
            "--worker-tier",
            "2",
        ],
    )
    assert r_lock.exit_code == 0, r_lock.output
    reloaded = load_project_config(cfg_path)
    rating = reloaded.models.ratings["pi"]["openai/gpt-6.1-sol"]
    assert rating.worker_tier == 2
    assert rating.worker_tier_override is True
    assert rating.manual_override is False

    # Combining set + clear must fail
    r_conflict = runner.invoke(
        aihead_app,
        [
            "catalog",
            "rate",
            "--project",
            str(cfg_path),
            "--binding",
            "pi:openai/gpt-6.1-sol",
            "--worker-tier",
            "1",
            "--clear-worker-tier-override",
        ],
    )
    assert r_conflict.exit_code != 0

    # Clear worker tier override recomputes tier locally
    r_clear = runner.invoke(
        aihead_app,
        [
            "catalog",
            "rate",
            "--project",
            str(cfg_path),
            "--binding",
            "pi:openai/gpt-6.1-sol",
            "--clear-worker-tier-override",
        ],
    )
    assert r_clear.exit_code == 0, r_clear.output
    cleared = load_project_config(cfg_path).models.ratings["pi"]["openai/gpt-6.1-sol"]
    assert cleared.worker_tier == 3
    assert cleared.worker_tier_override is False

    # Available JSON reports ratings cleanly
    r_avail = runner.invoke(
        aihead_app,
        ["catalog", "available", "--project", str(cfg_path), "--json"],
    )
    assert r_avail.exit_code == 0, r_avail.output
    payload = json.loads(r_avail.output)
    assert payload["unrated_models"] == {}


def test_skill_composition_and_install_remove_in_isolated_home(isolated_home: Path) -> None:
    base = load_skill_body("orchestration")
    composed_agy = render_skill_for_harness("orchestration", "agy")
    assert len(composed_agy) > len(base)
    assert "swarm" in composed_agy.lower()

    runner = CliRunner()
    r_install = runner.invoke(aihead_app, ["skills", "install", "--harness", "agy", "--force"])
    assert r_install.exit_code == 0, r_install.output

    installed = (
        isolated_home / ".gemini" / "antigravity-cli" / "skills" / "orchestration" / "SKILL.md"
    )
    assert installed.exists()

    r_remove = runner.invoke(aihead_app, ["skills", "remove"])
    assert r_remove.exit_code == 0, r_remove.output
    assert not installed.exists()


def test_setup_tui_worker_tier_picker_and_save_stays_open(isolated_home: Path) -> None:
    from herdr_master.cli.setup_catalog import ChoiceOption, ModelCatalog
    from herdr_master.cli.setup_tui import SetupTuiApp

    cfg_path = isolated_home / ".herdr-master.toml"
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(herdr={"pi": ["openai/gpt-6.1-sol"]})
    save_project_config(cfg, cfg_path)

    catalog = ModelCatalog(
        herdr_harnesses=[ChoiceOption("pi", "pi")],
        native_harnesses=[],
        herdr_models={"pi": [ChoiceOption("openai/gpt-6.1-sol", "openai/gpt-6.1-sol")]},
        native_models={},
    )
    app = SetupTuiApp(config_path=cfg_path, catalog=catalog)
    app.handle_key("6")
    assert app.current_section == "models"
    app.focus_pane = "options"
    rows = app._current_rows()
    model_idx = next(i for i, r in enumerate(rows) if r.key == "available.toggle.0")
    app.field_cursors["models"] = model_idx

    # Open tier picker with 't', pick Tier 3 (Frontier), confirm with Enter
    app.handle_key("t")
    assert app.picker is not None
    assert [opt.value for opt in app.picker.options] == [1, 2, 3, "auto"]
    app.handle_key("DOWN")
    app.handle_key("DOWN")
    app.handle_key("ENTER")
    assert app.picker is None
    assert app.dirty is True

    # Save with 's' must persist and NOT close
    app.handle_key("s")
    assert app.done is False
    assert app.saved is True
    assert app.dirty is False

    reloaded = load_project_config(cfg_path)
    saved_rating = reloaded.models.ratings["pi"]["openai/gpt-6.1-sol"]
    assert saved_rating.worker_tier == 3
    assert saved_rating.worker_tier_override is True

    # Only 'q' closes, preserving saved=True
    app.handle_key("q")
    assert app.done is True
    assert app.saved is True


def test_setup_tui_harnesses_tab_and_agy_grouped_models(isolated_home: Path) -> None:
    from herdr_master.cli.setup_catalog import ChoiceOption, ModelCatalog
    from herdr_master.cli.setup_tui import SetupTuiApp

    cfg_path = isolated_home / ".herdr-master.toml"
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={
            "agy": [
                "gemini-4-neon-low",
                "gemini-4-neon-medium",
                "gemini-4-neon-high",
                "claude-opus-5-5-high",
            ]
        }
    )
    save_project_config(cfg, cfg_path)

    catalog = ModelCatalog(
        herdr_harnesses=[ChoiceOption("agy", "agy")],
        native_harnesses=[],
        herdr_models={
            "agy": [
                ChoiceOption("gemini-4-neon-low", "gemini-4-neon-low"),
                ChoiceOption("gemini-4-neon-medium", "gemini-4-neon-medium"),
                ChoiceOption("gemini-4-neon-high", "gemini-4-neon-high"),
                ChoiceOption("claude-opus-5-5-high", "claude-opus-5-5-high"),
            ]
        },
        native_models={},
    )
    app = SetupTuiApp(config_path=cfg_path, catalog=catalog)

    # Header shows resolved path (~/.herdr-master.toml), not bare filename
    rendered = app.render(width=100, height=30)
    assert "~/.herdr-master.toml" in rendered

    # [6] Available groups agy effort variants into base models
    app.handle_key("6")
    model_rows = [r for r in app._current_rows() if r.key.startswith("available.toggle.")]
    assert [r.label for r in model_rows] == ["gemini-4-neon", "claude-opus-5-5"]
    assert "T2 Everyday" in model_rows[0].badge
    assert "T3 Frontier" in model_rows[1].badge

    # Toggling base model off removes all its effort variants
    neon_idx = next(
        i for i, r in enumerate(app._current_rows()) if r.key == "available.toggle.0"
    )
    app.field_cursors["models"] = neon_idx
    app.handle_key("SPACE")
    assert app.cfg.models.available is not None
    assert app.cfg.models.available.herdr["agy"] == ["claude-opus-5-5-high"]

    # [7] Harnesses shows installed/not installed and toggles harness on/off
    app.handle_key("7")
    assert app.current_section == "harnesses"
    h_rows = app._current_rows()
    assert len(h_rows) >= 7
    assert all(r.badge in ("[installed]", "[not installed]") for r in h_rows)
    first_harness = h_rows[0].key.split(".", 1)[1]
    app.handle_key("SPACE")
    assert app.cfg.models.harnesses[first_harness] is False

    app.handle_key("s")
    reloaded = load_project_config(cfg_path)
    assert reloaded.models.harnesses[first_harness] is False
