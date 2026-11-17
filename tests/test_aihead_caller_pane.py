"""Unit tests verifying caller pane adoption and delegation for aihead skills.

When `aihead` is invoked from inside an active harness/Herdr pane (e.g., `HERDR_PANE_ID`
is set or running inside a Herdr-managed agent pane), skill invocations (ergo, council,
orchestration, herdr-master) must NOT spawn a new AiHead pane in the job workspace;
instead they must use the existing caller pane (`HERDR_PANE_ID`) as the active
AiHead / parent pane.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from herdr_master.core.config import (
    AiHeadConfig,
    CouncilConfig,
    HerdrMasterConfig,
)
from herdr_master.core.context import HerdrContext
from herdr_master.core.models import HarnessKind, Squad
from herdr_master.pipeline.ai_head import AiHeadDiscovery
from herdr_master.pipeline.launcher import (
    _allocate_launch_target,
    _Allocated,
    _resolve_and_wire_ai_head,
    launch_council,
    launch_ergo,
    launch_master,
    launch_orchestration,
)


def _mock_client(workspace_id: str = "ws-owned-1") -> AsyncMock:
    client = AsyncMock()
    client.socket_path = Path("/tmp/herdr.sock")
    client.session = "default"
    client.create_workspace.return_value = {
        "workspace": {"workspace_id": workspace_id},
        "root_pane": {"pane_id": f"{workspace_id}:p1"},
        "tab": {"tab_id": f"{workspace_id}:t1"},
    }
    client.create_tab.return_value = {
        "root_pane": {"pane_id": f"{workspace_id}:p2"},
        "tab": {"tab_id": f"{workspace_id}:t2"},
    }
    client.start_agent.return_value = {"type": "agent_started"}
    client.prompt_agent.return_value = {"type": "ok"}
    client.get_agent.return_value = {"agent": {"agent_status": "idle"}}
    client.focus_workspace.return_value = {}
    client.focus_tab.return_value = {}
    client.get_snapshot.return_value = SimpleNamespace(
        workspaces=[SimpleNamespace(workspace_id=workspace_id, label="desk")],
        tabs=[],
        panes=[],
    )
    return client


def _ctx(
    inside: bool = True,
    session: str = "default",
    pane_id: str | None = "caller-ctx-p1",
    workspace_id: str | None = "ws-ctx-1",
) -> HerdrContext:
    return HerdrContext(
        is_inside_herdr=inside,
        session_name=session,
        socket_path=Path("/tmp/herdr.sock"),
        workspace_id=workspace_id,
        pane_id=pane_id,
        tab_id=f"{workspace_id}:t_caller" if pane_id else None,
    )


# ============================================================================
# 1. Unit tests for _allocate_launch_target
# ============================================================================


@pytest.mark.asyncio
async def test_allocate_launch_target_with_explicit_parent_pane_id():
    """When parent_pane_id is passed, allocates owned workspace with delegated=True,
    sets parent_pane_id, and creates tab with HERDR_PARENT_PANE_ID env var."""
    client = _mock_client("ws-alloc-1")
    ctx = _ctx(inside=True, pane_id=None)

    allocated = await _allocate_launch_target(
        client,
        ctx,
        session="default",
        isolated_name="job-alloc",
        tab_label="job-tab",
        workspace_label="job-ws",
        cwd="/tmp/project",
        fresh_workspace=True,
        focus=True,
        parent_pane_id="caller-pane-100",
    )

    assert allocated.delegated is True
    assert allocated.parent_pane_id == "caller-pane-100"
    assert allocated.owns_workspace is True
    assert allocated.workspace_id == "ws-alloc-1"
    assert allocated.pane_id == "ws-alloc-1:p2"
    assert allocated.tab_id == "ws-alloc-1:t2"

    client.create_workspace.assert_awaited_once_with(
        label="job-ws",
        focus=True,
        cwd="/tmp/project",
    )
    client.create_tab.assert_awaited_once_with(
        workspace_id="ws-alloc-1",
        label="job-tab",
        focus=True,
        cwd="/tmp/project",
        env={"HERDR_PARENT_PANE_ID": "caller-pane-100"},
    )


@pytest.mark.asyncio
async def test_allocate_launch_target_with_herdr_pane_id_env(monkeypatch):
    """When HERDR_PANE_ID is in environment, resolves eff_parent_pane from env,
    sets delegated=True, and creates tab with HERDR_PARENT_PANE_ID env var."""
    monkeypatch.setenv("HERDR_PANE_ID", "caller-from-env-42")
    client = _mock_client("ws-alloc-2")
    ctx = _ctx(inside=True, pane_id=None)

    allocated = await _allocate_launch_target(
        client,
        ctx,
        session="default",
        isolated_name="job-alloc",
        tab_label="job-tab",
        workspace_label="job-ws",
        cwd="/tmp/project",
        fresh_workspace=True,
        focus=True,
        delegated=True,
    )

    assert allocated.delegated is True
    assert allocated.parent_pane_id == "caller-from-env-42"
    assert allocated.owns_workspace is True
    client.create_tab.assert_awaited_once_with(
        workspace_id="ws-alloc-2",
        label="job-tab",
        focus=True,
        cwd="/tmp/project",
        env={"HERDR_PARENT_PANE_ID": "caller-from-env-42"},
    )


@pytest.mark.asyncio
async def test_allocate_launch_target_explicit_parent_takes_precedence_over_env(monkeypatch):
    """Explicit parent_pane_id argument takes precedence over HERDR_PANE_ID env."""
    monkeypatch.setenv("HERDR_PANE_ID", "caller-env-ignore")
    client = _mock_client("ws-alloc-3")
    ctx = _ctx(inside=True, pane_id="caller-ctx-ignore")

    allocated = await _allocate_launch_target(
        client,
        ctx,
        session="default",
        isolated_name="job-alloc",
        tab_label="job-tab",
        workspace_label="job-ws",
        cwd="/tmp/project",
        fresh_workspace=True,
        parent_pane_id="caller-explicit-wins",
    )

    assert allocated.delegated is True
    assert allocated.parent_pane_id == "caller-explicit-wins"
    client.create_tab.assert_awaited_once_with(
        workspace_id="ws-alloc-3",
        label="job-tab",
        focus=True,
        cwd="/tmp/project",
        env={"HERDR_PARENT_PANE_ID": "caller-explicit-wins"},
    )


@pytest.mark.asyncio
async def test_allocate_launch_target_resolves_ctx_pane_id_when_env_unset(monkeypatch):
    """When parent_pane_id is None and HERDR_PANE_ID is unset, resolves from ctx.pane_id."""
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    client = _mock_client("ws-alloc-4")
    ctx = _ctx(inside=True, pane_id="caller-from-ctx-77")

    allocated = await _allocate_launch_target(
        client,
        ctx,
        session="default",
        isolated_name="job-alloc",
        tab_label="job-tab",
        workspace_label="job-ws",
        cwd="/tmp/project",
        fresh_workspace=True,
        delegated=True,
    )

    assert allocated.delegated is True
    assert allocated.parent_pane_id == "caller-from-ctx-77"
    client.create_tab.assert_awaited_once_with(
        workspace_id="ws-alloc-4",
        label="job-tab",
        focus=True,
        cwd="/tmp/project",
        env={"HERDR_PARENT_PANE_ID": "caller-from-ctx-77"},
    )


@pytest.mark.asyncio
async def test_allocate_launch_target_delegated_without_identifiable_parent_raises(monkeypatch):
    """When delegated=True but no parent pane can be identified, aborts with RuntimeError."""
    monkeypatch.delenv("HERDR_PANE_ID", raising=False)
    monkeypatch.delenv("HERDR_PARENT_PANE_ID", raising=False)
    client = _mock_client("ws-alloc-5")
    ctx = _ctx(inside=True, pane_id=None)

    with pytest.raises(RuntimeError, match="Cannot identify the caller or AiHead parent pane"):
        await _allocate_launch_target(
            client,
            ctx,
            session="default",
            isolated_name="job-alloc",
            tab_label="job-tab",
            workspace_label="job-ws",
            cwd="/tmp/project",
            fresh_workspace=True,
            delegated=True,
            parent_pane_id=None,
        )


@pytest.mark.asyncio
async def test_allocate_launch_target_outside_herdr_delegated_creates_owned_workspace():
    """Outside Herdr, when parent_pane_id is given, delegates to owned workspace."""
    client = _mock_client("ws-alloc-ext")
    ctx = _ctx(inside=False, pane_id=None)

    with (
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_server", new_callable=AsyncMock
        ) as mock_srv,
        patch("herdr_master.pipeline.launcher.HerdrClient", return_value=client),
    ):
        allocated = await _allocate_launch_target(
            client,
            ctx,
            session=None,
            isolated_name="job-ext",
            tab_label="job-tab",
            workspace_label="job-ws",
            cwd="/tmp/project",
            fresh_workspace=True,
            parent_pane_id="caller-ext-p1",
        )

    mock_srv.assert_awaited_once()
    assert allocated.delegated is True
    assert allocated.parent_pane_id == "caller-ext-p1"
    assert allocated.owns_workspace is True
    client.create_tab.assert_awaited_once_with(
        workspace_id="ws-alloc-ext",
        label="job-tab",
        focus=True,
        cwd="/tmp/project",
        env={"HERDR_PARENT_PANE_ID": "caller-ext-p1"},
    )


# ============================================================================
# 2. Unit tests for _resolve_and_wire_ai_head
# ============================================================================


@pytest.mark.asyncio
async def test_resolve_and_wire_ai_head_delegated_skips_ensure_herdr_ai_head(tmp_path: Path):
    """When allocated.delegated is True, ensure_herdr_ai_head is NEVER called,
    and the returned AiHeadDiscovery has pane_id set to the caller pane."""
    client = _mock_client("ws-wire-1")
    allocated = _Allocated(
        client=client,
        session_name="default",
        socket_path=Path("/tmp/herdr.sock"),
        workspace_id="ws-wire-1",
        pane_id="ws-wire-1:p2",
        tab_id="ws-wire-1:t2",
        owns_workspace=True,
        parent_pane_id="caller-authoritative-p1",
        delegated=True,
    )

    cfg = HerdrMasterConfig(ai_head=AiHeadConfig(enabled=False))
    with (
        patch("herdr_master.pipeline.launcher.load_project_config", return_value=cfg),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", new_callable=AsyncMock
        ) as mock_ensure,
    ):
        discovery, config_path = await _resolve_and_wire_ai_head(
            allocated=allocated,
            target_cwd=str(tmp_path),
            ai_head_override=None,
        )

    mock_ensure.assert_not_called()
    assert discovery is not None
    assert isinstance(discovery, AiHeadDiscovery)
    assert discovery.pane_id == "caller-authoritative-p1"
    assert discovery.enabled is True
    assert discovery.mode == "herdr"


@pytest.mark.asyncio
async def test_resolve_and_wire_ai_head_parent_pane_and_owns_workspace_skips_ensure(tmp_path: Path):
    """An owned workspace with a parent never calls ensure_herdr_ai_head."""
    client = _mock_client("ws-wire-2")
    allocated = _Allocated(
        client=client,
        session_name="default",
        socket_path=Path("/tmp/herdr.sock"),
        workspace_id="ws-wire-2",
        pane_id="ws-wire-2:p2",
        tab_id="ws-wire-2:t2",
        owns_workspace=True,
        parent_pane_id="caller-owned-parent-p2",
        # parent_pane_id + owns_workspace triggers it even when delegated is false
        delegated=False,
    )

    cfg = HerdrMasterConfig(ai_head=AiHeadConfig(enabled=True))
    with (
        patch("herdr_master.pipeline.launcher.load_project_config", return_value=cfg),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", new_callable=AsyncMock
        ) as mock_ensure,
    ):
        discovery, config_path = await _resolve_and_wire_ai_head(
            allocated=allocated,
            target_cwd=str(tmp_path),
            ai_head_override=None,
        )

    mock_ensure.assert_not_called()
    assert discovery is not None
    assert discovery.pane_id == "caller-owned-parent-p2"
    assert discovery.enabled is True


@pytest.mark.asyncio
async def test_resolve_and_wire_ai_head_non_delegated_without_parent_calls_ensure(tmp_path: Path):
    """Contrast verification: when delegated=False and parent_pane_id=None,
    ensure_herdr_ai_head IS called (standard child AiHead provisioning)."""
    client = _mock_client("ws-wire-3")
    allocated = _Allocated(
        client=client,
        session_name="default",
        socket_path=Path("/tmp/herdr.sock"),
        workspace_id="ws-wire-3",
        pane_id="ws-wire-3:p2",
        tab_id="ws-wire-3:t2",
        owns_workspace=False,
        parent_pane_id=None,
        delegated=False,
    )

    cfg = HerdrMasterConfig(ai_head=AiHeadConfig(enabled=True))
    head_info = {
        "pane_id": "ws-wire-3:p_head",
        "tab_id": "ws-wire-3:t_head",
        "busy": False,
        "reconciled": True,
        "model": "head-model",
        "enabled": True,
    }
    with (
        patch("herdr_master.pipeline.launcher.load_project_config", return_value=cfg),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", AsyncMock(return_value=head_info)
        ) as mock_ensure,
    ):
        discovery, config_path = await _resolve_and_wire_ai_head(
            allocated=allocated,
            target_cwd=str(tmp_path),
            ai_head_override=None,
        )

    mock_ensure.assert_awaited_once()
    assert discovery is not None
    assert discovery.pane_id == "ws-wire-3:p_head"


@pytest.mark.asyncio
async def test_resolve_and_wire_ai_head_returns_none_when_no_ai_head_config(tmp_path: Path):
    """When project config has no ai_head section, returns None without calling ensure."""
    client = _mock_client("ws-wire-4")
    allocated = _Allocated(
        client=client,
        session_name="default",
        socket_path=Path("/tmp/herdr.sock"),
        workspace_id="ws-wire-4",
        pane_id="ws-wire-4:p2",
        tab_id="ws-wire-4:t2",
        owns_workspace=True,
        parent_pane_id="caller-p4",
        delegated=True,
    )

    with (
        patch(
            "herdr_master.pipeline.launcher.load_project_config", side_effect=Exception("No config")
        ),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", new_callable=AsyncMock
        ) as mock_ensure,
    ):
        discovery, config_path = await _resolve_and_wire_ai_head(
            allocated=allocated,
            target_cwd=str(tmp_path),
            ai_head_override=None,
        )

    mock_ensure.assert_not_called()
    assert discovery is None


# ============================================================================
# 3. Unit tests for launch_ergo, launch_council, launch_orchestration, launch_master
# ============================================================================


@pytest.mark.asyncio
async def test_launch_ergo_with_caller_pane(tmp_path: Path, monkeypatch):
    """launch_ergo with HERDR_PANE_ID set:
    - ensure_herdr_ai_head is never called
    - AiHead discovery is wired to caller pane
    - prompt contains the AiHead Parent Directive referencing the caller pane
    - create_tab passes HERDR_PARENT_PANE_ID in env
    """
    caller_pane = "caller-pane-ergo-1"
    monkeypatch.setenv("HERDR_PANE_ID", caller_pane)
    client = _mock_client("ws-ergo-job")
    ctx = _ctx(inside=True, pane_id=caller_pane)

    squad = Squad()
    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", new_callable=AsyncMock
        ) as mock_ensure,
    ):
        res = await launch_ergo(
            client=client,
            intent="implement feature X",
            workspace_label="ergo-job-1",
            squad=squad,
            cwd=str(tmp_path),
            fresh_workspace=True,
            delegated=True,
            parent_pane_id=caller_pane,
        )

    # 1. ensure_herdr_ai_head is NOT called
    mock_ensure.assert_not_called()

    # 2. AiHead discovery is wired to caller pane
    assert res["ai_head"] is not None
    assert res["ai_head"].pane_id == caller_pane
    assert res["ai_head"].enabled is True

    # 3. Prompt contains AiHead Parent Directive
    prompt = client.prompt_agent.await_args.args[1]
    assert "AiHead Parent Directive:" in prompt
    assert (
        f"AiHead is active at pane {caller_pane} as the authoritative technical decision-maker."
        in prompt
    )
    assert f'herdr agent prompt {caller_pane} "<question / plan>"' in prompt
    assert f"herdr agent prompt {caller_pane}:" in prompt

    # 4. create_tab passed HERDR_PARENT_PANE_ID in env
    assert client.create_tab.await_args.kwargs["env"] == {"HERDR_PARENT_PANE_ID": caller_pane}


@pytest.mark.asyncio
async def test_launch_council_with_caller_pane(tmp_path: Path, monkeypatch):
    """launch_council with HERDR_PANE_ID set:
    - ensure_herdr_ai_head is never called
    - AiHead discovery is wired to caller pane
    - prompt contains the AiHead Parent Directive referencing the caller pane
    - create_tab passes HERDR_PARENT_PANE_ID in env
    """
    caller_pane = "caller-pane-council-1"
    monkeypatch.setenv("HERDR_PANE_ID", caller_pane)
    client = _mock_client("ws-council-job")
    ctx = _ctx(inside=True, pane_id=caller_pane)

    council_cfg = CouncilConfig()
    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", new_callable=AsyncMock
        ) as mock_ensure,
    ):
        res = await launch_council(
            client=client,
            intent="deliberate architecture",
            workspace_label="council-job-1",
            council=council_cfg,
            cwd=str(tmp_path),
            fresh_workspace=True,
            delegated=True,
            parent_pane_id=caller_pane,
        )

    # 1. ensure_herdr_ai_head is NOT called
    mock_ensure.assert_not_called()

    # 2. AiHead discovery is wired to caller pane
    assert res["ai_head"] is not None
    assert res["ai_head"].pane_id == caller_pane

    # 3. Prompt contains AiHead Parent Directive
    prompt = client.prompt_agent.await_args.args[1]
    assert "AiHead Parent Directive:" in prompt
    assert (
        f"AiHead is active at pane {caller_pane} as the authoritative technical decision-maker."
        in prompt
    )
    assert f'herdr agent prompt {caller_pane} "<question / plan>"' in prompt

    # 4. create_tab passed HERDR_PARENT_PANE_ID in env
    assert client.create_tab.await_args.kwargs["env"] == {"HERDR_PARENT_PANE_ID": caller_pane}


@pytest.mark.asyncio
async def test_launch_orchestration_with_caller_pane(tmp_path: Path, monkeypatch):
    """launch_orchestration with HERDR_PANE_ID set:
    - ensure_herdr_ai_head is never called
    - AiHead discovery is wired to caller pane
    - prompt contains the AiHead Parent Directive referencing the caller pane
    - create_tab passes HERDR_PARENT_PANE_ID in env
    """
    caller_pane = "caller-pane-orch-1"
    monkeypatch.setenv("HERDR_PANE_ID", caller_pane)
    client = _mock_client("ws-orch-job")
    ctx = _ctx(inside=True, pane_id=caller_pane)

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", new_callable=AsyncMock
        ) as mock_ensure,
    ):
        res = await launch_orchestration(
            client=client,
            intent="native orchestration pipeline",
            workspace_label="orch-job-1",
            harness=HarnessKind.AGY,
            scout_model="gemini-3.8-flash-medium",
            architect_model="gemini-3.8-flash-medium",
            coder_model="gemini-3.8-flash-medium",
            reviewer_model="gemini-3.8-flash-medium",
            cwd=str(tmp_path),
            fresh_workspace=True,
            delegated=True,
            parent_pane_id=caller_pane,
        )

    # 1. ensure_herdr_ai_head is NOT called
    mock_ensure.assert_not_called()

    # 2. AiHead discovery is wired to caller pane
    assert res["ai_head"] is not None
    assert res["ai_head"].pane_id == caller_pane

    # 3. Prompt contains AiHead Parent Directive
    prompt = client.prompt_agent.await_args.args[1]
    assert "AiHead Parent Directive:" in prompt
    assert (
        f"AiHead is active at pane {caller_pane} as the authoritative technical decision-maker."
        in prompt
    )
    assert f'herdr agent prompt {caller_pane} "<question / plan>"' in prompt

    # 4. create_tab passed HERDR_PARENT_PANE_ID in env
    assert client.create_tab.await_args.kwargs["env"] == {"HERDR_PARENT_PANE_ID": caller_pane}


@pytest.mark.asyncio
async def test_launch_master_with_caller_pane(tmp_path: Path, monkeypatch):
    """launch_master with HERDR_PANE_ID set:
    - ensure_herdr_ai_head is never called
    - AiHead discovery is wired to caller pane
    - prompt contains the AiHead Parent Directive referencing the caller pane
    - create_tab passes HERDR_PARENT_PANE_ID in env
    """
    caller_pane = "caller-pane-master-1"
    monkeypatch.setenv("HERDR_PANE_ID", caller_pane)
    client = _mock_client("ws-master-job")
    ctx = _ctx(inside=True, pane_id=caller_pane)

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher.ensure_herdr_ai_head", new_callable=AsyncMock
        ) as mock_ensure,
    ):
        res = await launch_master(
            client=client,
            name="master-coord",
            harness=HarnessKind.AGY,
            model="gemini-3.8-flash-medium",
            initial_task="coordinate subagents",
            cwd=str(tmp_path),
            fresh_workspace=True,
            delegated=True,
            parent_pane_id=caller_pane,
        )

    # 1. ensure_herdr_ai_head is NOT called
    mock_ensure.assert_not_called()

    # 2. AiHead discovery is wired to caller pane
    assert res["ai_head"] is not None
    assert res["ai_head"].pane_id == caller_pane

    # 3. Prompt contains AiHead Parent Directive
    prompt = client.prompt_agent.await_args.args[1]
    assert "AiHead Parent Directive:" in prompt
    assert (
        f"AiHead is active at pane {caller_pane} as the authoritative technical decision-maker."
        in prompt
    )
    assert f'herdr agent prompt {caller_pane} "<question / plan>"' in prompt

    # 4. create_tab passed HERDR_PARENT_PANE_ID in env
    assert client.create_tab.await_args.kwargs["env"] == {"HERDR_PARENT_PANE_ID": caller_pane}
