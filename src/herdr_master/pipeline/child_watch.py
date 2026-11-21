"""Parent-owned child supervision, independent of readiness and result callbacks.

Shell owner: create a private run directory (0700), register confirmed children,
then start ``python -m herdr_master.pipeline.child_watch --run-dir DIR
--parent-pane ID``. For detached handoff pin the previously observed parent with
``--parent-generation`` and preserve ``HERDR_SOCKET_PATH`` for a nondefault socket.
Create ``DIR/child-watch.stop`` to stop; never signal a PID read from the run
directory. A shell watcher persists between assignments until teardown or loss
of its pinned parent. Launchers also supply ``--run-id ID --startup-timeout 5``
and validate bound ownership via ``watcher_ready`` after spawning.

Launcher-tagged rows require an exact correlated receipt from the completion
helper, not a terminal-looking ledger status or an artifact. The private receipt
is local operational evidence of RPC acceptance, not authentication against
another process running as the same UID or proof of who authored the prompt.

Native/background owners supply an exact run-ID status adapter; synchronous
backends use their terminal callback instead. No watcher retries launches.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import inspect
import json
import logging
import math
import os
import re
import signal
import stat
import tempfile
from collections.abc import Awaitable, Callable, Iterable
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypeVar

_LOG = logging.getLogger(__name__)
_PANE = re.compile(r"(?P<workspace>[A-Za-z0-9][A-Za-z0-9_-]*):p[A-Za-z0-9]+\Z")
_GENERATION_PREFIX = "herdr-v1:"
_LAUNCHER_COMPLETION = "launcher-completion-v1"
_RUN_ID = re.compile(r"[0-9a-f]{32}\Z")
_OWNER_LOCK = "child-watch.lock"
_READY_GATE = "child-watch-ready.lock"
_READY_FILE = "child-watch-ready.json"
_STARTUP_TIMEOUT = 5.0
_RECONCILED_LEDGER = frozenset({"acknowledged", "reconciled"})
_TERMINAL_LEDGER = frozenset({"acknowledged", "closed", "completed", "integrated", "reconciled"})
_AMBIGUOUS_LEDGER = frozenset({"ambiguous", "launch_ambiguous", "launch_pending", "pending"})
_LIMIT = 4 * 1024 * 1024


@dataclass(frozen=True)
class ChildIdentity:
    transport: str
    target: str
    generation: str | None = None
    workspace: str | None = None

    def __post_init__(self) -> None:
        if not self.transport or not self.target or self.generation == "":
            raise ValueError("identity requires a transport and stable target")
        if self.transport == "herdr":
            pane = _PANE.fullmatch(self.target)
            if not pane or self.workspace not in (None, pane["workspace"]):
                raise ValueError("Herdr identity requires a canonical workspace-qualified pane ID")
            object.__setattr__(self, "workspace", pane["workspace"])


@dataclass(frozen=True)
class ChildRecord:
    identity: ChildIdentity
    role: str
    outcome: str | None = None
    acknowledged: bool = False
    completion_accepted: bool = False
    tagged: bool = False
    assignment_epoch: str | None = None

    @property
    def suppress_health_report(self) -> bool:
        return (
            self.acknowledged
            or self.completion_accepted
            or (self.tagged and self.assignment_epoch is None)
        )


class ChildStatus(Enum):
    ALIVE = "alive"
    ABSENT = "absent"
    EXITED = "exited"
    REPLACED = "replaced"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChildEvent:
    child: ChildRecord
    status: ChildStatus


@dataclass(frozen=True)
class _Probe:
    status: ChildStatus
    generation: str | None = None


_DEAD = frozenset({ChildStatus.ABSENT, ChildStatus.EXITED, ChildStatus.REPLACED})
_T = TypeVar("_T")


async def _resolve(value: _T | Awaitable[_T]) -> _T:
    return await value if inspect.isawaitable(value) else value


def _private_dir(directory: Path) -> None:
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("child watcher run directory must be private and owned by this user")


def _read_text(path: Path) -> str:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError(f"not a regular file: {path.name}")
        content = file.read(_LIMIT + 1)
        if len(content) > _LIMIT:
            raise ValueError(f"file too large: {path.name}")
        return content.decode("utf-8")


def _atomic_write(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".child-watch-", delete=False
    ) as file:
        temp = Path(file.name)
        try:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
            os.replace(temp, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temp.unlink(missing_ok=True)


@contextmanager
def _lock_fd(path: Path, *, create: bool = False):
    flags = os.O_NOFOLLOW | os.O_NONBLOCK
    flags |= (os.O_CREAT | os.O_RDWR) if create else os.O_RDONLY
    fd = os.open(path, flags, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("child watcher lock must be a private regular file")
        yield fd
    finally:
        os.close(fd)


def _try_lock(fd: int, mode: int = fcntl.LOCK_EX) -> bool:
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _run_manifest(run_id: str, parent_pane_id: str, parent_generation: str) -> dict:
    if not _RUN_ID.fullmatch(run_id) or not parent_generation:
        raise ValueError(
            "launcher watcher needs a 32-character lowercase hex run ID and generation"
        )
    ChildIdentity("herdr", parent_pane_id, parent_generation)
    return {
        "version": 1,
        "run_id": run_id,
        "parent_pane_id": parent_pane_id,
        "parent_generation": parent_generation,
    }


def _check_manifest(directory: Path, expected: dict) -> None:
    manifest = json.loads(_read_text(directory / "launcher-run.json"))
    if manifest != expected or type(manifest.get("version")) is not int:
        raise ValueError("watcher launcher manifest does not match the exact run and parent")


def watcher_ready(
    run_dir: str | Path,
    *,
    run_id: str,
    parent_pane_id: str,
    parent_generation: str,
) -> bool:
    """Return live, bound ownership evidence; a stale ready file or PID is insufficient."""
    directory = Path(run_dir)
    try:
        _private_dir(directory)
        expected = _run_manifest(run_id, parent_pane_id, parent_generation)
        with _lock_fd(directory / _READY_GATE) as gate:
            if not _try_lock(gate, fcntl.LOCK_SH):
                return False
            _check_manifest(directory, expected)
            if json.loads(_read_text(directory / _READY_FILE)) != expected:
                return False
            with _lock_fd(directory / _OWNER_LOCK) as owner:
                return not _try_lock(owner) and not (directory / "child-watch.stop").exists()
    except (OSError, ValueError, TypeError):
        return False


@contextmanager
def _owner(directory: Path | None):
    if directory is None:
        yield True
        return
    _private_dir(directory)
    with _lock_fd(directory / _OWNER_LOCK, create=True) as fd:
        # Serialize readiness reads with taking over ownership and clearing stale proof.
        # A new lock holder cannot accidentally inherit a dead holder's ready file.
        with _lock_fd(directory / _READY_GATE, create=True) as gate:
            fcntl.flock(gate, fcntl.LOCK_EX)
            acquired = _try_lock(fd)
            if acquired:
                (directory / _READY_FILE).unlink(missing_ok=True)
        if not acquired:
            yield False
            return
        pid_path = directory / "child-watch.pid"
        try:
            _atomic_write(pid_path, f"{os.getpid()}\n")
            yield True
        finally:
            (directory / _READY_FILE).unlink(missing_ok=True)
            pid_path.unlink(missing_ok=True)


def _key(identity: ChildIdentity) -> str:
    return json.dumps(
        (identity.transport, identity.workspace, identity.target, identity.generation),
        separators=(",", ":"),
    )


class _State:
    def __init__(self, directory: Path | None, parent: ChildIdentity, run_id: str | None) -> None:
        self.path = directory / "child-watch-state.json" if directory is not None else None
        self.data = {"version": 1, "parent": _key(parent), "pins": {}, "events": {}}
        if run_id is not None:
            self.data["run_id"] = run_id
        if self.path is not None:
            try:
                saved = json.loads(_read_text(self.path))
            except FileNotFoundError:
                self.save()  # Bind this directory before any observations or deliveries.
                return
            if (
                not isinstance(saved, dict)
                or saved.get("version") != 1
                or saved.get("parent") != self.data["parent"]
                or saved.get("run_id") != run_id
                or not all(
                    isinstance(saved.get(key), dict)
                    and all(isinstance(v, str) and v for v in saved[key].values())
                    for key in ("pins", "events")
                )
                or not set(saved["events"].values()) <= {"reserved", "delivered", "uncertain"}
            ):
                raise ValueError(
                    "invalid child watcher state or different owning parent/run; refusing replay"
                )
            self.data = saved
            if "reserved" in self.data["events"].values():
                _LOG.warning("previous child event delivery may be uncertain; not replaying")

    def save(self) -> None:
        if self.path is not None:
            _atomic_write(self.path, json.dumps(self.data, sort_keys=True) + "\n")

    def identity(self, identity: ChildIdentity) -> ChildIdentity:
        generation = self.data["pins"].get(_key(identity), identity.generation)
        if generation == identity.generation:
            return identity
        return ChildIdentity(identity.transport, identity.target, generation, identity.workspace)

    def pin(self, identity: ChildIdentity, generation: str) -> ChildIdentity:
        self.data["pins"][_key(identity)] = generation
        self.save()
        return self.identity(identity)

    def reserved(self, identity: ChildIdentity, epoch: str | None = None) -> bool:
        key = _key(identity) if epoch is None else json.dumps((_key(identity), epoch))
        return key in self.data["events"]

    def event(self, identity: ChildIdentity, status: str, epoch: str | None = None) -> None:
        key = _key(identity) if epoch is None else json.dumps((_key(identity), epoch))
        self.data["events"][key] = status
        self.save()


def _launcher_receipt(run_dir: Path, parent: ChildIdentity | None, child: ChildIdentity) -> bool:
    if (
        parent is None
        or parent.transport != "herdr"
        or not parent.generation
        or not child.generation
        or parent.target == child.target
    ):
        return False
    try:
        from herdr_master.pipeline.parent_contract import completion_receipt_accepted

        return (
            completion_receipt_accepted(
                run_dir,
                parent_pane_id=parent.target,
                parent_generation=parent.generation,
                child_pane_id=child.target,
                child_generation=child.generation,
            )
            is True
        )
    except Exception:
        _LOG.warning("cannot verify child completion receipt for %s", child.target, exc_info=True)
        return False


def _assignment_epoch(
    run_dir: Path, parent: ChildIdentity | None, child: ChildIdentity
) -> str | None:
    if (
        parent is None
        or parent.transport != "herdr"
        or not parent.generation
        or not child.generation
    ):
        return None
    try:
        from herdr_master.pipeline.parent_contract import current_assignment_epoch

        epoch = current_assignment_epoch(
            run_dir,
            parent_pane_id=parent.target,
            parent_generation=parent.generation,
            child_pane_id=child.target,
            child_generation=child.generation,
        )
        return epoch if isinstance(epoch, str) and re.fullmatch(r"[0-9a-f]{64}", epoch) else None
    except Exception:
        _LOG.warning("cannot verify child assignment for %s", child.target, exc_info=True)
        return None


def read_herdr_children(run_dir: Path, *, parent: ChildIdentity | None = None) -> list[ChildRecord]:
    """Read registered children; only tagged launcher rows require a correlated receipt.

    Legacy shell status is a parent assertion, not transport-verified delivery. Its
    owner must acknowledge only an actual direct-parent completion prompt. A
    tagged record suppresses health alerts after current-assignment transport
    acceptance; only an explicit parent reconciliation status marks it acknowledged.
    """
    try:
        content = _read_text(run_dir / "children.tsv")
    except FileNotFoundError:
        return []
    records: dict[ChildIdentity, ChildRecord] = {}
    # An interrupted append is not a complete snapshot; shell writers emit a newline.
    if content and not content.endswith("\n"):
        raise ValueError("incomplete children.tsv snapshot")
    for line in content.splitlines():
        if not line:
            continue
        fields = line.split("\t")
        if len(fields) not in (8, 9, 10) or not all(fields[index].strip() for index in (0, 1, 6)):
            raise ValueError("malformed children.tsv row")
        tagged = len(fields) == 10
        if tagged and (fields[9] != _LAUNCHER_COMPLETION or not fields[8].strip()):
            raise ValueError("invalid launcher completion row tag or generation")
        pane, role, tab, _, _, _, status, _ = fields[:8]
        status = status.strip().lower()
        if not tagged and status in _AMBIGUOUS_LEDGER:
            continue  # Untagged attempts must be reconciled before monitoring.
        generation = (fields[8] or None) if len(fields) >= 9 else None
        identity = ChildIdentity("herdr", pane, generation)
        # The exact registered child may live in a workspace distinct from its owner.
        workspace = identity.workspace
        if workspace is None or not re.fullmatch(rf"{re.escape(workspace)}:t[A-Za-z0-9]+", tab):
            raise ValueError("children.tsv pane and tab workspace disagree")
        if identity in records:
            raise ValueError("duplicate child identity in children.tsv")
        if tagged:
            epoch = _assignment_epoch(run_dir, parent, identity)
            accepted = epoch is not None and _launcher_receipt(run_dir, parent, identity)
            # Detect pointer changes across the separate receipt lookup.
            if epoch != _assignment_epoch(run_dir, parent, identity):
                epoch, accepted = None, False
            reconciled = accepted and status in _RECONCILED_LEDGER
            records[identity] = ChildRecord(
                identity, role, status if reconciled else None, reconciled, accepted, True, epoch
            )
        else:
            terminal = status in _TERMINAL_LEDGER
            records[identity] = ChildRecord(identity, role, status if terminal else None, terminal)
    return list(records.values())


async def _snapshot(
    read_children, parent: ChildIdentity
) -> dict[ChildIdentity, ChildRecord] | None:
    try:
        records: dict[ChildIdentity, ChildRecord] = {}
        for record in await _resolve(read_children()):
            if (
                not isinstance(record, ChildRecord)
                or not record.role
                or record.identity in records
                or (
                    parent.transport == "herdr"
                    and record.identity.transport == "herdr"
                    and record.identity.target == parent.target
                )
            ):
                raise ValueError("invalid or duplicate child record")
            records[record.identity] = record
        return records
    except Exception:
        _LOG.warning("child ledger unavailable; skipping snapshot", exc_info=True)
        return None


async def _probe(callback, identity: ChildIdentity) -> _Probe:
    try:
        result = await _resolve(callback(identity))
        if isinstance(result, _Probe) and isinstance(result.status, ChildStatus):
            return result
        if isinstance(result, ChildStatus):
            return _Probe(result)
    except Exception:
        _LOG.warning("child watcher status unavailable for %s", identity.target, exc_info=True)
    return _Probe(ChildStatus.UNKNOWN)


async def _wait_for_stop(stop: asyncio.Event, delay: float) -> None:
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), delay)


async def watch_children(
    *,
    parent: ChildIdentity,
    read_children: Callable[[], Iterable[ChildRecord] | Awaitable[Iterable[ChildRecord]]],
    probe_child: Callable[[ChildIdentity], ChildStatus | _Probe | Awaitable[ChildStatus | _Probe]],
    notify_parent: Callable[[ChildIdentity, ChildEvent], object | Awaitable[object]],
    stop: asyncio.Event,
    interval_seconds: float = 600,
    probe_parent: Callable[[ChildIdentity], ChildStatus | _Probe | Awaitable[ChildStatus | _Probe]]
    | None = None,
    state_dir: str | Path | None = None,
    keep_alive: bool = False,
    _manifest: dict | None = None,
    _clock: Callable[[], float] | None = None,
    _sleep: Callable[[asyncio.Event, float], Awaitable[None]] = _wait_for_stop,
) -> None:
    """Poll a confirmed-child snapshot; reserve each terminal report before attempting it.

    A Herdr parent needs a pinned generation, a revalidation callback and a private
    state directory. Without a directory, a native lifetime-bound sink gets only
    in-memory deduplication. Callbacks must be nonblocking (async or quick sync).
    Acceptance suppresses health reports but is not reconciliation and cannot
    stop a watcher on its own. Persistent owners wait for teardown or parent loss.
    """
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("child watcher interval must be positive and finite")
    directory = Path(state_dir) if state_dir is not None else None
    if not all(callable(callback) for callback in (read_children, probe_child, notify_parent)):
        raise ValueError("child watcher requires a queryable asynchronous status adapter and sink")
    if parent.transport == "herdr" and (
        not parent.generation or probe_parent is None or directory is None
    ):
        raise ValueError(
            "a Herdr watcher requires pinned parent identity, parent revalidation and durable state"
        )
    if stop.is_set():
        return
    with _owner(directory) as acquired:
        if not acquired:
            _LOG.info("another watcher owns this run")
            return
        state = _State(directory, parent, _manifest["run_id"] if _manifest else None)
        if _manifest is not None:
            assert directory is not None
            _check_manifest(directory, _manifest)
            if stop.is_set() or (directory / "child-watch.stop").exists():
                return
            _atomic_write(directory / _READY_FILE, json.dumps(_manifest) + "\n")
        clock = _clock or asyncio.get_running_loop().time
        deadline = clock() + interval_seconds
        seen_child = False

        async def parent_status() -> ChildStatus:
            return (
                (await _probe(probe_parent, parent)).status if probe_parent else ChildStatus.ALIVE
            )

        while not stop.is_set():
            if (delay := deadline - clock()) > 0:
                await _sleep(stop, delay)
                continue
            deadline += interval_seconds
            if deadline <= clock():
                deadline = clock() + interval_seconds  # Never replay missed ticks.
            owner_status = await parent_status()
            if owner_status in _DEAD or stop.is_set():
                return
            if owner_status is not ChildStatus.ALIVE:
                continue
            snapshot = await _snapshot(read_children, parent)
            if snapshot is None:
                continue
            seen_child |= bool(snapshot)
            for record in snapshot.values():
                if stop.is_set():
                    return
                identity = state.identity(record.identity)
                if record.suppress_health_report or state.reserved(
                    identity, record.assignment_epoch
                ):
                    continue
                probe = await _probe(probe_child, identity)
                if stop.is_set():
                    return
                if (
                    probe.status is ChildStatus.ALIVE
                    and probe.generation
                    and identity == record.identity
                    and probe.generation != identity.generation
                ):
                    # Strengthen legacy/raw pins using the first matching observation.
                    state.pin(record.identity, probe.generation)
                if probe.status not in _DEAD:
                    continue
                owner_status = await parent_status()
                if owner_status in _DEAD or stop.is_set():
                    return
                if owner_status is not ChildStatus.ALIVE:
                    continue
                current = await _snapshot(read_children, parent)
                latest = current.get(record.identity) if current is not None else None
                if (
                    latest is None
                    or latest.suppress_health_report
                    or stop.is_set()
                    or latest.assignment_epoch != record.assignment_epoch
                ):
                    continue
                event = ChildEvent(ChildRecord(identity, latest.role, latest.outcome), probe.status)
                state.event(identity, "reserved", record.assignment_epoch)
                try:
                    await _resolve(notify_parent(parent, event))
                except asyncio.CancelledError:
                    _LOG.warning(
                        "child event delivery cancelled, outcome uncertain for %s", identity.target
                    )
                    raise  # The persisted reservation also fences a process crash.
                except Exception:
                    state.event(identity, "uncertain", record.assignment_epoch)
                    _LOG.warning(
                        "child event delivery uncertain for %s; not replaying",
                        identity.target,
                        exc_info=True,
                    )
                else:
                    state.event(identity, "delivered", record.assignment_epoch)
            if keep_alive:
                continue
            current = await _snapshot(read_children, parent)
            if (
                seen_child
                and current is not None
                and all(
                    row.acknowledged
                    or state.reserved(state.identity(row.identity), row.assignment_epoch)
                    for row in current.values()
                )
            ):
                return


def _nonempty(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class _Pane:
    pane_id: str
    workspace: str
    terminal: str | None
    session: str | None
    agent: str | None
    exited: bool

    @classmethod
    def parse(cls, body: object, identity: ChildIdentity) -> _Pane | None:
        if not isinstance(body, dict) or body.get("pane_id") != identity.target:
            return None
        if body.get("workspace_id") != identity.workspace:
            return None
        session = body.get("agent_session")
        return cls(
            identity.target,
            body["workspace_id"],
            _nonempty(body.get("terminal_id")),
            _nonempty(session.get("value")) if isinstance(session, dict) else None,
            _nonempty(body.get("agent")),
            body.get("exited") is True or body.get("agent_status") == "exited",
        )

    @property
    def generation(self) -> str | None:
        if self.terminal or self.session:
            return _GENERATION_PREFIX + json.dumps(
                (self.terminal, self.session), separators=(",", ":")
            )
        return None

    def match(self, expected: str | None) -> ChildStatus:
        if expected is None:
            return ChildStatus.ALIVE
        if expected.startswith(_GENERATION_PREFIX):
            try:
                values = json.loads(expected[len(_GENERATION_PREFIX) :])
            except ValueError:
                return ChildStatus.UNKNOWN
            if (
                not isinstance(values, list)
                or len(values) != 2
                or not any(values)
                or any(v is not None and not _nonempty(v) for v in values)
            ):
                return ChildStatus.UNKNOWN
            terminal, session = values
            fields = ((terminal, self.terminal), (session, self.session))
            if any(before and now and before != now for before, now in fields):
                return ChildStatus.REPLACED
            if any(before and not now for before, now in fields):
                return ChildStatus.UNKNOWN
            return ChildStatus.ALIVE
        if expected in (self.terminal, self.session):
            return ChildStatus.ALIVE
        # A missing expected session can be an incomplete transport observation.
        if (self.terminal and self.session) or (self.terminal and expected.startswith("term_")):
            return ChildStatus.REPLACED
        return ChildStatus.UNKNOWN


async def _herdr_command(*args: str) -> tuple[int, dict]:
    proc = await asyncio.create_subprocess_exec(
        "herdr",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), 10)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        with suppress(ProcessLookupError):
            proc.kill()  # Only this private CLI subprocess, never a pane or persisted PID.
        await proc.wait()
        raise
    # Herdr 0.9 CLI puts structured errors on stderr and successes on stdout.
    payload = json.loads(stdout or stderr)
    if not isinstance(payload, dict):
        raise ValueError("malformed Herdr reply")
    return proc.returncode, payload


class HerdrAdapter:
    """Exact pane inspection and a one-shot prompt to the pinned parent."""

    def __init__(self, run=_herdr_command) -> None:
        self._run = run

    async def _get(self, identity: ChildIdentity) -> tuple[ChildStatus, _Pane | None]:
        rc, reply = await self._run("pane", "get", identity.target)
        error = reply.get("error")
        if rc == 1 and isinstance(error, dict) and error.get("code") == "pane_not_found":
            return ChildStatus.ABSENT, None
        result = reply.get("result")
        if rc != 0 or not isinstance(result, dict) or result.get("type") != "pane_info" or error:
            return ChildStatus.UNKNOWN, None
        pane = _Pane.parse(result.get("pane"), identity)
        if pane is None:
            return ChildStatus.UNKNOWN, None
        status = pane.match(identity.generation)
        if pane.exited and status is ChildStatus.ALIVE:
            return ChildStatus.EXITED, pane
        return status, pane

    async def bind_parent(self, identity: ChildIdentity) -> ChildIdentity:
        status, pane = await self._get(identity)
        if status is not ChildStatus.ALIVE or pane is None or not pane.agent or not pane.generation:
            raise ValueError("cannot verify exact Herdr parent generation")
        generation = identity.generation
        if generation is None or not generation.startswith(_GENERATION_PREFIX):
            generation = pane.generation
        return ChildIdentity("herdr", identity.target, generation, identity.workspace)

    async def probe_parent(self, identity: ChildIdentity) -> ChildStatus:
        status, pane = await self._get(identity)
        if status is ChildStatus.ALIVE and (pane is None or not pane.agent):
            return ChildStatus.UNKNOWN
        return status

    async def probe_child(self, identity: ChildIdentity) -> _Probe:
        status, pane = await self._get(identity)
        return _Probe(status, pane.generation if pane and pane.agent else None)

    async def notify_parent(self, parent: ChildIdentity, event: ChildEvent) -> None:
        # Herdr has no generation-conditional prompt. Revalidate immediately before
        # submission, then verify the returned identity; never fall back/retry.
        if await self.probe_parent(parent) is not ChildStatus.ALIVE:
            raise RuntimeError("pinned parent changed or became unqueryable before submission")
        role = json.dumps(event.child.role, ensure_ascii=True)
        message = (
            f"Child watcher: {role} ({event.child.identity.target}) is {event.status.value}; "
            "no terminal outcome was acknowledged. Reconcile this attempt without respawning it."
        )
        rc, reply = await self._run("agent", "prompt", parent.target, message)
        result = reply.get("result")
        if (
            rc
            or not isinstance(result, dict)
            or result.get("type") != "agent_prompted"
            or reply.get("error")
        ):
            raise RuntimeError("Herdr did not confirm prompt delivery")
        routed = _Pane.parse(result.get("agent"), parent)
        if routed is None or routed.match(parent.generation) is not ChildStatus.ALIVE:
            raise RuntimeError("Herdr did not confirm delivery to the pinned parent generation")


async def _run_shell(
    run_dir: Path,
    parent_pane: str,
    generation: str | None,
    run_id: str | None = None,
    startup_timeout: float = _STARTUP_TIMEOUT,
) -> None:
    _private_dir(run_dir)
    stop = asyncio.Event()
    sentinel = run_dir / "child-watch.stop"
    if sentinel.exists():
        return
    adapter = HerdrAdapter()
    manifest = _run_manifest(run_id, parent_pane, generation or "") if run_id else None
    if manifest is not None:
        _check_manifest(run_dir, manifest)
    requested = ChildIdentity("herdr", parent_pane, generation)

    async def bind() -> ChildIdentity:
        while True:
            try:
                return await adapter.bind_parent(requested)
            except (ValueError, OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.1)

    bound = await asyncio.wait_for(bind(), startup_timeout)
    # Launcher receipt correlation uses precisely the generation in its manifest.
    parent = requested if manifest is not None else bound
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async def follow_sentinel() -> None:
        while not stop.is_set():
            if sentinel.exists():
                stop.set()
                return
            await _wait_for_stop(stop, 1)

    sentry = asyncio.create_task(follow_sentinel())
    try:
        await watch_children(
            parent=parent,
            read_children=lambda: read_herdr_children(run_dir, parent=parent),
            probe_child=adapter.probe_child,
            notify_parent=adapter.notify_parent,
            stop=stop,
            probe_parent=adapter.probe_parent,
            state_dir=run_dir,
            keep_alive=manifest is not None,
            _manifest=manifest,
        )
    finally:
        stop.set()
        await sentry
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--parent-pane", required=True)
    parser.add_argument(
        "--parent-generation", help="previously observed terminal ID or agent session value"
    )
    parser.add_argument("--run-id", help="32-character lowercase hexadecimal launcher run ID")
    parser.add_argument("--startup-timeout", type=float, default=_STARTUP_TIMEOUT)
    args = parser.parse_args(argv)
    if not math.isfinite(args.startup_timeout) or args.startup_timeout <= 0:
        parser.error("--startup-timeout must be positive and finite")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        asyncio.run(
            _run_shell(
                args.run_dir,
                args.parent_pane,
                args.parent_generation,
                args.run_id,
                args.startup_timeout,
            )
        )
    except (OSError, ValueError, asyncio.TimeoutError) as exc:
        _LOG.error("child watcher stopped: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
