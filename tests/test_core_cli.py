"""Standalone AiHead Core backend routing, without starting processes."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app
from herdr_master.core.models import EffortLevel, HarnessKind

runner = CliRunner()


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        '[ai_head]\ndangerously_skip_permissions = false\n'
        '[ai_head.binding]\nkind = "pi"\nmodel = "saved-model"\neffort = "high"\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def core_route():
    with (
        patch("herdr_master.herdr.core_aihead.ensure_core_aihead", new_callable=AsyncMock)
        as ensure,
        patch("herdr_master.cli.main.os.execvp") as execvp,
        patch("herdr_master.cli.main._dispatch_standalone_aihead", new_callable=AsyncMock)
        as legacy,
        patch("herdr_master.cli.main.resolve_launch_context") as context,
    ):
        ensure.return_value = SimpleNamespace(address="terminal-123", created=True, state="live")
        yield SimpleNamespace(ensure=ensure, execvp=execvp, legacy=legacy, context=context)


def test_core_forwards_resolved_options_and_attaches(config, tmp_path, core_route):
    result = runner.invoke(aihead_app, [
        "--backend", "core", "--config", str(config), "--cwd", str(tmp_path),
        "--harness", "pi", "--model", "override-model", "--effort", "low",
        "--skip-permissions", "--task", "ship it", "--automode",
    ])

    assert result.exit_code == 0, result.output
    core_route.ensure.assert_awaited_once_with(
        backend="core", config_path=config, cwd=str(tmp_path.resolve()), kind=HarnessKind.PI,
        model="override-model", effort=EffortLevel.LOW,
        dangerously_skip_permissions=True, task="ship it", automode=True,
    )
    assert "Started" in result.output and "terminal-123" in result.output
    core_route.execvp.assert_called_once_with("hc", ["hc", "attach", "terminal-123"])
    core_route.legacy.assert_not_awaited()
    core_route.context.assert_not_called()


@pytest.mark.parametrize("created", [True, False])
def test_core_detached_uses_config_and_reports_creation(config, core_route, created):
    core_route.ensure.return_value.created = created
    result = runner.invoke(aihead_app, [
        "--backend", "core", "--config", str(config), "--no-attach", "--no-automode",
    ])

    assert result.exit_code == 0, result.output
    kwargs = core_route.ensure.await_args.kwargs
    assert kwargs["kind"] is HarnessKind.PI
    assert kwargs["model"] == "saved-model"
    assert kwargs["effort"] is EffortLevel.HIGH
    assert kwargs["dangerously_skip_permissions"] is False
    assert kwargs["automode"] is False
    assert ("Started" if created else "Found") in result.output
    assert "terminal-123" in result.output
    core_route.execvp.assert_not_called()
    core_route.legacy.assert_not_awaited()


@pytest.mark.parametrize("flags", [
    ["--ergo", "work"], ["--council", "work"], ["--orchestration", "work"],
    ["--herdr-master"], ["--setup"], ["--menu"], ["--session", "other"],
    ["--workspace", "desk"], ["--split"], ["--secondary"],
])
def test_core_rejects_incompatible_options_before_launch(core_route, flags):
    result = runner.invoke(aihead_app, ["--backend", "core", *flags])
    assert result.exit_code != 0
    assert "Core backend" in result.output
    core_route.ensure.assert_not_awaited()
    core_route.legacy.assert_not_awaited()
    core_route.execvp.assert_not_called()


def test_default_backend_dispatches_legacy(config, tmp_path, core_route):
    core_route.context.return_value = SimpleNamespace(is_inside_herdr=True, session_name="default")
    core_route.legacy.return_value = {"session": "default", "workspace_id": "ws", "pane_id": "p"}
    result = runner.invoke(
        aihead_app, ["--config", str(config), "--cwd", str(tmp_path), "--no-attach"]
    )
    assert result.exit_code == 0, result.output
    core_route.legacy.assert_awaited_once()
    core_route.ensure.assert_not_awaited()
    core_route.execvp.assert_not_called()


def test_core_route_arguments_match_real_lifecycle_signature(config, tmp_path):
    # Autospec catches mismatches that a permissive AsyncMock would hide.
    with (
        patch("herdr_master.herdr.core_aihead.ensure_core_aihead", autospec=True) as ensure,
        patch("herdr_master.cli.main.os.execvp") as execvp,
    ):
        ensure.return_value = SimpleNamespace(address="terminal-123", created=True, state="live")
        result = runner.invoke(aihead_app, [
            "--backend", "core", "--config", str(config), "--cwd", str(tmp_path), "--no-attach",
        ])
    assert result.exit_code == 0, result.output
    ensure.assert_awaited_once()
    execvp.assert_not_called()


@pytest.mark.parametrize("state", ["finished", "spawn_pending"])
def test_core_does_not_attach_non_live_head(config, core_route, state):
    core_route.ensure.return_value.state = state
    result = runner.invoke(aihead_app, ["--backend", "core", "--config", str(config)])
    assert result.exit_code != 0
    assert state in result.output
    core_route.execvp.assert_not_called()


def test_core_delegate_cli_happy_path(tmp_path):
    with patch("herdr_master.herdr.core_delegation.delegate_core_child", autospec=True) as deleg:
        deleg.return_value = SimpleNamespace(address="t9", state="live", reason=None)
        result = runner.invoke(
            aihead_app,
            ["core", "delegate", "--cwd", str(tmp_path), "--name", "w1", "--", "pi", "--", "hi"],
        )
    assert result.exit_code == 0, result.output
    assert '"address": "t9"' in result.output
    deleg.assert_awaited_once()


def test_core_delegate_cli_fails_closed(tmp_path):
    from herdr_master.herdr.core_delegation import CoreDelegationError

    with patch("herdr_master.herdr.core_delegation.delegate_core_child", autospec=True) as deleg:
        deleg.side_effect = CoreDelegationError("spawn outcome unknown")
        result = runner.invoke(
            aihead_app,
            ["core", "delegate", "--cwd", str(tmp_path), "--", "pi"],
        )
    assert result.exit_code != 0
    assert "spawn outcome unknown" in result.output


def test_core_send_cli_happy_path():
    with patch("herdr_master.herdr.core_delegation.send_core_child", autospec=True) as send:
        send.return_value = {}
        result = runner.invoke(aihead_app, ["core", "send", "t9", "ping"])
    assert result.exit_code == 0, result.output
    assert '"sent": true' in result.output
    send.assert_awaited_once_with("t9", "ping", timeout=30.0)


def test_core_send_cli_fails_closed():
    from herdr_master.herdr.core_delegation import CoreDelegationError

    with patch("herdr_master.herdr.core_delegation.send_core_child", autospec=True) as send:
        send.side_effect = CoreDelegationError("send failed")
        result = runner.invoke(aihead_app, ["core", "send", "t9", "ping"])
    assert result.exit_code != 0
    assert "send failed" in result.output


def test_core_delegate_wait_cli_uses_one_child_workflow(tmp_path):
    with patch(
        "herdr_master.herdr.core_delegation.run_core_one_child_workflow", autospec=True
    ) as wf:
        wf.return_value = SimpleNamespace(
            parent="t1", child="t9", exit_code=0, wait_reason="exit"
        )
        result = runner.invoke(
            aihead_app,
            ["core", "delegate", "--cwd", str(tmp_path), "--parent", "t1", "--wait", "--", "pi"],
        )
    assert result.exit_code == 0, result.output
    assert '"exit_code": 0' in result.output
    wf.assert_awaited_once()


def test_core_work_cli_happy_path(tmp_path):
    with patch(
        "herdr_master.herdr.core_delegation.delegate_core_task", autospec=True
    ) as task_fn:
        task_fn.return_value = SimpleNamespace(
            address="t9", state="live", reason=None
        )
        result = runner.invoke(
            aihead_app,
            ["core", "work", "summarize repo", "--cwd", str(tmp_path), "--parent", "t1"],
        )
    assert result.exit_code == 0, result.output
    assert '"address": "t9"' in result.output
    task_fn.assert_awaited_once()
