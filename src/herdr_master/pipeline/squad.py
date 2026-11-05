"""Squad configuration lifecycle, validation, serialization, and prompt formatting."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from herdr_master.core.catalog import (
    SCHEMA_VERSION,
    CatalogError,
    LaunchMode,
    describe_herdr_agent,
    describe_native_agent,
    ensure_herdr_routable,
    get_harness,
    native_binding_error,
    qualify_binding_slug,
    split_worker_bindings,
    validate_mode,
    worker_capability,
    worker_route_note,
)
from herdr_master.core.config import (
    find_project_config_path,
    load_project_config,
    project_model_overrides,
    save_project_config,
    squad_to_project_config,
)
from herdr_master.core.models import HarnessBinding, Squad
from herdr_master.core.slug import InvalidBindingSlugError, parse_binding

_ROLE_KEYS = ("scout", "architect", "reviewer")
_SOURCE_KEYS = ("scout", "architect", "reviewer", "workers")
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


def format_allowed_coder_models(
    squad: Squad,
    *,
    mode: LaunchMode = "herdr",
    native_harness: Optional[str] = None,
) -> str:
    """Format the ordered worker pool block to paste into the Architect prompt.

    Workers do not have a hardcoded thinking effort; the Architect dynamically
    decides reasoning effort per task. Constraint lines name the supported routes.
    """
    lines: List[str] = [
        "ALLOWED CODER MODELS: (assign each task in plan.md one worker model from this list, "
        "and select reasoning effort low/medium/high/max based on task complexity):"
    ]
    ordered = sorted(enumerate(squad.worker_pool), key=lambda pair: (pair[1].order, pair[0]))
    for _, worker in ordered:
        if not worker.enabled:
            continue
        slug = worker.to_slug(include_effort=False)
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
    ) -> None:
        self.squad = squad
        self.sources = dict(sources)
        self.allowed_coder_models = allowed_coder_models
        self.mode = mode
        self.native_harness = native_harness
        self.advisories = list(advisories)
        self.roles = roles
        self.workers = workers

    def to_dict(self) -> Dict[str, Any]:
        squad = self.squad
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": self.mode,
            "native_harness": self.native_harness,
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


def _finalize(
    squad: Squad,
    sources: Dict[str, str],
    mode: str,
    native_harness: Optional[str],
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
        format_allowed_coder_models(squad, mode=mode_key, native_harness=host),
        mode=mode_key,
        native_harness=host,
        advisories=_family_advisory(squad),
        roles=role_descriptors,
        workers=worker_rows,
    )


def resolve_squad(
    project_path: Optional[Path] = None,
    *,
    mode: LaunchMode = "herdr",
    native_harness: Optional[str] = None,
    run_squad_path: Optional[Path] = None,
    overrides: Optional[Mapping[str, str]] = None,
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
    if project["worker_pool"] is not None:
        _apply_worker_bindings(squad, sources, project["worker_pool"], "project")
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
    return _finalize(squad, sources, mode_key, host)


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
