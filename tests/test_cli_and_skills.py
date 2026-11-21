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
