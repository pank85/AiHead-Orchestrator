"""Squad configuration lifecycle, validation, serialization, and prompt formatting."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from herdr_master.core.available_models import AvailableModels, enabled_model_ids
from herdr_master.core.catalog import (
    SCHEMA_VERSION,
    CatalogError,
    HarnessKind,
    LaunchMode,
    describe_herdr_agent,
    describe_native_agent,
    ensure_herdr_routable,
    get_harness,
    native_binding_error,
    qualify_binding_slug,
    split_worker_bindings,
    supported_harnesses,
    validate_mode,
    worker_capability,
    worker_route_note,
)
from herdr_master.core.config import (
    find_project_config_path,
    load_effective_models_state,
    load_effective_project_config,
    load_project_config,
    project_model_overrides,
    resolve_effective_worker_mode,
    save_project_config,
    squad_to_project_config,
    validate_routing_profile,
)
from herdr_master.core.model_ratings import (
    ModelRating,
    ModelRatingsMap,
    effective_worker_tier,
    format_context_window,
    lookup_rating,
)
from herdr_master.core.models import HarnessBinding, Squad
from herdr_master.core.slug import InvalidBindingSlugError, parse_binding

_ROLE_KEYS = ("scout", "architect", "reviewer")
_SOURCE_KEYS = ("scout", "architect", "reviewer", "workers")
WORKER_TIER_LABELS: Dict[int, str] = {
    1: "fast/cheap",
    2: "decent/all-around",
    3: "frontier/clever",
}
MODE_TIER_PREFERENCE: Dict[str, Tuple[int, int, int]] = {
    "balanced": (2, 1, 3),
    "frugal": (1, 2, 3),
    "quality": (3, 2, 1),
    "fast": (1, 2, 3),
}
MODE_ROLE_TIER_GUIDANCE: Dict[str, Dict[str, Tuple[int, ...]]] = {
    "balanced": {"scout": (1,), "architect": (3,), "reviewer": (2,), "worker": (2, 1)},
    "frugal": {"scout": (1,), "architect": (2, 1), "reviewer": (1, 2), "worker": (1, 2)},
    "quality": {"scout": (2, 1), "architect": (3,), "reviewer": (3, 2), "worker": (3, 2)},
    "fast": {"scout": (1,), "architect": (2, 3), "reviewer": (2, 1), "worker": (1, 2)},
}
_COMPACT_ROLES = {
    "scout": "scout",
    "architect": "architect",
    "reviewer": "reviewer",
    "workers": "workers",
    "worker": "workers",
    "coder": "workers",
    "coders": "workers",
}


class SquadValidationError(ValueError):
    """Raised when squad configuration violates invariants."""


def validate_squad(squad: Squad) -> List[str]:
    """Validate squad configuration and return a list of error messages.

    Checks:
    - Worker pool is non-empty.
    - At least one worker is enabled.
    - No duplicate (kind, model, effort) entries in the worker pool.
    - Scout, Architect, and Reviewer are valid bindings.
    """
    errors: List[str] = []

    if not squad.worker_pool:
        errors.append("Worker pool cannot be empty: at least one worker model is required.")
    elif not any(worker.enabled for worker in squad.worker_pool):
        errors.append(
            "Worker pool cannot be all disabled: at least one enabled worker model is required."
        )

    for role_name, binding in (
        ("scout", squad.scout),
        ("architect", squad.architect),
        ("reviewer", squad.reviewer),
    ):
        if not binding.kind or not binding.model:
            errors.append(f"{role_name.capitalize()} binding is incomplete.")

    seen_bindings = set()
    for idx, worker in enumerate(squad.worker_pool):
        key = (worker.kind, worker.model, worker.effort)
        if key in seen_bindings:
            errors.append(f"Duplicate worker model binding at row {idx + 1}: '{worker.to_slug()}'")
        seen_bindings.add(key)

    return errors


_WORKER_MODE_GUIDANCE: Dict[str, str] = {
    "balanced": (
        "match model strength & effort to task complexity; use fast/cheap models for "
        "mechanical work and high-coding/intelligence models for complex tasks"
    ),
    "quality": (
        "prioritize highest coding, intelligence, and agentic ratings with higher effort "
        "even on medium tasks"
    ),
    "fast": (
        "prioritize highest speed and low latency while maintaining adequate coding skill "
        "for each task"
    ),
    "frugal": (
        "prioritize highest cost_efficiency / free routes and lower effort; reserve heavy "
        "models strictly for high-complexity tasks"
    ),
}


def worker_mode_rank_score(rating: Optional[ModelRating], worker_mode: str = "balanced") -> float:
    """Compute a sorting score for a candidate coder model under ``worker_mode``."""
    if rating is None:
        return -1.0
    mode = validate_routing_profile(worker_mode)
    if mode == "quality":
        return 0.45 * rating.coding + 0.35 * rating.intelligence + 0.20 * rating.agentic
    if mode == "fast":
        return 0.50 * rating.speed + 0.30 * rating.coding + 0.20 * rating.cost_efficiency
    if mode == "frugal":
        return 0.55 * rating.cost_efficiency + 0.25 * rating.coding + 0.20 * rating.speed
    return (
        0.35 * rating.coding
        + 0.25 * rating.intelligence
        + 0.15 * rating.agentic
        + 0.15 * rating.speed
        + 0.10 * rating.cost_efficiency
    )


def tier_preference_rank(tier: int, worker_mode: str = "balanced") -> int:
    """Return the preference index for a worker tier; unknown tiers sort last."""
    mode = validate_routing_profile(worker_mode)
    try:
        return MODE_TIER_PREFERENCE[mode].index(tier)
    except ValueError:
        return 3


def worker_tier_allowed(
    tier: int, *, very_hard: bool, worker_mode: str = "balanced"
) -> bool:
    """Return whether a rated tier is eligible for the task and selected mode."""
    if type(very_hard) is not bool:
        raise ValueError("very_hard must be a boolean from the difficulty classifier")
    mode = validate_routing_profile(worker_mode)
    if tier not in (1, 2, 3):
        return False
    return tier != 3 or very_hard or mode == "quality"


def preferred_coder_pool(
    pool: List[HarnessBinding],
    ratings: Optional[ModelRatingsMap],
    *,
    very_hard: bool,
    worker_mode: str = "balanced",
) -> List[HarnessBinding]:
    """Filter enabled rated bindings by tier policy and order by mode preference."""
    mode = validate_routing_profile(worker_mode)
    ranked = []
    for index, binding in enumerate(pool):
        if not binding.enabled:
            continue
        rating = lookup_rating(ratings, binding.kind, binding.model)
        if rating is None:
            continue
        tier = effective_worker_tier(rating)
        if not worker_tier_allowed(tier, very_hard=very_hard, worker_mode=mode):
            continue
        ranked.append(
            (
                tier_preference_rank(tier, mode),
                -worker_mode_rank_score(rating, mode),
                index,
                binding,
            )
        )
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


def format_mode_tier_guidance(worker_mode: str = "balanced") -> str:
    """Format tier definitions and nonbinding role preferences for a mode."""
    mode = validate_routing_profile(worker_mode)
    role_guidance = MODE_ROLE_TIER_GUIDANCE[mode]
    lines = [
        "WORKER TIERS:",
        *(f"{tier}={label}" for tier, label in WORKER_TIER_LABELS.items()),
        f"MODE PREFERENCE ({mode}): "
        + " > ".join(str(tier) for tier in MODE_TIER_PREFERENCE[mode]),
        "ROLE TIER GUIDANCE:",
    ]
    lines.extend(
        f"{role}: {', '.join(str(tier) for tier in tiers)}"
        for role, tiers in role_guidance.items()
    )
    lines.append("These are preferences, not a one-to-one lock.")
    return "\n".join(lines)


def eligible_coder_pool(
    pool: List[HarnessBinding],
    ratings: Optional[ModelRatingsMap],
    *,
    very_hard: bool,
    worker_mode: str = "balanced",
) -> List[HarnessBinding]:
    """Filter a resolved pool per task; unknown and disabled routes never qualify."""
    if type(very_hard) is not bool:
        raise ValueError("very_hard must be a boolean from the difficulty classifier")
    ranked = []
    for index, binding in enumerate(pool):
        if not binding.enabled:
            continue
        rating = lookup_rating(ratings, binding.kind, binding.model)
        if rating is None:
            continue
        tier = effective_worker_tier(rating)
        if tier not in (1, 2, 3) or (tier == 3 and not very_hard):
            continue
        ranked.append((tier, -worker_mode_rank_score(rating, worker_mode), index, binding))
    ranked.sort(key=lambda item: item[:3])
    return [item[3] for item in ranked]


def build_dynamic_coder_pool(
    *,
    available: Optional[AvailableModels],
    ratings: Optional[ModelRatingsMap],
    mode: LaunchMode = "herdr",
    native_harness: Optional[str] = None,
    agy_mode: str = "subagents",
    worker_mode: str = "balanced",
    disabled_harnesses: Optional[List[str] | set[str] | frozenset[str]] = None,
) -> tuple[List[HarnessBinding], str, List[str]]:
    """Derive the Architect's coder pool dynamically from ``[models.available]`` + ratings.

    - In ``herdr`` mode (Ergo): includes enabled Herdr models across all enabled harnesses.
    - In ``native`` mode (Orchestration): includes ONLY enabled models for ``native_harness``
      (respecting ``agy_mode == "swarm"`` using ``herdr.agy`` slugs vs ``native.agy`` tiers).
    - When completely unrestricted (``available is None``), falls back to already-rated
      models for the scope (or returns empty so caller retains catalog defaults) with an
      advisory suggesting configuring ``[models.available]``.
    """
    mode_key = validate_mode(mode)
    disabled_set = {str(h).strip().lower() for h in (disabled_harnesses or ())}
    advisories: List[str] = []
    pairs: List[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_candidate(kind_str: str, model_id: str) -> None:
        if kind_str.strip().lower() in disabled_set:
            return
        key = (kind_str, model_id)
        if key in seen:
            return
        # Validate routability silently so incompatible entries never break resolution
        try:
            if mode_key == "native":
                host = native_harness or kind_str
                if host == "agy" and agy_mode == "swarm":
                    ensure_herdr_routable(
                        kind_str, model_id, None, role="worker", require_effort=False
                    )
                else:
                    if native_binding_error(kind_str, model_id, role="coder", native_harness=host):
                        return
            else:
                ensure_herdr_routable(kind_str, model_id, None, role="worker", require_effort=False)
        except CatalogError:
            return
        seen.add(key)
        pairs.append(key)

    source_tag = "available_rated"

    if mode_key == "native":
        if not native_harness:
            return [], "product_default", advisories
        host = get_harness(native_harness).kind
        pool_mode = "herdr" if (host == "agy" and agy_mode == "swarm") else "native"
        allow = enabled_model_ids(available, pool_mode, host)
        if allow is None and pool_mode == "native" and host != "agy":
            allow = enabled_model_ids(available, "herdr", host)
        if allow is not None:
            for mid in allow:
                add_candidate(host, mid)
        else:
            source_tag = "rated_fallback"
            for mid in (ratings or {}).get(host, {}):
                add_candidate(host, mid)
            if pairs:
                advisories.append(
                    f"No [models.available] allowlist configured for {host}; "
                    "using rated models. Configure Available models in `aihead --setup`."
                )
    else:
        if available is not None and available.herdr:
            ordered_harnesses = [
                k.value for k in HarnessKind if k.value in available.herdr
            ]
            ordered_harnesses.extend(
                k for k in available.herdr if k not in ordered_harnesses
            )
            for h_kind in ordered_harnesses:
                for mid in available.herdr.get(h_kind, ()):
                    add_candidate(h_kind, mid)
        else:
            source_tag = "rated_fallback"
            if ratings:
                for h_kind in supported_harnesses(mode="herdr"):
                    for mid in ratings.get(h_kind, {}):
                        add_candidate(h_kind, mid)
                if pairs:
                    advisories.append(
                        "No [models.available] allowlist configured; using rated models. "
                        "Configure Available models in `aihead --setup`."
                    )

    if not pairs:
        return [], source_tag, advisories

    # Sort candidates by worker_mode preference (rated models first, preserving order on ties)
    indexed = list(enumerate(pairs))
    indexed.sort(
        key=lambda item: (
            -worker_mode_rank_score(lookup_rating(ratings, item[1][0], item[1][1]), worker_mode),
            item[0],
        )
    )
    pool: List[HarnessBinding] = []
    for order_idx, (_, (h_kind, mid)) in enumerate(indexed):
        pool.append(
            HarnessBinding(
                kind=HarnessKind(h_kind),
                model=mid,
                effort=None,
                label=f"worker-{order_idx + 1}",
                enabled=True,
                order=order_idx,
            )
        )
    return pool, source_tag, advisories


def format_allowed_coder_models(
    squad: Squad,
    *,
    mode: LaunchMode = "herdr",
    native_harness: Optional[str] = None,
    ratings: Optional[ModelRatingsMap] = None,
    worker_mode: str = "balanced",
) -> str:
    """Format the ordered worker pool block to paste into the Architect prompt.

    Workers do not have a hardcoded thinking effort; the Architect dynamically
    decides reasoning effort per task. Constraint lines name the supported routes.
    """
    eff_mode = validate_routing_profile(worker_mode)
    guidance = _WORKER_MODE_GUIDANCE[eff_mode]
    lines: List[str] = [
        f"WORKER SELECTION MODE: {eff_mode} ({guidance})",
        "ALLOWED CODER MODELS: (assign each task in plan.md one worker model from this list, "
        "and select reasoning effort low/medium/high/max based on task complexity):",
    ]
    ordered = sorted(enumerate(squad.worker_pool), key=lambda pair: (pair[1].order, pair[0]))
    for _, worker in ordered:
        if not worker.enabled:
            continue
        slug = worker.to_slug(include_effort=False)
        rating = lookup_rating(ratings, worker.kind.value, worker.model)
        if rating is not None:
            ctx = format_context_window(rating.context_window)
            eff = (
                f"choose {'|'.join(rating.supported_efforts)}"
                if rating.supported_efforts
                else "fixed/none"
            )
            roles = ",".join(rating.recommended_roles) if rating.recommended_roles else "-"
            tier = effective_worker_tier(rating)
            tier_source = (
                "manual"
                if rating.worker_tier_override
                else "derived"
                if rating.worker_tier is None
                else "auto"
            )
            lines.append(
                f"- {slug} | fam={rating.family} | "
                f"I:{rating.intelligence} C:{rating.coding} A:{rating.agentic} "
                f"S:{rating.speed} $:{rating.cost_efficiency} Ctx:{ctx} | "
                f"billing_tier={rating.billing_tier} | "
                f"worker_tier={tier} ({tier_source}) | effort: {eff} | roles={roles}"
            )
        else:
            hint = f" ({worker.label})" if worker.label else ""
            desc = f" — {worker.description}" if worker.description else ""
            lines.append(f"- {slug}{hint}{desc}")
        note = worker_route_note(
            worker.kind.value,
            worker.model,
            mode=mode,
            native_harness=native_harness,
        )
        if note:
            lines.append(f"  {note}")
    return "\n".join(lines)


def _binding_record(binding: HarnessBinding) -> Dict[str, Any]:
    return {
        "kind": binding.kind.value,
        "model": binding.model,
        "effort": binding.effort.value if binding.effort else None,
        "label": binding.label,
        "description": binding.description,
        "enabled": binding.enabled,
        "order": binding.order,
    }


class ResolvedSquad:
    """A resolved squad plus provenance and derived launch metadata."""

    def __init__(
        self,
        squad: Squad,
        sources: Mapping[str, str],
        allowed_coder_models: str,
        *,
        mode: str,
        native_harness: Optional[str],
        advisories: List[str],
        roles: Dict[str, Any],
        workers: List[Dict[str, Any]],
        worker_mode: str = "balanced",
    ) -> None:
        self.squad = squad
        self.sources = dict(sources)
        self.allowed_coder_models = allowed_coder_models
        self.mode = mode
        self.native_harness = native_harness
        self.advisories = list(advisories)
        self.roles = roles
        self.workers = workers
        self.worker_mode = worker_mode

    def to_dict(self) -> Dict[str, Any]:
        squad = self.squad
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": self.mode,
            "native_harness": self.native_harness,
            "worker_mode": self.worker_mode,
            "sources": dict(self.sources),
            "advisories": list(self.advisories),
            "squad": {
                "scout": _binding_record(squad.scout),
                "architect": _binding_record(squad.architect),
                "reviewer": _binding_record(squad.reviewer),
                "worker_pool": [_binding_record(worker) for worker in squad.worker_pool],
            },
            "roles": self.roles,
            "workers": self.workers,
            "allowed_coder_models": self.allowed_coder_models,
        }


def _clean_flag(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise CatalogError(f"--{name} is empty.")
    return text


def _parse_compact_models(models: str) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    tokens = models.split()
    if not tokens:
        raise CatalogError(
            "--models is empty. Expected role:binding entries separated by spaces."
        )
    for token in tokens:
        if ":" not in token:
            raise CatalogError(
                f"Malformed --models entry '{token}'. Expected role:binding "
                "(for example scout:agy:gemini-3.8-flash-medium:medium). "
                "Worker bindings are comma-separated without spaces."
            )
        role_raw, rest = token.split(":", 1)
        role_key = role_raw.strip().lower()
        if role_key not in _COMPACT_ROLES:
            raise CatalogError(
                f"Unknown role '{role_raw}'. Expected scout, architect, reviewer, or workers "
                "(aliases: coder, coders). Role entries are whitespace-separated; "
                "worker bindings must be comma-separated without spaces."
            )
        role = _COMPACT_ROLES[role_key]
        if not rest.strip():
            raise CatalogError(f"Role '{role}' has an empty binding in --models.")
        if role in parsed:
            raise CatalogError(f"Duplicate role '{role}' in --models.")
        parsed[role] = rest.strip()
    return parsed


def parse_squad_overrides(
    models: Optional[str] = None,
    *,
    scout: Optional[str] = None,
    architect: Optional[str] = None,
    workers: Optional[str] = None,
    reviewer: Optional[str] = None,
) -> Dict[str, str]:
    """Parse compact ``--models`` and individual role flags. Flags win on conflict."""
    parsed: Dict[str, str] = {}
    if models is not None:
        parsed.update(_parse_compact_models(models))
    flags = {
        "scout": _clean_flag("scout", scout),
        "architect": _clean_flag("architect", architect),
        "reviewer": _clean_flag("reviewer", reviewer),
        "workers": _clean_flag("workers", workers),
    }
    for key, value in flags.items():
        if value is not None:
            parsed[key] = value
    return parsed


def _apply_role_slug(
    squad: Squad,
    sources: Dict[str, str],
    role: str,
    raw: str,
    source: str,
    *,
    mode: str,
    native_harness: Optional[str],
) -> None:
    qualified = qualify_binding_slug(raw, mode=mode, native_harness=native_harness)
    try:
        binding = parse_binding(qualified, label=role)
    except InvalidBindingSlugError as err:
        raise CatalogError(f"Invalid {role} binding '{raw}': {err}") from err
    setattr(squad, role, binding)
    sources[role] = source


def _apply_worker_bindings(
    squad: Squad,
    sources: Dict[str, str],
    pool: List[HarnessBinding],
    source: str,
) -> None:
    cleaned: List[HarnessBinding] = []
    for worker in pool:
        cleaned.append(worker.model_copy(update={"effort": None}))
    squad.worker_pool = cleaned
    sources["workers"] = source


def _workers_from_override(
    raw: str,
    *,
    mode: str,
    native_harness: Optional[str],
) -> List[HarnessBinding]:
    pool: List[HarnessBinding] = []
    for idx, part in enumerate(split_worker_bindings(raw)):
        qualified = qualify_binding_slug(part, mode=mode, native_harness=native_harness)
        try:
            binding = parse_binding(qualified, label=f"worker-{idx + 1}", order=idx)
        except InvalidBindingSlugError as err:
            raise CatalogError(f"Invalid worker binding '{part}': {err}") from err
        binding.effort = None
        pool.append(binding)
    return pool


def _project_layer(project_path: Optional[Path]) -> Dict[str, Any]:
    if project_path is None:
        found = find_project_config_path()
        if found is None:
            return {"roles": {}, "worker_pool": None}
        return project_model_overrides(load_project_config(found))
    path = Path(project_path)
    if path.is_dir():
        candidate = path / ".herdr-master.toml"
        if not candidate.is_file():
            return {"roles": {}, "worker_pool": None}
        return project_model_overrides(load_project_config(candidate))
    if not path.is_file():
        raise FileNotFoundError(f"Project configuration file not found: {path}")
    return project_model_overrides(load_project_config(path))


def _pool_from_raw(raw_pool: Any) -> List[HarnessBinding]:
    pool: List[HarnessBinding] = []
    if isinstance(raw_pool, list):
        for idx, row in enumerate(raw_pool):
            if isinstance(row, dict):
                binding_str = row.get("binding") or row.get("slug") or ""
                binding = HarnessBinding.from_slug(
                    str(binding_str),
                    label=row.get("label"),
                    description=row.get("description"),
                    enabled=bool(row.get("enabled", True)),
                    order=int(row.get("order", idx)),
                )
            elif isinstance(row, str):
                binding = HarnessBinding.from_slug(row, order=idx)
            else:
                continue
            binding.effort = None
            pool.append(binding)
        return pool
    if isinstance(raw_pool, dict):
        for idx, (label, val) in enumerate(raw_pool.items()):
            if isinstance(val, dict):
                binding_str = val.get("binding") or val.get("slug") or ""
                binding = HarnessBinding.from_slug(
                    str(binding_str),
                    label=label,
                    description=val.get("description"),
                    enabled=bool(val.get("enabled", True)),
                    order=int(val.get("order", idx)),
                )
            else:
                binding = HarnessBinding.from_slug(str(val), label=label, order=idx)
            binding.effort = None
            pool.append(binding)
        return pool
    raise CatalogError("worker_pool must be a list or table.")


def _run_layer(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"Squad configuration file not found: {path}")
    with open(path, "rb") as handle:
        data = tomllib.load(handle)
    raw_roles = data.get("roles") or {}
    roles: Dict[str, str] = {}
    if isinstance(raw_roles, dict):
        for key in _ROLE_KEYS:
            if key in raw_roles and raw_roles[key]:
                roles[key] = str(raw_roles[key])
    workers = _pool_from_raw(data["worker_pool"]) if "worker_pool" in data else None
    return {"roles": roles, "worker_pool": workers}


def _family_advisory(squad: Squad) -> List[str]:
    if not squad.has_reviewer_model_family_conflict():
        return []
    return [
        "Advisory: Reviewer model family "
        f"'{squad.reviewer_model_family()}' matches Architect family "
        f"'{squad.architect_model_family()}'. Prefer a different reviewer family. "
        "This does not block resolution."
    ]


def _ensure_core_kind(kind: str) -> None:
    allowed = supported_harnesses(mode="core")
    if kind not in allowed:
        raise CatalogError(
            f"Harness '{kind}' is not supported in Core mode. "
            f"Supported: {', '.join(allowed)}."
        )


def _finalize(
    squad: Squad,
    sources: Dict[str, str],
    mode: str,
    native_harness: Optional[str],
    *,
    ratings: Optional[ModelRatingsMap] = None,
    worker_mode: str = "balanced",
    extra_advisories: Optional[List[str]] = None,
) -> ResolvedSquad:
    errors = validate_squad(squad)
    if errors:
        raise SquadValidationError(" ".join(errors))
    mode_key = validate_mode(mode)
    host = get_harness(native_harness).kind if native_harness else None
    if mode_key == "native":
        if host is None:
            raise CatalogError(
                "Native mode requires an explicit native host (--harness). "
                "There is no native default squad."
            )
        spec = get_harness(host)
        if not spec.supports_native:
            raise CatalogError(
                f"Harness '{spec.kind}' has no native adapter, install destination, "
                "or default model. Pass --herdr and an explicit --model. "
                "Refusing to fall back to another harness."
            )
        problems: List[str] = []
        for role in _ROLE_KEYS:
            binding = getattr(squad, role)
            problem = native_binding_error(
                binding.kind.value,
                binding.model,
                role=role,
                native_harness=host,
            )
            if problem:
                problems.append(problem)
        for worker in squad.worker_pool:
            label = worker.label or "worker"
            problem = native_binding_error(
                worker.kind.value,
                worker.model,
                role=label,
                native_harness=host,
            )
            if problem:
                problems.append(problem)
        if problems:
            raise CatalogError(
                f"Native host '{host}' cannot run the resolved squad: "
                + "; ".join(problems)
                + ". Override the incompatible entries or choose --herdr. "
                "There is no native default squad, and product bindings are not rewritten."
            )
    else:
        host = None
        for role in _ROLE_KEYS:
            binding = getattr(squad, role)
            ensure_herdr_routable(
                binding.kind.value,
                binding.model,
                binding.effort.value if binding.effort else None,
                role=role,
                require_effort=True,
            )
        for worker in squad.worker_pool:
            if mode_key == "core":
                _ensure_core_kind(worker.kind.value)
            if not worker.enabled:
                continue
            ensure_herdr_routable(
                worker.kind.value,
                worker.model,
                None,
                role=worker.label or "worker",
                require_effort=False,
            )
    role_descriptors = {
        role: describe_agent(
            getattr(squad, role),
            mode=mode_key,
            role=role,
            native_harness=host,
        )
        for role in _ROLE_KEYS
    }
    worker_rows: List[Dict[str, Any]] = []
    for worker in sorted(squad.worker_pool, key=lambda item: item.order):
        capability = worker_capability(
            worker.kind.value,
            worker.model,
            mode=mode_key,
            native_harness=host,
        )
        worker_rows.append(
            {
                "label": worker.label,
                "description": worker.description,
                "enabled": worker.enabled,
                "order": worker.order,
                "binding": _binding_record(worker),
                **capability,
            }
        )
    return ResolvedSquad(
        squad,
        sources,
        format_allowed_coder_models(
            squad,
            mode=mode_key,
            native_harness=host,
            ratings=ratings,
            worker_mode=worker_mode,
        ),
        mode=mode_key,
        native_harness=host,
        advisories=[*_family_advisory(squad), *(extra_advisories or [])],
        roles=role_descriptors,
        workers=worker_rows,
        worker_mode=worker_mode,
    )


def resolve_squad(
    project_path: Optional[Path] = None,
    *,
    mode: LaunchMode = "herdr",
    native_harness: Optional[str] = None,
    run_squad_path: Optional[Path] = None,
    overrides: Optional[Mapping[str, str]] = None,
    worker_mode: Optional[str] = None,
    inherit_ancestors: Optional[bool] = None,
) -> ResolvedSquad:
    """Resolve a squad. Explicit overrides beat the run snapshot, project file, then catalog."""
    mode_key = validate_mode(mode)
    if mode_key == "herdr" and native_harness:
        raise CatalogError(
            "--harness is only used in --native mode. Herdr mode selects the harness per binding."
        )
    if mode_key == "native" and not native_harness:
        raise CatalogError(
            "Native mode requires an explicit native host (--harness). "
            "There is no native default squad."
        )
    host = get_harness(native_harness).kind if native_harness else None
    squad = Squad()
    sources = {key: "product_default" for key in _SOURCE_KEYS}
    project = _project_layer(project_path)
    for role, slug in project["roles"].items():
        _apply_role_slug(
            squad, sources, role, slug, "project", mode=mode_key, native_harness=host
        )

    # Resolve effective worker_mode and dynamic rated coder pool from [models.available] + ratings.
    # Explicit TOML file paths stay hermetic unless inherit_ancestors=True is requested;
    # directory / workspace lookups automatically inherit ancestor ~/.herdr-master.toml.
    should_inherit = (
        inherit_ancestors
        if inherit_ancestors is not None
        else (project_path is None or Path(project_path).is_dir())
    )
    effective_config_path = project_path
    if effective_config_path is None:
        effective_config_path = find_project_config_path()
    raw_project_cfg = (
        load_effective_project_config(effective_config_path)
        if effective_config_path is not None
        and Path(effective_config_path).exists()
        and should_inherit
        else load_project_config(effective_config_path)
        if effective_config_path is not None and Path(effective_config_path).exists()
        else None
    )
    effective_models = (
        load_effective_models_state(
            effective_config_path,
            project_cfg=raw_project_cfg,
            inherit_ancestors=should_inherit,
        )
        if effective_config_path is not None
        else None
    )
    section_wmode: Optional[str] = None
    ai_head_prof: Optional[str] = None
    agy_mode = "subagents"
    if raw_project_cfg is not None:
        ai_head_prof = raw_project_cfg.ai_head.routing_profile
        agy_mode = raw_project_cfg.orchestration.agy_mode
        if mode_key == "native":
            section_wmode = raw_project_cfg.orchestration.worker_mode
        elif raw_project_cfg.ergo is not None:
            section_wmode = raw_project_cfg.ergo.worker_mode

    resolved_worker_mode = resolve_effective_worker_mode(
        worker_mode,
        section_mode=section_wmode,
        ai_head_profile=ai_head_prof,
    )
    effective_ratings = effective_models.ratings if effective_models is not None else None
    extra_advisories: List[str] = []

    if project["worker_pool"] is not None:
        _apply_worker_bindings(squad, sources, project["worker_pool"], "project")
    elif effective_models is not None:
        dyn_pool, dyn_source, dyn_advisories = build_dynamic_coder_pool(
            available=effective_models.available,
            ratings=effective_ratings,
            mode=mode_key,
            native_harness=host,
            agy_mode=agy_mode,
            worker_mode=resolved_worker_mode,
            disabled_harnesses=effective_models.disabled_harnesses,
        )
        extra_advisories.extend(dyn_advisories)
        if dyn_pool:
            _apply_worker_bindings(squad, sources, dyn_pool, dyn_source)

    if run_squad_path is not None:
        snapshot = _run_layer(Path(run_squad_path))
        for role, slug in snapshot["roles"].items():
            _apply_role_slug(
                squad,
                sources,
                role,
                slug,
                "run_snapshot",
                mode=mode_key,
                native_harness=host,
            )
        if snapshot["worker_pool"] is not None:
            _apply_worker_bindings(squad, sources, snapshot["worker_pool"], "run_snapshot")
    if overrides:
        for role in _ROLE_KEYS:
            if overrides.get(role):
                _apply_role_slug(
                    squad,
                    sources,
                    role,
                    overrides[role],
                    "override",
                    mode=mode_key,
                    native_harness=host,
                )
        if overrides.get("workers"):
            _apply_worker_bindings(
                squad,
                sources,
                _workers_from_override(overrides["workers"], mode=mode_key, native_harness=host),
                "override",
            )
    return _finalize(
        squad,
        sources,
        mode_key,
        host,
        ratings=effective_ratings,
        worker_mode=resolved_worker_mode,
        extra_advisories=extra_advisories,
    )


def _binding_from_document(record: Any, *, role: str, allow_effort: bool) -> HarnessBinding:
    if not isinstance(record, Mapping):
        raise CatalogError(f"{role} must be an object with kind and model.")
    if "kind" not in record or "model" not in record:
        raise CatalogError(f"{role} requires kind and model. Refusing to invent them.")
    if not allow_effort and record.get("effort"):
        raise CatalogError(
            f"{role} must not set effort; the Architect chooses worker effort per task."
        )
    model = str(record["model"]).strip()
    if not model:
        raise CatalogError(f"{role} requires a model. Refusing to invent one.")
    from herdr_master.core.catalog import normalize_effort
    from herdr_master.core.models import EffortLevel, HarnessKind

    kind = HarnessKind(get_harness(str(record["kind"])).kind)
    effort = None
    if allow_effort and record.get("effort"):
        effort = EffortLevel(normalize_effort(str(record["effort"])))
    label = record.get("label") or role
    return HarnessBinding(
        kind=kind,
        model=model,
        effort=effort,
        label=str(label),
        description=record.get("description"),
        enabled=bool(record.get("enabled", True)),
        order=int(record.get("order", 0)),
    )


def resolve_frozen_squad(document: Mapping[str, Any]) -> ResolvedSquad:
    """Validate a complete snapshot document without merging project or product defaults."""
    if not isinstance(document, Mapping):
        raise CatalogError("Snapshot document must be a JSON object.")
    if "schema_version" not in document:
        raise CatalogError("Snapshot document requires schema_version.")
    if document["schema_version"] != SCHEMA_VERSION:
        raise CatalogError(
            f"Unsupported schema_version {document['schema_version']!r}. Expected {SCHEMA_VERSION}."
        )
    if "mode" not in document:
        raise CatalogError("Snapshot document requires mode.")
    if "native_harness" not in document:
        raise CatalogError("Snapshot document requires native_harness (use null for Herdr mode).")
    squad_doc = document.get("squad")
    if not isinstance(squad_doc, Mapping):
        raise CatalogError("Snapshot document requires a complete squad object.")
    for key in ("scout", "architect", "reviewer", "worker_pool"):
        if key not in squad_doc:
            raise CatalogError(
                f"Frozen squad is missing '{key}'. "
                "Refusing to inherit product defaults."
            )
    if not isinstance(squad_doc["worker_pool"], list):
        raise CatalogError(
            "Frozen worker_pool must be a list. Refusing to inherit product workers."
        )
    mode_key = validate_mode(str(document["mode"]))
    native = document["native_harness"]
    if native is not None and not isinstance(native, str):
        raise CatalogError("native_harness must be a string or null.")
    if native == "":
        native = None
    if mode_key == "herdr" and native:
        raise CatalogError(
            "Herdr mode snapshot must set native_harness to null. "
            "A native host is only valid when mode is native."
        )
    if mode_key == "native" and not native:
        raise CatalogError("Native mode snapshot requires native_harness.")
    squad = Squad(
        scout=_binding_from_document(squad_doc["scout"], role="scout", allow_effort=True),
        architect=_binding_from_document(
            squad_doc["architect"], role="architect", allow_effort=True
        ),
        reviewer=_binding_from_document(squad_doc["reviewer"], role="reviewer", allow_effort=True),
        worker_pool=[
            _binding_from_document(row, role=f"worker-{idx + 1}", allow_effort=False)
            for idx, row in enumerate(squad_doc["worker_pool"])
        ],
    )
    sources = {key: "frozen_input" for key in _SOURCE_KEYS}
    return _finalize(squad, sources, mode_key, native)


def describe_agent(
    binding: HarnessBinding,
    *,
    mode: LaunchMode,
    role: str,
    native_harness: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a Herdr argv descriptor or a native tool descriptor for one binding."""
    mode_key = validate_mode(mode)
    effort = binding.effort.value if binding.effort else None
    if mode_key == "native":
        if not native_harness:
            raise CatalogError(
                "Native mode requires an explicit native host (--harness). "
                "There is no native default squad."
            )
        return describe_native_agent(
            binding.kind.value,
            binding.model,
            effort,
            role,
            native_harness,
        )
    if mode_key == "core":
        _ensure_core_kind(binding.kind.value)
        desc = describe_herdr_agent(binding.kind.value, binding.model, effort, role)
        if desc["role"] == "watcher":
            raise CatalogError("Core watcher descriptors are not supported; use hc wait directly.")
        desc["mode"] = "core"
        return desc
    return describe_herdr_agent(binding.kind.value, binding.model, effort, role)


def _role_meta(binding: HarnessBinding) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "label": binding.label or "",
        "enabled": binding.enabled,
        "order": binding.order,
    }
    if binding.description:
        meta["description"] = binding.description
    return meta


def load_run_squad(path: Path) -> Squad:
    """Load a run squad from squad.toml. Omitted roles inherit the product squad."""
    if not path.is_file():
        raise FileNotFoundError(f"Squad configuration file not found: {path}")

    with open(path, "rb") as handle:
        data = tomllib.load(handle)

    product = Squad()
    roles = data.get("roles") or {}
    meta = data.get("role_meta") or {}
    if not isinstance(roles, dict):
        roles = {}
    if not isinstance(meta, dict):
        meta = {}

    def role_binding(name: str, product_binding: HarnessBinding) -> HarnessBinding:
        row = meta.get(name) if isinstance(meta.get(name), dict) else {}
        if name not in roles or not roles.get(name):
            return product_binding
        return HarnessBinding.from_slug(
            str(roles[name]),
            label=(row or {}).get("label") or name,
            description=(row or {}).get("description") or None,
            enabled=bool((row or {}).get("enabled", True)),
            order=int((row or {}).get("order", 0)),
        )

    scout = role_binding("scout", product.scout)
    architect = role_binding("architect", product.architect)
    reviewer = role_binding("reviewer", product.reviewer)
    pool = product.worker_pool if "worker_pool" not in data else _pool_from_raw(data["worker_pool"])
    return Squad(scout=scout, architect=architect, reviewer=reviewer, worker_pool=pool)


def save_run_squad(
    squad: Squad,
    path: Path,
    *,
    mode: LaunchMode = "herdr",
    native_harness: Optional[str] = None,
    worker_mode: str = "balanced",
) -> Path:
    """Save the ordered run squad to squad.toml.

    ``mode`` and ``native_harness`` are keyword-only. Older callers that pass
    only the squad and path keep working; missing resolution metadata means Herdr.
    """
    mode_key = validate_mode(mode)
    host = ""
    if native_harness:
        host = get_harness(native_harness).kind
        if mode_key == "herdr":
            raise CatalogError(
                "Herdr snapshots must not set a native host. "
                "Omit native_harness or use native mode."
            )
    doc: Dict[str, Any] = {
        "roles": {
            "scout": squad.scout.to_slug(),
            "architect": squad.architect.to_slug(),
            "reviewer": squad.reviewer.to_slug(),
        },
        "role_meta": {
            "scout": _role_meta(squad.scout),
            "architect": _role_meta(squad.architect),
            "reviewer": _role_meta(squad.reviewer),
        },
        "worker_pool": [
            {
                "binding": worker.to_slug(include_effort=False),
                "label": worker.label or "",
                "description": worker.description or "",
                "enabled": worker.enabled,
                "order": worker.order,
            }
            for worker in squad.worker_pool
        ],
        "resolution": {
            "mode": mode_key,
            "native_harness": host,
            "worker_mode": validate_routing_profile(worker_mode),
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(tomli_w.dumps(doc))
    return path


def save_squad_as_default(squad: Squad, project_config_path: Optional[Path] = None) -> Path:
    """Copy the squad back into .herdr-master.toml as the project default."""
    cfg = load_project_config(project_config_path)
    updated_cfg = squad_to_project_config(squad, existing_config=cfg)
    return save_project_config(updated_cfg, project_config_path)
