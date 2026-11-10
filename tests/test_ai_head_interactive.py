"""Comprehensive unit and integration tests for AiHead interactive terminal sessions & pane swapping.

Covers:
1. HerdrClient pane methods (split_pane, swap_panes, current_pane, get_pane_layout):
   - Protocol 22 RPC request payload framing.
   - Result unwrapping and error handling on Protocol 22 error envelopes.
   - Real Unix domain socket framing and in-memory RPC roundtrips.
2. AiHeadConfig split property:
   - Default split == True.
   - TOML parsing with split = false and split = true.
   - String and numeric coercion ("yes", "no", "0", "1", 0, 1, "false", "true", "on", "off").
   - Invalid value rejection.
3. build_interactive_ai_head_prompt:
   - Default soul (DEFAULT_AI_HEAD_SOUL) and custom soul support.
   - Working directory injection.
   - Core workflow knowledge: /orchestration, /ergo, /council, /herdr-master.
   - Decision heuristics and safety guardrails.
   - Initial task section vs default greeting/readiness section.
4. launch_ai_head:
   - Inside Herdr split layout: current_pane -> split_pane -> swap_panes -> focus_pane -> prompt_agent.
   - Inside Herdr split=False: standalone allocation, no split/swap.
   - Standalone workspace allocation.
   - Binding overrides (harness, model, effort).
5. CLI aihead and head commands:
   - Help text and command aliases.
   - Flag forwarding: defaults, --no-split, --secondary, --task / -t, overrides.
   - Outside-Herdr attachment with os.execvp.
"""

import asyncio
import inspect
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.core.config import (
    DEFAULT_AI_HEAD_SOUL,
    AiHeadConfig,
    HerdrMasterConfig,
    load_project_config,
    save_project_config,
)
from herdr_master.core.context import HerdrContext
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind
from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.socket import HerdrConnectionError, HerdrRPCError
from herdr_master.pipeline.ai_head import build_interactive_ai_head_prompt
from herdr_master.pipeline.launcher import (
    _Allocated,
    launch_ai_head,
)

runner = CliRunner()


# ===========================================================================
# 1. HerdrClient Pane Methods (Protocol 22 RPC Framing & Error Handling)
# ===========================================================================


@pytest.mark.asyncio
async def test_client_split_pane_framing_defaults():
    """split_pane should frame RPC method 'pane.split' with default direction and focus."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(return_value={"id": "test-1", "result": {"pane_id": "p-new-1"}})

    res = await client.split_pane()

    assert res == {"pane_id": "p-new-1"}
    client._rpc.assert_awaited_once_with(
        "pane.split",
        {"direction": "right", "focus": False},
    )


@pytest.mark.asyncio
async def test_client_split_pane_framing_all_params():
    """split_pane should frame all optional parameters according to Protocol 22."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(return_value={"id": "test-2", "result": {"pane_id": "p-new-2"}})

    res = await client.split_pane(
        direction="down",
        target_pane_id="pane-orig",
        workspace_id="ws-main",
        ratio=0.35,
        cwd="/workspace/repo",
        env={"HERDR_AGENT": "ai-head"},
        focus=True,
    )

    assert res == {"pane_id": "p-new-2"}
    client._rpc.assert_awaited_once_with(
        "pane.split",
        {
            "direction": "down",
            "focus": True,
            "target_pane_id": "pane-orig",
            "workspace_id": "ws-main",
            "ratio": 0.35,
            "cwd": "/workspace/repo",
            "env": {"HERDR_AGENT": "ai-head"},
        },
    )


@pytest.mark.asyncio
async def test_client_swap_panes_framing():
    """swap_panes should frame RPC method 'pane.swap' with source, target, and direction."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(return_value={"id": "test-3", "result": {"status": "swapped"}})

    # With source and target
    res1 = await client.swap_panes(source_pane_id="p-1", target_pane_id="p-2")
    assert res1 == {"status": "swapped"}
    client._rpc.assert_awaited_with(
        "pane.swap",
        {"source_pane_id": "p-1", "target_pane_id": "p-2"},
    )

    # With direction only
    client._rpc.reset_mock()
    res2 = await client.swap_panes(direction="left")
    assert res2 == {"status": "swapped"}
    client._rpc.assert_awaited_with(
        "pane.swap",
        {"direction": "left"},
    )

    # Empty params
    client._rpc.reset_mock()
    res3 = await client.swap_panes()
    assert res3 == {"status": "swapped"}
    client._rpc.assert_awaited_with("pane.swap", {})


@pytest.mark.asyncio
async def test_client_current_pane_framing():
    """current_pane should frame RPC method 'pane.current'."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(
        return_value={"id": "test-4", "result": {"pane_id": "p-active", "tab_id": "t-1"}}
    )

    # Without caller_pane_id
    res1 = await client.current_pane()
    assert res1 == {"pane_id": "p-active", "tab_id": "t-1"}
    client._rpc.assert_awaited_with("pane.current", {})

    # With caller_pane_id
    client._rpc.reset_mock()
    res2 = await client.current_pane(caller_pane_id="p-caller")
    assert res2 == {"pane_id": "p-active", "tab_id": "t-1"}
    client._rpc.assert_awaited_with("pane.current", {"caller_pane_id": "p-caller"})


@pytest.mark.asyncio
async def test_client_get_pane_layout_framing():
    """get_pane_layout should frame RPC method 'pane.layout'."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(
        return_value={"id": "test-5", "result": {"type": "split", "orientation": "horizontal"}}
    )

    # Without pane_id
    res1 = await client.get_pane_layout()
    assert res1 == {"type": "split", "orientation": "horizontal"}
    client._rpc.assert_awaited_with("pane.layout", {})

    # With pane_id
    client._rpc.reset_mock()
    res2 = await client.get_pane_layout(pane_id="p-10")
    assert res2 == {"type": "split", "orientation": "horizontal"}
    client._rpc.assert_awaited_with("pane.layout", {"pane_id": "p-10"})


@pytest.mark.asyncio
async def test_client_pane_methods_unix_socket_roundtrip_and_errors():
    """Integration test: real Unix domain socket server verifying Protocol 22 framing & error envelopes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "test_herdr.sock"

        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            line = await reader.readline()
            if not line:
                return
            req = json.loads(line.decode("utf-8"))
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "pane.split":
                if params.get("direction") == "invalid":
                    resp = {
                        "id": req_id,
                        "error": {
                            "code": "INVALID_ARGUMENT",
                            "message": "Unsupported split direction: invalid",
                        },
                    }
                else:
                    resp = {
                        "id": req_id,
                        "result": {
                            "pane_id": "pane-split-created",
                            "direction": params.get("direction"),
                            "focus": params.get("focus"),
                        },
                    }
            elif method == "pane.swap":
                if params.get("source_pane_id") == "missing-pane":
                    resp = {
                        "id": req_id,
                        "error": {
                            "code": "PANE_NOT_FOUND",
                            "message": "Pane missing-pane does not exist",
                        },
                    }
                else:
                    resp = {
                        "id": req_id,
                        "result": {"status": "ok", "swapped": True},
                    }
            elif method == "pane.current":
                resp = {
                    "id": req_id,
                    "result": {"pane_id": "pane-active-123", "tab_id": "tab-abc"},
                }
            elif method == "pane.layout":
                resp = {
                    "id": req_id,
                    "result": {"tree": {"root": "pane-active-123"}},
                }
            else:
                resp = {
                    "id": req_id,
                    "error": {"code": "METHOD_NOT_FOUND", "message": f"Unknown: {method}"},
                }

            writer.write(json.dumps(resp).encode("utf-8") + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(sock_path))

        try:
            client = HerdrClient(socket_path=sock_path)

            # 1. split_pane success
            split_res = await client.split_pane(direction="right", focus=True)
            assert split_res["pane_id"] == "pane-split-created"
            assert split_res["direction"] == "right"
            assert split_res["focus"] is True

            # 2. split_pane Protocol 22 error
            with pytest.raises(HerdrRPCError) as exc_info:
                await client.split_pane(direction="invalid")
            assert exc_info.value.code == "INVALID_ARGUMENT"
            assert "Unsupported split direction" in exc_info.value.message

            # 3. swap_panes success
            swap_res = await client.swap_panes(source_pane_id="p-1", target_pane_id="p-2")
            assert swap_res["swapped"] is True

            # 4. swap_panes Protocol 22 error
            with pytest.raises(HerdrRPCError) as exc_info:
                await client.swap_panes(source_pane_id="missing-pane", target_pane_id="p-2")
            assert exc_info.value.code == "PANE_NOT_FOUND"
            assert "Pane missing-pane does not exist" in exc_info.value.message

            # 5. current_pane success
            curr_res = await client.current_pane()
            assert curr_res["pane_id"] == "pane-active-123"

            # 6. get_pane_layout success
            layout_res = await client.get_pane_layout()
            assert "tree" in layout_res

        finally:
            server.close()
            await server.wait_closed()


# ===========================================================================
# 2. AiHeadConfig Split Property (Defaults, TOML Parsing, Coercion)
# ===========================================================================


def test_ai_head_config_split_default():
    """AiHeadConfig should have split == True by default."""
    cfg = AiHeadConfig()
    assert cfg.split is True


def test_ai_head_config_cwd_and_folder_parsing(tmp_path: Path):
    """AiHeadConfig should parse cwd, folder, and directory aliases and expand user."""
    # Test default
    cfg = AiHeadConfig()
    assert cfg.cwd is None
    assert cfg.folder is None

    # Test folder alias in toml
    toml_folder = tmp_path / ".herdr-master-folder.toml"
    test_dir = str(tmp_path / "workdir")
    toml_folder.write_text(
        f"""
[ai_head]
enabled = true
folder = "{test_dir}"
"""
    )
    project_folder = load_project_config(toml_folder)
    assert project_folder.ai_head.cwd == test_dir
    assert project_folder.ai_head.folder == test_dir

    # Test cwd in toml
    toml_cwd = tmp_path / ".herdr-master-cwd.toml"
    toml_cwd.write_text(
        f"""
[ai_head]
enabled = true
cwd = "{test_dir}"
"""
    )
    project_cwd = load_project_config(toml_cwd)
    assert project_cwd.ai_head.cwd == test_dir
    assert project_cwd.ai_head.folder == test_dir

    # Test setter
    project_cwd.ai_head.folder = "/another/path"
    assert project_cwd.ai_head.cwd == "/another/path"


def test_ai_head_config_split_toml_parsing(tmp_path: Path):
    """AiHeadConfig split property should parse cleanly from TOML file."""
    # Test split = false
    toml_false = tmp_path / ".herdr-master-false.toml"
    toml_false.write_text(
        """
[ai_head]
enabled = true
split = false
"""
    )
    project_false = load_project_config(toml_false)
    assert project_false.ai_head.split is False

    # Test split = true
    toml_true = tmp_path / ".herdr-master-true.toml"
    toml_true.write_text(
        """
[ai_head]
enabled = true
split = true
"""
    )
    project_true = load_project_config(toml_true)
    assert project_true.ai_head.split is True


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("1", True),
        (1, True),
        (1.0, True),
        ("yes", True),
        ("Yes", True),
        ("on", True),
        ("ON", True),
        ("false", False),
        ("False", False),
        ("FALSE", False),
        ("0", False),
        (0, False),
        (0.0, False),
        ("no", False),
        ("No", False),
        ("off", False),
        ("OFF", False),
    ],
)
def test_ai_head_config_split_coercion(raw_value: Any, expected: bool):
    """AiHeadConfig split field should coerce strings and numerics properly."""
    cfg = AiHeadConfig.model_validate({"split": raw_value})
    assert cfg.split is expected


def test_ai_head_config_split_invalid_rejected():
    """Invalid boolean string should raise validation error."""
    with pytest.raises(ValidationError):
        AiHeadConfig.model_validate({"split": "maybe"})


# ===========================================================================
# 3. build_interactive_ai_head_prompt (Prompt Construction & Knowledge)
# ===========================================================================


def test_build_interactive_ai_head_prompt_defaults():
    """Interactive prompt should contain default soul, workflows, heuristics, and safety guardrails."""
    prompt = build_interactive_ai_head_prompt()

    # Persona & Soul
    assert "You are AiHead" in prompt
    assert DEFAULT_AI_HEAD_SOUL in prompt

    # 4 Core Workflows
    assert "CORE WORKFLOWS & CAPABILITIES:" in prompt
    assert "/orchestration" in prompt
    assert "Single-Harness Native Pipeline" in prompt
    assert "100% Herdr-free" in prompt

    assert "/ergo" in prompt
    assert "Multi-Harness Cross-Session Pipeline" in prompt
    assert "best-of-breed" in prompt

    assert "/council" in prompt
    assert "AI Council Consensus Deliberation" in prompt
    assert "produces final_plan.md only" in prompt

    assert "/herdr-master" in prompt
    assert "Master coordinator" in prompt
    assert "managing multiple parallel jobs" in prompt

    # Selection Heuristics
    assert "WORKFLOW SELECTION HEURISTICS:" in prompt
    assert "Ambiguity / Architectural High Stakes" in prompt
    assert "Subtask Topology (Multiple Distinct Jobs)" in prompt
    assert "Full-Stack Feature Rigor" in prompt
    assert "Fast / Cohesive Single-Ecosystem Task" in prompt

    # Plan Approval & Architectural Alignment Gate
    assert "Plan Approval & Architectural Alignment Gate:" in prompt
    assert "Plain-Language Plan Presentation:" in prompt
    assert "Mandatory Architectural Alignment:" in prompt
    assert "User Escalation When No Solution Exists:" in prompt

    # Hierarchical Parent Notification Protocol
    assert "Hierarchical Parent-Child Notification Protocol (One Level Up):" in prompt
    assert "HERDR_PARENT_PANE_ID" in prompt
    assert "herdr agent prompt <PARENT_PANE_ID>" in prompt
    assert "Level 3 (Coders / Reviewer / Council Members) notify Level 2 (Ergo Orchestrator / Council Coordinator)" in prompt
    assert "Level 2 (Ergo Orchestrator / Council Coordinator / Job Worker) notifies Level 1 (AiHead / Master Coordinator)" in prompt

    # Safety Guardrails
    assert "Safety Guardrails:" in prompt
    assert "git reset --hard" in prompt
    assert "deleting branches/worktrees" in prompt

    # Greeting section when no task is provided
    assert "=== READINESS & GREETING ===" in prompt
    assert "Greet the user" in prompt


def test_build_interactive_ai_head_prompt_with_custom_soul_and_cwd():
    """Interactive prompt should respect custom soul and origin_cwd."""
    custom_soul = "I am a high-velocity startup tech lead focusing on MVP delivery."
    cfg = AiHeadConfig(soul=custom_soul)

    prompt = build_interactive_ai_head_prompt(
        head_config=cfg,
        origin_cwd="/home/developer/project-alpha",
    )

    assert custom_soul in prompt
    assert "Working Directory: /home/developer/project-alpha" in prompt


def test_build_interactive_ai_head_prompt_with_initial_task():
    """When initial_task is provided, prompt should contain initial objective instructions."""
    task = "Refactor authentication system"
    prompt = build_interactive_ai_head_prompt(initial_task=task)

    assert "=== INITIAL OBJECTIVE ===" in prompt
    assert f'"{task}"' in prompt
    assert "Analyze this objective immediately" in prompt
    # Greeting section should be omitted when initial objective is given
    assert "=== READINESS & GREETING ===" not in prompt


@pytest.mark.asyncio
async def test_launch_ai_head_inside_herdr_split_snapshot_fallback():
    """When split_pane does not return pane_id, it should fall back to snapshot pane inspection."""
    client = AsyncMock(spec=HerdrClient)
    # split_pane returns generic result without pane_id
    client.split_pane = AsyncMock(return_value={"status": "ok"})
    client.swap_panes = AsyncMock(return_value={"status": "ok"})
    client.focus_pane = AsyncMock(return_value={"status": "ok"})
    client.start_agent = AsyncMock(return_value={"status": "ok"})
    client.prompt_agent = AsyncMock(return_value={"status": "ok"})

    snap = SimpleNamespace(
        panes=[
            SimpleNamespace(pane_id="orig-pane-1", tab_id="tab-20"),
            SimpleNamespace(pane_id="discovered-split-pane", tab_id="tab-20"),
        ]
    )
    client.get_snapshot = AsyncMock(return_value=snap)

    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-10",
        tab_id="tab-20",
        pane_id="orig-pane-1",
    )

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.load_project_config") as mock_load_cfg,
    ):
        mock_cfg = MagicMock()
        mock_cfg.ai_head = AiHeadConfig(split=True)
        mock_load_cfg.return_value = mock_cfg

        res = await launch_ai_head(
            client=client,
            split=True,
            cwd="/tmp/my-project",
        )

    assert res["pane_id"] == "discovered-split-pane"
    assert res["secondary_pane_id"] == "orig-pane-1"
    client.swap_panes.assert_awaited_once_with(
        source_pane_id="orig-pane-1",
        target_pane_id="discovered-split-pane",
    )


@pytest.mark.asyncio
async def test_launch_ai_head_inside_herdr_split_failure_raises_runtime_error():
    """When split_pane returns no new pane and snapshot cannot find one, RuntimeError is raised."""
    client = AsyncMock(spec=HerdrClient)
    client.split_pane = AsyncMock(return_value={"status": "empty"})
    client.get_snapshot = AsyncMock(return_value=SimpleNamespace(panes=[]))

    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-10",
        tab_id="tab-20",
        pane_id="orig-pane-1",
    )

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.load_project_config") as mock_load_cfg,
    ):
        mock_cfg = MagicMock()
        mock_cfg.ai_head = AiHeadConfig(split=True)
        mock_load_cfg.return_value = mock_cfg

        with pytest.raises(RuntimeError, match="Could not determine new pane ID"):
            await launch_ai_head(
                client=client,
                split=True,
                cwd="/tmp/my-project",
            )


# ===========================================================================
# 4. launch_ai_head (Screen Splitting, Allocation & Invocations)
# ===========================================================================


@pytest.mark.asyncio
async def test_launch_ai_head_inside_herdr_split_applied():
    """Inside Herdr with split=True: calls split_pane -> swap_panes -> focus_pane -> prompt_agent."""
    client = AsyncMock(spec=HerdrClient)
    client.current_pane = AsyncMock(return_value={"pane_id": "orig-pane-1"})
    client.split_pane = AsyncMock(return_value={"pane_id": "new-pane-2"})
    client.swap_panes = AsyncMock(return_value={"status": "ok"})
    client.focus_pane = AsyncMock(return_value={"status": "ok"})
    client.start_agent = AsyncMock(return_value={"status": "ok"})
    client.prompt_agent = AsyncMock(return_value={"status": "ok"})
    client.focus_workspace = AsyncMock(return_value={"status": "ok"})
    client.focus_tab = AsyncMock(return_value={"status": "ok"})

    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-10",
        tab_id="tab-20",
        pane_id="orig-pane-1",
    )

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.load_project_config") as mock_load_cfg,
    ):
        mock_cfg = MagicMock()
        mock_cfg.ai_head = AiHeadConfig(split=True)
        mock_load_cfg.return_value = mock_cfg

        res = await launch_ai_head(
            client=client,
            harness=HarnessKind.PI,
            model="openai-codex/gpt-6.1-sol",
            effort=EffortLevel.MAX,
            split=True,
            initial_task="Audit security posture",
            cwd="/tmp/my-project",
        )

    # Verify return dict
    assert res["session"] == "work-session"
    assert res["workspace_id"] == "ws-10"
    assert res["pane_id"] == "new-pane-2"
    assert res["secondary_pane_id"] == "orig-pane-1"
    assert res["split"] is True
    assert res["harness"] == "pi"
    assert res["model"] == "openai-codex/gpt-6.1-sol"
    assert res["effort"] == "max"

    # Verify execution sequence
    client.split_pane.assert_awaited_once_with(
        direction="right",
        target_pane_id="orig-pane-1",
        cwd="/tmp/my-project",
        focus=True,
    )
    client.swap_panes.assert_awaited_once_with(
        source_pane_id="orig-pane-1",
        target_pane_id="new-pane-2",
    )
    client.focus_pane.assert_awaited_once_with("new-pane-2")
    client.start_agent.assert_awaited_once()
    assert client.start_agent.await_args.kwargs["pane_id"] == "new-pane-2"
    assert client.start_agent.await_args.kwargs["kind"] == "pi"

    client.prompt_agent.assert_awaited_once()
    delivered_prompt = client.prompt_agent.await_args.args[1]
    assert "Audit security posture" in delivered_prompt


@pytest.mark.asyncio
async def test_launch_ai_head_inside_herdr_split_discovers_active_pane_via_current_pane():
    """When ctx.pane_id is None inside Herdr, active pane should be discovered via current_pane()."""
    client = AsyncMock(spec=HerdrClient)
    client.current_pane = AsyncMock(return_value={"pane_id": "discovered-pane-5"})
    client.split_pane = AsyncMock(return_value={"pane_id": "new-pane-9"})
    client.swap_panes = AsyncMock(return_value={"status": "ok"})
    client.focus_pane = AsyncMock(return_value={"status": "ok"})
    client.start_agent = AsyncMock(return_value={"status": "ok"})
    client.prompt_agent = AsyncMock(return_value={"status": "ok"})

    ctx = HerdrContext(
        session_name="session-x",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-x",
        tab_id="tab-x",
        pane_id=None,  # Not in env
    )

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.load_project_config") as mock_load_cfg,
    ):
        mock_cfg = MagicMock()
        mock_cfg.ai_head = AiHeadConfig(split=True)
        mock_load_cfg.return_value = mock_cfg

        res = await launch_ai_head(
            client=client,
            split=True,
            cwd="/tmp/my-project",
        )

    client.current_pane.assert_awaited_once()
    client.split_pane.assert_awaited_once_with(
        direction="right",
        target_pane_id="discovered-pane-5",
        cwd="/tmp/my-project",
        focus=True,
    )
    assert res["pane_id"] == "new-pane-9"
    assert res["secondary_pane_id"] == "discovered-pane-5"
    assert res["split"] is True


@pytest.mark.asyncio
async def test_launch_ai_head_inside_herdr_no_split():
    """When split=False inside Herdr, it should use _allocate_launch_target without splitting."""
    client = AsyncMock(spec=HerdrClient)
    allocated_mock = _Allocated(
        client=client,
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        workspace_id="ws-nosplit",
        pane_id="pane-nosplit",
        tab_id="tab-nosplit",
        owns_workspace=False,
    )

    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-10",
        tab_id="tab-20",
        pane_id="orig-pane-1",
    )

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher._allocate_launch_target",
            new_callable=AsyncMock,
            return_value=allocated_mock,
        ) as mock_alloc,
        patch("herdr_master.pipeline.launcher._start_and_prompt", new_callable=AsyncMock) as mock_start,
    ):
        res = await launch_ai_head(
            client=client,
            split=False,
            cwd="/tmp/my-project",
        )

    assert res["split"] is False
    assert "secondary_pane_id" not in res
    assert res["pane_id"] == "pane-nosplit"
    mock_alloc.assert_awaited_once()
    mock_start.assert_awaited_once()
    client.split_pane.assert_not_called()
    client.swap_panes.assert_not_called()


@pytest.mark.asyncio
async def test_launch_ai_head_with_explicit_workspace():
    """When an explicit workspace is passed, it should allocate standalone workspace without splitting."""
    client = AsyncMock(spec=HerdrClient)
    snap = SimpleNamespace(
        workspaces=[
            SimpleNamespace(workspace_id="ws-existing", label="my-target-ws"),
        ]
    )
    client.get_snapshot = AsyncMock(return_value=snap)
    client.create_tab = AsyncMock(
        return_value={"root_pane": {"pane_id": "pane-ws-tab"}, "tab": {"tab_id": "tab-ws-tab"}}
    )

    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-current",
        tab_id="tab-current",
        pane_id="pane-current",
    )

    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher._start_and_prompt", new_callable=AsyncMock) as mock_start,
    ):
        res = await launch_ai_head(
            client=client,
            workspace="my-target-ws",
            split=True,  # Should be bypassed because workspace is explicit
            cwd="/tmp/my-project",
        )

    assert res["workspace_id"] == "ws-existing"
    assert res["pane_id"] == "pane-ws-tab"
    assert res["split"] is False
    assert "secondary_pane_id" not in res
    client.split_pane.assert_not_called()
    client.swap_panes.assert_not_called()
    client.create_tab.assert_awaited_once_with(
        workspace_id="ws-existing",
        label="ai-head",
        focus=True,
        cwd="/tmp/my-project",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "harness,model",
    [
        (HarnessKind.AGY, "gemini-3.8-flash-high"),
        (HarnessKind.HERMES, "openai-codex/gpt-6.1-terra"),
    ],
)
async def test_launch_ai_head_binding_overrides(harness, model):
    """Overrides for harness, model, and effort should propagate into launch metadata."""
    client = AsyncMock(spec=HerdrClient)
    client.get_agent = AsyncMock(return_value={"agent": {"agent_status": "idle"}})
    client.split_pane = AsyncMock(return_value={"pane_id": "pane-agy-1"})
    client.swap_panes = AsyncMock(return_value={"status": "ok"})
    client.focus_pane = AsyncMock(return_value={"status": "ok"})
    client.start_agent = AsyncMock(return_value={"status": "ok"})
    client.prompt_agent = AsyncMock(return_value={"status": "ok"})

    ctx = HerdrContext(
        session_name="session-binding",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-b",
        tab_id="tab-b",
        pane_id="pane-orig-b",
    )

    with patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx):
        res = await launch_ai_head(
            client=client,
            harness=harness,
            model=model,
            effort=EffortLevel.HIGH,
            split=True,
            cwd="/tmp/my-project",
        )

    assert res["harness"] == harness.value
    assert res["model"] == model
    assert res["effort"] == "high"
    client.start_agent.assert_awaited_once()
    assert client.start_agent.await_args.kwargs["kind"] == harness.value
    if harness is HarnessKind.HERMES:
        assert client.start_agent.await_args.kwargs["args"] == [
            "--provider", "openai-codex", "--model", "gpt-6.1-terra",
            "--reasoning", "high", "--yolo",
        ]
        assert "You are AiHead" in client.prompt_agent.await_args.args[1]


# ===========================================================================
# 5. CLI aihead and head Commands (Typer CLI Integration)
# ===========================================================================


def test_cli_aihead_and_head_help():
    """Both 'aihead' and 'head' commands should produce valid help output."""
    res_aihead = runner.invoke(app, ["aihead", "--help"], env={"COLUMNS": "200"})
    assert res_aihead.exit_code == 0
    assert "Launch an interactive AiHead terminal session" in res_aihead.output
    assert "--split" in res_aihead.output
    assert "--no-split" in res_aihead.output
    assert "--skip-permissions" in res_aihead.output
    assert "--no-skip-permissions" in res_aihead.output
    assert "--secondary" in res_aihead.output
    assert "--task" in res_aihead.output

    res_head = runner.invoke(app, ["head", "--help"], env={"COLUMNS": "200"})
    assert res_head.exit_code == 0
    assert "Launch an interactive AiHead terminal session" in res_head.output
    assert "--skip-permissions" in res_head.output
    assert "--no-skip-permissions" in res_head.output


def test_cli_aihead_default_invokes_split():
    """Default invocation of aihead should enable split=True and call launch_ai_head."""
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-1",
            "pane_id": "pane-primary",
            "secondary_pane_id": "pane-caller",
            "split": True,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-1",
        tab_id="tab-1",
        pane_id="pane-caller",
    )

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
        patch("herdr_master.cli.main.os.execvp") as mock_exec,
    ):
        result = runner.invoke(app, ["aihead"])

    assert result.exit_code == 0, result.output
    assert launch_mock.await_count == 1
    kwargs = launch_mock.await_args.kwargs
    assert kwargs["split"] is True
    assert kwargs["initial_task"] is None
    # Inside Herdr: execvp should not be called
    mock_exec.assert_not_called()
    assert "split layout" in result.output


def test_cli_aihead_no_split_flag():
    """--no-split should pass split=False to launch_ai_head."""
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-1",
            "pane_id": "pane-standalone",
            "split": False,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-1",
        tab_id="tab-1",
        pane_id="pane-caller",
    )

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
    ):
        result = runner.invoke(app, ["aihead", "--no-split"])

    assert result.exit_code == 0, result.output
    assert launch_mock.await_count == 1
    kwargs = launch_mock.await_args.kwargs
    assert kwargs["split"] is False


def test_cli_aihead_secondary_flag_enforces_split():
    """--secondary should enforce split=True even if --no-split or config specifies false."""
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-1",
            "pane_id": "pane-head",
            "secondary_pane_id": "pane-sec",
            "split": True,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-1",
        tab_id="tab-1",
        pane_id="pane-caller",
    )

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
    ):
        result = runner.invoke(app, ["aihead", "--secondary"])

    assert result.exit_code == 0, result.output
    assert launch_mock.await_count == 1
    kwargs = launch_mock.await_args.kwargs
    assert kwargs["split"] is True


def test_cli_aihead_initial_task_flags():
    """--task and -t should forward initial_task to launch_ai_head."""
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-1",
            "pane_id": "p-1",
            "split": True,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
    )

    # Test long flag --task with exact prompt string "build feature"
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
    ):
        result = runner.invoke(app, ["aihead", "--task", "build feature"])

    assert result.exit_code == 0
    assert launch_mock.await_args.kwargs["initial_task"] == "build feature"

    # Test short flag -t with head alias
    launch_mock.reset_mock()
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
    ):
        result_short = runner.invoke(app, ["head", "-t", "build feature"])

    assert result_short.exit_code == 0
    assert launch_mock.await_args.kwargs["initial_task"] == "build feature"


def test_cli_aihead_binding_and_workspace_overrides():
    """CLI flags for harness, model, effort, and workspace should forward properly."""
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-explicit",
            "pane_id": "pane-custom",
            "split": False,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
    )

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
    ):
        result = runner.invoke(
            app,
            [
                "aihead",
                "--harness",
                "pi",
                "--model",
                "openai-codex/gpt-6.1-sol",
                "--effort",
                "high",
                "--workspace",
                "ws-target",
            ],
        )

    assert result.exit_code == 0, result.output
    kwargs = launch_mock.await_args.kwargs
    assert kwargs["harness"] == HarnessKind.PI
    assert kwargs["model"] == "openai-codex/gpt-6.1-sol"
    assert kwargs["effort"] == EffortLevel.HIGH
    assert kwargs["workspace"] == "ws-target"


def test_cli_aihead_cwd_options(tmp_path: Path):
    """CLI options --cwd, --folder, and -C should forward the resolved directory."""
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-1",
            "pane_id": "pane-1",
            "split": False,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
    )
    target_folder = str(tmp_path.resolve())

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
    ):
        # 1. Test --cwd
        res1 = runner.invoke(app, ["aihead", "--cwd", target_folder])
        assert res1.exit_code == 0
        assert launch_mock.await_args.kwargs["cwd"] == target_folder

        # 2. Test --folder
        res2 = runner.invoke(app, ["head", "--folder", target_folder])
        assert res2.exit_code == 0
        assert launch_mock.await_args.kwargs["cwd"] == target_folder

        # 3. Test -C
        res3 = runner.invoke(app, ["aihead", "-C", target_folder])
        assert res3.exit_code == 0
        assert launch_mock.await_args.kwargs["cwd"] == target_folder


def test_cli_aihead_outside_herdr_attach_invokes_execvp():
    """Outside Herdr with default --attach, CLI should call os.execvp to attach."""
    launch_mock = AsyncMock(
        return_value={
            "session": "isolated-head-session",
            "workspace_id": "ws-1",
            "pane_id": "pane-1",
            "split": False,
        }
    )
    ctx = HerdrContext(
        session_name="isolated-head-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=False,
    )

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
        patch("herdr_master.cli.main.os.execvp") as mock_exec,
    ):
        result = runner.invoke(app, ["aihead"])

    assert result.exit_code == 0, result.output
    mock_exec.assert_called_once_with(
        "herdr",
        ["herdr", "--session", "isolated-head-session"],
    )


def test_cli_aihead_outside_herdr_no_attach_skips_execvp():
    """Outside Herdr with --no-attach, CLI should NOT call os.execvp."""
    launch_mock = AsyncMock(
        return_value={
            "session": "isolated-head-session",
            "workspace_id": "ws-1",
            "pane_id": "pane-1",
            "split": False,
        }
    )
    ctx = HerdrContext(
        session_name="isolated-head-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=False,
    )

    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
        patch("herdr_master.cli.main.os.execvp") as mock_exec,
    ):
        result = runner.invoke(app, ["aihead", "--no-attach"])

    assert result.exit_code == 0, result.output
    mock_exec.assert_not_called()


def _ai_head_toml(
    *,
    kind: str,
    model: str,
    effort: Optional[str],
    skip: Optional[bool],
) -> str:
    lines = ["[ai_head]", "enabled = true"]
    if skip is not None:
        lines.append(f"dangerously_skip_permissions = {'true' if skip else 'false'}")
    lines.extend(["", "[ai_head.binding]", f'kind = "{kind}"', f'model = "{model}"'])
    if effort is not None:
        lines.append(f'effort = "{effort}"')
    return "\n".join(lines) + "\n"


def _inside_launch_client() -> AsyncMock:
    client = AsyncMock(spec=HerdrClient)
    client.current_pane = AsyncMock(return_value={"pane_id": "orig-pane-1"})
    client.split_pane = AsyncMock(return_value={"pane_id": "new-pane-2"})
    client.swap_panes = AsyncMock(return_value={"status": "ok"})
    client.focus_pane = AsyncMock(return_value={"status": "ok"})
    client.start_agent = AsyncMock(return_value={"status": "ok"})
    client.prompt_agent = AsyncMock(return_value={"status": "ok"})
    return client


async def _launch_args(
    tmp_path: Path,
    config_text: str,
    **kwargs: Any,
) -> List[str]:
    config_path = tmp_path / "ai-head.toml"
    config_path.write_text(config_text, encoding="utf-8")
    client = _inside_launch_client()
    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-10",
        tab_id="tab-20",
        pane_id="orig-pane-1",
    )
    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_agy_trusted"),
    ):
        await launch_ai_head(
            client=client,
            cwd=str(tmp_path),
            config_path=config_path,
            split=True,
            **kwargs,
        )
    args = client.start_agent.await_args.kwargs["args"]
    assert "--kind" not in args
    return args


@pytest.mark.asyncio
async def test_launch_ai_head_permission_and_effort_precedence(tmp_path: Path):
    """Explicit false is an override. Missing config falls back to True."""
    saved_false = await _launch_args(
        tmp_path,
        _ai_head_toml(
            kind="agy", model="gemini-2.5-pro", effort="high", skip=False
        ),
    )
    assert saved_false == ["--model", "gemini-2.5-pro", "--effort", "high"]

    override_true = await _launch_args(
        tmp_path,
        _ai_head_toml(
            kind="agy", model="gemini-2.5-pro", effort="high", skip=False
        ),
        dangerously_skip_permissions=True,
    )
    assert override_true == [
        "--model",
        "gemini-2.5-pro",
        "--effort",
        "high",
        "--dangerously-skip-permissions",
    ]
    assert override_true.count("--dangerously-skip-permissions") == 1
    assert override_true.count("--effort") == 1

    override_false = await _launch_args(
        tmp_path,
        _ai_head_toml(
            kind="claude", model="claude-opus-4-6", effort="max", skip=True
        ),
        dangerously_skip_permissions=False,
        effort=EffortLevel.LOW,
    )
    assert override_false == ["--model", "claude-opus-4-6"]
    assert "--dangerously-skip-permissions" not in override_false

    missing_key = await _launch_args(
        tmp_path,
        _ai_head_toml(kind="agy", model="gemini-2.5-pro", effort=None, skip=None),
    )
    assert missing_key == [
        "--model",
        "gemini-2.5-pro",
        "--dangerously-skip-permissions",
    ]

    broken = tmp_path / "broken.toml"
    broken.write_text("ai_head = [[[", encoding="utf-8")
    client = _inside_launch_client()
    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-10",
        tab_id="tab-20",
        pane_id="orig-pane-1",
    )
    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_agy_trusted"),
    ):
        await launch_ai_head(
            client=client,
            harness=HarnessKind.AGY,
            model="gemini-2.5-pro",
            cwd=str(tmp_path),
            config_path=broken,
            split=True,
        )
        await launch_ai_head(
            client=client,
            harness=HarnessKind.AGY,
            model="gemini-2.5-pro",
            dangerously_skip_permissions=False,
            cwd=str(tmp_path),
            config_path=broken,
            split=True,
        )
    fallback_true, explicit_false = (
        call.kwargs["args"] for call in client.start_agent.await_args_list[-2:]
    )
    assert "--dangerously-skip-permissions" in fallback_true
    assert "--dangerously-skip-permissions" not in explicit_false

    pi_args = await _launch_args(
        tmp_path,
        _ai_head_toml(
            kind="pi",
            model="openai-codex/gpt-6.1-sol",
            effort="max",
            skip=True,
        ),
    )
    assert pi_args == [
        "--provider",
        "openai-codex",
        "--model",
        "gpt-6.1-sol",
        "--thinking",
        "max",
    ]
    assert "--dangerously-skip-permissions" not in pi_args
    assert pi_args.count("--thinking") == 1

    opencode_args = await _launch_args(
        tmp_path,
        _ai_head_toml(
            kind="opencode",
            model="openai-codex/gpt-6.1-sol",
            effort="max",
            skip=True,
        ),
    )
    assert opencode_args == ["mini", "--model", "openai/gpt-6.1-sol"]
    assert "--dangerously-skip-permissions" not in opencode_args


@pytest.mark.asyncio
async def test_launch_ai_head_legacy_positional_order_and_permission_keyword(tmp_path: Path):
    """Appending permission preserves every pre-existing positional binding."""
    config_path = tmp_path / "ai-head.toml"
    config_path.write_text(
        _ai_head_toml(
            kind="agy", model="gemini-2.5-pro", effort=None, skip=False
        ),
        encoding="utf-8",
    )
    legacy_values = (
        MagicMock(spec=HerdrClient),
        HarnessKind.AGY,
        "gemini-2.5-pro",
        None,
        False,
        "legacy-workspace",
        "legacy task",
        "legacy-session",
        str(tmp_path),
        config_path,
        "legacy-head",
        False,
    )
    bound = inspect.signature(launch_ai_head).bind(*legacy_values)
    bound.apply_defaults()
    assert bound.arguments == {
        "client": legacy_values[0],
        "harness": HarnessKind.AGY,
        "model": "gemini-2.5-pro",
        "effort": None,
        "split": False,
        "workspace": "legacy-workspace",
        "initial_task": "legacy task",
        "session": "legacy-session",
        "cwd": str(tmp_path),
        "config_path": config_path,
        "name": "legacy-head",
        "focus": False,
        "dangerously_skip_permissions": None,
    }

    ctx = HerdrContext(
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        is_inside_herdr=True,
        workspace_id="ws-10",
        tab_id="tab-20",
        pane_id="orig-pane-1",
    )
    allocated = _Allocated(
        client=legacy_values[0],
        session_name="work-session",
        socket_path=Path("/tmp/herdr.sock"),
        workspace_id="ws-nosplit",
        pane_id="pane-nosplit",
        tab_id="tab-nosplit",
        owns_workspace=False,
    )
    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher._allocate_launch_target",
            new_callable=AsyncMock,
            return_value=allocated,
        ) as allocate,
        patch("herdr_master.pipeline.launcher._start_and_prompt", new_callable=AsyncMock),
    ):
        result = await launch_ai_head(
            legacy_values[0],
            HarnessKind.AGY,
            "gemini-2.5-pro",
            None,
            False,
            None,
            None,
            None,
            str(tmp_path),
            config_path,
            "legacy-head",
            False,
        )
    assert result["split"] is False
    legacy_values[0].split_pane.assert_not_called()
    allocate.assert_awaited_once_with(
        legacy_values[0],
        ctx,
        session=None,
        isolated_name="aihead-legacy-head",
        tab_label="legacy-head",
        workspace_label="legacy-head",
        cwd=str(tmp_path),
        fresh_workspace=False,
        focus=False,
    )

    old_split_client = _inside_launch_client()
    explicit_override_client = _inside_launch_client()
    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch("herdr_master.pipeline.launcher.ensure_agy_trusted"),
    ):
        await launch_ai_head(
            old_split_client,
            HarnessKind.AGY,
            "gemini-2.5-pro",
            None,
            True,
            cwd=str(tmp_path),
            config_path=config_path,
        )
        await launch_ai_head(
            explicit_override_client,
            HarnessKind.AGY,
            "gemini-2.5-pro",
            None,
            True,
            cwd=str(tmp_path),
            config_path=config_path,
            dangerously_skip_permissions=True,
        )
    old_split_client.split_pane.assert_awaited_once()
    old_args = old_split_client.start_agent.await_args.kwargs["args"]
    assert "--dangerously-skip-permissions" not in old_args
    explicit_args = explicit_override_client.start_agent.await_args.kwargs["args"]
    assert "--dangerously-skip-permissions" in explicit_args


@pytest.mark.parametrize(
    ("command", "flags", "skip", "expected"),
    [
        ("aihead", [], False, False),
        ("aihead", [], None, True),
        ("aihead", ["--skip-permissions"], False, True),
        ("aihead", ["--no-skip-permissions"], True, False),
        ("head", ["--no-skip-permissions"], True, False),
        ("head", [], False, False),
    ],
)
def test_cli_aihead_and_head_forward_permission_precedence(
    tmp_path: Path,
    command: str,
    flags: List[str],
    skip: Optional[bool],
    expected: bool,
):
    config_path = tmp_path / "ai-head.toml"
    config_path.write_text(
        _ai_head_toml(
            kind="agy", model="gemini-2.5-pro", effort="high", skip=skip
        ),
        encoding="utf-8",
    )
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-1",
            "pane_id": "pane-1",
            "split": False,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=tmp_path / "herdr.sock",
        is_inside_herdr=True,
    )
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
        patch("herdr_master.cli.main.os.execvp") as execvp,
    ):
        result = runner.invoke(
            app,
            [command, "--config", str(config_path), "--no-attach", *flags],
        )

    assert result.exit_code == 0, result.output
    kwargs = launch_mock.await_args.kwargs
    assert kwargs["dangerously_skip_permissions"] is expected
    assert kwargs["effort"] is EffortLevel.HIGH
    execvp.assert_not_called()


def test_cli_effort_override_does_not_discard_permission_override(tmp_path: Path):
    config_path = tmp_path / "ai-head.toml"
    config_path.write_text(
        _ai_head_toml(kind="agy", model="gemini-2.5-pro", effort="high", skip=True),
        encoding="utf-8",
    )
    launch_mock = AsyncMock(
        return_value={
            "session": "alpha",
            "workspace_id": "ws-1",
            "pane_id": "pane-1",
            "split": False,
        }
    )
    ctx = HerdrContext(
        session_name="alpha",
        socket_path=tmp_path / "herdr.sock",
        is_inside_herdr=True,
    )
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=ctx),
        patch("herdr_master.cli.main.launch_ai_head", launch_mock),
    ):
        result = runner.invoke(
            app,
            [
                "head",
                "--config",
                str(config_path),
                "--effort",
                "low",
                "--no-skip-permissions",
                "--no-attach",
            ],
        )

    assert result.exit_code == 0, result.output
    kwargs = launch_mock.await_args.kwargs
    assert kwargs["effort"] is EffortLevel.LOW
    assert kwargs["dangerously_skip_permissions"] is False
