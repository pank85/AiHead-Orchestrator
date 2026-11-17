"""Offline Codex and Kilo contracts verified against CLI `--help` (no model calls)."""

import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli import setup_catalog
from herdr_master.cli.main import app
from herdr_master.cli.setup_catalog import ChoiceOption, discover_model_catalog
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


def test_codex_live_probe_paginates_and_cleans_up(monkeypatch):
    script = """
import json, sys
def receive(): return json.loads(sys.stdin.readline())
def send(value): print(json.dumps(value), flush=True)
assert receive()["method"] == "initialize"
send({"id": 1, "result": {}})
assert receive()["method"] == "initialized"
request = receive()
assert request["method"] == "model/list" and request["params"] == {}
send({"method": "notice", "params": {}})
send({"id": request["id"], "result": {"data": [{"model": "live-one"}], "nextCursor": "next"}})
request = receive()
assert request["params"] == {"cursor": "next"}
send({"id": request["id"], "result": {
    "data": [{"model": "live-two"}, {"model": "live-one"}], "nextCursor": None}})
sys.stdin.read()
"""
    real_popen = subprocess.Popen
    processes = []

    def spawn(*args, **kwargs):
        proc = real_popen([sys.executable, "-u", "-c", script], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(setup_catalog.subprocess, "Popen", spawn)
    assert [option.value for option in setup_catalog._probe_codex_models()] == [
        "live-one",
        "live-two",
    ]
    assert processes[0].poll() is not None


@pytest.mark.parametrize(
    "script",
    [
        "import time; time.sleep(10)",
        "print('not json', flush=True)",
        "import json; print(json.dumps({'id': 1, 'error': {'message': 'failed'}}), flush=True)",
    ],
)
def test_codex_live_probe_failures_are_bounded(monkeypatch, script):
    real_popen = subprocess.Popen
    processes = []

    def spawn(*args, **kwargs):
        proc = real_popen([sys.executable, "-u", "-c", script], **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(setup_catalog.subprocess, "Popen", spawn)
    start = time.monotonic()
    assert setup_catalog._probe_codex_models(timeout=0.3) == []
    assert time.monotonic() - start < 2
    assert processes[0].poll() is not None


def test_codex_live_probe_missing_binary(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(setup_catalog.subprocess, "Popen", missing)
    assert setup_catalog._probe_codex_models() == []


def test_codex_live_models_are_in_catalog(monkeypatch):
    monkeypatch.setattr(setup_catalog, "_run_probe", lambda *args: None)
    monkeypatch.setattr(setup_catalog, "_probe_hermes_config_models", lambda: [])
    monkeypatch.setattr(
        setup_catalog,
        "_probe_codex_models",
        lambda: [
            ChoiceOption(value="codex-live-test", label="codex-live-test", detail="codex live")
        ],
    )
    catalog = discover_model_catalog()
    assert catalog.live_counts["codex"] == 1
    assert "codex-live-test" in [option.value for option in catalog.models("codex")]
    assert "gpt-5.4" in [option.value for option in catalog.models("codex")]
