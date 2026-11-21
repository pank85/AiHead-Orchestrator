import json

from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app
from herdr_master.pipeline import worker_dispatch as routing


def test_external_approval_workflow_and_no_clobber(tmp_path, monkeypatch):
    from herdr_master.core.model_ratings import ModelRating

    rating = ModelRating(
        intelligence=80,
        coding=80,
        agentic=80,
        speed=80,
        cost_efficiency=80,
        context_handling=80,
        worker_tier=1,
    )
    snapshot = {
        "available": None,
        "ratings": {"pi": {"openai/example": rating.model_dump(mode="json")}},
    }
    run_policy = {
        "schema_version": 1,
        "run_id": "run",
        "worktree": str(tmp_path),
        "mode": "herdr",
        "native_host": None,
        "run_squad_path": None,
        "workers_override": None,
        "override_provenance": "none",
        "worker_pool": [{"kind": "pi", "model": "openai/example", "enabled": True}],
    }
    run_path = tmp_path / "run.json"
    run_path.write_text(json.dumps(run_policy))
    manifest = {
        "schema_version": 1,
        "classifier_version": 1,
        "run_id": "run",
        "workspace_id": "w",
        "worktree": str(tmp_path),
        "mode": "herdr",
        "native_host": None,
        "parent_pane_id": "w:p1",
        "session": "default",
        "socket_path": "/tmp/herdr.sock",
        "snapshot_digest": routing.canonical_digest(snapshot),
        "run_policy_path": str(run_path),
        "run_policy_digest": routing.canonical_digest(run_policy),
        "tasks": [
            {
                "id": "one",
                "scope": "scope",
                "prompt": "prompt",
                "kind": "pi",
                "model": "openai/example",
                "effort": "high",
                "attempt": 1,
                "difficulty": {
                    "cross_system_invariant": False,
                    "security_or_data_loss": False,
                    "novel_algorithm_or_ambiguous_architecture": False,
                    "multiple_concurrent_state_boundaries": False,
                },
            }
        ],
    }
    monkeypatch.setattr(routing, "policy_snapshot", lambda _: snapshot)
    monkeypatch.setattr(
        routing,
        "create_run_policy",
        lambda **kwargs: routing.WorkerRunPolicy.model_validate(run_policy),
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    runner = CliRunner()
    digest_result = runner.invoke(
        aihead_app, ["worker", "run-policy", "digest", "--file", str(run_path)]
    )
    assert digest_result.exit_code == 0, digest_result.output
    assert json.loads(digest_result.stdout)["run_policy_digest"] == routing.canonical_digest(
        run_policy
    )
    assert json.loads(digest_result.stdout)["provenance_validated"] is False
    check = runner.invoke(
        aihead_app, ["worker", "manifest", "validate", "--manifest", str(manifest_path)]
    )
    assert check.exit_code == 0, check.output
    inspected = json.loads(check.stdout)
    assert inspected["approved"] is False
    assert inspected["manifest"]["tasks"][0]["prompt"] == "prompt"
    output = tmp_path / "approval.json"
    args = [
        "worker",
        "approval",
        "create",
        "--manifest",
        str(manifest_path),
        "--manifest-digest",
        inspected["manifest_digest"],
        "--snapshot-digest",
        inspected["snapshot_digest"],
        "--approver-type",
        "human",
        "--approver",
        "alice",
        "--output",
        str(output),
    ]
    assert runner.invoke(aihead_app, args).exit_code != 0
    assert not output.exists()
    invalid = args.copy()
    invalid[invalid.index("--manifest-digest") + 1] = "0" * 64
    assert runner.invoke(aihead_app, invalid + ["--confirm-approved"]).exit_code != 0
    assert not output.exists()
    result = runner.invoke(aihead_app, args + ["--confirm-approved"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["identity_authenticated"] is False
    original = output.read_bytes()
    assert runner.invoke(aihead_app, args + ["--confirm-approved"]).exit_code != 0
    assert output.read_bytes() == original
    output.unlink()
    run_path.write_text(
        json.dumps({k: v for k, v in run_policy.items() if k != "override_provenance"})
    )
    assert runner.invoke(aihead_app, args + ["--confirm-approved"]).exit_code != 0
    assert not output.exists()
    run_path.write_text(json.dumps({**run_policy, "run_id": "changed"}))
    assert runner.invoke(aihead_app, args + ["--confirm-approved"]).exit_code != 0
    assert not output.exists()
    run_path.write_text(json.dumps(run_policy))
    snapshot["available"] = {"herdr": {"pi": []}}
    assert runner.invoke(aihead_app, args + ["--confirm-approved"]).exit_code != 0
    assert not output.exists()


def test_snapshot_no_clobber(tmp_path, monkeypatch):
    snapshot = {"available": None, "ratings": {}}
    monkeypatch.setattr(routing, "policy_snapshot", lambda _: snapshot)
    path = tmp_path / "snapshot.json"
    args = ["worker", "snapshot", "--worktree", str(tmp_path), "--output", str(path)]
    runner = CliRunner()
    result = runner.invoke(aihead_app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["snapshot_digest"] == routing.canonical_digest(snapshot)
    assert json.loads(path.read_text()) == snapshot
    assert runner.invoke(aihead_app, args).exit_code != 0
