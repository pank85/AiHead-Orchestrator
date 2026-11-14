"""Allowlist persistence: missing config inherits, empty collections are explicit."""

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pytest
from pydantic import ValidationError

from herdr_master.cli.setup_catalog import (
    ChoiceOption,
    ModelCatalog,
    discover_model_catalog,
)
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


def test_model_catalog_add_custom_model_all_harnesses_both_modes():
    """Verify ModelCatalog.add_custom_model adds custom model IDs for all harnesses

    in both herdr and native modes, and catalog.models(...) returns them.
    """
    catalog = discover_model_catalog(probe_live=False)

    for harness in HarnessKind:
        h_name = harness.value
        for mode in ("herdr", "native"):
            custom_id = f"custom-provider/{h_name}-{mode}-v1"

            # Add custom model
            catalog.add_custom_model(h_name, custom_id, mode=mode)

            # models() should return the newly added custom model
            options = catalog.models(h_name, mode=mode)
            matches = [opt for opt in options if opt.value == custom_id]
            assert len(matches) == 1, f"Expected 1 match for {custom_id} in {mode}/{h_name}"
            assert matches[0].label == custom_id
            assert matches[0].detail == "custom"

            # models() with current matching custom model should include it without duplicate
            curr_options = catalog.models(h_name, mode=mode, current=custom_id)
            curr_matches = [opt for opt in curr_options if opt.value == custom_id]
            assert len(curr_matches) == 1

            # models() with another current model should still include the custom model
            other_options = catalog.models(
                h_name, mode=mode, current=f"some-other-{h_name}-current"
            )
            assert any(opt.value == custom_id for opt in other_options)


def test_model_catalog_add_custom_model_deduplication_and_validation():
    """Verify deduplication, whitespace stripping, and input validation."""
    catalog = ModelCatalog(
        herdr_harnesses=[],
        native_harnesses=[],
        herdr_models={"pi": [ChoiceOption(value="stock-pi-1", label="stock-pi-1", detail="live")]},
        native_models={},
    )

    # Adding custom model
    catalog.add_custom_model("pi", "custom/foo", mode="herdr")
    opts = [opt for opt in catalog.models("pi", mode="herdr") if opt.value == "custom/foo"]
    assert len(opts) == 1

    # Adding exact duplicate is a no-op
    catalog.add_custom_model("pi", "custom/foo", mode="herdr")
    opts = [opt for opt in catalog.models("pi", mode="herdr") if opt.value == "custom/foo"]
    assert len(opts) == 1

    # Adding with whitespace strips and prevents duplicate
    catalog.add_custom_model("pi", "  custom/foo  ", mode="herdr")
    opts = [opt for opt in catalog.models("pi", mode="herdr") if opt.value == "custom/foo"]
    assert len(opts) == 1

    # Adding existing stock model does not duplicate or overwrite detail
    catalog.add_custom_model("pi", "stock-pi-1", mode="herdr")
    matches = [opt for opt in catalog.models("pi", mode="herdr") if opt.value == "stock-pi-1"]
    assert len(matches) == 1
    assert matches[0].detail == "live"

    # Validation: empty or blank model ID
    with pytest.raises(ValueError, match="blank"):
        catalog.add_custom_model("pi", "", mode="herdr")
    with pytest.raises(ValueError, match="blank"):
        catalog.add_custom_model("pi", "   ", mode="herdr")

    # Validation: non-string model ID
    with pytest.raises(ValueError, match="non-empty string"):
        catalog.add_custom_model("pi", None, mode="herdr")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-empty string"):
        catalog.add_custom_model("pi", 123, mode="herdr")  # type: ignore[arg-type]

    # Validation: invalid mode
    with pytest.raises(ValueError, match="Unknown mode"):
        catalog.add_custom_model("pi", "custom/foo", mode="invalid_mode")


def test_custom_models_allowlist_materialize_toggle_and_filter():
    """Verify custom model IDs in allowlist work with materialize_toggle and filter_model_ids."""
    for harness in HarnessKind:
        for mode in ("herdr", "native"):
            custom_id = f"custom-org/model-{harness.value}"
            stock_id = f"stock-model-{harness.value}"

            # First touch: materialize_toggle adds custom_id to discovered pool
            available = materialize_toggle(
                None, mode, harness, custom_id, True, [stock_id]
            )
            allowlist = enabled_model_ids(available, mode, harness)
            assert allowlist is not None
            assert custom_id in allowlist
            assert stock_id in allowlist

            # filter_model_ids retains allowlisted custom and stock models
            discovered = [stock_id, custom_id, "unallowed-model"]
            filtered = filter_model_ids(available, mode, harness, discovered)
            assert filtered == [stock_id, custom_id]

            # filter_model_ids appends non-empty current even if not in allowlist
            filtered_curr = filter_model_ids(
                available, mode, harness, discovered, current="another-custom"
            )
            assert filtered_curr == [stock_id, custom_id, "another-custom"]

            # Toggle off custom model
            available_off = materialize_toggle(
                available, mode, harness, custom_id, False, discovered
            )
            allowlist_off = enabled_model_ids(available_off, mode, harness)
            assert allowlist_off is not None
            assert custom_id not in allowlist_off
            assert stock_id in allowlist_off

            # is_all_enabled checks
            assert is_all_enabled(available, mode, harness, [stock_id, custom_id]) is True
            assert is_all_enabled(available, mode, harness, [stock_id, custom_id, "other"]) is False
            assert is_all_enabled(available_off, mode, harness, [custom_id]) is False


def test_custom_models_serialization_and_roundtrip_toml(tmp_path):
    """Verify serialization and roundtrip of custom model IDs in .herdr-master.toml."""
    cfg = HerdrMasterConfig()
    herdr_allowlist = {
        h.value: [f"custom-vendor/{h.value}-herdr-1", f"custom-vendor/{h.value}-herdr-2"]
        for h in HarnessKind
    }
    native_allowlist = {
        h.value: [f"custom-vendor/{h.value}-native-1"]
        for h in HarnessKind
    }
    cfg.models.available = AvailableModels(
        herdr=herdr_allowlist,
        native=native_allowlist,
    )

    config_path = tmp_path / ".herdr-master.toml"
    save_project_config(cfg, config_path)

    # Verify written text contains custom model names
    text = config_path.read_text(encoding="utf-8")
    for h in HarnessKind:
        assert f"custom-vendor/{h.value}-herdr-1" in text
        assert f"custom-vendor/{h.value}-native-1" in text

    # Reload and verify full roundtrip
    reloaded = load_project_config(config_path)
    assert reloaded.models.available is not None
    assert reloaded.models.available.herdr == herdr_allowlist
    assert reloaded.models.available.native == native_allowlist

    for h in HarnessKind:
        assert (
            enabled_model_ids(reloaded.models.available, "herdr", h)
            == herdr_allowlist[h.value]
        )
        assert (
            enabled_model_ids(reloaded.models.available, "native", h)
            == native_allowlist[h.value]
        )

