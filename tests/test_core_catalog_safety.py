"""Core catalog resolution must not advertise unsupported routes."""

import pytest
from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.core.catalog import CatalogError, supported_harnesses
from herdr_master.core.models import HarnessKind
from herdr_master.core.slug import parse_binding
from herdr_master.pipeline.squad import describe_agent, resolve_frozen_squad, resolve_squad


def test_core_cli_rejects_unsupported_worker(tmp_path):
    result = CliRunner().invoke(
        catalog_app,
        ["squad", "--mode", "core", "--project", str(tmp_path), "--workers", "kilo:example/model"],
    )
    assert result.exit_code != 0
    assert "not supported in Core mode" in result.output


@pytest.mark.parametrize("kind", [kind.value for kind in HarnessKind])
def test_core_worker_kinds_follow_live_catalog(kind, tmp_path):
    binding = parse_binding(f"{kind}:example/model")
    if kind not in supported_harnesses(mode="core"):
        with pytest.raises(CatalogError, match="not supported in Core mode"):
            describe_agent(binding, mode="core", role="coder")
        with pytest.raises(CatalogError, match="not supported in Core mode"):
            resolve_squad(tmp_path, mode="core", overrides={"workers": binding.to_slug()})
    else:
        describe_agent(binding, mode="core", role="coder")
        resolved = resolve_squad(tmp_path, mode="core", overrides={"workers": binding.to_slug()})
        assert resolved.workers[0]["binding"]["kind"] == kind


def test_frozen_core_rejects_disabled_unsupported_worker(tmp_path):
    document = resolve_squad(tmp_path, mode="core").to_dict()
    document["squad"]["worker_pool"].append(
        {"kind": "kilo", "model": "example/model", "enabled": False}
    )
    with pytest.raises(CatalogError, match="not supported in Core mode"):
        resolve_frozen_squad(document)


def test_core_watcher_fails_closed_without_changing_legacy():
    binding = parse_binding("pi:example/model")
    with pytest.raises(CatalogError, match="hc wait"):
        describe_agent(binding, mode="core", role="watcher")
    assert describe_agent(binding, mode="herdr", role="watcher")["tool"] == "herdr agent wait"
    native = describe_agent(binding, mode="native", role="watcher", native_harness="pi")
    assert native["mode"] == "native"
