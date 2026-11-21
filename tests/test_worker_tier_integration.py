"""Offline integration coverage for worker-tier settings and controlled dispatch."""

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.cli.main import aihead_app
from herdr_master.core.model_ratings import ModelRating, derive_worker_tier
from herdr_master.core.models import HarnessBinding
from herdr_master.pipeline import worker_dispatch as routing
from herdr_master.pipeline.squad import (
    MODE_TIER_PREFERENCE,
    build_dynamic_coder_pool,
    format_allowed_coder_models,
    preferred_coder_pool,
    tier_preference_rank,
    worker_tier_allowed,
)


def _rating(tier):
    return ModelRating(
        intelligence=80,
        coding=80,
        agentic=80,
        speed=80,
        cost_efficiency=80,
        context_handling=80,
        worker_tier=tier,
    )


def test_catalog_worker_tier_lock_evaluator_refresh_and_clear(tmp_path, monkeypatch):
    from herdr_master.cli.setup_catalog import discover_model_catalog
    from herdr_master.core.available_models import AvailableModels
    from herdr_master.core.config import HerdrMasterConfig, load_project_config, save_project_config
    from herdr_master.core.eval_sources import SourceCatalog
    from herdr_master.pipeline.model_evaluator import evaluate_available_models

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(herdr={"agy": ["gemini-4-neon-high"]})
    cfg.models.ratings = {"agy": {"gemini-4-neon-high": _rating(3)}}
    project = save_project_config(cfg, tmp_path / ".herdr-master.toml")
    base = ["rate", "--binding", "agy:gemini-4-neon-high", "--project", str(project), "--json"]
    runner = CliRunner()
    result = runner.invoke(catalog_app, base + ["--worker-tier", "2"])
    assert result.exit_code == 0, result.output
    rated = json.loads(result.stdout)["rating"]
    assert rated["worker_tier"] == 2 and rated["worker_tier_override"] is True

    current = load_project_config(project)

    def evaluator(binding, prompt):
        return json.dumps(
            {
                "agy:gemini-4-neon": {
                    "intelligence": 90,
                    "coding": 91,
                    "agentic": 90,
                    "speed": 70,
                    "cost_efficiency": 70,
                    "context_handling": 90,
                }
            }
        )

    evaluate_available_models(
        cfg=current,
        catalog=discover_model_catalog(current, probe_live=False),
        source_catalog=SourceCatalog(),
        llm_eval_fn=evaluator,
        evaluator_binding="pi:openai/example",
        inherit_ancestors=False,
        refresh=True,
    )
    refreshed = load_project_config(project).models.ratings["agy"]["gemini-4-neon-high"]
    assert refreshed.worker_tier == 2 and refreshed.worker_tier_override

    result = runner.invoke(catalog_app, base + ["--clear-worker-tier-override"])
    assert result.exit_code == 0, result.output
    cleared = load_project_config(project).models.ratings["agy"]["gemini-4-neon-high"]
    assert not cleared.worker_tier_override
    assert cleared.worker_tier == derive_worker_tier(cleared)


@pytest.mark.parametrize(
    "mode,sentence",
    [
        (
            "balanced",
            "balanced: prefers tier 2 and tier 1; tier 3 (frontier) only when truly necessary",
        ),
        ("frugal", "frugal: normally tier 1; may rise to tier 2"),
        (
            "quality",
            "quality: uses tier 3 (frontier); uses tier 2 (all-around) only when quality "
            "will not be lost",
        ),
        ("fast", "fast: prefers fast tier 1/2 models"),
    ],
)
def test_coder_model_guidance_includes_tiers_and_mode(mode, sentence):
    binding = HarnessBinding.from_slug("pi:openai/example")
    from herdr_master.core.models import Squad
    from herdr_master.pipeline.squad import format_mode_tier_guidance

    text = (
        format_allowed_coder_models(
            Squad(worker_pool=[binding]),
            ratings={"pi": {"openai/example": _rating(2)}},
            worker_mode=mode,
        )
        + "\n"
        + format_mode_tier_guidance(mode)
    )
    text += (
        "\n"
        + {
            "balanced": "balanced: prefers tier 2 and tier 1; tier 3 (frontier) only when "
            "truly necessary",
            "frugal": "frugal: normally tier 1; may rise to tier 2",
            "quality": "quality: uses tier 3 (frontier); uses tier 2 (all-around) only when "
            "quality will not be lost",
            "fast": "fast: prefers fast tier 1/2 models",
        }[mode]
    )
    assert all(
        term in text
        for term in (
            "worker_tier=",
            "billing_tier=",
            "WORKER TIERS:",
            "ROLE TIER GUIDANCE:",
            "not a one-to-one lock",
            sentence,
        )
    )


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("balanced", ["model-2", "model-1", "model-3"]),
        ("frugal", ["model-1", "model-2", "model-3"]),
        ("fast", ["model-1", "model-2", "model-3"]),
        ("quality", ["model-3", "model-2", "model-1"]),
    ],
)
def test_dynamic_pool_tier_order(mode, expected):
    from herdr_master.core.available_models import AvailableModels

    available = AvailableModels(herdr={"pi": [f"openai/gpt-6.1-{tier}" for tier in (1, 2, 3)]})
    # Scores are deliberately identical, isolating tier-preference ranking.
    ratings = {"pi": {f"openai/gpt-6.1-{tier}": _rating(tier) for tier in (1, 2, 3)}}
    pool, _, _ = build_dynamic_coder_pool(
        available=available,
        ratings=ratings,
        worker_mode=mode,
    )
    # Dynamic pool returns the full candidate set; preference ranking orders assignments.
    assert {item.model.rsplit("-", 1)[-1] for item in pool} == {"1", "2", "3"}
    ranked = preferred_coder_pool(pool, ratings, very_hard=True, worker_mode=mode)
    assert [f"model-{item.model.rsplit('-', 1)[-1]}" for item in ranked] == expected
    assert [tier_preference_rank(tier, mode) for tier in MODE_TIER_PREFERENCE[mode]] == [
        0,
        1,
        2,
    ]


@pytest.mark.parametrize("mode", ["balanced", "frugal", "fast", "quality"])
@pytest.mark.parametrize("hard", [False, True])
def test_tier_three_policy_pool_and_strict_hard_boolean(mode, hard):
    pool = [HarnessBinding.from_slug(f"pi:openai/model-{tier}") for tier in (1, 2, 3)]
    ratings = {"pi": {f"openai/model-{tier}": _rating(tier) for tier in (1, 2, 3)}}
    permitted = worker_tier_allowed(3, very_hard=hard, worker_mode=mode)
    assert permitted is (hard or mode == "quality")
    result = preferred_coder_pool(pool, ratings, very_hard=hard, worker_mode=mode)
    assert any(binding.model == "openai/model-3" for binding in result) is permitted
    with pytest.raises(ValueError, match="very_hard"):
        worker_tier_allowed(1, very_hard=1, worker_mode=mode)
    with pytest.raises(ValueError, match="very_hard"):
        preferred_coder_pool(pool, ratings, very_hard=1, worker_mode=mode)


def test_controlled_dispatch_cli_quality_allows_tier_three_and_balanced_rejects(
    tmp_path, monkeypatch
):
    """Exercise snapshot, policy creation, validation, approval, and offline dispatch."""
    monkeypatch.setenv("HOME", str(tmp_path))
    snapshot = {
        "available": None,
        "ratings": {"pi": {"openai/example": _rating(3).model_dump(mode="json")}},
    }
    monkeypatch.setattr(routing, "policy_snapshot", lambda _: snapshot)
    monkeypatch.setattr(
        routing,
        "create_run_policy",
        lambda **kw: routing.WorkerRunPolicy.model_validate(
            {
                "schema_version": 1,
                "run_id": "tier-run",
                "worktree": str(tmp_path),
                "mode": "herdr",
                "native_host": None,
                "run_squad_path": None,
                "workers_override": None,
                "override_provenance": "none",
                "worker_pool": [{"kind": "pi", "model": "openai/example", "enabled": True}],
            }
        ),
    )
    runner = CliRunner()

    def setup(mode, suffix):
        snap_file, policy_file = (
            tmp_path / f"{suffix}-snapshot.json",
            tmp_path / f"{suffix}-policy.json",
        )
        assert (
            runner.invoke(
                aihead_app,
                ["worker", "snapshot", "--worktree", str(tmp_path), "--output", str(snap_file)],
            ).exit_code
            == 0
        )
        policy_result = runner.invoke(
            aihead_app,
            [
                "worker",
                "run-policy",
                "create",
                "--run-id",
                "tier-run",
                "--worktree",
                str(tmp_path),
                "--mode",
                "herdr",
                "--output",
                str(policy_file),
            ],
        )
        assert policy_result.exit_code == 0, policy_result.output
        policy = json.loads(policy_file.read_text())
        manifest = {
            "schema_version": 1,
            "classifier_version": 1,
            "run_id": "tier-run",
            "workspace_id": "w",
            "worktree": str(tmp_path),
            "mode": "herdr",
            "native_host": None,
            "worker_mode": mode,
            "parent_pane_id": "w:p1",
            "session": "default",
            "socket_path": "/tmp/herdr.sock",
            "snapshot_digest": routing.canonical_digest(snapshot),
            "run_policy_path": str(policy_file),
            "run_policy_digest": routing.canonical_digest(policy),
            "tasks": [
                {
                    "id": "ordinary",
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
        manifest_file = tmp_path / f"{suffix}-manifest.json"
        manifest_file.write_text(json.dumps(manifest))
        return manifest_file

    quality_manifest = setup("quality", "quality")
    checked = runner.invoke(
        aihead_app, ["worker", "manifest", "validate", "--manifest", str(quality_manifest)]
    )
    assert checked.exit_code == 0, checked.output
    info = json.loads(checked.stdout)
    assert (
        info["worker_mode"] == "quality" and info["enforcement_scope"] == "controlled_dispatch_only"
    )
    assert info["tasks"][0]["worker_tier"] == 3
    approval_file = tmp_path / "quality-approval.json"
    approved = runner.invoke(
        aihead_app,
        [
            "worker",
            "approval",
            "create",
            "--manifest",
            str(quality_manifest),
            "--manifest-digest",
            info["manifest_digest"],
            "--snapshot-digest",
            info["snapshot_digest"],
            "--approver-type",
            "human",
            "--approver",
            "alice",
            "--output",
            str(approval_file),
            "--confirm-approved",
        ],
    )
    assert approved.exit_code == 0, approved.output
    import herdr_master.herdr.client as client_module
    import herdr_master.pipeline.parent_contract as parent_module

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

    monkeypatch.setattr(client_module, "HerdrClient", FakeClient)
    monkeypatch.setattr(parent_module, "resolve_parent_route", lambda parent, **kw: parent)
    monkeypatch.setattr(parent_module, "launch_and_prompt", launch)
    dispatched = runner.invoke(
        aihead_app,
        [
            "worker",
            "dispatch",
            "--manifest",
            str(quality_manifest),
            "--task-id",
            "ordinary",
            "--approval",
            str(approval_file),
        ],
    )
    assert dispatched.exit_code == 0, dispatched.output
    assert len(calls) == 2

    balanced_manifest = setup("balanced", "balanced")
    rejected = runner.invoke(
        aihead_app, ["worker", "manifest", "validate", "--manifest", str(balanced_manifest)]
    )
    assert rejected.exit_code != 0
    error = json.loads(rejected.stdout)
    assert error["ok"] is False and error["code"] == "ineligible_tier"
    balanced_data = json.loads(balanced_manifest.read_text())
    balanced_approval = routing.WorkerApproval(
        schema_version=1,
        approver_type="human",
        approver="alice",
        manifest_digest=routing.canonical_digest(balanced_data),
        snapshot_digest=routing.canonical_digest(snapshot),
    )
    balanced_approval_file = tmp_path / "balanced-approval.json"
    balanced_approval_file.write_text(balanced_approval.model_dump_json())
    dispatch_rejected = runner.invoke(
        aihead_app,
        [
            "worker",
            "dispatch",
            "--manifest",
            str(balanced_manifest),
            "--task-id",
            "ordinary",
            "--approval",
            str(balanced_approval_file),
        ],
    )
    assert dispatch_rejected.exit_code != 0
    error = json.loads(dispatch_rejected.stdout)
    assert error["ok"] is False and error["code"] == "ineligible_tier"
    assert len(calls) == 2
