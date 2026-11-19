"""Tests for Pass 2: dynamic Architect worker pools, worker modes, and AiHead triage."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.cli.main import aihead_app
from herdr_master.cli.setup_catalog import discover_model_catalog
from herdr_master.cli.setup_tui import SetupTuiApp
from herdr_master.core.available_models import AvailableModels
from herdr_master.core.config import (
    ErgoConfig,
    HerdrMasterConfig,
    load_project_config,
    resolve_effective_worker_mode,
    save_project_config,
)
from herdr_master.core.model_ratings import ModelRating, derive_recommended_roles
from herdr_master.pipeline.squad import build_dynamic_coder_pool, resolve_squad
from herdr_master.pipeline.triage import (
    RoutingPlan,
    parse_json_payload,
    run_triage,
    select_triage_models,
    validate_route,
)


def _rating(
    *,
    intel: int,
    coding: int,
    agentic: int,
    speed: int,
    cost: int,
    ctx: int = 85,
    family: str = "openai",
    billing: str = "api",
    efforts: list[str] | None = None,
) -> ModelRating:
    r = ModelRating(
        intelligence=intel,
        coding=coding,
        agentic=agentic,
        speed=speed,
        cost_efficiency=cost,
        context_handling=ctx,
        family=family,
        billing_tier=billing,  # type: ignore[arg-type]
        context_window=200_000,
        supported_efforts=efforts if efforts is not None else ["low", "medium", "high", "max"],
        source="models_dev+swebench",
        confidence="high",
    )
    r.recommended_roles = derive_recommended_roles(r)
    return r


def _seeded_config(tmp_path: Path) -> Path:
    cfg = HerdrMasterConfig()
    cfg.ai_head.routing_profile = "balanced"
    cfg.models.available = AvailableModels(
        herdr={
            "agy": ["gemini-4-hydrogen-low", "gemini-4-neon-high", "claude-opus-5-5-high"],
            "pi": ["openai/gpt-6.1-sol", "xai/grok-4.7"],
            "kilo": ["kilo/kilo-auto/free"],
        },
        native={
            "agy": ["flash_lite", "flash", "pro"],
            "pi": ["openai/gpt-6.1-sol", "xai/grok-4.7"],
        },
    )
    cfg.models.ratings = {
        "agy": {
            "gemini-4-hydrogen-low": _rating(
                intel=76,
                coding=78,
                agentic=76,
                speed=96,
                cost=92,
                family="google",
                billing="subscription",
                efforts=[],
            ),
            "gemini-4-neon-high": _rating(
                intel=93,
                coding=94,
                agentic=91,
                speed=62,
                cost=60,
                family="google",
                billing="subscription",
                efforts=[],
            ),
            "claude-opus-5-5-high": _rating(
                intel=95,
                coding=96,
                agentic=94,
                speed=52,
                cost=48,
                family="anthropic",
                billing="subscription",
                efforts=[],
            ),
            "flash_lite": _rating(
                intel=65, coding=66, agentic=64, speed=98, cost=96, family="google"
            ),
            "flash": _rating(intel=82, coding=84, agentic=80, speed=86, cost=84, family="google"),
            "pro": _rating(intel=93, coding=94, agentic=92, speed=64, cost=62, family="google"),
        },
        "pi": {
            "openai/gpt-6.1-sol": _rating(
                intel=94, coding=95, agentic=92, speed=66, cost=60, family="openai"
            ),
            "xai/grok-4.7": _rating(
                intel=88, coding=90, agentic=87, speed=82, cost=78, family="xai"
            ),
        },
        "kilo": {
            "kilo/kilo-auto/free": _rating(
                intel=72, coding=74, agentic=70, speed=88, cost=99, family="unknown", billing="free"
            ),
        },
    }
    return save_project_config(cfg, tmp_path / ".herdr-master.toml")


def test_worker_mode_inheritance_and_tui_persistence(tmp_path: Path):
    assert (
        resolve_effective_worker_mode(None, section_mode="inherit", ai_head_profile="fast")
        == "fast"
    )
    assert (
        resolve_effective_worker_mode(None, section_mode="quality", ai_head_profile="fast")
        == "quality"
    )
    assert (
        resolve_effective_worker_mode("frugal", section_mode="quality", ai_head_profile="fast")
        == "frugal"
    )
    assert (
        resolve_effective_worker_mode(None, section_mode="inherit", ai_head_profile=None)
        == "balanced"
    )

    cfg_path = tmp_path / ".herdr-master.toml"
    tui = SetupTuiApp(config_path=cfg_path, catalog=discover_model_catalog(probe_live=False))

    # Set AiHead routing_profile -> quality, Ergo worker_mode -> frugal, Orch worker_mode -> fast
    tui._apply_field_value("aihead.routing_profile", "quality")
    tui._apply_field_value("ergo.worker_mode", "frugal")
    tui._apply_field_value("orch.worker_mode", "fast")
    assert tui.save() is True

    reloaded = load_project_config(cfg_path)
    assert reloaded.ai_head.routing_profile == "quality"
    assert reloaded.ergo is not None and reloaded.ergo.worker_mode == "frugal"
    assert reloaded.orchestration.worker_mode == "fast"
    assert reloaded.models.worker_pool is None
    assert reloaded.orchestration.worker_models == []


def test_dynamic_coder_pool_for_ergo_and_same_harness_orchestration(tmp_path: Path):
    cfg_path = _seeded_config(tmp_path)
    cfg = load_project_config(cfg_path)

    # Ergo (herdr): all enabled harnesses included, ranked by worker_mode!
    ergo_quality = resolve_squad(cfg_path, mode="herdr", worker_mode="quality")
    assert ergo_quality.sources["workers"] == "available_rated"
    assert ergo_quality.worker_mode == "quality"
    assert ergo_quality.squad.worker_pool[0].model == "claude-opus-5-5-high"
    assert "WORKER SELECTION MODE: quality" in ergo_quality.allowed_coder_models
    assert "I:95 C:96" in ergo_quality.allowed_coder_models

    ergo_frugal = resolve_squad(cfg_path, mode="herdr", worker_mode="frugal")
    assert ergo_frugal.squad.worker_pool[0].model == "kilo/kilo-auto/free"

    ergo_fast = resolve_squad(cfg_path, mode="herdr", worker_mode="fast")
    assert ergo_fast.squad.worker_pool[0].model == "gemini-4-hydrogen-low"

    # Orchestration (native pi): ONLY pi enabled models appear!
    pi_pool, src, _ = build_dynamic_coder_pool(
        available=cfg.models.available,
        ratings=cfg.models.ratings,
        mode="native",
        native_harness="pi",
        worker_mode="quality",
    )
    assert src == "available_rated"
    assert {w.kind.value for w in pi_pool} == {"pi"}
    assert [w.model for w in pi_pool] == ["openai/gpt-6.1-sol", "xai/grok-4.7"]

    # Orchestration on agy: subagents -> native.agy tiers; swarm -> herdr.agy exact slugs!
    agy_sub, _, _ = build_dynamic_coder_pool(
        available=cfg.models.available,
        ratings=cfg.models.ratings,
        mode="native",
        native_harness="agy",
        agy_mode="subagents",
        worker_mode="fast",
    )
    assert [w.model for w in agy_sub] == ["flash_lite", "flash", "pro"]

    agy_swarm, _, _ = build_dynamic_coder_pool(
        available=cfg.models.available,
        ratings=cfg.models.ratings,
        mode="native",
        native_harness="agy",
        agy_mode="swarm",
        worker_mode="quality",
    )
    assert {w.kind.value for w in agy_swarm} == {"agy"}
    assert agy_swarm[0].model == "claude-opus-5-5-high"

    # Explicit CLI --workers override still wins over dynamic available pool
    pinned = resolve_squad(
        cfg_path,
        mode="herdr",
        overrides={"workers": "pi:xai/grok-4.7"},
    )
    assert pinned.sources["workers"] == "override"
    assert [w.to_slug(include_effort=False) for w in pinned.squad.worker_pool] == [
        "pi:xai/grok-4.7"
    ]


def test_select_triage_models_native_and_non_native_hosts(tmp_path: Path):
    cfg_path = _seeded_config(tmp_path)
    cfg = load_project_config(cfg_path)

    # Native pi: cheap/fast grok-4.7 for Scout (low), strong gpt-6.1-sol for Router (medium)
    pi_sel = select_triage_models(
        "pi",
        available=cfg.models.available,
        ratings=cfg.models.ratings,
        routing_profile="balanced",
    )
    assert pi_sel.adapter == "native_subagent"
    assert pi_sel.scout_binding == "pi:xai/grok-4.7"
    assert pi_sel.scout_effort == "low"
    assert pi_sel.router_binding == "pi:openai/gpt-6.1-sol"
    assert pi_sel.router_effort == "medium"

    # Native agy (subagents): flash_lite -> pro
    agy_sub_sel = select_triage_models(
        "agy",
        available=cfg.models.available,
        ratings=cfg.models.ratings,
        agy_mode="subagents",
    )
    assert agy_sub_sel.adapter == "native_subagent"
    assert agy_sub_sel.scout_binding == "agy:flash_lite"
    assert agy_sub_sel.router_binding == "agy:pro"

    # Native agy (swarm): exact slugs gemini-4-hydrogen-low -> claude-opus-5-5-high
    agy_swarm_sel = select_triage_models(
        "agy",
        available=cfg.models.available,
        ratings=cfg.models.ratings,
        agy_mode="swarm",
    )
    assert agy_swarm_sel.adapter == "native_subagent"
    assert agy_swarm_sel.scout_binding == "agy:gemini-4-hydrogen-low"
    assert agy_swarm_sel.scout_effort is None  # effort encoded in slug
    assert agy_swarm_sel.router_binding == "agy:claude-opus-5-5-high"

    # Non-native kilo: automatic headless_cli fallback using headless-capable rated pool
    kilo_sel = select_triage_models(
        "kilo",
        available=cfg.models.available,
        ratings=cfg.models.ratings,
    )
    assert kilo_sel.adapter == "headless_cli"
    assert kilo_sel.scout_binding == "agy:gemini-4-hydrogen-low"
    assert kilo_sel.router_binding == "agy:claude-opus-5-5-high"


def test_validate_route_enforces_allowlists_native_core_and_family_advisories(tmp_path: Path):
    cfg_path = _seeded_config(tmp_path)
    cfg = load_project_config(cfg_path)

    # Valid cross-family Ergo plan
    valid_ergo = RoutingPlan(
        workflow="ergo",
        worker_mode="balanced",
        scout="agy:gemini-4-hydrogen-low",
        architect="pi:openai/gpt-6.1-sol:high",
        reviewer="agy:claude-opus-5-5-high",
        rationale="Complex multi-module feature.",
    )
    res_ok = validate_route(valid_ergo, available=cfg.models.available, ratings=cfg.models.ratings)
    assert res_ok.valid is True
    assert res_ok.errors == []
    assert res_ok.advisories == []

    # Hallucinated model rejected
    bad_model = RoutingPlan(
        workflow="direct",
        direct_binding="pi:openai/invented-model-9000",
    )
    res_bad = validate_route(bad_model, available=cfg.models.available)
    assert res_bad.valid is False
    assert any("not in [models.available.herdr.pi]" in e for e in res_bad.errors)

    # Cross-harness model inside /orchestration rejected
    bad_orch = RoutingPlan(
        workflow="orchestration",
        target_mode="native",
        target_harness="pi",
        scout="agy:gemini-4-hydrogen-low",
        architect="pi:openai/gpt-6.1-sol:high",
        reviewer="pi:xai/grok-4.7:medium",
    )
    res_orch = validate_route(bad_orch, available=cfg.models.available)
    assert res_orch.valid is False
    assert any("single harness 'pi'" in e for e in res_orch.errors)

    # Core backend rejects legacy /ergo
    res_core = validate_route(valid_ergo, available=cfg.models.available, backend="core")
    assert res_core.valid is False
    assert any("Herdr Core" in e for e in res_core.errors)

    # Same-family Architect & Reviewer emits advisory (still valid)
    same_fam = RoutingPlan(
        workflow="ergo",
        scout="agy:gemini-4-hydrogen-low",
        architect="agy:gemini-4-neon-high",
        reviewer="agy:gemini-4-hydrogen-low",
    )
    res_fam = validate_route(same_fam, available=cfg.models.available)
    assert res_fam.valid is True
    assert any("share model family 'google'" in a for a in res_fam.advisories)


def test_run_triage_end_to_end_self_repair_and_cli(tmp_path: Path):
    cfg_path = _seeded_config(tmp_path)
    calls: list[tuple[str, str | None]] = []

    def fake_runner(binding: str, effort: str | None, prompt: str, cwd: str) -> str:
        calls.append((binding, effort))
        if len(calls) == 1:
            # Stage-1 Mini-Scout
            return json.dumps(
                {
                    "scope_size": "medium",
                    "estimated_files": 5,
                    "touched_areas": ["src/herdr_master/core/", "tests/"],
                    "requires_design": True,
                    "ambiguity": "low",
                    "multi_job_detected": False,
                    "test_framework": "pytest",
                    "summary": "Clean modular refactor across 5 files.",
                }
            )
        if len(calls) == 2:
            # Stage-2 attempt 1: hallucinates an unallowlisted model -> triggers bounded retry
            return json.dumps(
                {
                    "workflow": "ergo",
                    "confidence": "high",
                    "rationale": "Initial bad attempt.",
                    "worker_mode": "balanced",
                    "scout": "pi:openai/not-in-allowlist",
                    "architect": "pi:openai/gpt-6.1-sol:high",
                    "reviewer": "agy:claude-opus-5-5-high",
                }
            )
        # Stage-2 attempt 2: fixes the error
        assert "PREVIOUS ROUTE VALIDATION FAILED" in prompt
        return json.dumps(
            {
                "workflow": "ergo",
                "confidence": "high",
                "rationale": "Cross-harness feature with distinct Architect & Reviewer.",
                "worker_mode": "quality",
                "scout": "agy:gemini-4-hydrogen-low",
                "architect": "pi:openai/gpt-6.1-sol:high",
                "reviewer": "agy:claude-opus-5-5-high",
            }
        )

    res = run_triage(
        "Add model evaluation and smart triage",
        cwd=tmp_path,
        project_path=cfg_path,
        host_harness="kilo",
        runner_fn=fake_runner,
    )
    assert res.ok is True
    assert res.retries_used == 1
    assert res.triage_models.adapter == "headless_cli"
    assert res.routing_plan is not None
    assert res.routing_plan.workflow == "ergo"
    assert res.routing_plan.worker_mode == "quality"
    assert res.artifact_dir is not None
    assert (Path(res.artifact_dir) / "scout.json").is_file()
    assert (Path(res.artifact_dir) / "route.json").is_file()

    # Verify CLI subcommands: triage-models & validate-route
    runner = CliRunner()
    tm_res = runner.invoke(
        catalog_app,
        ["triage-models", "--harness", "pi", "--project", str(cfg_path), "--json"],
    )
    assert tm_res.exit_code == 0, tm_res.output
    tm_data = json.loads(tm_res.stdout)
    assert tm_data["scout_binding"] == "pi:xai/grok-4.7"
    assert tm_data["router_binding"] == "pi:openai/gpt-6.1-sol"

    route_file = Path(res.artifact_dir) / "route.json"
    vr_res = runner.invoke(
        catalog_app,
        ["validate-route", "--from-json", str(route_file), "--project", str(cfg_path), "--json"],
    )
    assert vr_res.exit_code == 0, vr_res.output
    assert json.loads(vr_res.stdout)["valid"] is True

    # Verify top-level `aihead triage --help` is wired
    help_res = runner.invoke(aihead_app, ["triage", "--help"])
    assert help_res.exit_code == 0
    assert "--task" in help_res.output
    assert "--profile" in help_res.output

    # Verify ErgoConfig worker_mode round-trip
    cfg = load_project_config(cfg_path)
    cfg.ergo = ErgoConfig(worker_mode="fast")
    save_project_config(cfg, cfg_path)
    assert load_project_config(cfg_path).ergo.worker_mode == "fast"


def test_parse_json_payload_handles_pi_jsonl_stream_without_user_echo():
    stream = "\n".join(
        [
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": 'Prompt schema: {"ignored": 1}'}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": '{"scope_size": "atomic"}'}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "turn_end",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": '{"scope_size": "atomic"}'}],
                    },
                }
            ),
            json.dumps({"type": "agent_settled", "aborted": False}),
        ]
    )
    assert parse_json_payload(stream) == {"scope_size": "atomic"}
