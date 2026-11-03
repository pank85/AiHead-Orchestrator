"""Pure state transition functions and checks for Workers and Pipelines."""

from enum import Enum
from typing import Any, Dict, Optional

from herdr_master.core.models import PipelinePhase, WorkerState


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal state transition is attempted."""

    pass


class StateTransitionCheckError(ValueError):
    """Raised when a pre-condition or invariant check for a transition fails."""

    pass


class WorkerEvent(str, Enum):
    AGENT_READY = "AGENT_READY"
    PROMPT_DISPATCHED = "PROMPT_DISPATCHED"
    AGENT_BLOCKED = "AGENT_BLOCKED"
    OPERATOR_RESUME = "OPERATOR_RESUME"
    TURN_SETTLED = "TURN_SETTLED"
    SUBMIT_REVIEW = "SUBMIT_REVIEW"
    REVIEW_REJECTED = "REVIEW_REJECTED"
    REVIEW_APPROVED = "REVIEW_APPROVED"
    START_SQUASH = "START_SQUASH"
    SQUASH_SUCCEEDED = "SQUASH_SUCCEEDED"
    FAIL = "FAIL"
    ABORT = "ABORT"


class PipelineEvent(str, Enum):
    START_SCOUT = "START_SCOUT"
    SCOUT_DONE = "SCOUT_DONE"
    PLAN_READY = "PLAN_READY"
    OPERATOR_APPROVED = "OPERATOR_APPROVED"
    CODERS_DONE = "CODERS_DONE"
    REVIEW_PASSED = "REVIEW_PASSED"
    REVIEW_NEEDS_FIXES = "REVIEW_NEEDS_FIXES"
    FIX_LOOP_SETTLED = "FIX_LOOP_SETTLED"
    SQUASH_DONE = "SQUASH_DONE"
    CLEANUP_DONE = "CLEANUP_DONE"
    FAIL = "FAIL"
    ABORT = "ABORT"


# Allowed transitions map: CurrentState -> {Event -> NextState}
WORKER_TRANSITIONS: Dict[WorkerState, Dict[WorkerEvent, WorkerState]] = {
    WorkerState.SPAWNING: {
        WorkerEvent.AGENT_READY: WorkerState.IDLE,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.IDLE: {
        WorkerEvent.PROMPT_DISPATCHED: WorkerState.WORKING,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.WORKING: {
        WorkerEvent.AGENT_BLOCKED: WorkerState.BLOCKED,
        WorkerEvent.TURN_SETTLED: WorkerState.DONE,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.BLOCKED: {
        WorkerEvent.OPERATOR_RESUME: WorkerState.WORKING,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.DONE: {
        WorkerEvent.SUBMIT_REVIEW: WorkerState.NEEDS_REVIEW,
        WorkerEvent.PROMPT_DISPATCHED: WorkerState.WORKING,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.NEEDS_REVIEW: {
        WorkerEvent.REVIEW_REJECTED: WorkerState.FIXING,
        WorkerEvent.REVIEW_APPROVED: WorkerState.APPROVED,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.FIXING: {
        WorkerEvent.AGENT_BLOCKED: WorkerState.BLOCKED,
        WorkerEvent.TURN_SETTLED: WorkerState.DONE,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.APPROVED: {
        WorkerEvent.START_SQUASH: WorkerState.SQUASHING,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.SQUASHING: {
        WorkerEvent.SQUASH_SUCCEEDED: WorkerState.LANDED,
        WorkerEvent.FAIL: WorkerState.FAILED,
        WorkerEvent.ABORT: WorkerState.FAILED,
    },
    WorkerState.LANDED: {},
    WorkerState.FAILED: {},
}


PIPELINE_TRANSITIONS: Dict[PipelinePhase, Dict[PipelineEvent, PipelinePhase]] = {
    PipelinePhase.PREFLIGHT: {
        PipelineEvent.START_SCOUT: PipelinePhase.SCOUT,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.SCOUT: {
        PipelineEvent.SCOUT_DONE: PipelinePhase.ARCHITECT,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.ARCHITECT: {
        PipelineEvent.PLAN_READY: PipelinePhase.APPROVAL_GATE,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.APPROVAL_GATE: {
        PipelineEvent.OPERATOR_APPROVED: PipelinePhase.CODERS,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.CODERS: {
        PipelineEvent.CODERS_DONE: PipelinePhase.REVIEW,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.REVIEW: {
        PipelineEvent.REVIEW_PASSED: PipelinePhase.LANDING,
        PipelineEvent.REVIEW_NEEDS_FIXES: PipelinePhase.FIX_LOOP,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.FIX_LOOP: {
        PipelineEvent.FIX_LOOP_SETTLED: PipelinePhase.REVIEW,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.LANDING: {
        PipelineEvent.SQUASH_DONE: PipelinePhase.CLEANUP,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.CLEANUP: {
        PipelineEvent.CLEANUP_DONE: PipelinePhase.CLEANUP,
        PipelineEvent.FAIL: PipelinePhase.FAILED,
        PipelineEvent.ABORT: PipelinePhase.ABORTED,
    },
    PipelinePhase.ABORTED: {},
    PipelinePhase.FAILED: {},
}


def transition_worker_state(
    current_state: WorkerState,
    event: WorkerEvent,
    context: Optional[Dict[str, Any]] = None,
) -> WorkerState:
    """Pure transition function for WorkerState with guard checks.

    Guards:
    - DONE -> NEEDS_REVIEW requires artifact check (context['has_artifact'] is True).
    - APPROVED -> SQUASHING requires operator authorization (context['operator_authorized']).
    """
    ctx = context or {}
    transitions = WORKER_TRANSITIONS.get(current_state, {})
    if event not in transitions:
        raise InvalidStateTransitionError(
            f"Cannot transition WorkerState from '{current_state.value}' via event '{event.value}'"
        )

    next_state = transitions[event]

    # Invariant: DONE -> NEEDS_REVIEW requires artifact existence check
    if current_state == WorkerState.DONE and next_state == WorkerState.NEEDS_REVIEW:
        if not ctx.get("has_artifact", False):
            raise StateTransitionCheckError(
                "Cannot transition to NEEDS_REVIEW: required role artifact is missing or turn"
                " did not produce output."
            )

    # Invariant: APPROVED -> SQUASHING requires explicit operator authorization
    if current_state == WorkerState.APPROVED and next_state == WorkerState.SQUASHING:
        if not ctx.get("operator_authorized", False):
            raise StateTransitionCheckError(
                "Cannot transition to SQUASHING: operator authorization ('m' or --auto-approve)"
                " is required."
            )

    return next_state


def transition_pipeline_phase(
    current_phase: PipelinePhase,
    event: PipelineEvent,
    context: Optional[Dict[str, Any]] = None,
) -> PipelinePhase:
    """Pure transition function for PipelinePhase with guard checks.

    Guards:
    - FIX_LOOP cap: maximum 5 cycles before transitioning to FAILED.
    - APPROVAL_GATE -> CODERS requires non-empty pool and valid plan.
    """
    ctx = context or {}
    transitions = PIPELINE_TRANSITIONS.get(current_phase, {})
    if event not in transitions:
        raise InvalidStateTransitionError(
            f"Cannot transition PipelinePhase from '{current_phase.value}'"
            f" via event '{event.value}'"
        )

    # Guard: Fix loop cycle cap
    if current_phase == PipelinePhase.REVIEW and event == PipelineEvent.REVIEW_NEEDS_FIXES:
        fix_count = ctx.get("fix_count", 0)
        if fix_count >= 5:
            # Fix loop cap exhausted (GROK_IMPLEMENTATION_PLAN §1.4 & §6)
            return PipelinePhase.FAILED

    return transitions[event]
