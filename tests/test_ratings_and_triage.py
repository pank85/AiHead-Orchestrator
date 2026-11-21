"""Essential tests for model ratings, worker tiers, squad selection, and triage validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import HerdrMasterConfig, save_project_config
from herdr_master.core.eval_sources import BenchmarkEvidence
from herdr_master.core.model_ratings import (
    ModelRating,
    apply_effort_calibration,
    default_worker_tier_for_model,
    derive_worker_tier,
    effective_worker_tier,
    find_unrated_models,
)
from herdr_master.pipeline.model_evaluator import evaluate_available_models
from herdr_master.pipeline.squad import (
    build_dynamic_coder_pool,
    worker_mode_rank_score,
    worker_tier_allowed,
)
from herdr_master.pipeline.triage import RoutingPlan, select_triage_models, validate_route


def _rating(
    *,
    intelligence: int,
    coding: int,
    agentic: int,
    speed: int = 75,
    cost_efficiency: int = 75,
    context_handling: int = 80,
    family: str = "openai",
    worker_tier: int | None = None,
    worker_tier_override: bool = False,
) -> ModelRating:
    return ModelRating(
        intelligence=intelligence,
        coding=coding,
        agentic=agentic,
        speed=speed,
        cost_efficiency=cost_efficiency,
        context_handling=context_handling,
        family=family,
        worker_tier=worker_tier,
        worker_tier_override=worker_tier_override,
    )


def test_worker_tier_derivation_and_override_validation() -> None:
    t3 = _rating(intelligence=92, coding=93, agentic=90, speed=60, cost_efficiency=50)
    t2 = _rating(intelligence=82, coding=84, agentic=80, speed=72, cost_efficiency=70)
    t1_fast = _rating(intelligence=80, coding=80, agentic=78, speed=88, cost_efficiency=90)
    t1_low = _rating(intelligence=68, coding=70, agentic=65, speed=60, cost_efficiency=60)

    assert derive_worker_tier(t3) == 3
    assert derive_worker_tier(t2) == 2
    assert derive_worker_tier(t1_fast) == 1
    assert derive_worker_tier(t1_low) == 1

    locked = _rating(
        intelligence=95,
        coding=95,
        agentic=95,
        worker_tier=2,
        worker_tier_override=True,
    )
    assert effective_worker_tier(locked) == 2

    with pytest.raises(ValidationError):
        _rating(intelligence=80, coding=80, agentic=80, worker_tier_override=True)


def test_effort_calibration_is_monotonic_and_unrated_detection_works() -> None:
    base = _rating(intelligence=85, coding=85, agentic=84, speed=75, cost_efficiency=75)
    low = apply_effort_calibration(base, "low", harness="agy", model_id="gemini-4-neon-low")
    high = apply_effort_calibration(base, "high", harness="agy", model_id="gemini-4-neon-high")

    assert low.speed >= high.speed
    assert high.intelligence >= low.intelligence

    unrated = find_unrated_models(
        {"pi": ["m1", "m2"]},
        {"pi": {"m1": base}},
    )
    assert unrated == {"pi": ["m2"]}


def test_evaluator_preserves_worker_tier_override_even_on_force_dry_run(
    isolated_home: Path,
) -> None:
    project_dir = isolated_home / "proj"
    project_dir.mkdir()
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(herdr={"pi": ["openai/gpt-6.1-sol"]})
    cfg.models.ratings = {
        "pi": {
            "openai/gpt-6.1-sol": _rating(
                intelligence=94,
                coding=95,
                agentic=92,
                worker_tier=2,
                worker_tier_override=True,
            )
        }
    }
    cfg_path = save_project_config(cfg, project_dir / ".herdr-master.toml")

    class _StubCatalog:
        source_status: dict[str, str] = {}

        def match(self, _norm):
            return BenchmarkEvidence(canonical_id="openai/gpt-6.1-sol"), "high"

    fresh = _rating(intelligence=95, coding=96, agentic=95)
    with (
        patch(
            "herdr_master.pipeline.model_evaluator.fetch_source_catalog",
            return_value=_StubCatalog(),
        ),
        patch(
            "herdr_master.pipeline.model_evaluator.synthesize_rating",
            return_value=fresh,
        ),
    ):
        res = evaluate_available_models(
            project_path=cfg_path,
            use_llm=False,
            force=True,
            dry_run=True,
        )
    rated = res.evaluated["pi"]["openai/gpt-6.1-sol"]
    assert rated.worker_tier == 2
    assert rated.worker_tier_override is True


@pytest.mark.parametrize(
    ("tier", "very_hard", "mode", "expected"),
    [
        (1, False, "balanced", True),
        (2, False, "balanced", True),
        (3, False, "balanced", False),
        (3, True, "balanced", True),
        (3, False, "quality", True),
        (3, False, "frugal", False),
        (3, True, "frugal", True),
        (2, False, "frugal", True),
        (3, False, "fast", False),
    ],
)
def test_worker_tier_allowed_matrix(tier: int, very_hard: bool, mode: str, expected: bool) -> None:
    assert worker_tier_allowed(tier, very_hard=very_hard, worker_mode=mode) is expected


def test_dynamic_coder_pool_and_triage_route_validation() -> None:
    available = AvailableModels(
        herdr={
            "pi": ["openai/gpt-6.1-sol", "openai/gpt-6-luna"],
            "agy": ["gemini-4-hydrogen-low", "claude-opus-5-5-high"],
        }
    )
    ratings = {
        "pi": {
            "openai/gpt-6.1-sol": _rating(
                intelligence=93,
                coding=94,
                agentic=92,
                speed=70,
                cost_efficiency=65,
                family="openai",
            ),
            "openai/gpt-6-luna": _rating(
                intelligence=78,
                coding=80,
                agentic=78,
                speed=90,
                cost_efficiency=88,
                family="openai",
            ),
        },
        "agy": {
            "gemini-4-hydrogen-low": _rating(
                intelligence=76,
                coding=78,
                agentic=75,
                speed=95,
                cost_efficiency=92,
                family="google",
            ),
            "claude-opus-5-5-high": _rating(
                intelligence=94,
                coding=94,
                agentic=93,
                speed=62,
                cost_efficiency=55,
                family="anthropic",
            ),
        },
    }

    pool, _, _ = build_dynamic_coder_pool(
        available=available,
        ratings=ratings,
        mode="herdr",
        worker_mode="balanced",
    )
    assert len(pool) >= 2

    picked = select_triage_models("pi", available=available, ratings=ratings)
    assert picked.scout_binding == "pi:openai/gpt-6-luna"
    assert picked.router_binding == "pi:openai/gpt-6.1-sol"

    valid_ergo = RoutingPlan(
        workflow="ergo",
        target_mode="herdr",
        coordinator="agy:gemini-4-hydrogen-low",
        scout="agy:gemini-4-hydrogen-low",
        architect="pi:openai/gpt-6.1-sol",
        reviewer="agy:claude-opus-5-5-high",
    )
    assert validate_route(valid_ergo, available=available, ratings=ratings).valid is True

    hallucinated = RoutingPlan(
        workflow="direct",
        target_mode="herdr",
        direct_binding="pi:openai/nonexistent-model",
    )
    assert validate_route(hallucinated, available=available, ratings=ratings).valid is False

    core_ergo = validate_route(valid_ergo, available=available, ratings=ratings, backend="core")
    assert core_ergo.valid is False

    # Disabled harness is excluded from dynamic coder pool and rejected by validate_route
    filtered_pool, _, _ = build_dynamic_coder_pool(
        available=available,
        ratings=ratings,
        mode="herdr",
        worker_mode="balanced",
        disabled_harnesses={"pi"},
    )
    assert all(b.kind.value != "pi" for b in filtered_pool)

    disabled_route = validate_route(
        valid_ergo, available=available, ratings=ratings, disabled_harnesses={"pi"}
    )
    assert disabled_route.valid is False


@pytest.mark.parametrize(
    ("model_id", "expected_tier"),
    [
        ("haiku", 1),
        ("claude-sonnet-5-5-medium", 2),
        ("claude-opus-5-5-high", 3),
        ("openai/gpt-6-luna", 1),
        ("openai/gpt-6.1-terra", 2),
        ("openai/gpt-6.1-sol", 3),
        ("gemini-4-hydrogen-low", 1),
        ("gemini-4-neon-medium", 2),
        ("gemini-4-carbon-high", 3),
        ("gemini-4-argon-high", 3),
        ("kilo/kilo-auto/free", 1),
    ],
)
def test_provider_default_worker_tier_presets(model_id: str, expected_tier: int) -> None:
    assert default_worker_tier_for_model(model_id) == expected_tier


def test_intra_tier_ranking_respects_subscription_cost_and_mode() -> None:
    # Both on subscription within Tier 2: quota-light value model vs heavier top model
    quota_light = ModelRating(
        intelligence=84,
        coding=86,
        agentic=84,
        speed=86,
        cost_efficiency=88,
        context_handling=90,
        billing_tier="subscription",
        worker_tier=2,
    )
    heavy_top = ModelRating(
        intelligence=93,
        coding=94,
        agentic=92,
        speed=62,
        cost_efficiency=50,
        context_handling=95,
        billing_tier="subscription",
        worker_tier=2,
    )

    assert worker_mode_rank_score(quota_light, "frugal") > worker_mode_rank_score(
        heavy_top, "frugal"
    )
    assert worker_mode_rank_score(quota_light, "balanced") > worker_mode_rank_score(
        heavy_top, "balanced"
    )
    assert worker_mode_rank_score(heavy_top, "quality") > worker_mode_rank_score(
        quota_light, "quality"
    )


def test_validate_route_rejects_tier_one_architect() -> None:
    available = AvailableModels(
        herdr={"pi": ["openai/gpt-6-luna", "openai/gpt-6.1-terra", "openai/gpt-6.1-sol"]}
    )
    ratings = {
        "pi": {
            "openai/gpt-6-luna": _rating(
                intelligence=70, coding=68, agentic=66, worker_tier=1, family="openai"
            ),
            "openai/gpt-6.1-terra": _rating(
                intelligence=82, coding=80, agentic=81, worker_tier=2, family="openai"
            ),
            "openai/gpt-6.1-sol": _rating(
                intelligence=92, coding=93, agentic=91, worker_tier=3, family="openai"
            ),
        }
    }

    def plan(architect: str) -> RoutingPlan:
        return RoutingPlan(
            workflow="ergo",
            target_mode="herdr",
            scout="pi:openai/gpt-6-luna",
            architect=architect,
            reviewer="pi:openai/gpt-6.1-terra",
        )

    bad = validate_route(plan("pi:openai/gpt-6-luna"), available=available, ratings=ratings)
    assert bad.valid is False
    assert any("Architect must be Tier 2 or Tier 3" in err for err in bad.errors)

    for ok_model in ("pi:openai/gpt-6.1-terra", "pi:openai/gpt-6.1-sol"):
        assert validate_route(plan(ok_model), available=available, ratings=ratings).valid is True
