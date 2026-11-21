"""Essential tests for worker/pipeline state machines and Council lifecycle."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_master.core.models import PipelinePhase, WorkerState
from herdr_master.core.state_machine import (
    InvalidStateTransitionError,
    PipelineEvent,
    WorkerEvent,
    transition_pipeline_phase,
    transition_worker_state,
)
from herdr_master.pipeline import council


def test_worker_state_machine_valid_and_invalid_transitions() -> None:
    state = WorkerState.SPAWNING
    state = transition_worker_state(state, WorkerEvent.AGENT_READY)
    assert state == WorkerState.IDLE
    state = transition_worker_state(state, WorkerEvent.PROMPT_DISPATCHED)
    assert state == WorkerState.WORKING
    state = transition_worker_state(state, WorkerEvent.TURN_SETTLED)
    assert state == WorkerState.DONE

    with pytest.raises(InvalidStateTransitionError):
        transition_worker_state(WorkerState.SPAWNING, WorkerEvent.REVIEW_APPROVED)


def test_pipeline_phase_transitions() -> None:
    phase = PipelinePhase.PREFLIGHT
    phase = transition_pipeline_phase(phase, PipelineEvent.START_SCOUT)
    assert phase == PipelinePhase.SCOUT
    phase = transition_pipeline_phase(phase, PipelineEvent.SCOUT_DONE)
    assert phase == PipelinePhase.ARCHITECT
    phase = transition_pipeline_phase(phase, PipelineEvent.PLAN_READY)
    assert phase == PipelinePhase.APPROVAL_GATE
    phase = transition_pipeline_phase(phase, PipelineEvent.OPERATOR_APPROVED)
    assert phase == PipelinePhase.CODERS

    with pytest.raises(InvalidStateTransitionError):
        transition_pipeline_phase(PipelinePhase.PREFLIGHT, PipelineEvent.SQUASH_DONE)


def test_council_init_scout_and_initial_plan_submission(tmp_path: Path) -> None:
    run_dir = tmp_path / "council-run"
    state = council.init(
        run_dir=run_dir,
        roster=[
            {"label": "member-1", "binding": "pi:openai/gpt-6.1-sol:high"},
            {"label": "member-2", "binding": "agy:claude-opus-5-5-high"},
        ],
        specifications="Design auth middleware",
        coordinator="coordinator",
        scout="scout",
    )
    assert state["phase"] == council.CouncilPhase.INITIALIZED.value
    members = state["roster"]["members"]
    assert len(members) == 2

    # Submitting a plan before scout context is recorded must fail closed
    plan_file = tmp_path / "plan.md"
    plan_file.write_text("# Plan\nUse JWT middleware.\n", encoding="utf-8")
    with pytest.raises(council.CouncilTransitionError):
        council.submit_initial_plan(run_dir, members[0]["id"], plan_file)

    scout_file = tmp_path / "scout.md"
    scout_file.write_text("# Scout\nRepo has FastAPI routes.\n", encoding="utf-8")
    after_scout = council.record_scout_context(run_dir, scout_file)
    assert after_scout["phase"] == council.CouncilPhase.SCOUTED.value

    after_plan = council.submit_initial_plan(run_dir, members[0]["id"], plan_file)
    assert after_plan["ok"] is True
    assert after_plan["member_id"] == members[0]["id"]
