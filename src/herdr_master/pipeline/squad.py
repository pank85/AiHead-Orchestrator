"""Squad configuration lifecycle, validation, serialization, and prompt formatting."""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from herdr_master.core.config import (
    load_project_config,
    save_project_config,
    squad_to_project_config,
)
from herdr_master.core.models import HarnessBinding, Squad


class SquadValidationError(ValueError):
    """Raised when squad configuration violates invariants."""

    pass


def validate_squad(squad: Squad) -> List[str]:
    """Validate squad configuration and return a list of error messages.

    Checks:
    - Worker pool is non-empty.
    - No duplicate (kind, model, effort) entries in the worker pool.
    - Scout, Architect, and Reviewer are valid bindings.
    """
    errors: List[str] = []

    if not squad.worker_pool:
        errors.append("Worker pool cannot be empty: at least one worker model is required.")

    seen_bindings = set()
    for idx, worker in enumerate(squad.worker_pool):
        key = (worker.kind, worker.model, worker.effort)
        if key in seen_bindings:
            errors.append(f"Duplicate worker model binding at row {idx + 1}: '{worker.to_slug()}'")
        seen_bindings.add(key)

    return errors


def format_allowed_coder_models(squad: Squad) -> str:
    """Format the ordered worker pool block to paste into the Architect prompt."""
    lines: List[str] = ["ALLOWED CODER MODELS:"]
    for worker in squad.worker_pool:
        if not worker.enabled:
            continue
        slug = worker.to_slug()
        hint = f" ({worker.label})" if worker.label else ""
        lines.append(f"- {slug}{hint}")
    return "\n".join(lines)


def load_run_squad(path: Path) -> Squad:
    """Load a run squad from squad.toml."""
    if not path.is_file():
        raise FileNotFoundError(f"Squad configuration file not found: {path}")

    with open(path, "rb") as f:
        data = tomllib.load(f)

    roles = data.get("roles", {})
    scout_slug = roles.get("scout", "agy:gemini-3.8-flash-high")
    architect_slug = roles.get("architect", "pi:openai-codex/gpt-6.1-sol:high")
    reviewer_slug = roles.get("reviewer", "agy:gemini-4-argon-high")

    scout = HarnessBinding.from_slug(scout_slug, label="scout")
    architect = HarnessBinding.from_slug(architect_slug, label="architect")
    reviewer = HarnessBinding.from_slug(reviewer_slug, label="reviewer")

    raw_pool = data.get("worker_pool", [])
    pool: List[HarnessBinding] = []

    if isinstance(raw_pool, list):
        for idx, row in enumerate(raw_pool):
            if isinstance(row, dict):
                binding_str = row.get("binding") or row.get("slug")
                if not binding_str:
                    continue
                label = row.get("label")
                enabled = row.get("enabled", True)
                order = row.get("order", idx)
                binding = HarnessBinding.from_slug(
                    binding_str, label=label, enabled=enabled, order=order
                )
                pool.append(binding)
            elif isinstance(row, str):
                pool.append(HarnessBinding.from_slug(row, order=idx))
    elif isinstance(raw_pool, dict):
        for idx, (label, slug) in enumerate(raw_pool.items()):
            pool.append(HarnessBinding.from_slug(slug, label=label, order=idx))

    return Squad(
        scout=scout,
        architect=architect,
        reviewer=reviewer,
        worker_pool=pool,
    )


def save_run_squad(squad: Squad, path: Path) -> Path:
    """Save the ordered run squad to squad.toml."""
    doc: Dict[str, Any] = {
        "roles": {
            "scout": squad.scout.to_slug(),
            "architect": squad.architect.to_slug(),
            "reviewer": squad.reviewer.to_slug(),
        },
        "worker_pool": [
            {
                "binding": worker.to_slug(),
                "label": worker.label or "",
                "enabled": worker.enabled,
                "order": worker.order,
            }
            for worker in squad.worker_pool
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    toml_str = tomli_w.dumps(doc)
    with open(path, "w", encoding="utf-8") as f:
        f.write(toml_str)

    return path


def save_squad_as_default(squad: Squad, project_config_path: Optional[Path] = None) -> Path:
    """Copy the squad back into .herdr-master.toml as the project default."""
    cfg = load_project_config(project_config_path)
    updated_cfg = squad_to_project_config(squad, existing_config=cfg)
    return save_project_config(updated_cfg, project_config_path)
