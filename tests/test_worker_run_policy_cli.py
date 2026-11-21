import json

from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app
from herdr_master.pipeline import worker_dispatch as routing


def test_create_forwards_actual_inputs_and_exclusive_output(tmp_path, monkeypatch):
    captured = {}
    squad = tmp_path / "squad.toml"
    squad.write_text("")

    def resolve(**kwargs):
        captured.update(kwargs)
        return routing.WorkerRunPolicy.model_validate(
            {
                "schema_version": 1,
                "run_id": kwargs["run_id"],
                "worktree": str(kwargs["worktree"]),
                "mode": kwargs["mode"],
                "native_host": kwargs["native_host"],
                "run_squad_path": str(kwargs["run_squad_path"]),
                "workers_override": kwargs["workers_override"],
                "override_provenance": "explicit",
                "worker_pool": [{"kind": "pi", "model": "a/b", "enabled": True}],
            }
        )

    monkeypatch.setattr(routing, "create_run_policy", resolve)
    output = tmp_path / "run.json"
    args = [
        "worker",
        "run-policy",
        "create",
        "--run-id",
        "run",
        "--worktree",
        str(tmp_path),
        "--mode",
        "herdr",
        "--run-squad",
        str(squad),
        "--workers",
        "pi:a/b",
        "--output",
        str(output),
    ]
    runner = CliRunner()
    result = runner.invoke(aihead_app, args)
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["provenance_authenticated"] is False
    assert report["run_policy_digest"] == routing.canonical_digest(json.loads(output.read_text()))
    assert captured == {
        "run_id": "run",
        "worktree": tmp_path,
        "mode": "herdr",
        "native_host": None,
        "run_squad_path": squad,
        "workers_override": "pi:a/b",
    }
    original = output.read_bytes()
    assert runner.invoke(aihead_app, args).exit_code != 0
    assert output.read_bytes() == original
