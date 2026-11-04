"""Unit tests for standalone skills and commands installer."""

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.cli.skills import (
    TargetLocation,
    find_repo_root,
    get_skill_targets,
)

runner = CliRunner()


def test_find_repo_root():
    root = find_repo_root()
    assert (root / "skills" / "herdr-master" / "SKILL.md").exists()
    assert (root / "skills" / "ergo" / "SKILL.md").exists()


def test_get_skill_targets_structure():
    targets = get_skill_targets()
    assert "herdr-master" in targets
    assert "ergo" in targets

    for _skill_name, locs in targets.items():
        harnesses = {loc.harness for loc in locs}
        assert "Antigravity (agy)" in harnesses
        assert "Pi (pi)" in harnesses
        assert "Claude Code (claude)" in harnesses
        assert "OpenCode (opencode)" in harnesses


def test_cli_skills_list():
    result = runner.invoke(app, ["skills", "list"])
    assert result.exit_code == 0
    assert "Herdr-Master" in result.output
    assert "/herdr-master" in result.output
    assert "/ergo" in result.output


def test_cli_skills_install_in_temp_home(tmp_path: Path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    fake_targets = {
        "herdr-master": [
            TargetLocation(
                "Pi (pi)",
                "command",
                fake_home / ".pi" / "agent" / "prompts" / "herdr-master.md",
                "Desc",
            ),
        ],
        "ergo": [
            TargetLocation(
                "Claude Code (claude)",
                "command",
                fake_home / ".claude" / "commands" / "ergo.md",
                "Desc",
            ),
        ],
    }

    with patch("herdr_master.cli.skills.get_skill_targets", return_value=fake_targets):
        result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0
        assert (fake_home / ".pi" / "agent" / "prompts" / "herdr-master.md").is_symlink()
        assert (fake_home / ".claude" / "commands" / "ergo.md").is_symlink()

        # Remove
        remove_result = runner.invoke(app, ["skills", "remove"])
        assert remove_result.exit_code == 0
        assert not (fake_home / ".pi" / "agent" / "prompts" / "herdr-master.md").exists()
        assert not (fake_home / ".claude" / "commands" / "ergo.md").exists()
