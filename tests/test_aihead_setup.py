"""AiHead setup wizard and root launcher flags."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app, app

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


@pytest.mark.parametrize(
    "skill", ["aihead", "ergo", "orchestration", "council", "herdr-master", "all"]
)
def test_skill_setup_selects_and_saves_once(tmp_path, monkeypatch, skill):
    cfg = tmp_path / ".herdr-master.toml"
    cfg.write_text('[unrelated]\nvalue = "keep"\n', encoding="utf-8")
    changes = {
        "AiHead binding": "pi:xai/grok-4.7:high",
        "Ergo scout binding": "agy:gemini-3.8-flash:low",
        "Ergo workers": "agy:gemini-3.8-flash, pi:xai/grok-4.7",
        "Orchestration harness": "opencode",
        "Orchestration scout model": "opencode/beam-free",
        "Council coordinator binding": "pi:xai/grok-4.7:high",
        "Master coordinator binding": "pi:xai/grok-4.7:high",
    }

    def ask(label, default=None, **kwargs):
        if label == "Skill":
            return skill
        return next((value for key, value in changes.items() if label.startswith(key)), default)

    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", ask)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    from herdr_master.cli import aihead_setup
    with patch.object(
        aihead_setup, "save_project_config", wraps=aihead_setup.save_project_config
    ) as save:
        result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    save.assert_called_once()
    data = _load(cfg)
    assert data["unrelated"]["value"] == "keep"
    if skill in ("aihead", "all"):
        assert data["ai_head"]["binding"]["model"] == "xai/grok-4.7"
    if skill in ("ergo", "all"):
        assert data["models"]["default_squad"]["scout"] == "agy:gemini-3.8-flash:low"
        assert data["models"]["worker_pool"]["worker-2"]["binding"] == "pi:xai/grok-4.7"
    if skill in ("orchestration", "all"):
        assert data["orchestration"]["harness"] == "opencode"
        assert data["orchestration"]["scout_model"] == "opencode/beam-free"
    if skill in ("council", "all"):
        assert data["council"]["coordinator"]["model"] == "xai/grok-4.7"
    if skill in ("herdr-master", "all"):
        assert data["master_coordinator"]["model"] == "xai/grok-4.7"


@pytest.mark.parametrize("cancel,invalid", [(True, False), (False, True)])
def test_skill_setup_cancel_or_invalid_never_writes(tmp_path, monkeypatch, cancel, invalid):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")
    def ask(label, default=None, **kwargs):
        if label == "Skill":
            return "orchestration"
        if invalid and label == "Orchestration scout model":
            return "pi:xai/grok-4.7"
        return default
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", ask)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: not cancel)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code == (0 if cancel else 1), result.output
    assert cfg.read_text(encoding="utf-8") == original


def test_master_binding_uses_config_and_explicit_override(tmp_path):
    from herdr_master.pipeline.launcher import resolve_master_binding
    cfg = tmp_path / "config.toml"
    cfg.write_text('[master_coordinator]\nkind = "pi"\nmodel = "xai/grok-4.7"\neffort = "high"\n')
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


def test_skill_setup_interrupt_leaves_file_untouched(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", interrupt)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code != 0
    assert cfg.read_text(encoding="utf-8") == original


def test_setup_cannot_be_combined_with_a_launch(tmp_path):
    cfg = tmp_path / "config.toml"
    result = runner.invoke(aihead_app, ["--setup", "--ergo", "task", "--config", str(cfg)])
    assert result.exit_code != 0
    assert not cfg.exists()


def test_orchestration_switch_preserves_existing_profiles(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('''
[orchestration]
harness = "agy"
scout_model = "agy-old"
[orchestration.harnesses.agy]
scout_model = "agy-old"
custom = "keep-agy"
[orchestration.harnesses.opencode]
scout_model = "opencode/old"
architect_model = "openai/arch"
reviewer_model = "openai/review"
worker_models = ["opencode/worker"]
custom = "keep-opencode"
''')
    seen = {}
    def ask(label, default=None, **kwargs):
        if label == "Skill":
            return "orchestration"
        if label == "Orchestration harness":
            return "opencode"
        seen[label] = default
        return default
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", ask)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert seen["Orchestration scout model"] == "opencode/old"
    orch = _load(cfg)["orchestration"]
    assert orch["harnesses"]["agy"]["custom"] == "keep-agy"
    assert orch["harnesses"]["opencode"]["custom"] == "keep-opencode"
    assert orch["harnesses"]["opencode"]["scout_model"] == "opencode/old"


def test_setup_cwd_anchors_config(tmp_path, monkeypatch):
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask",
                        lambda label, default=None, **kw: "aihead" if label == "Skill" else default)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    result = runner.invoke(aihead_app, ["--setup", "--cwd", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / ".herdr-master.toml").is_file()


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
        result = runner.invoke(aihead_app, ["--herdr-master", "test", "--harness", "pi",
                                           "--model", "xai/grok-4.7", "--effort", "high"])
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["harness"] == "pi"
    assert launch.await_args.kwargs["model"] == "xai/grok-4.7"
    assert launch.await_args.kwargs["effort"] == "high"


def test_failed_atomic_replace_preserves_file(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask",
                        lambda label, default=None, **kw: "aihead" if label == "Skill" else default)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    with patch("herdr_master.core.config.os.replace", side_effect=OSError("disk failure")):
        result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code != 0
    assert cfg.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".config.toml.*.tmp"))


@pytest.mark.parametrize("skill,label,binding", [
    ("herdr-master", "Master coordinator binding", "agy:gemini-3.8-flash-low:high"),
    ("ergo", "Ergo scout binding", "pi:litellm/example:high"),
])
def test_setup_rejects_unlaunchable_binding(tmp_path, monkeypatch, skill, label, binding):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original, encoding="utf-8")
    def ask(question, default=None, **kwargs):
        if question == "Skill":
            return skill
        return binding if question.startswith(label) else default
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", ask)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code != 0, result.output
    assert cfg.read_text(encoding="utf-8") == original


def test_ergo_preserves_extra_worker_metadata(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[models.worker_pool.worker-1]\nbinding = "pi:xai/grok-4.7"\ncustom = "keep"\n')
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask",
                        lambda label, default=None, **kw: "ergo" if label == "Skill" else default)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert _load(cfg)["models"]["worker_pool"]["worker-1"]["custom"] == "keep"


@pytest.mark.parametrize("skill,label", [
    ("aihead", "AiHead binding"), ("council", "Council coordinator binding"),
])
def test_setup_rejects_unlaunchable_head_or_council(tmp_path, monkeypatch, skill, label):
    cfg = tmp_path / "config.toml"
    original = '[unrelated]\nvalue = "keep"\n'
    cfg.write_text(original)
    def ask(question, default=None, **kwargs):
        if question == "Skill":
            return skill
        return "agy:gemini-3.8-flash" if question.startswith(label) else default
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", ask)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code != 0
    assert cfg.read_text() == original


def test_orchestration_uses_native_defaults_and_rejects_invalid_tiers(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    def ask(question, default=None, **kwargs):
        return "orchestration" if question == "Skill" else default
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", ask)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert _load(cfg)["orchestration"]["scout_model"] == "flash_lite"
    original = cfg.read_text()
    def invalid(question, default=None, **kwargs):
        if question == "Orchestration scout model":
            return "gemini-3.8-flash"
        return ask(question, default, **kwargs)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask", invalid)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code != 0
    assert cfg.read_text() == original


def test_setup_preserves_unrelated_binding_extra_fields(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text('''
[ai_head.binding]
kind = "pi"
model = "xai/grok-4.7"
custom = "preserve"
''')
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Prompt.ask",
                        lambda label, default=None, **kw: "ergo" if label == "Skill" else default)
    monkeypatch.setattr("herdr_master.cli.aihead_setup.Confirm.ask", lambda *a, **kw: True)
    result = runner.invoke(aihead_app, ["--setup", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert _load(cfg)["ai_head"]["binding"]["custom"] == "preserve"
