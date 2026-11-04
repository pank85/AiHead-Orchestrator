"""Bootstrap handoff is the canonical skill body plus version-1 JSON."""

import json

import pytest

from herdr_master.core.skill_resources import SkillResourceError, load_skill_body
from herdr_master.pipeline.bootstrap import BOOTSTRAP_SENTINEL, build_bootstrap_prompt

OLD_RECON = "Perform reconnaissance on the following objective"


def _context(**overrides):
    base = {
        "entrypoint": "ergo",
        "intent": "ship the cache",
        "origin_cwd": "/tmp/repo",
        "session": "alpha",
        "socket": "/tmp/sessions/alpha/herdr.sock",
        "workspace_id": "w9",
        "owns_workspace": False,
    }
    base.update(overrides)
    return base


def _payload(prompt: str) -> dict:
    marker = f"{BOOTSTRAP_SENTINEL}\n```json\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n```", start)
    return json.loads(prompt[start:end])


def test_bootstrap_uses_canonical_body_and_json_intent():
    intent = 'build "cache"\nand $(rm -rf /)\nline'
    prompt = build_bootstrap_prompt("ergo", _context(intent=intent))
    body = load_skill_body("ergo").rstrip("\n")
    assert prompt.startswith(body)
    appendix = prompt[len(body) :]
    payload = _payload(prompt)
    assert payload["bootstrap_version"] == 1
    assert payload["intent"] == intent
    assert payload["origin_cwd"] == "/tmp/repo"
    assert payload["owns_workspace"] is False
    assert "worktree" not in payload
    assert "run_dir" not in payload
    assert OLD_RECON not in appendix
    assert "already executed" not in appendix.lower()
    assert "pipeline is" not in appendix.lower()


def test_bootstrap_rejects_missing_relative_and_unknown_skill():
    missing = _context()
    del missing["intent"]
    with pytest.raises(ValueError, match="intent"):
        build_bootstrap_prompt("ergo", missing)
    with pytest.raises(ValueError, match="origin_cwd"):
        build_bootstrap_prompt("ergo", _context(origin_cwd="relative/repo"))
    with pytest.raises(ValueError, match="owns_workspace"):
        build_bootstrap_prompt("ergo", _context(owns_workspace="false"))
    with pytest.raises(SkillResourceError):
        build_bootstrap_prompt("not-a-skill", _context())


def test_bootstrap_copies_optional_fields_only_when_present():
    prompt = build_bootstrap_prompt(
        "herdr-master",
        _context(
            entrypoint="herdr-master",
            pane_id="w9:p2",
            worktree="/tmp/repo-wt",
            run_dir=None,
        ),
    )
    payload = _payload(prompt)
    assert payload["entrypoint"] == "herdr-master"
    assert payload["pane_id"] == "w9:p2"
    assert payload["worktree"] == "/tmp/repo-wt"
    assert "run_dir" not in payload
    assert prompt.startswith(load_skill_body("herdr-master").rstrip("\n"))
