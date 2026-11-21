"""Offline tests for the controlled worker gate."""

import itertools

import pytest

from herdr_master.core.model_ratings import ModelRating
from herdr_master.pipeline.worker_dispatch import (
    WorkerApproval,
    WorkerDispatchError,
    WorkerManifest,
    WorkerRunPolicy,
    canonical_digest,
    classify_difficulty,
    create_run_policy,
    dispatch_worker,
    load_run_policy,
)
from herdr_master.pipeline.worker_dispatch import (
    validate_worker_assignment as _validate_worker_assignment,
)

KEYS = (
    "cross_system_invariant",
    "security_or_data_loss",
    "novel_algorithm_or_ambiguous_architecture",
    "multiple_concurrent_state_boundaries",
)


def test_per_task_pool_tier_order_and_unknown():
    from herdr_master.core.models import HarnessBinding
    from herdr_master.pipeline.squad import eligible_coder_pool

    def rating(tier):
        return ModelRating(
            intelligence=80,
            coding=80,
            agentic=80,
            speed=80,
            cost_efficiency=80,
            context_handling=80,
            worker_tier=tier,
        )

    pool = [
        HarnessBinding.from_slug(f"pi:openai/{name}") for name in ("three", "two", "one", "unknown")
    ]
    ratings = {"pi": {"openai/three": rating(3), "openai/two": rating(2), "openai/one": rating(1)}}
    for mode in ("balanced", "quality", "fast", "frugal"):
        assert [
            b.model for b in eligible_coder_pool(pool, ratings, very_hard=False, worker_mode=mode)
        ] == ["openai/one", "openai/two"]
        assert [
            b.model for b in eligible_coder_pool(pool, ratings, very_hard=True, worker_mode=mode)
        ] == ["openai/one", "openai/two", "openai/three"]
    assert eligible_coder_pool(pool, {}, very_hard=True) == []


def test_difficulty_truth_table_and_invalid():
    for values in itertools.product((False, True), repeat=4):
        assert classify_difficulty(dict(zip(KEYS, values, strict=True))) == (
            values[1] or sum(values[::2] + values[3:]) >= 2
        )
    for facts in (
        {},
        {**dict.fromkeys(KEYS, False), "extra": True},
        {**dict.fromkeys(KEYS, False), KEYS[0]: 1},
    ):
        with pytest.raises(WorkerDispatchError):
            classify_difficulty(facts)


def run_policy():
    return WorkerRunPolicy.model_validate(
        {
            "schema_version": 1,
            "run_id": "run",
            "worktree": "/tmp/project",
            "mode": "herdr",
            "native_host": None,
            "run_squad_path": None,
            "workers_override": None,
            "override_provenance": "none",
            "worker_pool": [{"kind": "pi", "model": "openai/example", "enabled": True}],
        }
    )


def validate_worker_assignment(manifest, approval, task_id, snapshot):
    return _validate_worker_assignment(manifest, approval, task_id, snapshot, run_policy())


def setup_assignment(tier=3, hard=False):
    rating = ModelRating(
        intelligence=80,
        coding=80,
        agentic=80,
        speed=80,
        cost_efficiency=80,
        context_handling=80,
        worker_tier=tier,
    )
    snapshot = {
        "worker_pool": None,
        "available": None,
        "ratings": {"pi": {"openai/example": rating.model_dump(mode="json")}},
    }
    facts = dict.fromkeys(KEYS, False)
    facts["security_or_data_loss"] = hard
    manifest = WorkerManifest.model_validate(
        {
            "schema_version": 1,
            "classifier_version": 1,
            "run_id": "run",
            "workspace_id": "w",
            "worktree": "/tmp/project",
            "mode": "herdr",
            "native_host": None,
            "parent_pane_id": "w:p1",
            "session": "default",
            "socket_path": "/tmp/herdr.sock",
            "run_policy_path": "/tmp/run-policy.json",
            "run_policy_digest": canonical_digest(run_policy().model_dump(mode="json")),
            "snapshot_digest": canonical_digest(snapshot),
            "tasks": [
                {
                    "id": "one",
                    "scope": "scope",
                    "prompt": "prompt",
                    "kind": "pi",
                    "model": "openai/example",
                    "effort": "high",
                    "difficulty": facts,
                    "attempt": 1,
                }
            ],
        }
    )
    approval = WorkerApproval(
        schema_version=1,
        approver_type="human",
        approver="alice",
        manifest_digest=canonical_digest(manifest.model_dump(mode="json")),
        snapshot_digest=canonical_digest(snapshot),
    )
    return manifest, approval, snapshot


@pytest.mark.parametrize(
    "tier,hard,allowed", [(1, False, True), (2, False, True), (3, False, False), (3, True, True)]
)
def test_gate(tier, hard, allowed):
    manifest, approval, snapshot = setup_assignment(tier, hard)
    if allowed:
        assert validate_worker_assignment(manifest, approval, "one", snapshot).id == "one"
    else:
        with pytest.raises(WorkerDispatchError, match="Tier 3"):
            validate_worker_assignment(manifest, approval, "one", snapshot)


def test_unknown_duplicate_native_and_changed_prompt_rejected():
    manifest, approval, snapshot = setup_assignment(1)
    changed = manifest.model_copy(deep=True)
    changed.tasks[0].prompt = "different"
    with pytest.raises(WorkerDispatchError) as exc:
        validate_worker_assignment(changed, approval, "one", snapshot)
    assert exc.value.code == "approval_mismatch"
    duplicate = manifest.model_copy(deep=True)
    duplicate.tasks.append(duplicate.tasks[0])
    with pytest.raises(WorkerDispatchError) as exc:
        validate_worker_assignment(duplicate, approval, "one", snapshot)
    assert exc.value.code == "duplicate_task"
    native = manifest.model_copy(update={"mode": "native"})
    native_approval = approval.model_copy(
        update={"manifest_digest": canonical_digest(native.model_dump(mode="json"))}
    )
    native_policy = run_policy().model_copy(update={"mode": "native"})
    native = native.model_copy(
        update={"run_policy_digest": canonical_digest(native_policy.model_dump(mode="json"))}
    )
    native_approval = native_approval.model_copy(
        update={"manifest_digest": canonical_digest(native.model_dump(mode="json"))}
    )
    with pytest.raises(WorkerDispatchError) as exc:
        _validate_worker_assignment(native, native_approval, "one", snapshot, native_policy)
    assert exc.value.code == "native_unsupported"
    empty = {"worker_pool": None, "available": None, "ratings": {}}
    missing = manifest.model_copy(update={"snapshot_digest": canonical_digest(empty)})
    missing_approval = approval.model_copy(
        update={
            "manifest_digest": canonical_digest(missing.model_dump(mode="json")),
            "snapshot_digest": canonical_digest(empty),
        }
    )
    with pytest.raises(WorkerDispatchError) as exc:
        validate_worker_assignment(missing, missing_approval, "one", empty)
    assert exc.value.code == "unknown_tier"


def test_run_policy_binding_and_override(tmp_path, monkeypatch):
    import herdr_master.pipeline.worker_dispatch as module

    monkeypatch.setattr(module, "create_run_policy", lambda **kwargs: run_policy())
    policy = run_policy()
    path = tmp_path / "run.json"
    path.write_text(policy.model_dump_json())
    manifest, approval, snapshot = setup_assignment(1)
    manifest = manifest.model_copy(update={"run_policy_path": str(path)})
    approval = approval.model_copy(
        update={"manifest_digest": canonical_digest(manifest.model_dump(mode="json"))}
    )
    loaded = load_run_policy(manifest)
    assert _validate_worker_assignment(manifest, approval, "one", snapshot, loaded).id == "one"
    path.write_text(policy.model_copy(update={"worker_pool": []}).model_dump_json())
    with pytest.raises(WorkerDispatchError) as exc:
        load_run_policy(manifest)
    assert exc.value.code == "run_policy_unbound"
    path.unlink()
    with pytest.raises(WorkerDispatchError) as exc:
        load_run_policy(manifest)
    assert exc.value.code == "run_policy_unbound"

    explicit = policy.model_copy(
        update={"workers_override": "pi:openai/example", "override_provenance": "explicit"}
    )
    manifest = manifest.model_copy(
        update={"run_policy_digest": canonical_digest(explicit.model_dump(mode="json"))}
    )
    path.write_text(explicit.model_dump_json())
    monkeypatch.setattr(module, "create_run_policy", lambda **kwargs: explicit)
    assert load_run_policy(manifest) == explicit
    invalid = explicit.model_copy(update={"workers_override": "pi:openai/other"})
    path.write_text(invalid.model_dump_json())
    manifest = manifest.model_copy(
        update={"run_policy_digest": canonical_digest(invalid.model_dump(mode="json"))}
    )
    with pytest.raises(WorkerDispatchError) as exc:
        load_run_policy(manifest)
    assert exc.value.code == "run_policy_unbound"


def test_real_run_policy_producer_detects_changed_run_squad(tmp_path):
    run_squad = tmp_path / "squad.toml"
    run_squad.write_text('worker_pool = ["pi:openai/example"]\n')
    policy = create_run_policy(
        run_id="run",
        worktree=tmp_path,
        mode="herdr",
        native_host=None,
        run_squad_path=run_squad,
        workers_override=None,
    )
    assert [(route.kind, route.model) for route in policy.worker_pool] == [("pi", "openai/example")]
    path = tmp_path / "run.json"
    path.write_text(policy.model_dump_json())
    manifest, _, _ = setup_assignment(1)
    manifest = manifest.model_copy(
        update={
            "worktree": str(tmp_path),
            "run_policy_path": str(path),
            "run_policy_digest": canonical_digest(policy.model_dump(mode="json")),
        }
    )
    assert load_run_policy(manifest) == policy
    run_squad.write_text('worker_pool = ["pi:openai/other"]\n')
    with pytest.raises(WorkerDispatchError) as exc:
        load_run_policy(manifest)
    assert exc.value.code == "run_policy_unbound"


def test_drift_fails():
    manifest, approval, snapshot = setup_assignment(1)
    snapshot["available"] = {"herdr": {"pi": []}}
    with pytest.raises(WorkerDispatchError) as exc:
        validate_worker_assignment(manifest, approval, "one", snapshot)
    assert exc.value.code == "approval_mismatch"


def test_approved_launch_and_idempotency(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import herdr_master.herdr.client as client_module
    import herdr_master.pipeline.parent_contract as parent_module
    import herdr_master.pipeline.worker_dispatch as module

    manifest, approval, snapshot = setup_assignment(1)
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def get_snapshot(self):
            return SimpleNamespace(
                workspaces=[SimpleNamespace(workspace_id="w")],
                panes=[SimpleNamespace(pane_id="w:p1", workspace_id="w")],
            )

        async def create_tab(self, **kwargs):
            calls.append(kwargs)
            return {"root_pane": {"pane_id": "w:p2", "tab_id": "w:t1"}}

    async def launch(client, **kwargs):
        calls.append(kwargs)
        return tmp_path / "watch"

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(client_module, "HerdrClient", FakeClient)
    monkeypatch.setattr(parent_module, "resolve_parent_route", lambda parent, **kw: parent)
    monkeypatch.setattr(parent_module, "launch_and_prompt", launch)
    monkeypatch.setattr(module, "policy_snapshot", lambda _: snapshot)
    policy = create_run_policy(
        run_id="run",
        worktree=tmp_path,
        mode="herdr",
        native_host=None,
        run_squad_path=None,
        workers_override="pi:openai/example",
    )
    run_path = tmp_path / "run.json"
    run_path.write_text(policy.model_dump_json())
    manifest = manifest.model_copy(
        update={
            "worktree": str(tmp_path),
            "run_policy_path": str(run_path),
            "run_policy_digest": canonical_digest(policy.model_dump(mode="json")),
        }
    )
    approval = approval.model_copy(
        update={"manifest_digest": canonical_digest(manifest.model_dump(mode="json"))}
    )
    manifest_path, approval_path = tmp_path / "m.json", tmp_path / "a.json"
    manifest_path.write_text(manifest.model_dump_json())
    approval_path.write_text(approval.model_dump_json())
    run_path.write_text("{}")
    with pytest.raises(WorkerDispatchError) as missing:
        dispatch_worker(manifest_path=manifest_path, task_id="one", approval_path=approval_path)
    assert missing.value.code == "run_policy_unbound"
    assert not calls
    run_path.write_text(policy.model_dump_json())
    changed_snapshot = dict(snapshot)
    changed_snapshot["available"] = {"herdr": {"pi": []}}
    monkeypatch.setattr(module, "policy_snapshot", lambda _: changed_snapshot)
    with pytest.raises(WorkerDispatchError) as drift:
        dispatch_worker(manifest_path=manifest_path, task_id="one", approval_path=approval_path)
    assert drift.value.code == "approval_mismatch"
    assert not calls
    monkeypatch.setattr(module, "policy_snapshot", lambda _: snapshot)
    result = dispatch_worker(
        manifest_path=manifest_path, task_id="one", approval_path=approval_path
    )
    assert result["pane_id"] == "w:p2"
    assert calls[1]["parent_pane_id"] == "w:p1"
    assert calls[1]["prompt"] == "prompt"
    with pytest.raises(WorkerDispatchError) as exc:
        dispatch_worker(manifest_path=manifest_path, task_id="one", approval_path=approval_path)
    assert exc.value.code == "duplicate_attempt"
    assert len(calls) == 2


@pytest.mark.parametrize("worker_mode,tier,hard,allowed", [
    (mode, tier, hard, (tier != 3 or hard or mode == "quality"))
    for mode in ("balanced", "frugal", "fast", "quality")
    for tier in (1, 2, 3)
    for hard in (False, True)
])
def test_validate_worker_assignment_worker_mode_tier_matrix(worker_mode, tier, hard, allowed):
    manifest, approval, snapshot = setup_assignment(tier, hard)
    manifest = manifest.model_copy(update={"worker_mode": worker_mode})
    approval = approval.model_copy(
        update={"manifest_digest": canonical_digest(manifest.model_dump(mode="json"))}
    )
    if allowed:
        assert validate_worker_assignment(manifest, approval, "one", snapshot).id == "one"
    else:
        with pytest.raises(WorkerDispatchError) as exc:
            validate_worker_assignment(manifest, approval, "one", snapshot)
        assert exc.value.code == "ineligible_tier"
        assert str(exc.value).startswith("Tier 3 is not eligible for ordinary work")
        assert worker_mode in str(exc.value)
        assert "very-hard facts" in str(exc.value) and "quality" in str(exc.value)
