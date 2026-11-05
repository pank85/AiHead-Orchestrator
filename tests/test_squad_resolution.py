"""Squad precedence, native compatibility, formatter, and catalog CLI resolution tests."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from herdr_master.cli.catalog import catalog_app
from herdr_master.core.catalog import CatalogError
from herdr_master.core.models import HarnessBinding, Squad
from herdr_master.pipeline.squad import (
    SquadValidationError,
    format_allowed_coder_models,
    load_run_squad,
    parse_squad_overrides,
    resolve_frozen_squad,
    resolve_squad,
    save_run_squad,
)

REPO = Path(__file__).resolve().parents[1]


def _json(result):
    stdout = result.stdout if result.stdout else result.output
    return json.loads(stdout)


def test_repo_toml_does_not_override_product_squad():
    text = (REPO / ".herdr-master.toml").read_text(encoding="utf-8")
    assert "claude-3-opus" not in text
    assert "gemini-4-argon" not in text
    assert "gemini-3.8-flash-high" not in text
    assert 'target_integration_branch = "develop"' in text
    assert "disallow_hard_reset = true" in text
    resolved = resolve_squad(REPO / ".herdr-master.toml")
    assert resolved.sources == {
        "scout": "product_default",
        "architect": "product_default",
        "reviewer": "product_default",
        "workers": "product_default",
    }
    assert resolved.squad.scout.model == "gemini-3.8-flash-medium"
    assert resolved.squad.scout.effort.value == "medium"
    assert resolved.mode == "herdr"
    assert resolved.native_harness is None
    payload = resolved.to_dict()
    assert "claude-3-opus" not in payload["allowed_coder_models"]
    assert "argon" not in payload["allowed_coder_models"]
    assert payload["roles"]["architect"]["argv"] == [
        "--provider",
        "openai-codex",
        "--model",
        "gpt-6.1-sol",
        "--thinking",
        "max",
    ]
    assert payload["roles"]["reviewer"]["argv"] == [
        "--provider",
        "xai",
        "--model",
        "grok-4.7",
        "--thinking",
        "high",
    ]
    assert " ".join(payload["roles"]["scout"]["argv"]) == (
        "--model gemini-3.8-flash-medium --effort medium --dangerously-skip-permissions"
    )


def test_formatter_keeps_bullet_shape_and_adds_route_constraints():
    squad = Squad()
    block = format_allowed_coder_models(squad)
    assert "ALLOWED CODER MODELS:" in block
    assert "reasoning effort" in block
    assert "- agy:gemini-3.8-flash (worker-1) — Simple, mechanical, well-scoped jobs" in block
    assert "- pi:xai/grok-4.7 (worker-2) — Complex, architectural, or subtle jobs" in block
    assert "gemini-3.8-flash-low" in block
    assert "gemini-3.8-flash-medium" in block
    assert "gemini-3.8-flash-high" in block
    assert "--provider xai --model grok-4.7" in block
    assert "claude-3-opus" not in block
    assert "argon" not in block
    disabled = Squad(
        worker_pool=[
            HarnessBinding.from_slug(
                "agy:gemini-3.8-flash",
                label="worker-1",
                enabled=True,
                order=0,
            ),
            HarnessBinding.from_slug(
                "pi:xai/grok-4.7",
                label="worker-2",
                description="hidden",
                enabled=False,
                order=1,
            ),
        ]
    )
    hidden = format_allowed_coder_models(disabled)
    assert "worker-2" not in hidden
    assert "hidden" not in hidden
    assert "worker-1" in hidden


def test_parse_overrides_flags_beat_compact_and_reject_malformed():
    parsed = parse_squad_overrides(
        models="scout:agy:gemini-3.8-flash:low workers:agy:gemini-3.8-flash,pi:xai/grok-4.7",
        scout="agy:gemini-3.8-flash-medium:medium",
    )
    assert parsed["scout"] == "agy:gemini-3.8-flash-medium:medium"
    assert parsed["workers"] == "agy:gemini-3.8-flash,pi:xai/grok-4.7"
    aliased = parse_squad_overrides(models="coder:agy:gemini-3.8-flash")
    assert aliased["workers"] == "agy:gemini-3.8-flash"
    with pytest.raises(CatalogError, match="Malformed"):
        parse_squad_overrides(models="nope")
    with pytest.raises(CatalogError, match="Duplicate"):
        parse_squad_overrides(
            models="scout:agy:gemini-3.8-flash:low scout:agy:gemini-3.8-flash:high"
        )
    with pytest.raises(CatalogError, match="Unknown role"):
        parse_squad_overrides(models="watcher:agy:flash")
    with pytest.raises(CatalogError, match="empty"):
        parse_squad_overrides(workers="")


def test_precedence_layers(tmp_path):
    project = tmp_path / ".herdr-master.toml"
    project.write_text(
        "\n".join(
            [
                "[models.default_squad]",
                'scout = "agy:gemini-3.8-flash:low"',
                'architect = "pi:openai-codex/gpt-6.1-sol:low"',
                "",
                "[models.worker_pool.worker-1]",
                'binding = "pi:xai/grok-4.7"',
                'description = "project worker"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    snapshot = tmp_path / "squad.toml"
    snapshot.write_text(
        "\n".join(
            [
                "[roles]",
                'architect = "pi:openai-codex/gpt-6.1-sol:medium"',
                'reviewer = "pi:xai/grok-4.7:low"',
                "",
                "[[worker_pool]]",
                'binding = "agy:gemini-3.8-flash"',
                'label = "snap-worker"',
                "enabled = true",
                "order = 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    resolved = resolve_squad(
        project,
        mode="herdr",
        run_squad_path=snapshot,
        overrides=parse_squad_overrides(scout="agy:gemini-3.8-flash-high"),
    )
    assert resolved.sources["scout"] == "override"
    assert resolved.sources["architect"] == "run_snapshot"
    assert resolved.sources["reviewer"] == "run_snapshot"
    assert resolved.sources["workers"] == "run_snapshot"
    assert resolved.squad.scout.model == "gemini-3.8-flash-high"
    assert resolved.squad.architect.effort.value == "medium"
    assert resolved.squad.reviewer.model == "xai/grok-4.7"
    assert resolved.squad.worker_pool[0].label == "snap-worker"
    project_only = resolve_squad(project)
    assert project_only.sources["scout"] == "project"
    assert project_only.sources["reviewer"] == "product_default"
    assert project_only.squad.reviewer.to_slug() == "pi:xai/grok-4.7:high"
    assert project_only.sources["workers"] == "project"
    assert project_only.squad.worker_pool[0].description == "project worker"


def test_explicit_empty_and_disabled_pools_error(tmp_path):
    empty = tmp_path / "empty.toml"
    empty.write_text("[models.worker_pool]\n", encoding="utf-8")
    with pytest.raises(SquadValidationError, match="cannot be empty"):
        resolve_squad(empty)
    disabled = tmp_path / "disabled.toml"
    disabled.write_text(
        "\n".join(
            [
                "[models.worker_pool.worker-1]",
                'binding = "pi:xai/grok-4.7"',
                "enabled = false",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(SquadValidationError, match="all disabled"):
        resolve_squad(disabled)


def test_native_mixed_default_fails_until_incompatible_entries_are_overridden(tmp_path):
    with pytest.raises(CatalogError, match="--herdr") as mixed:
        resolve_squad(tmp_path, mode="native", native_harness="pi")
    assert "not rewritten" in str(mixed.value)
    assert "scout" in str(mixed.value)
    with pytest.raises(CatalogError, match="no native adapter"):
        resolve_squad(tmp_path, mode="native", native_harness="codex")
    with pytest.raises(CatalogError, match="no native default"):
        resolve_squad(tmp_path, mode="native")
    resolved = resolve_squad(
        tmp_path,
        mode="native",
        native_harness="pi",
        overrides={
            "scout": "openai-codex/gpt-6.1-sol:low",
            "workers": "xai/grok-4.7",
        },
    )
    assert resolved.squad.scout.kind.value == "pi"
    assert resolved.squad.scout.model == "openai-codex/gpt-6.1-sol"
    assert resolved.squad.worker_pool[0].to_slug() == "pi:xai/grok-4.7"
    assert resolved.native_harness == "pi"
    with pytest.raises(CatalogError, match="harness kind"):
        resolve_squad(tmp_path, overrides={"scout": "gemini-3.8-flash-medium:medium"})


def test_same_family_native_squad_is_advisory_not_error(tmp_path):
    resolved = resolve_squad(
        tmp_path,
        mode="native",
        native_harness="pi",
        overrides={
            "scout": "pi:xai/grok-4.7:low",
            "architect": "pi:xai/grok-4.7:max",
            "reviewer": "pi:xai/grok-4.7:high",
            "workers": "pi:openai-codex/gpt-6.1-sol",
        },
    )
    assert resolved.advisories
    assert "Advisory" in resolved.advisories[0]
    assert resolved.squad.has_reviewer_model_family_conflict()


def test_snapshot_resolution_metadata_and_old_file_defaults_to_herdr(tmp_path):
    squad = Squad()
    path = tmp_path / "squad.toml"
    save_run_squad(squad, path, mode="herdr")
    text = path.read_text(encoding="utf-8")
    assert 'mode = "herdr"' in text
    loaded = load_run_squad(path)
    assert loaded.scout.to_slug() == "agy:gemini-3.8-flash-medium:medium"
    assert loaded.worker_pool[1].effort is None
    old = tmp_path / "old.toml"
    old.write_text(
        "\n".join(
            [
                "[roles]",
                'scout = "agy:gemini-3.8-flash-high"',
                'architect = "pi:openai-codex/gpt-6.1-sol:high"',
                'reviewer = "agy:gemini-4-argon-high"',
                "",
                "[[worker_pool]]",
                'binding = "claude:claude-3-opus"',
                'label = "worker-1"',
                'description = "Senior dev - Very capable"',
                "enabled = true",
                "order = 0",
                "",
            ]
        ),
        encoding="utf-8",
    )
    legacy = resolve_squad(tmp_path, run_squad_path=old)
    assert legacy.mode == "herdr"
    assert legacy.sources["scout"] == "run_snapshot"
    assert legacy.sources["workers"] == "run_snapshot"
    assert legacy.squad.scout.model == "gemini-3.8-flash-high"
    assert legacy.squad.worker_pool[0].to_slug() == "claude:claude-3-opus"
    omitted = tmp_path / "partial.toml"
    omitted.write_text(
        '[roles]\narchitect = "pi:openai-codex/gpt-6.1-sol:low"\n',
        encoding="utf-8",
    )
    partial = load_run_squad(omitted)
    assert partial.scout.model == "gemini-3.8-flash-medium"
    assert partial.architect.effort.value == "low"
    assert len(partial.worker_pool) == 2


def test_frozen_stdin_does_not_merge_defaults(tmp_path):
    document = {
        "schema_version": 1,
        "mode": "herdr",
        "native_harness": None,
        "squad": {
            "scout": {"kind": "agy", "model": "gemini-3.8-flash-low", "effort": "low"},
            "architect": {"kind": "pi", "model": "openai-codex/gpt-6.1-sol", "effort": "max"},
            "reviewer": {"kind": "pi", "model": "xai/grok-4.7", "effort": "high"},
            "worker_pool": [
                {"kind": "pi", "model": "xai/grok-4.7", "label": "only-worker", "effort": None}
            ],
        },
    }
    resolved = resolve_frozen_squad(document)
    assert resolved.sources["scout"] == "frozen_input"
    assert resolved.squad.scout.model == "gemini-3.8-flash-low"
    assert len(resolved.squad.worker_pool) == 1
    assert "allowed_coder_models" in resolved.to_dict()
    incomplete = dict(document)
    incomplete["squad"] = dict(document["squad"])
    del incomplete["squad"]["reviewer"]
    with pytest.raises(CatalogError, match="missing 'reviewer'"):
        resolve_frozen_squad(incomplete)
    runner = CliRunner()
    destination = tmp_path / "frozen.toml"
    result = runner.invoke(
        catalog_app,
        ["squad", "--from-json", "-", "--write-snapshot", str(destination), "--json"],
        input=json.dumps(document),
    )
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["snapshot_path"] == str(destination)
    assert payload["squad"]["scout"]["model"] == "gemini-3.8-flash-low"
    assert "claude-3-opus" not in payload["allowed_coder_models"]
    assert destination.is_file()
    relative = runner.invoke(
        catalog_app,
        ["squad", "--from-json", "-", "--write-snapshot", "relative.toml", "--json"],
        input=json.dumps(document),
    )
    assert relative.exit_code != 0
    assert not (Path.cwd() / "relative.toml").exists()


def test_catalog_squad_cli_and_show_do_not_write(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("display must not write a snapshot")

    monkeypatch.setattr("herdr_master.cli.catalog.save_run_squad", boom)
    runner = CliRunner()
    shown = runner.invoke(catalog_app, ["show", "--json"])
    assert shown.exit_code == 0, shown.output
    resolved = runner.invoke(
        catalog_app,
        ["squad", "--project", str(tmp_path), "--json"],
    )
    assert resolved.exit_code == 0, resolved.output
    payload = _json(resolved)
    assert "\x1b" not in (resolved.stdout or "")
    assert payload["squad"]["scout"]["model"] == "gemini-3.8-flash-medium"
    assert payload["squad"]["architect"]["effort"] == "max"
    assert payload["squad"]["reviewer"]["model"] == "xai/grok-4.7"
    assert [row["model"] for row in payload["squad"]["worker_pool"]] == [
        "gemini-3.8-flash",
        "xai/grok-4.7",
    ]
    assert "claude-3-opus" not in payload["allowed_coder_models"]
    assert "argon" not in payload["allowed_coder_models"]
    bad = runner.invoke(catalog_app, ["squad", "--mode", "sideways", "--json"])
    assert bad.exit_code != 0
    assert _json(bad)["ok"] is False
