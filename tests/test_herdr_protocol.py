"""Essential tests for Herdr NDJSON framing, RPC validation, and snapshot parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from herdr_master.herdr.protocol import parse_pane_read, parse_prompt_result
from herdr_master.herdr.snapshot import SessionSnapshot
from herdr_master.herdr.socket import HerdrRPCError, NDJSONBuffer, validate_rpc_response


def test_ndjson_buffer_handles_chunked_and_multi_line_frames() -> None:
    buf = NDJSONBuffer()
    assert buf.feed(b'{"id":"1","ok":') == []
    frames = buf.feed(b'true}\n{"id":"2","ok":false}\n{"partial":')
    assert frames == [{"id": "1", "ok": True}, {"id": "2", "ok": False}]
    assert buf.remaining == b'{"partial":'
    buf.clear()
    assert buf.remaining == b""


def test_validate_rpc_response_checks_errors_and_ids() -> None:
    validate_rpc_response({"id": "req-1", "ok": True, "result": {}}, "req-1")

    with pytest.raises(HerdrRPCError) as exc_info:
        validate_rpc_response(
            {
                "id": "req-1",
                "ok": False,
                "error": {"code": "agent_not_found", "message": "Missing pane"},
            },
            "req-1",
        )
    assert exc_info.value.code == "agent_not_found"


def test_protocol_parsers_and_snapshot_version_check() -> None:
    agent = parse_prompt_result(
        {
            "type": "agent_prompted",
            "agent": {
                "terminal_id": "term-1",
                "pane_id": "w1:p1",
                "workspace_id": "w1",
                "tab_id": "w1:t1",
                "agent": "pi",
                "agent_status": "idle",
                "focused": True,
                "revision": 1,
            },
        }
    )
    assert agent.pane_id == "w1:p1"
    assert agent.agent == "pi"

    pane_read = parse_pane_read(
        {
            "pane_id": "w1:p1",
            "workspace_id": "w1",
            "tab_id": "w1:t1",
            "source": "visible",
            "format": "text",
            "text": "hello\nworld",
            "revision": 1,
            "truncated": False,
        }
    )
    assert pane_read.pane_id == "w1:p1"
    assert "hello" in pane_read.text

    snap = SessionSnapshot.model_validate({"protocol": 22, "version": "0.9.5"})
    assert snap.protocol == 22

    with pytest.raises(ValidationError, match="Unsupported Herdr protocol"):
        SessionSnapshot.model_validate({"protocol": 999, "version": "0.9.5"})
