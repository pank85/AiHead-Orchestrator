"""Unit tests for standalone skills and commands installer."""

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.cli.skills import (
    TargetLocation,
    _evaluate_link_status,
    find_repo_root,
    get_skill_targets,
)
from herdr_master.core.catalog import CANONICAL_SKILLS, HarnessKind, get_harness
from herdr_master.core.skill_resources import get_skill_source

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[1]


def test_find_repo_root():
    root = find_repo_root()
    assert (root / "skills" / "herdr-master" / "SKILL.md").exists()
    assert (root / "skills" / "ergo" / "SKILL.md").exists()
    assert (root / "skills" / "council" / "SKILL.md").exists()


def test_find_repo_root_raises_without_hardcoded_fallback(tmp_path: Path):
    with patch("herdr_master.cli.skills.Path") as mock_path:
        mock_file = tmp_path / "somewhere" / "skills.py"
        mock_path.return_value = mock_file
        mock_path.__file__ = str(mock_file)
        with pytest.raises(FileNotFoundError, match="Could not locate herdr-master root"):
            find_repo_root()


def test_get_skill_targets_matches_catalog_registry():
    targets = get_skill_targets()
    assert set(targets.keys()) == set(CANONICAL_SKILLS)

    for skill_name in CANONICAL_SKILLS:
        locs = targets[skill_name]
        # Sentinel assertion: 4 harnesses, 5 destinations per skill
        assert len(locs) == 5
        harnesses = {loc.harness for loc in locs}
        assert "Antigravity (agy)" in harnesses
        assert "Pi (pi)" in harnesses
        assert "Claude Code (claude)" in harnesses
        assert "OpenCode (opencode)" in harnesses

        # Validate against catalog templates directly
        expected_templates: list[str] = []
        for kind in HarnessKind:
            spec = get_harness(kind.value)
            for t in spec.install_targets:
                expected_templates.append(t.path_template)

        assert len(locs) == len(expected_templates)


def test_get_skill_targets_with_custom_home(tmp_path: Path):
    fake_home = tmp_path / "custom_home"
    targets = get_skill_targets(home=fake_home)
    for _skill, locs in targets.items():
        for loc in locs:
            assert str(fake_home) in str(loc.path)
            assert str(Path.home()) not in str(loc.path)


def test_evaluate_link_status(tmp_path: Path):
    canonical = tmp_path / "canonical.md"
    canonical.write_text("canonical", encoding="utf-8")

    # 1. Missing
    missing = tmp_path / "missing.md"
    assert _evaluate_link_status(missing, canonical) == "[red]✗ Missing[/red]"

    # 2. Active symlink
    active_link = tmp_path / "active.md"
    active_link.symlink_to(canonical)
    assert _evaluate_link_status(active_link, canonical) == "[green]✓ Active[/green]"

    # 3. Stale symlink (points to another file)
    other = tmp_path / "other.md"
    other.write_text("other", encoding="utf-8")
    stale_link = tmp_path / "stale.md"
    stale_link.symlink_to(other)
    assert _evaluate_link_status(stale_link, canonical) == "[yellow]⚠ Stale[/yellow]"

    # 4. Broken symlink
    broken_link = tmp_path / "broken.md"
    broken_link.symlink_to(tmp_path / "nonexistent.md")
    assert _evaluate_link_status(broken_link, canonical) == "[red]✗ Broken[/red]"

    # 5. Regular file (not a symlink)
    reg_file = tmp_path / "regular.md"
    reg_file.write_text("not a link", encoding="utf-8")
    assert _evaluate_link_status(reg_file, canonical) == "[yellow]⚠ Stale[/yellow]"


def test_cli_skills_list():
    result = runner.invoke(app, ["skills", "list"])
    assert result.exit_code == 0
    assert "Herdr-Master" in result.output
    assert "/herdr-master" in result.output
    assert "/ergo" in result.output
    assert "/orchestration" in result.output
    assert "/council" in result.output


def test_cli_skills_install_and_remove_in_temp_home(tmp_path: Path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    targets_map = get_skill_targets(home=fake_home)
    expected_links = sum(len(locs) for locs in targets_map.values())

    with patch("herdr_master.cli.skills.get_skill_targets", return_value=targets_map):
        # Initial install
        result = runner.invoke(app, ["skills", "install"])
        assert result.exit_code == 0
        assert f"{expected_links} skill/command links configured" in result.output

        # Verify all symlinks exist and point to canonical files
        for skill_name, locs in targets_map.items():
            canonical = get_skill_source(skill_name)
            for loc in locs:
                assert loc.path.is_symlink()
                assert loc.path.resolve() == canonical.resolve()

        # Idempotent re-install with force
        reinstall_result = runner.invoke(app, ["skills", "install"])
        assert reinstall_result.exit_code == 0

        # Install with --no-force skips existing
        noforce_result = runner.invoke(app, ["skills", "install", "--no-force"])
        assert noforce_result.exit_code == 0
        assert "Skipping existing symlink" in noforce_result.output

        # Remove
        remove_result = runner.invoke(app, ["skills", "remove"])
        assert remove_result.exit_code == 0
        assert f"Cleaned up {expected_links} skill/command symlinks" in remove_result.output

        for _skill_name, locs in targets_map.items():
            for loc in locs:
                assert not loc.path.exists()
                assert not loc.path.is_symlink()


def test_install_handles_existing_regular_file(tmp_path: Path):
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()

    test_file = fake_home / ".claude" / "commands" / "ergo.md"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("existing regular file", encoding="utf-8")

    fake_targets = {
        "ergo": [
            TargetLocation(
                harness="Claude Code (claude)",
                kind="command",
                path=test_file,
                description="ergo command",
            ),
        ],
    }

    with patch("herdr_master.cli.skills.get_skill_targets", return_value=fake_targets):
        # 1. With --no-force, regular file is untouched
        res_noforce = runner.invoke(app, ["skills", "install", "--no-force"])
        assert res_noforce.exit_code == 0
        assert "Skipping existing file" in res_noforce.output
        assert not test_file.is_symlink()
        assert test_file.read_text(encoding="utf-8") == "existing regular file"

        # 2. With force, regular file is replaced by symlink
        res_force = runner.invoke(app, ["skills", "install", "--force"])
        assert res_force.exit_code == 0
        assert test_file.is_symlink()
        assert test_file.resolve() == get_skill_source("ergo").resolve()


def test_install_fails_on_directory_conflict(tmp_path: Path):
    fake_home = tmp_path / "fake_home"
    conflict_dir = fake_home / ".gemini" / "antigravity-cli" / "skills" / "ergo" / "SKILL.md"
    conflict_dir.mkdir(parents=True)

    fake_targets = {
        "ergo": [
            TargetLocation(
                harness="Antigravity (agy)",
                kind="skill",
                path=conflict_dir,
                description="ergo skill",
            ),
        ],
    }

    with patch("herdr_master.cli.skills.get_skill_targets", return_value=fake_targets):
        res = runner.invoke(app, ["skills", "install"])
        assert res.exit_code != 0
        assert "Refusing to overwrite existing directory" in res.output


def test_install_fails_on_missing_canonical_skill():
    fake_targets = {
        "nonexistent-skill": [
            TargetLocation(
                harness="Antigravity (agy)",
                kind="skill",
                path=Path("/tmp/some_path"),
                description="desc",
            ),
        ],
    }
    with patch("herdr_master.cli.skills.get_skill_targets", return_value=fake_targets):
        res = runner.invoke(app, ["skills", "install"])
        assert res.exit_code != 0
        assert "Error locating canonical skill" in res.output


def test_shell_install_wrapper_delegation(tmp_path: Path):
    script = REPO_ROOT / "scripts" / "install-skills.sh"
    alias = REPO_ROOT / "scripts" / "install-skill.sh"

    # Alias must remain a symlink to install-skills.sh
    assert alias.is_symlink()
    assert os.readlink(alias) == "install-skills.sh"

    # Script must not contain hand-maintained destination directories
    script_content = script.read_text(encoding="utf-8")
    assert "AGY_SKILLS=" not in script_content
    assert "PI_PROMPTS=" not in script_content
    assert "CLAUDE_CMDS=" not in script_content
    assert "OPENCODE_SKILLS=" not in script_content
    assert "mkdir -p" not in script_content

    # Test execution with stub uv executable in PATH
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "stub_uv.log"

    stub_uv = bin_dir / "uv"
    stub_uv.write_text(
        f'#!/bin/sh\necho "$@" > "{log_file}"\nexit 0\n',
        encoding="utf-8",
    )
    stub_uv.chmod(stub_uv.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    proc = subprocess.run(
        [str(script), "--no-force"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.returncode == 0

    logged_args = log_file.read_text(encoding="utf-8").strip()
    assert f"run --project {REPO_ROOT} herdm skills install --no-force" in logged_args


def test_shell_install_wrapper_failure_without_tools(tmp_path: Path):
    script = REPO_ROOT / "scripts" / "install-skills.sh"
    clean_bin = tmp_path / "clean_bin"
    clean_bin.mkdir()

    # Symlink only bash and core tools, ensuring uv and herdm are absent
    for tool in ["bash", "dirname", "pwd", "echo"]:
        tool_path = Path(f"/usr/bin/{tool}")
        if not tool_path.exists():
            tool_path = Path(f"/bin/{tool}")
        if tool_path.exists():
            (clean_bin / tool).symlink_to(tool_path)

    env = dict(os.environ)
    env["PATH"] = str(clean_bin)
    env.pop("VIRTUAL_ENV", None)

    proc = subprocess.run(
        [str(clean_bin / "bash"), str(script)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "Error: 'uv' or active virtualenv is required" in proc.stderr
