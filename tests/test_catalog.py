"""Catalog identity, product-squad, and Herdr/native routing tests."""

import ast
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.core.catalog import HarnessKind as CatalogHarnessKind
from herdr_master.core.catalog import (
    build_agent_args,
    catalog_document,
    default_squad_data,
    describe_herdr_agent,
    describe_native_agent,
    get_harness,
)
from herdr_master.core.models import HarnessKind as ModelsHarnessKind
from herdr_master.core.models import Squad
from herdr_master.core.slug import parse_binding
from herdr_master.pipeline.squad import describe_agent

REPO = Path(__file__).resolve().parents[1]
SCOUT_ARGV = [
    "--model",
    "gemini-3.8-flash-medium",
    "--effort",
    "medium",
    "--dangerously-skip-permissions",
]
ARCHITECT_ARGV = [
    "--provider",
    "openai-codex",
    "--model",
    "gpt-6.1-sol",
    "--thinking",
    "max",
]
REVIEWER_ARGV = ["--provider", "xai", "--model", "grok-4.7", "--thinking", "high"]


def _json(result):
    stdout = result.stdout if result.stdout else result.output
    return json.loads(stdout)


def test_harness_kind_is_reexported_same_object():
    assert ModelsHarnessKind is CatalogHarnessKind
    assert [kind.value for kind in ModelsHarnessKind] == [
        "agy",
        "pi",
        "claude",
        "opencode",
        "codex",
        "gemini",
    ]


def test_catalog_module_does_not_import_models_or_pydantic():
    source = (REPO / "src/herdr_master/core/catalog.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert "pydantic" not in imported
    assert "herdr_master.core.models" not in imported


@pytest.mark.parametrize("kind", [member.value for member in CatalogHarnessKind])
def test_harness_identity_matches_enum(kind):
    spec = get_harness(kind)
    assert spec.kind == kind
    assert spec.default_model is None
    assert not hasattr(spec, "default_squad")
    assert spec.supports_herdr is True
    if kind in {"agy", "pi", "claude", "opencode"}:
        assert spec.supports_native is True
        assert spec.native is not None
        assert spec.install_targets
    else:
        assert spec.supports_native is False
        assert spec.native is None
        assert spec.install_targets == ()


def test_no_hidden_per_host_default_squad():
    document = catalog_document()
    assert document["default_mode"] == "herdr"
    assert document["native_default_squad"] is None
    for spec in document["identities"]:
        assert "default_squad" not in spec
        assert spec["default_model"] is None
    targets = [target for spec in document["identities"] for target in spec["install_targets"]]
    assert len(targets) == 5
    assert {target["harness"] for target in targets} == {"agy", "pi", "claude", "opencode"}
    for target in targets:
        template = target["path_template"]
        assert "{home}" in template and "{skill}" in template
    assert all("/home/pank" not in target["path_template"] for target in targets)


def test_product_squad_is_fresh_and_exact():
    first = default_squad_data()
    second = default_squad_data()
    assert first is not second
    assert first["worker_pool"] is not second["worker_pool"]
    first["worker_pool"].clear()
    assert len(second["worker_pool"]) == 2
    assert first["scout"]["model"] == "gemini-3.8-flash-medium"
    assert second["scout"]["effort"] == "medium"
    assert second["architect"] == {
        "kind": "pi",
        "model": "openai-codex/gpt-6.1-sol",
        "effort": "max",
        "label": "architect",
        "description": "Product Architect — native-provider Pi Codex Sol at thinking max",
        "enabled": True,
        "order": 0,
    }
    assert second["reviewer"]["kind"] == "pi"
    assert second["reviewer"]["model"] == "xai/grok-4.7"
    assert second["reviewer"]["effort"] == "high"
    assert [row["model"] for row in second["worker_pool"]] == [
        "gemini-3.8-flash",
        "xai/grok-4.7",
    ]
    assert all(row["effort"] is None for row in second["worker_pool"])
    encoded = json.dumps(second)
    assert "claude-3-opus" not in encoded
    assert "gemini-4-argon" not in encoded
    assert all(preset["default"] is False for preset in catalog_document()["compatibility_presets"])


def test_squad_defaults_do_not_share_worker_lists():
    first = Squad()
    second = Squad()
    assert first.worker_pool is not second.worker_pool
    assert first.scout is not second.scout
    first.worker_pool.pop()
    assert len(second.worker_pool) == 2


def test_product_role_argv_literals():
    scout = build_agent_args("agy", "gemini-3.8-flash-medium", "medium")
    architect = build_agent_args("pi", "openai-codex/gpt-6.1-sol", "max")
    reviewer = build_agent_args("pi", "xai/grok-4.7", "high")
    assert scout == SCOUT_ARGV
    assert architect == ARCHITECT_ARGV
    assert reviewer == REVIEWER_ARGV
    assert "litellm" not in architect
    assert "litellm" not in reviewer
    assert " ".join(scout).count("--model gemini-3.8-flash-medium --effort medium") == 1
    described = describe_agent(
        parse_binding("agy:gemini-3.8-flash-medium:medium"),
        mode="herdr",
        role="scout",
    )
    assert described["argv"] == SCOUT_ARGV
    assert described["prompt_prefix"] is None


def test_flash_family_mapping_and_conflicts():
    assert build_agent_args("agy", "gemini-3.8-flash", "low") == [
        "--model",
        "gemini-3.8-flash-low",
        "--effort",
        "low",
        "--dangerously-skip-permissions",
    ]
    assert build_agent_args("agy", "gemini-3.8-flash", "high")[1] == "gemini-3.8-flash-high"
    preserved = build_agent_args("agy", "gemini-3.8-flash-medium", None)
    assert preserved[1] == "gemini-3.8-flash-medium"
    assert "--effort" in preserved
    with pytest.raises(ValueError, match="conflicting effort"):
        build_agent_args("agy", "gemini-3.8-flash-medium", "high")
    with pytest.raises(ValueError, match="fourth variant"):
        build_agent_args("agy", "gemini-3.8-flash", "max")
    with pytest.raises(ValueError, match="--native"):
        build_agent_args("agy", "flash", "high")
    with pytest.raises(ValueError, match="--native"):
        build_agent_args("agy", "pro", None)


def test_native_agy_rejects_cli_slugs_and_keeps_phrases():
    with pytest.raises(ValueError, match="--herdr"):
        describe_native_agent("agy", "gemini-3.8-flash-medium", "medium", "scout", "agy")
    native = describe_native_agent("agy", "flash", "high", "coder", "agy")
    assert native["tool"] == "invoke_subagent"
    assert native["kwargs"]["Model"] == "flash"
    assert native["kwargs"]["TypeName"] == "ergo-worker"
    assert native["effort_prefix"] == "think harder"
    assert native["prompt_prefix"] == "think harder"
    assert "pro" != native["kwargs"]["Model"]
    lite = describe_native_agent("agy", "flash-lite", None, "watcher", "agy")
    assert lite["kwargs"]["Model"] == "flash_lite"
    assert lite["read_only"] is True


def test_pi_routes_reject_litellm_and_keep_provider():
    with pytest.raises(ValueError, match="LiteLLM"):
        build_agent_args("pi", "litellm/xai/grok-4.7", "high")
    medium = describe_agent(
        parse_binding("pi:xai/grok-4.7:medium"),
        mode="herdr",
        role="coder",
    )
    maximum = describe_agent(
        parse_binding("pi:xai/grok-4.7:max"),
        mode="herdr",
        role="coder",
    )
    assert medium["argv"] == ["--provider", "xai", "--model", "grok-4.7", "--thinking", "medium"]
    assert maximum["argv"] == ["--provider", "xai", "--model", "grok-4.7", "--thinking", "max"]
    native = describe_native_agent("pi", "xai/grok-4.7", "high", "coder", "pi")
    assert native["kwargs"]["model"] == "xai/grok-4.7:high"
    assert native["kwargs"]["agent"] == "worker"
    assert "litellm" not in json.dumps(native)
    aliased = build_agent_args("pi", "xai/grok-4.7", "xhigh")
    assert aliased[-1] == "max"


def test_prompt_effort_is_not_an_invented_flag():
    claude = describe_herdr_agent("claude", "claude-3-opus", "high", "coder")
    assert claude["argv"] == ["--model", "claude-3-opus", "--dangerously-skip-permissions"]
    assert "--effort" not in claude["argv"]
    assert "--thinking" not in claude["argv"]
    assert claude["prompt_prefix"] == "think harder"
    opencode = describe_herdr_agent("opencode", "opencode-go/deepseek-v4.1-flash", "low", "coder")
    assert opencode["argv"] == ["mini", "--model", "opencode-go/deepseek-v4.1-flash"]
    assert opencode["prompt_prefix"] == "think"
    opencode_aliased = describe_herdr_agent(
        "opencode", "openai-codex/gpt-6.1-sol", None, "architect"
    )
    assert opencode_aliased["argv"] == ["mini", "--model", "openai/gpt-6.1-sol"]
    with pytest.raises(ValueError, match="explicit --model"):
        build_agent_args("codex", "", None)
    with pytest.raises(ValueError, match="no effort flag"):
        build_agent_args("gemini", "gemini-2.5-pro", "high")
    assert build_agent_args("codex", "gpt-5.1", None) == ["--model", "gpt-5.1"]
    with pytest.raises(ValueError, match="no native adapter"):
        describe_native_agent("codex", "gpt-5.1", None, "coder", "codex")


def test_catalog_cli_show_and_agent_json():
    runner = CliRunner()
    shown = runner.invoke(catalog_app, ["show", "--json"])
    assert shown.exit_code == 0, shown.output
    payload = _json(shown)
    assert "\x1b" not in (shown.stdout or "")
    assert payload["default_squad"]["scout"]["model"] == "gemini-3.8-flash-medium"
    assert "claude-3-opus" not in json.dumps(payload["default_squad"])
    assert "gemini-4-argon" not in json.dumps(payload["default_squad"])
    agent = runner.invoke(
        catalog_app,
        [
            "agent",
            "--mode",
            "herdr",
            "--binding",
            "pi:openai-codex/gpt-6.1-sol:max",
            "--role",
            "architect",
            "--json",
        ],
    )
    assert agent.exit_code == 0, agent.output
    described = _json(agent)
    assert described["argv"] == ARCHITECT_ARGV
    missing = runner.invoke(
        catalog_app,
        ["agent", "--binding", "nope", "--role", "scout", "--json"],
    )
    assert missing.exit_code != 0
    error_payload = _json(missing)
    assert error_payload["ok"] is False
    assert error_payload["error"]
