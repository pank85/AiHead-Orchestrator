import asyncio
import sys
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app
from herdr_master.herdr.core_client import (
    CoreClientError,
    CoreRPCError,
    request,
    terminal_kill,
    terminal_list,
    terminal_run,
    terminal_send,
)


@pytest.fixture
def hc(tmp_path):
    path = tmp_path / "hc"

    def make(body):
        path.write_text(f"#!{sys.executable}\nimport json, sys, time\n{body}\n")
        path.chmod(0o755)
        return str(path)

    return make


async def test_terminal_list_and_matching_id(hc):
    executable = hc('''
assert sys.argv[1:] == ["connect"]
req = json.loads(sys.stdin.readline())
assert req == {"jsonrpc": "2.0", "id": 1, "method": "terminal.list", "params": {}}
print(json.dumps({"jsonrpc": "2.0", "method": "event"}), flush=True)
print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": "wrong"}), flush=True)
result = {"terminals": [], "hidden_finished": 0, "machines": []}
print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}), flush=True)
time.sleep(10)
''')
    assert await terminal_list(executable=executable) == {
        "terminals": [], "hidden_finished": 0, "machines": []
    }


async def test_generic_request_and_rpc_error(hc):
    executable = hc('''
req = json.loads(sys.stdin.readline())
assert req["method"] == "rpc.discover" and req["params"] == {"x": 1}
error = {"code": -32000, "message": "missing", "data": {"kind": "not_found"}}
print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "error": error}), flush=True)
''')
    with pytest.raises(CoreRPCError) as caught:
        await request("rpc.discover", {"x": 1}, executable=executable)
    assert caught.value.code == -32000
    assert caught.value.data == {"kind": "not_found"}


async def test_large_discovery_response(hc):
    executable = hc('''
req = json.loads(sys.stdin.readline())
print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": "x" * 100000}), flush=True)
''')
    assert await request("rpc.discover", executable=executable) == "x" * 100000


@pytest.mark.parametrize("body", [
    'print("not json", flush=True)',
    'print(json.dumps({"jsonrpc": "2.0", "id": 1}), flush=True)',
    'print(json.dumps({"jsonrpc": "2.0", "id": 1, "error": {}}), flush=True)',
    'pass',
])
async def test_invalid_or_closed_connection(hc, body):
    with pytest.raises(CoreClientError):
        await request("test", executable=hc(body))


async def test_timeout_and_missing_executable(hc):
    with pytest.raises(CoreClientError, match="timed out"):
        await request("test", timeout=0.05, executable=hc("time.sleep(10)"))
    with pytest.raises(CoreClientError, match="failed"):
        await request("test", executable="/nonexistent/hc")


async def test_cancellation(hc):
    task = asyncio.create_task(request("test", executable=hc("time.sleep(10)")))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_cli_core_terminals():
    result_data = {"terminals": [], "hidden_finished": 0, "machines": []}
    with patch("herdr_master.herdr.core_client.terminal_list", new_callable=AsyncMock) as listing:
        listing.return_value = result_data
        result = CliRunner().invoke(aihead_app, ["core", "terminals"])
    assert result.exit_code == 0
    assert '"terminals": []' in result.output
    listing.assert_awaited_once()


def test_cli_core_terminals_reports_failure():
    with patch("herdr_master.herdr.core_client.terminal_list", new_callable=AsyncMock) as listing:
        listing.side_effect = CoreClientError("core unavailable")
        result = CliRunner().invoke(aihead_app, ["core", "terminals"])
    assert result.exit_code != 0
    assert "core unavailable" in result.output


async def test_terminal_run_send_kill(hc):
    executable = hc('''
req = json.loads(sys.stdin.readline())
print(json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"echo": req}}), flush=True)
time.sleep(10)
''')
    run_res = await terminal_run(
        ["pi", "--", "hello"],
        cwd="/tmp/proj",
        name="child-1",
        labels={"role": "worker"},
        no_parent=False,
        wait=False,
        executable=executable,
    )
    assert run_res["echo"]["method"] == "terminal.run"
    assert run_res["echo"]["params"] == {
        "argv": ["pi", "--", "hello"],
        "cwd": "/tmp/proj",
        "name": "child-1",
        "labels": {"role": "worker"},
        "no_parent": False,
        "wait": False,
    }

    send_res = await terminal_send("t2", "status?", executable=executable)
    assert send_res["echo"]["method"] == "terminal.send"
    assert send_res["echo"]["params"] == {"terminal": "t2", "text": "status?"}

    kill_res = await terminal_kill("t2", executable=executable)
    assert kill_res["echo"]["method"] == "terminal.kill"
    assert kill_res["echo"]["params"] == {"terminal": "t2"}
