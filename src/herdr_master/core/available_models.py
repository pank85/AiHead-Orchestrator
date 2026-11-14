"""Project allowlists for harness model pickers.

Missing config inherits unrestricted. A present harness key, including an empty
list, is an explicit allowlist for that mode and harness only. These helpers do
not probe the catalog and do not reject operator overrides.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator

from herdr_master.core.catalog import HarnessKind

__all__ = [
    "AvailableModels",
    "enabled_model_ids",
    "filter_model_ids",
    "format_available_models_block",
    "is_all_enabled",
    "materialize_toggle",
    "materialize_toggle_all",
]

_MODES = ("herdr", "native")
_HARNESS_VALUES = {kind.value: kind.value for kind in HarnessKind}


class AvailableModels(BaseModel):
    """Saved per-mode harness allowlists.

    ``None`` on either mode map means that mode was not saved. An absent harness
    key stays unrestricted. An empty list is an explicit empty allowlist.
    """

    model_config = ConfigDict(extra="allow")

    herdr: Optional[Dict[str, List[str]]] = None
    native: Optional[Dict[str, List[str]]] = None

    @field_validator("herdr", "native", mode="before")
    @classmethod
    def _validate_mode_map(cls, value: Any) -> Optional[Dict[str, List[str]]]:
        return _clean_mode_map(value)


def enabled_model_ids(
    available: Optional[AvailableModels],
    mode: str,
    harness: Union[HarnessKind, str],
) -> Optional[List[str]]:
    """Return the explicit allowlist, or None when that scope is unrestricted.

    ``None``, a missing mode map, and an absent harness key are inherit. A
    present key, including ``[]``, is returned as a copy.
    """
    mode_map = _mode_map(available, mode)
    if mode_map is None:
        return None
    key = _lookup_harness(harness)
    if key is None or key not in mode_map:
        return None
    return list(mode_map[key])


def filter_model_ids(
    available: Optional[AvailableModels],
    mode: str,
    harness: Union[HarnessKind, str],
    discovered: List[str],
    *,
    current: Optional[str] = None,
) -> List[str]:
    """Intersect a discovered pool with the allowlist.

    Unrestricted scopes return ``discovered`` unchanged, including its identity,
    and do not append ``current``. A restricted scope keeps discovered order,
    drops ids that are not allowlisted, then appends a non-empty ``current``
    when it is missing from that result.
    """
    allowlist = enabled_model_ids(available, mode, harness)
    if allowlist is None:
        return discovered
    allowed = set(allowlist)
    filtered = [model_id for model_id in discovered if model_id in allowed]
    if _nonempty_current(current):
        curr = current.strip()
        if curr not in filtered and current not in filtered:
            filtered.append(current)
    return filtered


def is_all_enabled(
    available: Optional[AvailableModels],
    mode: str,
    harness: Union[HarnessKind, str],
    discovered: List[str],
) -> bool:
    """Return whether all discovered models are allowlisted.

    An unrestricted scope (missing mode or harness key) returns True.
    An explicit empty list returns False.
    Otherwise, returns True if discovered is a subset of the allowlist.
    """
    allowlist = enabled_model_ids(available, mode, harness)
    if allowlist is None:
        return True
    if not allowlist:
        return False
    return set(discovered).issubset(set(allowlist))


def materialize_toggle(
    available: Optional[AvailableModels],
    mode: str,
    harness: Union[HarnessKind, str],
    model_id: str,
    enabled: bool,
    discovered: List[str],
) -> AvailableModels:
    """Copy-on-first-touch toggle. Does not probe the catalog.

    The first touch of a harness with no saved key copies that mode's discovered
    ids for that harness only, then applies the toggle. Other harnesses and the
    other mode stay as they were, including unrestricted ``None``.
    """
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a bool")
    mode_name = _require_mode(mode)
    harness_key = _require_harness_key(harness)
    cleaned_id = _clean_model_id(model_id)

    herdr, native, extras = _clone_maps(available)
    target = herdr if mode_name == "herdr" else native
    if target is None:
        target = {}
    if harness_key not in target:
        ids = []
        for item in discovered:
            cid = _clean_model_id(item)
            if cid not in ids:
                ids.append(cid)
    else:
        ids = list(target[harness_key])
    if enabled:
        if cleaned_id not in ids:
            ids.append(cleaned_id)
    else:
        ids = [item for item in ids if item != cleaned_id]
    target[harness_key] = ids
    if mode_name == "herdr":
        herdr = target
    else:
        native = target
    return AvailableModels(herdr=herdr, native=native, **extras)


def materialize_toggle_all(
    available: Optional[AvailableModels],
    mode: str,
    harness: Union[HarnessKind, str],
    enabled: bool,
    discovered: List[str],
) -> AvailableModels:
    """Materialize an all-enabled or all-disabled allowlist for a harness.

    Toggling to True sets target[harness_key] to all cleaned, deduplicated
    discovered ids. Toggling to False sets target[harness_key] to an explicit
    empty list []. Untouched harnesses and modes remain unaffected.
    """
    if not isinstance(enabled, bool):
        raise TypeError("enabled must be a bool")
    mode_name = _require_mode(mode)
    harness_key = _require_harness_key(harness)

    herdr, native, extras = _clone_maps(available)
    target = herdr if mode_name == "herdr" else native
    if target is None:
        target = {}

    if enabled:
        cleaned: List[str] = []
        for item in discovered:
            cid = _clean_model_id(item)
            if cid not in cleaned:
                cleaned.append(cid)
        target[harness_key] = cleaned
    else:
        target[harness_key] = []

    if mode_name == "herdr":
        herdr = target
    else:
        native = target
    return AvailableModels(herdr=herdr, native=native, **extras)


def format_available_models_block(available: Optional[AvailableModels]) -> str:
    """Render saved allowlists without probing.

    ``available is None`` is ``unrestricted (no allowlist saved)``. Otherwise
    only saved harness keys are listed, in mode order then ``HarnessKind``
    order. An explicit empty list is ``(empty)``. Absent keys are omitted.
    """
    if available is None:
        return "unrestricted (no allowlist saved)"
    lines: List[str] = []
    for mode in _MODES:
        mode_map = getattr(available, mode)
        if not mode_map:
            continue
        pending = {str(key): list(ids) for key, ids in mode_map.items()}
        ordered = [kind.value for kind in HarnessKind if kind.value in pending]
        ordered.extend(key for key in pending if key not in ordered)
        for key in ordered:
            ids = pending[key]
            rendered = ", ".join(ids) if ids else "(empty)"
            lines.append(f"{mode}.{key}: {rendered}")
    return "\n".join(lines)


def _known_harnesses() -> str:
    return ", ".join(kind.value for kind in HarnessKind)


def _require_mode(mode: Any) -> str:
    if not isinstance(mode, str):
        raise ValueError(f"Unknown mode {mode!r}. Expected 'herdr' or 'native'.")
    name = mode.strip().lower()
    if name not in _MODES:
        raise ValueError(f"Unknown mode {mode!r}. Expected 'herdr' or 'native'.")
    return name


def _lookup_harness(harness: Any) -> Optional[str]:
    try:
        return _require_harness_key(harness)
    except ValueError:
        return None


def _require_harness_key(value: Any) -> str:
    if isinstance(value, HarnessKind):
        return value.value
    if isinstance(value, str):
        key = value.strip()
        if key in _HARNESS_VALUES:
            return key
    raise ValueError(
        f"Unknown harness key {value!r}. Expected one of: {_known_harnesses()}."
    )


def _clean_model_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"Model id must be a non-empty string, got {type(value).__name__}."
        )
    model_id = value.strip()
    if not model_id:
        raise ValueError("Model id must not be blank.")
    return model_id


def _clean_mode_map(value: Any) -> Optional[Dict[str, List[str]]]:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(
            "allowlist mode map must be a table of harness keys to model id lists"
        )
    cleaned: Dict[str, List[str]] = {}
    for key, ids in value.items():
        harness = _require_harness_key(key)
        if not isinstance(ids, list):
            raise ValueError(
                f"Allowlist for harness {harness!r} must be a list of model ids."
            )
        cleaned[harness] = [_clean_model_id(item) for item in ids]
    return cleaned


def _mode_map(
    available: Optional[AvailableModels],
    mode: str,
) -> Optional[Dict[str, List[str]]]:
    if available is None or not isinstance(mode, str):
        return None
    name = mode.strip().lower()
    if name not in _MODES:
        return None
    return getattr(available, name)


def _nonempty_current(current: Optional[str]) -> bool:
    return isinstance(current, str) and bool(current.strip())


def _copy_mode_map(
    mode_map: Optional[Dict[str, List[str]]],
) -> Optional[Dict[str, List[str]]]:
    if mode_map is None:
        return None
    return {str(key): list(ids) for key, ids in mode_map.items()}


def _clone_maps(
    available: Optional[AvailableModels],
) -> tuple[Optional[Dict[str, List[str]]], Optional[Dict[str, List[str]]], Dict[str, Any]]:
    if available is None:
        return None, None, {}
    return (
        _copy_mode_map(available.herdr),
        _copy_mode_map(available.native),
        dict(available.model_extra or {}),
    )
