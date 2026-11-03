"""Harness binding grammar and slug parser.

Binding grammar:
  binding := kind ":" model_ref [ ":" effort ]
  kind    := agy | pi | claude | opencode | codex | gemini
  effort  := low | medium | high | max | xhigh   # xhigh aliases to max in storage
  model_ref contains slashes, dots, dashes; no colon
"""

from typing import Optional, Tuple

from herdr_master.core.models import EffortLevel, HarnessBinding, HarnessKind


class InvalidBindingSlugError(ValueError):
    """Raised when a harness binding slug violates grammar."""

    pass


EFFORT_ALIASES = {
    "xhigh": EffortLevel.MAX,
}

VALID_EFFORTS = {e.value for e in EffortLevel} | set(EFFORT_ALIASES.keys())


def parse_slug_components(raw: str) -> Tuple[HarnessKind, str, Optional[EffortLevel]]:
    """Parse a raw slug string into (kind, model_ref, effort).

    Rules:
    - Split on the first colon for kind.
    - If there is a trailing colon, inspect whether the token after it is an effort level.
    - Do not treat '-high' inside 'gemini-4-argon-high' as effort.
    - 'xhigh' aliases to 'max' in storage.
    """
    if not raw or not isinstance(raw, str):
        raise InvalidBindingSlugError("Binding slug cannot be empty")

    slug = raw.strip()
    if ":" not in slug:
        raise InvalidBindingSlugError(
            f"Invalid binding slug '{raw}': missing colon separator between kind and model"
        )

    parts = slug.split(":", 1)
    kind_str, rest = parts[0].strip().lower(), parts[1].strip()

    try:
        kind = HarnessKind(kind_str)
    except ValueError as err:
        valid_kinds = ", ".join(k.value for k in HarnessKind)
        raise InvalidBindingSlugError(
            f"Invalid harness kind '{kind_str}'. Must be one of: {valid_kinds}"
        ) from err

    if not rest:
        raise InvalidBindingSlugError(f"Model reference cannot be empty in '{raw}'")

    effort: Optional[EffortLevel] = None
    model_ref: str

    if ":" in rest:
        model_part, effort_part = rest.rsplit(":", 1)
        effort_key = effort_part.strip().lower()
        if effort_key in VALID_EFFORTS:
            if not model_part:
                raise InvalidBindingSlugError(f"Model reference cannot be empty in '{raw}'")
            if ":" in model_part:
                raise InvalidBindingSlugError(f"Model reference cannot contain colons in '{raw}'")
            model_ref = model_part
            if effort_key in EFFORT_ALIASES:
                effort = EFFORT_ALIASES[effort_key]
            else:
                effort = EffortLevel(effort_key)
        else:
            raise InvalidBindingSlugError(
                f"Invalid trailing token or colon in '{raw}': '{effort_part}' is not a valid effort"
            )
    else:
        model_ref = rest

    return kind, model_ref, effort


def parse_binding(
    raw: str,
    default_effort: Optional[EffortLevel] = None,
    label: Optional[str] = None,
    enabled: bool = True,
    order: int = 0,
) -> HarnessBinding:
    """Parse a slug into a HarnessBinding instance."""
    kind, model_ref, effort = parse_slug_components(raw)
    final_effort = effort if effort is not None else default_effort
    return HarnessBinding(
        kind=kind,
        model=model_ref,
        effort=final_effort,
        label=label,
        enabled=enabled,
        order=order,
    )


def format_binding(binding: HarnessBinding, include_effort: bool = True) -> str:
    """Render a HarnessBinding back to canonical slug form.

    If effort is present and include_effort is True:
        kind:model:effort
    Otherwise:
        kind:model
    """
    base = f"{binding.kind.value}:{binding.model}"
    if include_effort and binding.effort is not None:
        return f"{base}:{binding.effort.value}"
    return base
