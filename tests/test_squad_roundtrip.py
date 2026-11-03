"""Unit tests for squad configuration TOML persistence, validation, and round-tripping."""

import tempfile
from pathlib import Path

from herdr_master.core.config import (
    load_project_config,
    squad_from_project_config,
)
from herdr_master.core.models import HarnessBinding, Squad
from herdr_master.pipeline.squad import (
    format_allowed_coder_models,
    load_run_squad,
    save_run_squad,
    save_squad_as_default,
    validate_squad,
)


def test_squad_roundtrip_toml():
    """Verify Squad saves to squad.toml and reloads with exact ordered pool and bindings."""
    squad = Squad(
        scout=HarnessBinding.from_slug("agy:gemini-3.8-flash-high"),
        architect=HarnessBinding.from_slug("pi:openai-codex/gpt-6.1-sol:high"),
        reviewer=HarnessBinding.from_slug("agy:gemini-4-argon-high"),
        worker_pool=[
            HarnessBinding.from_slug("pi:xai/grok-4.7", label="general_backend", order=0),
            HarnessBinding.from_slug(
                "pi:openai-codex/gpt-6.1-terra:high", label="concurrency", order=1
            ),
            HarnessBinding.from_slug("agy:gemini-4-argon-high", label="frontend", order=2),
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        squad_path = Path(tmpdir) / "squad.toml"
        saved_path = save_run_squad(squad, squad_path)
        assert saved_path.exists()

        reloaded = load_run_squad(saved_path)
        assert reloaded.scout.to_slug() == squad.scout.to_slug()
        assert reloaded.architect.to_slug() == squad.architect.to_slug()
        assert reloaded.reviewer.to_slug() == squad.reviewer.to_slug()
        assert len(reloaded.worker_pool) == 3

        assert reloaded.worker_pool[0].to_slug() == "pi:xai/grok-4.7"
        assert reloaded.worker_pool[0].label == "general_backend"
        assert reloaded.worker_pool[1].to_slug() == "pi:openai-codex/gpt-6.1-terra:high"
        assert reloaded.worker_pool[2].to_slug() == "agy:gemini-4-argon-high"


def test_save_squad_as_default():
    """Verify copying run squad to .herdr-master.toml."""
    squad = Squad(
        scout=HarnessBinding.from_slug("agy:gemini-3.8-flash-high"),
        architect=HarnessBinding.from_slug("pi:openai-codex/gpt-6.1-sol:high"),
        reviewer=HarnessBinding.from_slug("agy:gemini-4-argon-high"),
        worker_pool=[
            HarnessBinding.from_slug("pi:xai/grok-4.7", label="backend", order=0),
            HarnessBinding.from_slug("agy:gemini-4-argon-high", label="ui", order=1),
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        proj_config_path = Path(tmpdir) / ".herdr-master.toml"
        save_squad_as_default(squad, proj_config_path)
        assert proj_config_path.exists()

        loaded_cfg = load_project_config(proj_config_path)
        reloaded_squad = squad_from_project_config(loaded_cfg)

        assert reloaded_squad.scout.to_slug() == squad.scout.to_slug()
        assert len(reloaded_squad.worker_pool) == 2
        assert reloaded_squad.worker_pool[0].label == "backend"
        assert reloaded_squad.worker_pool[1].label == "ui"


def test_squad_validation():
    """Verify validation detects empty pool and duplicates."""
    # Empty pool
    empty_squad = Squad(worker_pool=[])
    errors = validate_squad(empty_squad)
    assert any("cannot be empty" in e for e in errors)

    # Duplicate binding
    duplicate_squad = Squad(
        worker_pool=[
            HarnessBinding.from_slug("pi:xai/grok-4.7", label="worker1"),
            HarnessBinding.from_slug("pi:xai/grok-4.7", label="worker2"),
        ]
    )
    dup_errors = validate_squad(duplicate_squad)
    assert any("Duplicate worker model binding" in e for e in dup_errors)


def test_format_allowed_coder_models():
    """Verify allowed coder models block formatting for the Architect prompt."""
    squad = Squad(
        worker_pool=[
            HarnessBinding.from_slug("pi:xai/grok-4.7", label="backend", order=0),
            HarnessBinding.from_slug("agy:gemini-4-argon-high", label="frontend", order=1),
        ]
    )
    block = format_allowed_coder_models(squad)
    assert "ALLOWED CODER MODELS:" in block
    assert "- pi:xai/grok-4.7 (backend)" in block
    assert "- agy:gemini-4-argon-high (frontend)" in block
