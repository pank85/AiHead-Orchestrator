"""Offline Codex and Kilo contracts verified against CLI `--help` (no model calls)."""

from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.cli.setup_catalog import discover_model_catalog
from herdr_master.core.catalog import (
    CatalogError,
    build_agent_args,
    describe_herdr_agent,
    describe_native_agent,
    supported_harnesses,
    worker_capability,
)
from herdr_master.core.config import HerdrMasterConfig, load_project_config, save_project_config
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind
from herdr_master.core.slug import parse_binding
from herdr_master.pipeline.ai_head import build_headless_cli_argv


def test_codex_and_kilo_in_supported_harnesses():
    herdr = supported_harnesses(mode="herdr")
    native = supported_harnesses(mode="native")
    assert "codex" in herdr
    assert "kilo" in herdr
    assert "codex" not in native
    assert "kilo" not in native


@pytest.mark.parametrize(
    ("slug", "expected_kind", "expected_model", "expected_effort"),
    [
        ("codex:gpt-5.4", HarnessKind.CODEX, "gpt-5.4", None),
        ("codex:gpt-5.3-codex:high", HarnessKind.CODEX, "gpt-5.3-codex", EffortLevel.HIGH),
        (
            "kilo:kilo/~anthropic/claude-sonnet-latest",
            HarnessKind.KILO,
            "kilo/~anthropic/claude-sonnet-latest",
            None,
        ),
        (
            "kilo:kilo/~openai/gpt-sol-latest:max",
            HarnessKind.KILO,
            "kilo/~openai/gpt-sol-latest",
            EffortLevel.MAX,
        ),
    ],
)
def test_codex_and_kilo_slug_parsing(slug, expected_kind, expected_model, expected_effort):
    binding = parse_binding(slug)
    assert binding.kind is expected_kind
    assert binding.model == expected_model
    assert binding.effort is expected_effort
    assert binding.to_slug() == slug


def test_kilo_cli_flags_and_capabilities():
    assert build_agent_args("kilo", "kilo/~anthropic/claude-sonnet-latest", "high") == [
        "--model",
        "kilo/~anthropic/claude-sonnet-latest",
    ]
    descriptor = describe_herdr_agent(
        "kilo", "kilo/~anthropic/claude-sonnet-latest", "high", "coder"
    )
    assert descriptor["launch_model"] == "kilo/~anthropic/claude-sonnet-latest"
    assert descriptor["prompt_prefix"] == "think harder"
    capability = worker_capability("kilo", "kilo/~anthropic/claude-sonnet-latest")
    assert capability["allowed_efforts"] == ["low", "medium", "high", "max"]
    assert "think harder" in capability["route_constraints"]
    with pytest.raises(CatalogError, match="no native adapter"):
        describe_native_agent(
            "kilo", "kilo/~anthropic/claude-sonnet-latest", "high", "coder", "kilo"
        )


def test_codex_cli_flags_and_capabilities():
    assert build_agent_args("codex", "gpt-5.4", None) == ["--model", "gpt-5.4"]
    with pytest.raises(CatalogError, match="no effort flag"):
        build_agent_args("codex", "gpt-5.4", "high")
    capability = worker_capability("codex", "gpt-5.4")
    assert capability["allowed_efforts"] == []
    assert capability["route_constraints"] == "no effort flag; route: --model gpt-5.4"
    with pytest.raises(CatalogError, match="no native adapter"):
        describe_native_agent("codex", "gpt-5.4", None, "coder", "codex")


@pytest.mark.parametrize(
    ("slug", "expected_kind", "expected_model"),
    [
        ("codex:gpt-5.4", HarnessKind.CODEX, "gpt-5.4"),
        (
            "kilo:kilo/~anthropic/claude-sonnet-latest:high",
            HarnessKind.KILO,
            "kilo/~anthropic/claude-sonnet-latest",
        ),
    ],
)
def test_codex_and_kilo_config_roundtrip_and_headless_guard(
    tmp_path, slug, expected_kind, expected_model
):
    config = HerdrMasterConfig()
    config.ai_head.binding = HarnessBinding.from_slug(slug)
    path = tmp_path / ".herdr-master.toml"
    save_project_config(config, path=path)
    restored = load_project_config(path).ai_head
    assert restored.harness is expected_kind
    assert restored.model == expected_model
    assert restored.binding.to_slug() == slug
    with pytest.raises(ValueError, match="does not support headless"):
        build_headless_cli_argv(restored, "not sent")


@pytest.mark.parametrize(
    ("harness", "model", "expected_kind"),
    [
        ("codex", "gpt-5.4", HarnessKind.CODEX),
        ("kilo", "kilo/~anthropic/claude-sonnet-latest", HarnessKind.KILO),
    ],
)
def test_codex_and_kilo_interactive_cli_override(tmp_path, harness, model, expected_kind):
    launch = AsyncMock(
        return_value={
            "session": "test",
            "workspace_id": "ws",
            "pane_id": "pane",
            "tab_id": "tab",
        }
    )
    with patch("herdr_master.cli.main.launch_ai_head", launch):
        result = CliRunner().invoke(
            app,
            [
                "aihead",
                "--harness",
                harness,
                "--model",
                model,
                "--no-attach",
                "--cwd",
                str(tmp_path),
            ],
        )
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["harness"] is expected_kind
    assert launch.await_args.kwargs["model"] == model


def test_setup_catalog_includes_codex_and_kilo():
    catalog = discover_model_catalog(probe_live=False)
    herdr_values = [opt.value for opt in catalog.herdr_harnesses]
    assert "codex" in herdr_values
    assert "kilo" in herdr_values
    assert [opt.value for opt in catalog.models("codex", mode="herdr")]
    assert [opt.value for opt in catalog.models("kilo", mode="herdr")]
