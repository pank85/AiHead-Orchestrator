"""Essential tests for controlled worker dispatch, tier gates, and CLI workflow."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app
from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import HerdrMasterConfig, save_project_config
from herdr_master.core.model_ratings import ModelRating
from herdr_master.pipeline.worker_dispatch import (
    Difficulty,
    WorkerApproval,
    WorkerDispatchError,
    WorkerManifest,
    WorkerTask,
    canonical_digest,
    classify_difficulty,
    create_run_policy,
    policy_snapshot,
    validate_worker_assignment,
)


def _setup_worktree(tmp_path: Path) -> Path:
    worktree = tmp_path / "repo"
    worktree.mkdir()
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={"pi": ["openai/gpt-6-luna", "openai/gpt-6.1-sol"]}
    )
    cfg.models.ratings = {
        "pi": {
            "openai/gpt-6-luna": ModelRating(
                intelligence=78,
                coding=80,
                agentic=78,
                speed=90,
                cost_efficiency=88,
                context_handling=80,
                family="openai",
                worker_tier=1,
            ),
            "openai/gpt-6.1-sol": ModelRating(
                intelligence=94,
                coding=95,
                agentic=92,
                speed=65,
                cost_efficiency=55,
                context_handling=95,
                family="openai",
                worker_tier=3,
            ),
        }
    }
    save_project_config(cfg, worktree / ".herdr-master.toml")
    return worktree


def _routine_difficulty() -> Difficulty:
    return Difficulty(
        cross_system_invariant=False,
        security_or_data_loss=False,
        novel_algorithm_or_ambiguous_architecture=False,
        multiple_concurrent_state_boundaries=False,
    )


def _hard_difficulty() -> Difficulty:
    return Difficulty(
        cross_system_invariant=True,
        security_or_data_loss=True,
        novel_algorithm_or_ambiguous_architecture=False,
        multiple_concurrent_state_boundaries=False,
    )


def test_difficulty_classifier() -> None:
    assert classify_difficulty(_routine_difficulty()) is False
    assert classify_difficulty(_hard_difficulty()) is True


def test_controlled_dispatch_enforces_tier_gate_and_approval_digests(
    isolated_home: Path,
) -> None:
    worktree = _setup_worktree(isolated_home)
    snap = policy_snapshot(worktree)
    snap_digest = canonical_digest(snap)

    run_policy = create_run_policy(
        run_id="run-1",
        worktree=worktree,
        mode="herdr",
        native_host=None,
        run_squad_path=None,
        workers_override=None,
    )
    run_policy_path = isolated_home / "run-policy.json"
    run_policy_path.write_text(
        json.dumps(run_policy.model_dump(mode="json")),
        encoding="utf-8",
    )
    run_policy_digest = canonical_digest(run_policy.model_dump(mode="json"))

    def make_manifest(worker_mode: str, model: str, difficulty: Difficulty) -> WorkerManifest:
        return WorkerManifest(
            schema_version=1,
            classifier_version=1,
            run_id="run-1",
            workspace_id="w1",
            worktree=str(worktree.resolve()),
            mode="herdr",
            worker_mode=worker_mode,
            native_host=None,
            parent_pane_id="w1:p1",
            session="default",
            socket_path="/tmp/fake.sock",
            snapshot_digest=snap_digest,
            run_policy_path=str(run_policy_path.resolve()),
            run_policy_digest=run_policy_digest,
            tasks=[
                WorkerTask(
                    id="task-1",
                    scope="tests",
                    prompt="Implement feature",
                    kind="pi",
                    model=model,
                    effort=None,
                    difficulty=difficulty,
                    attempt=1,
                )
            ],
        )

    def approve(manifest: WorkerManifest) -> WorkerApproval:
        return WorkerApproval(
            schema_version=1,
            approver_type="human",
            approver="tester",
            manifest_digest=canonical_digest(manifest.model_dump(mode="json")),
            snapshot_digest=snap_digest,
        )

    # Tier 3 rejected on routine task in balanced mode
    m_bal_t3 = make_manifest("balanced", "openai/gpt-6.1-sol", _routine_difficulty())
    with pytest.raises(WorkerDispatchError):
        validate_worker_assignment(m_bal_t3, approve(m_bal_t3), "task-1", snap, run_policy)

    # Tier 3 allowed on routine task in quality mode
    m_qual_t3 = make_manifest("quality", "openai/gpt-6.1-sol", _routine_difficulty())
    task = validate_worker_assignment(m_qual_t3, approve(m_qual_t3), "task-1", snap, run_policy)
    assert task.model == "openai/gpt-6.1-sol"

    # Tier 3 allowed on very-hard task in balanced mode
    m_bal_hard = make_manifest("balanced", "openai/gpt-6.1-sol", _hard_difficulty())
    task_hard = validate_worker_assignment(
        m_bal_hard, approve(m_bal_hard), "task-1", snap, run_policy
    )
    assert task_hard.id == "task-1"

    # Approval digest mismatch fails closed
    bad_approval = WorkerApproval(
        schema_version=1,
        approver_type="human",
        approver="tester",
        manifest_digest="0" * 64,
        snapshot_digest=snap_digest,
    )
    with pytest.raises(WorkerDispatchError):
        validate_worker_assignment(m_qual_t3, bad_approval, "task-1", snap, run_policy)


def test_worker_cli_snapshot_policy_validate_approval_dispatch(
    isolated_home: Path,
) -> None:
    runner = CliRunner()
    worktree = _setup_worktree(isolated_home)
    out_dir = isolated_home / "artifacts"
    out_dir.mkdir()

    snap_path = out_dir / "policy.json"
    rp_path = out_dir / "run-policy.json"
    manifest_path = out_dir / "manifest.json"
    approval_path = out_dir / "approval.json"

    r_snap = runner.invoke(
        aihead_app,
        ["worker", "snapshot", "--worktree", str(worktree), "--output", str(snap_path)],
    )
    assert r_snap.exit_code == 0, r_snap.output
    snap_digest = json.loads(r_snap.output)["snapshot_digest"]

    r_rp = runner.invoke(
        aihead_app,
        [
            "worker",
            "run-policy",
            "create",
            "--run-id",
            "run-cli",
            "--worktree",
            str(worktree),
            "--mode",
            "herdr",
            "--output",
            str(rp_path),
        ],
    )
    assert r_rp.exit_code == 0, r_rp.output
    rp_digest = json.loads(r_rp.output)["run_policy_digest"]

    manifest_data = {
        "schema_version": 1,
        "classifier_version": 1,
        "run_id": "run-cli",
        "workspace_id": "w1",
        "worktree": str(worktree.resolve()),
        "mode": "herdr",
        "worker_mode": "balanced",
        "native_host": None,
        "parent_pane_id": "w1:p1",
        "session": "default",
        "socket_path": "/tmp/fake.sock",
        "snapshot_digest": snap_digest,
        "run_policy_path": str(rp_path.resolve()),
        "run_policy_digest": rp_digest,
        "tasks": [
            {
                "id": "t1",
                "scope": "core",
                "prompt": "Do routine work",
                "kind": "pi",
                "model": "openai/gpt-6-luna",
                "effort": None,
                "difficulty": _routine_difficulty().model_dump(),
                "attempt": 1,
            }
        ],
    }
    manifest_path.write_text(json.dumps(manifest_data), encoding="utf-8")

    r_val = runner.invoke(
        aihead_app,
        ["worker", "manifest", "validate", "--manifest", str(manifest_path)],
    )
    assert r_val.exit_code == 0, r_val.output
    manifest_digest = json.loads(r_val.output)["manifest_digest"]

    r_app = runner.invoke(
        aihead_app,
        [
            "worker",
            "approval",
            "create",
            "--manifest",
            str(manifest_path),
            "--manifest-digest",
            manifest_digest,
            "--snapshot-digest",
            snap_digest,
            "--approver-type",
            "human",
            "--approver",
            "tester",
            "--confirm-approved",
            "--output",
            str(approval_path),
        ],
    )
    assert r_app.exit_code == 0, r_app.output

    with patch(
        "herdr_master.pipeline.worker_dispatch.dispatch_worker",
        return_value={"status": "dispatched", "task_id": "t1", "pane_id": "w1:p2"},
    ) as mock_dispatch:
        r_disp = runner.invoke(
            aihead_app,
            [
                "worker",
                "dispatch",
                "--manifest",
                str(manifest_path),
                "--task-id",
                "t1",
                "--approval",
                str(approval_path),
            ],
        )
    assert r_disp.exit_code == 0, r_disp.output
    mock_dispatch.assert_called_once()
