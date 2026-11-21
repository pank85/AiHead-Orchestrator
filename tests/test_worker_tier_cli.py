import json

from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.core.config import HerdrMasterConfig, load_project_config, save_project_config
from herdr_master.core.model_ratings import ModelRating, derive_worker_tier


def test_tier_locks_independent_and_preserve_extra(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = HerdrMasterConfig()
    rating = ModelRating(
        intelligence=75,
        coding=76,
        agentic=77,
        speed=78,
        cost_efficiency=79,
        context_handling=80,
        source="benchmark",
        custom_origin="kept",
        manual_override=True,
    )
    cfg.models.ratings = {"pi": {"provider/model": rating}}
    path = save_project_config(cfg, tmp_path / ".herdr-master.toml")
    runner = CliRunner()
    base = ["rate", "--binding", "pi:provider/model", "--project", str(path), "--json"]
    result = runner.invoke(catalog_app, base + ["--worker-tier", "1"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["rating"]
    assert data["worker_tier"] == 1 and data["worker_tier_override"]
    assert data["manual_override"] and data["custom_origin"] == "kept"
    assert data["source"] == "benchmark" and data["coding"] == 76
    result = runner.invoke(catalog_app, base + ["--clear-override"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["rating"]["worker_tier_override"]
    result = runner.invoke(catalog_app, base + ["--clear-worker-tier-override"])
    assert result.exit_code == 0, result.output
    saved = load_project_config(path).models.ratings["pi"]["provider/model"]
    assert not saved.worker_tier_override and not saved.manual_override
    assert saved.worker_tier == derive_worker_tier(saved)
    assert saved.custom_origin == "kept"
    before = path.read_bytes()
    for args in (["--worker-tier", "4"], ["--worker-tier", "1", "--clear-worker-tier-override"]):
        assert runner.invoke(catalog_app, base + args).exit_code != 0
        assert path.read_bytes() == before
    table = runner.invoke(catalog_app, ["ratings", "--project", str(path)])
    assert "billing_tier=api" in table.stdout and "worker_tier=" in table.stdout


def test_tier_only_new_record_does_not_lock_scores(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = save_project_config(HerdrMasterConfig(), tmp_path / ".herdr-master.toml")
    result = CliRunner().invoke(
        catalog_app,
        [
            "rate",
            "--binding",
            "pi:provider/model",
            "--project",
            str(path),
            "--worker-tier",
            "2",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["rating"]
    assert data["worker_tier_override"] and not data["manual_override"]
