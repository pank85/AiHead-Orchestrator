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
    materialize_toggle,
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
