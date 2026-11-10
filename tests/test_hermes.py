"""Offline Hermes contracts verified against `hermes --help` (no model calls)."""

from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import app
from herdr_master.core.catalog import (
    CatalogError,
    build_agent_args,
    describe_herdr_agent,
    describe_native_agent,
    worker_capability,
)
from herdr_master.core.config import HerdrMasterConfig, load_project_config, save_project_config
from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind
from herdr_master.pipeline.ai_head import build_headless_cli_argv


@pytest.mark.parametrize("effort", [None, "low", "medium", "high", "max", "xhigh"])
@pytest.mark.parametrize("skip", [False, True])
def test_hermes_cli_flags(effort, skip):
    expected = ["--provider", "openai-codex", "--model", "gpt-6.1-terra"]
    if effort:
        expected += ["--reasoning", "max" if effort == "xhigh" else effort]
    if skip:
        expected += ["--yolo"]
    assert build_agent_args(
        "hermes", "openai-codex/gpt-6.1-terra", effort, skip_permissions=skip
    ) == expected


@pytest.mark.parametrize("model", ["", "/model", "provider/"])
def test_hermes_rejects_invalid_model(model):
    with pytest.raises(CatalogError):
        build_agent_args("hermes", model)


def test_hermes_bare_and_nested_model_and_capabilities():
    assert build_agent_args("hermes", "gpt-6.1-terra", skip_permissions=False) == [
        "--model", "gpt-6.1-terra"
    ]
    assert build_agent_args("hermes", "provider/org/model", skip_permissions=False) == [
        "--provider", "provider", "--model", "org/model"
    ]
    descriptor = describe_herdr_agent(
        "hermes", "openai-codex/gpt-6.1-terra", "high", "master", skip_permissions=False
    )
    assert descriptor["launch_model"] == "gpt-6.1-terra"
    assert descriptor["prompt_prefix"] is None
    capability = worker_capability("hermes", "openai-codex/gpt-6.1-terra")
    assert capability["allowed_efforts"] == ["low", "medium", "high", "max"]
    with pytest.raises(CatalogError, match="no native adapter"):
        describe_native_agent("hermes", "gpt-6.1-terra", "high", "master", "hermes")


def test_hermes_config_slug_roundtrip_and_headless_guard(tmp_path):
    config = HerdrMasterConfig()
    config.ai_head.binding = HarnessBinding.from_slug(
        "hermes:openai-codex/gpt-6.1-terra:high"
    )
    config.ai_head.dangerously_skip_permissions = False
    path = tmp_path / ".herdr-master.toml"
    save_project_config(config, path=path)
    restored = load_project_config(path).ai_head
    assert restored.harness is HarnessKind.HERMES
    assert restored.model == "openai-codex/gpt-6.1-terra"
    assert restored.effort is EffortLevel.HIGH
    assert restored.binding.to_slug() == "hermes:openai-codex/gpt-6.1-terra:high"
    assert restored.dangerously_skip_permissions is False
    with pytest.raises(ValueError, match="does not support headless"):
        build_headless_cli_argv(restored, "not sent")


def test_hermes_interactive_cli_override(tmp_path):
    launch = AsyncMock(return_value={
        "session": "test", "workspace_id": "ws", "pane_id": "pane", "tab_id": "tab",
    })
    with patch("herdr_master.cli.main.launch_ai_head", launch):
        result = CliRunner().invoke(app, [
            "aihead", "--harness", "hermes", "--model", "openai-codex/gpt-6.1-terra",
            "--effort", "high", "--no-attach", "--no-skip-permissions", "--cwd", str(tmp_path),
        ])
    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["harness"] is HarnessKind.HERMES
    assert launch.await_args.kwargs["model"] == "openai-codex/gpt-6.1-terra"
    assert launch.await_args.kwargs["effort"] is EffortLevel.HIGH
    assert launch.await_args.kwargs["dangerously_skip_permissions"] is False
