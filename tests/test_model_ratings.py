"""Unit tests for core model evaluation profiles, inheritance, and calibration."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pytest
from pydantic import ValidationError

from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import (
    HerdrMasterConfig,
    load_effective_models_state,
    load_project_config,
    save_project_config,
)
from herdr_master.core.model_ratings import (
    ModelEvaluationConfig,
    ModelRating,
    apply_effort_calibration,
    derive_recommended_roles,
    derive_worker_tier,
    effective_worker_tier,
    detect_model_family,
    find_unrated_models,
    format_rated_models_table,
    infer_supported_efforts,
    lookup_rating,
    normalize_model_slug,
    score_context_handling,
    score_cost_efficiency,
    split_effort_suffix,
)


def _sample_rating(**overrides) -> ModelRating:
    base = dict(
        intelligence=90,
        coding=92,
        agentic=89,
        speed=72,
        cost_efficiency=68,
        context_handling=86,
        family="openai",
        billing_tier="api",
        context_window=200_000,
        supports_reasoning=True,
        supported_efforts=["low", "medium", "high", "max"],
        source="models_dev+swebench",
        confidence="high",
    )
    base.update(overrides)
    rating = ModelRating(**base)
    if not rating.recommended_roles:
        rating.recommended_roles = derive_recommended_roles(rating)
    return rating


@pytest.mark.parametrize("value", [True, False, 1.0, "1", 0, 4, -1])
def test_worker_tier_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        _sample_rating(worker_tier=value)


def test_worker_tier_lock_and_legacy_round_trip():
    with pytest.raises(ValidationError):
        _sample_rating(worker_tier_override=True)
    legacy = _sample_rating(extra_provenance="kept")
    assert legacy.worker_tier is None
    assert effective_worker_tier(legacy) == 3
    restored = ModelRating.model_validate(legacy.model_dump())
    assert restored.model_extra["extra_provenance"] == "kept"
    assert restored.billing_tier == "api" and restored.worker_tier is None
    assert effective_worker_tier(_sample_rating(worker_tier=1)) == 1


@pytest.mark.parametrize("capability,speed,cost,expected", [
    (74, 10, 10, 1), (75, 79, 80, 2), (87, 80, 80, 1),
    (87, 80, 79, 2), (88, 100, 100, 3),
])
def test_worker_tier_calibration_boundaries(capability, speed, cost, expected):
    rating = _sample_rating(intelligence=capability, coding=capability,
                            agentic=capability, speed=speed, cost_efficiency=cost)
    assert derive_worker_tier(rating) == expected


def test_model_rating_clamping_badge_and_staleness():
    r = ModelRating(
        intelligence=110,
        coding=-5,
        agentic=80.6,
        speed=75,
        cost_efficiency=60,
        context_handling=85,
        evaluated_at="2026-01-01T00:00:00Z",
        manual_override=True,
    )
    assert r.intelligence == 100
    assert r.coding == 0
    assert r.agentic == 81
    assert r.compact_badge() == "I:100 C:0 S:75 $:60*"

    now = datetime(2026, 2, 15, tzinfo=timezone.utc)
    assert r.is_stale(30, now=now) is True
    assert r.is_stale(60, now=now) is False

    recent = ModelRating(
        intelligence=80,
        coding=80,
        agentic=80,
        speed=80,
        cost_efficiency=80,
        context_handling=80,
        evaluated_at=(now - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
    )
    assert recent.is_stale(30, now=now) is False


def test_model_rating_rejects_invalid_harness_or_billing():
    with pytest.raises(ValidationError, match="Unknown harness key"):
        ModelEvaluationConfig(billing={"not-a-harness": "api"})
    with pytest.raises(ValidationError, match="Invalid billing tier"):
        ModelEvaluationConfig(billing={"pi": "enterprise"})
    with pytest.raises(ValidationError, match="Unknown harness key"):
        HerdrMasterConfig.model_validate(
            {"models": {"ratings": {"bogus": {"m1": _sample_rating().model_dump()}}}}
        )


def test_ratings_and_evaluation_round_trip_without_affecting_unrestricted_allowlist(tmp_path: Path):
    cfg = HerdrMasterConfig()
    cfg.models.evaluation = ModelEvaluationConfig(
        billing={"agy": "subscription", "pi": "api", "kilo": "free"}
    )
    cfg.models.ratings = {
        "pi": {"openai/gpt-6.1-sol": _sample_rating()},
        "agy": {
            "gemini-4-neon-high": _sample_rating(
                family="google",
                billing_tier="subscription",
                supported_efforts=[],
            )
        },
    }
    path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    parsed = tomllib.loads(text)

    assert "available" not in parsed.get("models", {})
    assert parsed["models"]["evaluation"]["billing"]["agy"] == "subscription"
    assert parsed["models"]["ratings"]["pi"]["openai/gpt-6.1-sol"]["coding"] == 92

    reloaded = load_project_config(path)
    assert reloaded.models.available is None
    assert reloaded.models.evaluation is not None
    assert reloaded.models.evaluation.billing_for("kilo") == "free"
    assert reloaded.models.evaluation.billing_for("claude") == "api"
    found = lookup_rating(reloaded.models.ratings, "pi", "openai/gpt-6.1-sol")
    assert found is not None
    assert found.intelligence == 90
    assert found.family == "openai"


def test_ancestor_inheritance_for_ratings_billing_and_available(tmp_path: Path):
    home_dir = tmp_path / "home"
    proj_dir = home_dir / "projects" / "app"
    proj_dir.mkdir(parents=True)

    home_cfg = HerdrMasterConfig()
    home_cfg.models.available = AvailableModels(
        herdr={"pi": ["openai/gpt-6.1-sol", "xai/grok-4.7"]}
    )
    home_cfg.models.evaluation = ModelEvaluationConfig(
        billing={"pi": "api", "agy": "subscription"}
    )
    home_cfg.models.ratings = {
        "pi": {
            "openai/gpt-6.1-sol": _sample_rating(intelligence=94, coding=95),
            "xai/grok-4.7": _sample_rating(intelligence=88, coding=90, family="xai"),
        }
    }
    home_path = save_project_config(home_cfg, home_dir / ".herdr-master.toml")

    # Project overrides one rating and one billing tier, but omits [models.available]
    proj_cfg = HerdrMasterConfig()
    proj_cfg.models.evaluation = ModelEvaluationConfig(billing={"pi": "subscription"})
    proj_cfg.models.ratings = {
        "pi": {
            "xai/grok-4.7": _sample_rating(
                intelligence=91, coding=93, family="xai", manual_override=True
            )
        }
    }
    proj_path = save_project_config(proj_cfg, proj_dir / ".herdr-master.toml")

    # Raw project load stays unmerged
    raw_proj = load_project_config(proj_path)
    assert raw_proj.models.available is None
    assert lookup_rating(raw_proj.models.ratings, "pi", "openai/gpt-6.1-sol") is None

    # Effective state inherits from ancestor
    eff = load_effective_models_state(proj_path)
    assert eff.inherited_paths == (home_path.resolve(),)
    assert eff.available is not None
    assert eff.available.herdr == {"pi": ["openai/gpt-6.1-sol", "xai/grok-4.7"]}
    assert eff.evaluation.billing_for("agy") == "subscription"
    assert eff.evaluation.billing_for("pi") == "subscription"  # project wins

    gpt = lookup_rating(eff.ratings, "pi", "openai/gpt-6.1-sol")
    grok = lookup_rating(eff.ratings, "pi", "xai/grok-4.7")
    assert gpt is not None and gpt.intelligence == 94
    assert grok is not None and grok.intelligence == 91 and grok.manual_override is True


def test_slug_normalization_effort_splitting_and_family_detection():
    assert split_effort_suffix("gemini-4-neon-xhigh") == ("gemini-4-neon", "max")
    assert split_effort_suffix("gemini-4-hydrogen-low") == ("gemini-4-hydrogen", "low")
    assert split_effort_suffix("openai/gpt-6.1-sol") == ("openai/gpt-6.1-sol", None)

    norm_agy = normalize_model_slug("agy", "gemini-4-neon-xhigh")
    assert norm_agy.base_model == "gemini-4-neon"
    assert norm_agy.encoded_effort == "max"
    assert norm_agy.family == "google"
    assert norm_agy.is_free_route is False

    norm_kilo = normalize_model_slug("kilo", "kilo/~anthropic/claude-sonnet-latest")
    assert norm_kilo.family == "anthropic"
    assert "anthropic/claude-sonnet" in norm_kilo.canonical_candidates

    norm_free = normalize_model_slug("opencode", "opencode/beam-free")
    assert norm_free.is_free_route is True

    norm_claude = normalize_model_slug("claude", "opus")
    assert norm_claude.family == "anthropic"
    assert "anthropic/claude-opus-4-6" in norm_claude.canonical_candidates

    assert detect_model_family("pi", "xai/grok-4.7") == "xai"
    assert detect_model_family("codex", "o3") == "openai"
    assert detect_model_family("agy", "flash_lite") == "google"

    # Effort inference respects agy encoded suffixes and codex no-effort
    assert infer_supported_efforts("agy", "gemini-4-neon-high", mode="herdr") == []
    assert infer_supported_efforts("pi", "openai/gpt-6.1-sol", mode="herdr") == [
        "low",
        "medium",
        "high",
        "max",
    ]
    assert infer_supported_efforts("codex", "gpt-5.4", mode="herdr") == []


def test_effort_calibration_is_monotonic():
    base = _sample_rating(
        intelligence=86,
        coding=86,
        agentic=85,
        speed=75,
        cost_efficiency=74,
        family="google",
        billing_tier="subscription",
    )
    low = apply_effort_calibration(base, "low", harness="agy", model_id="gemini-4-neon-low")
    med = apply_effort_calibration(base, "medium", harness="agy", model_id="gemini-4-neon-medium")
    high = apply_effort_calibration(base, "high", harness="agy", model_id="gemini-4-neon-high")
    xhigh = apply_effort_calibration(base, "xhigh", harness="agy", model_id="gemini-4-neon-xhigh")

    assert low.intelligence < med.intelligence < high.intelligence < xhigh.intelligence
    assert low.coding < med.coding < high.coding < xhigh.coding
    assert low.speed > med.speed > high.speed > xhigh.speed
    assert low.cost_efficiency > med.cost_efficiency > high.cost_efficiency > xhigh.cost_efficiency
    assert low.supported_efforts == []
    assert xhigh.supported_efforts == []


def test_billing_aware_cost_and_context_scoring():
    free_score = score_cost_efficiency("free", intelligence=80)
    api_cheap = score_cost_efficiency(
        "api", input_price_per_1m=0.15, output_price_per_1m=0.60, intelligence=80
    )
    api_expensive = score_cost_efficiency(
        "api", input_price_per_1m=15.0, output_price_per_1m=75.0, intelligence=95
    )
    sub_low = score_cost_efficiency("subscription", intelligence=80, encoded_effort="low")
    sub_max = score_cost_efficiency("subscription", intelligence=94, encoded_effort="max")

    assert free_score >= 95
    assert api_cheap > api_expensive
    assert sub_low > sub_max
    assert score_context_handling(1_000_000, 90) > score_context_handling(32_000, 90)


def test_find_unrated_models_incremental_manual_and_stale():
    now = datetime(2026, 11, 19, tzinfo=timezone.utc)
    fresh_ts = (now - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    old_ts = (now - timedelta(days=45)).isoformat().replace("+00:00", "Z")

    ratings = {
        "pi": {
            "openai/gpt-6.1-sol": _sample_rating(evaluated_at=fresh_ts, manual_override=False),
            "xai/grok-4.7": _sample_rating(evaluated_at=old_ts, manual_override=False),
            "openai/gpt-6-luna": _sample_rating(evaluated_at=old_ts, manual_override=True),
        }
    }
    targets = {
        "pi": [
            "openai/gpt-6.1-sol",
            "xai/grok-4.7",
            "openai/gpt-6-luna",
            "openai/gpt-6.1-terra",
        ]
    }

    # Default incremental: only missing model
    assert find_unrated_models(targets, ratings, now=now) == {
        "pi": ["openai/gpt-6.1-terra"]
    }
    # max_age_days=30: includes stale non-manual + missing, still protects manual_override
    assert find_unrated_models(targets, ratings, max_age_days=30, now=now) == {
        "pi": ["xai/grok-4.7", "openai/gpt-6.1-terra"]
    }
    # refresh=True: re-evaluates all non-manual, still protects manual_override
    assert find_unrated_models(targets, ratings, refresh=True, now=now) == {
        "pi": ["openai/gpt-6.1-sol", "xai/grok-4.7", "openai/gpt-6.1-terra"]
    }
    # force=True: includes manual_override too
    assert find_unrated_models(targets, ratings, force=True, now=now) == targets

    table = format_rated_models_table(targets, ratings)
    assert "pi:openai/gpt-6.1-sol" in table
    assert "pi:openai/gpt-6.1-terra | [unrated]" in table
