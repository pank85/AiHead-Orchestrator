"""Integrated acceptance tests for Settings, AI Council, and AiHead cross-harness workflows.

Covers:
1. Save and reload all 5 settings sections (Projects, Ergo, Orchestration, Council, AiHead).
2. Named-folder launch selection and strict precedence over defaults.
3. Council launch, bootstrap generation, and pre-allocation roster guard linkage.
4. Real Council state-machine: early consensus (round 1) vs 5-round disagreement and fallback vote
   with deterministic tie-breaking. Hard cycle limits (round 6 refusal) and neutrality guards.
5. AiHead enable/disable/discovery and escalation across ergo, orchestration, council, and aihead-cli.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.core.config import (
    AiHeadConfig,
    CouncilConfig,
    CouncilMember,
    FavoriteProject,
    HerdrMasterConfig,
    ModelsConfig,
    OrchestrationConfig,
    ProjectsConfig,
    is_folder_available,
    load_project_config,
    resolve_effective_folder,
    resolve_folder_path,
    save_project_config,
)
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.pipeline.ai_head import (
    AiHeadDecisionReply,
    AiHeadDecisionRequest,
    build_ai_head_discovery,
    build_headless_cli_argv,
    execute_cli_decision,
)
from herdr_master.pipeline.bootstrap import (
    BOOTSTRAP_SENTINEL,
    build_bootstrap_prompt,
)
from herdr_master.pipeline.council import (
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
    submit_revised_plan,
    submit_unified_feedback,
    submit_vote,
)
from herdr_master.pipeline.launcher import (
    COUNCIL_COORDINATOR_NAME,
    launch_council,
    launch_ergo,
    launch_orchestration,
)

runner = CliRunner()


# ==============================================================================
# Helper fakes
# ==============================================================================


class FakeLaunchCtx:
    def __init__(self, **kwargs):
        self.is_inside_herdr = kwargs.get("is_inside_herdr", True)
        self.session_name = kwargs.get("session_name", "alpha")
        self.socket_path = kwargs.get("socket_path", Path("/tmp/herdr.sock"))
        self.workspace_id = kwargs.get("workspace_id", "w9")
        self.pane_id = kwargs.get("pane_id", "w9:p1")
        self.tab_id = kwargs.get("tab_id")


def _extract_payload(prompt: str) -> dict:
    marker = f"{BOOTSTRAP_SENTINEL}\n```json\n"
    start = prompt.index(marker) + len(marker)
    end = prompt.index("\n```", start)
    return json.loads(prompt[start:end])


def _fake_client():
    client = AsyncMock()
    client.create_tab.return_value = {
        "root_pane": {"pane_id": "w9:p2"},
        "tab": {"tab_id": "w9:t2"},
    }
    client.start_agent.return_value = {"type": "agent_started"}
    client.prompt_agent.return_value = {"type": "ok"}
    return client


def _fake_context(tmp_path: Path) -> FakeLaunchCtx:
    return FakeLaunchCtx(
        is_inside_herdr=True,
        session_name="alpha",
        socket_path=tmp_path / "herdr.sock",
        workspace_id="w9",
        pane_id="w9:p1",
    )


# ==============================================================================
# 1. Save and reload all 5 settings sections
# ==============================================================================


def test_integration_save_and_reload_all_five_settings_sections(tmp_path):
    """Verify full fidelity roundtrip of projects, ergo squad, orchestration, council, and ai_head."""
    toml_path = tmp_path / ".herdr-master.toml"

    config = HerdrMasterConfig(
        projects=ProjectsConfig(
            default_folder=str(tmp_path / "default_dir"),
            favorites=[
                FavoriteProject(name="core-api", path=str(tmp_path / "core_api")),
                FavoriteProject(name="web-ui", path=str(tmp_path / "web_ui")),
            ],
        ),
        models=ModelsConfig(
            default_squad={
                "scout": "agy:gemini-3.8-flash-medium:medium",
                "architect": "pi:openai-codex/gpt-6.1-sol:max",
                "reviewer": "pi:xai/grok-4.7:high",
            }
        ),
        orchestration=OrchestrationConfig(
            harness=HarnessKind.CLAUDE,
            scout_model="claude-sonnet-4-6",
            architect_model="claude-opus-4-6",
            worker_models=["claude-sonnet-4-6", "claude-haiku-4-6"],
            reviewer_model="claude-opus-4-6",
        ),
        council=CouncilConfig(
            coordinator=HarnessBinding.from_slug(
                "pi:openai-codex/gpt-6.1-sol:max", label="coordinator"
            ),
            scout=HarnessBinding.from_slug(
                "agy:gemini-3.8-flash-medium:medium", label="scout"
            ),
            members=[
                CouncilMember.from_slug(
                    "agy:gemini-3.8-flash", label="member-1", order=0
                ),
                CouncilMember.from_slug(
                    "pi:xai/grok-4.7", label="member-2", order=1
                ),
                CouncilMember.from_slug(
                    "claude:claude-sonnet-4-6", label="member-3", order=2
                ),
            ],
        ),
        ai_head=AiHeadConfig(
            enabled=True,
            soul="Staff Systems Architect obsessed with UX, accessibility, and zero-leak abstractions",
            mode="cli",
            binding=HarnessBinding.from_slug(
                "claude:claude-sonnet-4-6", label="ai-head"
            ),
        ),
    )

    # 1. Save atomically
    saved_path = save_project_config(config, toml_path)
    assert saved_path == toml_path
    assert toml_path.is_file()

    # 2. Reload and assert all sections match
    reloaded = load_project_config(toml_path)

    # Tab 1: Projects
    assert reloaded.projects.default_folder == str(tmp_path / "default_dir")
    assert len(reloaded.projects.favorites) == 2
    assert reloaded.projects.favorites[0].name == "core-api"
    assert reloaded.projects.favorites[0].path == str(tmp_path / "core_api")
    assert reloaded.projects.favorites[1].name == "web-ui"

    # Tab 2: Ergo Squad default
    assert reloaded.models.default_squad is not None
    assert "architect" in reloaded.models.default_squad

    # Tab 3: Orchestration
    assert reloaded.orchestration.harness == HarnessKind.CLAUDE
    assert reloaded.orchestration.scout_model == "claude-sonnet-4-6"
    assert reloaded.orchestration.architect_model == "claude-opus-4-6"
    assert reloaded.orchestration.worker_models == [
        "claude-sonnet-4-6",
        "claude-haiku-4-6",
    ]
    assert reloaded.orchestration.reviewer_model == "claude-opus-4-6"

    # Tab 4: AI Council
    assert reloaded.council.coordinator.kind == HarnessKind.PI
    assert reloaded.council.coordinator.model == "openai-codex/gpt-6.1-sol"
    assert reloaded.council.scout.kind == HarnessKind.AGY
    assert reloaded.council.scout.model == "gemini-3.8-flash-medium"
    assert len(reloaded.council.members) == 3
    assert [m.label for m in reloaded.council.members] == [
        "member-1",
        "member-2",
        "member-3",
    ]
    assert [m.enabled for m in reloaded.council.members] == [True, True, True]

    # Tab 5: AiHead Persona
    assert reloaded.ai_head.enabled is True
    assert (
        reloaded.ai_head.soul
        == "Staff Systems Architect obsessed with UX, accessibility, and zero-leak abstractions"
    )
    assert reloaded.ai_head.mode == "cli"
    assert reloaded.ai_head.harness == HarnessKind.CLAUDE
    assert reloaded.ai_head.model == "claude-sonnet-4-6"


# ==============================================================================
# 2. Named-folder launch selection (strict precedence over defaults)
# ==============================================================================


@pytest.mark.asyncio
async def test_integration_named_folder_precedence_and_field_selection(tmp_path):
    """Verify: explicit launch folder > configured default folder > anchor directory / cwd."""
    default_dir = tmp_path / "configured_default"
    default_dir.mkdir()
    explicit_dir = tmp_path / "explicit_launch"
    explicit_dir.mkdir()
    cwd_dir = tmp_path / "current_working_dir"
    cwd_dir.mkdir()

    cfg = HerdrMasterConfig(
        projects=ProjectsConfig(
            default_folder=str(default_dir),
            favorites=[
                FavoriteProject(name="explicit-fav", path=str(explicit_dir)),
            ],
        )
    )

    # 1. Precedence: explicit folder wins when supplied
    eff_explicit = resolve_effective_folder(
        explicit_folder=explicit_dir, config=cfg, anchor_dir=cwd_dir
    )
    assert eff_explicit == explicit_dir.resolve()

    # 2. Precedence: configured default wins when explicit is None
    eff_default = resolve_effective_folder(
        explicit_folder=None, config=cfg, anchor_dir=cwd_dir
    )
    assert eff_default == default_dir.resolve()

    # 3. Precedence: anchor/cwd wins when config has no default_folder
    empty_cfg = HerdrMasterConfig()
    eff_anchor = resolve_effective_folder(
        explicit_folder=None, config=empty_cfg, anchor_dir=cwd_dir
    )
    assert eff_anchor == cwd_dir.resolve()


# ==============================================================================
# 3. Council launch, bootstrap, and guard linkage
# ==============================================================================


@pytest.mark.asyncio
async def test_integration_council_launch_bootstrap_and_guard_linkage(tmp_path):
    """Verify launch_council constructs valid bootstrap payload and enforces roster pre-allocation."""
    client = _fake_client()
    ctx = _fake_context(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    council_cfg = CouncilConfig(
        coordinator=HarnessBinding.from_slug(
            "pi:openai-codex/gpt-6.1-sol:max", label="coordinator"
        ),
        scout=HarnessBinding.from_slug(
            "agy:gemini-3.8-flash-medium:medium", label="scout"
        ),
        members=[
            CouncilMember.from_slug(
                "agy:gemini-3.8-flash", label="m1", order=0
            ),
            CouncilMember.from_slug(
                "pi:xai/grok-4.7", label="m2", order=1
            ),
        ],
    )

    # A. Pre-allocation guard check: < 2 enabled members must raise ValueError BEFORE allocating pane
    bad_cfg = CouncilConfig.model_construct(
        coordinator=council_cfg.coordinator,
        scout=council_cfg.scout,
        members=[
            CouncilMember(
                kind=HarnessKind.AGY, model="gemini-3.8-flash", label="solo", enabled=True
            ),
            CouncilMember(
                kind=HarnessKind.PI, model="xai/grok-4.7", label="disabled", enabled=False
            ),
        ],
    )

    with patch(
        "herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx
    ):
        with pytest.raises(ValueError, match="at least 2 enabled members"):
            await launch_council(
                client=client,
                intent="test guard",
                workspace_label="bad-council",
                council=bad_cfg,
                cwd=repo_dir,
            )
        client.start_agent.assert_not_called()

    # B. Valid launch produces coordinator pane and verified bootstrap document
    with patch(
        "herdr_master.pipeline.launcher.resolve_herdr_context", return_value=ctx
    ):
        result = await launch_council(
            client=client,
            intent="Architect resilient cross-region queue",
            workspace_label="council-queue",
            council=council_cfg,
            cwd=repo_dir,
            ai_head=True,
        )

    assert result["agent_name"] == COUNCIL_COORDINATOR_NAME
    assert result["workspace_id"] == "w9"
    assert result["coordinator_pane_id"] == "w9:p2"
    assert result["intent"] == "Architect resilient cross-region queue"

    # Verify coordinator was started and prompted with canonical bootstrap prompt
    coordinator_call = next(
        c for c in client.start_agent.await_args_list if c.kwargs.get("name") == COUNCIL_COORDINATOR_NAME
    )
    assert coordinator_call.kwargs["pane_id"] == "w9:p2"

    client.prompt_agent.assert_awaited()
    # The prompt_agent call for coordinator delivers the bootstrap document
    prompt_args = [
        c.args[1] for c in client.prompt_agent.await_args_list
        if len(c.args) > 1 and "HERDR_MASTER_BOOTSTRAP_V1" in c.args[1]
    ]
    assert len(prompt_args) >= 1
    init_prompt = prompt_args[0]
    assert "HERDR_MASTER_BOOTSTRAP_V1" in init_prompt

    payload = _extract_payload(init_prompt)
    assert payload is not None
    assert payload["entrypoint"] == "council"
    assert payload["intent"] == "Architect resilient cross-region queue"

    council_payload = payload["council"]
    assert council_payload["max_rounds"] == 5
    assert council_payload["coordinator"]["kind"] == "pi"
    assert council_payload["scout"]["kind"] == "agy"
    assert len(council_payload["members"]) == 2
    assert [m["label"] for m in council_payload["members"]] == ["m1", "m2"]

    # AiHead block was wired into bootstrap
    assert "ai_head" in payload
    assert payload["ai_head"]["enabled"] is True


# ==============================================================================
# 4. Real Council state-machine: Early consensus vs 5th-round fallback vote
# ==============================================================================


def test_integration_council_early_consensus_round_1(tmp_path):
    """Verify early consensus in Round 1 stops cycles immediately and finalizes UNANIMOUS_CONSENSUS."""
    run_dir = tmp_path / "council_consensus_run"
    context_file = tmp_path / "context.md"
    context_file.write_text("Discovered architecture: microservices with grpc.")

    # 1. Initialize with coordinator, scout, and 2 members
    init(
        run_dir=run_dir,
        roster=["m1", "m2"],
        specifications="Design auth token service",
    )

    # 2. Record scout context
    record_scout_context(run_dir=run_dir, context_file=context_file)

    # 3. Round 0 initial drafts
    plan_m1 = tmp_path / "plan_m1.md"
    plan_m2 = tmp_path / "plan_m2.md"
    plan_m1.write_text("# Plan M1: JWT with RS256")
    plan_m2.write_text("# Plan M2: Paseto v4 tokens")

    submit_initial_plan(run_dir=run_dir, member_id="m1", plan_file=plan_m1)
    submit_initial_plan(run_dir=run_dir, member_id="m2", plan_file=plan_m2)

    # 4. Open Round 1
    open_round(run_dir=run_dir, round_number=1)

    # Both members independently cross-review and agree to adopt m1's initial plan (m1_v0)
    fb_m1 = tmp_path / "fb_m1.md"
    fb_m2 = tmp_path / "fb_m2.md"
    fb_m1.write_text("I support Plan M1 as optimal.")
    fb_m2.write_text("I agree and adopt Plan M1.")

    submit_feedback(
        run_dir=run_dir,
        round_number=1,
        member_id="m1",
        feedback_file=fb_m1,
        supported_plan_id="m1_v0",
    )
    submit_feedback(
        run_dir=run_dir,
        round_number=1,
        member_id="m2",
        feedback_file=fb_m2,
        supported_plan_id="m1_v0",
    )

    # 5. Check consensus - must reach consensus immediately
    consensus_res = check_consensus(run_dir=run_dir, round_number=1)
    assert consensus_res["consensus"] is True
    assert consensus_res["plan_id"] == "m1_v0"

    # 6. Finalize run
    final_res = finalize(run_dir=run_dir)
    assert final_res["outcome"] == "UNANIMOUS_CONSENSUS"
    assert final_res["rounds_used"] == 1
    assert final_res["winning_plan"] == "m1_v0"

    final_plan_file = run_dir / "final_plan.md"
    assert final_plan_file.is_file()
    content = final_plan_file.read_text()
    assert "UNANIMOUS_CONSENSUS" in content
    assert "JWT with RS256" in content


def test_integration_council_five_cycles_disagreement_and_deterministic_vote_tie_break(
    tmp_path,
):
    """Verify 5 feedback cycles without consensus trigger 1-round vote with deterministic tie-break."""
    run_dir = tmp_path / "council_vote_run"
    context_file = tmp_path / "context.md"
    context_file.write_text("Context: high concurrency backend.")

    # 3 members with deterministic pinned order: alpha, beta, gamma
    init(
        run_dir=run_dir,
        roster=["alpha", "beta", "gamma"],
        specifications="Design cache sharding",
    )
    record_scout_context(run_dir=run_dir, context_file=context_file)

    # Round 0 drafts
    for mem in ("alpha", "beta", "gamma"):
        pf = tmp_path / f"plan_{mem}_r0.md"
        pf.write_text(f"# Plan by {mem}")
        submit_initial_plan(run_dir=run_dir, member_id=mem, plan_file=pf)

    # Run through 5 full feedback cycles with persistent disagreement
    for r in range(1, 6):
        open_round(run_dir=run_dir, round_number=r)

        # Members each support their own plan
        for mem in ("alpha", "beta", "gamma"):
            fb = tmp_path / f"fb_{mem}_r{r}.md"
            fb.write_text(f"Round {r} critique from {mem}")
            prior_plan = f"{mem}_v{r-1}"
            submit_feedback(
                run_dir=run_dir,
                round_number=r,
                member_id=mem,
                feedback_file=fb,
                supported_plan_id=prior_plan,
            )

        cons = check_consensus(run_dir=run_dir, round_number=r)
        assert cons["consensus"] is False

        # Unified feedback and revisions
        uf = tmp_path / f"unified_r{r}.md"
        uf.write_text(f"Unified synthesis for round {r}")
        submit_unified_feedback(
            run_dir=run_dir, round_number=r, unified_feedback_file=uf
        )

        for mem in ("alpha", "beta", "gamma"):
            rev = tmp_path / f"rev_{mem}_r{r}.md"
            rev.write_text(f"Revised plan from {mem} round {r}")
            submit_revised_plan(
                run_dir=run_dir,
                round_number=r,
                member_id=mem,
                plan_file=rev,
                adopted_plan_id=f"{mem}_v{r}",
            )

    # Hard Limit Invariant: Attempting round 6 must be rejected!
    with pytest.raises(
        CouncilValidationError, match="Maximum of 5 feedback cycles allowed"
    ):
        open_round(run_dir=run_dir, round_number=6)

    # Coordinator Neutrality Invariant: Coordinator cannot vote
    with pytest.raises(CouncilValidationError, match="Coordinator and Scout do not vote"):
        submit_vote(
            run_dir=run_dir, member_id="coordinator", voted_plan_id="alpha_v5"
        )

    # Fallback 1-round vote: 3-way split (1 vote for alpha, 1 for beta, 1 for gamma)
    submit_vote(
        run_dir=run_dir, member_id="alpha", voted_plan_id="alpha_v5"
    )
    submit_vote(
        run_dir=run_dir, member_id="beta", voted_plan_id="beta_v5"
    )
    submit_vote(
        run_dir=run_dir, member_id="gamma", voted_plan_id="gamma_v5"
    )

    # Finalize: deterministic tie-break chooses 'alpha' because alpha is first in pinned roster
    final_res = finalize(run_dir=run_dir)
    assert final_res["outcome"] == "MAJORITY_VOTE"
    assert final_res["winning_plan"] == "alpha_v5"
    assert final_res["rounds_used"] == 5

    # Verify written final_plan.md documents deterministic tie-break and tallies
    final_plan_file = run_dir / "final_plan.md"
    assert final_plan_file.is_file()
    body = final_plan_file.read_text()
    assert "MAJORITY_VOTE" in body
    assert "alpha_v5" in body
    assert "Tie-break" in body or "tie-break" in body.lower()

    # Mutation after finalization is sealed
    with pytest.raises(CouncilTransitionError, match="sealed"):
        submit_vote(
            run_dir=run_dir, member_id="alpha", voted_plan_id="beta_v5"
        )

    # Repeated finalize is idempotent
    repeat_res = finalize(run_dir=run_dir)
    assert repeat_res["winning_plan"] == "alpha_v5"


# ==============================================================================
# 5. AiHead enable/disable/discovery and escalation across commands
# ==============================================================================


@pytest.mark.asyncio
async def test_integration_aihead_discovery_and_cli_execution():
    """Verify AiHead discovery generation, direct CLI execution, and ambiguity escalation."""
    head_config = AiHeadConfig(
        enabled=True,
        soul="Software Architect UX specialist",
        mode="cli",
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="general",
            label="ai-head",
        ),
    )

    # 1. Discovery format in CLI mode
    disc = build_ai_head_discovery(head_config, origin_cwd="/tmp/project")
    assert disc.mode == "cli"
    assert disc.enabled is True
    assert disc.harness == "opencode"
    assert disc.argv == ["opencode", "run", "--format", "json", "--model", "general"]

    # 2. Supported headless commands across all 4 harnesses
    for kind, expected_bin in [
        (HarnessKind.OPENCODE, "opencode"),
        (HarnessKind.AGY, "agy"),
        (HarnessKind.CLAUDE, "claude"),
        (HarnessKind.PI, "pi"),
    ]:
        cfg = AiHeadConfig(
            enabled=True,
            binding=HarnessBinding(kind=kind, model="m", label="ai-head"),
        )
        argv = build_headless_cli_argv(cfg)
        assert argv[0] == expected_bin

    # 3. Autonomous decision execution via mock runner
    request = AiHeadDecisionRequest(
        question="Select primary key strategy",
        context="Distributed sharded PostgreSQL tables",
        choices=["UUIDv7", "BIGSERIAL"],
    )

    def mock_decided_runner(cmd, **kwargs):
        reply_json = json.dumps(
            {
                "request_id": request.request_id,
                "status": "decided",
                "decision": "UUIDv7",
                "rationale": "Time-ordered and globally unique across shards.",
            }
        )
        return reply_json, "", 0

    reply = await execute_cli_decision(
        head_config, request, runner=mock_decided_runner
    )
    assert reply.is_decided is True
    assert reply.needs_user is False
    assert reply.decision == "UUIDv7"
    assert "globally unique" in (reply.rationale or "")

    # 4. Ambiguity Escalation Guard: ambiguous/destructive action escalates with needs_user
    def mock_ambiguous_runner(cmd, **kwargs):
        reply_json = json.dumps(
            {
                "request_id": request.request_id,
                "status": "needs_user",
                "decision": None,
                "rationale": "Deleting production data cannot be authorized autonomously.",
                "question": "Do you confirm truncating the audit table in production?",
            }
        )
        return reply_json, "", 0

    reply_esc = await execute_cli_decision(
        head_config, request, runner=mock_ambiguous_runner
    )
    assert reply_esc.is_decided is False
    assert reply_esc.needs_user is True
    assert "confirm truncating" in (reply_esc.question or "")

    # 5. Disabled AiHead does not execute subprocess; immediately escalates
    disabled_cfg = AiHeadConfig(enabled=False)
    reply_disabled = await execute_cli_decision(disabled_cfg, request)
    assert reply_disabled.needs_user is True
    assert "disabled" in (reply_disabled.rationale or "").lower()


def test_integration_cli_commands_aihead_and_state_gate_wiring():
    """Verify CLI commands accept --ai-head/--no-ai-head and state gate subcommands execute cleanly."""
    # 1. aihead-cli --discover
    disc_res = runner.invoke(app, ["aihead-cli", "--discover"])
    assert disc_res.exit_code == 0
    disc_data = json.loads(disc_res.stdout)
    assert "mode" in disc_data
    assert "harness" in disc_data

    # 2. ergo, orchestration, council commands wire --ai-head flag
    with patch(
        "herdr_master.cli.main.launch_ergo", new_callable=AsyncMock
    ) as m_ergo:
        m_ergo.return_value = {
            "session": "s",
            "workspace_id": "w",
            "pane_id": "p",
        }
        res_ergo = runner.invoke(
            app, ["ergo", "task", "--ai-head", "--no-attach"]
        )
        assert res_ergo.exit_code == 0
        assert m_ergo.await_args.kwargs["ai_head"] is True

    with patch(
        "herdr_master.cli.main.launch_orchestration", new_callable=AsyncMock
    ) as m_orch:
        m_orch.return_value = {
            "session": "s",
            "workspace_id": "w",
            "pane_id": "p",
        }
        res_orch = runner.invoke(
            app, ["orchestration", "task", "--no-ai-head", "--no-attach"]
        )
        assert res_orch.exit_code == 0
        assert m_orch.await_args.kwargs["ai_head"] is False

    with patch(
        "herdr_master.cli.main.launch_council", new_callable=AsyncMock
    ) as m_council:
        m_council.return_value = {
            "session": "s",
            "workspace_id": "w",
            "pane_id": "p",
        }
        res_council = runner.invoke(
            app, ["council", "task", "--ai-head", "--no-attach"]
        )
        assert res_council.exit_code == 0
        assert m_council.await_args.kwargs["ai_head"] is True


def test_integration_council_cli_documented_skill_commands_consensus_path(tmp_path: Path):
    """Verify exact documented skill commands in skills/council/SKILL.md execute without errors for consensus path."""
    run_dir = tmp_path / "council-run-consensus"

    # Step 1: Initialization & Scout Discovery
    res_init = runner.invoke(
        app,
        [
            "council-state",
            "init",
            "--run-dir",
            str(run_dir),
            "--roster",
            "member-1,member-2,member-3",
            "--specifications",
            "Architect distributed caching",
        ],
    )
    assert res_init.exit_code == 0, res_init.stdout

    context_file = run_dir / "context.md"
    context_file.write_text("# Scout Context\nVerified cache constraints.\n", encoding="utf-8")

    res_scout = runner.invoke(
        app,
        [
            "council-state",
            "record-scout",
            "--run-dir",
            str(run_dir),
            "--context",
            str(context_file),
        ],
    )
    assert res_scout.exit_code == 0, res_scout.stdout

    # Step 2: Independent Plan Drafting (Round 0)
    res_open_r0 = runner.invoke(
        app,
        ["council-state", "open-round", "--run-dir", str(run_dir), "--round", "0"],
    )
    assert res_open_r0.exit_code == 0, res_open_r0.stdout

    plans_dir = run_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    p1 = plans_dir / "member-1_v0.md"
    p2 = plans_dir / "member-2_v0.md"
    p3 = plans_dir / "member-3_v0.md"
    p1.write_text("# Plan Member 1\nRedis cluster sharding.\n", encoding="utf-8")
    p2.write_text("# Plan Member 2\nMemcached consistent hash.\n", encoding="utf-8")
    p3.write_text("# Plan Member 3\nHybrid tiering.\n", encoding="utf-8")

    for member, p_file in [("member-1", p1), ("member-2", p2), ("member-3", p3)]:
        res_sub = runner.invoke(
            app,
            [
                "council-state",
                "submit-plan",
                "--run-dir",
                str(run_dir),
                "--member",
                member,
                "--plan",
                str(p_file),
                "--revision",
                "0",
            ],
        )
        assert res_sub.exit_code == 0, res_sub.stdout

    # Step 3: Feedback Round 1 -> Unanimous Consensus
    res_open_r1 = runner.invoke(
        app,
        ["council-state", "open-round", "--run-dir", str(run_dir), "--round", "1"],
    )
    assert res_open_r1.exit_code == 0, res_open_r1.stdout

    fb_dir = run_dir / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    fb1 = fb_dir / "member-1_round_1.md"
    fb2 = fb_dir / "member-2_round_1.md"
    fb3 = fb_dir / "member-3_round_1.md"
    fb1.write_text("Adopt member-1 Redis sharding.", encoding="utf-8")
    fb2.write_text("Agreed on member-1 plan.", encoding="utf-8")
    fb3.write_text("Agreed on member-1 plan.", encoding="utf-8")

    for member, fb_file in [("member-1", fb1), ("member-2", fb2), ("member-3", fb3)]:
        res_fb = runner.invoke(
            app,
            [
                "council-state",
                "submit-feedback",
                "--run-dir",
                str(run_dir),
                "--round",
                "1",
                "--member",
                member,
                "--feedback",
                str(fb_file),
                "--selected-plan",
                "member-1_v0",
            ],
        )
        assert res_fb.exit_code == 0, res_fb.stdout

    res_cons = runner.invoke(
        app,
        ["council-state", "consensus", "--run-dir", str(run_dir), "--round", "1"],
    )
    assert res_cons.exit_code == 0, res_cons.stdout
    assert "Unanimous consensus reached" in res_cons.stdout

    # Step 5: Finalization
    res_fin = runner.invoke(
        app,
        ["council-state", "finalize", "--run-dir", str(run_dir)],
    )
    assert res_fin.exit_code == 0, res_fin.stdout
    assert "UNANIMOUS_CONSENSUS" in res_fin.stdout
    assert (run_dir / "final_plan.md").exists()


def test_integration_council_cli_documented_skill_commands_five_cycles_voting_path(tmp_path: Path):
    """Verify exact documented skill commands execute without errors through 5 cycles and fallback vote."""
    run_dir = tmp_path / "council-run-vote"

    # Step 1: Init & Scout Discovery
    res_init = runner.invoke(
        app,
        [
            "council-state",
            "init",
            "--run-dir",
            str(run_dir),
            "--roster",
            "member-1,member-2",
            "--specifications",
            "Consensus vs Voting architecture",
        ],
    )
    assert res_init.exit_code == 0, res_init.stdout

    context_file = run_dir / "context.md"
    context_file.write_text("# Context\nExisting codebase constraints.\n", encoding="utf-8")

    res_scout = runner.invoke(
        app,
        [
            "council-state",
            "record-scout",
            "--run-dir",
            str(run_dir),
            "--context",
            str(context_file),
        ],
    )
    assert res_scout.exit_code == 0, res_scout.stdout

    # Step 2: Round 0 initial plans
    res_open_r0 = runner.invoke(
        app,
        ["council-state", "open-round", "--run-dir", str(run_dir), "--round", "0"],
    )
    assert res_open_r0.exit_code == 0, res_open_r0.stdout

    plans_dir = run_dir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    p1_0 = plans_dir / "member-1_v0.md"
    p2_0 = plans_dir / "member-2_v0.md"
    p1_0.write_text("Member 1 draft\n", encoding="utf-8")
    p2_0.write_text("Member 2 draft\n", encoding="utf-8")

    for m, p in [("member-1", p1_0), ("member-2", p2_0)]:
        res_sub = runner.invoke(
            app,
            [
                "council-state",
                "submit-plan",
                "--run-dir",
                str(run_dir),
                "--member",
                m,
                "--plan",
                str(p),
                "--revision",
                "0",
            ],
        )
        assert res_sub.exit_code == 0, res_sub.stdout

    fb_dir = run_dir / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)

    # Step 3: Cycles 1 through 5 with disagreement and unified feedback revisions
    for cycle in range(1, 6):
        res_open = runner.invoke(
            app,
            ["council-state", "open-round", "--run-dir", str(run_dir), "--round", str(cycle)],
        )
        assert res_open.exit_code == 0, res_open.stdout

        # Member cross-reviews (disagreeing)
        fb1 = fb_dir / f"member-1_round_{cycle}.md"
        fb2 = fb_dir / f"member-2_round_{cycle}.md"
        fb1.write_text(f"Member 1 feedback round {cycle}\n", encoding="utf-8")
        fb2.write_text(f"Member 2 feedback round {cycle}\n", encoding="utf-8")

        res_fb1 = runner.invoke(
            app,
            [
                "council-state",
                "submit-feedback",
                "--run-dir",
                str(run_dir),
                "--round",
                str(cycle),
                "--member",
                "member-1",
                "--feedback",
                str(fb1),
                "--selected-plan",
                f"member-1_v{cycle - 1}",
            ],
        )
        assert res_fb1.exit_code == 0, res_fb1.stdout

        res_fb2 = runner.invoke(
            app,
            [
                "council-state",
                "submit-feedback",
                "--run-dir",
                str(run_dir),
                "--round",
                str(cycle),
                "--member",
                "member-2",
                "--feedback",
                str(fb2),
                "--selected-plan",
                f"member-2_v{cycle - 1}",
            ],
        )
        assert res_fb2.exit_code == 0, res_fb2.stdout

        # Consensus check returns no consensus
        res_cons = runner.invoke(
            app,
            ["council-state", "consensus", "--run-dir", str(run_dir), "--round", str(cycle)],
        )
        assert res_cons.exit_code == 0, res_cons.stdout
        assert "No consensus reached" in res_cons.stdout

        # Coordinator records unified feedback
        u_fb = fb_dir / f"unified_round_{cycle}.md"
        u_fb.write_text(f"Unified feedback cycle {cycle}\n", encoding="utf-8")
        res_ufb = runner.invoke(
            app,
            [
                "council-state",
                "submit-unified-feedback",
                "--run-dir",
                str(run_dir),
                "--round",
                str(cycle),
                "--feedback",
                str(u_fb),
            ],
        )
        assert res_ufb.exit_code == 0, res_ufb.stdout

        # Members submit revised plans for this cycle
        p1_c = plans_dir / f"member-1_v{cycle}.md"
        p2_c = plans_dir / f"member-2_v{cycle}.md"
        p1_c.write_text(f"Member 1 revision {cycle}\n", encoding="utf-8")
        p2_c.write_text(f"Member 2 revision {cycle}\n", encoding="utf-8")

        for m, p in [("member-1", p1_c), ("member-2", p2_c)]:
            res_rev = runner.invoke(
                app,
                [
                    "council-state",
                    "submit-plan",
                    "--run-dir",
                    str(run_dir),
                    "--member",
                    m,
                    "--plan",
                    str(p),
                    "--revision",
                    str(cycle),
                ],
            )
            assert res_rev.exit_code == 0, res_rev.stdout

    # Step 4: Fallback 1-round vote
    res_vote1 = runner.invoke(
        app,
        [
            "council-state",
            "vote",
            "--run-dir",
            str(run_dir),
            "--member",
            "member-1",
            "--ballot",
            "member-1_v5",
        ],
    )
    assert res_vote1.exit_code == 0, res_vote1.stdout

    res_vote2 = runner.invoke(
        app,
        [
            "council-state",
            "vote",
            "--run-dir",
            str(run_dir),
            "--member",
            "member-2",
            "--ballot",
            "member-2_v5",
        ],
    )
    assert res_vote2.exit_code == 0, res_vote2.stdout

    # Step 5: Finalization after tie-break
    res_fin = runner.invoke(
        app,
        ["council-state", "finalize", "--run-dir", str(run_dir)],
    )
    assert res_fin.exit_code == 0, res_fin.stdout
    assert "MAJORITY_VOTE" in res_fin.stdout
    assert "member-1_v5" in res_fin.stdout
    assert (run_dir / "final_plan.md").exists()
