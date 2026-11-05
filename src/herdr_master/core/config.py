"""Configuration schemas, loading, and saving for .herdr-master.toml."""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from herdr_master.core.models import HarnessBinding, Squad


class GeneralConfig(BaseModel):
    default_session: str = "default"
    target_integration_branch: str = "develop"
    create_develop_if_missing: bool = True
    disallow_hard_reset: bool = True


class WorktreeConfig(BaseModel):
    base_directory: str = ".herdr-worktrees"
    naming_template: str = "herdm/{job_name}-{timestamp}"
    auto_clean_on_success: bool = True


class ModelsConfig(BaseModel):
    """Explicit project model overrides.

    None means the field is absent and inherits the catalog product squad.
    An empty worker_pool mapping is an explicit empty pool, not a missing one.
    Pydantic must not populate these with the product squad, or absent files
    become indistinguishable from explicit overrides.
    """

    default_squad: Optional[Dict[str, str]] = None
    worker_pool: Optional[Dict[str, Any]] = None


class TUIConfig(BaseModel):
    refresh_rate_ms: int = 250
    theme: str = "dark"
    stream_buffer_lines: int = 1000


class HerdrMasterConfig(BaseModel):
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    tui: TUIConfig = Field(default_factory=TUIConfig)


def find_project_config_path(start_path: Optional[Path] = None) -> Optional[Path]:
    """Search upwards from start_path (or cwd) for .herdr-master.toml."""
    current = (start_path or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".herdr-master.toml"
        if candidate.is_file():
            return candidate
    return None


def load_project_config(path: Optional[Path] = None) -> HerdrMasterConfig:
    """Load HerdrMasterConfig from path or by searching upwards for .herdr-master.toml."""
    config_path = path or find_project_config_path()
    if config_path is None or not config_path.is_file():
        return HerdrMasterConfig()

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    return HerdrMasterConfig.model_validate(data)


def save_project_config(config: HerdrMasterConfig, path: Optional[Path] = None) -> Path:
    """Save HerdrMasterConfig to TOML format."""
    target_path = path or find_project_config_path() or (Path.cwd() / ".herdr-master.toml")
    dumped = config.model_dump(exclude_none=True)
    toml_str = tomli_w.dumps(dumped)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(toml_str)

    return target_path


def bindings_from_worker_mapping(worker_pool: Dict[str, Any]) -> List[HarnessBinding]:
    """Parse an explicit project worker table. Effort is cleared."""
    pool: List[HarnessBinding] = []
    for order, (label, val) in enumerate(worker_pool.items()):
        if isinstance(val, dict):
            slug_str = val.get("binding") or val.get("slug") or ""
            binding = HarnessBinding.from_slug(
                slug_str,
                label=label,
                description=val.get("description"),
                enabled=bool(val.get("enabled", True)),
                order=int(val.get("order", order)),
            )
        else:
            binding = HarnessBinding.from_slug(str(val), label=label, order=order)
        binding.effort = None
        pool.append(binding)
    return pool


def project_model_overrides(config: HerdrMasterConfig) -> Dict[str, Any]:
    """Return only fields present in the project file.

    ``worker_pool`` is None when the project did not set one. An empty list means
    the project explicitly configured an empty pool.
    """
    roles: Dict[str, str] = {}
    raw_roles = config.models.default_squad or {}
    for key in ("scout", "architect", "reviewer"):
        value = raw_roles.get(key)
        if value:
            roles[key] = value
    workers: Optional[List[HarnessBinding]] = None
    if config.models.worker_pool is not None:
        workers = bindings_from_worker_mapping(config.models.worker_pool)
    return {"roles": roles, "worker_pool": workers}


def squad_from_project_config(config: HerdrMasterConfig) -> Squad:
    """Build a Squad from explicit project fields, inheriting the catalog otherwise."""
    product = Squad()
    explicit = project_model_overrides(config)
    scout = product.scout
    architect = product.architect
    reviewer = product.reviewer
    if "scout" in explicit["roles"]:
        scout = HarnessBinding.from_slug(explicit["roles"]["scout"], label="scout")
    if "architect" in explicit["roles"]:
        architect = HarnessBinding.from_slug(explicit["roles"]["architect"], label="architect")
    if "reviewer" in explicit["roles"]:
        reviewer = HarnessBinding.from_slug(explicit["roles"]["reviewer"], label="reviewer")
    pool = product.worker_pool if explicit["worker_pool"] is None else explicit["worker_pool"]
    return Squad(
        scout=scout,
        architect=architect,
        reviewer=reviewer,
        worker_pool=pool,
    )


def squad_to_project_config(
    squad: Squad,
    existing_config: Optional[HerdrMasterConfig] = None,
) -> HerdrMasterConfig:
    """Export a Squad into HerdrMasterConfig models."""
    cfg = existing_config or HerdrMasterConfig()
    if cfg.models.default_squad is None:
        cfg.models.default_squad = {}
    cfg.models.default_squad["scout"] = squad.scout.to_slug()
    cfg.models.default_squad["architect"] = squad.architect.to_slug()
    cfg.models.default_squad["reviewer"] = squad.reviewer.to_slug()

    pool_dict: Dict[str, Any] = {}
    for idx, worker in enumerate(squad.worker_pool):
        key = worker.label or f"worker-{idx + 1}"
        entry: Dict[str, Any] = {
            "binding": worker.to_slug(include_effort=False),
            "enabled": bool(worker.enabled),
        }
        if worker.description:
            entry["description"] = worker.description
        pool_dict[key] = entry

    cfg.models.worker_pool = pool_dict
    return cfg
