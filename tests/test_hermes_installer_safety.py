"""Offline installer safety tests: all targets live under pytest's temporary home."""

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.cli.skills import get_skill_targets
from herdr_master.core.skill_resources import get_skill_source


def targets(home: Path):
    all_targets = get_skill_targets(home=home)
    return {name: [loc for loc in locations if loc.harness == "Hermes (hermes)"]
            for name, locations in all_targets.items()}


def invoke(home: Path, command: list[str]):
    with patch("herdr_master.cli.skills.get_skill_targets", return_value=targets(home)):
        return CliRunner().invoke(app, command)


@pytest.mark.parametrize("command", [["install"], ["skills", "install", "--force"]])
@pytest.mark.parametrize("kind", ["file", "link", "broken-link"])
def test_unowned_target_survives(tmp_path: Path, command, kind):
    home = tmp_path / "home"
    target = home / ".hermes/skills/ergo/SKILL.md"
    target.parent.mkdir(parents=True)
    other = tmp_path / "user-skill.md"
    if kind == "file":
        target.write_text("user content")
    else:
        if kind == "link":
            other.write_text("user content")
        target.symlink_to(other)
    result = invoke(home, command)
    assert result.exit_code != 0
    assert "Refusing to overwrite unowned Hermes target" in result.output
    if kind == "file":
        assert not target.is_symlink() and target.read_text() == "user content"
    else:
        assert target.is_symlink() and target.readlink() == other
    if kind != "broken-link":
        assert (home / ".hermes/skills/council/SKILL.md").is_symlink()
    else:
        # An unreadable candidate prevents proving that any other name is unique.
        assert "Failed to link" in result.output


@pytest.mark.parametrize("candidate", ["nested", "declared", "flat"])
def test_nested_collision_prevents_install(tmp_path: Path, candidate):
    home = tmp_path / "home"
    root = home / ".hermes/skills"
    path = {"nested": root / "group/ergo/SKILL.md", "declared": root / "group/other/SKILL.md", "flat": root / "group/ergo.md"}[candidate]
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: ergo\ndescription: user\n---\nuser content\n")
    result = invoke(home, ["skills", "install"])
    assert result.exit_code != 0 and "Hermes skill collision" in result.output
    assert path.read_text().endswith("user content\n")
    assert not (root / "ergo/SKILL.md").exists()


def test_clean_install_and_reinstall(tmp_path: Path):
    home = tmp_path / "home"
    for command in (["skills", "install"], ["install"]):
        result = invoke(home, command)
        assert result.exit_code == 0, result.output
        assert "active profiles, project and external roots are not checked" in " ".join(result.output.split())
        for name, locations in targets(home).items():
            assert locations[0].path.is_symlink()
            assert locations[0].path.resolve() == get_skill_source(name).resolve()


@pytest.mark.parametrize("command", [["install"], ["skills", "install"]])
@pytest.mark.parametrize("content", [
    '---\n"name": ergo\ndescription: user\n---\n',
    "\ufeff---\n'name': ergo\ndescription: user\n---\n",
    '---\n"na\\u006de": ergo\n---\n',
    '---\n<<: *alias\n---\n',
    '---\nname: [ergo\n---\n',
    '---\ndescription: missing close\n',
    '---\nname: other\nname: ergo\n---\n',
])
def test_metadata_collision_or_unsupported_fails_closed(tmp_path: Path, command, content):
    home = tmp_path / "home"
    candidate = home / ".hermes/skills/group/other/SKILL.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(content, encoding="utf-8")
    result = invoke(home, command)
    assert result.exit_code != 0
    assert "Hermes skill collision" in result.output or "frontmatter" in result.output
    assert candidate.read_text(encoding="utf-8") == content
    assert not (home / ".hermes/skills/ergo/SKILL.md").exists()


@pytest.mark.parametrize("command", [["install"], ["skills", "install", "--force"]])
def test_realistic_nested_frontmatter_and_aliases_install(tmp_path: Path, command):
    home = tmp_path / "home"
    candidate = home / ".hermes/skills/mlops/research/dspy/SKILL.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(
        "\ufeff---\n'name': dspy\ndescription: 'Realistic: model'\n"
        "dependencies: [dspy, openai]\nmetadata:\n  hermes:\n"
        "    tags: &tags [agents, RAG]\n    related: *tags\n---\ncontent\n",
        encoding="utf-8",
    )
    result = invoke(home, command)
    assert result.exit_code == 0, result.output
    assert candidate.read_text(encoding="utf-8").startswith("\ufeff---")
    assert (home / ".hermes/skills/ergo/SKILL.md").is_symlink()


@pytest.mark.parametrize("content", [
    "---\ndefaults: &defaults {name: ergo}\n<<: *defaults\n---\n",
    "---\nalias_value: &alias ergo\nname: *alias\n---\n",
])
def test_alias_and_merge_names_are_detected(tmp_path: Path, content):
    home = tmp_path / "home"
    candidate = home / ".hermes/skills/group/other/SKILL.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(content)
    result = invoke(home, ["install"])
    assert result.exit_code != 0 and "Hermes skill collision" in result.output
    assert not (home / ".hermes/skills/ergo/SKILL.md").exists()


@pytest.mark.parametrize("content", [
    "---\nname: [ergo, council]\n---\n",
    "---\nname: true\n---\n",
    "---\nname: ' ergo '\n---\n",
    "---\nmetadata: {a: 1, a: 2}\n---\n",
    "---\nname: !!python/object:builtins.object {}\n---\n",
    "---\nname: *undefined\n---\n",
])
def test_unsafe_or_ambiguous_yaml_blocks_all_installation(tmp_path: Path, content):
    home = tmp_path / "home"
    candidate = home / ".hermes/skills/group/other/SKILL.md"
    candidate.parent.mkdir(parents=True)
    candidate.write_text(content)
    result = invoke(home, ["skills", "install"])
    assert result.exit_code != 0 and "frontmatter" in result.output
    assert not (home / ".hermes/skills/ergo/SKILL.md").exists()
    assert candidate.read_text() == content


def test_symlinked_directory_fails_closed(tmp_path: Path):
    home = tmp_path / "home"
    root = home / ".hermes/skills"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside, target_is_directory=True)
    result = invoke(home, ["install"])
    assert result.exit_code != 0 and "symlinked skill directory" in result.output
    assert (root / "linked").is_symlink()
    assert not (root / "ergo/SKILL.md").exists()


@pytest.mark.parametrize("command", [["install"], ["skills", "install"]])
def test_partial_failure_keeps_successful_links_and_reports_error(tmp_path: Path, command):
    home = tmp_path / "home"
    failing = home / ".hermes/skills/ergo/SKILL.md"
    original = Path.symlink_to

    def injected(path, *args, **kwargs):
        if path == failing:
            raise OSError("injected failure")
        return original(path, *args, **kwargs)

    with patch.object(Path, "symlink_to", injected):
        result = invoke(home, command)
    assert result.exit_code != 0 and "injected failure" in result.output
    assert "Completed with errors" in result.output
    assert not failing.exists()
    assert (home / ".hermes/skills/council/SKILL.md").is_symlink()
    assert invoke(home, ["skills", "install"]).exit_code == 0
    assert failing.is_symlink()
