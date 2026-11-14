"""Allowlist persistence: missing config inherits, empty collections are explicit."""

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pytest
from pydantic import ValidationError

from herdr_master.core.available_models import (
    AvailableModels as HelperAvailableModels,
)
from herdr_master.core.available_models import (
    enabled_model_ids,
    filter_model_ids,
    format_available_models_block,
    is_all_enabled,
    materialize_toggle,
    materialize_toggle_all,
)
from herdr_master.core.catalog import HarnessKind
from herdr_master.core.config import (
    AiHeadConfig,
    AvailableModels,
    HerdrMasterConfig,
    load_project_config,
    save_project_config,
    squad_to_project_config,
)
from herdr_master.core.models import Squad


def test_available_models_reexported_from_config():
    assert AvailableModels is HelperAvailableModels


def test_legacy_toml_without_allowlist_or_automode(tmp_path):
    """Absent keys inherit: no allowlist, automode stays off."""
    path = tmp_path / ".herdr-master.toml"
    path.write_text(
        '[general]\ndefault_session = "legacy"\n\n[ai_head]\nenabled = false\n',
        encoding="utf-8",
    )
    cfg = load_project_config(path)
    assert cfg.models.available is None
    assert cfg.ai_head.automode is False
    assert enabled_model_ids(cfg.models.available, "herdr", "pi") is None
    assert HerdrMasterConfig().models.available is None
    assert AiHeadConfig().automode is False


def test_explicit_allowlists_round_trip_including_empty_and_native_tiers(tmp_path):
    cfg = HerdrMasterConfig()
    cfg.models.available = AvailableModels(
        herdr={"pi": ["  openai/gpt-6.1-sol  "], "claude": []},
        native={"agy": ["flash_lite", "flash", "pro"]},
    )
    cfg.ai_head.automode = True
    path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, path)
    text = path.read_text(encoding="utf-8")
    assert "[models.available" in text or "available" in text
    assert "flash_lite" in text
    assert "openai/gpt-6.1-sol" in text

    reloaded = load_project_config(path)
    assert reloaded.models.available is not None
    assert reloaded.models.available.herdr == {
        "pi": ["openai/gpt-6.1-sol"],
        "claude": [],
    }
    assert reloaded.models.available.native == {
        "agy": ["flash_lite", "flash", "pro"],
    }
    assert reloaded.ai_head.automode is True
    parsed = tomllib.loads(text)
    assert parsed["models"]["available"]["herdr"]["claude"] == []
    assert parsed["ai_head"]["automode"] is True


def test_whitespace_model_ids_are_stripped_on_load(tmp_path):
    path = tmp_path / ".herdr-master.toml"
    path.write_text(
        '[models.available.native]\nagy = ["  flash  ", "pro"]\n',
        encoding="utf-8",
    )
    cfg = load_project_config(path)
    assert cfg.models.available is not None
    assert cfg.models.available.native == {"agy": ["flash", "pro"]}
    assert cfg.models.available.herdr is None


def test_unknown_harness_key_and_blank_model_id_rejected():
    with pytest.raises(ValidationError, match="Unknown harness key"):
        AvailableModels(herdr={"not-a-harness": ["x"]})
    with pytest.raises(ValidationError, match="Unknown harness key"):
        HerdrMasterConfig.model_validate(
            {"models": {"available": {"native": {"PI": ["flash"]}}}}
        )
    with pytest.raises(ValidationError, match="blank"):
        AvailableModels(native={"agy": ["flash", "  "]})
    with pytest.raises(ValidationError, match="blank"):
        AvailableModels(herdr={"pi": [""]})
    with pytest.raises(ValidationError, match="non-empty string"):
        AvailableModels(native={"agy": ["flash", 1]})
    with pytest.raises(ValidationError, match="must be a list"):
        AvailableModels(herdr={"pi": "openai/gpt-6.1-sol"})


def test_absent_harness_stays_unrestricted_beside_explicit_empty():
    available = AvailableModels(herdr={"pi": []})
    assert enabled_model_ids(available, "herdr", HarnessKind.PI) == []
    assert enabled_model_ids(available, "herdr", "claude") is None
    assert enabled_model_ids(available, "native", "pi") is None
    assert enabled_model_ids(available, "nope", "pi") is None
    discovered = ["a", "b"]
    assert filter_model_ids(available, "herdr", "claude", discovered) is discovered
    assert filter_model_ids(available, "herdr", "pi", discovered) == []
    assert (
        filter_model_ids(available, "herdr", "pi", discovered, current="saved")
        == ["saved"]
    )


def test_automode_coercion_matches_split_tokens(tmp_path):
    assert AiHeadConfig.model_validate({"automode": "yes"}).automode is True
    assert AiHeadConfig.model_validate({"automode": "off"}).automode is False
    assert AiHeadConfig.model_validate({"automode": "1"}).automode is True
    assert AiHeadConfig.model_validate({"automode": 0}).automode is False
    assert AiHeadConfig.model_validate({"automode": 1}).automode is True
    assert AiHeadConfig.model_validate({"automode": "  NO  "}).automode is False
    with pytest.raises(ValidationError, match="Invalid boolean value for automode"):
        AiHeadConfig.model_validate({"automode": "maybe"})

    path = tmp_path / ".herdr-master.toml"
    path.write_text('[ai_head]\nautomode = "on"\n', encoding="utf-8")
    assert load_project_config(path).ai_head.automode is True
    path.write_text("[ai_head]\nautomode = false\n", encoding="utf-8")
    assert load_project_config(path).ai_head.automode is False
    dumped = HerdrMasterConfig().model_dump(exclude_none=True)
    assert "available" not in dumped["models"]
    assert dumped["ai_head"]["automode"] is False


def test_filter_model_ids_unrestricted_is_noop_and_restricted_keeps_current():
    discovered = ["a", "b", "c"]
    assert filter_model_ids(None, "herdr", "pi", discovered, current="missing") is discovered
    assert (
        filter_model_ids(AvailableModels(), "native", "agy", discovered) is discovered
    )
    other_mode = AvailableModels(native={"pi": ["a"]})
    assert filter_model_ids(other_mode, "herdr", "pi", discovered) is discovered
    sibling = AvailableModels(herdr={"claude": ["a"]})
    assert filter_model_ids(sibling, "herdr", "pi", discovered) is discovered

    restricted = AvailableModels(herdr={"pi": ["c", "a"]})
    assert filter_model_ids(restricted, "herdr", "pi", discovered) == ["a", "c"]
    assert filter_model_ids(restricted, "herdr", "pi", discovered) is not discovered
    assert discovered == ["a", "b", "c"]
    assert (
        filter_model_ids(restricted, "herdr", "pi", discovered, current="b")
        == ["a", "c", "b"]
    )
    assert (
        filter_model_ids(restricted, "herdr", "pi", discovered, current="a")
        == ["a", "c"]
    )
    assert (
        filter_model_ids(restricted, "herdr", "pi", discovered, current="")
        == ["a", "c"]
    )
    assert (
        filter_model_ids(restricted, "herdr", "pi", discovered, current="   ")
        == ["a", "c"]
    )
    full = AvailableModels(herdr={"pi": ["a", "b", "c"]})
    same_members = filter_model_ids(full, "herdr", "pi", discovered)
    assert same_members == discovered
    assert same_members is not discovered


def test_materialize_toggle_copies_only_touched_harness_and_mode():
    existing = AvailableModels.model_validate(
        {"native": {"pi": ["keep-me"]}, "future_flag": 1}
    )
    native_ids = existing.native["pi"]
    result = materialize_toggle(
        existing,
        "herdr",
        HarnessKind.CLAUDE,
        "claude-sonnet",
        False,
        ["claude-sonnet", "claude-haiku", "claude-opus"],
    )
    assert existing.herdr is None
    assert existing.native["pi"] == ["keep-me"]
    assert existing.native["pi"] is native_ids
    assert existing.model_extra["future_flag"] == 1
    assert result.native == {"pi": ["keep-me"]}
    assert result.native is not existing.native
    assert result.herdr == {"claude": ["claude-haiku", "claude-opus"]}
    assert "pi" not in result.herdr
    assert result.model_extra["future_flag"] == 1

    seeded = AvailableModels(herdr={"pi": ["only-saved"]})
    toggled = materialize_toggle(
        seeded,
        "herdr",
        "pi",
        "new-id",
        True,
        ["discovered-a", "discovered-b"],
    )
    assert seeded.herdr == {"pi": ["only-saved"]}
    assert toggled.herdr == {"pi": ["only-saved", "new-id"]}
    assert toggled.native is None

    first = materialize_toggle(
        None, "native", "agy", "flash", False, ["flash", "pro"]
    )
    assert first.herdr is None
    assert first.native == {"agy": ["pro"]}

    added = materialize_toggle(None, "herdr", "pi", " extra ", True, ["a"])
    assert added.herdr == {"pi": ["a", "extra"]}

    with pytest.raises(ValueError, match="Unknown harness key"):
        materialize_toggle(None, "herdr", "nope", "x", True, ["x"])
    with pytest.raises(ValueError, match="Unknown mode"):
        materialize_toggle(None, "cli", "pi", "x", True, ["x"])
    with pytest.raises(ValueError, match="blank"):
        materialize_toggle(None, "herdr", "pi", "  ", True, ["x"])
    with pytest.raises(TypeError):
        materialize_toggle(None, "herdr", "pi", "x", "yes", ["x"])


def test_format_lists_only_saved_keys():
    assert (
        format_available_models_block(None) == "unrestricted (no allowlist saved)"
    )
    assert format_available_models_block(AvailableModels()) == ""
    text = format_available_models_block(
        AvailableModels(
            herdr={"hermes": ["h"], "agy": ["a"], "pi": []},
            native={"agy": ["flash_lite", "pro"]},
        )
    )
    assert text == (
        "herdr.agy: a\n"
        "herdr.pi: (empty)\n"
        "herdr.hermes: h\n"
        "native.agy: flash_lite, pro"
    )
    assert "unrestricted" not in text
    assert "claude" not in text


def test_squad_to_project_config_mutates_models_and_keeps_available():
    """A future edit that replaces cfg.models must fail this identity check."""
    cfg = HerdrMasterConfig()
    allow = AvailableModels(herdr={"agy": []}, native={"agy": ["flash", "pro"]})
    cfg.models.available = allow
    models = cfg.models
    updated = squad_to_project_config(Squad(), existing_config=cfg)
    assert updated is cfg
    assert updated.models is models
    assert updated.models.available is allow
    assert updated.models.available.herdr == {"agy": []}
    assert updated.models.available.native["agy"] == ["flash", "pro"]
    assert updated.models.worker_pool is not None
    assert updated.models.default_squad is not None
    assert "scout" in updated.models.default_squad


def test_save_project_config_writes_available_and_reload_matches(tmp_path):
    cfg = HerdrMasterConfig()
    cfg.ai_head.automode = False
    cfg.models.available = AvailableModels(
        herdr={"pi": ["openai/gpt-6.1-sol"], "agy": []},
        native={"agy": ["flash", "pro"]},
    )
    updated = squad_to_project_config(Squad(), existing_config=cfg)
    path = tmp_path / ".herdr-master.toml"
    save_project_config(updated, path)
    text = path.read_text(encoding="utf-8")
    assert "openai/gpt-6.1-sol" in text
    assert "automode = false" in text
    reloaded = load_project_config(path)
    assert reloaded.models.available == updated.models.available
    assert reloaded.ai_head.automode is False
    assert reloaded.models.available.herdr["agy"] == []
    assert reloaded.models.available.native["agy"] == ["flash", "pro"]


def test_is_all_enabled_unrestricted_and_empty_allowlist():
    # Unrestricted scope: available=None
    assert is_all_enabled(None, "herdr", "pi", ["a", "b"]) is True
    assert is_all_enabled(None, "herdr", "pi", []) is True

    # Unrestricted scope: AvailableModels with None mode maps
    assert is_all_enabled(AvailableModels(), "herdr", "pi", ["a", "b"]) is True

    # Unrestricted scope: harness not present in mode map
    available = AvailableModels(herdr={"claude": ["c1"]})
    assert is_all_enabled(available, "herdr", "pi", ["a", "b"]) is True
    assert is_all_enabled(available, "native", "claude", ["c1"]) is True

    # Explicit empty list [] returns False
    empty_pi = AvailableModels(herdr={"pi": []})
    assert is_all_enabled(empty_pi, "herdr", "pi", ["a", "b"]) is False
    assert is_all_enabled(empty_pi, "herdr", "pi", []) is False
    assert is_all_enabled(empty_pi, "herdr", HarnessKind.PI, ["a"]) is False

    # Empty discovered list with unrestricted returns True, with non-empty allowlist returns True
    populated = AvailableModels(herdr={"pi": ["a"]})
    assert is_all_enabled(populated, "herdr", "pi", []) is True
    assert is_all_enabled(None, "herdr", "pi", []) is True

    # Unknown/invalid harness returns True (inherits unrestricted without error)
    assert is_all_enabled(populated, "herdr", "nonexistent-harness", ["x"]) is True
    assert is_all_enabled(None, "herdr", "unknown", ["x"]) is True
    assert is_all_enabled(populated, "unknown-mode", "pi", ["x"]) is True


def test_is_all_enabled_matching_superset_and_missing():
    allow = AvailableModels(herdr={"pi": ["a", "b"]})

    # Allowlist matching discovered returns True
    assert is_all_enabled(allow, "herdr", "pi", ["a", "b"]) is True
    assert is_all_enabled(allow, "herdr", "pi", ["b", "a"]) is True
    assert is_all_enabled(allow, "herdr", HarnessKind.PI, ["a", "b"]) is True

    # Allowlist superset of discovered returns True
    assert is_all_enabled(allow, "herdr", "pi", ["a"]) is True
    assert is_all_enabled(allow, "herdr", "pi", ["b"]) is True

    # Allowlist missing one or more discovered models returns False
    assert is_all_enabled(allow, "herdr", "pi", ["a", "b", "c"]) is False
    assert is_all_enabled(allow, "herdr", "pi", ["c"]) is False
    assert is_all_enabled(allow, "herdr", "pi", ["a", "missing"]) is False


def test_materialize_toggle_all_disables_all_copy_on_touch():
    # From None: copies only touched harness and mode, other mode stays None
    result = materialize_toggle_all(None, "herdr", "pi", False, ["a", "b"])
    assert result.herdr == {"pi": []}
    assert result.native is None

    # Starting from existing with other mode and other harness:
    existing = AvailableModels.model_validate(
        {"herdr": {"claude": ["c1"]}, "native": {"agy": ["flash"]}, "future_flag": 42}
    )
    result = materialize_toggle_all(existing, "herdr", HarnessKind.PI, False, ["p1", "p2"])
    assert result.herdr == {"claude": ["c1"], "pi": []}
    assert result.native == {"agy": ["flash"]}
    assert result.native is not existing.native
    assert result.model_extra["future_flag"] == 42
    # Ensure original existing was not mutated
    assert existing.herdr == {"claude": ["c1"]}
    assert "pi" not in existing.herdr


def test_materialize_toggle_all_enables_all_order_and_deduplication():
    discovered = ["  model-b  ", "model-a", "model-b", "model-c", "model-a"]
    result = materialize_toggle_all(None, "native", "agy", True, discovered)
    assert result.native == {"agy": ["model-b", "model-a", "model-c"]}
    assert result.herdr is None

    # Order preserved as encountered
    result2 = materialize_toggle_all(None, "herdr", HarnessKind.HERMES, True, ["z", "y", "x"])
    assert result2.herdr == {"hermes": ["z", "y", "x"]}


def test_materialize_toggle_all_replaces_existing_allowlist():
    existing = AvailableModels(herdr={"pi": ["old-model"]})
    result = materialize_toggle_all(existing, "herdr", "pi", True, ["new-1", "new-2"])
    assert result.herdr == {"pi": ["new-1", "new-2"]}

    # Replace with False -> []
    disabled = materialize_toggle_all(result, "herdr", "pi", False, ["new-1", "new-2"])
    assert disabled.herdr == {"pi": []}

    # Replace [] with True -> new models
    re_enabled = materialize_toggle_all(disabled, "herdr", "pi", True, ["m1", "m2", "m1"])
    assert re_enabled.herdr == {"pi": ["m1", "m2"]}


def test_materialize_toggle_all_empty_discovered():
    # Empty discovered list with enabled=False results in []
    res_false = materialize_toggle_all(None, "herdr", "pi", False, [])
    assert res_false.herdr == {"pi": []}

    # Empty discovered list with enabled=True results in []
    res_true = materialize_toggle_all(None, "herdr", "pi", True, [])
    assert res_true.herdr == {"pi": []}

    # With pre-existing allowlist, empty discovered replaces with []
    existing = AvailableModels(herdr={"pi": ["a", "b"]})
    assert materialize_toggle_all(existing, "herdr", "pi", True, []).herdr == {"pi": []}
    assert materialize_toggle_all(existing, "herdr", "pi", False, []).herdr == {"pi": []}


def test_materialize_toggle_all_validations():
    # TypeError for non-bool enabled
    with pytest.raises(TypeError, match="enabled must be a bool"):
        materialize_toggle_all(None, "herdr", "pi", "true", ["a"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="enabled must be a bool"):
        materialize_toggle_all(None, "herdr", "pi", 1, ["a"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="enabled must be a bool"):
        materialize_toggle_all(None, "herdr", "pi", 0, ["a"])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="enabled must be a bool"):
        materialize_toggle_all(None, "herdr", "pi", None, ["a"])  # type: ignore[arg-type]

    # ValueError for unknown mode
    with pytest.raises(ValueError, match="Unknown mode"):
        materialize_toggle_all(None, "invalid_mode", "pi", True, ["a"])
    with pytest.raises(ValueError, match="Unknown mode"):
        materialize_toggle_all(None, 123, "pi", True, ["a"])  # type: ignore[arg-type]

    # ValueError for unknown harness
    with pytest.raises(ValueError, match="Unknown harness key"):
        materialize_toggle_all(None, "herdr", "invalid_harness", True, ["a"])
    with pytest.raises(ValueError, match="Unknown harness key"):
        materialize_toggle_all(None, "herdr", 123, True, ["a"])  # type: ignore[arg-type]

    # ValueError for blank string in discovered
    with pytest.raises(ValueError, match="Model id must not be blank"):
        materialize_toggle_all(None, "herdr", "pi", True, ["a", "   ", "b"])
    with pytest.raises(ValueError, match="Model id must not be blank"):
        materialize_toggle_all(None, "herdr", "pi", True, [""])

    # ValueError for non-string type in discovered
    with pytest.raises(ValueError, match="Model id must be a non-empty string"):
        materialize_toggle_all(None, "herdr", "pi", True, ["a", None])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="Model id must be a non-empty string"):
        materialize_toggle_all(None, "herdr", "pi", True, [123])  # type: ignore[list-item]


def test_materialize_toggle_all_round_trip_persistence(tmp_path):
    cfg = HerdrMasterConfig()
    cfg.models.available = materialize_toggle_all(
        cfg.models.available, "herdr", "pi", False, ["model-1", "model-2"]
    )
    cfg.models.available = materialize_toggle_all(
        cfg.models.available, "native", "agy", True, ["  flash  ", "pro", "flash"]
    )
    path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, path)

    text = path.read_text(encoding="utf-8")
    parsed = tomllib.loads(text)
    assert parsed["models"]["available"]["herdr"]["pi"] == []
    assert parsed["models"]["available"]["native"]["agy"] == ["flash", "pro"]

    reloaded = load_project_config(path)
    assert reloaded.models.available is not None
    assert reloaded.models.available.herdr == {"pi": []}
    assert reloaded.models.available.native == {"agy": ["flash", "pro"]}

    # Toggle pi to all enabled and re-save
    reloaded.models.available = materialize_toggle_all(
        reloaded.models.available, "herdr", "pi", True, ["model-1", "model-2"]
    )
    save_project_config(reloaded, path)
    reloaded2 = load_project_config(path)
    assert reloaded2.models.available is not None
    assert reloaded2.models.available.herdr == {"pi": ["model-1", "model-2"]}
    assert reloaded2.models.available.native == {"agy": ["flash", "pro"]}


def test_available_models_all_exports():
    import herdr_master.core.available_models as mod

    assert hasattr(mod, "__all__")
    assert "is_all_enabled" in mod.__all__
    assert "materialize_toggle_all" in mod.__all__
    assert "materialize_toggle" in mod.__all__
    assert "enabled_model_ids" in mod.__all__
    assert "filter_model_ids" in mod.__all__
    assert "format_available_models_block" in mod.__all__
    assert "AvailableModels" in mod.__all__
