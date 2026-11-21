import json
from pathlib import Path

from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app
from herdr_master.pipeline import worker_dispatch


def test_worker_dispatch_cli_forwards_exact_contract(monkeypatch, tmp_path):
    seen = {}

    def dispatch(**kwargs):
        seen.update(kwargs)
        return {"ok": True, "pane_id": "pane"}

    monkeypatch.setattr(worker_dispatch, "dispatch_worker", dispatch)
    manifest, approval = tmp_path / "manifest.json", tmp_path / "approval.json"
    result = CliRunner().invoke(
        aihead_app,
        [
            "worker",
            "dispatch",
            "--manifest",
            str(manifest),
            "--task-id",
            "task-1",
            "--approval",
            str(approval),
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["pane_id"] == "pane"
    assert seen == {
        "manifest_path": Path(manifest),
        "task_id": "task-1",
        "approval_path": Path(approval),
    }


def test_worker_dispatch_cli_reports_structured_rejection(monkeypatch, tmp_path):
    def reject(**kwargs):
        raise worker_dispatch.WorkerDispatchError("policy_drift", "Policy changed")

    monkeypatch.setattr(worker_dispatch, "dispatch_worker", reject)
    result = CliRunner().invoke(
        aihead_app,
        [
            "worker",
            "dispatch",
            "--manifest",
            str(tmp_path / "m"),
            "--task-id",
            "a",
            "--approval",
            str(tmp_path / "a"),
        ],
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "code": "policy_drift",
        "error": "Policy changed",
    }
