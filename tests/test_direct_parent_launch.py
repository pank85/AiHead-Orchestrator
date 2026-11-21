"""Offline transport captures for direct-parent launch and monitor handoff."""
import json
import shlex
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from herdr_master.herdr.client import HerdrClient
from herdr_master.herdr.socket import HerdrRPCError
from herdr_master.pipeline import parent_contract as contract
from herdr_master.pipeline.child_watch import ChildIdentity, read_herdr_children

PARENT = "w2Q:pE"
CHILD = "w3H:pZ"
SOCKET = Path("/tmp/test-launch.sock")


def test_cli_prints_runnable_verify_with_exact_full_report(tmp_path, monkeypatch, capsys):
    from herdr_master.cli.main import _print_child_watch_dir

    run_dir = tmp_path / "watch directory's name"
    _print_child_watch_dir({"child_watch_run_dir": run_dir})
    output = capsys.readouterr().out
    assert "exact full received payload" in output
    assert "including the HERDR TERMINAL REPORT header" in output
    report = "HERDR TERMINAL REPORT — report-123\nCoder's result: $safe\nsecond line\n"
    command = output.splitlines()[-1].replace(
        "\"<id from incoming report>\"", shlex.quote("report-123")
    ).replace("'<exact full received HERDR TERMINAL REPORT payload>'", shlex.quote(report))
    args = shlex.split(command)
    assert args[1:4] == ["-m", "herdr_master.pipeline.parent_contract", "verify"]
    verify = Mock(return_value=CHILD)
    monkeypatch.setattr(contract, "verify_terminal_completion", verify)
    assert contract.main(args[3:]) == 0
    verify.assert_called_once_with(run_dir, "report-123", report)


def agent(pane, *, terminal="terminal", status="idle", session=None):
    workspace, ordinal = pane.split(":p")
    record = {"pane_id": pane, "workspace_id": workspace,
              "tab_id": f"{workspace}:t{ordinal}", "agent": "pi",
              "agent_status": status, "terminal_id": terminal}
    if session is not None:
        record["agent_session"] = {"value": session}
    return {"agent": record}


def fake_client():
    client = Mock()
    client.get_agent = AsyncMock(side_effect=agent)
    client.start_agent = AsyncMock(return_value={"type": "agent_started"})
    client.prompt_agent = AsyncMock()
    return client


async def launch(client, *, parent=PARENT, delegated=True, tab=None):
    return await contract.launch_and_prompt(
        client, pane_id=CHILD, tab_id=tab, socket_path=SOCKET,
        parent_pane_id=parent, delegated=delegated, name="worker", kind="pi",
        args=["--thinking", "high"], cwd="/tmp", prompt="Persona\n\nReport final_plan.md",
    )


@pytest.fixture
def watch(monkeypatch, tmp_path):
    monkeypatch.setattr(contract, "_watch_run_dir", lambda *_: tmp_path)
    start = Mock()
    monkeypatch.setattr(contract.subprocess, "Popen", start)
    monkeypatch.setattr("herdr_master.pipeline.child_watch.watcher_ready", lambda *a, **kw: True)
    return start


@pytest.mark.asyncio
async def test_confirmed_cross_workspace_child_uses_actual_tab_and_first_prompt(tmp_path, watch):
    client = fake_client()
    assert await launch(client) == tmp_path
    target, first_prompt = client.prompt_agent.await_args.args
    assert target == CHILD
    assert first_prompt.startswith(f"DIRECT PARENT CONTRACT — your immediate supervisor is pane {PARENT}.")
    assert f'herdr agent prompt {PARENT} "<message>"' in first_prompt
    assert "Never contact any other pane" in first_prompt
    assert first_prompt.endswith("Persona\n\nReport final_plan.md")
    assert client.prompt_agent.await_args.kwargs == {"max_attempts": 1}
    assert client.start_agent.await_args.kwargs["recover_name_collision"] is False
    fields = (tmp_path / "children.tsv").read_text().rstrip("\n").split("\t")
    assert fields[2] == "w3H:tZ"
    assert fields[9] == "launcher-completion-v1"
    command = next(line for line in first_prompt.splitlines() if "--launch-token" in line)
    args = shlex.split(command)
    token = args[args.index("--launch-token") + 1]
    assert token not in fields and (tmp_path / f"launch-{token}.json").exists()
    [child] = read_herdr_children(tmp_path)
    assert child.identity.target == CHILD
    assert child.identity.generation == 'herdr-v1:["terminal",null]'
    watch.assert_called_once()
    argv, = watch.call_args.args
    assert argv[1:5] == ["-m", "herdr_master.pipeline.child_watch", "--run-dir", str(tmp_path)]
    assert argv[5:9] == ["--parent-pane", PARENT, "--parent-generation", 'herdr-v1:["terminal",null]']
    assert argv[-2:] == ["--startup-timeout", "5"]
    assert watch.call_args.kwargs["env"]["HERDR_SOCKET_PATH"] == str(SOCKET)
    assert watch.call_args.kwargs["env"]["HERDR_SOCKET"] == str(SOCKET)
    assert watch.call_args.kwargs["start_new_session"] is True


@pytest.mark.asyncio
async def test_completed_turn_is_live_but_exited_parent_fails_closed(watch):
    client = fake_client()
    client.get_agent.side_effect = lambda pane: agent(pane, status="exited" if pane == PARENT else "idle")
    with pytest.raises(contract.ParentRouteError, match="verify the current direct-parent"):
        await launch(client)
    client.start_agent.assert_not_awaited()
    watch.assert_not_called()

    client.get_agent.side_effect = lambda pane: agent(pane, status="done" if pane == PARENT else "idle")
    await launch(client)
    client.start_agent.assert_awaited_once()
    client.prompt_agent.assert_awaited_once()
    watch.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("parent", [None, "bad; echo nope", "w2Q:pA"])
async def test_unverified_delegated_launch_never_starts(parent, watch):
    client = fake_client()
    client.get_agent.side_effect = None
    client.get_agent.return_value = agent(PARENT)
    with pytest.raises(contract.ParentRouteError):
        await launch(client, parent=parent)
    client.start_agent.assert_not_awaited()
    client.prompt_agent.assert_not_awaited()
    watch.assert_not_called()


@pytest.mark.asyncio
async def test_root_ignores_inherited_parent_and_never_starts_watcher(monkeypatch, watch):
    monkeypatch.setenv("HERDR_PARENT_PANE_ID", PARENT)
    client = fake_client()
    assert await launch(client, parent=None, delegated=False) is None
    prompt = client.prompt_agent.await_args.args[1]
    assert prompt.startswith("ROOT ROUTING CONTRACT")
    assert "Ask the user directly" in prompt and PARENT not in prompt
    watch.assert_not_called()


@pytest.mark.asyncio
async def test_ambiguous_start_is_not_replayed_or_registered(tmp_path, watch):
    client = fake_client()
    client.start_agent.side_effect = TimeoutError("unknown outcome")
    with pytest.raises(TimeoutError, match="unknown outcome"):
        await launch(client)
    client.start_agent.assert_awaited_once()
    client.prompt_agent.assert_not_awaited()
    watch.assert_not_called()
    assert read_herdr_children(tmp_path) == []
    assert '"phase": "start"' in (tmp_path / "launch-uncertainty.jsonl").read_text()


@pytest.mark.asyncio
async def test_ambiguous_prompt_is_never_replayed(tmp_path, watch):
    client = fake_client()
    client.prompt_agent.side_effect = TimeoutError("unknown delivery")
    with pytest.raises(TimeoutError, match="unknown delivery"):
        await launch(client)
    client.start_agent.assert_awaited_once()
    client.prompt_agent.assert_awaited_once()
    assert len(read_herdr_children(tmp_path)) == 1
    assert '"phase": "first_prompt"' in (tmp_path / "launch-uncertainty.jsonl").read_text()
    watch.assert_called_once()


@pytest.mark.asyncio
async def test_replaced_parent_prevents_first_task_prompt(watch):
    client = fake_client()
    client.get_agent.side_effect = [agent(PARENT), agent(CHILD), agent(PARENT, terminal="replaced")]
    with pytest.raises(contract.ParentRouteError, match="changed during startup"):
        await launch(client)
    client.prompt_agent.assert_not_awaited()


GENERATION = 'herdr-v1:["terminal",null]'


def accepted(path, *, parent=PARENT, child=CHILD, child_generation=GENERATION):
    return contract.completion_receipt_accepted(
        path, parent_pane_id=parent, parent_generation=GENERATION,
        child_pane_id=child, child_generation=child_generation,
    )


@pytest.fixture
def completion(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("HERDR_PANE_ID", CHILD)
    monkeypatch.setenv("HERDR_PARENT_PANE_ID", "wrong:p1")
    token = contract._register_child(
        tmp_path, CHILD, "worker", "w3H:tZ", GENERATION,
        parent=PARENT, parent_generation=GENERATION, socket_path=SOCKET,
    )

    def herdr(endpoint, verb, pane, *_):
        assert endpoint == str(SOCKET)
        return {**agent(pane), "type": "agent_info" if verb == "get" else "agent_prompted"}

    rpc = Mock(side_effect=herdr)
    monkeypatch.setattr(contract, "_herdr_completion_rpc", rpc)
    return token, rpc


def test_completion_requires_accepted_herdr_send_not_artifact_or_ledger_status(tmp_path, completion):
    token, rpc = completion
    (tmp_path / "summary.md").write_text("Finished, I sent the message.")
    ledger = tmp_path / "children.tsv"
    ledger.write_text(ledger.read_text().replace("\trunning\t", "\tcompleted\t"))
    parent = ChildIdentity("herdr", PARENT, GENERATION)
    assert not accepted(tmp_path)
    assert not read_herdr_children(tmp_path, parent=parent)[0].acknowledged

    message = "Passed tests; artifact summary.md"
    report_id = contract.send_terminal_completion(tmp_path, token, message)
    assert rpc.call_args_list[-1].args == (
        str(SOCKET), "prompt", PARENT, f"HERDR TERMINAL REPORT {report_id} from {CHILD}\n\n{message}",
    )
    assert accepted(tmp_path)
    assert read_herdr_children(tmp_path, parent=parent)[0].completion_accepted
    assert not read_herdr_children(tmp_path, parent=parent)[0].acknowledged
    assert not accepted(tmp_path, parent="w2Q:pA")
    assert not accepted(tmp_path, child_generation='herdr-v1:["replaced",null]')
    assert contract.send_terminal_completion(tmp_path, token, message) == report_id
    assert rpc.call_count == 3  # One child check, one parent check, one send; no replay.

    receipt = tmp_path / f"receipt-{report_id}.json"
    data = json.loads(receipt.read_text())
    data["parent_pane_id"] = "w2Q:pA"
    receipt.write_text(json.dumps(data))
    assert not accepted(tmp_path)


@pytest.mark.parametrize("failure", ["timeout", "wrong-target", "wrong-generation", "malformed"])
def test_rejected_or_uncertain_send_withholds_receipt_and_does_not_retry(tmp_path, completion, failure):
    token, rpc = completion
    actual = rpc.side_effect

    def result(endpoint, verb, pane, *args):
        if verb != "prompt":
            return actual(endpoint, verb, pane, *args)
        if failure == "timeout":
            raise TimeoutError("acceptance unknown")
        if failure == "wrong-target":
            return {**agent("w2Q:pA"), "type": "agent_prompted"}
        if failure == "wrong-generation":
            return {**agent(pane, terminal="replaced"), "type": "agent_prompted"}
        return {"type": "other"}

    rpc.side_effect = result
    with pytest.raises((TimeoutError, RuntimeError)):
        contract.send_terminal_completion(tmp_path, token, "Complete")
    assert not accepted(tmp_path)
    with pytest.raises(RuntimeError, match="do not replay"):
        contract.send_terminal_completion(tmp_path, token, "Complete")
    assert sum(call.args[1] == "prompt" for call in rpc.call_args_list) == 1


def test_completion_with_wrong_caller_or_replaced_child_never_prompts(tmp_path, monkeypatch, completion):
    token, rpc = completion
    monkeypatch.setenv("HERDR_PANE_ID", PARENT)
    with pytest.raises(contract.ParentRouteError, match="exact child"):
        contract.send_terminal_completion(tmp_path, token, "Complete")
    rpc.assert_not_called()
    monkeypatch.setenv("HERDR_PANE_ID", CHILD)
    rpc.side_effect = None
    rpc.return_value = {**agent(CHILD, terminal="replaced"), "type": "agent_info"}
    with pytest.raises(RuntimeError, match="exact expected agent_info"):
        contract.send_terminal_completion(tmp_path, token, "Complete")
    assert rpc.call_args.args == (str(SOCKET), "get", CHILD)
    assert not accepted(tmp_path)


def test_parent_verify_withholds_done_until_acceptance(tmp_path, completion, monkeypatch, capsys):
    token, rpc = completion
    scope = contract._completion_scope(PARENT, GENERATION, CHILD, GENERATION)
    scope["assignment_id"] = contract._current_assignment(tmp_path, scope)
    report_id = contract._report_id(scope)
    payload = f"HERDR TERMINAL REPORT {report_id} from {CHILD}\n\nSucceeded"
    argv = ["verify", "--run-dir", str(tmp_path), "--report-id", report_id, "--message", payload]
    monkeypatch.setenv("HERDR_PANE_ID", PARENT)
    assert contract.main(argv) == 1
    rpc.assert_not_called()
    assert "Completion not confirmed" in capsys.readouterr().err

    monkeypatch.setenv("HERDR_PANE_ID", CHILD)
    assert contract.send_terminal_completion(tmp_path, token, "Succeeded") == report_id
    assert contract.main(argv) == 1  # Child cannot verify for its parent.
    monkeypatch.setenv("HERDR_PANE_ID", PARENT)
    assert contract.main(argv) == 0
    assert "Verified Herdr terminal report acceptance" in capsys.readouterr().out
    rpc.assert_called_with(str(SOCKET), "get", PARENT)
    (tmp_path / "children.tsv").unlink()
    assert contract.main(argv) == 1


def test_completion_allows_late_session_discovery_on_same_terminal(tmp_path, completion):
    token, rpc = completion
    rpc.side_effect = lambda endpoint, verb, pane, *_: {
        **agent(pane, session=f"session-{pane}"),
        "type": "agent_info" if verb == "get" else "agent_prompted",
    }
    contract.send_terminal_completion(tmp_path, token, "Succeeded")
    assert accepted(tmp_path)


def test_cli_completion_invokes_real_herdr_agent_prompt_shape(tmp_path, monkeypatch, capsys):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("HERDR_PANE_ID", CHILD)
    token = contract._register_child(
        tmp_path, CHILD, "worker", "w3H:tZ", GENERATION,
        parent=PARENT, parent_generation=GENERATION, socket_path=SOCKET,
    )
    # Exercise CLI extraction and actual argv construction without a live send.

    def run(argv, **kwargs):
        assert argv[:2] == ["herdr", "agent"]
        assert kwargs["env"]["HERDR_SOCKET_PATH"] == str(SOCKET)
        verb, pane = argv[2:4]
        return Mock(returncode=0, stdout=json.dumps({"result": {
            **agent(pane), "type": "agent_info" if verb == "get" else "agent_prompted",
        }}))

    proc = Mock(side_effect=run)
    monkeypatch.setattr(contract.subprocess, "run", proc)
    assert contract.main(["complete", "--run-dir", str(tmp_path), "--launch-token", token,
                          "--message", "Outcome; # sent literally"]) == 0
    assert proc.call_args.args[0][:4] == ["herdr", "agent", "prompt", PARENT]
    assert proc.call_args.args[0][4].endswith("Outcome; # sent literally")
    assert "Herdr accepted the terminal report" in capsys.readouterr().out
    assert accepted(tmp_path)


@pytest.mark.asyncio
async def test_rpc_collision_opt_out_does_not_clear_authority(monkeypatch):
    client = HerdrClient(socket_path=SOCKET)
    collision = HerdrRPCError("agent_name_taken", f"already owned by pane_id={PARENT}")
    rpc = AsyncMock(side_effect=collision)
    monkeypatch.setattr(client, "_rpc", rpc)
    with pytest.raises(HerdrRPCError, match="agent_name_taken"):
        await client.start_agent("worker", "pi", CHILD, recover_name_collision=False)
    rpc.assert_awaited_once()
    assert rpc.await_args.args[0] == "agent.start"
