"""Static recipe contracts; transport delivery and watcher behavior are tested separately."""

from pathlib import Path

import pytest

from herdr_master.core.skill_resources import render_skill_for_harness


ROOT = Path(__file__).resolve().parents[1]
HERDR_SKILLS = {
    "aihead": "DIRECT_PARENT_PANE_ID",
    "council": "COORDINATOR_PANE_ID",
    "ergo": "ORCHESTRATOR_PANE_ID",
    "herdr-master": "PARENT_PANE_ID",
}


def skill(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("name", HERDR_SKILLS)
def test_herdr_first_prompt_binds_only_verified_direct_parent(name):
    directive = skill(name).split("**Mandatory first-prompt contract.**", 1)[1].split("\n", 1)[0]
    assert "verified dispatching coordinator's own pane in the exact session" in directive
    assert "fail closed before spawn" in directive
    assert "first submitted task prompt" in directive
    assert f'herdr agent prompt <{HERDR_SKILLS[name]}> "<message>"' in directive
    assert "every question, decision or approval request, blocker, and completion" in directive
    assert "never contact any other pane or the user" in directive
    assert "only corroborate" in directive and "a follow-up cannot supply" in directive


def test_common_directives_cover_spawn_and_replacement_recipes():
    council = skill("council")
    assert "Submit the complete first prompt" in council
    assert "the Scout, initial member drafts, and any authorized replacement" in council
    assert "feedback, revision, or voting" in council
    aihead = skill("aihead")
    assert "including isolated-workspace, reused, and manually started agents" in aihead
    assert "this invoking AiHead through the native return/message channel" in aihead
    master = skill("herdr-master")
    assert "mandatory first-prompt contract to generic jobs, forwarded `/ergo` and `/orchestration`, and any dedicated Reviewer" in master
    assert "Pane launch verification (mandatory)" in master
    ergo = skill("ergo")
    briefs = ergo.split("## 7. Agent Briefs", 1)[1]
    preamble, role_payloads = briefs.split("### Scout Brief", 1)
    assert "every Scout, Architect, Coder (including parallel coders), Reviewer" in preamble
    assert "fix-verification request" in preamble
    # Native assembly may reuse the role payloads without a Herdr callback leaking in.
    assert "herdr agent prompt" not in role_payloads
    for artifact in ("context.md", "plan.md", "coder_<N>_summary.md", "review.md"):
        assert f"RUN_DIR/{artifact}" in role_payloads


@pytest.mark.parametrize("name", HERDR_SKILLS)
def test_herdr_watcher_is_parent_owned_and_scoped_to_exact_registered_children(name):
    body = skill(name)
    watcher = body.split("**Run-scoped child health watcher (Herdr only).**", 1)[1].split("\n\n**", 1)[0]
    assert "private, current-user-owned mode-700" in watcher
    assert "After registering the first confirmed child" in watcher
    assert "one detached process in the exact verified Herdr session" in watcher
    assert "not a pane, subagent, or ledger entry" in watcher
    assert 'python -m herdr_master.pipeline.child_watch --run-dir "$RUN_DIR"' in watcher
    assert '--parent-pane "$PARENT" --parent-generation "$PARENT_GENERATION"' in watcher
    assert '--run-id "$RUN_ID_HEX" --startup-timeout 5' in watcher
    assert "watcher_ready(run_dir, run_id=..., parent_pane_id=..., parent_generation=...)" in watcher
    assert "Every 600 seconds" in watcher
    assert "without accepted-send evidence or a reconciled terminal outcome" in watcher
    assert "once per identity to this spawning parent only" in watcher
    assert "transient RPC/transport errors mean unknown" in watcher
    assert "completion_accepted` only suppresses missing-send alerts" in watcher
    assert "Only the current receipt plus explicit parent ledger reconciliation (`acknowledged` or `reconciled`, not generic `completed` or `closed`) counts as `acknowledged`" in watcher
    assert "not a completion callback" in watcher
    assert "An artifact alone is not success" in watcher
    assert 'touch "$RUN_DIR/child-watch.stop"' in watcher
    assert all(f"child-watch.{file}" in watcher for file in ("lock", "pid"))
    assert "child-watch-state.json" in watcher
    assert "The owner keeps watching across all-terminal snapshots; later registrations and assignments use the same live watcher" in watcher
    assert "It stops on explicit teardown or loss of the bound parent, not when all children are terminal" in watcher
    assert "Only the parent writes it" in body
    assert "`tab_id` must belong to that child workspace" in body
    assert "status=acknowledged" in body


@pytest.mark.parametrize("name", ("ergo", "orchestration"))
def test_native_route_does_not_assume_herdr_or_fake_detached_status(name):
    body = skill(name)
    health = body.split("**Native child health.**", 1)[1].split("\n", 1)[0]
    assert "its own exact native run IDs" in health
    assert "600-second intervals" in health
    assert "terminal result/error callback" in health
    assert "no queryable status adapter, fail closed before dispatching" in health
    assert "Never fabricate a pane mapping or spawn a monitor agent" in health
    assert "exact invok" in body


def test_ergo_closes_only_its_planning_children_after_approval_and_coder_start():
    body = skill("ergo")
    step5 = body.split("### Step 5: Parallel Specialist Coders", 1)[1].split("### Step 6:", 1)[0]
    prune = next(line for line in step5.splitlines() if "Lifecycle Tab Pruning (Scout & Architect)" in line)
    assert "only after AiHead or the human explicitly approves the exact plan version" in prune
    assert "Coders have verifiably started" in prune
    assert "Ergo coordinator" in prune and "closes its own finished Scout and Architect" in prune
    assert "immediately" in prune
    coder_gate = next(line for line in body.splitlines() if "Close each Coder's tab/agent only after" in line)
    assert "Reviewer explicitly records `APPROVED` for that Coder's work" in coder_gate
    assert "parent retains the work, verification, and report" in coder_gate
    assert "No parent closes grandchildren" in body


@pytest.mark.parametrize("name", ("ergo", "orchestration", "council", "herdr-master"))
def test_only_aihead_or_human_can_approve_an_exact_gated_plan(name):
    body = skill(name)
    gate = body.split("**Plan approval authority.**", 1)[1].split("\n", 1)[0]
    assert "Only AiHead acting within its authorized scope or the human" in gate
    assert "exact plan, run ID and plan version to its verified direct parent" in gate
    assert "waits for an explicit `APPROVED`" in gate
    assert "they cannot mint approval" in gate
    assert "Coordinator self-approval" in gate
    assert "A changed plan requires fresh approval" in gate
    assert "Standalone coordinators" in gate and "never use either to bypass" in gate
    assert "a nonblocking update, not an approval" in gate


def test_council_selects_a_candidate_before_gate_and_endorses_after_approval():
    finalization = skill("council").split("### Step 5: Finalization & Plan Artifact", 1)[1]
    assert finalization.index("deliberation do not require prior plan approval") < finalization.index(
        "**Plan approval authority.**"
    ) < finalization.index("aihead council-state finalize")


@pytest.mark.parametrize("name", ("aihead", "ergo", "orchestration", "herdr-master"))
def test_implementation_worker_closure_follows_separate_approval_gates(name):
    body = skill(name)
    planning = next(
        line for line in body.splitlines() if "finished Scout and Architect" in line
    )
    assert "AiHead or the human" in planning and "exact plan version" in planning
    assert "Coders" in planning and "verifiably start" in planning
    assert "close" in planning and "immediately" in planning
    assert any(
        "close" in line.lower() and "only after" in line and "`APPROVED`" in line
        and "Coder" in line
        for line in body.splitlines()
    )
    assert any(
        "unreviewed" in line.lower() and "`NEEDS FIXES`" in line and "open" in line
        or "unreviewed" in line.lower() and "`NEEDS FIXES`" in line and "available" in line
        for line in body.splitlines()
    )
    assert "grandchildren" in body
    assert "no pane closes itself" in body.lower() or "your own pane" in body


@pytest.mark.parametrize("name", ("ergo", "orchestration"))
def test_reviewer_reports_per_coder_status_separately_from_pipeline_verdict(name):
    reviewer = skill(name).split("### Reviewer Brief", 1)[1]
    assert "explicit per-Coder `APPROVED` or `NEEDS FIXES`" in reviewer
    assert "separate overall verdict" in reviewer
    assert "do not mark unreviewed work approved" in reviewer


@pytest.mark.parametrize("name", (*HERDR_SKILLS, "orchestration"))
def test_herdr_completion_requires_received_direct_parent_report(name):
    gate = skill(name).split("**Completion gate (Herdr children).**", 1)[1].splitlines()[0]
    assert "not finished unless it has sent its terminal completion report" in gate
    target = (
        "<direct-parent-pane-id-from-first-prompt>"
        if name in ("ergo", "orchestration")
        else "<direct-parent-pane-id>"
    )
    assert f'herdr agent prompt {target} "<outcome and required artifacts>"' in gate
    assert "to its verified direct parent" in gate
    assert "withholds `done`" in gate and "until it receives that report" in gate
    assert "A file on disk, pane status or exit, and watcher alerts never substitute" in gate
    assert "If the report is missing or delivery is uncertain" in gate
    assert "do not advance any gate that depends on its completion" in gate
    assert "python -m herdr_master.pipeline.parent_contract complete --run-dir" in gate
    assert '--launch-token <launcher-provided-token> --message' in gate
    assert "one actual direct-parent `herdr agent prompt` RPC" in gate
    assert "bound live parent pane/session and generation" in gate
    assert "after receiving the incoming `HERDR TERMINAL REPORT`" in gate
    assert (
        "python -m herdr_master.pipeline.parent_contract verify --run-dir "
        "<launcher-provided-dir> --report-id <id in incoming HERDR TERMINAL REPORT> "
        '--message "<exact full received HERDR TERMINAL REPORT payload, including header>"'
    ) in gate
    assert "against the current assignment’s accepted-send receipt digest" in gate
    assert "exact full received terminal payload" in gate
    assert "tagged registry entry" in gate
    assert "If verification fails, withhold `done`" in gate
    assert "does not apply to untagged shell launches" in gate
    assert "plain shell launch without a launcher token" in gate
    assert "correlate to the exact child launch" in gate
    assert "do not invoke the tagged helper or invent a receipt" in gate
    assert "transport acceptance alone is not evidence the parent observed the report" in gate
    assert "does not authenticate the sender or make target validation and delivery atomic" in gate
    if name in ("ergo", "orchestration"):
        assert "native return/message channel" in gate
        assert "first prompt" in gate
        assert "fail closed" in gate or "STOP" in gate


@pytest.mark.parametrize("name", ("ergo", "orchestration"))
def test_decisions_approvals_and_untagged_reports_use_first_prompt_parent(name):
    body = skill(name)
    assert 'herdr agent prompt "$HERDR_PARENT_PANE_ID"' not in body
    assert "herdr agent prompt $HERDR_PARENT_PANE_ID" not in body
    assert "direct-parent-pane-id-from-first-prompt" in body
    assert "missing, ambiguous" in body or "missing or ambiguous" in body
    assert "parent_contract complete" in body
    assert "environment" in body.lower() and "never replace" in body


@pytest.mark.parametrize("name", ("aihead", "ergo", "orchestration", "herdr-master"))
def test_owned_operator_readmes_use_first_prompt_direct_parent_routing(name):
    readme = (ROOT / "skills" / name / "README.md").read_text(encoding="utf-8")
    assert 'herdr agent prompt "$HERDR_PARENT_PANE_ID"' not in readme
    assert "herdr agent prompt $HERDR_PARENT_PANE_ID" not in readme
    assert "first prompt" in readme.lower()
    assert "fail closed" in readme.lower() or "stop" in readme.lower()
    assert "parent_contract complete" in readme


def test_herdr_master_readme_keeps_user_question_tabs_root_only():
    readme = (ROOT / "skills" / "herdr-master" / "README.md").read_text(encoding="utf-8")
    assert "A verified root Herdr-Master may open an interactive question tab" in readme
    assert "never opens user question tabs directly" in readme
    assert "only the exact AiHead pane named in its first prompt" in readme

    aihead_readme = (ROOT / "skills" / "aihead" / "README.md").read_text(encoding="utf-8")
    assert "A verified parentless root may escalate" in aihead_readme
    assert "concrete direct-parent pane named in its own first prompt" in aihead_readme
    assert "never opens user question tabs directly" in aihead_readme


@pytest.mark.parametrize(
    ("path", "marker", "detail_offset"),
    (
        ("skills/herdr-master/SKILL.md", "- **Level 2 -> Level 1**:", 0),
        ("skills/aihead/SKILL.md", "Level 2 (Ergo Orchestrator, Council Coordinator", 1),
        ("skills/council/SKILL.md", "Level 2 (`Council Coordinator`) -> Level 1", 1),
        ("skills/council/README.md", "Level 1: AiHead / Master Coordinator", 0),
    ),
)
def test_level_one_hierarchy_names_first_prompt_parent_not_environment_route(path, marker, detail_offset):
    lines = (ROOT / path).read_text(encoding="utf-8").splitlines()
    index = next(i for i, line in enumerate(lines) if marker in line)
    line = lines[index + detail_offset]
    assert "first prompt" in line.lower()
    assert "direct-parent pane" in line.lower()
    assert "via `HERDR_PARENT_PANE_ID`" not in line
    assert "at `HERDR_PARENT_PANE_ID`" not in line
    assert "($HERDR_PARENT_PANE_ID)" not in line
    if path.endswith("SKILL.md"):
        assert "must never replace that pane as the notification target" in line


@pytest.mark.parametrize("name", HERDR_SKILLS)
def test_launcher_receipt_metadata_keeps_tokens_out_of_the_ledger(name):
    body = skill(name)
    assert "literal tenth field `launcher-completion-v1`" in body
    assert "launch token stays in private launcher metadata, never in the TSV" in body
    assert "herdr_master.pipeline.parent_contract.completion_receipt_accepted(" in body
    assert "current assignment pointer" in body
    assert "accepted-send receipt" in body
    assert "transport evidence, not parent observation or sender authentication" in body


def test_concrete_council_terminal_and_escalation_routes_are_transport_specific():
    body = skill("council")
    terminal = body.split("**Hierarchical Parent Notification (One Level Up — Level 2 -> Level 1)**", 1)[1].split("---", 1)[0]
    assert "Launcher-tagged Herdr" in terminal
    assert "parent_contract complete" in terminal
    assert "Do not send a raw terminal `herdr agent prompt`" in terminal
    assert "Untagged Herdr shell" in terminal
    assert "Native:" in terminal and "exact invoking supervisor's native result channel" in terminal
    assert "Only a verified parentless root reports directly to the user" in terminal
    master = skill("herdr-master")
    assert "A delegated coordinator must ask its verified direct parent" in master
    assert "never open a user question tab" in master


def test_composed_agy_contract_keeps_native_routes_and_read_only_policy():
    rendered = render_skill_for_harness("orchestration", "agy")
    body, overlay = rendered.split("## Antigravity (`agy`) Harness Notes", 1)
    assert body.count("Mandatory first-prompt contract (all native roles)") == 1
    assert "every question, decision, approval request, blocker, and completion" in body
    assert "Never contact the user, any pane, or a grandparent" in body
    assert "`Prompt` for `invoke_subagent` or `brief` for `swarm(action=\"add\")`" in overlay
    assert "after any required effort/persona prefix" in overlay
    assert "`EnableWriteTools: false`" in overlay
    assert "`flash_lite`, `flash`, `pro`" in overlay
    assert '`workspace: "inherit"`' in overlay
    assert '`workspace: "share"`' in overlay
    assert "herdr agent prompt" not in overlay


# Canonical worker-tier documentation phrases shared by Tasks 3–5.
P1 = "Tier 1 = fast/cheap, Tier 2 = decent/all-around, Tier 3 = frontier/clever"
P2A = "balanced: prefers tier 2 and tier 1; tier 3 (frontier) only when truly necessary"
P2B = "frugal: normally tier 1; may rise to tier 2"
P2C = "quality: uses tier 3 (frontier); uses tier 2 (all-around) only when quality will not be lost"
P2D = "fast: prefers fast tier 1/2 models"
P3 = "Thinking effort is guidance, not a one-to-one lock: AiHead or the Architect combines worker tier and effort."
P4 = "Option 1: tiers are enforced only on the controlled dispatch path (aihead worker dispatch); a direct agent launch is not intercepted or blocked."
P5 = "Role tiers (guidance): balanced = scout tier 1, architect tier 3, reviewer tier 2; frugal shifts roles down (tier 1, at most tier 2); quality shifts roles up (tier 3, tier 2 only when quality will not be lost)."


def normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def assert_phrases(text: str, *phrases: str) -> None:
    body = normalized(text)
    for phrase in phrases:
        assert normalized(phrase) in body


def test_aihead_worker_completion_uses_verified_direct_parent():
    body = normalized(skill("aihead"))
    assert "reports completion to $herdr_parent_pane_id" not in body
    assert "reports completion to `$herdr_parent_pane_id`" not in body
    assert "reports completion to its verified direct parent" in body


def test_ergo_skill_documents_operational_tier_dispatch_sequence():
    body = normalized(skill("ergo"))
    assert "blocked, not operational" not in body
    assert "intentionally fails closed with" not in body
    step5 = body.split("### step 5: parallel specialist coders", 1)[1].split("### step 6:", 1)[0]
    command_sequence = step5.split("before dispatch, capture a fresh policy snapshot", 1)[1]
    commands = (
        "aihead worker snapshot",
        "aihead worker run-policy create",
        "aihead worker manifest validate",
        "aihead worker approval create",
        "aihead worker dispatch",
    )
    positions = [command_sequence.index(command) for command in commands]
    assert positions == sorted(positions)
    assert_phrases(body, P1, P2A, P2B, P2C, P2D, P3, P4, P5, "worker_mode")


def test_ergo_readme_documents_worker_tiers_and_controlled_scope():
    readme = (ROOT / "skills" / "ergo" / "README.md").read_text(encoding="utf-8")
    assert_phrases(readme, P1, P3, P4)


@pytest.mark.parametrize("name", ("aihead", "orchestration"))
def test_aihead_and_orchestration_skills_document_worker_tier_guidance(name):
    assert_phrases(skill(name), P1, P2A, P2B, P2C, P2D, P3, P5)


def test_orchestration_readme_documents_worker_tiers():
    readme = (ROOT / "skills" / "orchestration" / "README.md").read_text(encoding="utf-8")
    assert_phrases(readme, P1, P3)


def test_root_readme_documents_worker_tiers_and_controlled_scope():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert_phrases(readme, P1, P2A, P2B, P2C, P2D, P3, P4)
