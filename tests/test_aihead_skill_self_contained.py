"""The AiHead skill retains its operational guidance without companion files."""

from pathlib import Path


def test_aihead_skill_inlines_coordinator_and_verification_guidance():
    skill = (Path(__file__).resolve().parents[1] / "skills/aihead/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "references/coordinator-bindings.md" not in skill
    assert "references/verifying-pipeline-results.md" not in skill
    assert "references/" not in skill
    for guidance in (
        "skill_sources.aihead",
        "`--run-squad` snapshot",
        "searching upward from the launch cwd",
        "`aihead catalog squad --json`",
        "active executable and imported `herdr_master` package location",
        "`run_ergo()` and `_launch_skill()`",
        "config round-trip, CLI forwarding, and wizard",
        "pre-change commit in a disposable baseline worktree",
        "Verify import provenance",
        "change one variable at a time",
        "new regressions, pre-existing failures, and environment-dependent failures",
        "missing, truncated, or killed run is not a result",
        "Prefer an isolated scratch target",
        "Do not overwrite or discard unrelated changes",
    ):
        assert guidance in skill

    assert "/home/pank/" not in skill
    assert "git checkout --" not in skill
