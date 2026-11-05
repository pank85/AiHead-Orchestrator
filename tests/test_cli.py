"""CLI registration, one resolver pass, and PATH plugin commands."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import tomllib
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.core.models import EffortLevel, HarnessKind
from herdr_master.pipeline.squad import resolve_squad

runner = CliRunner()
ROOT = Path(__file__).resolve().parents[1]


def _launch_result():
    return {
        "session": "alpha",
        "workspace_id": "w9",
        "pane_id": "w9:p2",
        "orchestrator_pane_id": "w9:p2",
        "scout_pane_id": "w9:p2",
    }


def test_catalog_is_registered_and_json_has_no_ansi():
    result = runner.invoke(app, ["catalog", "show", "--json"])
    assert result.exit_code == 0, result.output
    raw = result.stdout.strip() or result.output.strip()
    payload = json.loads(raw)
    assert payload["default_mode"] == "herdr"
    assert "\x1b" not in raw


def test_master_help_uses_catalog_harnesses():
    result = runner.invoke(app, ["master", "start", "--help"])
    assert result.exit_code == 0
    assert "codex" in result.output
    assert "gemini" in result.output
    assert "gemini-3.8-flash-medium" in result.output
    assert "gemini-3.8-flash-high" not in result.output
    assert "--effort" in result.output
    ergo = runner.invoke(app, ["ergo", "--help"])
    pipeline = runner.invoke(app, ["pipeline", "ergo", "--help"])
    assert "--run-squad" in ergo.output
    assert "--run-squad" in pipeline.output
    assert "--coder" in ergo.output


def test_master_start_no_override_uses_scout_medium(monkeypatch):
    monkeypatch.chdir(Path("/tmp"))
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.launch_master", launch),
        patch("herdr_master.cli.main.os.execvp") as execvp,
    ):
        result = runner.invoke(app, ["master", "start", "--no-attach"])
    assert result.exit_code == 0, result.output
    kwargs = launch.await_args.kwargs
    assert kwargs["harness"] == HarnessKind.AGY
    assert kwargs["model"] == "gemini-3.8-flash-medium"
    assert kwargs["effort"] == EffortLevel.MEDIUM
    assert kwargs["session"] is None
    assert "Scout active" not in result.output
    execvp.assert_not_called()


def test_other_harness_requires_model_before_client():
    launch = AsyncMock()
    with (
        patch("herdr_master.cli.main.HerdrClient") as client,
        patch("herdr_master.cli.main.launch_master", launch),
    ):
        result = runner.invoke(app, ["master", "start", "--harness", "pi", "--no-attach"])
    assert result.exit_code != 0
    client.assert_not_called()
    launch.assert_not_called()
    explicit = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.launch_master", explicit),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        ok = runner.invoke(
            app,
            [
                "master",
                "start",
                "--harness",
                "pi",
                "--model",
                "xai/grok-4.7",
                "--effort",
                "max",
                "--no-attach",
            ],
        )
    assert ok.exit_code == 0, ok.output
    assert explicit.await_args.kwargs["harness"] == HarnessKind.PI
    assert explicit.await_args.kwargs["model"] == "xai/grok-4.7"
    assert explicit.await_args.kwargs["effort"] == EffortLevel.MAX


def test_ergo_and_alias_share_one_product_resolution(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.resolve_squad", wraps=resolve_squad) as spy,
        patch("herdr_master.cli.main.launch_ergo", launch),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        first = runner.invoke(app, ["ergo", "ship it", "--no-attach"])
        second = runner.invoke(app, ["pipeline", "ergo", "ship it", "--no-attach"])
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert spy.call_count == 2
    squads = [call.kwargs["squad"] for call in launch.await_args_list]
    direct = resolve_squad(tmp_path).squad
    assert squads[0] == squads[1] == direct
    assert squads[0].scout.model == "gemini-3.8-flash-medium"
    assert squads[0].scout.effort == EffortLevel.MEDIUM
    assert squads[0].architect.model == "openai-codex/gpt-6.1-sol"
    assert squads[0].architect.effort == EffortLevel.MAX
    assert squads[0].reviewer.model == "xai/grok-4.7"
    assert squads[0].reviewer.effort == EffortLevel.HIGH
    assert [worker.model for worker in squads[0].worker_pool] == [
        "gemini-3.8-flash",
        "xai/grok-4.7",
    ]
    assert all(worker.effort is None for worker in squads[0].worker_pool)
    rendered = str(squads[0])
    assert "claude-3-opus" not in rendered
    assert "gemini-4-argon" not in rendered
    assert "litellm" not in rendered
    assert "Scout active" not in first.output
    assert "orchestrator started" in first.output
    assert launch.await_args_list[0].kwargs["session"] is None


def test_run_squad_and_flags_use_one_pass(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    snapshot = tmp_path / "squad.toml"
    snapshot.write_text(
        '[roles]\nscout = "agy:gemini-3.8-flash-high"\n',
        encoding="utf-8",
    )
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.resolve_squad", wraps=resolve_squad) as spy,
        patch("herdr_master.cli.main.launch_ergo", launch),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        result = runner.invoke(
            app,
            [
                "ergo",
                "ship it",
                "--no-attach",
                "--run-squad",
                str(snapshot),
                "--architect",
                "pi:xai/grok-4.7:high",
            ],
        )
    assert result.exit_code == 0, result.output
    assert spy.call_count == 1
    squad = launch.await_args.kwargs["squad"]
    assert squad.scout.model == "gemini-3.8-flash-high"
    assert squad.architect.model == "xai/grok-4.7"
    assert squad.architect.effort == EffortLevel.HIGH
    assert squad.reviewer.model == "xai/grok-4.7"


def test_invalid_override_and_session_mismatch_skip_client(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_SOCKET_PATH", "/tmp/sessions/alpha/herdr.sock")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w9")
    with (
        patch("herdr_master.cli.main.HerdrClient") as client,
        patch("herdr_master.cli.main.launch_ergo") as launch,
    ):
        bad = runner.invoke(app, ["ergo", "ship it", "--scout", "nope", "--no-attach"])
        mismatch = runner.invoke(
            app, ["ergo", "ship it", "--session", "beta", "--no-attach"]
        )
        missing = runner.invoke(
            app,
            ["ergo", "ship it", "--run-squad", "/tmp/missing-squad.toml", "--no-attach"],
        )
    assert bad.exit_code != 0
    assert mismatch.exit_code != 0
    assert missing.exit_code != 0
    client.assert_not_called()
    launch.assert_not_called()


def test_omitted_session_preserves_inherited_socket(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_SOCKET_PATH", "/tmp/sessions/alpha/herdr.sock")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w9")
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.HerdrClient") as client,
        patch("herdr_master.cli.main.launch_ergo", launch),
        patch("herdr_master.cli.main.os.execvp") as execvp,
    ):
        result = runner.invoke(app, ["ergo", "ship it", "--no-attach"])
    assert result.exit_code == 0, result.output
    assert client.call_args.kwargs["session"] == "alpha"
    assert client.call_args.kwargs["socket_path"] == Path("/tmp/sessions/alpha/herdr.sock")
    assert launch.await_args.kwargs["session"] is None
    execvp.assert_not_called()


def test_coder_alias_and_disagreement(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.launch_ergo", launch),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        ok = runner.invoke(
            app,
            ["ergo", "ship it", "--coder", "pi:xai/grok-4.7", "--no-attach"],
        )
    assert ok.exit_code == 0, ok.output
    pool = launch.await_args.kwargs["squad"].worker_pool
    assert [worker.model for worker in pool] == ["xai/grok-4.7"]
    assert pool[0].effort is None
    with patch("herdr_master.cli.main.HerdrClient") as client:
        bad = runner.invoke(
            app,
            [
                "ergo",
                "ship it",
                "--workers",
                "agy:gemini-3.8-flash",
                "--coder",
                "pi:xai/grok-4.7",
                "--no-attach",
            ],
        )
    assert bad.exit_code != 0
    client.assert_not_called()


def test_tui_omitted_session_is_not_explicit_default():
    with patch("herdr_master.ui.tui.app.HerdrMasterApp") as app_cls:
        app_cls.return_value.run.return_value = None
        result = runner.invoke(app, ["tui"])
    assert result.exit_code == 0, result.output
    assert app_cls.call_args.kwargs["session"] is None
    assert app_cls.call_args.kwargs["session"] != "default"


def test_plugin_commands_are_path_entrypoints():
    text = (ROOT / "herdr-plugin.toml").read_text(encoding="utf-8")
    assert "/home/pank" not in text
    data = tomllib.loads(text)
    assert data["panes"][0]["command"] == ["herdm", "tui"]
    new_workspace = next(item for item in data["actions"] if item["id"] == "new-workspace")
    assert new_workspace["command"] == ["herdm", "workspace"]
    for section in data["panes"] + data["actions"]:
        command = section["command"]
        assert command[0] in {"herdm", "herdr"}
        assert all(not str(part).startswith("/") for part in command)


def test_launch_workspace_shim_forwards(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    capture = tmp_path / "args"
    fake = bindir / "herdm"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n', encoding="utf-8")
    fake.chmod(0o755)
    work = tmp_path / "work space"
    rel = work / "rel"
    rel.mkdir(parents=True)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
    env["CAPTURE"] = str(capture)
    subprocess.run(
        ["bash", str(ROOT / "scripts" / "launch-workspace.sh"), "my label", "rel"],
        check=True,
        cwd=work,
        env=env,
    )
    lines = capture.read_text(encoding="utf-8").splitlines()
    assert lines == ["workspace", "--label", "my label", "--cwd", str(rel.resolve())]


def test_workspace_command_uses_typed_client_not_ergo(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    created = {"workspace": {"workspace_id": "w-created"}, "root_pane": {"pane_id": "w-created:p1"}}
    instance = AsyncMock()
    instance.create_workspace.return_value = created
    with (
        patch("herdr_master.cli.main.HerdrClient", return_value=instance) as client,
        patch("herdr_master.cli.main.subprocess.run") as opened,
        patch("herdr_master.cli.main.launch_ergo") as launch,
    ):
        opened.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        result = runner.invoke(
            app,
            ["workspace", "--label", "desk", "--cwd", str(tmp_path)],
        )
    assert result.exit_code == 0, result.output
    instance.create_workspace.assert_awaited()
    assert instance.create_workspace.await_args.kwargs["label"] == "desk"
    assert instance.create_workspace.await_args.kwargs["cwd"] == str(tmp_path)
    argv = opened.call_args.args[0]
    assert argv[:4] == ["herdr", "plugin", "pane", "open"]
    assert "w-created" in argv
    assert "devswha.herdr-master" in argv
    launch.assert_not_called()
    client.assert_called_once()


def test_workspace_failures_do_not_open_or_launch(monkeypatch):
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_SOCKET_PATH", "/tmp/sessions/alpha/herdr.sock")
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w9")
    with (
        patch("herdr_master.cli.main.HerdrClient") as client,
        patch("herdr_master.cli.main.subprocess.run") as opened,
    ):
        relative = runner.invoke(app, ["workspace", "--cwd", "relative"])
        mismatch = runner.invoke(app, ["workspace", "--session", "beta"])
    assert relative.exit_code != 0
    assert mismatch.exit_code != 0
    client.assert_not_called()
    opened.assert_not_called()

    instance = AsyncMock()
    instance.create_workspace.return_value = {}
    with (
        patch("herdr_master.cli.main.HerdrClient", return_value=instance),
        patch("herdr_master.cli.main.subprocess.run") as opened,
    ):
        missing = runner.invoke(app, ["workspace", "--cwd", "/tmp"])
    assert missing.exit_code != 0
    opened.assert_not_called()
