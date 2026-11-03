"""Tests for pure state machine transitions and guard checks."""

import pytest

from herdr_master.core.models import PipelinePhase, WorkerState
from herdr_master.core.state_machine import (
    InvalidStateTransitionError,
    PipelineEvent,
    StateTransitionCheckError,
    WorkerEvent,
    transition_pipeline_phase,
    transition_worker_state,
)


def test_worker_lifecycle_happy_path():
    """Verify normal worker lifecycle flow with guard preconditions."""
    state = WorkerState.SPAWNING

    # SPAWNING -> IDLE
    state = transition_worker_state(state, WorkerEvent.AGENT_READY)
    assert state == WorkerState.IDLE

    # IDLE -> WORKING
    state = transition_worker_state(state, WorkerEvent.PROMPT_DISPATCHED)
    assert state == WorkerState.WORKING

    # WORKING -> BLOCKED
    state = transition_worker_state(state, WorkerEvent.AGENT_BLOCKED)
    assert state == WorkerState.BLOCKED

    # BLOCKED -> WORKING
    state = transition_worker_state(state, WorkerEvent.OPERATOR_RESUME)
    assert state == WorkerState.WORKING

    # WORKING -> DONE
    state = transition_worker_state(state, WorkerEvent.TURN_SETTLED)
    assert state == WorkerState.DONE

    # DONE -> NEEDS_REVIEW requires artifact check
    with pytest.raises(StateTransitionCheckError, match="artifact is missing"):
        transition_worker_state(state, WorkerEvent.SUBMIT_REVIEW, context={"has_artifact": False})

    state = transition_worker_state(
        state, WorkerEvent.SUBMIT_REVIEW, context={"has_artifact": True}
    )
    assert state == WorkerState.NEEDS_REVIEW

    # NEEDS_REVIEW -> APPROVED
    state = transition_worker_state(state, WorkerEvent.REVIEW_APPROVED)
    assert state == WorkerState.APPROVED

    # APPROVED -> SQUASHING requires operator authorization
    with pytest.raises(StateTransitionCheckError, match="operator authorization"):
        transition_worker_state(
            state, WorkerEvent.START_SQUASH, context={"operator_authorized": False}
        )

    state = transition_worker_state(
        state, WorkerEvent.START_SQUASH, context={"operator_authorized": True}
    )
    assert state == WorkerState.SQUASHING

    # SQUASHING -> LANDED
    state = transition_worker_state(state, WorkerEvent.SQUASH_SUCCEEDED)
    assert state == WorkerState.LANDED


def test_illegal_worker_transitions():
    """Attempting illegal edges must raise InvalidStateTransitionError."""
    with pytest.raises(InvalidStateTransitionError):
        transition_worker_state(WorkerState.SPAWNING, WorkerEvent.START_SQUASH)

    with pytest.raises(InvalidStateTransitionError):
        transition_worker_state(WorkerState.IDLE, WorkerEvent.TURN_SETTLED)

    with pytest.raises(InvalidStateTransitionError):
        transition_worker_state(WorkerState.LANDED, WorkerEvent.PROMPT_DISPATCHED)


def test_pipeline_phases_and_fix_loop_cap():
    """Verify pipeline phase transitions and exhaustion of fix loop cap."""
    phase = PipelinePhase.PREFLIGHT

    phase = transition_pipeline_phase(phase, PipelineEvent.START_SCOUT)
    assert phase == PipelinePhase.SCOUT

    phase = transition_pipeline_phase(phase, PipelineEvent.SCOUT_DONE)
    assert phase == PipelinePhase.ARCHITECT

    phase = transition_pipeline_phase(phase, PipelineEvent.PLAN_READY)
    assert phase == PipelinePhase.APPROVAL_GATE

    phase = transition_pipeline_phase(phase, PipelineEvent.OPERATOR_APPROVED)
    assert phase == PipelinePhase.CODERS

    phase = transition_pipeline_phase(phase, PipelineEvent.CODERS_DONE)
    assert phase == PipelinePhase.REVIEW

    # Review asks for fixes (loop 1 to 5)
    phase = transition_pipeline_phase(
        phase, PipelineEvent.REVIEW_NEEDS_FIXES, context={"fix_count": 1}
    )
    assert phase == PipelinePhase.FIX_LOOP

    phase = transition_pipeline_phase(phase, PipelineEvent.FIX_LOOP_SETTLED)
    assert phase == PipelinePhase.REVIEW

    # Exceeding fix loop cap (> 5) transitions to FAILED
    phase = transition_pipeline_phase(
        phase, PipelineEvent.REVIEW_NEEDS_FIXES, context={"fix_count": 5}
    )
    assert phase == PipelinePhase.FAILED
