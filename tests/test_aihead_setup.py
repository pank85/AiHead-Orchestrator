"""AiHead setup wizard and root launcher flags."""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from typer.testing import CliRunner

from herdr_master.cli.main import app

runner = CliRunner()


def _load(path: Path) -> dict:
    with open(path, "rb") as handle:
        return tomllib.load(handle)


def test_defaults_writes_expected_role_models(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    result = runner.invoke(
        app, ["aihead-setup", "--defaults", "--yes", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output

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

    result = runner.invoke(
        app, ["--aihead-setup", "--defaults", "--yes", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output

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
    assert _load(cfg)["models"]["default_squad"]["scout"] == "opencode:opencode/beam-free:medium"


def test_invalid_effort_exits_nonzero_and_keeps_file(tmp_path: Path, monkeypatch):
    cfg = tmp_path / ".herdr-master.toml"
    original = '[general]\ndefault_session = "default"\n'
    cfg.write_text(original, encoding="utf-8")

    answers = ["opencode", "opencode/beam-free", "banana"]
    monkeypatch.setattr(
        "herdr_master.cli.aihead_setup._ask",
        lambda label, default: answers.pop(0) if answers else default,
    )

    result = runner.invoke(app, ["aihead-setup", "--yes", "--config", str(cfg)])

    assert result.exit_code != 0
    assert "effort" in result.output.lower()
    assert cfg.read_text(encoding="utf-8") == original


def test_bare_invocation_and_aihead_help_exit_zero():
    bare = runner.invoke(app, [])
    assert bare.exit_code == 0, bare.output
    assert "--aihead" in bare.output

    help_result = runner.invoke(app, ["--aihead", "--help"])
    assert help_result.exit_code == 0, help_result.output
