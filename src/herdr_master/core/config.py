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
    default_squad: Dict[str, str] = Field(
        default_factory=lambda: {
            "scout": "agy:gemini-3.8-flash-high",
            "architect": "pi:openai-codex/gpt-6.1-sol:high",
            "reviewer": "agy:gemini-4-argon-high",
        }
    )
    worker_pool: Dict[str, Any] = Field(
        default_factory=lambda: {
            "worker-1": {
                "binding": "claude:claude-3-opus",
                "description": "Senior dev - Very capable for complex, architectural tasks",
            },
            "worker-2": {
                "binding": "agy:gemini-3.8-flash",
                "description": "Junior dev - Fast and cost-effective for boilerplate tasks",
            },
            "worker-3": {
                "binding": "pi:xai/grok-4.7",
                "description": "Mid dev - General implementation, backend, APIs, refactoring",
            },
        }
    )


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
    dumped = config.model_dump()
    toml_str = tomli_w.dumps(dumped)

    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(toml_str)

    return target_path


def squad_from_project_config(config: HerdrMasterConfig) -> Squad:
    """Build a Squad domain object from HerdrMasterConfig."""
    squad_dict = config.models.default_squad
    scout_slug = squad_dict.get("scout", "agy:gemini-3.8-flash-high")
    architect_slug = squad_dict.get("architect", "pi:openai-codex/gpt-6.1-sol:high")
    reviewer_slug = squad_dict.get("reviewer", "agy:gemini-4-argon-high")

    scout = HarnessBinding.from_slug(scout_slug, label="scout")
    architect = HarnessBinding.from_slug(architect_slug, label="architect")
    reviewer = HarnessBinding.from_slug(reviewer_slug, label="reviewer")

    pool: List[HarnessBinding] = []
    order = 0
    for label, val in config.models.worker_pool.items():
        if isinstance(val, dict):
            slug_str = val.get("binding") or val.get("slug") or ""
            desc = val.get("description")
            binding = HarnessBinding.from_slug(
                slug_str, label=label, description=desc, order=order
            )
        else:
            binding = HarnessBinding.from_slug(str(val), label=label, order=order)
        # Workers have no fixed effort; Architect decides dynamically
        binding.effort = None
        pool.append(binding)
        order += 1

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
    cfg.models.default_squad["scout"] = squad.scout.to_slug()
    cfg.models.default_squad["architect"] = squad.architect.to_slug()
    cfg.models.default_squad["reviewer"] = squad.reviewer.to_slug()

    pool_dict: Dict[str, Any] = {}
    for idx, worker in enumerate(squad.worker_pool):
        key = worker.label or f"worker-{idx + 1}"
        entry: Dict[str, Any] = {
            "binding": worker.to_slug(include_effort=False),
        }
        if worker.description:
            entry["description"] = worker.description
        pool_dict[key] = entry

    cfg.models.worker_pool = pool_dict
    return cfg
