"""Tests for `herdm catalog available` command."""

import json
from pathlib import Path

from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.core.catalog import supported_harnesses
from herdr_master.core.config import (
    AvailableModels,
    HerdrMasterConfig,
    save_project_config,
)


def _json(result):
    stdout = result.stdout if result.stdout else result.output
    return json.loads(stdout)


def test_catalog_show_unchanged_and_no_available_key():
    runner = CliRunner()
    result = runner.invoke(catalog_app, ["show", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["default_squad"]["scout"]["model"] == "gemini-3.8-flash-medium"
    assert "available" not in payload


def test_catalog_available_omitted_project():
    runner = CliRunner()
    result = runner.invoke(catalog_app, ["available", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["restricted"] is False
    assert payload["automode"] is False
    assert payload["available"] is None
    assert set(payload["modes"].keys()) == {"herdr", "native"}

    for mode in ("herdr", "native"):
        for harness in supported_harnesses(mode=mode):
            assert harness in payload["modes"][mode]
            info = payload["modes"][mode][harness]
            assert isinstance(info["discovered"], list)
            assert len(info["discovered"]) > 0
            assert info["enabled"] is None


def test_catalog_available_toml_no_allowlist(tmp_path: Path):
    path = tmp_path / ".herdr-master.toml"
    path.write_text(
        '[general]\ndefault_session = "default"\n\n[ai_head]\nenabled = true\n',
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(catalog_app, ["available", "--project", str(path), "--json"])
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["restricted"] is False
    assert payload["automode"] is False
    assert payload["available"] is None

    for mode in ("herdr", "native"):
        for harness in supported_harnesses(mode=mode):
            assert payload["modes"][mode][harness]["enabled"] is None


def test_catalog_available_saved_ids_and_automode(tmp_path: Path):
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={"pi": ["xai/grok-4.7"], "claude": []},
        native={"agy": ["flash", "pro"]},
    )
    cfg.ai_head.automode = True
    path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, path)

    runner = CliRunner()
    result = runner.invoke(catalog_app, ["available", "--project", str(path), "--json"])
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["restricted"] is True
    assert payload["automode"] is True
    assert payload["available"] is not None
    assert payload["available"]["herdr"]["pi"] == ["xai/grok-4.7"]
    assert payload["available"]["herdr"]["claude"] == []
    assert payload["available"]["native"]["agy"] == ["flash", "pro"]

    modes = payload["modes"]
    assert modes["herdr"]["pi"]["enabled"] == ["xai/grok-4.7"]
    assert modes["herdr"]["claude"]["enabled"] == []
    assert modes["herdr"]["agy"]["enabled"] is None
    assert modes["native"]["agy"]["enabled"] == ["flash", "pro"]
    assert modes["native"]["pi"]["enabled"] is None


def test_catalog_available_directory_project(tmp_path: Path):
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={"pi": ["xai/grok-4.7"]},
    )
    save_project_config(cfg, tmp_path / ".herdr-master.toml")

    runner = CliRunner()
    result = runner.invoke(catalog_app, ["available", "--project", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["restricted"] is True
    assert payload["modes"]["herdr"]["pi"]["enabled"] == ["xai/grok-4.7"]


def test_catalog_available_pretty_text_mode(tmp_path: Path):
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={"pi": ["xai/grok-4.7"], "claude": []},
    )
    cfg.ai_head.automode = True
    path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, path)

    runner = CliRunner()
    result = runner.invoke(catalog_app, ["available", "--project", str(path)])
    assert result.exit_code == 0, result.output
    output = result.stdout or result.output
    assert "\x1b" not in output
    assert "restricted=true automode=true" in output
    assert "herdr.pi: enabled=xai/grok-4.7" in output
    assert "herdr.claude: enabled=none" in output
    assert "herdr.agy: enabled=unrestricted" in output


def test_catalog_available_error_cases(tmp_path: Path):
    runner = CliRunner()

    missing_file = tmp_path / "nonexistent.toml"
    res1 = runner.invoke(catalog_app, ["available", "--project", str(missing_file), "--json"])
    assert res1.exit_code != 0
    err1 = _json(res1)
    assert err1["ok"] is False
    assert "not found" in err1["error"]

    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    res2 = runner.invoke(catalog_app, ["available", "--project", str(empty_dir), "--json"])
    assert res2.exit_code != 0
    err2 = _json(res2)
    assert err2["ok"] is False
    assert "not found" in err2["error"]

    invalid_toml = tmp_path / "invalid.toml"
    invalid_toml.write_text("this is not valid toml = [[[\n", encoding="utf-8")
    res3 = runner.invoke(catalog_app, ["available", "--project", str(invalid_toml), "--json"])
    assert res3.exit_code != 0
    err3 = _json(res3)
    assert err3["ok"] is False
