"""AI Council multi-agent planning deliberation state machine and protocol guard.

Enforces the canonical multi-round Council protocol:
- Run initialization with pinned roster (coordinator, scout, >=2 members)
- Scout context prerequisite
- Round 0 initial plan drafting across all members
- Up to 5 feedback cycles (rounds 1-5) with cross-review and unified feedback
- Immediate stop on verified unanimous consensus
- 1-round fallback vote if cycle 5 concludes without consensus (deterministic tie-break)
- Hard rejection of round 6 and all out-of-order or post-finalization mutations
- Atomic, serialized state persistence via stdlib file locking
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Iterator, Sequence

from herdr_master.core.config import CouncilConfig, CouncilMember, HarnessBinding


SCHEMA_VERSION = 1
MAX_FEEDBACK_ROUNDS = 5
SAFE_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")


class CouncilError(Exception):
    """Base exception for all Council protocol and state machine errors."""


class CouncilTransitionError(CouncilError):
    """Raised when an out-of-sequence or invalid state transition is attempted."""


class CouncilValidationError(CouncilError):
    """Raised when validation of roster, member, round, or artifact fails."""


class CouncilSecurityError(CouncilError):
    """Raised when path traversal or unauthorized identity access is attempted."""


class CouncilPhase(str, Enum):
    """Lifecycle phases of an AI Council deliberation run."""

    INITIALIZED = "initialized"
    SCOUTED = "scouted"
    DRAFTING = "drafting"
    ROUND_FEEDBACK = "round_feedback"
    ROUND_REVISION = "round_revision"
    CONSENSUS_REACHED = "consensus_reached"
    VOTING = "voting"
    VOTED = "voted"
    FINALIZED = "finalized"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _lock_run(run_dir: Path) -> Iterator[None]:
    """Acquire exclusive file lock for run directory state transitions."""
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_file = run_dir / ".council_lock"
    with open(lock_file, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _state_file(run_dir: Path) -> Path:
    return run_dir / "council_state.json"


def _load_state_unlocked(run_dir: Path) -> dict[str, Any]:
    path = _state_file(run_dir)
    if not path.is_file():
        raise CouncilTransitionError(f"No active council run found at '{run_dir}'. Call init() first.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise CouncilError(f"Failed to load council state from '{path}': {exc}") from exc


def _save_state_unlocked(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = _utc_now_iso()
    path = _state_file(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    temp_file: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=run_dir,
            prefix=".council_state.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_file = Path(f.name)
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, path)
        temp_file = None
    finally:
        if temp_file is not None and temp_file.exists():
            try:
                temp_file.unlink()
            except OSError:
                pass


def _validate_safe_id(label: str, role_name: str = "member") -> str:
    text = str(label).strip()
    if not text:
        raise CouncilValidationError(f"{role_name.capitalize()} identity must not be blank.")
    if "/" in text or "\\" in text or ".." in text:
        raise CouncilSecurityError(
            f"Path traversal characters detected in {role_name} identity '{label}'."
        )
    if not SAFE_ID_REGEX.match(text):
        raise CouncilValidationError(
            f"{role_name.capitalize()} identity '{label}' contains invalid characters. "
            "Use alphanumeric characters, dashes, and underscores only."
        )
    return text


def _validate_file_path(path_str: str | Path, description: str) -> Path:
    p = Path(path_str).resolve()
    if not p.is_file():
        raise CouncilValidationError(f"{description} file does not exist or is not a regular file: '{path_str}'.")
    return p


def _check_not_finalized(state: dict[str, Any], action: str) -> None:
    if state.get("phase") == CouncilPhase.FINALIZED.value:
        raise CouncilTransitionError(f"Cannot {action}: Council run is finalized and sealed against mutations.")


def _member_ids(state: dict[str, Any]) -> list[str]:
    return [m["id"] for m in state["roster"]["members"]]


def _member_labels_lower(state: dict[str, Any]) -> dict[str, str]:
    return {m["id"].lower(): m["id"] for m in state["roster"]["members"]}


def _get_candidate_plans(state: dict[str, Any], round_number: int) -> list[str]:
    """Return valid candidate plan identifiers for feedback or voting."""
    candidates: list[str] = []
    # If round_number's revised plans exist (e.g. for voting at the end of round 5)
    r_curr = state.get("rounds", {}).get(str(round_number), {})
    revised_curr = r_curr.get("revised_plans", {})
    if revised_curr:
        for m_id, plan_info in revised_curr.items():
            candidates.append(plan_info.get("plan_id", f"{m_id}_v{round_number}"))
            candidates.append(m_id)
        return candidates

    # If round > 1, candidates are previous round's revised plans
    if round_number > 1:
        prev_round = str(round_number - 1)
        r_prev = state.get("rounds", {}).get(prev_round, {})
        revised = r_prev.get("revised_plans", {})
        if revised:
            for m_id, plan_info in revised.items():
                candidates.append(plan_info.get("plan_id", f"{m_id}_v{prev_round}"))
                candidates.append(m_id)
            return candidates

    # Round 0 initial plans
    r0 = state.get("rounds", {}).get("0", {}).get("plans", {})
    for m_id, plan_info in r0.items():
        candidates.append(plan_info.get("plan_id", f"{m_id}_v0"))
        candidates.append(m_id)
    return candidates


# ============================================================================
# Public State-Machine Python API
# ============================================================================


def init(
    run_dir: str | Path | None = None,
    roster: CouncilConfig | Sequence[CouncilMember | HarnessBinding | dict[str, Any] | str] | str | dict[str, Any] | None = None,
    specifications: str = "",
    coordinator: str | HarnessBinding | None = None,
    scout: str | HarnessBinding | None = None,
    spec_file: str | Path | None = None,
) -> dict[str, Any]:
    """Initialize a new Council deliberation run with a pinned roster and specifications."""
    if run_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target_dir = Path.cwd() / ".orchestration" / f"council-{timestamp}"
    else:
        target_dir = Path(run_dir).resolve()

    target_dir.mkdir(parents=True, exist_ok=True)

    with _lock_run(target_dir):
        state_file = _state_file(target_dir)
        if state_file.is_file():
            try:
                existing = _load_state_unlocked(target_dir)
                if existing.get("phase"):
                    raise CouncilTransitionError(
                        f"Council run at '{target_dir}' is already initialized (phase: {existing.get('phase')})."
                    )
            except CouncilError:
                raise
            except Exception:
                pass

        # Parse coordinator
        coord_label = "coordinator"
        if coordinator is not None:
            coord_label = getattr(coordinator, "label", None) or getattr(coordinator, "name", None) or str(coordinator)
        coord_id = _validate_safe_id(coord_label, "coordinator")

        # Parse scout
        scout_label = "scout"
        if scout is not None:
            scout_label = getattr(scout, "label", None) or getattr(scout, "name", None) or str(scout)
        scout_id = _validate_safe_id(scout_label, "scout")

        # Parse members
        parsed_members: list[dict[str, Any]] = []
        if isinstance(roster, CouncilConfig):
            if coordinator is None and roster.coordinator:
                coord_id = _validate_safe_id(roster.coordinator.label or "coordinator", "coordinator")
            if scout is None and roster.scout:
                scout_id = _validate_safe_id(roster.scout.label or "scout", "scout")
            for idx, m in enumerate(roster.members):
                if m.enabled:
                    m_label = _validate_safe_id(m.label or f"member-{idx + 1}", "member")
                    parsed_members.append({"id": m_label, "label": m_label, "order": m.order, "enabled": True})
        elif isinstance(roster, str):
            parts = [p.strip() for p in roster.split(",") if p.strip()]
            for idx, part in enumerate(parts):
                m_label = _validate_safe_id(part, "member")
                parsed_members.append({"id": m_label, "label": m_label, "order": idx, "enabled": True})
        elif isinstance(roster, dict) and "members" in roster:
            if coordinator is None and "coordinator" in roster:
                coord_id = _validate_safe_id(str(roster["coordinator"]), "coordinator")
            if scout is None and "scout" in roster:
                scout_id = _validate_safe_id(str(roster["scout"]), "scout")
            for idx, item in enumerate(roster["members"]):
                label = item.get("label") or item.get("id") or item.get("name") if isinstance(item, dict) else str(item)
                m_label = _validate_safe_id(label, "member")
                parsed_members.append({"id": m_label, "label": m_label, "order": idx, "enabled": True})
        elif isinstance(roster, Sequence):
            for idx, item in enumerate(roster):
                if isinstance(item, str):
                    m_label = _validate_safe_id(item, "member")
                    parsed_members.append({"id": m_label, "label": m_label, "order": idx, "enabled": True})
                elif hasattr(item, "label"):
                    m_label = _validate_safe_id(item.label or f"member-{idx + 1}", "member")
                    parsed_members.append({"id": m_label, "label": m_label, "order": getattr(item, "order", idx), "enabled": getattr(item, "enabled", True)})
                elif isinstance(item, dict):
                    label = item.get("label") or item.get("id") or item.get("name") or f"member-{idx + 1}"
                    m_label = _validate_safe_id(label, "member")
                    parsed_members.append({"id": m_label, "label": m_label, "order": item.get("order", idx), "enabled": item.get("enabled", True)})
                else:
                    m_label = _validate_safe_id(str(item), "member")
                    parsed_members.append({"id": m_label, "label": m_label, "order": idx, "enabled": True})
        elif roster is None:
            # Default 2 members
            parsed_members = [
                {"id": "member-1", "label": "member-1", "order": 0, "enabled": True},
                {"id": "member-2", "label": "member-2", "order": 1, "enabled": True},
            ]

        # Enforce roster invariants
        if len(parsed_members) < 2:
            raise CouncilValidationError(
                f"Council requires at least 2 members, but found {len(parsed_members)}."
            )

        seen_labels: set[str] = set()
        for m in parsed_members:
            m_key = m["id"].lower()
            if m_key in seen_labels:
                raise CouncilValidationError(f"Duplicate member label '{m['id']}' in roster.")
            if m_key == coord_id.lower():
                raise CouncilValidationError(f"Member label '{m['id']}' collides with coordinator label.")
            if m_key == scout_id.lower():
                raise CouncilValidationError(f"Member label '{m['id']}' collides with Scout label.")
            seen_labels.add(m_key)

        # Specifications handling
        spec_text = specifications.strip()
        if spec_file is not None:
            sf = _validate_file_path(spec_file, "Specifications")
            if not spec_text:
                spec_text = sf.read_text(encoding="utf-8")
        if spec_text:
            spec_path = target_dir / "specifications.md"
            if not spec_path.is_file():
                spec_path.write_text(spec_text, encoding="utf-8")

        # Subdirectories
        (target_dir / "plans").mkdir(parents=True, exist_ok=True)
        (target_dir / "feedback").mkdir(parents=True, exist_ok=True)

        state: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_dir": str(target_dir),
            "phase": CouncilPhase.INITIALIZED.value,
            "created_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
            "specifications": spec_text,
            "roster": {
                "coordinator": coord_id,
                "scout": scout_id,
                "members": parsed_members,
            },
            "scout_context": None,
            "current_round": None,
            "rounds": {},
            "consensus": None,
            "votes": {},
            "vote_results": None,
            "winning_plan": None,
            "final_plan_file": None,
        }
        _save_state_unlocked(target_dir, state)
        return {"ok": True, "run_dir": str(target_dir), "phase": state["phase"], "roster": state["roster"]}


def record_scout_context(run_dir: str | Path, context_file: str | Path) -> dict[str, Any]:
    """Record Scout codebase discovery context prerequisite."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, "record Scout context")

        ctx_path = _validate_file_path(context_file, "Scout context")
        # Persist a run-scoped copy if not already inside run_dir
        dest = r_dir / "context.md"
        if ctx_path.resolve() != dest.resolve():
            shutil.copy2(ctx_path, dest)
            ctx_path = dest

        state["scout_context"] = {
            "file": str(ctx_path),
            "recorded_at": _utc_now_iso(),
        }
        if state["phase"] == CouncilPhase.INITIALIZED.value:
            state["phase"] = CouncilPhase.SCOUTED.value

        _save_state_unlocked(r_dir, state)
        return {"ok": True, "phase": state["phase"], "context_file": str(ctx_path)}


def submit_initial_plan(run_dir: str | Path, member_id: str, plan_file: str | Path) -> dict[str, Any]:
    """Submit an independently drafted initial plan (Round 0) from a council member."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, "submit initial plan")

        if state.get("scout_context") is None:
            raise CouncilTransitionError(
                "Cannot submit initial plan: Scout context has not been recorded yet. "
                "Scout must discover context before drafting."
            )

        if state["phase"] not in {CouncilPhase.SCOUTED.value, CouncilPhase.DRAFTING.value}:
            raise CouncilTransitionError(
                f"Cannot submit initial plan in phase '{state['phase']}'. Round 0 drafting has concluded."
            )

        safe_m = _validate_safe_id(member_id, "member")
        members = _member_ids(state)
        members_map = _member_labels_lower(state)
        if safe_m.lower() not in members_map:
            known = ", ".join(members)
            raise CouncilValidationError(f"Unknown member '{member_id}'. Expected one of: {known}.")
        canonical_m = members_map[safe_m.lower()]

        p_path = _validate_file_path(plan_file, f"Initial plan for {canonical_m}")

        # Round 0 tracking
        r0 = state["rounds"].setdefault("0", {
            "round": 0,
            "type": "drafting",
            "status": "in_progress",
            "plans": {},
        })

        if canonical_m in r0["plans"]:
            raise CouncilValidationError(
                f"Member '{canonical_m}' has already submitted an initial plan for Round 0."
            )

        # Copy to run-scoped storage
        dest = r_dir / "plans" / f"{canonical_m}_v0.md"
        if p_path.resolve() != dest.resolve():
            shutil.copy2(p_path, dest)
            p_path = dest

        plan_id = f"{canonical_m}_v0"
        r0["plans"][canonical_m] = {
            "member_id": canonical_m,
            "plan_id": plan_id,
            "file": str(p_path),
            "revision": 0,
            "timestamp": _utc_now_iso(),
        }
        state["current_round"] = 0
        state["phase"] = CouncilPhase.DRAFTING.value

        # Check if all members submitted
        missing = [m for m in members if m not in r0["plans"]]
        if not missing:
            r0["status"] = "completed"

        _save_state_unlocked(r_dir, state)
        return {
            "ok": True,
            "round": 0,
            "member_id": canonical_m,
            "plan_id": plan_id,
            "file": str(p_path),
            "drafting_complete": len(missing) == 0,
            "missing_members": missing,
        }


def open_round(run_dir: str | Path, round_number: int) -> dict[str, Any]:
    """Open a round (Round 0 drafting or feedback cycle 1 through 5). Enforces cycle caps."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, f"open Round {round_number}")

        if round_number > MAX_FEEDBACK_ROUNDS:
            raise CouncilValidationError(
                f"Maximum of {MAX_FEEDBACK_ROUNDS} feedback cycles allowed. "
                f"Round {round_number} is rejected."
            )
        if round_number < 0:
            raise CouncilValidationError(f"Invalid round number {round_number}.")

        if (state.get("consensus") or {}).get("achieved") is True:
            raise CouncilTransitionError(
                "Consensus has already been reached. No further rounds can be opened."
            )

        members = _member_ids(state)

        # Opening round 0
        if round_number == 0:
            if state.get("scout_context") is None:
                raise CouncilTransitionError(
                    "Cannot open Round 0: Scout context must be recorded first."
                )
            state["current_round"] = 0
            state["phase"] = CouncilPhase.DRAFTING.value
            r0 = state["rounds"].setdefault("0", {
                "round": 0,
                "type": "drafting",
                "status": "in_progress",
                "plans": {},
            })
            _save_state_unlocked(r_dir, state)
            return {"ok": True, "round": 0, "status": r0["status"]}

        # Feedback rounds 1 through 5
        # Round 1 requires Round 0 complete
        if round_number == 1:
            r0_plans = state.get("rounds", {}).get("0", {}).get("plans", {})
            missing = [m for m in members if m not in r0_plans]
            if missing:
                raise CouncilTransitionError(
                    f"Cannot open Round 1: Round 0 drafting is incomplete. "
                    f"Missing initial plans from: {', '.join(missing)}."
                )
        else:
            # Round N > 1 requires Round N - 1 revised plans complete
            prev_round_str = str(round_number - 1)
            prev_round_data = state.get("rounds", {}).get(prev_round_str)
            if not prev_round_data:
                raise CouncilTransitionError(
                    f"Cannot open Round {round_number}: Round {round_number - 1} was never opened."
                )
            revised = prev_round_data.get("revised_plans", {})
            missing = [m for m in members if m not in revised]
            if missing:
                raise CouncilTransitionError(
                    f"Cannot open Round {round_number}: Round {round_number - 1} revisions are incomplete. "
                    f"Missing revised plans from: {', '.join(missing)}."
                )

        # Enforce consecutive round progression
        last_round = state.get("current_round")
        expected_round = (last_round + 1) if last_round is not None else 1
        if round_number != expected_round:
            raise CouncilTransitionError(
                f"Cannot open Round {round_number}; expected consecutive Round {expected_round}."
            )

        state["current_round"] = round_number
        state["phase"] = CouncilPhase.ROUND_FEEDBACK.value
        state["rounds"][str(round_number)] = {
            "round": round_number,
            "type": "feedback",
            "status": "open",
            "feedback": {},
            "consensus": None,
            "unified_feedback": None,
            "revised_plans": {},
        }
        _save_state_unlocked(r_dir, state)
        return {"ok": True, "round": round_number, "status": "open", "phase": state["phase"]}


def submit_feedback(
    run_dir: str | Path,
    round_number: int,
    member_id: str,
    feedback_file: str | Path,
    supported_plan_id: str,
) -> dict[str, Any]:
    """Submit a council member's cross-review feedback and explicit supported plan choice."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, "submit feedback")

        if state.get("current_round") != round_number:
            raise CouncilTransitionError(
                f"Cannot submit feedback for Round {round_number}: Active round is {state.get('current_round')}."
            )
        if state["phase"] != CouncilPhase.ROUND_FEEDBACK.value:
            raise CouncilTransitionError(
                f"Cannot submit feedback in phase '{state['phase']}'. Round is not open for feedback."
            )

        safe_m = _validate_safe_id(member_id, "member")
        members = _member_ids(state)
        members_map = _member_labels_lower(state)
        if safe_m.lower() not in members_map:
            raise CouncilValidationError(f"Unknown member '{member_id}'. Expected one of: {', '.join(members)}.")
        canonical_m = members_map[safe_m.lower()]

        f_path = _validate_file_path(feedback_file, f"Feedback from {canonical_m}")

        round_data = state["rounds"].get(str(round_number))
        if not round_data:
            raise CouncilTransitionError(f"Round {round_number} is not recorded.")

        if canonical_m in round_data["feedback"]:
            raise CouncilValidationError(
                f"Member '{canonical_m}' has already submitted feedback for Round {round_number}."
            )

        # Validate supported_plan_id against valid candidate plans
        candidates = _get_candidate_plans(state, round_number)
        norm_supported = supported_plan_id.strip()
        # Accept either plan_id (e.g. member-1_v0) or member label (e.g. member-1)
        valid_candidates = set(c.lower() for c in candidates)
        if norm_supported.lower() not in valid_candidates:
            raise CouncilValidationError(
                f"Invalid supported plan '{supported_plan_id}' for Round {round_number}. "
                f"Expected one of candidate plans: {', '.join(sorted(set(candidates)))}."
            )

        # Resolve to canonical member/plan ID
        for cand in candidates:
            if cand.lower() == norm_supported.lower():
                norm_supported = cand
                break

        dest = r_dir / "feedback" / f"{canonical_m}_round_{round_number}.md"
        if f_path.resolve() != dest.resolve():
            shutil.copy2(f_path, dest)
            f_path = dest

        round_data["feedback"][canonical_m] = {
            "member_id": canonical_m,
            "feedback_file": str(f_path),
            "supported_plan_id": norm_supported,
            "timestamp": _utc_now_iso(),
        }

        missing = [m for m in members if m not in round_data["feedback"]]

        _save_state_unlocked(r_dir, state)
        return {
            "ok": True,
            "round": round_number,
            "member_id": canonical_m,
            "supported_plan_id": norm_supported,
            "feedback_complete": len(missing) == 0,
            "missing_feedback": missing,
        }


def check_consensus(run_dir: str | Path, round_number: int) -> dict[str, Any]:
    """Evaluate whether all enabled members explicitly support the exact same plan revision."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, "check consensus")

        round_data = state.get("rounds", {}).get(str(round_number))
        if not round_data:
            raise CouncilTransitionError(f"Round {round_number} does not exist.")

        members = _member_ids(state)
        fb = round_data.get("feedback", {})
        missing = [m for m in members if m not in fb]
        if missing:
            raise CouncilTransitionError(
                f"Cannot evaluate consensus for Round {round_number}: Feedback incomplete. "
                f"Missing submissions from: {', '.join(missing)}."
            )

        selections = {m: fb[m]["supported_plan_id"] for m in members}
        unique_choices = set(selections.values())

        if len(unique_choices) == 1:
            winning_plan = unique_choices.pop()
            consensus_info = {
                "achieved": True,
                "round": round_number,
                "plan_id": winning_plan,
                "supported_by": list(members),
                "timestamp": _utc_now_iso(),
            }
            round_data["consensus"] = consensus_info
            state["consensus"] = consensus_info
            state["winning_plan"] = winning_plan
            state["phase"] = CouncilPhase.CONSENSUS_REACHED.value
            _save_state_unlocked(r_dir, state)
            return {"consensus": True, "round": round_number, "plan_id": winning_plan, "supported_by": list(members)}

        # Consensus not achieved
        consensus_info = {
            "achieved": False,
            "round": round_number,
            "selections": selections,
            "timestamp": _utc_now_iso(),
        }
        round_data["consensus"] = consensus_info
        _save_state_unlocked(r_dir, state)
        return {"consensus": False, "round": round_number, "selections": selections}


def submit_unified_feedback(
    run_dir: str | Path,
    round_number: int,
    unified_feedback_file: str | Path,
) -> dict[str, Any]:
    """Record coordinator unified feedback synthesizing member reviews for a round."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, "submit unified feedback")

        if state.get("current_round") != round_number:
            raise CouncilTransitionError(
                f"Cannot submit unified feedback for Round {round_number}: Active round is {state.get('current_round')}."
            )

        if (state.get("consensus") or {}).get("achieved") is True:
            raise CouncilTransitionError(
                f"Consensus was achieved in Round {round_number}. Unified feedback is not required."
            )

        round_data = state["rounds"].get(str(round_number))
        if not round_data:
            raise CouncilTransitionError(f"Round {round_number} is not recorded.")

        members = _member_ids(state)
        missing_fb = [m for m in members if m not in round_data.get("feedback", {})]
        if missing_fb:
            raise CouncilTransitionError(
                f"Cannot submit unified feedback: Member feedback incomplete for Round {round_number}. "
                f"Missing from: {', '.join(missing_fb)}."
            )

        u_path = _validate_file_path(unified_feedback_file, f"Unified feedback for Round {round_number}")

        dest = r_dir / "feedback" / f"unified_round_{round_number}.md"
        if u_path.resolve() != dest.resolve():
            shutil.copy2(u_path, dest)
            u_path = dest

        round_data["unified_feedback"] = {
            "file": str(u_path),
            "timestamp": _utc_now_iso(),
        }
        state["phase"] = CouncilPhase.ROUND_REVISION.value
        _save_state_unlocked(r_dir, state)
        return {"ok": True, "round": round_number, "unified_feedback_file": str(u_path), "phase": state["phase"]}


def submit_revised_plan(
    run_dir: str | Path,
    round_number: int,
    member_id: str,
    plan_file: str | Path,
    adopted_plan_id: str | None = None,
) -> dict[str, Any]:
    """Submit a council member's revised plan in response to unified feedback."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, "submit revised plan")

        if state.get("current_round") != round_number:
            raise CouncilTransitionError(
                f"Cannot submit revised plan for Round {round_number}: Active round is {state.get('current_round')}."
            )

        if state["phase"] != CouncilPhase.ROUND_REVISION.value:
            raise CouncilTransitionError(
                f"Cannot submit revised plan in phase '{state['phase']}'. "
                "Unified feedback must be recorded before members submit revisions."
            )

        safe_m = _validate_safe_id(member_id, "member")
        members = _member_ids(state)
        members_map = _member_labels_lower(state)
        if safe_m.lower() not in members_map:
            raise CouncilValidationError(f"Unknown member '{member_id}'. Expected one of: {', '.join(members)}.")
        canonical_m = members_map[safe_m.lower()]

        p_path = _validate_file_path(plan_file, f"Revised plan from {canonical_m}")

        round_data = state["rounds"].get(str(round_number))
        if not round_data:
            raise CouncilTransitionError(f"Round {round_number} is not recorded.")

        revised = round_data.setdefault("revised_plans", {})
        if canonical_m in revised:
            raise CouncilValidationError(
                f"Member '{canonical_m}' has already submitted a revised plan for Round {round_number}."
            )

        dest = r_dir / "plans" / f"{canonical_m}_v{round_number}.md"
        if p_path.resolve() != dest.resolve():
            shutil.copy2(p_path, dest)
            p_path = dest

        plan_id = f"{canonical_m}_v{round_number}"
        revised[canonical_m] = {
            "member_id": canonical_m,
            "plan_id": plan_id,
            "file": str(p_path),
            "revision": round_number,
            "adopted_plan_id": adopted_plan_id,
            "timestamp": _utc_now_iso(),
        }

        missing = [m for m in members if m not in revised]
        if not missing:
            round_data["status"] = "completed"

        _save_state_unlocked(r_dir, state)
        return {
            "ok": True,
            "round": round_number,
            "member_id": canonical_m,
            "plan_id": plan_id,
            "file": str(p_path),
            "revisions_complete": len(missing) == 0,
            "missing_members": missing,
        }


def submit_plan(
    run_dir: str | Path,
    member_id: str,
    plan_file: str | Path,
    revision: int = 0,
    adopted_plan_id: str | None = None,
) -> dict[str, Any]:
    """Unified helper for initial (revision=0) or revised (revision>0) plan submissions."""
    if revision == 0:
        return submit_initial_plan(run_dir, member_id, plan_file)
    return submit_revised_plan(run_dir, revision, member_id, plan_file, adopted_plan_id=adopted_plan_id)


def submit_vote(run_dir: str | Path, member_id: str, voted_plan_id: str) -> dict[str, Any]:
    """Submit a ballot in the fallback 1-round vote after 5 cycles without consensus."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        _check_not_finalized(state, "submit vote")

        # Invariant: Voting is ONLY permitted after 5 feedback cycles without consensus
        if (state.get("consensus") or {}).get("achieved") is True:
            raise CouncilTransitionError("Consensus was reached; fallback voting is not permitted.")

        current_round = state.get("current_round") or 0
        if current_round < MAX_FEEDBACK_ROUNDS:
            raise CouncilTransitionError(
                f"Voting is only permitted after {MAX_FEEDBACK_ROUNDS} feedback cycles without consensus "
                f"(currently at round {current_round})."
            )

        r5 = state.get("rounds", {}).get(str(MAX_FEEDBACK_ROUNDS), {})
        members = _member_ids(state)
        missing_revisions = [m for m in members if m not in r5.get("revised_plans", {})]
        if missing_revisions:
            raise CouncilTransitionError(
                f"Cannot vote: Round {MAX_FEEDBACK_ROUNDS} is incomplete. "
                f"Missing revised plans from: {', '.join(missing_revisions)}."
            )

        safe_m = _validate_safe_id(member_id, "voter")

        # Invariant: Coordinator and Scout must not vote
        coord_id = state["roster"]["coordinator"].lower()
        scout_id = state["roster"]["scout"].lower()
        if safe_m.lower() == coord_id or safe_m.lower() == scout_id:
            raise CouncilValidationError(
                f"Coordinator and Scout do not vote. Vote by '{member_id}' is rejected."
            )

        members_map = _member_labels_lower(state)
        if safe_m.lower() not in members_map:
            raise CouncilValidationError(f"Unknown voter '{member_id}'. Expected one of: {', '.join(members)}.")
        canonical_m = members_map[safe_m.lower()]

        votes = state.setdefault("votes", {})
        if canonical_m in votes:
            raise CouncilValidationError(f"Member '{canonical_m}' has already cast a ballot.")

        # Validate candidate
        candidates = _get_candidate_plans(state, MAX_FEEDBACK_ROUNDS)
        valid_candidates = set(c.lower() for c in candidates)
        norm_voted = voted_plan_id.strip()
        if norm_voted.lower() not in valid_candidates:
            raise CouncilValidationError(
                f"Invalid candidate '{voted_plan_id}' in ballot. Expected one of: {', '.join(sorted(set(candidates)))}."
            )

        for cand in candidates:
            if cand.lower() == norm_voted.lower():
                norm_voted = cand
                break

        votes[canonical_m] = norm_voted
        state["phase"] = CouncilPhase.VOTING.value

        # Check if all members have voted
        missing_votes = [m for m in members if m not in votes]
        vote_complete = len(missing_votes) == 0

        tally_result = None
        if vote_complete:
            # Tally votes
            tally = Counter(votes.values())
            max_count = max(tally.values())
            tied_candidates = [c for c, count in tally.items() if count == max_count]

            if len(tied_candidates) == 1:
                winner = tied_candidates[0]
                tie_break = None
            else:
                # Deterministic tie-break based on roster order
                roster_members = state["roster"]["members"]

                def member_rank(cand: str) -> int:
                    for m in roster_members:
                        m_id = m["id"]
                        if cand == m_id or cand.startswith(f"{m_id}_"):
                            return m.get("order", 999)
                    return 9999

                sorted_candidates = sorted(tied_candidates, key=member_rank)
                winner = sorted_candidates[0]
                tie_break = (
                    f"Tie among {', '.join(tied_candidates)} ({max_count} votes each). "
                    f"Deterministic tie-break selected '{winner}' based on configured roster order."
                )

            tally_result = {
                "tallies": dict(tally),
                "winner": winner,
                "tie_break": tie_break,
                "votes_cast": len(votes),
            }
            state["vote_results"] = tally_result
            state["winning_plan"] = winner
            state["phase"] = CouncilPhase.VOTED.value

        _save_state_unlocked(r_dir, state)
        return {
            "ok": True,
            "voter": canonical_m,
            "ballot": norm_voted,
            "vote_complete": vote_complete,
            "missing_votes": missing_votes,
            "tally_result": tally_result,
        }


def finalize(run_dir: str | Path, output_file: str | Path | None = None) -> dict[str, Any]:
    """Finalize the Council deliberation and generate the authoritative final_plan.md artifact."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)

        # Idempotent terminal read
        if state["phase"] == CouncilPhase.FINALIZED.value:
            return {
                "ok": True,
                "finalized": True,
                "winning_plan": state.get("winning_plan"),
                "outcome": (state.get("consensus") or {}).get("achieved") and "UNANIMOUS_CONSENSUS" or "MAJORITY_VOTE",
                "rounds_used": state.get("current_round", 0),
                "final_plan_file": state.get("final_plan_file"),
            }

        # Terminal prerequisites: Must be either CONSENSUS_REACHED or VOTED
        if state["phase"] not in {CouncilPhase.CONSENSUS_REACHED.value, CouncilPhase.VOTED.value}:
            raise CouncilTransitionError(
                f"Cannot finalize council run in phase '{state['phase']}'. "
                "Deliberation must reach unanimous consensus or complete voting first."
            )

        winning_plan = state.get("winning_plan")
        if not winning_plan:
            raise CouncilTransitionError("Cannot finalize: No winning plan resolved.")

        is_consensus = (state.get("consensus") or {}).get("achieved") is True
        outcome = "UNANIMOUS_CONSENSUS" if is_consensus else "MAJORITY_VOTE"
        rounds_used = state.get("current_round", 0)

        # Locate plan body
        plan_content = ""
        # Search for file matching winning_plan
        for r_num, r_data in state.get("rounds", {}).items():
            for p_info in list(r_data.get("plans", {}).values()) + list(r_data.get("revised_plans", {}).values()):
                if p_info.get("plan_id") == winning_plan or p_info.get("member_id") == winning_plan:
                    f_path = Path(p_info.get("file", ""))
                    if f_path.is_file():
                        plan_content = f_path.read_text(encoding="utf-8")
                        break
            if plan_content:
                break

        # Assemble final_plan.md
        out_path = Path(output_file).resolve() if output_file else r_dir / "final_plan.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = [
            "# AI Council Final Deliberation Plan",
            "",
            f"- **Outcome:** `{outcome}`",
            f"- **Winning Plan:** `{winning_plan}`",
            f"- **Rounds Used:** {rounds_used}",
            f"- **Deliberation Date:** {_utc_now_iso()}",
            f"- **Coordinator:** {state['roster']['coordinator']}",
            f"- **Scout:** {state['roster']['scout']}",
            f"- **Council Members:** {', '.join(_member_ids(state))}",
            "",
        ]

        if not is_consensus and state.get("vote_results"):
            vr = state["vote_results"]
            lines.append("## Voting Results")
            lines.append(f"- **Votes Cast:** {vr.get('votes_cast')}")
            for cand, count in vr.get("tallies", {}).items():
                lines.append(f"  - `{cand}`: {count} vote(s)")
            if vr.get("tie_break"):
                lines.append(f"- **Tie-Break Notice:** {vr['tie_break']}")
            lines.append("")

        lines.append("---")
        lines.append("")
        lines.append("## Approved Technical Plan Specification")
        lines.append("")
        if plan_content.strip():
            lines.append(plan_content.strip())
        else:
            lines.append(f"*(Refer to winning plan draft '{winning_plan}')*")
        lines.append("")

        out_path.write_text("\n".join(lines), encoding="utf-8")

        state["final_plan_file"] = str(out_path)
        state["phase"] = CouncilPhase.FINALIZED.value
        _save_state_unlocked(r_dir, state)

        return {
            "ok": True,
            "finalized": True,
            "winning_plan": winning_plan,
            "outcome": outcome,
            "rounds_used": rounds_used,
            "final_plan_file": str(out_path),
        }


def get_status(run_dir: str | Path) -> dict[str, Any]:
    """Inspect the current status and artifact provenance of a Council deliberation."""
    r_dir = Path(run_dir).resolve()
    with _lock_run(r_dir):
        state = _load_state_unlocked(r_dir)
        phase = state.get("phase", "unknown")
        return {
            "run_dir": str(r_dir),
            "phase": phase,
            "is_terminal": phase == CouncilPhase.FINALIZED.value,
            "current_round": state.get("current_round"),
            "roster": state.get("roster", {}),
            "scout_context_recorded": state.get("scout_context") is not None,
            "consensus": state.get("consensus"),
            "winning_plan": state.get("winning_plan"),
            "vote_results": state.get("vote_results"),
            "final_plan_file": state.get("final_plan_file"),
            "rounds_count": len(state.get("rounds", {})),
            "created_at": state.get("created_at"),
            "updated_at": state.get("updated_at"),
        }
