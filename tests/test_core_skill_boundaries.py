"""Core skill boundaries must precede legacy-only workflow instructions."""

from pathlib import Path

SKILLS = Path(__file__).resolve().parents[1] / "skills"


def test_aihead_core_boundary_precedes_legacy_tab_rule():
    text = (SKILLS / "aihead" / "SKILL.md").read_text(encoding="utf-8")
    assert text.index("**Core boundary:**") < text.index("## HARD RULE")
    assert "legacy workspace, pane, and inspection-tab rules below do\nnot apply" in text


def test_herdr_master_stops_before_legacy_orchestration_in_core():
    text = (SKILLS / "herdr-master" / "SKILL.md").read_text(encoding="utf-8")
    assert text.index("**Core boundary:**") < text.index("**Always Delegate to Background Agents")
    assert "stop and report the limitation; do not silently launch" in text


def test_ergo_does_not_silently_fall_back_from_core():
    text = (SKILLS / "ergo" / "SKILL.md").read_text(encoding="utf-8")
    boundary = text[text.index("**Core boundary:**") : text.index("Launch with")]
    assert "`HC_TERMINAL_ID` is set or `--backend core` is selected" in boundary
    assert "STOP and report" in boundary
    assert "even with `--native`" in boundary
    assert "Do not launch agents or use legacy Herdr operations as a fallback" in boundary
    assert "Native Ergo outside Core remains supported" in boundary
    assert "**Explicit `--native` Mode**" in text


def test_council_stops_before_legacy_workflow_in_core():
    text = (SKILLS / "council" / "SKILL.md").read_text(encoding="utf-8")
    boundary = text[text.index("**Core boundary:**") : text.index("Launch with")]
    assert "`HC_TERMINAL_ID` is set or `--backend core` is selected" in boundary
    assert "STOP and report" in boundary
    assert "Do not launch or use legacy Herdr operations as a fallback" in boundary
    assert "Outside Core, follow the legacy Council workflow below" in boundary
