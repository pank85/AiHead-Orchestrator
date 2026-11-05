"""Resolve canonical skill bodies from the source checkout or the installed wheel.

Editable/src layouts use the checkout ``skills/`` directory. Installed packages
use ``herdr_master/_skills`` shipped in the wheel. This module never falls back
to a hardcoded project path.
"""

from __future__ import annotations

from pathlib import Path

from herdr_master.core.catalog import CANONICAL_SKILLS


class SkillResourceError(FileNotFoundError):
    """Raised when a canonical skill resource cannot be resolved."""


def package_root() -> Path:
    """Return the ``herdr_master`` package directory that contains this module."""
    return Path(__file__).resolve().parents[1]


def _source_checkout_skills() -> Path | None:
    package_dir = package_root()
    if package_dir.parent.name != "src":
        return None
    skills = package_dir.parent.parent / "skills"
    if all((skills / name / "SKILL.md").is_file() for name in CANONICAL_SKILLS):
        return skills
    return None


def _bundled_skills_dir() -> Path:
    return package_root() / "_skills"


def _skills_root() -> Path:
    source = _source_checkout_skills()
    if source is not None:
        return source
    bundled = _bundled_skills_dir()
    if bundled.is_dir():
        return bundled
    names = ", ".join(CANONICAL_SKILLS)
    raise SkillResourceError(
        f"Canonical skills ({names}) were not found in the installed package or the "
        "containing source checkout. Refusing to fall back to a hardcoded project path. "
        "Expected SKILL.md under herdr_master/_skills/ or the source checkout skills/ directory."
    )


def _validate_skill_name(name: str) -> str:
    if name not in CANONICAL_SKILLS:
        known = ", ".join(CANONICAL_SKILLS)
        raise SkillResourceError(f"Unknown skill '{name}'. Expected one of: {known}.")
    return name


def get_skill_source(name: str) -> Path:
    """Return the absolute ``SKILL.md`` path for a canonical skill."""
    skill = _validate_skill_name(name)
    path = _skills_root() / skill / "SKILL.md"
    if not path.is_file():
        raise SkillResourceError(
            f"Canonical skill '{skill}' is missing at {path}. "
            "Refusing to search other project trees."
        )
    return path.resolve()


def strip_yaml_front_matter(text: str) -> str:
    """Return markdown with a leading YAML front-matter block removed."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return normalized
    end = normalized.find("\n---\n", 4)
    if end == -1:
        return normalized
    body = normalized[end + 5 :]
    if body.startswith("\n"):
        body = body[1:]
    return body


def load_skill_body(name: str) -> str:
    """Load a canonical skill body with YAML front matter removed."""
    return strip_yaml_front_matter(get_skill_source(name).read_text(encoding="utf-8"))
