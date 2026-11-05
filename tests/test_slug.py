"""Tests for harness binding slug parsing and formatting."""

import pytest

from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind, Squad
from herdr_master.core.slug import (
    InvalidBindingSlugError,
    parse_binding,
)


def test_four_spec_examples():
    """Verify the four load-bearing examples from §0 of GROK_IMPLEMENTATION_PLAN.md."""
    # 1. agy:gemini-3.8-flash-high (model has -high suffix; must NOT be stripped)
    b1 = parse_binding("agy:gemini-3.8-flash-high")
    assert b1.kind == HarnessKind.AGY
    assert b1.model == "gemini-3.8-flash-high"
    assert b1.effort is None
    assert b1.to_slug() == "agy:gemini-3.8-flash-high"

    # 2. pi:openai-codex/gpt-6.1-terra:high
    b2 = parse_binding("pi:openai-codex/gpt-6.1-terra:high")
    assert b2.kind == HarnessKind.PI
    assert b2.model == "openai-codex/gpt-6.1-terra"
    assert b2.effort == EffortLevel.HIGH
    assert b2.to_slug() == "pi:openai-codex/gpt-6.1-terra:high"

    # 3. pi:xai/grok-4.7
    b3 = parse_binding("pi:xai/grok-4.7")
    assert b3.kind == HarnessKind.PI
    assert b3.model == "xai/grok-4.7"
    assert b3.effort is None
    assert b3.to_slug() == "pi:xai/grok-4.7"

    # 4. agy:gemini-4-argon-high
    b4 = parse_binding("agy:gemini-4-argon-high")
    assert b4.kind == HarnessKind.AGY
    assert b4.model == "gemini-4-argon-high"
    assert b4.effort is None
    assert b4.to_slug() == "agy:gemini-4-argon-high"


def test_xhigh_alias_to_max():
    """Verify that xhigh aliases to max in storage and renders back as max."""
    b = parse_binding("pi:xai/grok-4.7:xhigh")
    assert b.kind == HarnessKind.PI
    assert b.model == "xai/grok-4.7"
    assert b.effort == EffortLevel.MAX
    assert b.to_slug() == "pi:xai/grok-4.7:max"


def test_model_slugs_with_slashes_dots_dashes():
    """Model slugs containing slashes, dots, and dashes are preserved without alteration."""
    slug = "pi:opencode-go/deepseek-v4.1-flash:medium"
    b = parse_binding(slug)
    assert b.kind == HarnessKind.PI
    assert b.model == "opencode-go/deepseek-v4.1-flash"
    assert b.effort == EffortLevel.MEDIUM
    assert b.to_slug() == slug


def test_default_effort_application():
    """When a slug lacks effort, a default effort can be applied."""
    b = parse_binding("claude:claude-3-5-sonnet", default_effort=EffortLevel.HIGH)
    assert b.kind == HarnessKind.CLAUDE
    assert b.model == "claude-3-5-sonnet"
    assert b.effort == EffortLevel.HIGH
    assert b.to_slug() == "claude:claude-3-5-sonnet:high"


def test_invalid_slugs():
    """Invalid slugs must raise InvalidBindingSlugError."""
    with pytest.raises(InvalidBindingSlugError):
        parse_binding("")

    with pytest.raises(InvalidBindingSlugError):
        parse_binding("invalid_no_colon")

    with pytest.raises(InvalidBindingSlugError):
        parse_binding("unknown_kind:model-name")

    with pytest.raises(InvalidBindingSlugError):
        parse_binding("agy:")

    with pytest.raises(InvalidBindingSlugError):
        parse_binding("agy:model:invalid_effort")

    with pytest.raises(InvalidBindingSlugError):
        parse_binding("agy:model:too:many:colons")


def test_squad_defaults_and_validation():
    """Squad defaults load the catalog product bindings and still detect family conflicts."""
    squad = Squad()
    assert squad.scout.model == "gemini-3.8-flash-medium"
    assert squad.scout.effort == EffortLevel.MEDIUM
    assert squad.scout.to_slug() == "agy:gemini-3.8-flash-medium:medium"
    assert squad.architect.model == "openai-codex/gpt-6.1-sol"
    assert squad.architect.effort == EffortLevel.MAX
    assert squad.architect.to_slug() == "pi:openai-codex/gpt-6.1-sol:max"
    assert squad.reviewer.model == "xai/grok-4.7"
    assert squad.reviewer.effort == EffortLevel.HIGH
    assert squad.reviewer.to_slug() == "pi:xai/grok-4.7:high"
    assert len(squad.worker_pool) == 2
    assert squad.worker_pool[0].label == "worker-1"
    assert squad.worker_pool[0].to_slug() == "agy:gemini-3.8-flash"
    assert squad.worker_pool[0].effort is None
    assert squad.worker_pool[1].label == "worker-2"
    assert squad.worker_pool[1].to_slug() == "pi:xai/grok-4.7"
    assert squad.worker_pool[1].effort is None
    assert "claude-3-opus" not in squad.scout.to_slug()
    assert "argon" not in squad.reviewer.to_slug()

    # Reviewer (xai) != Architect (openai-codex)
    assert not squad.has_reviewer_model_family_conflict()

    # If reviewer changed to codex/gpt, conflict is detected
    conflicting_squad = Squad(
        architect=HarnessBinding.from_slug("pi:openai-codex/gpt-6.1-sol:high"),
        reviewer=HarnessBinding.from_slug("pi:openai-codex/gpt-6.1-terra"),
    )
    assert conflicting_squad.has_reviewer_model_family_conflict()
