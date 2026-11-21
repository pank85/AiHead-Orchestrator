"""Launch-time routing and approval provenance shared by the Python entry points."""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import inspect
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from herdr_master.herdr.socket import HerdrRPCError


class ParentRouteError(ValueError):
    """A delegated launch has no usable direct-parent route."""


class UntrustedWorkspaceError(RuntimeError):
    """The launch cannot proceed without existing normal workspace trust."""


# Herdr uses case-sensitive alphanumeric pane/tab ordinals (e.g. w2Q:pE).
_PANE_ID = re.compile(r"(?P<workspace>[A-Za-z0-9][A-Za-z0-9_-]*):p[A-Za-z0-9]+", re.ASCII)
_TAB_ID = re.compile(r"(?P<workspace>[A-Za-z0-9][A-Za-z0-9_-]*):t[A-Za-z0-9]+", re.ASCII)
_LAUNCH_TOKEN = re.compile(r"[0-9a-f]{64}", re.ASCII)
COMPLETION_GATE = "launcher-completion-v1"


def validate_parent_pane(parent_pane_id: str) -> str:
    if not isinstance(parent_pane_id, str) or not _PANE_ID.fullmatch(parent_pane_id):
        raise ParentRouteError("Expected a canonical Herdr direct-parent pane ID (<workspace>:p<ordinal>).")
    return parent_pane_id


def resolve_parent_route(parent_pane_id: str | None, *, delegated: bool) -> str | None:
    if parent_pane_id is not None:
        return validate_parent_pane(parent_pane_id)
    if delegated:
        raise ParentRouteError("Cannot identify the immediate parent; delegated launch aborted.")
    return None


def direct_parent_directive(parent_pane_id: str) -> str:
    parent = validate_parent_pane(parent_pane_id)
    return (
        f"DIRECT PARENT CONTRACT — your immediate supervisor is pane {parent}.\n"
        f'Use `herdr agent prompt {parent} "<message>"` for all questions, approvals, '
        "decisions, blockers, and completion. Ask and notify ONLY this pane. Never contact "
        "any other pane (including a grandparent or a discovered AiHead) or the user; never "
        "open a user question or update tab. For these communications this contract takes "
        "precedence over any conflicting role, bootstrap, or skill guidance. Preserve the "
        "workflow's completion payload and artifacts (including review, actual verification, "
        "or final_plan.md where applicable). If a launcher completion helper is provided below, "
        "use it for the terminal report; it performs the required Herdr send. "
        "For your own native children bind the invoking "
        "agent's return channel, not this Herdr route."
    )


def compose_first_prompt(
    prompt: str, *, parent_pane_id: str | None = None, delegated: bool = False
) -> str:
    """Attach the launch contract once, without modifying the role/effort or payload block."""
    parent = resolve_parent_route(parent_pane_id, delegated=delegated)
    if parent is not None:
        return f"{direct_parent_directive(parent)}\n\n{PLAN_APPROVAL_POLICY}\n\n{HARD_STOP_POLICY}\n\n{prompt}"
    return (
        "ROOT ROUTING CONTRACT — no parent is assigned. Ask the user directly for necessary "
        "decisions and report here (or through the existing process result channel). Never "
        "invent a parent from inherited metadata, focus, or AiHead discovery. This takes "
        "precedence over conflicting role, bootstrap, or skill routing.\n\n"
        f"{PLAN_APPROVAL_POLICY}\n\n{HARD_STOP_POLICY}\n\n{prompt}"
    )


PLAN_APPROVAL_POLICY = (
    "Only AiHead or the human user may explicitly approve a specific gated plan. "
    "Ergo, Orchestration, Council and Herdr-Master coordinators must submit the gated "
    "plan to their exact direct parent and wait for explicit approval from AiHead or "
    "the human, relayed through that parent if needed. An intentional root asks the human "
    "directly. Coordinator self-approval, internal consensus, silence and ordinary technical "
    "assent are not plan approval. Correlate approval to the concrete pending plan; an "
    "unverified claim of human approval is not provenance. Routine automode plans may be "
    "nonblocking updates, not approvals; explicitly gated runs retain their gates."
)


HARD_STOP_POLICY = (
    "Irreversible destruction and security bypass always require explicit user approval "
    "for the specific action and target, relayed down the direct chain. Technical assent, "
    "plan approval, configuration flags, silence, timeout, and trust dialogs are not user "
    "approval. Do not add trust, dismiss unknown dialogs, or weaken permissions. Ask via "
    "your direct parent (or the user at a root) with a least-privilege alternative. On "
    "denial or timeout, leave the restricted state unchanged."
)


def require_existing_agy_trust(cwd: str, *, parent_pane_id: str | None = None) -> None:
    """Read existing explicit trust; never edit settings or interact with a trust dialog."""
    resolved = str(Path(cwd).resolve())
    settings = Path.home() / ".gemini" / "antigravity-cli" / "settings.json"
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
        trusted = data.get("trustedWorkspaces") if isinstance(data, dict) else None
        if isinstance(trusted, list) and resolved in trusted:
            return
    except (OSError, ValueError):
        pass
    route = (
        f'Report the blocker only via `herdr agent prompt {validate_parent_pane(parent_pane_id)} '
        '"<message>"`.'
        if parent_pane_id is not None
        else "Report the blocker through the invoking process."
    )
    raise UntrustedWorkspaceError(
        f"Cannot verify existing Antigravity workspace trust for {resolved!r}; no agent started. "
        "Use an already-trusted workspace or have the user review the workspace in the normal "
        f"trust flow and retry explicitly. {route}"
    )


async def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result = fn(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _agent(payload: Any, pane_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    record = payload.get("agent")
    if not isinstance(record, dict) or record.get("pane_id") != pane_id:
        return {}
    return record


def _launch_identity(record: dict[str, Any], pane_id: str) -> tuple[str, str] | None:
    pane = _PANE_ID.fullmatch(pane_id)
    tab_id = record.get("tab_id")
    tab = _TAB_ID.fullmatch(tab_id) if isinstance(tab_id, str) else None
    generation = _generation(record)
    if (
        pane and tab and generation and tab_id
        and pane['workspace'] == tab['workspace'] == record.get('workspace_id')
        and record.get('agent') and record.get('exited') is not True
        and record.get("agent_status") not in ("dead", "stopped", "exited", "terminated", "none", "terminal")
    ):
        return tab_id, generation
    return None


async def classify_launch_caller(
    client: Any, *, caller_pane_id: str | None, parent_pane_id: str | None, delegated: bool,
) -> tuple[str | None, str | None]:
    """Bind an exact live agent, or establish that an implicit caller is a plain shell."""
    explicit = delegated or parent_pane_id is not None
    pane = resolve_parent_route(
        parent_pane_id if parent_pane_id is not None else caller_pane_id, delegated=explicit,
    )
    if pane is None:
        return None, None
    try:
        reply = await _call(client.get_agent, pane)
    except HerdrRPCError as exc:
        if explicit or exc.code != "agent_not_found":
            raise ParentRouteError(f"Cannot query the exact direct-parent agent in {pane}.") from exc
        try:
            reply = await _call(client.current_pane, caller_pane_id=pane)
        except Exception as pane_exc:
            raise ParentRouteError(f"Cannot verify a shell in caller pane {pane}.") from pane_exc
        record = reply.get("pane") if isinstance(reply, dict) else None
        if (isinstance(record, dict) and reply.get("type") == "pane_info"
            and record.get("pane_id") == pane and not record.get("agent")
            and record.get("exited") is not True):
            return None, None
        raise ParentRouteError(f"Cannot verify a shell in caller pane {pane}.") from exc
    except Exception as exc:
        raise ParentRouteError(f"Cannot query the exact direct-parent agent in {pane}.") from exc
    identity = _launch_identity(_agent(reply, pane), pane)
    if identity is None:
        raise ParentRouteError(f"Cannot verify the current direct-parent agent in {pane}.")
    return pane, identity[1]


def _generation(agent: dict[str, Any]) -> str | None:
    session = agent.get("agent_session")
    terminal = agent.get("terminal_id")
    value = session.get("value") if isinstance(session, dict) else None
    terminal = terminal if isinstance(terminal, str) and terminal else None
    value = value if isinstance(value, str) and value else None
    if not (terminal or value):
        return None
    # B's canonical encoding is also escaped for one TSV field.
    return "herdr-v1:" + json.dumps((terminal, value), separators=(",", ":"))


def _private_dir(path: Path) -> Path:
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError(f"Unsafe ownership or permissions for watcher directory: {path}")
    return path


def _watch_run_dir(socket_path: Path, parent: str, generation: str, run_id: str) -> Path:
    # Each invocation has its own run, even when the same parent launches again.
    root = _private_dir(Path(tempfile.gettempdir()) / f"aihead-child-watch-{os.getuid()}")
    fingerprint = json.dumps((str(socket_path.resolve()), parent, generation, run_id))
    key = hashlib.sha256(fingerprint.encode()).hexdigest()
    return _private_dir(root / key)


def _completion_scope(parent: str, parent_generation: str, child: str, child_generation: str) -> dict[str, str]:
    return {
        "version": COMPLETION_GATE,
        "parent_pane_id": parent,
        "parent_generation": parent_generation,
        "child_pane_id": child,
        "child_generation": child_generation,
    }


def _report_id(scope: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()


def _assignment_path(run_dir: Path, scope: dict[str, str]) -> Path:
    base = {key: value for key, value in scope.items() if key != "assignment_id"}
    return run_dir / f"assignment-{_report_id(base)}.json"


def _current_assignment(run_dir: Path, scope: dict[str, str]) -> str:
    assignment = _read_private_json(_assignment_path(run_dir, scope)).get("assignment_id")
    if not isinstance(assignment, str) or not _LAUNCH_TOKEN.fullmatch(assignment):
        raise ValueError("Invalid current assignment.")
    return assignment


def _check_private_dir(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise RuntimeError(f"Unsafe completion directory: {path}")


def _read_private_json(path: Path) -> dict[str, Any]:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError(f"Unsafe completion file: {path}")
        data = json.loads(source.read(16_385))
    if not isinstance(data, dict):
        raise ValueError("Invalid completion record.")
    return data


def _write_private_json(path: Path, data: dict[str, Any]) -> None:
    # An atomic replacement prevents a watcher from accepting a partial record.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
        tmp = Path(out.name)
        try:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
            os.replace(tmp, path)
            dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            tmp.unlink(missing_ok=True)


def current_assignment_epoch(
    run_dir: Path, *, parent_pane_id: str, parent_generation: str,
    child_pane_id: str, child_generation: str,
) -> str | None:
    """Return the validated opaque current epoch, or None when unavailable."""
    try:
        _check_private_dir(run_dir)
        scope = _completion_scope(parent_pane_id, parent_generation, child_pane_id, child_generation)
        return _current_assignment(run_dir, scope)
    except (OSError, RuntimeError, ValueError):
        return None


def completion_receipt_accepted(
    run_dir: Path, *, parent_pane_id: str, parent_generation: str,
    child_pane_id: str, child_generation: str,
) -> bool:
    """Read correlated RPC-acceptance evidence, never infer completion from artifacts/status."""
    scope = _completion_scope(parent_pane_id, parent_generation, child_pane_id, child_generation)
    try:
        _check_private_dir(run_dir)
        scope["assignment_id"] = _current_assignment(run_dir, scope)
        report_id = _report_id(scope)
        receipt = _read_private_json(run_dir / f"receipt-{report_id}.json")
        receipt_scope = {key: receipt.get(key) for key in scope}
        digest = receipt.get("message_sha256")
        return (
            receipt_scope == scope and receipt.get("state") == "accepted"
            and receipt.get("rpc_result") == "agent_prompted"
            and isinstance(digest, str) and _LAUNCH_TOKEN.fullmatch(digest) is not None
        )
    except (OSError, RuntimeError, ValueError):
        return False


def _completion_directive(run_dir: Path, token: str) -> str:
    command = shlex.join([sys.executable, "-m", "herdr_master.pipeline.parent_contract",
                         "complete", "--run-dir", str(run_dir), "--launch-token", token, "--message"])
    return (
        "COMPLETION DELIVERY REQUIRED — for the terminal report use this launcher helper, "
        "not a raw prompt or an artifact alone. It sends `herdr agent prompt` to the exact "
        "direct parent and records acceptance. Preserve the required outcome and artifact paths. "
        "Use one shell argument for the message, and do not disclose the launch token:\n"
        f'{command} "<terminal outcome and required artifact paths>"\n'
        "If acceptance is uncertain, do not retry or substitute an artifact; reconcile with "
        "the direct parent. Use the ordinary direct-parent route for nonterminal messages."
    )


def _herdr_completion_rpc(endpoint: str, *args: str) -> dict[str, Any]:
    env = {**os.environ, "HERDR_SOCKET_PATH": endpoint, "HERDR_SOCKET": endpoint}
    result = subprocess.run(
        ["herdr", "agent", *args], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=15, env=env, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Herdr rejected or did not confirm agent {args[0]}.")
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict) or payload.get("error") or not isinstance(payload.get("result"), dict):
        raise RuntimeError("Herdr returned no structured acceptance evidence.")
    return payload["result"]


def _verify_completion_agent(result: dict[str, Any], pane: str, generation: str, reply_type: str) -> None:
    identity = _launch_identity(_agent(result, pane), pane)
    if result.get("type") != reply_type or identity is None or not _matches_generation(generation, identity[1]):
        raise RuntimeError(f"Herdr did not confirm the exact expected {reply_type} identity in {pane}.")


def _matches_generation(expected: str, observed: str) -> bool:
    if expected == observed:
        return True
    prefix = "herdr-v1:"
    if not expected.startswith(prefix) or not observed.startswith(prefix):
        return False
    before_terminal, before_session = json.loads(expected.removeprefix(prefix))
    now_terminal, now_session = json.loads(observed.removeprefix(prefix))
    # Herdr may learn a session ID after the first task prompt. Do not discard a
    # previously known field or accept changed terminals/sessions.
    return bool(before_terminal and before_terminal == now_terminal and before_session is None and now_session)


def send_terminal_completion(run_dir: Path, token: str, message: str) -> str:
    """Serialize completion against reassignment to avoid stale sends."""
    _check_private_dir(run_dir)
    with (run_dir / ".parent-ledger.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _send_terminal_completion_locked(run_dir, token, message)


def _send_terminal_completion_locked(run_dir: Path, token: str, message: str) -> str:
    """Send one correlated terminal report and persist only affirmative exact-target acceptance."""
    if not _LAUNCH_TOKEN.fullmatch(token) or not message.strip() or "\0" in message:
        raise ValueError("Expected a launcher-issued token and nonempty terminal message.")
    _check_private_dir(run_dir)
    launch = _read_private_json(run_dir / f"launch-{token}.json")
    parent, parent_generation = launch.get("parent_pane_id"), launch.get("parent_generation")
    child, child_generation = launch.get("child_pane_id"), launch.get("child_generation")
    endpoint = launch.get("socket")
    if (launch.get("version") != COMPLETION_GATE
        or not all(isinstance(value, str) and value for value in (parent, parent_generation, child, child_generation, endpoint))
        or validate_parent_pane(parent) == validate_parent_pane(child)
        or not Path(endpoint).is_absolute()
        or os.environ.get("HERDR_PANE_ID") != child):
        raise ParentRouteError("Completion must run in the exact child pane assigned by the launcher.")
    scope = _completion_scope(parent, parent_generation, child, child_generation)
    assignment_id = launch.get("assignment_id")
    if assignment_id != _current_assignment(run_dir, scope):
        raise RuntimeError("Completion belongs to a stale assignment.")
    scope["assignment_id"] = assignment_id
    report_id = _report_id(scope)
    receipt = run_dir / f"receipt-{report_id}.json"
    payload = f"HERDR TERMINAL REPORT {report_id} from {child}\n\n{message}"
    if completion_receipt_accepted(run_dir, parent_pane_id=parent, parent_generation=parent_generation,
                                   child_pane_id=child, child_generation=child_generation):
        accepted = _read_private_json(receipt)
        if accepted.get("message_sha256") != hashlib.sha256(payload.encode()).hexdigest():
            raise RuntimeError("Accepted completion has a different message; do not replay.")
        return report_id
    _verify_completion_agent(_herdr_completion_rpc(endpoint, "get", child), child, child_generation, "agent_info")
    _verify_completion_agent(_herdr_completion_rpc(endpoint, "get", parent), parent, parent_generation, "agent_info")
    # Exclusive reservation before prompting. A failed/ambiguous send (or a crash) is never
    # replayed automatically. An operator may reconcile the report ID at the exact parent.
    reservation = run_dir / f"attempt-{report_id}"
    try:
        with reservation.open("x", encoding="ascii") as attempt:
            attempt.write(report_id + "\n")
            attempt.flush()
            os.fsync(attempt.fileno())
        dfd = os.open(run_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except FileExistsError as exc:
        raise RuntimeError(f"Completion delivery already attempted ({report_id}); reconcile with the direct parent, do not replay.") from exc
    result = _herdr_completion_rpc(endpoint, "prompt", parent, payload)
    _verify_completion_agent(result, parent, parent_generation, "agent_prompted")
    _write_private_json(receipt, {
        **scope, "state": "accepted", "rpc_result": "agent_prompted", "socket": endpoint,
        "message_sha256": hashlib.sha256(payload.encode()).hexdigest(),
    })
    return report_id


def verify_terminal_completion(run_dir: Path, report_id: str, message: str) -> str:
    """Parent-facing gate for a received report ID; it never marks or sends anything."""
    if not _LAUNCH_TOKEN.fullmatch(report_id):
        raise ValueError("Expected the report ID from an incoming terminal report.")
    _check_private_dir(run_dir)
    receipt = _read_private_json(run_dir / f"receipt-{report_id}.json")
    fields = ("parent_pane_id", "parent_generation", "child_pane_id", "child_generation")
    identifiers = {key: receipt.get(key) for key in fields}
    endpoint = receipt.get("socket")
    if (not all(isinstance(value, str) and value for value in identifiers.values())
        or not isinstance(endpoint, str) or not Path(endpoint).is_absolute()
        or os.environ.get("HERDR_PANE_ID") != identifiers["parent_pane_id"]
        or receipt.get("assignment_id") != _current_assignment(run_dir, {"version": COMPLETION_GATE, **identifiers})
        or report_id != _report_id({"version": COMPLETION_GATE, **identifiers, "assignment_id": receipt.get("assignment_id")})
        or receipt.get("message_sha256") != hashlib.sha256(message.encode()).hexdigest()
        or not completion_receipt_accepted(run_dir, **identifiers)):
        raise RuntimeError("No accepted report receipt for this exact direct parent and child.")
    parent, child = identifiers["parent_pane_id"], identifiers["child_pane_id"]
    if validate_parent_pane(parent) == validate_parent_pane(child):
        raise RuntimeError("A terminal report must come from a distinct child.")
    ledger = (run_dir / "children.tsv").read_text(encoding="utf-8")
    if not any(
        len(row) == 10 and row[0] == child and row[8:] == [identifiers["child_generation"], COMPLETION_GATE]
        for row in (line.split("\t") for line in ledger.splitlines())
    ):
        raise RuntimeError("No matching registered launcher child for this report.")
    _verify_completion_agent(
        _herdr_completion_rpc(endpoint, "get", parent), parent, identifiers["parent_generation"], "agent_info",
    )
    return child


def _register_child(
    run_dir: Path, pane_id: str, role: str, tab_id: str, generation: str,
    *, parent: str, parent_generation: str, socket_path: Path,
) -> str:
    ledger = run_dir / "children.tsv"
    row = "\t".join((pane_id, role, tab_id, "", "", "", "running", "", generation, COMPLETION_GATE))
    with (run_dir / ".parent-ledger.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
        for line in lines:
            fields = line.split("\t")
            if fields[0] == pane_id and len(fields) >= 9 and fields[8] == generation:
                raise RuntimeError("Child generation is already registered; reconcile without relaunching.")
        token = secrets.token_hex(32)
        scope = _completion_scope(parent, parent_generation, pane_id, generation)
        assignment_id = secrets.token_hex(32)
        _write_private_json(_assignment_path(run_dir, scope), {"assignment_id": assignment_id})
        _write_private_json(
            run_dir / f"launch-{token}.json", {**scope, "assignment_id": assignment_id, "socket": str(socket_path.resolve())},
        )
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=run_dir, delete=False) as out:
            tmp = Path(out.name)
            try:
                out.write("\n".join([*lines, row]) + "\n")
                out.flush()
                os.replace(tmp, ledger)
            finally:
                tmp.unlink(missing_ok=True)
        return token


def assign_child(run_dir: Path, launch_token: str) -> str:
    """Issue a new completion helper before sending a new assignment to an existing child."""
    _check_private_dir(run_dir)
    if not _LAUNCH_TOKEN.fullmatch(launch_token):
        raise ValueError("Invalid launch token.")
    launch = _read_private_json(run_dir / f"launch-{launch_token}.json")
    keys = ("parent_pane_id", "parent_generation", "child_pane_id", "child_generation")
    values = {key: launch.get(key) for key in keys}
    endpoint = launch.get("socket")
    if (not all(isinstance(value, str) and value for value in values.values())
        or not isinstance(endpoint, str) or not Path(endpoint).is_absolute()
        or os.environ.get("HERDR_PANE_ID") != values["parent_pane_id"]):
        raise ParentRouteError("Only the exact direct parent can assign this child.")
    _verify_completion_agent(_herdr_completion_rpc(endpoint, "get", values["parent_pane_id"]),
                             values["parent_pane_id"], values["parent_generation"], "agent_info")
    _verify_completion_agent(_herdr_completion_rpc(endpoint, "get", values["child_pane_id"]),
                             values["child_pane_id"], values["child_generation"], "agent_info")
    scope = {"version": COMPLETION_GATE, **values}
    with (run_dir / ".parent-ledger.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if launch.get("assignment_id") != _current_assignment(run_dir, scope):
            raise RuntimeError("Cannot reassign from a stale launch token.")
        ledger = run_dir / "children.tsv"
        rows = [line.split("\t") for line in ledger.read_text(encoding="utf-8").splitlines()]
        matches = [row for row in rows if len(row) == 10 and row[0] == values["child_pane_id"]
                   and row[8:] == [values["child_generation"], COMPLETION_GATE]]
        if len(matches) != 1:
            raise RuntimeError("Cannot identify exact tagged child in ledger.")
        token, assignment_id = secrets.token_hex(32), secrets.token_hex(32)
        _write_private_json(run_dir / f"launch-{token}.json", {
            **scope, "assignment_id": assignment_id, "socket": endpoint,
        })
        _write_private_json(_assignment_path(run_dir, scope), {"assignment_id": assignment_id})
        matches[0][6] = "running"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=run_dir, delete=False) as out:
            tmp = Path(out.name)
            try:
                out.write("\n".join("\t".join(row) for row in rows) + "\n")
                out.flush()
                os.fsync(out.fileno())
                os.replace(tmp, ledger)
            finally:
                tmp.unlink(missing_ok=True)
    return token


def _ensure_watcher(run_dir: Path, parent: str, generation: str, socket_path: Path, run_id: str) -> None:
    from herdr_master.pipeline.child_watch import watcher_ready

    if (run_dir / "child-watch.stop").exists():
        raise RuntimeError("Parent stopped this watcher run; reconcile before launching more children.")
    _write_private_json(run_dir / "launcher-run.json", {
        "version": 1, "run_id": run_id, "parent_pane_id": parent, "parent_generation": generation,
    })
    # Set both aliases to the pinned endpoint, overriding any inherited mismatch.
    env = {**os.environ, "HERDR_SOCKET_PATH": str(socket_path), "HERDR_SOCKET": str(socket_path)}
    with (run_dir / "child-watch.log").open("ab") as log:
        # The watcher CLI holds its own per-run lock. Each handoff can ensure it,
        # including after a previous instance exited on an all-terminal ledger.
        process = subprocess.Popen(
            [sys.executable, "-m", "herdr_master.pipeline.child_watch", "--run-dir", str(run_dir),
             "--parent-pane", parent, "--parent-generation", generation,
             "--run-id", run_id, "--startup-timeout", "5"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if watcher_ready(run_dir, run_id=run_id, parent_pane_id=parent, parent_generation=generation):
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    raise RuntimeError("Watcher did not establish bounded run-scoped ownership; reconcile child before retrying.")


def _record_ambiguous(run_dir: Path | None, pane_id: str, phase: str) -> None:
    if run_dir is not None:
        with (run_dir / "launch-uncertainty.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"pane_id": pane_id, "phase": phase}) + "\n")


async def launch_and_prompt(
    client: Any, *, pane_id: str, tab_id: str | None, socket_path: Path,
    parent_pane_id: str | None, delegated: bool, name: str, kind: str,
    args: list[str], cwd: str, prompt: str, bound_parent_generation: str | None = None,
) -> Path | None:
    """Start one agent, attach its watcher only after confirmation, and prompt once."""
    parent = resolve_parent_route(parent_pane_id, delegated=delegated)
    if parent == pane_id:
        raise ParentRouteError("Cannot start a child in its direct parent's pane.")
    first_prompt = compose_first_prompt(prompt, parent_pane_id=parent)
    if kind == "agy":
        require_existing_agy_trust(cwd, parent_pane_id=parent)
    run_dir = None
    parent_generation = None
    completion_token = None
    run_id = secrets.token_hex(16)
    if parent is not None:
        if not _PANE_ID.fullmatch(pane_id):
            raise ParentRouteError("Cannot track a child without a canonical Herdr pane ID.")
        parent_agent = _agent(await _call(client.get_agent, parent), parent)
        parent_identity = _launch_identity(parent_agent, parent)
        if parent_identity is None:
            raise ParentRouteError(f"Cannot verify the current direct-parent agent in {parent}.")
        _, parent_generation = parent_identity
        if bound_parent_generation is not None and not _matches_generation(bound_parent_generation, parent_generation):
            raise ParentRouteError("Direct parent changed during allocation; no agent started.")
        parent_generation = bound_parent_generation or parent_generation
        run_dir = _watch_run_dir(socket_path, parent, parent_generation, run_id)
        if (run_dir / "child-watch.stop").exists():
            raise RuntimeError("Parent stopped this watcher run; reconcile before launching more children.")
    phase = "start"
    try:
        result = await _call(client.start_agent, name=name, kind=kind, pane_id=pane_id,
                             args=args, timeout_ms=30000, recover_name_collision=False)
        phase = "readiness"
        record = _agent(result, pane_id)
        confirmed_generation = None
        for attempt in range(31):
            identity = _launch_identity(record, pane_id) if run_dir is not None else None
            if identity is not None:
                confirmed_tab, generation = identity
                if tab_id is not None and tab_id != confirmed_tab:
                    raise RuntimeError("Child tab differs from the allocated tab; not replaying launch.")
                if confirmed_generation is not None and not _matches_generation(confirmed_generation, generation):
                    raise RuntimeError("Child identity changed before initial prompt; not replaying launch.")
                if confirmed_generation is None:
                    phase = "watcher_handoff"
                    assert run_dir is not None and parent is not None and parent_generation is not None
                    completion_token = _register_child(
                        run_dir, pane_id, name, confirmed_tab, generation,
                        parent=parent, parent_generation=parent_generation, socket_path=socket_path,
                    )
                    _ensure_watcher(run_dir, parent, parent_generation, socket_path, run_id)
                    confirmed_generation = generation
                    phase = "readiness"
            if record.get("agent_status") == "blocked":
                raise RuntimeError(f"Agent in {pane_id} is blocked during startup; no dialog was answered.")
            if (record.get("agent_status") in ("idle", "working", "done")
                and record.get("interactive_ready") is not False
                and record.get("exited") is not True
                and (run_dir is None or identity is not None)):
                break
            if attempt:
                await asyncio.sleep(0.5)
            try:
                record = _agent(await _call(client.get_agent, pane_id), pane_id)
            except Exception:
                record = {}
        else:
            raise RuntimeError(f"Could not verify readiness for {pane_id}; not submitting an initial prompt.")
        if parent is not None:
            current = _agent(await _call(client.get_agent, parent), parent)
            current_identity = _launch_identity(current, parent)
            if current_identity is None or not _matches_generation(parent_generation, current_identity[1]):
                raise ParentRouteError("Direct parent changed during startup; no task prompt was sent.")
            assert run_dir is not None and completion_token is not None
            completion = _completion_directive(run_dir, completion_token)
            routing, separator, body = first_prompt.partition("\n\n")
            first_prompt = f"{routing}{separator}{completion}\n\n{body}"
        phase = "first_prompt"
        await _call(client.prompt_agent, pane_id, first_prompt, max_attempts=1)
        return run_dir
    except (Exception, asyncio.CancelledError) as err:
        try:
            _record_ambiguous(run_dir, pane_id, phase)
        except OSError:
            pass
        err.add_note(f"Launch {phase} may have reached {pane_id}; reconcile it before retrying.")
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deliver a launcher-bound Herdr terminal report.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    complete = subparsers.add_parser("complete")
    complete.add_argument("--run-dir", required=True, type=Path)
    complete.add_argument("--launch-token", required=True)
    complete.add_argument("--message", required=True)
    assign = subparsers.add_parser("assign")
    assign.add_argument("--run-dir", required=True, type=Path)
    assign.add_argument("--launch-token", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--run-dir", required=True, type=Path)
    verify.add_argument("--report-id", required=True)
    verify.add_argument("--message", required=True, help="Exact full received HERDR TERMINAL REPORT payload")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            child = verify_terminal_completion(args.run_dir, args.report_id, args.message)
            print(f"Verified Herdr terminal report acceptance: {args.report_id} ({child})")
        elif args.command == "assign":
            token = assign_child(args.run_dir, args.launch_token)
            print(_completion_directive(args.run_dir, token))
        else:
            report_id = send_terminal_completion(args.run_dir, args.launch_token, args.message)
            print(f"Herdr accepted the terminal report: {report_id}")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Completion not confirmed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
