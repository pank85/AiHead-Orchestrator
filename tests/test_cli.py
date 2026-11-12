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


def test_tui_delegates_to_aiheaddev():
    with patch("shutil.which", return_value="/usr/local/bin/aiheaddev"), patch("os.execvp") as mock_execvp:
        result = runner.invoke(app, ["tui", "--session", "test-sess"])
        mock_execvp.assert_called_once_with("/usr/local/bin/aiheaddev", ["/usr/local/bin/aiheaddev", "--session", "test-sess"])


def test_tui_not_installed_exits_with_help():
    with patch("shutil.which", return_value=None), patch("importlib.util.find_spec", return_value=None):
        result = runner.invoke(app, ["tui"])
        assert result.exit_code != 0
        assert "AiHeadDev cockpit is not installed" in result.output


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


def test_cli_help_includes_council_orchestration_and_ai_head():
    council_help = runner.invoke(app, ["council", "--help"])
    assert council_help.exit_code == 0
    assert "--ai-head" in council_help.output
    assert "--no-ai-head" in council_help.output
    assert "--roster" in council_help.output
    assert "--attach" in council_help.output

    orch_help = runner.invoke(app, ["orchestration", "--help"])
    assert orch_help.exit_code == 0
    assert "--ai-head" in orch_help.output
    assert "--harness" in orch_help.output
    assert "--models" in orch_help.output
    assert "--scout" in orch_help.output
    assert "--architect" in orch_help.output
    assert "--workers" in orch_help.output
    assert "--reviewer" in orch_help.output

    ergo_help = runner.invoke(app, ["ergo", "--help"])
    assert ergo_help.exit_code == 0
    assert "--ai-head" in ergo_help.output

    aihead_help = runner.invoke(app, ["aihead-cli", "--help"])
    assert aihead_help.exit_code == 0
    assert "--question" in aihead_help.output
    assert "--context" in aihead_help.output
    assert "--choices" in aihead_help.output
    assert "--output-file" in aihead_help.output
    assert "--stdin" in aihead_help.output
    assert "--request-file" in aihead_help.output
    assert "--discover" in aihead_help.output

    state_help = runner.invoke(app, ["council-state", "--help"])
    assert state_help.exit_code == 0
    assert "init" in state_help.output
    assert "record-scout" in state_help.output
    assert "open-round" in state_help.output
    assert "submit-initial-plan" in state_help.output
    assert "submit-feedback" in state_help.output
    assert "check-consensus" in state_help.output
    assert "submit-vote" in state_help.output
    assert "finalize" in state_help.output
    assert "status" in state_help.output

    install_help = runner.invoke(app, ["install", "--help"])
    assert install_help.exit_code == 0
    assert "council" in install_help.output.lower()


def test_cli_ergo_ai_head_flag():
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.launch_ergo", launch),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        result_on = runner.invoke(app, ["ergo", "test intent", "--ai-head", "--no-attach"])
        assert result_on.exit_code == 0
        assert launch.await_args.kwargs["ai_head"] is True

        result_off = runner.invoke(app, ["ergo", "test intent", "--no-ai-head", "--no-attach"])
        assert result_off.exit_code == 0
        assert launch.await_args.kwargs["ai_head"] is False


def test_cli_orchestration_command():
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.launch_orchestration", launch),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        result = runner.invoke(
            app,
            [
                "orchestration",
                "build native cache",
                "--no-attach",
                "--harness",
                "agy",
                "--scout",
                "gemini-3.8-flash",
                "--architect",
                "gemini-3.8-flash",
                "--workers",
                "gemini-3.8-flash",
                "--reviewer",
                "gemini-3.8-flash",
                "--ai-head",
            ],
        )
    assert result.exit_code == 0, result.output
    kwargs = launch.await_args.kwargs
    assert kwargs["intent"] == "build native cache"
    assert kwargs["harness"] == "agy"
    assert kwargs["scout_model"] == "gemini-3.8-flash"
    assert kwargs["coder_model"] == "gemini-3.8-flash"
    assert kwargs["ai_head"] is True

    # Workers and coder disagreement
    disagree = runner.invoke(
        app,
        ["orchestration", "intent", "--workers", "m1", "--coder", "m2", "--no-attach"],
    )
    assert disagree.exit_code != 0
    assert "disagree" in disagree.output


def test_cli_council_command():
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.launch_council", launch),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        result = runner.invoke(
            app,
            ["council", "architect distributed consensus", "--no-attach", "--ai-head"],
        )
    assert result.exit_code == 0, result.output
    kwargs = launch.await_args.kwargs
    assert kwargs["intent"] == "architect distributed consensus"
    assert kwargs["ai_head"] is True
    assert kwargs["council"] is not None

    # Too few members in roster override
    bad_roster = runner.invoke(
        app,
        ["council", "architect", "--roster", "lone-wolf", "--no-attach"],
    )
    assert bad_roster.exit_code != 0
    assert "at least 2 members" in bad_roster.output


def test_cli_aihead_cli(tmp_path):
    from herdr_master.pipeline.ai_head import AiHeadDecisionReply

    # Test --discover
    disc_res = runner.invoke(app, ["aihead-cli", "--discover"])
    assert disc_res.exit_code == 0, disc_res.output
    disc_data = json.loads(disc_res.stdout)
    assert disc_data["mode"] == "cli"

    # Test --question with execution
    mock_reply = AiHeadDecisionReply(
        request_id="req123",
        status="decided",
        decision="Use Redis for shared caching",
        rationale="Redis provides low latency and TTL support",
    )
    out_file = tmp_path / "decision.json"
    with patch(
        "herdr_master.pipeline.ai_head.execute_cli_decision_sync",
        return_value=mock_reply,
    ):
        res = runner.invoke(
            app,
            [
                "aihead-cli",
                "--question",
                "Which cache layer to use?",
                "--context",
                "High write throughput expected",
                "--choices",
                "Redis,Memcached",
                "--output-file",
                str(out_file),
            ],
        )
    assert res.exit_code == 0, res.output
    assert "Decided:" in res.output
    assert "Use Redis" in res.output
    assert out_file.is_file()
    saved = json.loads(out_file.read_text())
    assert saved["decision"] == "Use Redis for shared caching"

    # Missing arguments returns error
    missing = runner.invoke(app, ["aihead-cli"])
    assert missing.exit_code != 0


def test_cli_council_state_e2e(tmp_path):
    run_dir = tmp_path / "council_run"
    context_file = tmp_path / "context.md"
    context_file.write_text("Context facts: codebase is Python and Typer.")

    # 1. init
    res_init = runner.invoke(
        app,
        ["council-state", "init", "--run-dir", str(run_dir), "--roster", "m1,m2"],
    )
    assert res_init.exit_code == 0, res_init.output

    # 2. record-scout
    res_scout = runner.invoke(
        app,
        ["council-state", "record-scout", "--run-dir", str(run_dir), "--context", str(context_file)],
    )
    assert res_scout.exit_code == 0, res_scout.output

    # 3. open-round 0
    res_r0 = runner.invoke(
        app,
        ["council-state", "open-round", "--run-dir", str(run_dir), "--round", "0"],
    )
    assert res_r0.exit_code == 0, res_r0.output

    # 4. submit-initial-plan
    plan_m1 = tmp_path / "plan_m1.md"
    plan_m1.write_text("Plan M1")
    plan_m2 = tmp_path / "plan_m2.md"
    plan_m2.write_text("Plan M2")

    res_p1 = runner.invoke(
        app,
        ["council-state", "submit-initial-plan", "--run-dir", str(run_dir), "--member", "m1", "--plan", str(plan_m1)],
    )
    assert res_p1.exit_code == 0, res_p1.output
    res_p2 = runner.invoke(
        app,
        ["council-state", "submit-initial-plan", "--run-dir", str(run_dir), "--member", "m2", "--plan", str(plan_m2)],
    )
    assert res_p2.exit_code == 0, res_p2.output

    # 5. open-round 1
    res_r1 = runner.invoke(
        app,
        ["council-state", "open-round", "--run-dir", str(run_dir), "--round", "1"],
    )
    assert res_r1.exit_code == 0, res_r1.output

    # 6. submit-feedback (both agree on m1_v0)
    fb_m1 = tmp_path / "fb_m1.md"
    fb_m1.write_text("Feedback M1")
    fb_m2 = tmp_path / "fb_m2.md"
    fb_m2.write_text("Feedback M2")

    res_fb1 = runner.invoke(
        app,
        ["council-state", "submit-feedback", "--run-dir", str(run_dir), "--round", "1", "--member", "m1", "--feedback", str(fb_m1), "--selected-plan", "m1_v0"],
    )
    assert res_fb1.exit_code == 0, res_fb1.output
    res_fb2 = runner.invoke(
        app,
        ["council-state", "submit-feedback", "--run-dir", str(run_dir), "--round", "1", "--member", "m2", "--feedback", str(fb_m2), "--selected-plan", "m1_v0"],
    )
    assert res_fb2.exit_code == 0, res_fb2.output

    # 7. check-consensus
    res_cons = runner.invoke(
        app,
        ["council-state", "check-consensus", "--run-dir", str(run_dir), "--round", "1"],
    )
    assert res_cons.exit_code == 0, res_cons.output
    assert "consensus reached" in res_cons.output.lower()

    # 8. status
    res_status = runner.invoke(
        app,
        ["council-state", "status", "--run-dir", str(run_dir)],
    )
    assert res_status.exit_code == 0, res_status.output
    status_data = json.loads(res_status.stdout)
    assert status_data["phase"] == "consensus_reached"

    # 9. finalize
    final_out = tmp_path / "final_plan.md"
    res_fin = runner.invoke(
        app,
        ["council-state", "finalize", "--run-dir", str(run_dir), "--output", str(final_out)],
    )
    assert res_fin.exit_code == 0, res_fin.output
    assert final_out.is_file()

    # 10. round 6 rejection
    res_r6 = runner.invoke(
        app,
        ["council-state", "open-round", "--run-dir", str(run_dir), "--round", "6"],
    )
    assert res_r6.exit_code != 0


def test_cli_config_command():
    res = runner.invoke(app, ["config"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert "projects" in data
    assert "orchestration" in data
    assert "council" in data
    assert "ai_head" in data



def test_ergo_coordinator_flag_forwards_binding_and_defaults_to_none(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    launch = AsyncMock(return_value=_launch_result())
    with (
        patch("herdr_master.cli.main.launch_ergo", launch),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        flagged = runner.invoke(
            app,
            [
                "ergo",
                "ship it",
                "--coordinator",
                "pi:openai/gpt-6.1-sol:high",
                "--no-attach",
            ],
        )
        plain = runner.invoke(app, ["ergo", "ship it", "--no-attach"])
    assert flagged.exit_code == 0, flagged.output
    assert plain.exit_code == 0, plain.output
    binding = launch.await_args_list[0].kwargs["coordinator"]
    assert binding.kind == HarnessKind.PI
    assert binding.model == "openai/gpt-6.1-sol"
    assert binding.effort == EffortLevel.HIGH
    assert launch.await_args_list[1].kwargs["coordinator"] is None
