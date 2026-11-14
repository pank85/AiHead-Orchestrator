"""Standalone ``aihead`` console-script launch contract.

The command launches an interactive Herdr session. It adopts a live workspace,
starts Herdr only when that session is absent or empty, and never falls back to
the headless decision CLI or the AiHeadDev cockpit.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app, app
from herdr_master.core.context import HerdrContext
from herdr_master.core.models import EffortLevel, HarnessKind
from herdr_master.herdr.socket import HerdrConnectionError, HerdrRPCError
from herdr_master.menu import MenuError

runner = CliRunner()


def _output(result) -> str:
    # CliRunner already mixes stderr into output in this environment.
    return result.output or ""


def _ctx(
    *,
    inside: bool,
    socket: Path,
    session: str = "default",
    workspace_id: str | None = None,
    pane_id: str | None = None,
) -> HerdrContext:
    return HerdrContext(
        is_inside_herdr=inside,
        session_name=session,
        socket_path=socket,
        workspace_id=workspace_id,
        pane_id=pane_id,
        tab_id="tab-1" if pane_id else None,
    )


def _launch_result(
    session: str = "default",
    workspace_id: str = "ws-1",
    *,
    split: bool = False,
) -> dict:
    result = {
        "session": session,
        "workspace_id": workspace_id,
        "pane_id": f"{workspace_id}:p1",
        "split": split,
    }
    if split:
        result["secondary_pane_id"] = f"{workspace_id}:p2"
    return result


def _live_client(workspace_id: str = "ws-live") -> AsyncMock:
    client = AsyncMock()
    client.ping = AsyncMock(return_value={})
    client.get_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            workspaces=[SimpleNamespace(workspace_id=workspace_id, label="desk")],
            focused_workspace_id=workspace_id,
        )
    )
    client.create_workspace = AsyncMock(
        return_value={"workspace": {"workspace_id": "ws-should-not-create"}}
    )
    return client


def _which(name: str) -> str | None:
    if name == "herdr":
        return "/usr/bin/herdr"
    if name == "aihead":
        return "/usr/local/bin/aihead"
    return None


def test_aihead_app_help_is_a_direct_launch_command():
    result = runner.invoke(aihead_app, ["--help"], env={"COLUMNS": "200"})
    text = _output(result)
    assert result.exit_code == 0, text
    assert "Usage: aihead [OPTIONS]" in text
    assert "COMMAND" not in text
    assert "Launch an interactive AiHead session" in text
    for option in (
        "--task",
        "-t",
        "--menu",
        "--harness",
        "--model",
        "--effort",
        "--skip-permissions",
        "--no-skip-permissions",
        "--split",
        "--no-split",
        "--secondary",
        "--workspace",
        "-w",
        "--session",
        "--attach",
        "--no-attach",
        "--cwd",
        "--folder",
        "-C",
        "--config",
    ):
        assert option in text, option


def test_bare_invocation_prints_help_without_launching():
    with patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch:
        result = runner.invoke(aihead_app, [])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output
    launch.assert_not_called()


def test_nested_aihead_argument_is_rejected(tmp_path: Path):
    ctx = _ctx(inside=True, session="alpha", socket=tmp_path / "sock", workspace_id="ws-1")
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
    ):
        result = runner.invoke(aihead_app, ["aihead"])
    assert result.exit_code != 0
    launch.assert_not_called()


def test_forwards_task_binding_aliases_and_paths(tmp_path: Path):
    ctx = _ctx(
        inside=True,
        session="alpha",
        socket=tmp_path / "sock",
        workspace_id="ws-1",
        pane_id="pane-1",
    )
    folder = tmp_path / "work"
    folder.mkdir()
    cfg = tmp_path / "custom.toml"
    original = "[ai_head]\nenabled = false\n"
    cfg.write_text(original, encoding="utf-8")
    launch = AsyncMock(return_value=_launch_result("alpha", "desk"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=_live_client("ws-1")),
        patch("herdr_master.cli.main.launch_ai_head", launch),
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
    ):
        result = runner.invoke(
            aihead_app,
            [
                "-t",
                "ship the feature",
                "--harness",
                "pi",
                "--model",
                "xai/grok-4.7",
                "--effort",
                "xhigh",
                "--secondary",
                "--no-split",
                "-w",
                "desk",
                "--session",
                "alpha",
                "-C",
                str(folder),
                "--config",
                str(cfg),
                "--no-attach",
            ],
        )

    text = _output(result)
    assert result.exit_code == 0, text
    kwargs = launch.await_args.kwargs
    assert kwargs["harness"] is HarnessKind.PI
    assert kwargs["model"] == "xai/grok-4.7"
    assert kwargs["effort"] is EffortLevel.MAX
    assert kwargs["dangerously_skip_permissions"] is True
    assert kwargs["initial_task"] == "ship the feature"
    assert kwargs["split"] is True
    assert kwargs["workspace"] == "desk"
    assert kwargs["session"] == "alpha"
    assert Path(kwargs["cwd"]) == folder.resolve()
    assert Path(kwargs["config_path"]) == cfg
    server.assert_not_called()
    headless.assert_not_called()
    assert cfg.read_text(encoding="utf-8") == original


def test_adopts_external_workspace_without_creating_another(tmp_path: Path, monkeypatch):
    sock = tmp_path / "herdr.sock"
    sock.write_text("", encoding="utf-8")
    monkeypatch.delenv("HERDR_ENV", raising=False)
    monkeypatch.delenv("HERDR_SOCKET_PATH", raising=False)
    monkeypatch.delenv("HERDR_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    ctx = _ctx(inside=False, socket=sock, workspace_id=None)
    client = _live_client("ws-live")
    seen: dict[str, str | None] = {}

    async def fake_launch(**kwargs):
        seen["env"] = os.environ.get("HERDR_ENV")
        seen["socket"] = os.environ.get("HERDR_SOCKET_PATH")
        seen["workspace"] = os.environ.get("HERDR_WORKSPACE_ID")
        seen["pane"] = os.environ.get("HERDR_PANE_ID")
        assert kwargs["workspace"] == "ws-live"
        return _launch_result("default", "ws-live")

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=client),
        patch("herdr_master.cli.main.launch_ai_head", side_effect=fake_launch),
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.cli.main.os.execvp") as execvp,
        patch("shutil.which", side_effect=_which),
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
    ):
        result = runner.invoke(aihead_app, ["--no-attach"])

    assert result.exit_code == 0, _output(result)
    server.assert_not_called()
    client.create_workspace.assert_not_called()
    execvp.assert_not_called()
    headless.assert_not_called()
    assert seen == {
        "env": "1",
        "socket": str(sock),
        "workspace": "ws-live",
        "pane": None,
    }
    assert os.environ.get("HERDR_ENV") is None
    assert sock.exists()


def test_starts_server_and_creates_workspace_when_herdr_is_absent(tmp_path: Path):
    sock = tmp_path / "missing.sock"
    ctx = _ctx(inside=False, session="beta", socket=sock)
    client = AsyncMock()
    client.create_workspace = AsyncMock(
        return_value={
            "workspace": {"workspace_id": "ws-new"},
            "root_pane": {"pane_id": "ws-new:p1"},
        }
    )
    launch = AsyncMock(return_value=_launch_result("beta", "ws-new"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=client),
        patch("herdr_master.cli.main.launch_ai_head", launch),
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.cli.main.os.execvp") as execvp,
        patch("shutil.which", side_effect=_which),
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
        patch("pathlib.Path.unlink", side_effect=AssertionError("must not unlink socket")),
    ):
        result = runner.invoke(aihead_app, ["--no-attach"])

    assert result.exit_code == 0, _output(result)
    server.assert_awaited_once_with(sock, session_name="beta")
    client.create_workspace.assert_awaited_once()
    created = client.create_workspace.await_args
    assert created.kwargs["label"] == "ai-head"
    assert created.kwargs["focus"] is True
    assert launch.await_args.kwargs["workspace"] == "ws-new"
    assert launch.await_args.kwargs["session"] == "beta"
    execvp.assert_not_called()
    headless.assert_not_called()
    client.ping.assert_not_called()


def test_creates_workspace_when_live_session_has_none(tmp_path: Path):
    sock = tmp_path / "herdr.sock"
    sock.write_text("", encoding="utf-8")
    ctx = _ctx(inside=False, socket=sock)
    client = AsyncMock()
    client.ping = AsyncMock(return_value={})
    client.get_snapshot = AsyncMock(
        return_value=SimpleNamespace(workspaces=[], focused_workspace_id=None)
    )
    client.create_workspace = AsyncMock(
        return_value={"workspace": {"workspace_id": "ws-created"}}
    )
    launch = AsyncMock(return_value=_launch_result("default", "ws-created"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=client),
        patch("herdr_master.cli.main.launch_ai_head", launch),
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("shutil.which", side_effect=_which),
        patch("herdr_master.cli.main.os.execvp"),
    ):
        result = runner.invoke(aihead_app, ["--no-attach"])

    assert result.exit_code == 0, _output(result)
    server.assert_awaited_once()
    client.create_workspace.assert_awaited_once()
    assert launch.await_args.kwargs["workspace"] == "ws-created"
    assert sock.exists()


def test_named_workspace_does_not_precreate_a_second_workspace(tmp_path: Path):
    sock = tmp_path / "missing.sock"
    ctx = _ctx(inside=False, session="beta", socket=sock)
    client = AsyncMock()
    launch = AsyncMock(return_value=_launch_result("beta", "desk"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=client),
        patch("herdr_master.cli.main.launch_ai_head", launch),
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("shutil.which", side_effect=_which),
    ):
        result = runner.invoke(aihead_app, ["--no-attach", "--workspace", "desk"])

    assert result.exit_code == 0, _output(result)
    server.assert_awaited_once()
    client.create_workspace.assert_not_called()
    assert launch.await_args.kwargs["workspace"] == "desk"


def test_stale_socket_starts_server_without_unlinking_here(tmp_path: Path):
    sock = tmp_path / "stale.sock"
    sock.write_text("", encoding="utf-8")
    ctx = _ctx(inside=False, socket=sock)
    client = AsyncMock()
    client.ping = AsyncMock(side_effect=HerdrConnectionError("dead socket"))
    client.create_workspace = AsyncMock(return_value={"workspace_id": "ws-fresh"})
    launch = AsyncMock(return_value=_launch_result("default", "ws-fresh"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=client),
        patch("herdr_master.cli.main.launch_ai_head", launch),
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("shutil.which", side_effect=_which),
        patch("pathlib.Path.unlink", side_effect=AssertionError("cli must not unlink")),
    ):
        result = runner.invoke(aihead_app, ["--no-attach"])

    assert result.exit_code == 0, _output(result)
    server.assert_awaited_once()
    assert launch.await_args.kwargs["workspace"] == "ws-fresh"
    assert sock.read_text(encoding="utf-8") == ""


def test_rpc_error_is_not_treated_as_absence(tmp_path: Path):
    sock = tmp_path / "herdr.sock"
    sock.write_text("", encoding="utf-8")
    ctx = _ctx(inside=False, socket=sock)
    client = AsyncMock()
    client.ping = AsyncMock(side_effect=HerdrRPCError("session", "refusing snapshot"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=client),
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
    ):
        result = runner.invoke(aihead_app, ["--no-attach"])

    text = _output(result)
    assert result.exit_code != 0, text
    assert "refusing snapshot" in text
    server.assert_not_called()
    launch.assert_not_called()
    client.create_workspace.assert_not_called()
    headless.assert_not_called()


def test_session_mismatch_fails_before_server_or_launch(monkeypatch, tmp_path: Path):
    sock = tmp_path / "sessions" / "alpha" / "herdr.sock"
    monkeypatch.setenv("HERDR_ENV", "1")
    monkeypatch.setenv("HERDR_SOCKET_PATH", str(sock))
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w9")
    monkeypatch.delenv("HERDR_SOCKET", raising=False)
    with (
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.HerdrClient") as client,
    ):
        result = runner.invoke(aihead_app, ["--session", "beta"])

    assert result.exit_code != 0
    assert "Refusing session" in _output(result)
    server.assert_not_called()
    launch.assert_not_called()
    client.assert_not_called()


def test_invalid_binding_fails_before_probe(tmp_path: Path):
    cfg = tmp_path / "empty.toml"
    cfg.write_text("[ai_head]\nenabled = false\n", encoding="utf-8")
    with (
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.HerdrClient") as client,
    ):
        unknown = runner.invoke(
            aihead_app,
            ["--config", str(cfg), "--harness", "not-a-harness"],
        )
        empty_model = runner.invoke(
            aihead_app,
            ["--config", str(cfg), "--harness", "pi", "--model", " "],
        )

    assert unknown.exit_code != 0
    assert "Unknown harness" in _output(unknown)
    assert empty_model.exit_code != 0
    assert "model" in _output(empty_model).lower()
    server.assert_not_called()
    launch.assert_not_called()
    client.assert_not_called()


def test_malformed_or_missing_config_fails_before_launch(tmp_path: Path):
    bad = tmp_path / "bad.toml"
    bad.write_text("not = [toml", encoding="utf-8")
    missing = tmp_path / "missing.toml"
    with (
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
    ):
        malformed = runner.invoke(aihead_app, ["--config", str(bad)])
        absent = runner.invoke(aihead_app, ["--config", str(missing)])

    assert malformed.exit_code != 0
    assert "Invalid AiHead configuration" in _output(malformed)
    assert absent.exit_code != 0
    assert "not found" in _output(absent).lower()
    server.assert_not_called()
    launch.assert_not_called()


def test_config_cwd_selection_does_not_mutate_files_or_process_cwd(tmp_path: Path):
    project = tmp_path / "proj"
    configured = tmp_path / "configured"
    project.mkdir()
    configured.mkdir()
    cfg = project / ".herdr-master.toml"
    cfg.write_text(
        f'[ai_head]\nenabled = false\nsplit = false\ncwd = "{configured}"\n',
        encoding="utf-8",
    )
    before = cfg.read_bytes()
    cwd_before = os.getcwd()
    ctx = _ctx(
        inside=True,
        session="alpha",
        socket=tmp_path / "sock",
        workspace_id="ws-1",
        pane_id="pane-1",
    )
    launch = AsyncMock(return_value=_launch_result("alpha"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=_live_client("ws-1")),
        patch("herdr_master.cli.main.launch_ai_head", launch),
    ):
        from_config = runner.invoke(
            aihead_app,
            ["--config", str(cfg), "--harness", "pi", "--model", "xai/grok-4.7"],
        )
        from_cli = runner.invoke(
            aihead_app,
            [
                "--config",
                str(cfg),
                "--cwd",
                str(project),
                "--harness",
                "pi",
                "--model",
                "xai/grok-4.7",
            ],
        )

    assert from_config.exit_code == 0, _output(from_config)
    assert from_cli.exit_code == 0, _output(from_cli)
    assert Path(launch.await_args_list[0].kwargs["cwd"]) == configured.resolve()
    assert launch.await_args_list[0].kwargs["split"] is False
    assert Path(launch.await_args_list[1].kwargs["cwd"]) == project.resolve()
    assert cfg.read_bytes() == before
    assert os.getcwd() == cwd_before


def test_missing_directory_fails_before_launch(tmp_path: Path):
    with patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch:
        result = runner.invoke(aihead_app, ["--cwd", str(tmp_path / "missing-dir")])
    assert result.exit_code != 0
    assert "working directory" in _output(result).lower()
    launch.assert_not_called()


def test_attach_and_no_attach(tmp_path: Path):
    sock = tmp_path / "herdr.sock"
    sock.write_text("", encoding="utf-8")
    ctx = _ctx(inside=False, socket=sock)
    client = _live_client("ws-live")
    launch = AsyncMock(return_value=_launch_result("default", "ws-live"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=client),
        patch("herdr_master.cli.main.launch_ai_head", launch),
        patch("herdr_master.cli.main.os.execvp") as execvp,
        patch("shutil.which", side_effect=_which),
        patch("importlib.util.find_spec", wraps=importlib.util.find_spec) as spec,
    ):
        attached = runner.invoke(aihead_app, ["--task", "test"])
        detached = runner.invoke(aihead_app, ["--no-attach"])

    assert attached.exit_code == 0, _output(attached)
    assert "Attaching to isolated Herdr session 'default'" in _output(attached)
    execvp.assert_called_once_with("herdr", ["herdr", "--session", "default"])
    assert detached.exit_code == 0, _output(detached)
    assert execvp.call_count == 1
    assert all(call.args[0] != "aihead" for call in execvp.call_args_list)
    assert all("aiheaddev" not in call.args[0] for call in execvp.call_args_list)
    assert all(call.args[:1] != ("aiheaddev",) for call in spec.call_args_list)
    assert launch.await_count == 2


def test_missing_herdr_executable_fails_before_panes_or_server(tmp_path: Path):
    live_sock = tmp_path / "live.sock"
    live_sock.write_text("", encoding="utf-8")
    live_ctx = _ctx(inside=False, socket=live_sock)
    missing_ctx = _ctx(inside=False, socket=tmp_path / "missing.sock")
    with (
        patch("herdr_master.cli.main.HerdrClient", return_value=_live_client("ws-live")),
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("shutil.which", return_value=None),
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
    ):
        with patch("herdr_master.cli.main.resolve_launch_context", return_value=live_ctx):
            attach_blocked = runner.invoke(aihead_app, ["--task", "test"])
        with patch("herdr_master.cli.main.resolve_launch_context", return_value=missing_ctx):
            start_blocked = runner.invoke(aihead_app, ["--no-attach"])

    assert attach_blocked.exit_code != 0
    assert start_blocked.exit_code != 0
    assert "herdr executable is not on PATH" in _output(attach_blocked)
    assert "herdr executable is not on PATH" in _output(start_blocked)
    assert "headless" in _output(attach_blocked).lower()
    launch.assert_not_called()
    server.assert_not_called()
    headless.assert_not_called()


def test_no_attach_live_daemon_does_not_require_herdr_binary(tmp_path: Path):
    sock = tmp_path / "herdr.sock"
    sock.write_text("", encoding="utf-8")
    ctx = _ctx(inside=False, socket=sock)
    launch = AsyncMock(return_value=_launch_result("default", "ws-live"))
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=_live_client("ws-live")),
        patch("herdr_master.cli.main.launch_ai_head", launch),
        patch("herdr_master.cli.main.os.execvp") as execvp,
        patch("shutil.which", return_value=None),
    ):
        result = runner.invoke(aihead_app, ["--no-attach"])

    assert result.exit_code == 0, _output(result)
    launch.assert_awaited_once()
    execvp.assert_not_called()


def test_launch_failure_does_not_fall_back_to_headless(tmp_path: Path):
    ctx = _ctx(
        inside=True,
        session="alpha",
        socket=tmp_path / "sock",
        workspace_id="ws-1",
        pane_id="pane-1",
    )
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.HerdrClient", return_value=_live_client("ws-1")),
        patch(
            "herdr_master.cli.main.launch_ai_head",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pane allocation failed"),
        ),
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
    ):
        result = runner.invoke(aihead_app, ["--task", "test"])

    text = _output(result)
    assert result.exit_code != 0, text
    assert "pane allocation failed" in text
    server.assert_not_called()
    headless.assert_not_called()


def test_tui_does_not_execute_aihead_when_aiheaddev_is_absent():
    def which(name: str) -> str | None:
        if name == "aihead":
            return "/usr/local/bin/aihead"
        return None

    with (
        patch("shutil.which", side_effect=which),
        patch("os.execvp") as execvp,
        patch("importlib.util.find_spec", return_value=None),
    ):
        result = runner.invoke(app, ["tui", "--session", "alpha"])

    text = _output(result)
    assert result.exit_code != 0, text
    assert "AiHead-Cockpit is not installed" in text
    execvp.assert_not_called()


def test_install_help_refers_to_canonical_skills_generically():
    result = runner.invoke(app, ["install", "--help"])
    text = _output(result)
    assert result.exit_code == 0, text
    assert "canonical skills" in text.lower()
    assert "/herdr-master, /ergo, /orchestration, and /council" not in text


def _permission_config(tmp_path: Path, *,
    skip: bool | None,
    effort: str | None = "high",
) -> Path:
    lines = ["[ai_head]", "enabled = true"]
    if skip is not None:
        lines.append(
            "dangerously_skip_permissions = " + ("true" if skip else "false")
        )
    lines.extend(
        [
            "",
            "[ai_head.binding]",
            'kind = "agy"',
            'model = "gemini-2.5-pro"',
        ]
    )
    if effort is not None:
        lines.append(f'effort = "{effort}"')
    path = tmp_path / "ai-head.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_standalone_forwards_permission_precedence_with_effort(tmp_path: Path):
    ctx = _ctx(
        inside=True,
        session="alpha",
        socket=tmp_path / "sock",
        workspace_id="ws-1",
        pane_id="pane-1",
    )
    cases = [
        ([], False, False, EffortLevel.HIGH),
        ([], None, True, EffortLevel.HIGH),
        (["--skip-permissions"], False, True, EffortLevel.HIGH),
        (["--no-skip-permissions", "--effort", "low"], True, False, EffortLevel.LOW),
    ]
    for flags, saved_skip, expected_skip, expected_effort in cases:
        launch = AsyncMock(return_value=_launch_result("alpha", "ws-1"))
        with (
            patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
            patch("herdr_master.cli.main.HerdrClient", return_value=_live_client("ws-1")),
            patch("herdr_master.cli.main.launch_ai_head", launch),
            patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock),
            patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
            patch("os.execvp") as execvp,
        ):
            result = runner.invoke(
                aihead_app,
                [
                    "--config",
                    str(_permission_config(tmp_path, skip=saved_skip)),
                    "--no-attach",
                    *flags,
                ],
            )
        text = _output(result)
        assert result.exit_code == 0, text
        kwargs = launch.await_args.kwargs
        assert kwargs["dangerously_skip_permissions"] is expected_skip
        assert kwargs["effort"] is expected_effort
        headless.assert_not_called()
        execvp.assert_not_called()


def test_menu_flag_dispatches_lazily_and_calls_run_menu_once():
    with (
        patch("herdr_master.menu.run_menu") as run_mock,
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.cli.main.tui") as tui_mock,
        patch("herdr_master.cli.main.resolve_launch_context") as resolve_ctx,
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
    ):
        result = runner.invoke(aihead_app, ["--menu"])
    assert result.exit_code == 0, _output(result)
    run_mock.assert_called_once_with()
    launch.assert_not_called()
    server.assert_not_called()
    tui_mock.assert_not_called()
    resolve_ctx.assert_not_called()
    headless.assert_not_called()


def test_menu_flag_menu_error_aborts_cleanly():
    with (
        patch(
            "herdr_master.menu.run_menu",
            side_effect=MenuError("no usable focused project"),
        ) as run_mock,
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
        patch("herdr_master.cli.main.ensure_herdr_server", new_callable=AsyncMock) as server,
        patch("herdr_master.cli.main.tui") as tui_mock,
        patch("herdr_master.cli.main.resolve_launch_context") as resolve_ctx,
        patch("herdr_master.pipeline.ai_head.execute_cli_decision_sync") as headless,
    ):
        result = runner.invoke(aihead_app, ["--menu"])
    assert result.exit_code == 1
    assert "no usable focused project" in _output(result)
    run_mock.assert_called_once_with()
    launch.assert_not_called()
    server.assert_not_called()
    tui_mock.assert_not_called()
    resolve_ctx.assert_not_called()
    headless.assert_not_called()


@pytest.mark.parametrize(
    ("flags", "expected_err"),
    [
        (["--menu", "--setup"], "Choose only one launch mode"),
        (["--menu", "--tui"], "Choose only one launch mode"),
        (["--menu", "--herdr-master"], "Choose only one launch mode"),
        (["--menu", "--ergo", "test"], "Choose only one launch mode"),
        (["--menu", "--council", "test"], "Choose only one launch mode"),
        (["--menu", "--orchestration", "test"], "Choose only one launch mode"),
        (["--menu", "--task", "test"], "Choose only one launch mode"),
        (["--menu", "--session", "alpha"], "--session is not supported with --menu"),
        (["--menu", "--no-attach"], "--no-attach is not supported with --menu"),
    ],
)
def test_menu_flag_conflicts_are_rejected(flags: list[str], expected_err: str):
    with (
        patch("herdr_master.menu.run_menu") as run_mock,
        patch("herdr_master.cli.main.launch_ai_head", new_callable=AsyncMock) as launch,
    ):
        result = runner.invoke(aihead_app, flags)
    assert result.exit_code != 0
    assert expected_err in _output(result)
    run_mock.assert_not_called()
    launch.assert_not_called()
