"""Contract tests for canonical /ergo and /herdr-master skills."""

from pathlib import Path

import pytest

from herdr_master.core.catalog import (
    CANONICAL_SKILLS,
    CatalogError,
    describe_native_agent,
)
from herdr_master.core.skill_resources import get_skill_source, load_skill_body
from herdr_master.pipeline.squad import (
    format_allowed_coder_models,
    parse_squad_overrides,
    resolve_squad,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_canonical_skills_loadable_and_valid():
    """Every canonical skill must resolve from resources with YAML front matter."""
    for skill_name in CANONICAL_SKILLS:
        source_path = get_skill_source(skill_name)
        assert source_path.exists()
        assert source_path.name == "SKILL.md"

        body = load_skill_body(skill_name)
        assert len(body) > 100
        # load_skill_body strips the YAML front matter
        assert not body.startswith("---")

        raw_text = source_path.read_text(encoding="utf-8")
        assert raw_text.startswith("---\nname: " + skill_name)


def test_ergo_skill_default_mode_is_herdr_and_uses_catalog():
    """Assert /ergo defaults to Herdr mode and queries herdm catalog."""
    body = load_skill_body("ergo")

    # Default Mode is Herdr
    assert "Default Mode (Herdr Cross-Harness)" in body
    assert "Plain `/ergo` defaults to Herdr mode" in body
    assert "`--herdr` is an explicit alias" in body

    # Sourcing from herdm catalog squad (no hand-maintained fallback tables)
    assert "herdm catalog squad" in body
    assert "herdm catalog agent" in body
    assert "Do NOT maintain or consult hardcoded default model tables" in body

    # Actionable install prerequisite on missing resolver
    assert "actionable installation prerequisite error" in body
    assert "Never fall back to an embedded table" in body


def test_ergo_skill_product_squad_contract():
    """Assert product default squad details in ergo skill and offline resolver."""
    body = load_skill_body("ergo")

    # Product default in skill markdown
    assert "agy:gemini-3.8-flash-medium" in body
    assert "pi:openai-codex/gpt-6.1-sol:max" in body
    assert "pi:xai/grok-4.7:high" in body
    assert "agy:gemini-3.8-flash" in body
    assert "pi:xai/grok-4.7" in body

    # Neither Claude Opus nor Argon is a default
    assert "claude:claude-3-opus" in body
    assert "NOT defaults anywhere" in body

    # Offline resolution verification across parent harnesses in default Herdr mode
    resolved = resolve_squad(mode="herdr")
    assert resolved.squad.scout.kind == "agy"
    assert resolved.squad.scout.model == "gemini-3.8-flash-medium"
    assert resolved.squad.scout.effort == "medium"

    assert resolved.squad.architect.kind == "pi"
    assert resolved.squad.architect.model == "openai-codex/gpt-6.1-sol"
    assert resolved.squad.architect.effort == "max"

    assert resolved.squad.reviewer.kind == "pi"
    assert resolved.squad.reviewer.model == "xai/grok-4.7"
    assert resolved.squad.reviewer.effort == "high"

    assert len(resolved.squad.worker_pool) == 2
    assert resolved.squad.worker_pool[0].kind == "agy"
    assert resolved.squad.worker_pool[0].model == "gemini-3.8-flash"
    assert resolved.squad.worker_pool[0].effort is None

    assert resolved.squad.worker_pool[1].kind == "pi"
    assert resolved.squad.worker_pool[1].model == "xai/grok-4.7"
    assert resolved.squad.worker_pool[1].effort is None

    # Formatted allowed coder models block excludes Opus and Argon
    formatted_block = format_allowed_coder_models(resolved.squad, mode="herdr")
    assert "claude-3-opus" not in formatted_block
    assert "gemini-4-argon" not in formatted_block
    assert "agy:gemini-3.8-flash" in formatted_block
    assert "pi:xai/grok-4.7" in formatted_block


def test_ergo_skill_no_per_host_native_fallback_tables():
    """Assert old per-host native default tables are absent."""
    body = load_skill_body("ergo")

    # Obsolete native default tables must NOT exist
    assert "ergo-scout" not in body
    assert "ergo-architect" not in body
    assert "ergo-reviewer" not in body
    assert "Gemini Pro / Argon tier" not in body
    assert "ALLOWED CODER MODELS (paste verbatim" not in body
    assert "gpt-6-luna" not in body
    assert "haiku" not in body
    assert "sonnet" not in body
    assert "grok-build-0.1" not in body


def test_native_mode_offline_behavior():
    """Verify explicit compatible --native behavior vs incompatible product squad."""
    # 1. Un-overridden product squad in native mode fails because it mixes agy and pi
    with pytest.raises(CatalogError, match="incompatible"):
        resolve_squad(mode="native", native_harness="agy")

    # 2. Overriding all roles to compatible agy tiers succeeds
    overrides = parse_squad_overrides(
        scout="agy:flash:medium",
        architect="agy:pro:max",
        reviewer="agy:pro:high",
        workers="agy:flash,agy:pro",
    )
    native_resolved = resolve_squad(
        mode="native",
        native_harness="agy",
        overrides=overrides,
    )
    assert native_resolved.squad.scout.model == "flash"
    assert native_resolved.squad.architect.model == "pro"

    # Native descriptor verifies agy tier handling
    desc = describe_native_agent("agy", "flash", "medium", "scout", "agy")
    assert desc["tool"] == "invoke_subagent"
    assert desc["kwargs"]["Model"] == "flash"

    # Exact CLI slugs are rejected in native agy
    with pytest.raises(CatalogError, match="--herdr"):
        describe_native_agent("agy", "gemini-3.8-flash-medium", "medium", "scout", "agy")


def test_ergo_skill_phase_persona_and_gate_preservation():
    """Assert all phase briefs, persona, approval, and report gates are preserved."""
    body = load_skill_body("ergo")

    # Phases
    assert "Step 1: Isolation" in body
    assert "Step 2: Scout" in body
    assert "Step 3: Architect" in body
    assert "Step 4: User Approval Gate" in body
    assert "Step 5: Parallel Specialist Coders" in body
    assert "Step 6: Full Reviewer & Scoped Fix Loop" in body
    assert "Step 7: Land" in body

    # Gates
    assert "NEVER skip the Scout, the Approval Gate, or the Reviewer" in body
    assert "STOP and wait for explicit user approval" in body
    assert "Adopt this expert persona: <Expert Persona>." in body
    assert "NEVER code directly in the orchestrator" in body
    assert "NEVER run `git reset --hard`" in body

    # Snapshot persistence via catalog
    assert "herdm catalog squad --from-json - --write-snapshot" in body

    # Native reviewer read-only report persistence
    assert "persist" in body.lower()
    assert "RUN_DIR/review.md" in body
    assert "read-only" in body.lower()


def test_ergo_skill_workspace_adoption_and_no_recursive_kickoff():
    """Assert bootstrap adoption contract and no recursive herdm ergo."""
    body = load_skill_body("ergo")

    # Bootstrap owns_workspace boolean is the ONLY ownership signal
    assert "owns_workspace` is the ONLY ownership signal" in body
    assert (
        "Do not set or infer `owns_workspace = false` merely because `workspace_id` is present"
        in body
    )
    # Must NOT force/derive owns_workspace = false merely because workspace_id is present
    assert "the workspace is **adopted** (`owns_workspace = false`)" not in body
    assert "the workspace is adopted (owns_workspace = false)" not in body

    # Present workspace_id means adopt that workspace, not create another
    assert "adopt that workspace directly" in body
    assert "Do NOT call `herdr workspace create` again when `workspace_id` is already set" in body
    assert "if `owns_workspace == true` and not yet created" not in body

    # Close is strictly gated on the bootstrap boolean
    assert "Close the workspace only when bootstrap `owns_workspace` is true" in body
    assert "Adopted workspaces (`owns_workspace == false`) are NEVER closed" in body
    assert "Adopted workspaces are NEVER closed" in body

    # No recursive kickoff
    assert "Do NOT recursively invoke `herdm ergo`" in body
    assert "The current session IS the orchestrator" in body


def test_herdr_master_workspace_adoption_contract():
    """Assert /herdr-master obeys bootstrap owns_workspace and does not hardcode false."""
    body = load_skill_body("herdr-master")

    # Bootstrap boolean is the ONLY ownership signal, not hardcoded false
    assert (
        "This orchestrator runs in an existing Herdr workspace (`owns_workspace = false`)"
        not in body
    )
    assert "owns_workspace` is the ONLY ownership signal" in body
    assert (
        "Do not assume or set `owns_workspace = false` merely because `workspace_id` is present"
        in body
    )

    # When workspace_id is set, adopt that workspace and do not create another
    assert "adopt that workspace directly" in body
    assert "Do NOT call `herdr workspace create` again" in body

    # Workspace close is strictly gated on bootstrap owns_workspace == true
    assert "Close the workspace only when bootstrap `owns_workspace` is true" in body
    assert 'herdr workspace close "$HERDR_WORKSPACE_ID"' in body
    assert "Adopted workspaces (`owns_workspace == false`) are NEVER closed" in body


def test_herdr_master_skill_catalog_consumption_and_bindings():
    """Assert /herdr-master does not substitute parent harness for Scout or Reviewer."""
    body = load_skill_body("herdr-master")

    # Uses catalog
    assert "herdm catalog squad" in body
    assert "herdm catalog agent" in body

    # Scout/bootstrap binding is product Scout (agy:gemini-3.8-flash-medium:medium)
    assert "agy:gemini-3.8-flash-medium:medium" in body
    assert "must not substitute its parent harness for the product Scout/bootstrap binding" in body

    # Dedicated final reviewer is pi:xai/grok-4.7:high
    assert "pi:xai/grok-4.7:high" in body
    expected_phrase = (
        "must not substitute its parent harness for the product Scout/bootstrap "
        "binding or the XAI Grok reviewer"
    )
    assert expected_phrase in body

    # Neither Argon nor Opus is named as a reviewer in herdr-master
    assert "gemini-4-argon" not in body
    assert "claude:opus" not in body

    # Async watchers use catalog mechanism
    assert "role watcher" in body
    assert "herdr agent wait" in body
    assert "must never send input or start a coordinator" in body

    # Resolves canonical /ergo from catalog, not dotfiles
    assert "herdm catalog show --json" in body
    assert "dotfiles" not in body

    # Adopted workspaces are not created or closed
    assert "Adopted workspaces (`owns_workspace == false`) are NEVER closed" in body
    assert "Do NOT recursively invoke `herdm ergo`" in body


def test_orchestration_skill_is_herdr_free_and_keeps_every_gate():
    """Assert /orchestration runs on host subagents only and keeps the pipeline gates."""
    body = load_skill_body("orchestration")

    # Orchestrator never implements; every phase and gate is present
    assert "Never implement code directly in the main conversation" in body
    assert "## 2. Scout — discovery (never skip)" in body
    assert "## 3. Architect — design and allocation (never skip)" in body
    assert "## 5. User Approval Gate (never skip)" in body
    assert "## 6. Parallel Specialist Coders" in body
    assert "## 7. Blind Reviewer and Scoped Fix Loop (never skip)" in body
    assert "## 8. Land: Sync, Then One Squash Commit" in body

    # Hard rules preserved
    assert "Never skip the Scout, the Approval Gate, or the Reviewer" in body
    assert "Never code in the orchestrator" in body
    assert "Adopt this expert persona: <Expert Persona>." in body
    assert "Never `git reset --hard`" in body
    assert "Never invent a model ID" in body

    # Herdr surface strictly forbidden from all pipeline execution and state management
    for forbidden in (
        "herdr tab",
        "herdr workspace",
        "herdr status",
        "herdm catalog",
        "herdm ergo",
        "HERDR_WORKSPACE_ID",
        "owns_workspace",
        "herdr agent start",
        "herdr agent wait",
        "herdr agent send-keys",
    ):
        assert forbidden not in body, f"/orchestration must not reference {forbidden}"

    # Precisely assert that any 'herdr agent' command is strictly a parent notification prompt
    import re
    prompt_targets = re.findall(r"herdr agent prompt\s+([^\s\"]+|\"[^\"]+\")", body)
    assert prompt_targets, "Expected delegated parent prompt notification"
    allowed_parent_targets = {'"$HERDR_PARENT_PANE_ID"'}
    for target in prompt_targets:
        assert target in allowed_parent_targets, (
            f"Forbidden herdr agent prompt target in /orchestration: {target}. "
            f"Must target parent pane ID variable only."
        )

    # Ensure no other 'herdr agent' subcommands exist anywhere in the body
    agent_subcommands = re.findall(r"herdr agent\s+([a-zA-Z0-9_-]+)", body)
    assert all(cmd in {"prompt", "get", "read"} for cmd in agent_subcommands), (
        f"Forbidden herdr agent subcommands in /orchestration: {agent_subcommands}"
    )

    # Negative assertions: ensure no arbitrary agent target, raw placeholder, or pane target exists
    for bad_target in ('"$HERDR_PANE_ID"', '<child', '$child', '<worker', '<target>', '<PARENT_PANE_ID>'):
        assert f"herdr agent prompt {bad_target}" not in body, (
            f"Forbidden non-parent target in /orchestration: {bad_target}"
        )

    # HERDR_PANE_ID must only ever appear as HERDR_PARENT_PANE_ID
    assert "HERDR_PANE_ID" not in body.replace("HERDR_PARENT_PANE_ID", "")

    # Host adapter table covers all native subagent hosts
    for host_tool in ("invoke_subagent", "subagent", "Agent", "delegate_task"):
        assert host_tool in body
    assert "EnableWriteTools: false" in body

    # Read-only reviewer report persistence without granting write access
    assert "RUN_DIR/review.md" in body
    assert "persist it into" in body


def test_orchestration_can_run_as_a_delegated_job():
    """Assert /orchestration adapts when a master hands it a job in a new tab."""
    body = load_skill_body("orchestration")

    assert "Delegated from a master orchestrator" in body
    assert "Adopt, never recreate." in body
    assert "Never land into the integration branch." in body
    assert "do step 1 and stop" in body
    assert "do not delete the branch or worktree, on success or failure" in body
    assert "Never start another master or pipeline." in body

    # Delegation must not weaken the mandatory gates
    assert "do not get skipped just because a master is watching" in body


def test_delegated_report_preserves_actual_results():
    body = load_skill_body("orchestration")
    report = body.split("## 9. Report & Hierarchical Parent Notification", 1)[1].split("## Hard Rules", 1)[0]
    assert "actual assigned branch" in report
    assert "commit SHA (or say no commit was made)" in report
    assert "actual Reviewer verdict" in report
    assert "including `unitTestsMode=none`" in report
    assert "never describe skipped tests as passed" in report
    assert "Never claim a target-branch squash or integration for delegated work" in report
    assert "review <actual-verdict-or-not-run>" in report
    assert "verification <actual-results-and-skips>" in report


def test_herdr_master_forwards_orchestration_jobs():
    """Assert /herdr-master can dispatch a job as a /orchestration pipeline in its own tab."""
    body = load_skill_body("herdr-master")

    assert "Forwarding `/orchestration` to Child Sessions" in body
    assert 'run a job "with orchestration"' in body
    assert "prompt the child with `/orchestration <task>`" in body
    assert "skill_sources.orchestration" in body
    assert "one new tab, one isolated worktree" in body
    # Master keeps integration and the final review gate
    assert "**The master alone** runs the Master Final Review Gate" in body


def test_council_skill_contract():
    """Assert /council defines the canonical AI Council protocol and state gates."""
    body = load_skill_body("council")

    # Header and scope
    assert "# /council" in body
    assert "RUN_DIR/final_plan.md" in body
    assert "plan" in body.lower()
    assert "NOT perform code implementation" in body or "never modifies codebase source" in body

    # Roster
    assert "1 Coordinator" in body
    assert "1 Scout" in body
    assert "Dynamic Council Members (> 1)" in body or "dynamic members (> 1)" in body.lower()
    assert "never votes" in body.lower()

    # State-gate commands
    for cmd in (
        "council-state init",
        "council-state open-round",
        "council-state submit-plan",
        "council-state submit-feedback",
        "council-state consensus",
        "council-state vote",
        "council-state finalize",
    ):
        assert cmd in body, f"Expected {cmd} in /council skill contract"

    # Multi-round protocol stages
    assert "Independent Plan Drafting" in body
    assert "Cross-Review" in body
    assert "maximum number of feedback cycles is 5" in body or "Max 5 Cycles" in body
    assert "Immediate Stop on Consensus" in body or "Stop immediately at verified consensus" in body
    assert "Fallback 1-Round Vote" in body or "1-round vote" in body.lower()
    assert "deterministic tie-break" in body.lower()

    # AiHead availability and autonomous decisions
    assert "--ai-head" in body
    assert "--no-ai-head" in body
    assert "TUI Mode" in body
    assert "CLI Mode (100% Herdr-free)" in body
    assert "correlated requests" in body.lower() or "correlation id" in body.lower()
    assert "structured decision" in body.lower()
    assert "delegated scope" in body.lower()
    assert "Destructive operations, security bypasses, or out-of-scope actions always require explicit user approval" in body


def test_ergo_skill_ai_head_section():
    """Assert /ergo includes the AiHead availability and autonomous decision section."""
    body = load_skill_body("ergo")

    assert "AiHead Availability and Autonomous Decisions" in body
    assert "--ai-head" in body
    assert "--no-ai-head" in body
    assert "TUI Mode" in body
    assert "CLI Mode (100% Herdr-free)" in body
    assert "persistent Herdr tab" in body
    assert "correlation ID" in body
    assert "structured artifact" in body
    assert "delegated scope" in body.lower()
    assert "Destructive operations, security bypasses, or out-of-scope actions always require explicit user approval" in body


def test_orchestration_skill_ai_head_section():
    """Assert /orchestration includes AiHead via aihead-cli while retaining native subagents."""
    body = load_skill_body("orchestration")

    assert "AiHead Availability and Autonomous Decisions" in body
    assert "--ai-head" in body
    assert "--no-ai-head" in body
    assert "Retain Native Subagents for All Implementation Roles" in body
    assert "aihead-cli" in body
    assert "decision-only advisor" in body
    assert "CLI Mode (100% Herdr-free)" in body
    assert "correlation ID" in body
    assert "Destructive operations, security bypasses, or out-of-scope actions always require explicit user approval" in body

    # Verify no Herdr surface was introduced in AiHead section
    for forbidden in (
        "herdr tab",
        "herdr workspace",
        "herdr status",
        "herdm catalog",
        "herdm ergo",
        "HERDR_WORKSPACE_ID",
        "owns_workspace",
        "herdr agent start",
        "herdr agent wait",
    ):
        assert forbidden not in body, f"/orchestration must not reference {forbidden}"
    assert "HERDR_PANE_ID" not in body.replace("HERDR_PARENT_PANE_ID", "")


def test_aihead_skill_contract():
    """Assert /aihead operational contract, front matter, workspace adoption, and safety flags."""
    assert "aihead" in CANONICAL_SKILLS
    assert CANONICAL_SKILLS.index("aihead") == 4

    source_path = get_skill_source("aihead")
    assert source_path.exists()
    assert source_path.name == "SKILL.md"

    raw_text = source_path.read_text(encoding="utf-8")
    assert raw_text.startswith("---\nname: aihead\n")
    assert "argument-hint:" in raw_text
    assert "slash: false" in raw_text
    assert "opencode/autoinvoke: false" in raw_text
    assert "${@:-$ARGUMENTS}" in raw_text

    body = load_skill_body("aihead")
    assert len(body) > 100
    assert not body.startswith("---")

    # Command verification
    assert "aihead --help" in body

    # Agent programmatic invocation uses --no-attach
    assert "--no-attach" in body

    # Interactive workspace adoption and automatic server startup
    assert "workspace" in body.lower()
    assert "adopt" in body.lower()

    # Self-contained: No AiHeadDev requirement
    assert "aiheaddev" in body.lower()


def test_aihead_initial_parent_directive_has_workflow_specific_completion():
    """The actual child prompt must not require plan-only Council to commit."""
    body = load_skill_body("aihead")
    directive = body.split("  AiHead Parent Directive:\n", 1)[1].split("  ```", 1)[0]

    assert "herdr agent prompt $HERDR_PANE_ID" in directive
    assert "For an implementation pipeline, upon completion of the assigned branch commit and review" in directive
    assert "report the commit, review, and actual verification" in directive
    assert "For plan-only Council, upon finalization report the outcome and RUN_DIR/final_plan.md" in directive
    assert "do not wait for or make an implementation commit" in directive
    assert "If either workflow is blocked, report the blocker immediately" in directive
    assert "Upon completion of the assigned branch commit and review (or if blocked)" not in directive


def test_no_obsolete_dotfiles_or_hardcoded_paths():
    """Ensure no obsolete dotfiles or hardcoded user paths remain in owned files."""
    aihead_raw = (REPO_ROOT / "skills" / "aihead" / "SKILL.md").read_text(encoding="utf-8")
    council_raw = (REPO_ROOT / "skills" / "council" / "SKILL.md").read_text(encoding="utf-8")
    ergo_raw = (REPO_ROOT / "skills" / "ergo" / "SKILL.md").read_text(encoding="utf-8")
    hm_raw = (REPO_ROOT / "skills" / "herdr-master" / "SKILL.md").read_text(encoding="utf-8")
    orch_raw = (REPO_ROOT / "skills" / "orchestration" / "SKILL.md").read_text(encoding="utf-8")
    skills_cli_raw = (REPO_ROOT / "src" / "herdr_master" / "cli" / "skills.py").read_text(
        encoding="utf-8"
    )
    sh_installer_raw = (REPO_ROOT / "scripts" / "install-skills.sh").read_text(encoding="utf-8")

    for path_str, content in [
        ("skills/aihead/SKILL.md", aihead_raw),
        ("skills/council/SKILL.md", council_raw),
        ("skills/ergo/SKILL.md", ergo_raw),
        ("skills/herdr-master/SKILL.md", hm_raw),
        ("skills/orchestration/SKILL.md", orch_raw),
        ("src/herdr_master/cli/skills.py", skills_cli_raw),
        ("scripts/install-skills.sh", sh_installer_raw),
    ]:
        assert "dotfiles" not in content, f"Found obsolete 'dotfiles' in {path_str}"
        assert "/home/pank/projects/herdr-master" not in content, (
            f"Found hardcoded project path in {path_str}"
        )


def test_canonical_skills_hermes_support():
    """Assert all 5 canonical skills accurately define Hermes contracts and constraints."""
    aihead_body = load_skill_body("aihead")
    assert "aihead --harness hermes" in aihead_body
    assert "--provider openai-codex --model gpt-6.1-terra --reasoning high" in aihead_body
    assert "Hermes has no catalog native-subagent adapter or direct headless AiHead runner" in aihead_body
    assert "--yolo" in aihead_body
    assert "--no-skip-permissions" in aihead_body
    assert 'aihead --orchestration "<intent>"' in aihead_body
    assert 'aihead --ergo "<intent>"' in aihead_body
    assert 'aihead --council "<intent>"' in aihead_body

    hm_body = load_skill_body("herdr-master")
    assert "--kind hermes" in hm_body
    assert "supports_native=False" in hm_body
    assert "terminal(command=\"herdr agent wait <target> --timeout <ms>\"" in hm_body
    assert "delegate_task" in hm_body
    assert "--reasoning" in hm_body
    assert "--yolo" in hm_body
    assert "Async delivery is runtime/session-dependent" in hm_body
    assert "Scope this check strictly to when a Hermes child route is selected" in hm_body

    ergo_body = load_skill_body("ergo")
    assert "--kind hermes" in ergo_body
    assert "Hermes (`hermes`)" in ergo_body
    assert "supports_native=False" in ergo_body
    assert "--yolo" in ergo_body
    assert "index.lock" in ergo_body
    assert "Scope Hermes catalog checks strictly" in ergo_body
    assert "STOP and report that native mode is unsupported" in ergo_body
    assert "Do NOT silently switch execution mode to Herdr" in ergo_body
    assert "Branch A — Parent Master Coordinator Gate" in ergo_body
    assert "Branch B — Delegated Autonomous AiHead Gate" in ergo_body
    assert "Branch C — Direct Standalone Human Approval Gate" in ergo_body
    assert "HERDR_NEEDS_USER: Please review and approve" in ergo_body
    assert "STOP and wait for explicit user approval" in ergo_body

    orch_body = load_skill_body("orchestration")
    assert "| Hermes (`hermes`)" in orch_body
    assert "delegate_task" in orch_body
    assert "runtime- and session-dependent" in orch_body
    assert "MUST FAIL CLOSED" in orch_body

    council_body = load_skill_body("council")
    assert "--kind hermes" in council_body
    assert "supports_native=False" in council_body
    assert "Scope this catalog check strictly to requested Hermes member routes" in council_body
    assert "Hermes does not provide a headless CLI runner" in council_body

    aihead_body = load_skill_body("aihead")
    assert "herdm aihead" in aihead_body
    assert "herdm skills install --no-force" in aihead_body
    assert "Harness Skill Target Inventory" in aihead_body
    assert "Profile-Aware No-Clobber Opt-In Collision Procedure" in aihead_body
    assert "NEVER alter, rename, or delete user skills without explicit permission" in aihead_body



def test_delegated_shared_session_parent_contract():
    """Static prose contracts; these do not prove runtime sender identity."""
    import re
    import shlex

    for skill in ("ergo", "orchestration"):
        body = load_skill_body(skill)
        assert "same-session agents can impersonate" in body
        assert "no authenticated sender or security isolation claim" in body
        assert "dispatching parent's own pane ID" in body
        assert "never its grandparent" in body
        assert "no-integration/no-cleanup restrictions" in body
        assert "Only a genuinely standalone invocation may use standalone routing or landing" in body
        assert "A changed plan requires fresh approval" in body
        assert "no Coder starts before approval" in body
        assert "Destructive operations, security bypasses, and out-of-scope actions" in body
        assert "independent trusted provenance" not in body
        assert "authenticated parent approval" not in body

    orch = load_skill_body("orchestration")
    for command in re.findall(r"`(herdr agent prompt [^`]+)`", orch):
        assert shlex.split(command)[3] == "$HERDR_PARENT_PANE_ID"
    assert "Never convert it to standalone" in orch
    gate = orch.split("## 5. User Approval Gate", 1)[1].split("## 6.", 1)[0]
    for branch in ("Delegated parent gate", "Standalone AiHead gate", "Standalone human gate"):
        assert branch in gate
    assert "A plan change invalidates prior approval" in gate

    master = load_skill_body("herdr-master")
    assert "this dispatching master's own `$HERDR_PANE_ID` as the child's direct parent" in master
    assert "master's parent is the child's grandparent" in master
    assert "a root master may have no parent" in master
    assert 'herdr agent prompt <child-pane-id> "<decision>"' in master
    assert "same-session agents can impersonate" in master
    aihead = load_skill_body("aihead")
    assert "AiHead's own `$HERDR_PANE_ID` as the child's direct parent" in aihead
    assert "not authenticated sender verification or security isolation" in aihead


def test_strict_one_level_up_hierarchy_and_nonblocking_contract():
    """Verify all 5 skills enforce non-blocking parents, both decision+completion notifications, and strict 1-level-up hierarchy."""
    aihead = load_skill_body("aihead")
    master = load_skill_body("herdr-master")
    ergo = load_skill_body("ergo")
    council = load_skill_body("council")
    orch = load_skill_body("orchestration")

    for name, body in (
        ("aihead", aihead),
        ("herdr-master", master),
        ("ergo", ergo),
        ("council", council),
        ("orchestration", orch),
    ):
        assert "NEVER block or wait in polling loops" in body, f"{name} missing non-blocking rule"
        assert "HERDR_PARENT_PANE_ID" in body, f"{name} missing HERDR_PARENT_PANE_ID"

    # Ergo Level 3 briefs (Scout, Architect, Coder, Reviewer) notify ONLY ORCHESTRATOR_PANE_ID
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Scout finished:' in ergo
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Scout blocked/decision needed:' in ergo
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Architect finished:' in ergo
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Architect blocked/decision needed:' in ergo
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Coder <N> finished:' in ergo
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Coder <N> blocked/decision needed:' in ergo
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Reviewer finished:' in ergo
    assert 'herdr agent prompt <ORCHESTRATOR_PANE_ID> "Reviewer blocked/decision needed:' in ergo
    assert "NEVER AiHead" in ergo

    # Council Level 3 (Scout & Members) notify ONLY COORDINATOR_PANE_ID; Level 2 notifies HERDR_PARENT_PANE_ID
    assert "COORDINATOR_PANE_ID" in council
    assert "herdr agent prompt <COORDINATOR_PANE_ID>" in council
    assert 'herdr agent prompt "$HERDR_PARENT_PANE_ID" "Council deliberation finished:' in council
    assert 'herdr agent prompt "$HERDR_PARENT_PANE_ID" "Council decision/blocker needed:' in council
    assert "NEVER notify AiHead or Herdr-Master directly" in council


def test_all_skills_require_pane_launch_verification():
    """Every skill that spawns panes must demand evidence of a successful launch."""
    for skill in ("aihead", "herdr-master", "ergo", "orchestration", "council"):
        body = load_skill_body(skill)
        assert "Pane launch verification" in body, skill
        assert "herdr agent get" in body, skill
        assert "agent_status" in body, skill
        assert "agent_not_found" in body, skill

    for skill in ("aihead", "herdr-master"):
        body = load_skill_body(skill)
        assert "opencode v2.2.6 TUI has no `--model` flag" in body, skill
        assert 'herdr agent prompt <PANE> "/models"' in body, skill
        assert "rate-limited" in body, skill


def test_hermes_skill_discovery_production_lookup_in_sandbox(tmp_path):
    """Optional subprocess integration: synthetic candidates, manually assigned tier only."""
    import os
    import subprocess

    python = os.environ.get("HERMES_TEST_PYTHON")
    workspace = os.environ.get("HERMES_TEST_WORKSPACE")
    if not python and not workspace:
        pytest.skip("Optional production discovery not run: set HERMES_TEST_PYTHON and HERMES_TEST_WORKSPACE")
    assert python and workspace, "Both HERMES_TEST_PYTHON and HERMES_TEST_WORKSPACE are required"
    assert Path(python).is_absolute() and Path(workspace).is_absolute()
    script = r'''
import sys
import socket
from pathlib import Path
# This subprocess must not contact a network, regardless of imported modules.
def deny_network(*args, **kwargs):
    raise RuntimeError("network disabled in discovery test")
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network
sys.path.insert(0, sys.argv[1])
from tools.skills_tool import _collect_skill_candidates
from agent.skill_utils import pick_skill_candidate
root = Path(sys.argv[2]) / "skills"
user = root / "custom" / "ergo"
canonical = root / "ergo"
for directory in (user, canonical):
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: ergo\ndescription: test\n---\n# Test\n")
candidates = _collect_skill_candidates("ergo", None, [root])
assert len(candidates) == 2
winner, contenders = pick_skill_candidate([(1, str(root), 1, c[1]) for c in candidates])
assert winner is None and len(contenders) == 2
(user / "SKILL.md").write_text("---\nname: overlay\ndescription: test\n---\n# Test\n")
assert len(_collect_skill_candidates("ergo", None, [root])) == 2
user.rename(user.with_name("overlay"))
assert len(_collect_skill_candidates("ergo", None, [root])) == 1
assert len(_collect_skill_candidates("overlay", None, [root])) == 1
quoted = root / "group" / "other" / "SKILL.md"
quoted.parent.mkdir(parents=True)
for text in ('---\n"name": ergo\ndescription: user\n---\n',
             '\ufeff---\n\'name\': ergo\ndescription: user\n---\n'):
    quoted.write_text(text, encoding="utf-8")
    found = _collect_skill_candidates("ergo", None, [root])
    assert {item[1] for item in found} == {canonical / "SKILL.md", quoted}
'''
    env = {"HOME": str(tmp_path), "HERMES_HOME": str(tmp_path / "hermes"),
           "TMPDIR": str(tmp_path), "PYTHONDONTWRITEBYTECODE": "1",
           "PATH": os.defpath}
    result = subprocess.run([python, "-B", "-I", "-c", script, workspace, str(tmp_path)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
