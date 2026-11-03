"""Unit tests for Unix domain socket communication, NDJSON framing, and Protocol 22 client."""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.snapshot import (
    ProtocolMismatchError,
    SessionSnapshot,
)
from herdr_master.herdr.socket import (
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
