import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from herdr_master.pipeline import child_watch as cw
from herdr_master.pipeline import parent_contract
from herdr_master.pipeline.child_watch import (
    ChildEvent,
    ChildIdentity,
    ChildRecord,
    ChildStatus,
    HerdrAdapter,
    read_herdr_children,
    watch_children,
)

NATIVE_PARENT = ChildIdentity("native", "invoker-7")
NATIVE_CHILD = ChildRecord(ChildIdentity("native", "run-42"), "coder")
PARENT = ChildIdentity("herdr", "w3H:p2", "term_parent")
CHILD = ChildRecord(ChildIdentity("herdr", "w3H:p3", "term_child"), "scout")
MISSING = (1, {"error": {"code": "pane_not_found"}})


class Tick:
    def __init__(self, count=1):
        self.limit = count
        self.now = 0
        self.delays = []

    def clock(self):
        return self.now

    async def sleep(self, stop, delay):
        await asyncio.sleep(0)
        if len(self.delays) == self.limit:
            stop.set()
        else:
            self.delays.append(delay)
            self.now += delay

    @property
    def kwargs(self):
        return {"_clock": self.clock, "_sleep": self.sleep}


def pane(identity, *, terminal=None, session="session-1", status="working", **changes):
    content = {
        "pane_id": identity.target,
        "workspace_id": identity.workspace,
        "terminal_id": terminal or identity.generation,
        "agent_session": {"value": session} if session is not None else None,
        "agent_status": status,
        "agent": "pi",
    }
    return content | changes


def info(identity, **kwargs):
    return 0, {"result": {"type": "pane_info", "pane": pane(identity, **kwargs)}}


async def test_tick_cadence_and_nonblocking_wait():
    tick = Tick(3)
    moments = []
    stop = asyncio.Event()
    task = asyncio.create_task(
        watch_children(
            parent=NATIVE_PARENT,
            read_children=lambda: [NATIVE_CHILD],
            probe_child=lambda _: moments.append(tick.now) or ChildStatus.ALIVE,
            notify_parent=AsyncMock(side_effect=AssertionError("live child reported")),
            stop=stop,
            **tick.kwargs,
        )
    )
    await asyncio.sleep(0)  # The watcher can wait while its owner continues.
    assert not task.done()
    assert moments == []
    await task
    assert moments == [600, 1200, 1800]
    assert tick.delays == [600, 600, 600]


@pytest.mark.parametrize("status", [ChildStatus.ABSENT, ChildStatus.EXITED, ChildStatus.REPLACED])
async def test_terminal_report_and_stop_when_all_children_terminal(status):
    tick = Tick(10)
    notify = AsyncMock()
    await watch_children(
        parent=NATIVE_PARENT,
        read_children=lambda: [NATIVE_CHILD],
        probe_child=lambda _: status,
        notify_parent=notify,
        stop=asyncio.Event(),
        **tick.kwargs,
    )
    notify.assert_awaited_once_with(NATIVE_PARENT, ChildEvent(NATIVE_CHILD, status))
    assert tick.delays == [600]


@pytest.mark.parametrize("response", [ChildStatus.ALIVE, ChildStatus.UNKNOWN, None, "exited"])
async def test_nonaffirmative_status_never_reports(response):
    notify = AsyncMock()
    await watch_children(
        parent=NATIVE_PARENT,
        read_children=lambda: [NATIVE_CHILD],
        probe_child=AsyncMock(return_value=response),
        notify_parent=notify,
        stop=asyncio.Event(),
        **Tick(2).kwargs,
    )
    notify.assert_not_called()


async def test_transient_probe_error_is_unknown():
    notify = AsyncMock()
    probe = AsyncMock(side_effect=[TimeoutError("no reply"), ChildStatus.EXITED])
    await watch_children(
        parent=NATIVE_PARENT,
        read_children=lambda: [NATIVE_CHILD],
        probe_child=probe,
        notify_parent=notify,
        stop=asyncio.Event(),
        **Tick(2).kwargs,
    )
    assert probe.await_count == 2
    notify.assert_awaited_once_with(NATIVE_PARENT, ChildEvent(NATIVE_CHILD, ChildStatus.EXITED))


async def test_acknowledgement_race_and_already_acknowledged():
    ledger = [NATIVE_CHILD]
    acknowledged = ChildRecord(NATIVE_CHILD.identity, "coder", "done", True)
    notify = AsyncMock()

    async def probe(_):
        ledger[:] = [acknowledged]
        return ChildStatus.EXITED

    await watch_children(
        parent=NATIVE_PARENT,
        read_children=lambda: ledger,
        probe_child=probe,
        notify_parent=notify,
        stop=asyncio.Event(),
        **Tick().kwargs,
    )
    second_probe = AsyncMock()
    await watch_children(
        parent=NATIVE_PARENT,
        read_children=lambda: ledger,
        probe_child=second_probe,
        notify_parent=notify,
        stop=asyncio.Event(),
        **Tick().kwargs,
    )
    second_probe.assert_not_called()
    notify.assert_not_called()


@pytest.mark.parametrize("failure", [None, TimeoutError("accepted or lost")])
async def test_durable_at_most_once_on_second_tick_and_restart(tmp_path, failure, caplog):
    other = ChildRecord(ChildIdentity("native", "run-other"), "worker")
    probe = AsyncMock(
        side_effect=lambda who: (
            ChildStatus.ABSENT if who == NATIVE_CHILD.identity else ChildStatus.ALIVE
        )
    )
    notify = AsyncMock(side_effect=failure)
    kwargs = dict(
        parent=NATIVE_PARENT,
        read_children=lambda: [NATIVE_CHILD, other],
        probe_child=probe,
        notify_parent=notify,
        state_dir=tmp_path,
    )
    for _ in range(2):
        await watch_children(**kwargs, stop=asyncio.Event(), **Tick(2).kwargs)
    notify.assert_awaited_once_with(NATIVE_PARENT, ChildEvent(NATIVE_CHILD, ChildStatus.ABSENT))
    assert sum(c.args[0] == NATIVE_CHILD.identity for c in probe.await_args_list) == 1
    assert not (tmp_path / "child-watch.pid").exists()
    state = json.loads((tmp_path / "child-watch-state.json").read_text())
    assert list(state["events"].values()) == ["uncertain" if failure else "delivered"]
    if failure:
        assert "delivery uncertain" in caplog.text


async def test_cancellation_during_delivery_retains_reservation(tmp_path):
    entered = asyncio.Event()

    async def notify(*_):
        entered.set()
        await asyncio.Event().wait()

    kwargs = dict(
        parent=NATIVE_PARENT,
        read_children=lambda: [NATIVE_CHILD],
        probe_child=lambda _: ChildStatus.EXITED,
        state_dir=tmp_path,
    )
    task = asyncio.create_task(
        watch_children(**kwargs, notify_parent=notify, stop=asyncio.Event(), **Tick().kwargs)
    )
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    second = AsyncMock()
    await watch_children(**kwargs, notify_parent=second, stop=asyncio.Event(), **Tick().kwargs)
    second.assert_not_called()
    assert not (tmp_path / "child-watch.pid").exists()


async def test_parent_replaced_during_probe_prevents_notification(tmp_path):
    probe_parent = AsyncMock(side_effect=[ChildStatus.ALIVE, ChildStatus.REPLACED])
    notify = AsyncMock()
    await watch_children(
        parent=PARENT,
        read_children=lambda: [CHILD],
        probe_child=lambda _: ChildStatus.ABSENT,
        notify_parent=notify,
        stop=asyncio.Event(),
        probe_parent=probe_parent,
        state_dir=tmp_path,
        **Tick(5).kwargs,
    )
    notify.assert_not_called()
    assert probe_parent.await_count == 2


async def test_parent_transient_status_does_not_notify_or_stop(tmp_path):
    parent_probe = AsyncMock(
        side_effect=[ChildStatus.UNKNOWN, ChildStatus.ALIVE, ChildStatus.ALIVE]
    )
    notify = AsyncMock()
    await watch_children(
        parent=PARENT,
        read_children=lambda: [CHILD],
        probe_child=lambda _: ChildStatus.EXITED,
        notify_parent=notify,
        stop=asyncio.Event(),
        probe_parent=parent_probe,
        state_dir=tmp_path,
        **Tick(2).kwargs,
    )
    assert parent_probe.await_count == 3
    notify.assert_awaited_once_with(PARENT, ChildEvent(CHILD, ChildStatus.EXITED))


async def test_one_watcher_per_run_and_clean_stop(tmp_path):
    stop, entered = asyncio.Event(), asyncio.Event()
    notify = AsyncMock()

    async def sleep(event, _delay):
        entered.set()
        await event.wait()

    kwargs = dict(
        parent=NATIVE_PARENT,
        read_children=lambda: [NATIVE_CHILD],
        probe_child=lambda _: ChildStatus.EXITED,
        notify_parent=notify,
        state_dir=tmp_path,
    )
    first = asyncio.create_task(watch_children(**kwargs, stop=stop, _sleep=sleep))
    await asyncio.wait_for(entered.wait(), 1)
    pid_path = tmp_path / "child-watch.pid"
    assert pid_path.read_text().strip().isdigit()
    await watch_children(**kwargs, stop=asyncio.Event(), **Tick().kwargs)
    notify.assert_not_called()
    assert pid_path.exists()
    stop.set()
    await asyncio.wait_for(first, 1)
    assert not pid_path.exists()
    await watch_children(**kwargs, stop=asyncio.Event(), **Tick().kwargs)
    notify.assert_awaited_once()


def row(pane_id="w3H:p3", role="scout", status="working", generation=None, tab="w3H:t1"):
    cells = [pane_id, role, tab, "develop", "/repo", "/run", status, "2026-11-20T17:20:00Z"]
    return "\t".join(cells if generation is None else [*cells, generation]) + "\n"


def launcher_row(status="working", generation=CHILD.identity.generation):
    return row(status=status, generation=generation).rstrip("\n") + "\tlauncher-completion-v1\n"


@pytest.fixture
def completion_validator(monkeypatch):
    checker = Mock(return_value=False)
    monkeypatch.setattr(parent_contract, "completion_receipt_accepted", checker, raising=False)
    monkeypatch.setattr(parent_contract, "current_assignment_epoch", Mock(return_value="a" * 64))
    return checker


@pytest.mark.parametrize("status", ["acknowledged", "completed", "closed", "integrated"])
async def test_launcher_rows_do_not_trust_terminal_status_or_artifacts(
    tmp_path, completion_validator, status
):
    ledger = tmp_path / "children.tsv"
    ledger.write_text(launcher_row(status))
    (tmp_path / "scout_summary.md").write_text("All work is finished and all tests passed.")
    notify = AsyncMock()
    await watch_children(
        parent=PARENT,
        read_children=lambda: read_herdr_children(tmp_path, parent=PARENT),
        probe_child=lambda _: ChildStatus.ABSENT,
        probe_parent=lambda _: ChildStatus.ALIVE,
        notify_parent=notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    notify.assert_awaited_once_with(PARENT, ChildEvent(CHILD, ChildStatus.ABSENT))
    assert all(call.args == (tmp_path,) for call in completion_validator.call_args_list)
    assert all(
        call.kwargs
        == {
            "parent_pane_id": PARENT.target,
            "parent_generation": PARENT.generation,
            "child_pane_id": CHILD.identity.target,
            "child_generation": CHILD.identity.generation,
        }
        for call in completion_validator.call_args_list
    )


async def test_shell_wires_bound_parent_into_launcher_receipt_check(
    monkeypatch, tmp_path, completion_validator
):
    (tmp_path / "children.tsv").write_text(launcher_row())
    bound = ChildIdentity("herdr", PARENT.target, 'herdr-v1:["term_parent","session-1"]')
    adapter = Mock()
    adapter.bind_parent = AsyncMock(return_value=bound)
    monkeypatch.setattr(cw, "HerdrAdapter", lambda: adapter)
    completion_validator.return_value = True

    async def watch(**kwargs):
        assert kwargs["parent"] == bound
        received = kwargs["read_children"]()
        assert received == [
            ChildRecord(
                CHILD.identity,
                CHILD.role,
                completion_accepted=True,
                tagged=True,
                assignment_epoch="a" * 64,
            )
        ]

    monkeypatch.setattr(cw, "watch_children", watch)
    await cw._run_shell(tmp_path, PARENT.target, PARENT.generation)
    adapter.bind_parent.assert_awaited_once_with(PARENT)
    completion_validator.assert_called_once_with(
        tmp_path,
        parent_pane_id=bound.target,
        parent_generation=bound.generation,
        child_pane_id=CHILD.identity.target,
        child_generation=CHILD.identity.generation,
    )


async def test_launcher_receipt_arrives_after_probe_before_notification(
    tmp_path, completion_validator
):
    (tmp_path / "children.tsv").write_text(launcher_row())
    notify = AsyncMock()
    tick = Tick(5)

    async def probe_child(identity):
        assert identity == CHILD.identity
        completion_validator.return_value = True
        return ChildStatus.ABSENT

    await watch_children(
        parent=PARENT,
        read_children=lambda: read_herdr_children(tmp_path, parent=PARENT),
        probe_child=probe_child,
        probe_parent=lambda _: ChildStatus.ALIVE,
        notify_parent=notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **tick.kwargs,
    )
    notify.assert_not_called()
    assert tick.delays == [600, 600, 600, 600, 600]
    record = read_herdr_children(tmp_path, parent=PARENT)[0]
    assert record.completion_accepted and not record.acknowledged
    (tmp_path / "children.tsv").write_text(launcher_row(status="reconciled"))
    record = read_herdr_children(tmp_path, parent=PARENT)[0]
    assert record.completion_accepted and record.acknowledged


@pytest.mark.parametrize("accepted", [True, False])
async def test_launcher_writer_and_watcher_real_receipt_integration(
    tmp_path, monkeypatch, accepted
):
    owner_pane = pane(PARENT, tab_id="w3H:t2")
    child_pane = pane(CHILD.identity, tab_id="w3H:t3")
    parent_generation = 'herdr-v1:["term_parent","session-1"]'
    child_generation = 'herdr-v1:["term_child","session-1"]'
    parent = ChildIdentity("herdr", PARENT.target, parent_generation)
    child = ChildIdentity("herdr", CHILD.identity.target, child_generation)
    endpoint = tmp_path / "herdr.sock"
    token = parent_contract._register_child(
        tmp_path,
        child.target,
        CHILD.role,
        child_pane["tab_id"],
        child_generation,
        parent=parent.target,
        parent_generation=parent_generation,
        socket_path=endpoint,
    )
    ledger = (tmp_path / "children.tsv").read_text()
    assert token not in ledger
    assert ledger.rstrip("\n").split("\t")[-1] == "launcher-completion-v1"
    assert not read_herdr_children(tmp_path, parent=parent)[0].acknowledged
    monkeypatch.setenv("HERDR_PANE_ID", child.target)
    prompt_result = owner_pane if accepted else {**owner_pane, "terminal_id": "term_replaced"}
    transport = Mock(
        side_effect=[
            {"type": "agent_info", "agent": child_pane},
            {"type": "agent_info", "agent": owner_pane},
            {"type": "agent_prompted", "agent": prompt_result},
        ]
    )
    monkeypatch.setattr(parent_contract, "_herdr_completion_rpc", transport)
    message = "verified failure outcome; artifact /tmp/child-summary.md"
    if accepted:
        report_id = parent_contract.send_terminal_completion(tmp_path, token, message)
        assert (tmp_path / f"receipt-{report_id}.json").is_file()
        other = ChildIdentity("herdr", parent.target, 'herdr-v1:["term_replaced","session-1"]')
        assert not read_herdr_children(tmp_path, parent=other)[0].acknowledged
    else:
        with pytest.raises(RuntimeError, match="exact expected agent_prompted identity"):
            parent_contract.send_terminal_completion(tmp_path, token, message)
        assert not list(tmp_path.glob("receipt-*.json"))
    assert transport.call_count == 3
    assert transport.call_args.args[:3] == (str(endpoint), "prompt", parent.target)
    assert message in transport.call_args.args[-1]

    probe, notify = AsyncMock(return_value=ChildStatus.ABSENT), AsyncMock()
    await watch_children(
        parent=parent,
        read_children=lambda: read_herdr_children(tmp_path, parent=parent),
        probe_parent=lambda _: ChildStatus.ALIVE,
        probe_child=probe,
        notify_parent=notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    if accepted:
        probe.assert_not_called()
        notify.assert_not_called()
    else:
        probe.assert_awaited_once_with(child)
        notify.assert_awaited_once_with(
            parent, ChildEvent(ChildRecord(child, CHILD.role), ChildStatus.ABSENT)
        )


async def test_real_assignment_epoch_invalidates_prior_receipt_and_reconciliation(
    tmp_path, monkeypatch
):
    owner_pane = pane(PARENT, tab_id="w3H:t2")
    child_pane = pane(CHILD.identity, tab_id="w3H:t3")
    parent = ChildIdentity("herdr", PARENT.target, 'herdr-v1:["term_parent","session-1"]')
    child = ChildIdentity("herdr", CHILD.identity.target, 'herdr-v1:["term_child","session-1"]')
    token = parent_contract._register_child(
        tmp_path,
        child.target,
        CHILD.role,
        "w3H:t3",
        child.generation,
        parent=parent.target,
        parent_generation=parent.generation,
        socket_path=tmp_path / "herdr.sock",
    )

    def rpc(endpoint, operation, target, *args):
        agent = owner_pane if target == parent.target else child_pane
        return {"type": "agent_prompted" if operation == "prompt" else "agent_info", "agent": agent}

    monkeypatch.setattr(parent_contract, "_herdr_completion_rpc", rpc)
    first_notify = AsyncMock()
    await watch_children(
        parent=parent,
        read_children=lambda: read_herdr_children(tmp_path, parent=parent),
        probe_parent=lambda _: ChildStatus.ALIVE,
        probe_child=lambda _: ChildStatus.ABSENT,
        notify_parent=first_notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    first_notify.assert_awaited_once()
    monkeypatch.setenv("HERDR_PANE_ID", child.target)
    parent_contract.send_terminal_completion(tmp_path, token, "first result")
    record = read_herdr_children(tmp_path, parent=parent)[0]
    assert record.completion_accepted and not record.acknowledged
    ledger = tmp_path / "children.tsv"
    ledger.write_text(ledger.read_text().replace("\trunning\t", "\treconciled\t"))
    assert read_herdr_children(tmp_path, parent=parent)[0].acknowledged

    monkeypatch.setenv("HERDR_PANE_ID", parent.target)
    next_token = parent_contract.assign_child(tmp_path, token)
    record = read_herdr_children(tmp_path, parent=parent)[0]
    assert not record.completion_accepted and not record.acknowledged
    assert "\trunning\t" in ledger.read_text()
    notify = AsyncMock()
    await watch_children(
        parent=parent,
        read_children=lambda: read_herdr_children(tmp_path, parent=parent),
        probe_parent=lambda _: ChildStatus.ALIVE,
        probe_child=lambda _: ChildStatus.ABSENT,
        notify_parent=notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    notify.assert_awaited_once()
    monkeypatch.setenv("HERDR_PANE_ID", child.target)
    with pytest.raises(RuntimeError, match="stale assignment"):
        parent_contract.send_terminal_completion(tmp_path, token, "old result")
    parent_contract.send_terminal_completion(tmp_path, next_token, "second result")
    record = read_herdr_children(tmp_path, parent=parent)[0]
    assert record.completion_accepted and not record.acknowledged


async def test_missing_assignment_epoch_fails_closed(tmp_path, monkeypatch, completion_validator):
    (tmp_path / "children.tsv").write_text(launcher_row())
    monkeypatch.setattr(parent_contract, "current_assignment_epoch", Mock(return_value=None))
    probe, notify = AsyncMock(), AsyncMock()
    await watch_children(
        parent=PARENT,
        read_children=lambda: read_herdr_children(tmp_path, parent=PARENT),
        probe_parent=lambda _: ChildStatus.ALIVE,
        probe_child=probe,
        notify_parent=notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    probe.assert_not_awaited()
    notify.assert_not_awaited()
    completion_validator.assert_not_called()


def test_launcher_receipt_unavailable_or_ambiguous_is_unacknowledged(
    tmp_path, completion_validator, caplog
):
    (tmp_path / "children.tsv").write_text(launcher_row("completed"))
    completion_validator.side_effect = [OSError("unreadable"), "true"]
    for _ in range(2):
        assert read_herdr_children(tmp_path, parent=PARENT) == [
            ChildRecord(CHILD.identity, CHILD.role, tagged=True, assignment_epoch="a" * 64)
        ]
    assert "cannot verify child completion receipt" in caplog.text
    completion_validator.reset_mock()
    assert read_herdr_children(tmp_path) == [ChildRecord(CHILD.identity, CHILD.role, tagged=True)]
    completion_validator.assert_not_called()


@pytest.mark.parametrize(
    ("generation", "tag"),
    [
        ("", "launcher-completion-v1"),
        ("term_child", "a" * 64),
        ("term_child", "launcher-completion-v2"),
    ],
)
def test_malformed_launcher_row_cannot_bypass_receipt(tmp_path, generation, tag):
    tagged = row(status="completed", generation=generation).rstrip("\n") + f"\t{tag}\n"
    (tmp_path / "children.tsv").write_text(tagged)
    with pytest.raises(ValueError, match="invalid launcher completion row tag or generation"):
        read_herdr_children(tmp_path, parent=PARENT)


def test_existing_tsv_optional_generation_and_terminal_rows(tmp_path):
    file = tmp_path / "children.tsv"
    content = (
        row()
        + row("w3H:p4", "architect", "completed", "term_2")
        + row("w3H:p5", "reviewer", "launch_ambiguous")
        + row("w3H:p6", status="closed")
    )
    file.write_text(content)
    assert read_herdr_children(tmp_path) == [
        ChildRecord(ChildIdentity("herdr", "w3H:p3"), "scout"),
        ChildRecord(ChildIdentity("herdr", "w3H:p4", "term_2"), "architect", "completed", True),
        ChildRecord(ChildIdentity("herdr", "w3H:p6"), "scout", "closed", True),
    ]
    assert file.read_text() == content


@pytest.mark.parametrize(
    "invalid", ["w3H:p4\tbroken\n", row("w8:p4"), row(), row("w3H:p4").rstrip()]
)
async def test_malformed_snapshot_does_not_probe_even_valid_rows(tmp_path, invalid):
    tsv = tmp_path / "children.tsv"
    contents = row() + invalid
    tsv.write_text(contents)
    probe, notify = AsyncMock(), AsyncMock()
    with pytest.raises(ValueError):
        read_herdr_children(tmp_path)
    await watch_children(
        parent=PARENT,
        read_children=lambda: read_herdr_children(tmp_path),
        probe_child=probe,
        notify_parent=notify,
        stop=asyncio.Event(),
        probe_parent=lambda _: ChildStatus.ALIVE,
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    probe.assert_not_called()
    notify.assert_not_called()
    assert tsv.read_text() == contents
    assert {p.name for p in tmp_path.iterdir()} == {
        "children.tsv",
        "child-watch.lock",
        "child-watch-ready.lock",
        "child-watch-state.json",
    }


@pytest.mark.parametrize(
    ("reply", "status"),
    [
        (MISSING, ChildStatus.ABSENT),
        (info(CHILD.identity, terminal="term_new"), ChildStatus.REPLACED),
        (info(CHILD.identity, exited=True), ChildStatus.EXITED),
        (info(CHILD.identity, status="working"), ChildStatus.ALIVE),
        (info(CHILD.identity, status="idle"), ChildStatus.ALIVE),
        (info(CHILD.identity, status="blocked"), ChildStatus.ALIVE),
        (info(CHILD.identity, status="done"), ChildStatus.ALIVE),
        (info(CHILD.identity, status="unknown"), ChildStatus.ALIVE),
        ((1, {"error": {"code": "server_not_running"}}), ChildStatus.UNKNOWN),
        ((1, {"error": {"code": "agent_not_found"}}), ChildStatus.UNKNOWN),
        ((0, {"result": {}}), ChildStatus.UNKNOWN),
        (info(CHILD.identity, workspace_id="other"), ChildStatus.UNKNOWN),
    ],
)
async def test_herdr_transport_exact_probe(reply, status):
    run = AsyncMock(return_value=reply)
    assert (await HerdrAdapter(run).probe_child(CHILD.identity)).status is status
    run.assert_awaited_once_with("pane", "get", "w3H:p3")


@pytest.mark.parametrize("recorded_generation", [None, "term_child", "session-1"])
async def test_child_pin_survives_restart_and_detects_session_replacement(
    tmp_path, recorded_generation
):
    child = ChildRecord(ChildIdentity("herdr", "w3H:p3", recorded_generation), "scout")
    notify = AsyncMock()
    kwargs = dict(
        parent=PARENT,
        read_children=lambda: [child],
        notify_parent=notify,
        probe_parent=lambda _: ChildStatus.ALIVE,
        state_dir=tmp_path,
    )
    for session in ["session-1", "session-2"]:
        adapter = HerdrAdapter(AsyncMock(return_value=info(CHILD.identity, session=session)))
        await watch_children(
            **kwargs, stop=asyncio.Event(), probe_child=adapter.probe_child, **Tick().kwargs
        )
    event = notify.await_args.args[1]
    assert notify.await_count == 1
    assert event.status is ChildStatus.REPLACED
    assert event.child.identity.generation == 'herdr-v1:["term_child","session-1"]'


async def test_explicit_composite_parent_pin_does_not_drift_across_restarts():
    parent = ChildIdentity("herdr", PARENT.target, 'herdr-v1:["term_parent",null]')
    adapter = HerdrAdapter(AsyncMock(return_value=info(PARENT)))
    assert await adapter.bind_parent(parent) == parent


async def test_shell_compatible_ledger_does_not_require_synthetic_heartbeat(tmp_path):
    ledger = tmp_path / "children.tsv"
    ledger.write_text("w3H:p3\tcoder\tw3H:t3\t\t\t\trunning\t\tterm_child\n")
    assert read_herdr_children(tmp_path) == [ChildRecord(CHILD.identity, "coder")]


async def test_without_queryable_native_adapter_refuses_to_start(tmp_path):
    with pytest.raises(ValueError, match="requires a queryable asynchronous status adapter"):
        await watch_children(
            parent=NATIVE_PARENT,
            read_children=lambda: [NATIVE_CHILD],
            probe_child=None,
            notify_parent=AsyncMock(),
            stop=asyncio.Event(),
            state_dir=tmp_path,
        )
    assert not list(tmp_path.iterdir())


async def test_herdr_notification_exact_target_and_only_one_write():
    result = (0, {"result": {"type": "agent_prompted", "agent": pane(PARENT)}})
    run = AsyncMock(side_effect=[info(PARENT), info(PARENT), result])
    adapter = HerdrAdapter(run)
    parent = await adapter.bind_parent(PARENT)
    assert parent.generation == 'herdr-v1:["term_parent","session-1"]'
    await adapter.notify_parent(parent, ChildEvent(CHILD, ChildStatus.ABSENT))
    calls = [entry.args for entry in run.await_args_list]
    assert [call[:3] for call in calls] == [
        ("pane", "get", PARENT.target),
        ("pane", "get", PARENT.target),
        ("agent", "prompt", PARENT.target),
    ]
    assert CHILD.identity.target in calls[-1][-1]


async def test_cross_workspace_child_notifies_only_pinned_parent(tmp_path):
    tsv = tmp_path / "children.tsv"
    tsv.write_text(row("w2Q:pE", tab="w2Q:tA", generation="term_cross"))
    child = read_herdr_children(tmp_path)[0]
    assert child.identity.workspace == "w2Q"
    result = (0, {"result": {"type": "agent_prompted", "agent": pane(PARENT)}})
    run = AsyncMock(side_effect=[info(PARENT), MISSING, info(PARENT), info(PARENT), result])
    adapter = HerdrAdapter(run)
    await watch_children(
        parent=PARENT,
        read_children=lambda: read_herdr_children(tmp_path),
        probe_parent=adapter.probe_parent,
        probe_child=adapter.probe_child,
        notify_parent=adapter.notify_parent,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    calls = [entry.args[:3] for entry in run.await_args_list]
    assert ("pane", "get", "w2Q:pE") in calls
    assert [call for call in calls if call[0] == "agent"] == [("agent", "prompt", PARENT.target)]


@pytest.mark.parametrize(
    ("pane_id", "tab_id"),
    [
        ("w2Q:p", "w2Q:tA"),
        ("w2Q:pE-", "w2Q:tA"),
        ("w2Q:tE", "w2Q:tA"),
        ("w2Q:pE", "w2Q:t"),
        ("w2Q:pE", "w2Q:tA-"),
        ("w2Q:pE", "w2Q:pA"),
    ],
)
def test_ledger_rejects_malformed_pane_or_tab_ordinal(tmp_path, pane_id, tab_id):
    (tmp_path / "children.tsv").write_text(row(pane_id, tab=tab_id))
    with pytest.raises(ValueError):
        read_herdr_children(tmp_path)


async def test_parent_cannot_be_its_own_registered_child(tmp_path):
    probe, notify = AsyncMock(), AsyncMock()
    (tmp_path / "children.tsv").write_text(row(PARENT.target))
    await watch_children(
        parent=PARENT,
        read_children=lambda: read_herdr_children(tmp_path),
        probe_parent=lambda _: ChildStatus.ALIVE,
        probe_child=probe,
        notify_parent=notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        **Tick().kwargs,
    )
    probe.assert_not_called()
    notify.assert_not_called()


async def test_pinned_parent_replaced_at_delivery_performs_no_write():
    run = AsyncMock(return_value=info(PARENT, terminal="term_other"))
    with pytest.raises(RuntimeError, match="pinned parent changed"):
        await HerdrAdapter(run).notify_parent(PARENT, ChildEvent(CHILD, ChildStatus.ABSENT))
    run.assert_awaited_once_with("pane", "get", PARENT.target)


async def test_restart_different_parent_fails_closed(tmp_path):
    kwargs = dict(
        read_children=lambda: [NATIVE_CHILD],
        probe_child=lambda _: ChildStatus.ALIVE,
        notify_parent=AsyncMock(),
        state_dir=tmp_path,
    )
    await watch_children(**kwargs, parent=NATIVE_PARENT, stop=asyncio.Event(), **Tick().kwargs)
    with pytest.raises(ValueError, match="different owning parent"):
        await watch_children(
            **kwargs,
            parent=ChildIdentity("native", "other-parent"),
            stop=asyncio.Event(),
            **Tick().kwargs,
        )


async def test_cli_sentinel_prevents_any_transport_calls(monkeypatch, tmp_path):
    adapter = AsyncMock(side_effect=AssertionError("transport must not be constructed"))
    monkeypatch.setattr(cw, "HerdrAdapter", adapter)
    sentinel = tmp_path / "child-watch.stop"
    sentinel.touch()
    for _ in range(2):
        await cw._run_shell(tmp_path, PARENT.target, PARENT.generation)
    assert list(tmp_path.iterdir()) == [sentinel]
    adapter.assert_not_called()


def test_cli_invalid_run_directory_fails_closed(tmp_path):
    tmp_path.chmod(0o755)
    assert cw.main(["--run-dir", str(tmp_path), "--parent-pane", PARENT.target]) == 1
    assert list(Path(tmp_path).iterdir()) == []


async def test_persistent_owner_covers_child_registered_after_terminal_snapshot(tmp_path):
    tick = Tick(3)
    records = [ChildRecord(CHILD.identity, CHILD.role, acknowledged=True)]
    other = ChildRecord(ChildIdentity("herdr", "w3H:p4", "term_other"), "coder")
    notify = AsyncMock()

    async def sleep(stop, delay):
        await tick.sleep(stop, delay)
        if len(tick.delays) == 2:
            records.append(other)

    await watch_children(
        parent=PARENT,
        read_children=lambda: records,
        probe_child=lambda _: ChildStatus.ABSENT,
        probe_parent=lambda _: ChildStatus.ALIVE,
        notify_parent=notify,
        stop=asyncio.Event(),
        state_dir=tmp_path,
        keep_alive=True,
        _clock=tick.clock,
        _sleep=sleep,
    )
    notify.assert_awaited_once_with(PARENT, ChildEvent(other, ChildStatus.ABSENT))
    assert len(tick.delays) == 3


async def test_ready_requires_matching_manifest_and_live_lock(tmp_path):
    run_id = "a" * 32
    manifest = cw._run_manifest(run_id, PARENT.target, PARENT.generation)
    (tmp_path / "launcher-run.json").write_text(json.dumps(manifest))

    def check():
        return cw.watcher_ready(
            tmp_path,
            run_id=run_id,
            parent_pane_id=PARENT.target,
            parent_generation=PARENT.generation,
        )

    stop = asyncio.Event()
    started = asyncio.Event()

    async def sleep(stop, delay):
        started.set()
        await stop.wait()

    task = asyncio.create_task(
        watch_children(
            parent=PARENT,
            read_children=lambda: [],
            probe_child=lambda _: ChildStatus.ALIVE,
            probe_parent=lambda _: ChildStatus.ALIVE,
            notify_parent=AsyncMock(),
            stop=stop,
            state_dir=tmp_path,
            keep_alive=True,
            _manifest=manifest,
            _sleep=sleep,
        )
    )
    await started.wait()
    assert check()
    assert not cw.watcher_ready(
        tmp_path, run_id="b" * 32, parent_pane_id=PARENT.target, parent_generation=PARENT.generation
    )
    stop.set()
    await task
    assert not check()


async def test_shell_retries_transient_bind_and_preserves_manifest_generation(
    monkeypatch, tmp_path
):
    run_id = "a" * 32
    manifest = cw._run_manifest(run_id, PARENT.target, PARENT.generation)
    (tmp_path / "launcher-run.json").write_text(json.dumps(manifest))
    adapter = Mock()
    adapter.bind_parent = AsyncMock(side_effect=[ValueError("transient"), PARENT])
    monkeypatch.setattr(cw, "HerdrAdapter", lambda: adapter)
    watch = AsyncMock()
    monkeypatch.setattr(cw, "watch_children", watch)
    await cw._run_shell(tmp_path, PARENT.target, PARENT.generation, run_id, 1)
    assert adapter.bind_parent.await_count == 2
    assert watch.await_args.kwargs["_manifest"] == manifest
    assert watch.await_args.kwargs["keep_alive"] is True
