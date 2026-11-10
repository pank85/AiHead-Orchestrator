"""Unit tests for AiHead lifecycle, discovery descriptors, and execution runtimes."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.core.config import DEFAULT_AI_HEAD_SOUL, AiHeadConfig
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind
from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.socket import HerdrRPCError
from herdr_master.herdr.snapshot import (
    AgentSnapshot,
    PaneSnapshot,
    SessionSnapshot,
    TabSnapshot,
    WorkspaceSnapshot,
)
from herdr_master.pipeline.ai_head import (
    CORE_WORKFLOWS,
    AiHeadDecisionReply,
    AiHeadDecisionRequest,
    AiHeadExecutionError,
    AiHeadReplyValidationError,
    AiHeadTimeoutError,
    _clear_running_head_states,
    _load_running_head_state,
    _save_running_head_state,
    assess_workflow_recommendation,
    build_agent_args,
    build_ai_head_decision_prompt,
    build_ai_head_discovery,
    build_ai_head_system_prompt,
    build_headless_cli_argv,
    ensure_herdr_ai_head,
    execute_cli_decision,
    execute_cli_decision_sync,
    parse_ai_head_response,
    request_herdr_decision,
    resolve_decision_artifact_path,
)

# ---------------------------------------------------------------------------
# 1. Schema Validation & Serialization
# ---------------------------------------------------------------------------


def test_request_schema_defaults_and_serialization():
    req = AiHeadDecisionRequest(
        question="Should we use SQLite or PostgreSQL?",
        context="Single-user desktop application with local state.",
        choices=["SQLite", "PostgreSQL"],
    )
    assert req.request_id is not None
    assert len(req.request_id) >= 8
    assert req.question == "Should we use SQLite or PostgreSQL?"
    assert req.choices == ["SQLite", "PostgreSQL"]
    assert req.run_id == ""
    assert req.origin_cwd == ""

    dumped = req.model_dump()
    assert dumped["question"] == req.question
    assert dumped["choices"] == ["SQLite", "PostgreSQL"]

    json_str = req.model_dump_json()
    assert req.request_id in json_str


def test_reply_schema_validation_and_helpers():
    decided = AiHeadDecisionReply(
        request_id="req-123",
        status="decided",
        decision="SQLite",
        rationale="Lightweight and embeddable without server setup.",
    )
    assert decided.is_decided is True
    assert decided.needs_user is False
    assert decided.decision == "SQLite"

    escalated = AiHeadDecisionReply(
        request_id="req-456",
        status="needs_user",
        rationale="Operation requires destructive database deletion.",
        question="Confirm dropping database 'prod_db'?",
    )
    assert escalated.is_decided is False
    assert escalated.needs_user is True
    assert escalated.question == "Confirm dropping database 'prod_db'?"

    # Invalid status raises validation error
    with pytest.raises(ValidationError):
        AiHeadDecisionReply(
            request_id="req-789",
            status="approved",  # only 'decided' | 'needs_user' permitted
            decision="Yes",
        )


@pytest.mark.asyncio
async def test_reply_is_awaitable():
    reply = AiHeadDecisionReply(
        request_id="req-await",
        status="decided",
        decision="Use Cache",
        rationale="Reduces network round trips",
    )
    awaited_reply = await reply
    assert awaited_reply.request_id == "req-await"
    assert awaited_reply.decision == "Use Cache"


# ---------------------------------------------------------------------------
# 2. Persona Prompt Construction
# ---------------------------------------------------------------------------


def test_persona_prompt_uses_soul_and_guardrails():
    # Default soul
    prompt_default = build_ai_head_system_prompt()
    assert DEFAULT_AI_HEAD_SOUL in prompt_default
    assert "Autonomous Decisions (status: 'decided')" in prompt_default
    assert "Plan Approval & Architectural Alignment Gate:" in prompt_default
    assert "Plain-Language Plan Presentation:" in prompt_default
    assert "Mandatory Architectural Alignment:" in prompt_default
    assert "User Escalation When No Solution Exists:" in prompt_default
    assert "Escalation & Guardrails (status: 'needs_user')" in prompt_default
    assert "NEVER authorize destructive operations" in prompt_default
    assert "git reset --hard" in prompt_default

    # Workflow awareness & model discovery in default system prompt
    assert "/orchestration" in prompt_default
    assert "/ergo" in prompt_default
    assert "/council" in prompt_default
    assert "/herdr-master" in prompt_default
    assert "models.default_squad" in prompt_default
    assert "[orchestration]" in prompt_default
    assert "catalog" in prompt_default
    assert "WORKFLOW SELECTION HEURISTICS" in prompt_default
    assert "scale, ambiguity, and subtask structure" in prompt_default

    # Custom soul
    custom_cfg = AiHeadConfig(soul="Principal Architect focused on security and clean architecture")
    prompt_custom = build_ai_head_system_prompt(custom_cfg)
    assert "Principal Architect focused on security and clean architecture" in prompt_custom
    assert "NEVER authorize destructive operations" in prompt_custom
    # Workflow knowledge persists regardless of customized soul
    assert "/orchestration" in prompt_custom
    assert "/ergo" in prompt_custom
    assert "/council" in prompt_custom
    assert "/herdr-master" in prompt_custom


def test_ai_head_core_workflows_registry():
    assert "/orchestration" in CORE_WORKFLOWS
    assert "/ergo" in CORE_WORKFLOWS
    assert "/council" in CORE_WORKFLOWS
    assert "/herdr-master" in CORE_WORKFLOWS

    assert "Single-Harness" in CORE_WORKFLOWS["/orchestration"]["name"]
    assert "Multi-Harness" in CORE_WORKFLOWS["/ergo"]["name"]
    assert "AI Council" in CORE_WORKFLOWS["/council"]["name"]
    assert "Master Orchestrator" in CORE_WORKFLOWS["/herdr-master"]["name"]


def test_ai_head_workflow_selection_heuristics():
    # Ambiguous / RFC -> /council
    rec_council = assess_workflow_recommendation(
        objective="Design multi-tenant event mesh RFC with consensus on partitioning",
        ambiguity="high",
    )
    assert rec_council["workflow"] == "/council"
    assert "consensus" in rec_council["rationale"] or "ambiguity" in rec_council["rationale"]

    # Multiple subtasks / parallel jobs -> /herdr-master
    rec_master = assess_workflow_recommendation(
        objective="Deploy 3 parallel service upgrades across frontend and backend",
        scale="multi_task",
        multiple_subtasks=True,
    )
    assert rec_master["workflow"] == "/herdr-master"
    assert "isolated tabs and worktrees" in rec_master["rationale"]

    # Rigorous feature development -> /ergo
    rec_ergo = assess_workflow_recommendation(
        objective="Implement payment gateway with rigorous review and best-of-breed cross-harness models",
        scale="feature",
    )
    assert rec_ergo["workflow"] == "/ergo"
    assert "best-of-breed" in rec_ergo["rationale"]

    # Fast cohesive single-task -> /orchestration
    rec_orch = assess_workflow_recommendation(
        objective="Add input validation helper to auth utility module",
        scale="small",
        ambiguity="low",
    )
    assert rec_orch["workflow"] == "/orchestration"
    assert "single-harness" in rec_orch["rationale"]


def test_decision_prompt_assembly():
    cfg = AiHeadConfig()
    req = AiHeadDecisionRequest(
        request_id="req-prompt-1",
        run_id="run-20261106",
        origin_cwd="/workspace/project",
        question="Should we add a cache layer?",
        context="Frequent read queries for static config.",
        choices=["Add in-memory cache", "Query database directly"],
    )
    full_prompt = build_ai_head_decision_prompt(req, cfg)
    assert "Request ID: req-prompt-1" in full_prompt
    assert "Run ID: run-20261106" in full_prompt
    assert "Origin CWD: /workspace/project" in full_prompt
    assert "Question: Should we add a cache layer?" in full_prompt
    assert "Context: Frequent read queries for static config." in full_prompt
    assert "- Add in-memory cache" in full_prompt
    assert "- Query database directly" in full_prompt
    assert DEFAULT_AI_HEAD_SOUL in full_prompt


# ---------------------------------------------------------------------------
# 3. Discovery Descriptor Generation
# ---------------------------------------------------------------------------


def test_discovery_herdr_mode():
    cfg = AiHeadConfig(enabled=True)
    discovery = build_ai_head_discovery(
        cfg,
        origin_cwd="/repo",
        mode="herdr",
        session_name="dev-session",
        socket_path="/tmp/herdr.sock",
        workspace_id="ws-123",
        tab_id="tab-456",
        pane_id="pane-789",
    )
    assert discovery.mode == "herdr"
    assert discovery.enabled is True
    assert discovery.harness == cfg.binding.kind.value
    assert discovery.model == cfg.binding.model
    assert discovery.session_name == "dev-session"
    assert discovery.socket_path == "/tmp/herdr.sock"
    assert discovery.workspace_id == "ws-123"
    assert discovery.tab_id == "tab-456"
    assert discovery.pane_id == "pane-789"
    assert discovery.argv == []
    assert discovery.status == "ready"
    assert discovery.reconciled is True
    assert discovery.actual_model is None


def test_discovery_cli_mode():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="general",
            label="ai-head",
        ),
    )
    discovery = build_ai_head_discovery(
        cfg,
        origin_cwd="/repo",
        mode="cli",
    )
    assert discovery.mode == "cli"
    assert discovery.enabled is True
    assert discovery.harness == "opencode"
    assert discovery.model == "general"
    assert discovery.session_name is None
    assert discovery.workspace_id is None
    assert discovery.argv == ["opencode", "run", "--format", "json", "--model", "general"]
    assert discovery.status == "ready"
    assert discovery.reconciled is True
    assert discovery.actual_model is None


def test_discovery_with_busy_and_deferred_reconciliation():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="desired-model-v2",
            label="ai-head",
        ),
    )
    discovery = build_ai_head_discovery(
        cfg,
        origin_cwd="/repo",
        mode="herdr",
        workspace_id="ws-123",
        tab_id="tab-456",
        pane_id="pane-789",
        status="busy",
        reconciled=False,
        actual_model="running-model-v1",
    )
    assert discovery.status == "busy"
    assert discovery.reconciled is False
    assert discovery.actual_model == "running-model-v1"
    # When actual_model is provided, model is set to actual_model
    assert discovery.model == "running-model-v1"


def test_build_headless_cli_argv_all_supported_harnesses():
    # 1. opencode
    cfg_opencode = AiHeadConfig(
        binding=HarnessBinding(kind=HarnessKind.OPENCODE, model="opencode-arch", label="ai-head")
    )
    argv = build_headless_cli_argv(cfg_opencode)
    assert argv == ["opencode", "run", "--format", "json", "--model", "opencode-arch"]
    argv_prompt = build_headless_cli_argv(cfg_opencode, prompt="Decide now")
    assert argv_prompt == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "opencode-arch",
        "Decide now",
    ]

    # 2. agy
    cfg_agy = AiHeadConfig(
        binding=HarnessBinding(
            kind=HarnessKind.AGY,
            model="gemini-3.8-flash-high",
            effort=EffortLevel.HIGH,
            label="ai-head",
        )
    )
    argv_agy = build_headless_cli_argv(cfg_agy, prompt="Prompt text")
    assert argv_agy == [
        "agy",
        "--output-format",
        "json",
        "--model",
        "gemini-3.8-flash-high",
        "--effort",
        "high",
        "--dangerously-skip-permissions",
        "--prompt",
        "Prompt text",
    ]
    assert argv_agy.count("--effort") == 1
    assert argv_agy.count("--dangerously-skip-permissions") == 1

    # 3. claude
    cfg_claude = AiHeadConfig(
        binding=HarnessBinding(
            kind=HarnessKind.CLAUDE,
            model="claude-opus-4-6",
            label="ai-head",
        )
    )
    argv_claude = build_headless_cli_argv(cfg_claude, prompt="Prompt text")
    assert argv_claude == [
        "claude",
        "-p",
        "--model",
        "claude-opus-4-6",
        "--dangerously-skip-permissions",
        "Prompt text",
    ]
    assert argv_claude.count("--dangerously-skip-permissions") == 1

    # 4. pi
    cfg_pi = AiHeadConfig(
        binding=HarnessBinding(
            kind=HarnessKind.PI,
            model="openai-codex/gpt-6.1-sol",
            effort=EffortLevel.MAX,
            label="ai-head",
        )
    )
    argv_pi = build_headless_cli_argv(cfg_pi, prompt="Prompt text")
    assert argv_pi == [
        "pi",
        "-p",
        "--mode",
        "json",
        "--provider",
        "openai-codex",
        "--model",
        "gpt-6.1-sol",
        "--thinking",
        "max",
        "--",
        "Prompt text",
    ]
    assert argv_pi.count("--thinking") == 1
    assert "--dangerously-skip-permissions" not in argv_pi


def test_build_headless_cli_argv_unsupported_harness():
    cfg_codex = AiHeadConfig()
    cfg_codex.binding.kind = HarnessKind.CODEX
    cfg_codex.binding.model = "gpt-4"
    with pytest.raises(ValueError, match="does not support headless direct CLI execution"):
        build_headless_cli_argv(cfg_codex)


# ---------------------------------------------------------------------------
# 4. Mocked Herdr Mode Lifecycle & Execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_reuses_existing_tab():
    _clear_running_head_states()
    client = MagicMock(spec=HerdrClient)
    cfg = AiHeadConfig(enabled=True)
    kind_str = cfg.binding.kind.value
    model_str = cfg.binding.model
    effort_str = cfg.binding.effort.value if cfg.binding.effort else None
    _save_running_head_state(
        "/repo",
        "ws-1",
        "pane-ai-head",
        {
            "kind": kind_str,
            "model": model_str,
            "effort": effort_str,
            "args": list(
                build_agent_args(
                    kind_str,
                    model_str,
                    effort_str,
                    skip_permissions=True,
                )
            ),
            "soul": cfg.soul,
        },
    )
    snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[
            TabSnapshot(tab_id="tab-ai-head", workspace_id="ws-1", label="ai-head"),
            TabSnapshot(tab_id="tab-other", workspace_id="ws-1", label="coder"),
        ],
        panes=[
            PaneSnapshot(pane_id="pane-ai-head", workspace_id="ws-1", tab_id="tab-ai-head"),
            PaneSnapshot(pane_id="pane-other", workspace_id="ws-1", tab_id="tab-other"),
        ],
        agents=[
            AgentSnapshot(
                pane_id="pane-ai-head",
                agent=cfg.binding.kind.value,
                name="ai-head",
                agent_status="idle",
            )
        ],
    )
    client.get_snapshot = AsyncMock(return_value=snap)
    client.create_tab = AsyncMock()
    client.start_agent = AsyncMock()
    client.prompt_agent = AsyncMock()

    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd="/repo"
    )

    assert res["reused"] is True
    assert res["reconciled"] is True
    assert res["busy"] is False
    assert res["status"] == "ready"
    assert res["tab_id"] == "tab-ai-head"
    assert res["pane_id"] == "pane-ai-head"
    assert res["enabled"] is True
    client.create_tab.assert_not_called()
    client.start_agent.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_reconciles_stale_model_or_args(tmp_path: Path):
    """Verify state persistence and reconciliation for model-only and soul-only changes."""
    _clear_running_head_states()
    client = MagicMock(spec=HerdrClient)
    origin_cwd = str(tmp_path)

    cfg1 = AiHeadConfig(
        enabled=True,
        soul="Original Soul",
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="model-v1",
            label="ai-head",
        ),
    )

    # Standard AgentSnapshot - no extra fields or subclasses
    agent_rec = AgentSnapshot(
        pane_id="pane-ai-head",
        agent="opencode",
        name="ai-head",
        agent_status="idle",
    )

    snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[TabSnapshot(tab_id="tab-ai-head", workspace_id="ws-1", label="ai-head")],
        panes=[PaneSnapshot(pane_id="pane-ai-head", workspace_id="ws-1", tab_id="tab-ai-head")],
        agents=[agent_rec],
    )
    client.get_snapshot = AsyncMock(return_value=snap)
    client.create_tab = AsyncMock()
    client.start_agent = AsyncMock(return_value={"status": "started"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    # Pre-seed initial running state for cfg1
    _save_running_head_state(
        origin_cwd,
        "ws-1",
        "pane-ai-head",
        {
            "kind": "opencode",
            "model": "model-v1",
            "effort": None,
            "args": list(
                build_agent_args(
                    "opencode",
                    "model-v1",
                    None,
                    skip_permissions=True,
                )
            ),
            "soul": "Original Soul",
        },
    )

    # First call reuses matching state
    res1 = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg1, origin_cwd=origin_cwd
    )
    assert res1["reused"] is True
    assert res1["reconciled"] is True
    client.start_agent.assert_not_called()

    # a) Same configuration -> reuses agent, start_agent not called
    client.start_agent.reset_mock()
    client.prompt_agent.reset_mock()
    res_same = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg1, origin_cwd=origin_cwd
    )
    assert res_same["reused"] is True
    client.start_agent.assert_not_called()
    client.prompt_agent.assert_not_called()

    # b) Model-only change on same harness -> triggers client.start_agent with new args and prompt
    cfg_new_model = AiHeadConfig(
        enabled=True,
        soul="Original Soul",
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="model-v2",
            label="ai-head",
        ),
    )
    client.start_agent.reset_mock()
    client.prompt_agent.reset_mock()
    res_new_model = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg_new_model, origin_cwd=origin_cwd
    )
    assert res_new_model["reused"] is True
    client.start_agent.assert_called_once()
    assert "model-v2" in client.start_agent.call_args.kwargs["args"]
    client.prompt_agent.assert_called_once()

    # c) Soul-only change on same harness and model -> triggers start_agent and delivers new prompt
    cfg_new_soul = AiHeadConfig(
        enabled=True,
        soul="Completely New Soul 2.0",
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="model-v2",
            label="ai-head",
        ),
    )
    client.start_agent.reset_mock()
    client.prompt_agent.reset_mock()
    res_new_soul = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg_new_soul, origin_cwd=origin_cwd
    )
    assert res_new_soul["reused"] is True
    client.start_agent.assert_called_once()
    client.prompt_agent.assert_called_once()
    assert "Completely New Soul 2.0" in client.prompt_agent.call_args.args[1]

    # Calling again with cfg_new_soul now reuses without restarting
    client.start_agent.reset_mock()
    client.prompt_agent.reset_mock()
    res_reused = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg_new_soul, origin_cwd=origin_cwd
    )
    assert res_reused["reused"] is True
    client.start_agent.assert_not_called()
    client.prompt_agent.assert_not_called()

    # Persistent state file was created and can be reloaded
    state_file = tmp_path / ".herdr-master.ai-head-state.json"
    assert state_file.exists()
    loaded_state = _load_running_head_state(origin_cwd, "ws-1", "pane-ai-head")
    assert loaded_state is not None
    assert loaded_state["soul"] == "Completely New Soul 2.0"
    assert loaded_state["model"] == "model-v2"


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_reconciles_unknown_state_live_agent(tmp_path: Path):
    """When a live agent runs but running_state is None, reconcile and restart agent."""
    _clear_running_head_states()
    client = MagicMock(spec=HerdrClient)
    origin_cwd = str(tmp_path)
    cfg = AiHeadConfig(
        enabled=True,
        soul="Known Desired Soul",
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="model-v1",
            label="ai-head",
        ),
    )
    snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[TabSnapshot(tab_id="tab-ai-head", workspace_id="ws-1", label="ai-head")],
        panes=[PaneSnapshot(pane_id="pane-ai-head", workspace_id="ws-1", tab_id="tab-ai-head")],
        agents=[
            AgentSnapshot(
                pane_id="pane-ai-head",
                agent="opencode",
                name="ai-head",
                agent_status="idle",
            )
        ],
    )
    client.get_snapshot = AsyncMock(return_value=snap)
    client.create_tab = AsyncMock()
    client.start_agent = AsyncMock(return_value={"status": "started"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    # Verify no pre-existing state
    assert _load_running_head_state(origin_cwd, "ws-1", "pane-ai-head") is None

    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd=origin_cwd
    )

    assert res["reused"] is True
    assert res["reconciled"] is True
    assert res["busy"] is False
    client.create_tab.assert_not_called()
    client.start_agent.assert_called_once()
    assert "model-v1" in client.start_agent.call_args.kwargs["args"]
    client.prompt_agent.assert_called_once()
    assert "Known Desired Soul" in client.prompt_agent.call_args.args[1]

    # Verify state was saved to persistence
    saved_state = _load_running_head_state(origin_cwd, "ws-1", "pane-ai-head")
    assert saved_state is not None
    assert saved_state["model"] == "model-v1"
    assert saved_state["soul"] == "Known Desired Soul"


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_defers_reconcile_when_agent_busy(tmp_path: Path):
    """When an agent is busy working, defer reconciliation without killing the session."""
    _clear_running_head_states()
    client = MagicMock(spec=HerdrClient)
    origin_cwd = str(tmp_path)

    # Save existing running state with old-model
    _save_running_head_state(
        origin_cwd,
        "ws-1",
        "pane-ai-head",
        {
            "kind": "opencode",
            "model": "old-model",
            "effort": None,
            "args": ["opencode", "run", "--model", "old-model"],
            "soul": "Old Soul",
        },
    )

    # New config with new-model
    cfg_new = AiHeadConfig(
        enabled=True,
        soul="New Soul",
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="new-model",
            label="ai-head",
        ),
    )

    # Agent is currently working (busy)
    snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[TabSnapshot(tab_id="tab-ai-head", workspace_id="ws-1", label="ai-head")],
        panes=[
            PaneSnapshot(
                pane_id="pane-ai-head",
                workspace_id="ws-1",
                tab_id="tab-ai-head",
                agent_status="working",
            )
        ],
        agents=[
            AgentSnapshot(
                pane_id="pane-ai-head",
                agent="opencode",
                name="ai-head",
                agent_status="working",
            )
        ],
    )
    client.get_snapshot = AsyncMock(return_value=snap)
    client.start_agent = AsyncMock()
    client.prompt_agent = AsyncMock()

    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg_new, origin_cwd=origin_cwd
    )

    # Safe policy: do NOT kill busy agent!
    client.start_agent.assert_not_called()
    client.prompt_agent.assert_not_called()
    assert res["busy"] is True
    assert res["reconciled"] is False
    assert res["status"] == "busy"
    assert res["reused"] is True
    assert res["model"] == "old-model"

    # When the caller IS the ai-head pane itself (HERDR_PANE_ID == pane-ai-head),
    # its 'working' status is from the launch command itself — must NOT be marked busy!
    res_caller = await ensure_herdr_ai_head(
        client,
        workspace_id="ws-1",
        head_config=cfg_new,
        origin_cwd=origin_cwd,
        caller_pane_id="pane-ai-head",
    )
    client.start_agent.assert_not_called()
    client.prompt_agent.assert_not_called()
    assert res_caller["busy"] is False
    assert res_caller["reconciled"] is True
    assert res_caller["status"] == "ready"
    assert res_caller["reused"] is True


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_starts_agent_when_dead_or_missing():
    """Verify that when an ai-head tab exists but agent is dead or missing, it starts the agent."""
    client = MagicMock(spec=HerdrClient)
    cfg = AiHeadConfig(enabled=True)

    # 1. Agent exists but is dead
    dead_snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[TabSnapshot(tab_id="tab-ai-head", workspace_id="ws-1", label="ai-head")],
        panes=[PaneSnapshot(pane_id="pane-ai-head", workspace_id="ws-1", tab_id="tab-ai-head")],
        agents=[
            AgentSnapshot(
                pane_id="pane-ai-head",
                agent=cfg.binding.kind.value,
                name="ai-head",
                agent_status="dead",
            )
        ],
    )
    client.get_snapshot = AsyncMock(return_value=dead_snap)
    client.create_tab = AsyncMock()
    client.start_agent = AsyncMock(return_value={"status": "started"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd="/repo"
    )

    assert res["reused"] is True
    assert res["tab_id"] == "tab-ai-head"
    assert res["pane_id"] == "pane-ai-head"
    client.create_tab.assert_not_called()
    client.start_agent.assert_called_once()
    assert client.start_agent.call_args.kwargs["pane_id"] == "pane-ai-head"
    assert client.start_agent.call_args.kwargs["name"] == "ai-head"
    client.prompt_agent.assert_called_once()
    assert DEFAULT_AI_HEAD_SOUL in client.prompt_agent.call_args.args[1]

    # 2. No agent attached at all
    client.start_agent.reset_mock()
    client.prompt_agent.reset_mock()
    no_agent_snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[TabSnapshot(tab_id="tab-ai-head", workspace_id="ws-1", label="ai-head")],
        panes=[PaneSnapshot(pane_id="pane-ai-head", workspace_id="ws-1", tab_id="tab-ai-head")],
        agents=[],
    )
    client.get_snapshot = AsyncMock(return_value=no_agent_snap)

    res2 = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd="/repo"
    )
    assert res2["reused"] is True
    assert res2["pane_id"] == "pane-ai-head"
    client.create_tab.assert_not_called()
    client.start_agent.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_tab_without_pane_creates_new():
    """Verify that if an existing tab has no pane, a new functional tab is created."""
    client = MagicMock(spec=HerdrClient)
    cfg = AiHeadConfig(enabled=True)

    paneless_snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[TabSnapshot(tab_id="tab-broken-head", workspace_id="ws-1", label="ai-head")],
        panes=[],  # No pane exists for this tab
    )
    client.get_snapshot = AsyncMock(return_value=paneless_snap)
    client.create_tab = AsyncMock(return_value={"tab_id": "tab-new-ai", "pane_id": "pane-new-ai"})
    client.start_agent = AsyncMock(return_value={"status": "started"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd="/repo"
    )

    assert res["reused"] is False
    assert res["tab_id"] == "tab-new-ai"
    assert res["pane_id"] == "pane-new-ai"
    client.create_tab.assert_called_once_with(
        workspace_id="ws-1",
        label="ai-head",
        cwd="/repo",
        focus=False,
    )
    client.start_agent.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_creates_new_tab_and_starts_agent():
    client = MagicMock(spec=HerdrClient)
    initial_snap = SessionSnapshot(
        protocol=22,
        workspaces=[],
        tabs=[TabSnapshot(tab_id="tab-main", workspace_id="ws-1", label="main")],
        panes=[PaneSnapshot(pane_id="pane-main", workspace_id="ws-1", tab_id="tab-main")],
    )
    client.get_snapshot = AsyncMock(return_value=initial_snap)
    client.create_tab = AsyncMock(return_value={"tab_id": "tab-new-ai", "pane_id": "pane-new-ai"})
    client.start_agent = AsyncMock(return_value={"status": "started"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    cfg = AiHeadConfig(enabled=True)
    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd="/repo"
    )

    assert res["reused"] is False
    assert res["tab_id"] == "tab-new-ai"
    assert res["pane_id"] == "pane-new-ai"
    assert res["agent_name"] == "ai-head"
    client.create_tab.assert_called_once_with(
        workspace_id="ws-1",
        label="ai-head",
        cwd="/repo",
        focus=False,
    )
    client.start_agent.assert_called_once()
    assert client.start_agent.call_args.kwargs["name"] == "ai-head"
    assert client.start_agent.call_args.kwargs["pane_id"] == "pane-new-ai"
    client.prompt_agent.assert_called_once()
    assert DEFAULT_AI_HEAD_SOUL in client.prompt_agent.call_args.args[1]


@pytest.mark.asyncio
async def test_request_herdr_decision_success(tmp_path: Path):
    client = MagicMock(spec=HerdrClient)
    req = AiHeadDecisionRequest(
        request_id="req-herdr-success",
        question="Adopt strategy A or B?",
        choices=["Strategy A", "Strategy B"],
    )
    artifact_file = tmp_path / f"aihead_decision_{req.request_id}.json"

    # Mock prompt_agent writing the artifact
    async def mock_prompt(pane_id, text, wait=None, timeout_ms=None):
        artifact_file.write_text(
            json.dumps(
                {
                    "request_id": req.request_id,
                    "status": "decided",
                    "decision": "Strategy A",
                    "rationale": "Better architectural cohesion",
                    "question": None,
                }
            ),
            encoding="utf-8",
        )
        return {"type": "agent_prompted"}

    client.prompt_agent = AsyncMock(side_effect=mock_prompt)

    reply = await request_herdr_decision(
        client,
        pane_id="pane-ai-1",
        request=req,
        timeout_ms=5000,
        artifact_path=artifact_file,
    )

    assert reply.request_id == "req-herdr-success"
    assert reply.status == "decided"
    assert reply.decision == "Strategy A"
    assert reply.rationale == "Better architectural cohesion"
    client.prompt_agent.assert_called_once()
    assert (
        "INSTRUCTION: Save your decision as a valid JSON object"
        in client.prompt_agent.call_args.args[1]
    )


@pytest.mark.asyncio
async def test_request_herdr_decision_correlation_mismatch(tmp_path: Path):
    client = MagicMock(spec=HerdrClient)
    req = AiHeadDecisionRequest(
        request_id="req-expected-123",
        question="Choose option",
    )
    artifact_file = tmp_path / "decision.json"

    async def mock_prompt(pane_id, text, wait=None, timeout_ms=None):
        artifact_file.write_text(
            json.dumps(
                {
                    "request_id": "req-wrong-999",  # mismatched
                    "status": "decided",
                    "decision": "Option X",
                }
            ),
            encoding="utf-8",
        )
        return {"type": "agent_prompted"}

    client.prompt_agent = AsyncMock(side_effect=mock_prompt)

    with pytest.raises(AiHeadReplyValidationError, match="Mismatched request_id"):
        await request_herdr_decision(
            client,
            pane_id="pane-ai-1",
            request=req,
            timeout_ms=1000,
            artifact_path=artifact_file,
        )


@pytest.mark.asyncio
async def test_request_herdr_decision_timeout(tmp_path: Path):
    client = MagicMock(spec=HerdrClient)
    req = AiHeadDecisionRequest(request_id="req-timeout", question="Slow question")
    artifact_file = tmp_path / "does_not_exist.json"

    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    with pytest.raises(AiHeadTimeoutError, match="timed out"):
        await request_herdr_decision(
            client,
            pane_id="pane-ai-1",
            request=req,
            timeout_ms=250,  # fast timeout
            artifact_path=artifact_file,
        )


@pytest.mark.asyncio
async def test_request_herdr_decision_serializes_concurrent_calls(tmp_path: Path):
    client = MagicMock(spec=HerdrClient)
    call_log = []

    async def mock_prompt(pane_id, text, wait=None, timeout_ms=None):
        req_id = "req-1" if "req-1" in text else "req-2"
        call_log.append(f"start-{req_id}")
        await asyncio.sleep(0.15)
        call_log.append(f"end-{req_id}")
        art = tmp_path / f"art_{req_id}.json"
        art.write_text(
            json.dumps({"request_id": req_id, "status": "decided", "decision": f"Dec-{req_id}"})
        )
        return {"type": "agent_prompted"}

    client.prompt_agent = AsyncMock(side_effect=mock_prompt)

    req1 = AiHeadDecisionRequest(request_id="req-1", question="Q1")
    req2 = AiHeadDecisionRequest(request_id="req-2", question="Q2")

    task1 = asyncio.create_task(
        request_herdr_decision(
            client,
            pane_id="shared-pane",
            request=req1,
            timeout_ms=5000,
            artifact_path=tmp_path / "art_req-1.json",
        )
    )
    task2 = asyncio.create_task(
        request_herdr_decision(
            client,
            pane_id="shared-pane",
            request=req2,
            timeout_ms=5000,
            artifact_path=tmp_path / "art_req-2.json",
        )
    )

    r1, r2 = await asyncio.gather(task1, task2)
    assert r1.decision == "Dec-req-1"
    assert r2.decision == "Dec-req-2"

    # Ensure calls did not interleave on the same pane
    assert call_log == ["start-req-1", "end-req-1", "start-req-2", "end-req-2"] or call_log == [
        "start-req-2",
        "end-req-2",
        "start-req-1",
        "end-req-1",
    ]


# ---------------------------------------------------------------------------
# 5. Mocked CLI Headless Subprocess Dispatch (100% Herdr-Free)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_cli_decision_raw_json_success():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(kind=HarnessKind.OPENCODE, model="general", label="ai-head"),
    )
    req = AiHeadDecisionRequest(
        request_id="req-cli-1",
        question="Use REST or GraphQL?",
        choices=["REST", "GraphQL"],
    )

    runner_called = {}

    def fake_runner(cmd, input, timeout, cwd, request_file):
        runner_called["cmd"] = cmd
        runner_called["input"] = input
        runner_called["file_existed"] = request_file.exists()
        out = json.dumps(
            {
                "request_id": "req-cli-1",
                "status": "decided",
                "decision": "REST",
                "rationale": "Lower complexity for client requirements.",
            }
        )
        return (out, "", 0)

    reply = await execute_cli_decision(cfg, req, timeout_seconds=10, runner=fake_runner)

    assert reply.request_id == "req-cli-1"
    assert reply.status == "decided"
    assert reply.decision == "REST"
    assert runner_called["file_existed"] is True
    assert "opencode" in runner_called["cmd"][0]


@pytest.mark.asyncio
async def test_execute_cli_decision_markdown_fenced_json_success():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(
            kind=HarnessKind.AGY, model="gemini-3.8-flash-high", label="ai-head"
        ),
    )
    req = AiHeadDecisionRequest(
        request_id="req-fence-1",
        question="Refactor auth service?",
    )

    fenced_output = """
    Thinking hard...
    Analyzing requirements...
    ```json
    {
      "request_id": "req-fence-1",
      "status": "needs_user",
      "rationale": "Refactoring affects active session tokens across all services.",
      "question": "Should existing session tokens be invalidated immediately or gracefully rotated?"
    }
    ```
    Analysis finished.
    """

    def fake_runner(cmd, input, timeout, cwd, request_file):
        return (fenced_output, "", 0)

    reply = await execute_cli_decision(cfg, req, timeout_seconds=10, runner=fake_runner)

    assert reply.request_id == "req-fence-1"
    assert reply.status == "needs_user"
    assert "gracefully rotated" in reply.question


@pytest.mark.asyncio
async def test_execute_cli_decision_subprocess_nonzero_exit():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(kind=HarnessKind.CLAUDE, model="claude-sonnet-4-6", label="ai-head"),
    )
    req = AiHeadDecisionRequest(request_id="req-err", question="Test Q")

    def fake_runner(cmd, input, timeout, cwd, request_file):
        return ("", "Error: Rate limit exceeded", 1)

    with pytest.raises(AiHeadExecutionError, match="exited with return code 1: Error: Rate limit"):
        await execute_cli_decision(cfg, req, timeout_seconds=10, runner=fake_runner)


@pytest.mark.asyncio
async def test_execute_cli_decision_subprocess_timeout():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(
            kind=HarnessKind.PI, model="openai-codex/gpt-6.1-sol", label="ai-head"
        ),
    )
    req = AiHeadDecisionRequest(request_id="req-pi-timeout", question="Test Q")

    async def fake_runner(cmd, input, timeout, cwd, request_file):
        raise asyncio.TimeoutError("Process timed out")

    with pytest.raises(AiHeadTimeoutError, match="timed out"):
        await execute_cli_decision(cfg, req, timeout_seconds=5, runner=fake_runner)


@pytest.mark.asyncio
async def test_execute_cli_decision_create_subprocess_exec_mocked():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(kind=HarnessKind.OPENCODE, model="general", label="ai-head"),
    )
    req = AiHeadDecisionRequest(request_id="req-subproc-1", question="Subproc test")

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(
        return_value=(
            json.dumps(
                {
                    "request_id": "req-subproc-1",
                    "status": "decided",
                    "decision": "Use Subprocess",
                    "rationale": "Direct execution verified",
                }
            ).encode("utf-8"),
            b"",
        )
    )

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)) as mock_exec:
        reply = await execute_cli_decision(cfg, req, timeout_seconds=15)
        assert reply.request_id == "req-subproc-1"
        assert reply.decision == "Use Subprocess"
        assert mock_exec.call_count == 1
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "opencode"
        assert call_args[1] == "run"
        assert call_args[2] == "--format"
        assert call_args[3] == "json"


@pytest.mark.asyncio
async def test_execute_cli_decision_subprocess_exec_timeout():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(kind=HarnessKind.CLAUDE, model="claude-sonnet-4-6", label="ai-head"),
    )
    req = AiHeadDecisionRequest(request_id="req-subproc-timeout", question="Timeout test")

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_proc.kill = MagicMock()
    mock_proc.wait = AsyncMock()

    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
        with pytest.raises(AiHeadTimeoutError, match="timed out"):
            await execute_cli_decision(cfg, req, timeout_seconds=2)
        mock_proc.kill.assert_called_once()
        mock_proc.wait.assert_called_once()


def test_execute_cli_decision_sync_wrapper():
    cfg = AiHeadConfig(
        enabled=True,
        binding=HarnessBinding(kind=HarnessKind.OPENCODE, model="general", label="ai-head"),
    )
    req = AiHeadDecisionRequest(request_id="req-sync-1", question="Sync decision test")

    def fake_runner(cmd, input, timeout, cwd, request_file):
        out = json.dumps(
            {
                "request_id": "req-sync-1",
                "status": "decided",
                "decision": "OK",
                "rationale": "Sync wrapper works",
            }
        )
        return (out, "", 0)

    reply = execute_cli_decision_sync(cfg, req, timeout_seconds=10, runner=fake_runner)
    assert reply.request_id == "req-sync-1"
    assert reply.decision == "OK"


# ---------------------------------------------------------------------------
# 6. Response Parsing Edge Cases
# ---------------------------------------------------------------------------


def test_parse_ai_head_response_variants():
    req_id = "r-variant"

    # 1. Wrapped in 'content' key
    wrapped_json = json.dumps(
        {
            "content": json.dumps(
                {
                    "request_id": req_id,
                    "status": "decided",
                    "decision": "WrapOK",
                }
            )
        }
    )
    res = parse_ai_head_response(wrapped_json, req_id)
    assert res.decision == "WrapOK"

    # 2. Wrapped in list of messages
    list_json = json.dumps(
        [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "request_id": req_id,
                        "status": "needs_user",
                        "question": "Clarify scope?",
                    }
                ),
            },
        ]
    )
    res = parse_ai_head_response(list_json, req_id)
    assert res.status == "needs_user"
    assert res.question == "Clarify scope?"

    # 3. Empty output
    with pytest.raises(AiHeadReplyValidationError, match="Empty output"):
        parse_ai_head_response("   ", req_id)

    # 4. Garbage text
    with pytest.raises(AiHeadReplyValidationError, match="Failed to extract structured JSON reply"):
        parse_ai_head_response("Total non-json garbage text with no json objects.", req_id)


# ---------------------------------------------------------------------------
# 7. Disabled / No-op Handling
# ---------------------------------------------------------------------------


def test_discovery_disabled_flag():
    cfg = AiHeadConfig(enabled=False)
    discovery = build_ai_head_discovery(cfg, origin_cwd="/repo", mode="cli")
    assert discovery.enabled is False


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_disabled_noop():
    client = MagicMock(spec=HerdrClient)
    client.get_snapshot = AsyncMock()
    client.create_tab = AsyncMock()

    cfg = AiHeadConfig(enabled=False)
    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd="/repo"
    )

    assert res["enabled"] is False
    assert res["tab_id"] is None
    assert res["pane_id"] is None
    client.get_snapshot.assert_not_called()
    client.create_tab.assert_not_called()


@pytest.mark.asyncio
async def test_execute_cli_decision_disabled_escalates():
    cfg = AiHeadConfig(enabled=False)
    req = AiHeadDecisionRequest(request_id="req-dis", question="Should we deploy?")

    reply = await execute_cli_decision(cfg, req)
    assert reply.request_id == "req-dis"
    assert reply.status == "needs_user"
    assert "AiHead is disabled in configuration" in reply.rationale
    assert reply.question == "Should we deploy?"


# ---------------------------------------------------------------------------
# 8. Artifact Path Resolution
# ---------------------------------------------------------------------------


def test_resolve_decision_artifact_path(tmp_path: Path):
    req_custom = AiHeadDecisionRequest(
        request_id="req-custom",
        question="Q",
        artifact_path=str(tmp_path / "custom.json"),
    )
    assert resolve_decision_artifact_path(req_custom) == tmp_path / "custom.json"

    req_orch = AiHeadDecisionRequest(
        request_id="req-orch",
        run_id="run-1",
        origin_cwd=str(tmp_path),
        question="Q",
    )
    resolved = resolve_decision_artifact_path(req_orch)
    assert resolved == tmp_path / ".orchestration" / "run-1" / "aihead_decision_req-orch.json"


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_reconciles_unknown_workspace_id():
    """Verify that if caller passes a non-existent workspace_id (e.g. ws-01),
    ensure_herdr_ai_head reconciles to the active workspace instead of failing."""
    client = MagicMock(spec=HerdrClient)
    snap = SessionSnapshot(
        protocol=22,
        focused_workspace_id="ws-live-focused",
        workspaces=[WorkspaceSnapshot(workspace_id="ws-live-focused", label="live-workspace")],
        tabs=[],
        panes=[],
    )
    client.get_snapshot = AsyncMock(return_value=snap)
    client.create_tab = AsyncMock(return_value={"tab_id": "tab-ai", "pane_id": "pane-ai"})
    client.start_agent = AsyncMock(return_value={"status": "started"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    cfg = AiHeadConfig(enabled=True)
    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-01", head_config=cfg, origin_cwd="/repo"
    )

    assert res["workspace_id"] == "ws-live-focused"
    assert res["tab_id"] == "tab-ai"
    client.create_tab.assert_called_once_with(
        workspace_id="ws-live-focused",
        label="ai-head",
        cwd="/repo",
        focus=False,
    )


@pytest.mark.asyncio
async def test_ensure_herdr_ai_head_falls_back_to_new_tab_on_stale_pane_failure():
    """Verify that when start_agent fails on a stale dead pane, the stale tab is closed and a new one is created."""
    client = MagicMock(spec=HerdrClient)
    cfg = AiHeadConfig(enabled=True)

    dead_snap = SessionSnapshot(
        protocol=22,
        workspaces=[WorkspaceSnapshot(workspace_id="ws-1", label="default")],
        tabs=[TabSnapshot(tab_id="tab-stale", workspace_id="ws-1", label="ai-head")],
        panes=[PaneSnapshot(pane_id="pane-stale", workspace_id="ws-1", tab_id="tab-stale")],
        agents=[
            AgentSnapshot(
                pane_id="pane-stale",
                name="ai-head",
                agent_status="dead",
            )
        ],
    )
    client.get_snapshot = AsyncMock(return_value=dead_snap)
    client.close_tab = AsyncMock(return_value={"type": "ok"})
    client.create_tab = AsyncMock(return_value={"tab_id": "tab-new", "pane_id": "pane-new"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})

    # First start_agent call on pane-stale fails, second call on pane-new succeeds
    call_count = 0

    async def mock_start_agent(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("pane_id") == "pane-stale":
            raise HerdrRPCError("agent_launch_failed", "shell process died")
        return {"status": "started"}

    client.start_agent = AsyncMock(side_effect=mock_start_agent)

    res = await ensure_herdr_ai_head(
        client, workspace_id="ws-1", head_config=cfg, origin_cwd="/repo"
    )

    assert res["reused"] is False
    assert res["tab_id"] == "tab-new"
    assert res["pane_id"] == "pane-new"
    client.close_tab.assert_called_once_with("tab-stale")
    client.create_tab.assert_called_once()
    assert client.start_agent.call_count == 2


def test_headless_catalog_tail_keeps_prompt_separators_and_does_not_duplicate_flags():
    """Catalog owns model, effort, and permission flags. Prefixes stay native."""
    opencode = AiHeadConfig(
        dangerously_skip_permissions=True,
        binding=HarnessBinding(
            kind=HarnessKind.OPENCODE,
            model="openai-codex/gpt-6.1-sol",
            effort=EffortLevel.MAX,
            label="ai-head",
        ),
    )
    opencode_argv = build_headless_cli_argv(opencode, prompt="go")
    assert opencode_argv == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "openai/gpt-6.1-sol",
        "go",
    ]
    assert "mini" not in opencode_argv
    assert "--dangerously-skip-permissions" not in opencode_argv
    assert opencode_argv.count("--model") == 1

    agy = AiHeadConfig(
        dangerously_skip_permissions=False,
        binding=HarnessBinding(
            kind=HarnessKind.AGY,
            model="gemini-3.8-flash-high",
            effort=None,
            label="ai-head",
        ),
    )
    agy_argv = build_headless_cli_argv(agy, prompt="go")
    assert agy_argv == [
        "agy",
        "--output-format",
        "json",
        "--model",
        "gemini-3.8-flash-high",
        "--effort",
        "high",
        "--prompt",
        "go",
    ]
    assert agy_argv.count("--effort") == 1
    assert "--dangerously-skip-permissions" not in agy_argv

    claude = AiHeadConfig(
        dangerously_skip_permissions=False,
        binding=HarnessBinding(
            kind=HarnessKind.CLAUDE,
            model="claude-opus-4-6",
            effort=EffortLevel.HIGH,
            label="ai-head",
        ),
    )
    claude_argv = build_headless_cli_argv(claude, prompt="go")
    assert claude_argv == ["claude", "-p", "--model", "claude-opus-4-6", "go"]
    assert "--effort" not in claude_argv
    assert "--dangerously-skip-permissions" not in claude_argv

    pi = AiHeadConfig(
        dangerously_skip_permissions=True,
        binding=HarnessBinding(
            kind=HarnessKind.PI,
            model="m",
            label="ai-head",
        ),
    )
    pi_argv = build_headless_cli_argv(pi, prompt="go")
    assert pi_argv == ["pi", "-p", "--mode", "json", "--model", "m", "--", "go"]
    assert "--dangerously-skip-permissions" not in pi_argv


def test_headless_opencode_strips_only_a_leading_mini_marker():
    cfg = AiHeadConfig(
        binding=HarnessBinding(kind=HarnessKind.OPENCODE, model="general", label="ai-head")
    )
    with patch(
        "herdr_master.pipeline.ai_head.build_agent_args",
        return_value=["--model", "general", "mini"],
    ):
        argv = build_headless_cli_argv(cfg, prompt="go")
    assert argv == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "general",
        "mini",
        "go",
    ]


def test_aihead_cli_discover_honors_saved_settings_without_launching(tmp_path: Path):
    """aihead-cli stays config-driven and must not contact Herdr or a harness."""
    cfg_path = tmp_path / "ai-head.toml"
    cfg_path.write_text(
        """
[ai_head]
enabled = true
dangerously_skip_permissions = false

[ai_head.binding]
kind = "agy"
model = "gemini-3.8-flash-high"
""",
        encoding="utf-8",
    )
    runner = CliRunner()
    with (
        patch("herdr_master.cli.main.HerdrClient") as client,
        patch("herdr_master.cli.main.ensure_herdr_server") as server,
        patch("asyncio.create_subprocess_exec") as subprocess_exec,
        patch("os.execvp") as execvp,
    ):
        result = runner.invoke(
            app,
            ["aihead-cli", "--discover", "--config", str(cfg_path)],
        )

    assert result.exit_code == 0, result.output
    client.assert_not_called()
    server.assert_not_called()
    subprocess_exec.assert_not_called()
    execvp.assert_not_called()
    discovery = json.loads(result.output)
    assert discovery["argv"] == [
        "agy",
        "--output-format",
        "json",
        "--model",
        "gemini-3.8-flash-high",
        "--effort",
        "high",
    ]
    assert discovery["argv"].count("--effort") == 1
    assert "--dangerously-skip-permissions" not in discovery["argv"]
    assert "--skip-permissions" not in result.output


@pytest.mark.asyncio
async def test_ensure_restarts_when_saved_permission_changes_agent_args(tmp_path: Path):
    """Permission changes the stored catalog args, so the existing restart policy applies."""
    _clear_running_head_states()
    origin = str(tmp_path)
    cfg = AiHeadConfig(
        enabled=True,
        dangerously_skip_permissions=False,
        binding=HarnessBinding(
            kind=HarnessKind.AGY,
            model="gemini-2.5-pro",
            effort=EffortLevel.HIGH,
            label="ai-head",
        ),
    )
    _save_running_head_state(
        origin,
        "ws-1",
        "pane-ai-head",
        {
            "kind": "agy",
            "model": "gemini-2.5-pro",
            "effort": "high",
            "args": build_agent_args(
                "agy", "gemini-2.5-pro", "high", skip_permissions=True
            ),
            "soul": cfg.soul,
            "cwd": origin,
        },
    )
    client = MagicMock(spec=HerdrClient)
    client.get_snapshot = AsyncMock(
        return_value=SessionSnapshot(
            protocol=22,
            workspaces=[],
            tabs=[TabSnapshot(tab_id="tab-ai-head", workspace_id="ws-1", label="ai-head")],
            panes=[PaneSnapshot(pane_id="pane-ai-head", workspace_id="ws-1", tab_id="tab-ai-head")],
            agents=[
                AgentSnapshot(
                    pane_id="pane-ai-head",
                    agent="agy",
                    name="ai-head",
                    agent_status="idle",
                )
            ],
        )
    )
    client.start_agent = AsyncMock(return_value={"status": "started"})
    client.prompt_agent = AsyncMock(return_value={"type": "agent_prompted"})
    client.create_tab = AsyncMock()

    with patch("herdr_master.pipeline.ai_head._ensure_agy_trusted"):
        result = await ensure_herdr_ai_head(
            client, workspace_id="ws-1", head_config=cfg, origin_cwd=origin
        )

    assert result["reconciled"] is True
    assert result["reused"] is True
    started = client.start_agent.await_args.kwargs["args"]
    assert started == ["--model", "gemini-2.5-pro", "--effort", "high"]
    assert "--dangerously-skip-permissions" not in started
    assert started.count("--effort") == 1

