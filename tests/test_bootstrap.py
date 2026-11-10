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


def test_bootstrap_council_formatting():
    from herdr_master.core.config import CouncilConfig

    council_cfg = CouncilConfig()
    prompt = build_bootstrap_prompt(
        "council",
        _context(entrypoint="council"),
        council=council_cfg,
    )
    payload = _payload(prompt)
    assert payload["entrypoint"] == "council"
    assert "council" in payload
    council_data = payload["council"]
    assert council_data["max_rounds"] == 5
    assert council_data["coordinator"]["label"] == "coordinator"
    assert council_data["scout"]["label"] == "scout"
    assert len(council_data["members"]) == 2
    assert council_data["members"][0]["label"] == "council-1"
    assert council_data["members"][1]["label"] == "council-2"
    assert prompt.startswith(load_skill_body("council").rstrip("\n"))


def test_bootstrap_ai_head_enabled_and_disabled():
    from herdr_master.pipeline.ai_head import AiHeadDiscovery

    enabled_head = AiHeadDiscovery(
        mode="cli",
        enabled=True,
        harness="agy",
        model="gemini-3.8-flash",
        origin_cwd="/tmp/repo",
        argv=["herdr-master", "aihead-cli"],
    )
    prompt = build_bootstrap_prompt(
        "ergo",
        _context(),
        ai_head=enabled_head,
    )
    payload = _payload(prompt)
    assert "ai_head" in payload
    assert payload["ai_head"]["enabled"] is True
    assert payload["ai_head"]["mode"] == "cli"
    assert payload["ai_head"]["harness"] == "agy"
    assert payload["ai_head"]["model"] == "gemini-3.8-flash"
    assert "instructions" in payload["ai_head"]

    disabled_head = AiHeadDiscovery(
        mode="cli",
        enabled=False,
        harness="agy",
        model="gemini-3.8-flash",
        origin_cwd="/tmp/repo",
    )
    prompt_dis = build_bootstrap_prompt(
        "ergo",
        _context(),
        ai_head=disabled_head,
    )
    payload_dis = _payload(prompt_dis)
    assert "ai_head" not in payload_dis


def test_bootstrap_config_path_preserved():
    from pathlib import Path

    cfg_path = Path("/tmp/repo/.herdr-master.toml")
    prompt = build_bootstrap_prompt(
        "ergo",
        _context(),
        config_path=cfg_path,
    )
    payload = _payload(prompt)
    assert payload["config_path"] == str(cfg_path)



@pytest.mark.parametrize("as_model", [False, True])
@pytest.mark.parametrize(
    "overrides,unavailable",
    [
        ({}, False),
        ({"status": "ready", "reconciled": True}, False),
        ({"status": "busy"}, True),
        ({"status": "pending"}, True),
        ({"status": "unavailable"}, True),
        ({"busy": True}, True),
        ({"reconciled": False}, True),
    ],
)
def test_bootstrap_ai_head_instructions_reflect_readiness(as_model, overrides, unavailable):
    from herdr_master.pipeline.ai_head import AiHeadDiscovery
    from herdr_master.pipeline.bootstrap import _normalize_ai_head

    base = {
        "mode": "cli",
        "enabled": True,
        "harness": "agy",
        "model": "m",
        "origin_cwd": "/tmp/repo",
    }
    if as_model:
        if "busy" in overrides:
            head = AiHeadDiscovery(**base, **overrides)  # extra="allow"
        else:
            head = AiHeadDiscovery(**base, **overrides)
    else:
        head = {**base, **overrides}
    instructions = _normalize_ai_head(head)["instructions"]
    if unavailable:
        assert instructions == (
            "AiHead is currently busy or unavailable. Do not consult AiHead; "
            "escalate decisions directly to the user."
        )
    else:
        assert instructions.startswith("AiHead is available for autonomous technical decisions")
        assert "structured JSON responses" in instructions


def test_bootstrap_caller_is_ai_head_not_marked_busy_and_preserves_parent_pane_id():
    from herdr_master.pipeline.ai_head import AiHeadDiscovery

    # Simulate AiHead launching a child pipeline from its own pane (wT:p2),
    # where Herdr temporarily reports AiHead status as busy/working during launch.
    head = AiHeadDiscovery(
        mode="herdr",
        enabled=True,
        harness="agy",
        model="gemini-3.8-flash",
        origin_cwd="/tmp/repo",
        workspace_id="wT",
        tab_id="wT:t1",
        pane_id="wT:p2",
        status="busy",
        reconciled=False,
    )
    prompt = build_bootstrap_prompt(
        "ergo",
        _context(pane_id="wT:p3", parent_pane_id="wT:p2"),
        ai_head=head,
    )
    payload = _payload(prompt)
    assert payload["parent_pane_id"] == "wT:p2"
    assert payload["ai_head"]["pane_id"] == "wT:p2"
    assert payload["ai_head"]["status"] == "ready"
    assert payload["ai_head"]["busy"] is False
    assert payload["ai_head"]["reconciled"] is True
    assert "Do not consult AiHead" not in payload["ai_head"]["instructions"]
    assert payload["ai_head"]["instructions"].startswith(
        "AiHead is available for autonomous technical decisions"
    )
