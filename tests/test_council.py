"""Comprehensive test suite for AI Council protocol, state machine, and executable guard."""

from pathlib import Path
import pytest

from herdr_master.core.config import CouncilConfig, CouncilMember, HarnessBinding
from herdr_master.pipeline.council import (
    MAX_FEEDBACK_ROUNDS,
    CouncilError,
    CouncilPhase,
    CouncilSecurityError,
    CouncilTransitionError,
    CouncilValidationError,
    check_consensus,
    finalize,
    get_status,
    init,
    open_round,
    record_scout_context,
    submit_feedback,
    submit_initial_plan,
    submit_plan,
    submit_revised_plan,
    submit_unified_feedback,
    submit_vote,
)


def _create_mock_file(path: Path, content: str = "mock content") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_init_roster_validation(tmp_path: Path):
    """Verify roster size, identity uniqueness, and configuration binding support."""
    run_dir = tmp_path / "council_init"

    # Fewer than 2 members rejected
    with pytest.raises(CouncilValidationError, match="at least 2 members"):
        init(run_dir=run_dir, roster=["single-member"])

    # Duplicate member IDs rejected
    with pytest.raises(CouncilValidationError, match="Duplicate member label"):
        init(run_dir=run_dir, roster=["member-1", "member-1"])

    # Collision with coordinator rejected
    with pytest.raises(CouncilValidationError, match="collides with coordinator"):
        init(run_dir=run_dir, roster=["coordinator", "member-2"], coordinator="coordinator")

    # Collision with scout rejected
    with pytest.raises(CouncilValidationError, match="collides with Scout"):
        init(run_dir=run_dir, roster=["member-1", "scout"], scout="scout")

    # Valid comma-separated string initialization
    res_str = init(run_dir=run_dir, roster="alpha,beta,gamma", specifications="Test spec")
    assert res_str["ok"] is True
    assert res_str["phase"] == CouncilPhase.INITIALIZED.value
    assert len(res_str["roster"]["members"]) == 3
    assert (run_dir / "council_state.json").is_file()
    assert (run_dir / "specifications.md").read_text(encoding="utf-8") == "Test spec"

    # Re-initialization of already initialized run rejected
    with pytest.raises(CouncilTransitionError, match="already initialized"):
        init(run_dir=run_dir, roster="alpha,beta")


def test_init_with_council_config(tmp_path: Path):
    """Verify initialization from a typed CouncilConfig object."""
    run_dir = tmp_path / "council_typed"
    config = CouncilConfig(
        coordinator=HarnessBinding.from_slug("pi:openai-codex/gpt-6.1-sol:max", label="lead-architect"),
        scout=HarnessBinding.from_slug("agy:gemini-3.8-flash-medium:medium", label="code-scout"),
        members=[
            CouncilMember.from_slug("agy:gemini-3.8-flash", label="planner-1", order=0),
            CouncilMember.from_slug("pi:xai/grok-4.7", label="planner-2", order=1),
        ],
    )
    res = init(run_dir=run_dir, roster=config)
    assert res["ok"] is True
    assert res["roster"]["coordinator"] == "lead-architect"
    assert res["roster"]["scout"] == "code-scout"
    assert [m["id"] for m in res["roster"]["members"]] == ["planner-1", "planner-2"]


def test_early_consensus_round_1(tmp_path: Path):
    """Deliberation converges to unanimous consensus in Round 1."""
    run_dir = tmp_path / "consensus_r1"
    init(run_dir=run_dir, roster=["alpha", "beta", "gamma"], specifications="Design system")

    # Prerequisite: Scout context
    scout_file = _create_mock_file(tmp_path / "scout_context.md", "# Codebase Context")
    rec = record_scout_context(run_dir, scout_file)
    assert rec["ok"] is True
    assert rec["phase"] == CouncilPhase.SCOUTED.value

    # Round 0: Independent Drafting
    open_round(run_dir, 0)
    for m in ["alpha", "beta", "gamma"]:
        p_file = _create_mock_file(tmp_path / f"{m}_plan.md", f"# Plan by {m}")
        res = submit_initial_plan(run_dir, m, p_file)
        assert res["ok"] is True
    assert res["drafting_complete"] is True

    # Round 1: Cross-Review Feedback
    open_round(run_dir, 1)
    for m in ["alpha", "beta", "gamma"]:
        fb_file = _create_mock_file(tmp_path / f"{m}_fb.md", f"Feedback from {m}")
        # All members support alpha's plan
        fb_res = submit_feedback(run_dir, 1, m, fb_file, supported_plan_id="alpha_v0")
        assert fb_res["ok"] is True
    assert fb_res["feedback_complete"] is True

    # Consensus Check
    cons = check_consensus(run_dir, 1)
    assert cons["consensus"] is True
    assert cons["plan_id"] == "alpha_v0"

    status = get_status(run_dir)
    assert status["phase"] == CouncilPhase.CONSENSUS_REACHED.value
    assert status["winning_plan"] == "alpha_v0"

    # Finalize
    fin = finalize(run_dir)
    assert fin["finalized"] is True
    assert fin["outcome"] == "UNANIMOUS_CONSENSUS"
    assert fin["rounds_used"] == 1
    assert fin["winning_plan"] == "alpha_v0"

    final_plan_text = Path(fin["final_plan_file"]).read_text(encoding="utf-8")
    assert "UNANIMOUS_CONSENSUS" in final_plan_text
    assert "# Plan by alpha" in final_plan_text


def test_early_consensus_round_2(tmp_path: Path):
    """Deliberation reaches unanimous consensus after unified feedback in Round 2."""
    run_dir = tmp_path / "consensus_r2"
    init(run_dir=run_dir, roster=["alpha", "beta"])

    scout_file = _create_mock_file(tmp_path / "context.md")
    record_scout_context(run_dir, scout_file)

    # Round 0
    open_round(run_dir, 0)
    submit_initial_plan(run_dir, "alpha", _create_mock_file(tmp_path / "alpha_0.md", "Plan Alpha v0"))
    submit_initial_plan(run_dir, "beta", _create_mock_file(tmp_path / "beta_0.md", "Plan Beta v0"))

    # Round 1: Disagreement
    open_round(run_dir, 1)
    submit_feedback(run_dir, 1, "alpha", _create_mock_file(tmp_path / "a_fb.md"), supported_plan_id="alpha_v0")
    submit_feedback(run_dir, 1, "beta", _create_mock_file(tmp_path / "b_fb.md"), supported_plan_id="beta_v0")

    cons1 = check_consensus(run_dir, 1)
    assert cons1["consensus"] is False

    # Coordinator produces unified feedback
    unified_1 = _create_mock_file(tmp_path / "unified_1.md", "# Unified Feedback Round 1")
    submit_unified_feedback(run_dir, 1, unified_1)

    # Members submit revised plans
    submit_revised_plan(run_dir, 1, "alpha", _create_mock_file(tmp_path / "alpha_1.md", "Plan Alpha v1"))
    submit_revised_plan(run_dir, 1, "beta", _create_mock_file(tmp_path / "beta_1.md", "Plan Beta v1"))

    # Round 2: Consensus reached
    open_round(run_dir, 2)
    submit_feedback(run_dir, 2, "alpha", _create_mock_file(tmp_path / "a2_fb.md"), supported_plan_id="beta_v1")
    submit_feedback(run_dir, 2, "beta", _create_mock_file(tmp_path / "b2_fb.md"), supported_plan_id="beta_v1")

    cons2 = check_consensus(run_dir, 2)
    assert cons2["consensus"] is True
    assert cons2["plan_id"] == "beta_v1"

    fin = finalize(run_dir)
    assert fin["outcome"] == "UNANIMOUS_CONSENSUS"
    assert fin["rounds_used"] == 2
    assert fin["winning_plan"] == "beta_v1"


def test_disagreement_through_5_cycles_then_fallback_vote(tmp_path: Path):
    """Deliberation runs through all 5 feedback cycles without consensus, then decides via vote."""
    run_dir = tmp_path / "council_vote"
    init(run_dir=run_dir, roster=["member-1", "member-2", "member-3"])

    scout_file = _create_mock_file(tmp_path / "context.md")
    record_scout_context(run_dir, scout_file)

    # Round 0: Drafting
    open_round(run_dir, 0)
    for m in ["member-1", "member-2", "member-3"]:
        submit_initial_plan(run_dir, m, _create_mock_file(tmp_path / f"{m}_0.md", f"Plan {m} v0"))

    # Run cycles 1 through 5 with persistent disagreement
    for r in range(1, MAX_FEEDBACK_ROUNDS + 1):
        open_round(run_dir, r)
        prior = f"v{r - 1}"
        submit_feedback(run_dir, r, "member-1", _create_mock_file(tmp_path / f"fb1_{r}.md"), f"member-1_{prior}")
        submit_feedback(run_dir, r, "member-2", _create_mock_file(tmp_path / f"fb2_{r}.md"), f"member-2_{prior}")
        submit_feedback(run_dir, r, "member-3", _create_mock_file(tmp_path / f"fb3_{r}.md"), f"member-2_{prior}")

        cons = check_consensus(run_dir, r)
        assert cons["consensus"] is False

        # Unified feedback & revised plans
        unified = _create_mock_file(tmp_path / f"unified_{r}.md", f"Unified Feedback Round {r}")
        submit_unified_feedback(run_dir, r, unified)
        for m in ["member-1", "member-2", "member-3"]:
            # Test helper submit_plan as revised plan wrapper
            submit_plan(run_dir, m, _create_mock_file(tmp_path / f"{m}_{r}.md", f"Plan {m} v{r}"), revision=r)

    # Fallback voting after cycle 5
    # member-1 votes for member-2_v5
    # member-2 votes for member-2_v5
    # member-3 votes for member-3_v5
    v1 = submit_vote(run_dir, "member-1", "member-2_v5")
    assert v1["vote_complete"] is False

    v2 = submit_vote(run_dir, "member-2", "member-2_v5")
    assert v2["vote_complete"] is False

    v3 = submit_vote(run_dir, "member-3", "member-3_v5")
    assert v3["vote_complete"] is True
    assert v3["tally_result"]["winner"] == "member-2_v5"
    assert v3["tally_result"]["tallies"]["member-2_v5"] == 2
    assert v3["tally_result"]["tallies"]["member-3_v5"] == 1
    assert v3["tally_result"]["tie_break"] is None

    # Finalize vote
    fin = finalize(run_dir)
    assert fin["finalized"] is True
    assert fin["outcome"] == "MAJORITY_VOTE"
    assert fin["winning_plan"] == "member-2_v5"
    assert fin["rounds_used"] == 5

    final_plan_text = Path(fin["final_plan_file"]).read_text(encoding="utf-8")
    assert "MAJORITY_VOTE" in final_plan_text
    assert "`member-2_v5`: 2 vote(s)" in final_plan_text


def test_round_6_refusal(tmp_path: Path):
    """Enforce hard maximum of 5 feedback cycles."""
    run_dir = tmp_path / "council_cap"
    init(run_dir=run_dir, roster=["alpha", "beta"])

    with pytest.raises(CouncilValidationError, match="Maximum of 5 feedback cycles allowed. Round 6 is rejected."):
        open_round(run_dir, 6)

    with pytest.raises(CouncilValidationError, match="Maximum of 5 feedback cycles allowed"):
        open_round(run_dir, 7)


def test_tie_break_determinism(tmp_path: Path):
    """Verify deterministic tie-break selects earliest candidate according to roster order."""
    run_dir = tmp_path / "council_tie"
    # Pinned roster order: member-1 (0), member-2 (1), member-3 (2)
    init(run_dir=run_dir, roster=["member-1", "member-2", "member-3"])

    scout_file = _create_mock_file(tmp_path / "context.md")
    record_scout_context(run_dir, scout_file)

    open_round(run_dir, 0)
    for m in ["member-1", "member-2", "member-3"]:
        submit_initial_plan(run_dir, m, _create_mock_file(tmp_path / f"{m}_0.md"))

    for r in range(1, MAX_FEEDBACK_ROUNDS + 1):
        open_round(run_dir, r)
        for m in ["member-1", "member-2", "member-3"]:
            submit_feedback(run_dir, r, m, _create_mock_file(tmp_path / f"fb_{m}_{r}.md"), f"{m}_v{r-1}")
        check_consensus(run_dir, r)
        submit_unified_feedback(run_dir, r, _create_mock_file(tmp_path / f"uni_{r}.md"))
        for m in ["member-1", "member-2", "member-3"]:
            submit_revised_plan(run_dir, r, m, _create_mock_file(tmp_path / f"{m}_{r}.md"))

    # Three-way tie: each member votes for their own plan
    submit_vote(run_dir, "member-1", "member-1_v5")
    submit_vote(run_dir, "member-2", "member-2_v5")
    v3 = submit_vote(run_dir, "member-3", "member-3_v5")

    assert v3["vote_complete"] is True
    # Deterministic tie-break chooses member-1 (earliest in roster order)
    assert v3["tally_result"]["winner"] == "member-1_v5"
    assert "Deterministic tie-break selected 'member-1_v5'" in v3["tally_result"]["tie_break"]

    fin = finalize(run_dir)
    assert fin["winning_plan"] == "member-1_v5"
    final_text = Path(fin["final_plan_file"]).read_text(encoding="utf-8")
    assert "Tie-Break Notice:" in final_text
    assert "member-1_v5" in final_text


def test_stale_revisions_and_invalid_plan_references(tmp_path: Path):
    """Enforce rejection of unknown candidate plans, duplicate submissions, and stale revisions."""
    run_dir = tmp_path / "council_invalid"
    init(run_dir=run_dir, roster=["alpha", "beta"])
    record_scout_context(run_dir, _create_mock_file(tmp_path / "ctx.md"))

    open_round(run_dir, 0)
    p_alpha = _create_mock_file(tmp_path / "alpha.md")
    submit_initial_plan(run_dir, "alpha", p_alpha)

    # Duplicate initial plan submission rejected
    with pytest.raises(CouncilValidationError, match="already submitted an initial plan"):
        submit_initial_plan(run_dir, "alpha", p_alpha)

    submit_initial_plan(run_dir, "beta", _create_mock_file(tmp_path / "beta.md"))

    open_round(run_dir, 1)

    # Invalid supported plan candidate rejected
    with pytest.raises(CouncilValidationError, match="Invalid supported plan 'nonexistent_plan'"):
        submit_feedback(run_dir, 1, "alpha", _create_mock_file(tmp_path / "fb.md"), supported_plan_id="nonexistent_plan")

    fb_ok = _create_mock_file(tmp_path / "fb_ok.md")
    submit_feedback(run_dir, 1, "alpha", fb_ok, supported_plan_id="beta_v0")

    # Duplicate feedback submission rejected
    with pytest.raises(CouncilValidationError, match="already submitted feedback"):
        submit_feedback(run_dir, 1, "alpha", fb_ok, supported_plan_id="beta_v0")


def test_out_of_sequence_and_missing_prerequisites(tmp_path: Path):
    """Guard against premature transitions, out-of-order execution, and non-voter ballots."""
    run_dir = tmp_path / "council_sequence"
    init(run_dir=run_dir, roster=["alpha", "beta"], coordinator="coord", scout="scout_agent")

    # Submitting initial plan before Scout context is recorded rejected
    with pytest.raises(CouncilTransitionError, match="Scout context has not been recorded yet"):
        submit_initial_plan(run_dir, "alpha", _create_mock_file(tmp_path / "p.md"))

    # Opening round 0 before Scout context rejected
    with pytest.raises(CouncilTransitionError, match="Scout context must be recorded"):
        open_round(run_dir, 0)

    # Record scout context
    record_scout_context(run_dir, _create_mock_file(tmp_path / "ctx.md"))

    # Opening round 1 before round 0 complete rejected
    with pytest.raises(CouncilTransitionError, match="Round 0 drafting is incomplete"):
        open_round(run_dir, 1)

    # Complete round 0
    submit_initial_plan(run_dir, "alpha", _create_mock_file(tmp_path / "a.md"))
    submit_initial_plan(run_dir, "beta", _create_mock_file(tmp_path / "b.md"))

    # Skipping rounds (e.g. 0 -> 2) rejected
    with pytest.raises(CouncilTransitionError, match="Round 1 was never opened"):
        open_round(run_dir, 2)

    open_round(run_dir, 1)

    # Checking consensus before all feedback is submitted rejected
    with pytest.raises(CouncilTransitionError, match="Feedback incomplete"):
        check_consensus(run_dir, 1)

    # Submitting revised plan before unified feedback rejected
    with pytest.raises(CouncilTransitionError, match="Unified feedback must be recorded"):
        submit_revised_plan(run_dir, 1, "alpha", _create_mock_file(tmp_path / "rev.md"))

    # Voting prematurely (before round 5 finishes) rejected
    with pytest.raises(CouncilTransitionError, match="Voting is only permitted after 5 feedback cycles"):
        submit_vote(run_dir, "alpha", "alpha_v0")

    # Finalizing before consensus or voting rejected
    with pytest.raises(CouncilTransitionError, match="Deliberation must reach unanimous consensus or complete voting"):
        finalize(run_dir)


def test_coordinator_and_scout_cannot_vote(tmp_path: Path):
    """Coordinator and Scout must not cast ballots in Council votes."""
    run_dir = tmp_path / "council_non_voters"
    init(run_dir=run_dir, roster=["alpha", "beta"], coordinator="coord_lead", scout="scout_rep")
    record_scout_context(run_dir, _create_mock_file(tmp_path / "ctx.md"))

    open_round(run_dir, 0)
    submit_initial_plan(run_dir, "alpha", _create_mock_file(tmp_path / "a0.md"))
    submit_initial_plan(run_dir, "beta", _create_mock_file(tmp_path / "b0.md"))

    for r in range(1, MAX_FEEDBACK_ROUNDS + 1):
        open_round(run_dir, r)
        submit_feedback(run_dir, r, "alpha", _create_mock_file(tmp_path / f"fa_{r}.md"), f"alpha_v{r-1}")
        submit_feedback(run_dir, r, "beta", _create_mock_file(tmp_path / f"fb_{r}.md"), f"beta_v{r-1}")
        check_consensus(run_dir, r)
        submit_unified_feedback(run_dir, r, _create_mock_file(tmp_path / f"u_{r}.md"))
        submit_revised_plan(run_dir, r, "alpha", _create_mock_file(tmp_path / f"a_{r}.md"))
        submit_revised_plan(run_dir, r, "beta", _create_mock_file(tmp_path / f"b_{r}.md"))

    # Coordinator vote rejected
    with pytest.raises(CouncilValidationError, match="Coordinator and Scout do not vote"):
        submit_vote(run_dir, "coord_lead", "alpha_v5")

    # Scout vote rejected
    with pytest.raises(CouncilValidationError, match="Coordinator and Scout do not vote"):
        submit_vote(run_dir, "scout_rep", "alpha_v5")


def test_mutation_after_finalization_rejected(tmp_path: Path):
    """Sealed council runs reject all subsequent mutations."""
    run_dir = tmp_path / "council_sealed"
    init(run_dir=run_dir, roster=["alpha", "beta"])
    record_scout_context(run_dir, _create_mock_file(tmp_path / "ctx.md"))

    open_round(run_dir, 0)
    submit_initial_plan(run_dir, "alpha", _create_mock_file(tmp_path / "a.md"))
    submit_initial_plan(run_dir, "beta", _create_mock_file(tmp_path / "b.md"))

    open_round(run_dir, 1)
    submit_feedback(run_dir, 1, "alpha", _create_mock_file(tmp_path / "fa.md"), "alpha_v0")
    submit_feedback(run_dir, 1, "beta", _create_mock_file(tmp_path / "fb.md"), "alpha_v0")
    check_consensus(run_dir, 1)

    fin1 = finalize(run_dir)
    assert fin1["finalized"] is True

    # Mutations after finalization rejected
    with pytest.raises(CouncilTransitionError, match="Council run is finalized and sealed"):
        open_round(run_dir, 2)

    with pytest.raises(CouncilTransitionError, match="Council run is finalized and sealed"):
        submit_initial_plan(run_dir, "alpha", _create_mock_file(tmp_path / "a_late.md"))

    with pytest.raises(CouncilTransitionError, match="Council run is finalized and sealed"):
        submit_feedback(run_dir, 1, "alpha", _create_mock_file(tmp_path / "f_late.md"), "alpha_v0")

    with pytest.raises(CouncilTransitionError, match="Council run is finalized and sealed"):
        submit_unified_feedback(run_dir, 1, _create_mock_file(tmp_path / "u_late.md"))

    with pytest.raises(CouncilTransitionError, match="Council run is finalized and sealed"):
        submit_revised_plan(run_dir, 1, "alpha", _create_mock_file(tmp_path / "rev_late.md"))

    with pytest.raises(CouncilTransitionError, match="Council run is finalized and sealed"):
        submit_vote(run_dir, "alpha", "alpha_v0")

    # Finalize call is idempotent
    fin2 = finalize(run_dir)
    assert fin2["finalized"] is True
    assert fin2["winning_plan"] == fin1["winning_plan"]


def test_path_traversal_and_security(tmp_path: Path):
    """Guard against path traversal or illegal characters in member IDs."""
    run_dir = tmp_path / "council_security"

    # Directory traversal in roster member ID
    with pytest.raises(CouncilSecurityError, match="Path traversal characters detected"):
        init(run_dir=run_dir, roster=["../../etc/passwd", "member-2"])

    init(run_dir=run_dir, roster=["member-1", "member-2"])
    record_scout_context(run_dir, _create_mock_file(tmp_path / "ctx.md"))

    # Directory traversal in submit_initial_plan
    with pytest.raises(CouncilSecurityError, match="Path traversal characters detected"):
        submit_initial_plan(run_dir, "../hacker", _create_mock_file(tmp_path / "plan.md"))

    # Invalid special characters
    with pytest.raises(CouncilValidationError, match="contains invalid characters"):
        submit_initial_plan(run_dir, "member$!*", _create_mock_file(tmp_path / "plan.md"))


def test_get_status_inspection(tmp_path: Path):
    """Verify get_status returns accurate inspectable dictionary throughout lifecycle."""
    run_dir = tmp_path / "council_status"
    init(run_dir=run_dir, roster=["alpha", "beta"], specifications="Status test")

    s0 = get_status(run_dir)
    assert s0["phase"] == CouncilPhase.INITIALIZED.value
    assert s0["is_terminal"] is False
    assert s0["scout_context_recorded"] is False
    assert s0["roster"]["members"][0]["id"] == "alpha"

    record_scout_context(run_dir, _create_mock_file(tmp_path / "ctx.md"))
    s1 = get_status(run_dir)
    assert s1["phase"] == CouncilPhase.SCOUTED.value
    assert s1["scout_context_recorded"] is True

    open_round(run_dir, 0)
    submit_initial_plan(run_dir, "alpha", _create_mock_file(tmp_path / "a.md"))
    submit_initial_plan(run_dir, "beta", _create_mock_file(tmp_path / "b.md"))

    open_round(run_dir, 1)
    s2 = get_status(run_dir)
    assert s2["phase"] == CouncilPhase.ROUND_FEEDBACK.value
    assert s2["current_round"] == 1

    submit_feedback(run_dir, 1, "alpha", _create_mock_file(tmp_path / "fa.md"), "alpha_v0")
    submit_feedback(run_dir, 1, "beta", _create_mock_file(tmp_path / "fb.md"), "alpha_v0")
    check_consensus(run_dir, 1)

    s3 = get_status(run_dir)
    assert s3["phase"] == CouncilPhase.CONSENSUS_REACHED.value
    assert s3["consensus"]["achieved"] is True
    assert s3["winning_plan"] == "alpha_v0"

    finalize(run_dir)
    s4 = get_status(run_dir)
    assert s4["phase"] == CouncilPhase.FINALIZED.value
    assert s4["is_terminal"] is True
    assert s4["final_plan_file"] is not None


def test_finalize_custom_output_file(tmp_path: Path):
    """Verify finalize accepts and writes to a custom output path."""
    run_dir = tmp_path / "council_custom_out"
    custom_out = tmp_path / "custom" / "custom_plan.md"
    init(run_dir=run_dir, roster=["m1", "m2"])
    record_scout_context(run_dir, _create_mock_file(tmp_path / "ctx.md"))

    open_round(run_dir, 0)
    submit_initial_plan(run_dir, "m1", _create_mock_file(tmp_path / "m1.md", "# M1 Custom Content"))
    submit_initial_plan(run_dir, "m2", _create_mock_file(tmp_path / "m2.md", "# M2 Custom Content"))

    open_round(run_dir, 1)
    submit_feedback(run_dir, 1, "m1", _create_mock_file(tmp_path / "f1.md"), "m1_v0")
    submit_feedback(run_dir, 1, "m2", _create_mock_file(tmp_path / "f2.md"), "m1_v0")
    check_consensus(run_dir, 1)

    fin = finalize(run_dir, output_file=custom_out)
    assert fin["final_plan_file"] == str(custom_out.resolve())
    assert custom_out.is_file()
    assert "# M1 Custom Content" in custom_out.read_text(encoding="utf-8")
