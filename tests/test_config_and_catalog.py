"""Essential tests for config inheritance, slug parsing, catalog, and available models."""

from __future__ import annotations

from pathlib import Path

import pytest

from herdr_master.core.available_models import (
    AvailableModels,
    enabled_model_ids,
    filter_model_ids,
    materialize_toggle,
)
from herdr_master.core.catalog import CatalogError, get_harness, normalize_role, validate_mode
from herdr_master.core.config import (
    HerdrMasterConfig,
    load_effective_project_config,
    load_project_config,
    save_project_config,
)
from herdr_master.core.models import EffortLevel, HarnessKind
from herdr_master.core.slug import InvalidBindingSlugError, format_binding, parse_binding


def test_effective_config_inherits_home_values_without_masking_project_overrides(
    isolated_home: Path,
) -> None:
    """Omitted project keys inherit from home; explicit project values still win."""
    home_cfg = isolated_home / ".herdr-master.toml"
    home_cfg.write_text(
        """
[ai_head]
automode = true
routing_profile = "frugal"

[orchestration]
harness = "agy"
agy_mode = "swarm"
worker_mode = "fast"

[ergo]
worker_mode = "quality"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    project_dir = isolated_home / "repo"
    project_dir.mkdir()
    project_cfg = project_dir / ".herdr-master.toml"
    project_cfg.write_text(
        """
[ai_head]
routing_profile = "quality"
""".strip()
        + "\n",
        encoding="utf-8",
    )

    raw = load_project_config(project_cfg)
    assert raw.ai_head.routing_profile == "quality"
    assert raw.ai_head.automode is False  # raw loader does not inherit

    effective = load_effective_project_config(project_cfg)
    assert effective.ai_head.automode is True
    assert effective.ai_head.routing_profile == "quality"
    assert effective.orchestration.agy_mode == "swarm"
    assert effective.orchestration.worker_mode == "fast"
    assert effective.ergo is not None
    assert effective.ergo.worker_mode == "quality"


def test_config_roundtrip_save_and_load(tmp_path: Path) -> None:
    cfg = HerdrMasterConfig()
    cfg.ai_head.automode = True
    cfg.ai_head.routing_profile = "fast"
    cfg.orchestration.agy_mode = "swarm"

    target = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, target)
    loaded = load_project_config(target)

    assert loaded.ai_head.automode is True
    assert loaded.ai_head.routing_profile == "fast"
    assert loaded.orchestration.agy_mode == "swarm"


@pytest.mark.parametrize(
    ("raw", "expected_kind", "expected_model", "expected_effort"),
    [
        ("pi:openai/gpt-6.1-sol:high", HarnessKind.PI, "openai/gpt-6.1-sol", EffortLevel.HIGH),
        ("agy:gemini-4-neon-medium", HarnessKind.AGY, "gemini-4-neon-medium", None),
        ("claude:sonnet:low", HarnessKind.CLAUDE, "sonnet", EffortLevel.LOW),
        ("kilo:kilo/kilo-auto/free", HarnessKind.KILO, "kilo/kilo-auto/free", None),
    ],
)
def test_binding_slug_parse_and_format(
    raw: str,
    expected_kind: HarnessKind,
    expected_model: str,
    expected_effort: EffortLevel | None,
) -> None:
    binding = parse_binding(raw)
    assert binding.kind == expected_kind
    assert binding.model == expected_model
    assert binding.effort == expected_effort
    assert format_binding(binding) == raw


@pytest.mark.parametrize("bad", ["", "noslash", "unknown:model", "pi:", ":model"])
def test_invalid_binding_slug_rejected(bad: str) -> None:
    with pytest.raises(InvalidBindingSlugError):
        parse_binding(bad)


def test_catalog_harnesses_modes_and_roles() -> None:
    for kind in ("agy", "pi", "claude", "opencode", "hermes", "codex", "kilo"):
        spec = get_harness(kind)
        assert spec.kind == kind

    assert validate_mode("herdr") == "herdr"
    assert validate_mode("native") == "native"
    assert normalize_role("coder") == "coder"

    with pytest.raises(CatalogError):
        get_harness("nonexistent-harness")
    with pytest.raises(CatalogError):
        validate_mode("invalid-mode")


def test_available_models_filtering_and_toggle() -> None:
    discovered = ["m1", "m2", "m3"]
    avail = AvailableModels(herdr={"pi": ["m1", "m3"]})

    assert enabled_model_ids(avail, "herdr", "pi") == ["m1", "m3"]
    assert filter_model_ids(avail, "herdr", "pi", discovered) == ["m1", "m3"]
    assert filter_model_ids(avail, "herdr", "pi", discovered, current="m2") == ["m1", "m3", "m2"]

    toggled_off = materialize_toggle(avail, "herdr", "pi", "m1", False, discovered)
    assert enabled_model_ids(toggled_off, "herdr", "pi") == ["m3"]

    toggled_on = materialize_toggle(toggled_off, "herdr", "pi", "m2", True, discovered)
    assert enabled_model_ids(toggled_on, "herdr", "pi") == ["m3", "m2"]
