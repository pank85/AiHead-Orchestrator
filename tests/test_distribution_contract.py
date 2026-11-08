"""Packaging, console scripts, and wheel distribution contract tests."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from herdr_master.core.catalog import CANONICAL_SKILLS

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_pyproject() -> dict:
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), f"Missing pyproject.toml at {pyproject_path}"
    with open(pyproject_path, "rb") as f:
        return tomllib.load(f)


def test_console_scripts_include_aihead_and_preserve_existing():
    """Assert pyproject.toml defines aihead script and leaves existing scripts unchanged."""
    data = _load_pyproject()
    scripts = data.get("project", {}).get("scripts", {})

    assert scripts.get("herdr-master") == "herdr_master.cli.main:app"
    assert scripts.get("herdm") == "herdr_master.cli.main:app"
    assert scripts.get("aihead") == "herdr_master.cli.main:aihead_app"


def test_wheel_force_include_covers_all_canonical_skills():
    """Assert every canonical skill is mapped in wheel force-include and source files exist."""
    data = _load_pyproject()
    wheel_conf = (
        data.get("tool", {})
        .get("hatch", {})
        .get("build", {})
        .get("targets", {})
        .get("wheel", {})
    )
    force_include = wheel_conf.get("force-include", {})

    # Check exact mapping for aihead
    assert "skills/aihead/SKILL.md" in force_include
    assert force_include["skills/aihead/SKILL.md"] == "herdr_master/_skills/aihead/SKILL.md"

    # Check all canonical skills from catalog are present in force-include
    for skill_name in CANONICAL_SKILLS:
        src_key = f"skills/{skill_name}/SKILL.md"
        dest_val = f"herdr_master/_skills/{skill_name}/SKILL.md"
        assert src_key in force_include, f"Skill {skill_name} missing from wheel force-include"
        assert force_include[src_key] == dest_val

        # Source file must physically exist in repository
        src_file = REPO_ROOT / src_key
        assert src_file.is_file(), f"Force-included source file does not exist: {src_file}"
