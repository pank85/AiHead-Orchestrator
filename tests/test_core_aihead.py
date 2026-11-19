from hashlib import sha256
from unittest.mock import AsyncMock, patch

import pytest

from herdr_master.herdr.core_aihead import CoreAiHeadError, ensure_core_aihead
from herdr_master.herdr.core_client import CoreClientError


def identity(path):
    digest = sha256(str(path.resolve()).encode()).hexdigest()
    return f"aihead-{digest[:24]}", {"aihead.role": "head", "aihead.project": digest}


@pytest.fixture
def rpc():
    module = "herdr_master.herdr.core_aihead"
    with (patch(f"{module}.core_client.terminal_list", new_callable=AsyncMock) as listing,
          patch(f"{module}.core_client.request", new_callable=AsyncMock) as request,
          patch(f"{module}.which", return_value="/usr/bin/pi"),
          patch(f"{module}.build_core_aihead_prompt", return_value="bootstrap") as prompt):
        listing.return_value = {"terminals": []}
        request.return_value = {"address": "returned/address", "state": "live"}
        yield listing, request, prompt


async def test_start_uses_config_and_returned_address(tmp_path, rpc):
    (tmp_path / ".herdr-master.toml").write_text(
        '[ai_head]\nharness = "pi"\nmodel = "xai/grok"\neffort = "high"\nautomode = true\n'
    )
    listing, request, prompt = rpc
    result = await ensure_core_aihead(backend="core", cwd=tmp_path, task="hello")
    assert (result.address, result.created, result.state, result.prompt_status) == (
        "returned/address", True, "live", "argv_unconfirmed")
    name, labels = identity(tmp_path)
    method, params = request.await_args.args
    assert method == "terminal.run"
    assert params == {"argv": ["/usr/bin/pi", "--provider", "xai", "--model", "grok",
                               "--thinking", "high", "--", "bootstrap"],
                      "cwd": str(tmp_path), "name": name, "labels": labels,
                      "no_parent": True, "wait": False}
    assert prompt.call_args.kwargs["initial_task"] == "hello"
    assert prompt.call_args.kwargs["automode"] is True
    listing.assert_awaited_once()


async def test_reuse_and_task_rejection(tmp_path, rpc):
    listing, request, _ = rpc
    name, labels = identity(tmp_path)
    listing.return_value = {"terminals": [{"name": name, "labels": labels,
                                           "address": "t42", "state": "live"}]}
    options = {"backend": "core", "cwd": tmp_path, "kind": "pi", "model": "xai/grok"}
    result = await ensure_core_aihead(**options)
    assert (result.address, result.created, result.prompt_status) == ("t42", False, "not_sent")
    with pytest.raises(CoreAiHeadError, match="task was not sent"):
        await ensure_core_aihead(**options, task="task")
    request.assert_not_awaited()


@pytest.mark.parametrize("scenario", ["duplicate", "conflict", "bad_list", "missing_address"])
async def test_discovery_fails_closed(tmp_path, rpc, scenario):
    listing, request, _ = rpc
    name, labels = identity(tmp_path)
    item = {"name": name, "labels": labels, "address": "t1", "state": "live"}
    if scenario == "duplicate":
        listing.return_value = {"terminals": [item, item]}
    elif scenario == "conflict":
        listing.return_value = {"terminals": [{**item, "labels": {}}]}
    elif scenario == "bad_list":
        listing.return_value = {}
    else:
        listing.return_value = {"terminals": [{**item, "address": None}]}
    with pytest.raises(CoreAiHeadError):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind="pi", model="xai/grok")
    request.assert_not_awaited()


async def test_unknown_spawn_is_never_retried(tmp_path, rpc):
    _, request, _ = rpc
    request.side_effect = CoreClientError("timeout")
    with pytest.raises(CoreAiHeadError, match="inspect before retrying"):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind="pi", model="xai/grok")
    request.assert_awaited_once()


async def test_pending_and_unknown_result(tmp_path, rpc):
    _, request, _ = rpc
    options = {"backend": "core", "cwd": tmp_path, "kind": "pi", "model": "xai/grok"}
    request.return_value = {"address": "t1", "reason": "spawn_pending", "state": "live"}
    result = await ensure_core_aihead(**options)
    assert (result.state, result.prompt_status) == ("spawn_pending", "unknown")
    request.return_value = {}
    with pytest.raises(CoreAiHeadError, match="outcome unknown"):
        await ensure_core_aihead(**options)


async def test_unsupported_and_list_errors_never_spawn(tmp_path, rpc):
    listing, request, _ = rpc
    with pytest.raises(CoreAiHeadError, match="unsupported"):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind="kilo", model="x/y")
    listing.assert_awaited_once()
    listing.side_effect = CoreClientError("offline")
    with pytest.raises(CoreClientError, match="offline"):
        await ensure_core_aihead(backend="core", cwd=tmp_path, kind="pi", model="x/y")
    request.assert_not_awaited()
