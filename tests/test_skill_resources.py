"""Canonical skill resource resolution for source checkouts and installed packages."""

from pathlib import Path

import pytest

from herdr_master.core import skill_resources
from herdr_master.core.skill_resources import (
    SkillResourceError,
    get_skill_source,
    load_skill_body,
)

REPO = Path(__file__).resolve().parents[1]


def test_source_checkout_loads_canonical_bodies():
    ergo = get_skill_source("ergo")
    master = get_skill_source("herdr-master")
    assert ergo == (REPO / "skills/ergo/SKILL.md").resolve()
    assert master == (REPO / "skills/herdr-master/SKILL.md").resolve()
    ergo_body = load_skill_body("ergo")
    master_body = load_skill_body("herdr-master")
    assert ergo_body.startswith("# /ergo")
    assert master_body.startswith("# `/herdr-master`")
    assert "slash: false" not in ergo_body
    assert not ergo_body.startswith("---")
    source = (REPO / "src/herdr_master/core/skill_resources.py").read_text(encoding="utf-8")
    assert "/home/pank/projects/herdr-master" not in source


def test_installed_package_uses_bundle_not_checkout(tmp_path, monkeypatch):
    bundle = tmp_path / "_skills"
    for name, heading in (("ergo", "# bundled ergo"), ("herdr-master", "# bundled master")):
        skill_dir = bundle / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\n---\n{heading}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(skill_resources, "_source_checkout_skills", lambda: None)
    monkeypatch.setattr(skill_resources, "_bundled_skills_dir", lambda: bundle)
    assert get_skill_source("ergo") == (bundle / "ergo" / "SKILL.md").resolve()
    assert load_skill_body("herdr-master") == "# bundled master\n"
    assert "/home/pank/projects/herdr-master" not in str(get_skill_source("ergo"))


def test_missing_bundle_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_resources, "_source_checkout_skills", lambda: None)
    monkeypatch.setattr(skill_resources, "_bundled_skills_dir", lambda: tmp_path / "missing")
    with pytest.raises(SkillResourceError, match="Refusing to fall back") as raised:
        get_skill_source("ergo")
    assert "/home/pank/projects/herdr-master" not in str(raised.value)
    with pytest.raises(SkillResourceError, match="Unknown skill"):
        get_skill_source("../secrets")
