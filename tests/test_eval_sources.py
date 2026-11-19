"""Offline fixture tests for zero-auth benchmark fetchers, cache, and matching."""

from __future__ import annotations

import json
from pathlib import Path

from herdr_master.core.eval_sources import (
    SourceCatalog,
    fetch_source_catalog,
    normalize_benchmark_key,
    parse_aider_polyglot,
    parse_livebench_csv,
    parse_models_dev,
    parse_openrouter_models,
    parse_swebench_leaderboards,
    synthesize_rating,
)
from herdr_master.core.model_ratings import normalize_model_slug

MODELS_DEV_FIXTURE = {
    "anthropic": {
        "id": "anthropic",
        "models": {
            "claude-sonnet-4-6": {
                "id": "claude-sonnet-4-6",
                "name": "Claude Sonnet 4.6",
                "reasoning": True,
                "tool_call": True,
                "cost": {"input": 3.0, "output": 15.0},
                "limit": {"context": 200000, "output": 16384},
            },
            "claude-metadata-only": {
                "id": "claude-metadata-only",
                "name": "Claude Metadata Only",
                "reasoning": False,
                "tool_call": True,
                "cost": {"input": 1.0, "output": 4.0},
                "limit": {"context": 128000},
            },
        },
    },
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

OPENROUTER_FIXTURE = {
    "data": [
        {
            "id": "google/gemini-2.5-flash",
            "canonical_slug": "google/gemini-2.5-flash",
            "name": "Gemini 2.5 Flash",
            "context_length": 1048576,
            "pricing": {"prompt": "0.00000015", "completion": "0.0000006"},
            "supported_parameters": ["reasoning", "tools"],
        },
        {
            "id": "anthropic/claude-sonnet-4.6",
            "canonical_slug": "anthropic/claude-sonnet-4-6",
            "name": "Claude Sonnet 4.6",
            "context_length": 200000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
            "supported_parameters": ["reasoning", "tools"],
        },
        {
            "id": "openai/gpt-5.4",
            "canonical_slug": "openai/gpt-5.4",
            "name": "GPT-5.4",
            "context_length": 256000,
            "pricing": {"prompt": "0.0000025", "completion": "0.00001"},
            "supported_parameters": ["reasoning", "tools"],
        },
    ]
}

SWEBENCH_FIXTURE = {
    "leaderboards": [
        {
            "name": "Lite",
            "results": [
                {"name": "Claude Sonnet 4.6", "resolved": 52.0},
            ],
        },
        {
            "name": "Verified",
            "results": [
                {"name": "OpenHands + Claude Sonnet 4.6 (20260115)", "resolved": 74.8},
                {"name": "GPT-5.4", "resolved": 71.2},
            ],
        },
    ]
}

AIDER_YAML_FIXTURE = """
- model: claude-sonnet-4-6 (thinking)
  pass_rate_2: 78.5
  total_cost: 28.4
- model: gpt-5.4
  pass_rate_2: 81.2
  total_cost: 22.1
- model: google/gemini-2.5-flash
  pass_rate_2: 61.0
  total_cost: 4.5
"""

LIVEBENCH_CSV_FIXTURE = """model,Global Average,Coding,Reasoning
claude-sonnet-4-6-20260115,74.2,76.8,75.0
gpt-5.4,77.1,79.5,80.2
gemini-2.5-flash,63.4,62.0,61.5
"""


def test_individual_parsers_extract_expected_metrics():
    md = parse_models_dev(MODELS_DEV_FIXTURE)
    assert "claude-sonnet-4.6" in md
    assert md["claude-sonnet-4.6"].context_window == 200000
    assert md["claude-sonnet-4.6"].input_price_per_1m == 3.0

    or_map = parse_openrouter_models(OPENROUTER_FIXTURE)
    assert "gemini-2.5-flash" in or_map
    assert or_map["gemini-2.5-flash"].speed_percentile is not None
    assert or_map["gemini-2.5-flash"].speed_percentile > or_map["gpt-5.4"].speed_percentile

    swe = parse_swebench_leaderboards(SWEBENCH_FIXTURE)
    # Verified (74.8) beats Lite (52.0)
    assert swe["claude-sonnet-4.6"].swebench_score == 74.8

    aider = parse_aider_polyglot(AIDER_YAML_FIXTURE)
    assert aider["gpt-5.4"].aider_score == 81.2

    lb = parse_livebench_csv(LIVEBENCH_CSV_FIXTURE)
    assert lb["gpt-5.4"].livebench_global == 77.1
    assert lb["gpt-5.4"].livebench_coding == 79.5


def test_source_catalog_matching_and_no_false_positive_for_codenames():
    cat = SourceCatalog()
    cat.merge_entries(parse_models_dev(MODELS_DEV_FIXTURE))
    cat.merge_entries(parse_openrouter_models(OPENROUTER_FIXTURE))
    cat.merge_entries(parse_swebench_leaderboards(SWEBENCH_FIXTURE))
    cat.merge_entries(parse_aider_polyglot(AIDER_YAML_FIXTURE))
    cat.merge_entries(parse_livebench_csv(LIVEBENCH_CSV_FIXTURE))

    # Alias match: claude:sonnet -> claude-sonnet-4-6
    sonnet_match = cat.match(normalize_model_slug("claude", "sonnet"))
    assert sonnet_match is not None
    ev, conf = sonnet_match
    assert conf == "high"
    assert ev.swebench_score == 74.8
    assert ev.aider_score == 78.5
    assert ev.context_window == 200000

    rating = synthesize_rating(
        normalize_model_slug("claude", "sonnet"),
        ev,
        confidence=conf,
        billing_tier="subscription",
    )
    assert rating is not None
    assert rating.coding >= 85
    assert rating.intelligence >= 85
    assert rating.billing_tier == "subscription"
    assert "swebench" in rating.source
    assert "aider" in rating.source

    # Metadata-only match yields None from synthesize_rating so Layer 3 handles quality scores
    meta_match = cat.match(normalize_model_slug("claude", "claude-metadata-only"))
    assert meta_match is not None
    meta_ev, _ = meta_match
    assert meta_ev.has_quality_benchmarks() is False
    assert (
        synthesize_rating(
            normalize_model_slug("claude", "claude-metadata-only"),
            meta_ev,
            confidence="medium",
            billing_tier="api",
        )
        is None
    )

    # Preview codename must NOT false-match gemini-2.5-flash!
    codename_match = cat.match(normalize_model_slug("agy", "gemini-4-hydrogen-low"))
    assert codename_match is None


def test_fetch_source_catalog_disk_cache_and_partial_failure_resilience(tmp_path: Path):
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> str:
        calls.append(url)
        if "models.dev" in url:
            return json.dumps(MODELS_DEV_FIXTURE)
        if "openrouter.ai" in url:
            return json.dumps(OPENROUTER_FIXTURE)
        if "SWE-bench" in url:
            return json.dumps(SWEBENCH_FIXTURE)
        if "Aider-AI" in url:
            return AIDER_YAML_FIXTURE
        raise OSError("LiveBench unreachable")

    cache_dir = tmp_path / "cache"
    cat1 = fetch_source_catalog(cache_dir=cache_dir, ttl_hours=24, http_get=fake_get)
    assert len(calls) == 5
    assert cat1.source_status["models_dev"] == "fetched"
    assert cat1.source_status["aider"] == "fetched"
    assert cat1.source_status["livebench"].startswith("error:")
    assert "gpt-5.4" in cat1.entries

    # Second call uses cache for the 4 succeeded sources; retries only failed uncached source
    calls.clear()
    cat2 = fetch_source_catalog(cache_dir=cache_dir, ttl_hours=24, http_get=fake_get)
    assert len(calls) == 1  # only livebench retried
    assert cat2.source_status["models_dev"] == "cached"
    assert cat2.source_status["swebench"] == "cached"
    assert "gpt-5.4" in cat2.entries

    assert (
        normalize_benchmark_key("OpenHands + Claude Sonnet 4.6 (20260115)")
        == "claude-sonnet-4.6"
    )
