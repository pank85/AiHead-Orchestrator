"""Unit tests for HerdrClient.prompt_agent retry policy, parameter handling, and attempt limits."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from protocol22_fixtures import agent_prompted

from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.socket import HerdrConnectionError, HerdrRPCError


@pytest.mark.asyncio
async def test_prompt_agent_invalid_attempt_counts():
    """Verify max_attempts < 1 raises ValueError before issuing any RPC."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock()

    with pytest.raises(ValueError, match=r"max_attempts must be at least 1, got 0"):
        await client.prompt_agent("pane-1", "test", max_attempts=0)

    with pytest.raises(ValueError, match=r"max_attempts must be at least 1, got -1"):
        await client.prompt_agent("pane-1", "test", max_attempts=-1)

    with pytest.raises(ValueError, match=r"max_attempts must be at least 1, got -10"):
        await client.prompt_agent("pane-1", "test", max_attempts=-10)

    assert client._rpc.call_count == 0


@pytest.mark.asyncio
async def test_prompt_agent_default_retry_compatibility_eventual_success(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify existing 20-attempt default retries on transient errors and succeeds."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(
        side_effect=[
            HerdrRPCError("agent_busy", "agent target is busy"),
            HerdrRPCError("agent_not_ready", "agent is not ready"),
            {"result": agent_prompted(pane_id="pane-1")},
        ]
    )

    sleep_calls = []
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=lambda s: sleep_calls.append(s)))

    res = await client.prompt_agent("pane-1", "hello")  # default max_attempts=20

    assert res == agent_prompted(pane_id="pane-1")
    assert client._rpc.call_count == 3
    assert sleep_calls == [0.5, 0.5]


@pytest.mark.asyncio
async def test_prompt_agent_default_retry_compatibility_exhausted(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify default max_attempts=20 exhausts all 20 attempts before raising last error."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(side_effect=HerdrRPCError("agent_busy", "agent is busy"))

    sleep_calls = []
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=lambda s: sleep_calls.append(s)))

    with pytest.raises(HerdrRPCError) as exc_info:
        await client.prompt_agent("pane-1", "hello")

    assert exc_info.value.code == "agent_busy"
    assert client._rpc.call_count == 20
    assert len(sleep_calls) == 19


@pytest.mark.asyncio
async def test_prompt_agent_one_attempt_busy_fails_immediately(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify max_attempts=1 fails immediately on agent_busy with zero retry sleeps."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(side_effect=HerdrRPCError("agent_busy", "agent is busy"))

    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    with pytest.raises(HerdrRPCError) as exc_info:
        await client.prompt_agent("pane-1", "hello", max_attempts=1)

    assert exc_info.value.code == "agent_busy"
    assert client._rpc.call_count == 1
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_agent_one_attempt_not_ready_fails_immediately(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify max_attempts=1 fails immediately on agent_not_ready with zero retry sleeps."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(side_effect=HerdrRPCError("agent_not_ready", "agent not ready"))

    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    with pytest.raises(HerdrRPCError) as exc_info:
        await client.prompt_agent("pane-1", "hello", max_attempts=1)

    assert exc_info.value.code == "agent_not_ready"
    assert client._rpc.call_count == 1
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_agent_one_attempt_not_detected_agent_message_fails_immediately(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify max_attempts=1 fails immediately on message pattern match without sleeping."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(
        side_effect=HerdrRPCError("custom_err", "target does not have a detected agent")
    )

    sleep_mock = AsyncMock()
    monkeypatch.setattr(asyncio, "sleep", sleep_mock)

    with pytest.raises(HerdrRPCError) as exc_info:
        await client.prompt_agent("pane-1", "hello", max_attempts=1)

    assert "does not have a detected agent" in exc_info.value.message
    assert client._rpc.call_count == 1
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_prompt_agent_one_attempt_success():
    """Verify max_attempts=1 succeeds on single RPC invocation."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(return_value={"result": agent_prompted(pane_id="pane-1")})

    res = await client.prompt_agent("pane-1", "hello world", max_attempts=1)

    assert res == agent_prompted(pane_id="pane-1")
    assert client._rpc.call_count == 1


@pytest.mark.asyncio
async def test_prompt_agent_transport_timeout_no_replay():
    """Verify transport timeout or connection error raises immediately with no retry replay."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(side_effect=asyncio.TimeoutError("RPC transport timeout"))

    with pytest.raises(asyncio.TimeoutError):
        await client.prompt_agent("pane-1", "hello", max_attempts=1)

    assert client._rpc.call_count == 1

    # Connection error with default max_attempts=20 must also NOT replay
    client._rpc = AsyncMock(side_effect=HerdrConnectionError("Connection lost"))

    with pytest.raises(HerdrConnectionError):
        await client.prompt_agent("pane-1", "hello")

    assert client._rpc.call_count == 1


@pytest.mark.asyncio
async def test_prompt_agent_params_unchanged_target_text_and_omission_of_wait():
    """Verify params contain exact target and text, omit wait and timeout_ms when not passed."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(return_value={"result": agent_prompted(pane_id="pane:w1:p1")})

    await client.prompt_agent("pane:w1:p1", "exact prompt content", max_attempts=1)

    assert client._rpc.call_count == 1
    call_args, call_kwargs = client._rpc.call_args
    assert call_args[0] == "agent.prompt"
    assert call_args[1] == {
        "target": "pane:w1:p1",
        "text": "exact prompt content",
    }
    assert "wait" not in call_args[1]
    assert "timeout_ms" not in call_args[1]
    assert call_kwargs.get("timeout") == 30.0


@pytest.mark.asyncio
async def test_prompt_agent_params_with_explicit_wait_and_timeout():
    """Verify params include wait and timeout_ms when provided, and calculate rpc_timeout."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(return_value={"result": agent_prompted(pane_id="pane:w1:p1")})

    await client.prompt_agent(
        "pane:w1:p1",
        "prompt text",
        wait="idle",
        timeout_ms=5000,
        max_attempts=1,
    )

    assert client._rpc.call_count == 1
    call_args, call_kwargs = client._rpc.call_args
    assert call_args[0] == "agent.prompt"
    assert call_args[1] == {
        "target": "pane:w1:p1",
        "text": "prompt text",
        "wait": "idle",
        "timeout_ms": 5000,
    }
    assert call_kwargs.get("timeout") == 20.0  # (5000 / 1000.0) + 15.0


@pytest.mark.asyncio
async def test_prompt_agent_custom_max_attempts(monkeypatch: pytest.MonkeyPatch):
    """Verify arbitrary positive max_attempts count limits retry loop precisely."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(
        side_effect=[
            HerdrRPCError("agent_busy", "busy"),
            HerdrRPCError("agent_busy", "busy"),
            {"result": agent_prompted(pane_id="pane-1")},
        ]
    )

    sleep_calls = []
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=lambda s: sleep_calls.append(s)))

    res = await client.prompt_agent("pane-1", "hello", max_attempts=3)
    assert res == agent_prompted(pane_id="pane-1")
    assert client._rpc.call_count == 3
    assert len(sleep_calls) == 2

    # Now if max_attempts=2 with 3 errors, it fails after 2
    client._rpc = AsyncMock(side_effect=HerdrRPCError("agent_busy", "busy"))
    sleep_calls.clear()

    with pytest.raises(HerdrRPCError):
        await client.prompt_agent("pane-1", "hello", max_attempts=2)

    assert client._rpc.call_count == 2
    assert len(sleep_calls) == 1


@pytest.mark.asyncio
async def test_client_start_agent_agent_name_taken_clears_authority_and_retries(
    monkeypatch: pytest.MonkeyPatch,
):
    """Verify that start_agent extracts candidate panes on agent_name_taken and clears authority."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    taken_err = HerdrRPCError(
        "agent_name_taken",
        "agent name ai-head is already used; candidates: terminal_id=t1 pane_id=wF:p99 workspace_id=wF status=Unknown",
    )
    rpc_calls = []

    async def mock_rpc(method: str, params: dict, **kwargs):
        rpc_calls.append((method, params))
        if method == "agent.start" and len(rpc_calls) == 1:
            raise taken_err
        return {"result": {"type": "ok"}}

    client._rpc = AsyncMock(side_effect=mock_rpc)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())

    res = await client.start_agent("ai-head", "opencode", "wF:p10")
    assert res == {"type": "ok"}
    # Verify pane.clear_agent_authority was called on candidate pane and target pane
    cleared_panes = [p["pane_id"] for m, p in rpc_calls if m == "pane.clear_agent_authority"]
    assert "wF:p99" in cleared_panes
    assert "wF:p10" in cleared_panes


@pytest.mark.asyncio
async def test_client_close_pane_and_clear_agent_authority():
    """Verify close_pane and clear_agent_authority RPC calls."""
    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock(return_value={"result": {"type": "ok"}})

    res_close = await client.close_pane("pane-1")
    assert res_close == {"type": "ok"}
    client._rpc.assert_called_with("pane.close", {"pane_id": "pane-1"})

    res_clear = await client.clear_agent_authority("pane-1")
    assert res_clear == {"type": "ok"}
    client._rpc.assert_called_with("pane.clear_agent_authority", {"pane_id": "pane-1"})
