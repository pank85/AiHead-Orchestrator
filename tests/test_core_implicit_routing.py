"""Implicit legacy launches must not escape a Core terminal."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app

runner = CliRunner()


@pytest.mark.parametrize("args", [
    [], ["--tui"], ["--task", "work"], ["--menu"], ["--ergo", "work"],
    ["--council", "work"], ["--orchestration", "work"], ["--herdr-master"],
    ["head"], ["ergo", "work"], ["council", "work"], ["orchestration", "work"],
    ["master", "start"], ["pipeline", "ergo", "work"], ["tui"], ["workspace"],
    ["aihead-cli", "--question", "work"],
])
def test_implicit_legacy_launch_is_blocked_in_core(monkeypatch, args):
    monkeypatch.setenv("HC_TERMINAL_ID", "terminal-1")
    with patch("herdr_master.cli.main.resolve_launch_context") as resolve:
        result = runner.invoke(aihead_app, args)
    assert result.exit_code != 0
    assert "Implicit legacy launch" in result.output
    assert "--backend legacy" in result.output
    resolve.assert_not_called()


@pytest.mark.parametrize("args", [["config"], ["catalog", "available"], ["core", "terminals"]])
def test_inspection_commands_remain_available(monkeypatch, args):
    monkeypatch.setenv("HC_TERMINAL_ID", "terminal-1")
    with patch("herdr_master.herdr.core_client.terminal_list", new_callable=AsyncMock) as listing:
        listing.return_value = {"terminals": []}
        result = runner.invoke(aihead_app, args)
    assert result.exit_code == 0, result.output


def test_setup_remains_available(monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "terminal-1")
    with patch("herdr_master.cli.main.run_aihead_setup") as setup:
        result = runner.invoke(aihead_app, ["--setup", "--defaults", "--yes"])
    assert result.exit_code == 0, result.output
    setup.assert_called_once()


@pytest.mark.parametrize("args", [[], ["head"]])
def test_explicit_legacy_is_respected(monkeypatch, args):
    monkeypatch.setenv("HC_TERMINAL_ID", "terminal-1")
    with patch(
        "herdr_master.cli.main.resolve_launch_context", side_effect=ValueError("legacy reached")
    ):
        result = runner.invoke(aihead_app, ["--backend", "legacy", *args])
    assert "legacy reached" in result.output


def test_implicit_legacy_outside_core_is_unchanged(monkeypatch):
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    with patch(
        "herdr_master.cli.main.resolve_launch_context", side_effect=ValueError("legacy reached")
    ):
        result = runner.invoke(aihead_app, [])
    assert "legacy reached" in result.output


def test_explicit_core_still_routes_to_core(monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "terminal-1")
    with patch(
        "herdr_master.herdr.core_aihead.ensure_core_aihead", new_callable=AsyncMock
    ) as ensure:
        ensure.return_value = SimpleNamespace(address="terminal-2", created=True, state="live")
        result = runner.invoke(aihead_app, ["--backend", "core", "--no-attach"])
    assert result.exit_code == 0, result.output
    ensure.assert_awaited_once()
