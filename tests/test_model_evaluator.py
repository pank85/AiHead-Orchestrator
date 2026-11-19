"""End-to-end tests for incremental model evaluation, CLI commands, and Setup TUI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.cli.setup_catalog import ChoiceOption, ModelCatalog, discover_model_catalog
from herdr_master.cli.setup_tui import SetupTuiApp
from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import HerdrMasterConfig, load_project_config, save_project_config
from herdr_master.core.eval_sources import (
    SourceCatalog,
    parse_aider_polyglot,
    parse_livebench_csv,
    parse_models_dev,
    parse_openrouter_models,
    parse_swebench_leaderboards,
)
from herdr_master.core.model_ratings import ModelEvaluationConfig, ModelRating, lookup_rating
from herdr_master.pipeline.model_evaluator import (
    EvaluationResult,
    ProbeOutcome,
    evaluate_available_models,
)

_MODELS_DEV_FIXTURE = {
    "openai": {
        "id": "openai",
        "models": {
            "gpt-5.4": {
                "id": "gpt-5.4",
                "name": "GPT-5.4",
                "reasoning": True,
                "tool_call": True,
                "cost": {"input": 2.5, "output": 10.0},
                "limit": {"context": 256000},
            }
        },
    },
}

_OPENROUTER_FIXTURE = {
    "data": [
        {
            "id": "openai/gpt-5.4",
            "canonical_slug": "openai/gpt-5.4",
            "name": "GPT-5.4",
            "context_length": 256000,
            "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
            "supported_parameters": ["reasoning", "tools"],
        }
    ]
}

_SWEBENCH_FIXTURE = {
    "leaderboards": [
        {
            "name": "Verified",
            "results": [{"name": "GPT-5.4", "resolved": 71.2}],
        }
    ]
}

_AIDER_YAML_FIXTURE = """
- model: gpt-5.4
  pass_rate_2: 81.2
  total_cost: 22.1
"""

_LIVEBENCH_CSV_FIXTURE = """model,Global Average,Coding,Reasoning
gpt-5.4,77.1,79.5,80.2
"""


def _fixture_source_catalog() -> SourceCatalog:
    cat = SourceCatalog(source_status={"models_dev": "cached", "swebench": "cached"})
    cat.merge_entries(parse_models_dev(_MODELS_DEV_FIXTURE))
    cat.merge_entries(parse_openrouter_models(_OPENROUTER_FIXTURE))
    cat.merge_entries(parse_swebench_leaderboards(_SWEBENCH_FIXTURE))
    cat.merge_entries(parse_aider_polyglot(_AIDER_YAML_FIXTURE))
    cat.merge_entries(parse_livebench_csv(_LIVEBENCH_CSV_FIXTURE))
    return cat


def _make_catalog(cfg: HerdrMasterConfig) -> ModelCatalog:
    catalog = discover_model_catalog(cfg, probe_live=False)
    catalog.merge_live_models(
        "pi",
        [
            ChoiceOption("openai/gpt-5.4", "openai/gpt-5.4", "pi live | ctx 256K | thinking yes"),
            ChoiceOption(
                "custom/internal-pro",
                "custom/internal-pro",
                "pi live | ctx 128K | thinking yes",
            ),
        ],
    )
    return catalog


def test_incremental_evaluation_base_grouping_layer3_and_manual_override(tmp_path: Path):
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={
            "pi": ["openai/gpt-5.4", "custom/internal-pro"],
            "agy": ["gemini-4-neon-low", "gemini-4-neon-high", "gemini-4-neon-xhigh"],
        }
    )
    cfg.models.evaluation = ModelEvaluationConfig(billing={"agy": "subscription", "pi": "api"})
    path = save_project_config(cfg, tmp_path / ".herdr-master.toml")

    llm_calls: list[str] = []

    def fake_llm_eval(binding, prompt: str) -> str:
        llm_calls.append(prompt)
        return json.dumps(
            {
                "pi:custom/internal-pro": {
                    "intelligence": 84,
                    "coding": 86,
                    "agentic": 82,
                    "speed": 74,
                    "cost_efficiency": 70,
                    "context_handling": 78,
                    "family": "unknown",
                    "supports_reasoning": True,
                    "confidence": "low",
                    "notes": "Internal custom model.",
                },
                "agy:gemini-4-neon": {
                    "intelligence": 90,
                    "coding": 91,
                    "agentic": 88,
                    "speed": 76,
                    "cost_efficiency": 72,
                    "context_handling": 92,
                    "family": "google",
                    "context_window": 1_000_000,
                    "supports_reasoning": True,
                    "confidence": "medium",
                    "notes": "Strong Antigravity flagship family.",
                },
            }
        )

    res1 = evaluate_available_models(
        project_path=path,
        catalog=_make_catalog(cfg),
        source_catalog=_fixture_source_catalog(),
        llm_eval_fn=fake_llm_eval,
        inherit_ancestors=False,
    )

    assert res1.evaluated_count == 5
    assert res1.skipped_count == 0
    assert res1.unresolved_count == 0
    assert res1.saved_path == path.resolve()
    # Layer-3 LLM evaluator called ONCE in a single batch (base-model grouped!)
    assert len(llm_calls) == 1
    assert "openai/gpt-5.4" not in llm_calls[0]  # matched deterministically in Layers 1-2!
    assert "agy:gemini-4-neon" in llm_calls[0]

    reloaded = load_project_config(path)
    gpt = lookup_rating(reloaded.models.ratings, "pi", "openai/gpt-5.4")
    assert gpt is not None
    assert gpt.confidence == "high"
    assert "swebench" in gpt.source

    custom = lookup_rating(reloaded.models.ratings, "pi", "custom/internal-pro")
    assert custom is not None
    # Hard CLI metadata context_window=128000 preserved!
    assert custom.context_window == 128_000
    assert custom.source == "harness_eval"

    neon_low = lookup_rating(reloaded.models.ratings, "agy", "gemini-4-neon-low")
    neon_high = lookup_rating(reloaded.models.ratings, "agy", "gemini-4-neon-high")
    neon_xhigh = lookup_rating(reloaded.models.ratings, "agy", "gemini-4-neon-xhigh")
    assert neon_low and neon_high and neon_xhigh
    assert neon_low.intelligence < neon_high.intelligence < neon_xhigh.intelligence
    assert neon_low.speed > neon_high.speed > neon_xhigh.speed
    assert neon_low.supported_efforts == []

    # Second run is strictly incremental: 0 newly evaluated, 5 skipped, 0 LLM calls!
    llm_calls.clear()
    res2 = evaluate_available_models(
        project_path=path,
        catalog=_make_catalog(reloaded),
        source_catalog=_fixture_source_catalog(),
        llm_eval_fn=fake_llm_eval,
        inherit_ancestors=False,
    )
    assert res2.evaluated_count == 0
    assert res2.skipped_count == 5
    assert len(llm_calls) == 0

    # Mark one model manual_override=True and verify refresh skips it while force updates it
    reloaded.models.ratings["pi"]["openai/gpt-5.4"].manual_override = True
    reloaded.models.ratings["pi"]["openai/gpt-5.4"].coding = 99
    save_project_config(reloaded, path)

    res3 = evaluate_available_models(
        project_path=path,
        catalog=_make_catalog(reloaded),
        source_catalog=_fixture_source_catalog(),
        refresh=True,
        llm_eval_fn=fake_llm_eval,
        inherit_ancestors=False,
    )
    assert "openai/gpt-5.4" in res3.skipped_manual.get("pi", [])
    after_refresh = load_project_config(path)
    assert after_refresh.models.ratings["pi"]["openai/gpt-5.4"].coding == 99

    res4 = evaluate_available_models(
        project_path=path,
        catalog=_make_catalog(after_refresh),
        source_catalog=_fixture_source_catalog(),
        force=True,
        llm_eval_fn=fake_llm_eval,
        inherit_ancestors=False,
    )
    assert res4.evaluated_count == 5
    assert res4.skipped_manual == {}


def test_no_llm_and_opt_in_probe_behavior(tmp_path: Path):
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={"pi": ["openai/gpt-5.4", "custom/mystery-model"]}
    )
    path = save_project_config(cfg, tmp_path / ".herdr-master.toml")

    probed: list[tuple[str, str]] = []

    def fake_probe(harness: str, model_id: str) -> ProbeOutcome:
        probed.append((harness, model_id))
        return ProbeOutcome(
            harness=harness,
            model_id=model_id,
            ok=True,
            latency_sec=1.2,
            estimated_tps=144.0,
        )

    res = evaluate_available_models(
        project_path=path,
        catalog=_make_catalog(cfg),
        source_catalog=_fixture_source_catalog(),
        probe=True,
        use_llm=False,
        dry_run=True,
        probe_fn=fake_probe,
        inherit_ancestors=False,
    )
    assert ("pi", "openai/gpt-5.4") in probed
    assert res.evaluated_count == 1
    assert res.unresolved == {"pi": ["custom/mystery-model"]}
    assert res.saved_path is None
    gpt = res.evaluated["pi"]["openai/gpt-5.4"]
    assert "probe" in gpt.source
    assert gpt.output_tokens_per_sec == 144.0


def test_catalog_cli_evaluate_ratings_rate_and_available(tmp_path: Path, monkeypatch):
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(herdr={"pi": ["openai/gpt-5.4"]})
    path = save_project_config(cfg, tmp_path / ".herdr-master.toml")

    # Patch source fetcher & catalog discovery to stay fast and offline
    monkeypatch.setattr(
        "herdr_master.pipeline.model_evaluator.fetch_source_catalog",
        lambda **kwargs: _fixture_source_catalog(),
    )
    monkeypatch.setattr(
        "herdr_master.cli.catalog.discover_model_catalog",
        lambda c: discover_model_catalog(c, probe_live=False),
    )

    runner = CliRunner()

    # Before evaluation: available reports unrated_models
    av_before = runner.invoke(catalog_app, ["available", "--project", str(path), "--json"])
    assert av_before.exit_code == 0, av_before.output
    av_data = json.loads(av_before.stdout)
    assert av_data["unrated_models"] == {"pi": ["openai/gpt-5.4"]}
    assert av_data["ratings"] == {}

    # Evaluate via CLI
    ev_res = runner.invoke(
        catalog_app, ["evaluate", "--project", str(path), "--no-llm", "--json"]
    )
    assert ev_res.exit_code == 0, ev_res.output
    ev_data = json.loads(ev_res.stdout)
    assert ev_data["evaluated_count"] == 1
    assert ev_data["unresolved_count"] == 0

    # Ratings command shows stored rating
    rat_res = runner.invoke(catalog_app, ["ratings", "--project", str(path), "--json"])
    assert rat_res.exit_code == 0, rat_res.output
    rat_data = json.loads(rat_res.stdout)
    assert "openai/gpt-5.4" in rat_data["ratings"]["pi"]

    # Manual rate override
    rate_res = runner.invoke(
        catalog_app,
        [
            "rate",
            "--binding",
            "pi:openai/gpt-5.4",
            "--coding",
            "98",
            "--billing-tier",
            "subscription",
            "--notes",
            "User benchmarked favorite",
            "--project",
            str(path),
            "--json",
        ],
    )
    assert rate_res.exit_code == 0, rate_res.output
    rate_data = json.loads(rate_res.stdout)
    assert rate_data["rating"]["coding"] == 98
    assert rate_data["rating"]["billing_tier"] == "subscription"
    assert rate_data["rating"]["manual_override"] is True

    # After evaluation: unrated_models is empty
    av_after = runner.invoke(catalog_app, ["available", "--project", str(path), "--json"])
    assert av_after.exit_code == 0, av_after.output
    av_after_data = json.loads(av_after.stdout)
    assert av_after_data["unrated_models"] == {}
    assert av_after_data["ratings"]["pi"]["openai/gpt-5.4"]["coding"] == 98


def test_setup_tui_available_ratings_badges_billing_evaluate_and_inspect(tmp_path: Path):
    cfg_path = tmp_path / ".herdr-master.toml"
    catalog = discover_model_catalog(probe_live=False)
    sample_rating = ModelRating(
        intelligence=91,
        coding=93,
        agentic=90,
        speed=84,
        cost_efficiency=82,
        context_handling=92,
        family="google",
        billing_tier="subscription",
        context_window=1_000_000,
        source="models_dev+swebench",
        confidence="high",
        notes="Fast and capable.",
    )

    def fake_eval(**kwargs) -> EvaluationResult:
        first_agy = str(catalog.models("agy", mode="herdr")[0].value)
        return EvaluationResult(evaluated={"agy": {first_agy: sample_rating}})

    tui = SetupTuiApp(config_path=cfg_path, catalog=catalog, evaluate_fn=fake_eval)

    # Go to Available section (section 6)
    tui.drive(["6"])
    rows_before = tui._current_rows()
    first_toggle = next(r for r in rows_before if r.key == "available.toggle.0")
    assert first_toggle.badge == "[unrated]"

    # Trigger evaluation via hotkey 'e'
    tui.drive(["e"])
    assert tui.dirty is True
    assert "Evaluated 1 model(s)" in tui.status_message
    rows_after = tui._current_rows()
    first_toggle_after = next(r for r in rows_after if r.key == "available.toggle.0")
    assert "I:91 C:93 S:84 $:82" == first_toggle_after.badge

    # Move cursor to first model toggle (index 3) and press 'v' to open rating inspector
    tui.drive(["HOME", "DOWN", "DOWN", "DOWN", "v"])
    assert tui.rating_inspector is not None
    rendered_insp = tui.render(width=100, height=30)
    assert "Model Rating Details" in rendered_insp
    assert "Intelligence:  91/100" in rendered_insp
    assert "Fast and capable." in rendered_insp

    # Close inspector
    tui.drive(["ESC"])
    assert tui.rating_inspector is None

    # Change Billing Tier row for agy to subscription
    billing_idx = next(i for i, r in enumerate(tui._current_rows()) if r.key == "available.billing")
    tui.field_cursors["models"] = billing_idx
    tui.drive(["RIGHT"])
    assert tui.cfg.models.evaluation is not None
    assert tui.cfg.models.evaluation.billing_for("agy") == "subscription"

    # Save and verify persisted TOML has billing + rating
    tui.drive(["s"])
    assert tui.saved is True
    reloaded = load_project_config(cfg_path)
    assert reloaded.models.evaluation is not None
    assert reloaded.models.evaluation.billing_for("agy") == "subscription"
    first_agy = str(catalog.models("agy", mode="herdr")[0].value)
    assert lookup_rating(reloaded.models.ratings, "agy", first_agy) is not None
