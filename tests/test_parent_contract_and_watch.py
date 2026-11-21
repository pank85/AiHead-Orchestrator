"""Essential tests for direct parent routing contract, trust gate, and child watcher."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from herdr_master.pipeline.child_watch import (
    ChildEvent,
    ChildIdentity,
    ChildRecord,
    ChildStatus,
    watch_children,
)
from herdr_master.pipeline.parent_contract import (
    ParentRouteError,
    UntrustedWorkspaceError,
    compose_first_prompt,
    require_existing_agy_trust,
    resolve_parent_route,
    validate_parent_pane,
)


def test_parent_pane_validation_and_route_resolution() -> None:
    assert validate_parent_pane("w2Q:pE") == "w2Q:pE"
    assert resolve_parent_route("w2Q:pE", delegated=True) == "w2Q:pE"
    assert resolve_parent_route(None, delegated=False) is None

    with pytest.raises(ParentRouteError):
        validate_parent_pane("not-a-pane")
    with pytest.raises(ParentRouteError):
        resolve_parent_route(None, delegated=True)


def test_compose_first_prompt_binds_direct_parent_or_root() -> None:
    delegated = compose_first_prompt("Run task", parent_pane_id="w2Q:pE", delegated=True)
    assert "DIRECT PARENT CONTRACT" in delegated
    assert "w2Q:pE" in delegated
    assert "Run task" in delegated

    root = compose_first_prompt("Run task", parent_pane_id=None, delegated=False)
    assert "ROOT ROUTING CONTRACT" in root
    assert "Run task" in root


def test_require_existing_agy_trust_fails_closed_unless_trusted(isolated_home: Path) -> None:
    workspace = isolated_home / "project"
    workspace.mkdir()

    with pytest.raises(UntrustedWorkspaceError):
        require_existing_agy_trust(str(workspace), parent_pane_id="w1:p1")

    settings = isolated_home / ".gemini" / "antigravity-cli" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"trustedWorkspaces": [str(workspace.resolve())]}),
        encoding="utf-8",
    )
    require_existing_agy_trust(str(workspace), parent_pane_id="w1:p1")


async def test_child_watch_alerts_only_on_verified_loss_and_stops_cleanly(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    parent = ChildIdentity(transport="herdr", target="w1:p1", generation="gen-p")
    child = ChildIdentity(transport="herdr", target="w1:p2", generation="gen-c")
    record = ChildRecord(identity=child, role="coder")

    statuses = iter([ChildStatus.ALIVE, ChildStatus.UNKNOWN, ChildStatus.ABSENT])
    events: list[ChildEvent] = []
    stop = asyncio.Event()
    now = [0.0]

    async def fake_sleep(_stop: asyncio.Event, seconds: float) -> None:
        now[0] += seconds
        await asyncio.sleep(0)

    def probe(_ident: ChildIdentity) -> ChildStatus:
        try:
            return next(statuses)
        except StopIteration:
            stop.set()
            return ChildStatus.ABSENT

    def notify(_parent: ChildIdentity, event: ChildEvent) -> None:
        events.append(event)
        stop.set()

    await watch_children(
        parent=parent,
        read_children=lambda: [record],
        probe_child=probe,
        probe_parent=lambda _ident: ChildStatus.ALIVE,
        notify_parent=notify,
        stop=stop,
        state_dir=run_dir,
        interval_seconds=1,
        _clock=lambda: now[0],
        _sleep=fake_sleep,
    )

    assert len(events) == 1
    assert events[0].child.identity == child
    assert events[0].status == ChildStatus.ABSENT
