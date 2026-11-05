"""Unit tests for Unix domain socket communication, NDJSON framing, and Protocol 22 client."""

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.snapshot import (
    ProtocolMismatchError,
    SessionSnapshot,
)
from herdr_master.herdr.socket import (
    HerdrConnectionError,
    HerdrRPCError,
    NDJSONBuffer,
    send_rpc,
)


def test_ndjson_line_reassembly():
    """Verify that chunked byte streams are reassembled properly across line breaks."""
    buf = NDJSONBuffer()

    # 1. Feed partial first message
    part1 = b'{"id": "msg-1", "result": {"stat'
    messages = buf.feed(part1)
    assert len(messages) == 0
    assert len(buf.remaining) > 0

    # 2. Feed rest of first message and start of second
    part2 = b'us": "ok"}}\n{"id": "msg-2", "val'
    messages = buf.feed(part2)
    assert len(messages) == 1
    assert messages[0] == {"id": "msg-1", "result": {"status": "ok"}}

    # 3. Feed rest of second message and full third message
    part3 = b'ue": 42}\n{"id": "msg-3", "done": true}\n'
    messages = buf.feed(part3)
    assert len(messages) == 2
    assert messages[0] == {"id": "msg-2", "value": 42}
    assert messages[1] == {"id": "msg-3", "done": True}
    assert len(buf.remaining) == 0


def test_session_snapshot_validation():
    """Verify SessionSnapshot validates Protocol 22 and rejects mismatched protocols."""
    valid_data = {
        "protocol": 22,
        "version": "0.9.5",
        "workspaces": [{"workspace_id": "ws-1", "label": "main", "focused": True}],
        "tabs": [{"tab_id": "t-1", "workspace_id": "ws-1", "label": "editor"}],
        "panes": [{"pane_id": "p-1", "workspace_id": "ws-1", "tab_id": "t-1"}],
        "agents": [{"pane_id": "p-1", "agent_status": "working", "name": "coder"}],
    }

    snapshot = SessionSnapshot.model_validate(valid_data)
    assert snapshot.protocol == 22
    assert snapshot.version == "0.9.5"
    assert len(snapshot.workspaces) == 1
    assert snapshot.workspaces[0].workspace_id == "ws-1"

    # Snapshot from fixture
    fixture_path = Path(__file__).parent / "fixtures" / "protocol22" / "snapshot.json"
    if fixture_path.exists():
        with open(fixture_path, "r", encoding="utf-8") as f:
            fixture_json = json.load(f)
        snap_from_file = SessionSnapshot.from_response(fixture_json)
        assert snap_from_file.protocol == 22
        assert len(snap_from_file.panes) == 2

    # Mismatched protocol 21 must raise
    invalid_data = dict(valid_data)
    invalid_data["protocol"] = 21
    with pytest.raises(ProtocolMismatchError, match="expected 22"):
        SessionSnapshot(**invalid_data)

    with pytest.raises(ProtocolMismatchError, match="expected 22"):
        SessionSnapshot.from_response({"snapshot": invalid_data})


@pytest.mark.asyncio
async def test_one_shot_rpc_mock_server():
    """Test one-shot RPC against an in-memory Unix domain socket server."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "mock_herdr.sock"

        async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            line = await reader.readline()
            req = json.loads(line.decode("utf-8"))
            if req.get("method") == "ping":
                resp = {"id": req["id"], "result": {"type": "pong", "protocol": 22}}
            elif req.get("method") == "fail":
                resp = {
                    "id": req["id"],
                    "error": {"code": "ERR_FAILED", "message": "Simulated error"},
                }
            else:
                resp = {"id": req["id"], "result": {"echo": req.get("params")}}

            writer.write(json.dumps(resp).encode("utf-8") + b"\n")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle_client, path=str(sock_path))

        try:
            client = HerdrClient(socket_path=sock_path)

            # Test ping
            pong = await client.ping()
            assert pong.get("type") == "pong"
            assert pong.get("protocol") == 22

            # Test RPC error handling
            with pytest.raises(HerdrRPCError) as exc_info:
                await send_rpc(sock_path, "fail")
            assert exc_info.value.code == "ERR_FAILED"
            assert "Simulated error" in exc_info.value.message

        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_subscribe_stream_and_double_subscribe_guard():
    """Test long-lived events.subscribe stream and refusal of second subscribe."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "mock_sub.sock"

        async def handle_sub_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            line = await reader.readline()
            req = json.loads(line.decode("utf-8"))
            assert req.get("method") == "events.subscribe"
            # Send subscription started
            start_resp = {"id": req["id"], "result": {"type": "subscription_started"}}
            writer.write(json.dumps(start_resp).encode("utf-8") + b"\n")
            await writer.drain()

            # Stream two events
            ev1 = {
                "event": "pane.agent_status_changed",
                "data": {"pane_id": "p-1", "workspace_id": "ws-1", "agent_status": "working"},
            }
            ev2 = {
                "event": "pane.agent_status_changed",
                "data": {"pane_id": "p-1", "workspace_id": "ws-1", "agent_status": "done"},
            }
            await asyncio.sleep(0.05)
            writer.write(json.dumps(ev1).encode("utf-8") + b"\n")
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.write(json.dumps(ev2).encode("utf-8") + b"\n")
            await writer.drain()
            await asyncio.sleep(0.2)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle_sub_client, path=str(sock_path))

        try:
            client = HerdrClient(socket_path=sock_path)
            sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

            started = await sub.connect()
            assert started["result"]["type"] == "subscription_started"

            # Attempting to call subscribe again on the same active connection must raise
            with pytest.raises(RuntimeError, match="Socket already subscribed"):
                await sub.subscribe()

            # Read events
            received_events: List[Dict[str, Any]] = []
            async for ev in sub.events():
                received_events.append(ev)
                if len(received_events) == 2:
                    break

            assert len(received_events) == 2
            assert received_events[0]["data"]["agent_status"] == "working"
            assert received_events[1]["data"]["agent_status"] == "done"

            await sub.close()

        finally:
            server.close()
            await server.wait_closed()


@pytest.mark.asyncio
async def test_start_agent_retries_on_agent_pane_busy():
    """Verify start_agent automatically retries if the pane shell is not yet ready."""
    from unittest.mock import AsyncMock

    client = HerdrClient(socket_path="/tmp/fake.sock")
    client._rpc = AsyncMock()
    client._rpc.side_effect = [
        HerdrRPCError("agent_pane_busy", "agent target pane wD:p4 is not an available shell"),
        {"result": {"type": "agent_started", "name": "scout"}},
    ]

    res = await client.start_agent(
        name="scout",
        kind="agy",
        pane_id="wD:p4",
        timeout_ms=5000,
    )

    assert res["type"] == "agent_started"
    assert client._rpc.call_count == 2


class _FakeAsyncStreamWriter:
    """Mock StreamWriter for deterministic socket framing tests."""

    def __init__(self) -> None:
        self.written: List[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


def setup_fake_stream(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Provide a real StreamReader and controlled FakeWriter with mocked unix connection."""
    sock_path = tmp_path / "mock_stream.sock"
    sock_path.touch()

    reader = asyncio.StreamReader(loop=asyncio.get_running_loop())
    writer = _FakeAsyncStreamWriter()

    async def _fake_open_unix_connection(path: str):
        assert Path(path) == sock_path
        return reader, writer

    monkeypatch.setattr(asyncio, "open_unix_connection", _fake_open_unix_connection)
    monkeypatch.setattr(
        "herdr_master.herdr.subscribe.uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="1"))
    )
    return sock_path, reader, writer


@pytest.mark.asyncio
async def test_subscribe_coalesced_ack_and_multiple_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Verify that multiple events coalesced with the subscription acknowledgement are not lost."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    ack = {"id": "sub-1", "result": {"type": "subscription_started"}}
    ev1 = {
        "event": "pane.agent_status_changed",
        "data": {"pane_id": "p-1", "agent_status": "working"},
    }
    ev2 = {
        "event": "pane.agent_status_changed",
        "data": {"pane_id": "p-1", "agent_status": "blocked"},
    }

    # Feed acknowledgement and two complete events in a single chunk
    feed_chunk = (
        json.dumps(ack).encode("utf-8")
        + b"\n"
        + json.dumps(ev1).encode("utf-8")
        + b"\n"
        + json.dumps(ev2).encode("utf-8")
        + b"\n"
    )
    reader.feed_data(feed_chunk)

    started = await sub.connect()
    assert started["result"]["type"] == "subscription_started"

    # Events arriving with acknowledgement must be immediately readable
    # without waiting for more chunks
    events_iter = sub.events().__aiter__()
    received_1 = await anext(events_iter)
    assert received_1 == ev1
    received_2 = await anext(events_iter)
    assert received_2 == ev2

    reader.feed_eof()
    with pytest.raises(StopAsyncIteration):
        await anext(events_iter)

    assert sub._closed is True
    assert writer.closed is True


@pytest.mark.asyncio
async def test_subscribe_coalesced_ack_complete_and_partial_event(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Verify handling of ack + complete event + partial event completed by subsequent feed."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    ack = {"id": "sub-1", "result": {"type": "subscription_started"}}
    ev1 = {
        "event": "pane.agent_status_changed",
        "data": {"pane_id": "p-1", "agent_status": "working"},
    }
    ev2 = {
        "event": "pane.agent_status_changed",
        "data": {"pane_id": "p-1", "agent_status": "done"},
    }

    ev2_bytes = json.dumps(ev2).encode("utf-8") + b"\n"
    split_point = 15
    part_a = ev2_bytes[:split_point]
    part_b = ev2_bytes[split_point:]

    # Feed 1: ack + complete ev1 + partial ev2
    chunk1 = (
        json.dumps(ack).encode("utf-8")
        + b"\n"
        + json.dumps(ev1).encode("utf-8")
        + b"\n"
        + part_a
    )
    reader.feed_data(chunk1)

    started = await sub.connect()
    assert started["result"]["type"] == "subscription_started"

    # ev1 is immediately yielded without waiting for the rest of ev2
    events_iter = sub.events().__aiter__()
    received_1 = await anext(events_iter)
    assert received_1 == ev1

    # Now deliver part_b to complete ev2, then EOF
    reader.feed_data(part_b)
    reader.feed_eof()

    received_2 = await anext(events_iter)
    assert received_2 == ev2

    with pytest.raises(StopAsyncIteration):
        await anext(events_iter)

    assert sub._closed is True


@pytest.mark.asyncio
async def test_subscribe_immediate_eof_after_coalesced_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Verify clean EOF handling when server disconnects immediately after coalesced frames."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    ack = {"id": "sub-1", "result": {"type": "subscription_started"}}
    ev1 = {
        "event": "pane.agent_status_changed",
        "data": {"pane_id": "p-1", "agent_status": "working"},
    }
    ev2 = {
        "event": "pane.agent_status_changed",
        "data": {"pane_id": "p-1", "agent_status": "done"},
    }

    # Feed ack + ev1 + ev2 and immediately EOF
    feed_chunk = (
        json.dumps(ack).encode("utf-8")
        + b"\n"
        + json.dumps(ev1).encode("utf-8")
        + b"\n"
        + json.dumps(ev2).encode("utf-8")
        + b"\n"
    )
    reader.feed_data(feed_chunk)
    reader.feed_eof()

    started = await sub.connect()
    assert started["result"]["type"] == "subscription_started"

    # Both events must be drained before EOF terminates the stream cleanly
    received = []
    async for ev in sub.events():
        received.append(ev)

    assert received == [ev1, ev2]
    assert sub._closed is True


@pytest.mark.asyncio
async def test_subscribe_ordering_and_no_duplication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Verify events are yielded in exact wire order with no duplicates across chunk boundaries."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    ack = {"id": "sub-1", "result": {"type": "subscription_started"}}
    events = [
        {"event": "pane.agent_status_changed", "data": {"seq": i, "pane_id": "p-1"}}
        for i in range(5)
    ]

    # Ack + event 0
    reader.feed_data(
        json.dumps(ack).encode("utf-8") + b"\n" + json.dumps(events[0]).encode("utf-8") + b"\n"
    )
    await sub.connect()

    # Events 1 and 2 in next chunk
    chunk_1_2 = (
        json.dumps(events[1]).encode("utf-8")
        + b"\n"
        + json.dumps(events[2]).encode("utf-8")
        + b"\n"
    )
    reader.feed_data(chunk_1_2)
    # Event 3 and 4 in subsequent individual chunks
    reader.feed_data(json.dumps(events[3]).encode("utf-8") + b"\n")
    reader.feed_data(json.dumps(events[4]).encode("utf-8") + b"\n")
    reader.feed_eof()

    collected = []
    async for ev in sub.events():
        collected.append(ev)

    assert len(collected) == 5
    assert collected == events


@pytest.mark.asyncio
async def test_subscribe_double_subscribe_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Verify double-subscribe refusal, idempotent close, and pending event release."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    ack = {"id": "sub-1", "result": {"type": "subscription_started"}}
    ev1 = {"event": "pane.agent_status_changed", "data": {"seq": 1}}
    ev2 = {"event": "pane.agent_status_changed", "data": {"seq": 2}}

    reader.feed_data(
        json.dumps(ack).encode("utf-8")
        + b"\n"
        + json.dumps(ev1).encode("utf-8")
        + b"\n"
        + json.dumps(ev2).encode("utf-8")
        + b"\n"
    )

    await sub.connect()
    assert len(sub._pending_events) == 2

    # Second subscribe refusal on active connection
    with pytest.raises(RuntimeError, match="Socket already subscribed"):
        await sub.subscribe()
    with pytest.raises(RuntimeError, match="Socket already subscribed"):
        await sub.connect()

    # Explicit close cleans up and drops pending frames
    await sub.close()
    assert sub._closed is True
    assert len(sub._pending_events) == 0
    assert len(sub._buffer.remaining) == 0
    assert writer.closed is True

    # Closing again is idempotent
    await sub.close()

    # Calling events() after close raises RuntimeError
    with pytest.raises(RuntimeError, match=r"Must call connect\(\) before reading events"):
        async for _ in sub.events():
            pass

    # Calling connect() after close raises RuntimeError
    with pytest.raises(RuntimeError, match="Subscription is closed"):
        await sub.connect()


@pytest.mark.asyncio
async def test_subscribe_ack_error_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Verify that an error in the ack frame closes the connection and discards surplus data."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    err_ack = {
        "id": "sub-1",
        "error": {"code": "PERMISSION_DENIED", "message": "Access denied"},
    }
    surplus_ev = {"event": "pane.agent_status_changed", "data": {"seq": 1}}

    reader.feed_data(
        json.dumps(err_ack).encode("utf-8")
        + b"\n"
        + json.dumps(surplus_ev).encode("utf-8")
        + b"\n"
    )

    with pytest.raises(HerdrRPCError) as exc_info:
        await sub.connect()

    assert exc_info.value.code == "PERMISSION_DENIED"
    assert sub._closed is True
    assert len(sub._pending_events) == 0
    assert len(sub._buffer.remaining) == 0
    assert writer.closed is True


@pytest.mark.asyncio
async def test_subscribe_mismatched_request_id_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Finding 5: subscription ACK with mismatched request ID is rejected and closes socket."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    wrong_id_ack = {
        "id": "wrong-request-id",
        "result": {"type": "subscription_started"},
    }
    reader.feed_data(json.dumps(wrong_id_ack).encode("utf-8") + b"\n")

    with pytest.raises(HerdrConnectionError, match="mismatched request id"):
        await sub.connect()

    assert sub._closed is True
    assert writer.closed is True


@pytest.mark.asyncio
async def test_subscribe_wrong_result_tag_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Finding 5: subscription ACK with missing or wrong result tag is rejected."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    bad_tag_ack = {
        "id": "sub-1",
        "result": {"type": "wrong_tag"},
    }
    reader.feed_data(json.dumps(bad_tag_ack).encode("utf-8") + b"\n")

    with pytest.raises(HerdrConnectionError, match="Invalid subscription acknowledgement"):
        await sub.connect()

    assert sub._closed is True
    assert writer.closed is True


@pytest.mark.asyncio
async def test_subscribe_event_before_ack_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Finding 5: premature event frame before subscription ACK is rejected."""
    sock_path, reader, writer = setup_fake_stream(monkeypatch, tmp_path)
    client = HerdrClient(socket_path=sock_path)
    sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])

    event_frame = {
        "event": "pane.agent_status_changed",
        "data": {"pane_id": "p-1", "agent_status": "working"},
    }
    reader.feed_data(json.dumps(event_frame).encode("utf-8") + b"\n")

    with pytest.raises(HerdrConnectionError):
        await sub.connect()

    assert sub._closed is True
    assert writer.closed is True


@pytest.mark.asyncio
async def test_subscribe_coalesced_ack_and_event_real_unix_socket():
    """Verify that coalesced ack and event are handled over a real Unix domain socket."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sock_path = Path(tmpdir) / "coalesced_sub.sock"

        async def handle_sub_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            line = await reader.readline()
            req = json.loads(line.decode("utf-8"))
            start_resp = {"id": req["id"], "result": {"type": "subscription_started"}}
            ev = {
                "event": "pane.agent_status_changed",
                "data": {"pane_id": "p-1", "workspace_id": "ws-1", "agent_status": "working"},
            }
            # Write both ack and event in a single write call
            writer.write(
                json.dumps(start_resp).encode("utf-8")
                + b"\n"
                + json.dumps(ev).encode("utf-8")
                + b"\n"
            )
            await writer.drain()
            await asyncio.sleep(0.05)
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(handle_sub_client, path=str(sock_path))
        try:
            client = HerdrClient(socket_path=sock_path)
            sub = client.subscribe([{"type": "pane.agent_status_changed", "pane_id": "p-1"}])
            started = await sub.connect()
            assert started["result"]["type"] == "subscription_started"

            events = []
            async for ev in sub.events():
                events.append(ev)

            assert len(events) == 1
            assert events[0]["data"]["agent_status"] == "working"
        finally:
            server.close()
            await server.wait_closed()
