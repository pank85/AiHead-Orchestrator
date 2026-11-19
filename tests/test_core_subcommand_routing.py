"""Explicit Core selection must never dispatch legacy launch subcommands."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app

runner = CliRunner()


@pytest.mark.parametrize(
    "command",
    [
        ["head"],
        ["ergo", "work"],
        ["council", "work"],
        ["orchestration", "work"],
        ["master", "start"],
        ["pipeline", "ergo", "work"],
        ["pipeline", "council", "work"],
        ["pipeline", "orchestration", "work"],
        ["tui"],
        ["workspace"],
        ["aihead-cli", "--question", "work"],
    ],
)
def test_core_rejects_legacy_launch_subcommands(command):
    with patch("herdr_master.cli.main.resolve_launch_context") as resolve:
        result = runner.invoke(aihead_app, ["--backend", "core", *command])

    assert result.exit_code != 0
    assert "Core backend does not support" in result.output
    assert command[0] in result.output
    resolve.assert_not_called()


def test_invalid_backend_is_rejected_before_subcommand():
    result = runner.invoke(aihead_app, ["--backend", "invalid", "config"])
    assert result.exit_code != 0
    assert "--backend must be legacy or core" in result.output


@pytest.mark.parametrize("backend", [[], ["--backend", "legacy"]])
def test_legacy_subcommand_dispatch_is_unchanged(backend):
    with patch(
        "herdr_master.cli.main.resolve_launch_context", side_effect=ValueError("legacy reached")
    ):
        result = runner.invoke(aihead_app, [*backend, "head"])
    assert result.exit_code != 0
    assert "legacy reached" in result.output


def test_core_allows_read_only_config():
    result = runner.invoke(aihead_app, ["--backend", "core", "config"])
    assert result.exit_code == 0, result.output
    assert "ai_head" in result.output


def test_core_inspection_subcommand_remains_available():
    with patch("herdr_master.herdr.core_client.terminal_list", return_value=None) as terminals:
        async def listing():
            return {"terminals": []}

        terminals.side_effect = listing
        result = runner.invoke(aihead_app, ["--backend", "core", "core", "terminals"])
    assert result.exit_code == 0, result.output
    assert '"terminals"' in result.output
    terminals.assert_called_once_with()
