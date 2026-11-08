"""Configuration schemas, loading, and saving for .herdr-master.toml."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from herdr_master.core.catalog import (
    CatalogError,
    HarnessKind,
    ensure_herdr_routable,
    get_harness,
)
from herdr_master.core.models import (
    EffortLevel,
    HarnessBinding,
    Squad,
    _catalog_binding,
)


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    default_session: str = "default"
    target_integration_branch: str = "develop"
    create_develop_if_missing: bool = True
    disallow_hard_reset: bool = True


class WorktreeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

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

    model_config = ConfigDict(extra="allow")

    default_squad: Optional[Dict[str, str]] = None
    worker_pool: Optional[Dict[str, Any]] = None


class TUIConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    refresh_rate_ms: int = 250
    theme: str = "dark"
    stream_buffer_lines: int = 1000


class FavoriteProject(BaseModel):
    """A favorite project location with a custom name and directory path."""

    model_config = ConfigDict(extra="allow")

    name: str
    path: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("Favorite project name must not be blank.")
        return s

    @field_validator("path")
    @classmethod
    def _validate_path(cls, v: Any) -> str:
        s = str(v).strip()
        if not s:
            raise ValueError("Favorite project path must not be blank.")
        return s


ProjectFavorite = FavoriteProject


class ProjectsConfig(BaseModel):
    """Project and folder defaults, including named favorite folders."""

    model_config = ConfigDict(extra="allow")

    default_folder: Optional[str] = None
    favorites: List[FavoriteProject] = Field(default_factory=list)

    @field_validator("default_folder")
    @classmethod
    def _validate_default_folder(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    @field_validator("favorites")
    @classmethod
    def _validate_unique_favorites(
        cls, favorites: List[FavoriteProject]
    ) -> List[FavoriteProject]:
        seen: set[str] = set()
        for fav in favorites:
            key = fav.name.strip().lower()
            if key in seen:
                raise ValueError(
                    f"Duplicate favorite project name '{fav.name}'. Favorite names must be unique."
                )
            seen.add(key)
        return favorites


DEFAULT_ORCHESTRATION_MODELS: Dict[str, Dict[str, str]] = {
    "agy": {
        "scout": "flash_lite",
        "architect": "pro",
        "coder": "flash",
        "reviewer": "pro",
    },
    "claude": {
        "scout": "claude-sonnet-4-6",
        "architect": "claude-opus-4-6",
        "coder": "claude-sonnet-4-6",
        "reviewer": "claude-opus-4-6",
    },
    "pi": {
        "scout": "anthropic/claude-3-5-sonnet",
        "architect": "openai-codex/gpt-6.1-sol",
        "coder": "anthropic/claude-3-5-sonnet",
        "reviewer": "xai/grok-4.7",
    },
    "opencode": {
        "scout": "general",
        "architect": "general",
        "coder": "general",
        "reviewer": "general",
    },
}


ORCHESTRATION_EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")


class HarnessOrchestrationSettings(BaseModel):
    """Configuration for single-harness orchestration role models and efforts."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    scout_model: Optional[str] = None
    architect_model: Optional[str] = None
    worker_models: List[str] = Field(default_factory=list)
    reviewer_model: Optional[str] = None
    scout_effort: Optional[str] = None
    architect_effort: Optional[str] = None
    reviewer_effort: Optional[str] = None

    @field_validator(
        "scout_effort", "architect_effort", "reviewer_effort", mode="before"
    )
    @classmethod
    def _validate_role_effort(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if hasattr(v, "value"):
            v = v.value
        text = str(v).strip().lower()
        if not text or text in ("none", "default"):
            return None
        if text not in ORCHESTRATION_EFFORT_VALUES:
            raise ValueError(
                f"Unsupported effort '{v}'. Expected one of: "
                + ", ".join(ORCHESTRATION_EFFORT_VALUES)
                + "."
            )
        return text

    @property
    def scout(self) -> Optional[str]:
        return self.scout_model

    @scout.setter
    def scout(self, v: Optional[str]) -> None:
        self.scout_model = v

    @property
    def architect(self) -> Optional[str]:
        return self.architect_model

    @architect.setter
    def architect(self, v: Optional[str]) -> None:
        self.architect_model = v

    @property
    def reviewer(self) -> Optional[str]:
        return self.reviewer_model

    @reviewer.setter
    def reviewer(self, v: Optional[str]) -> None:
        self.reviewer_model = v

    @property
    def workers(self) -> List[str]:
        return self.worker_models

    @workers.setter
    def workers(self, v: List[str]) -> None:
        self.worker_models = v

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "scout" in d and "scout_model" not in d:
            d["scout_model"] = d.pop("scout")
        if "architect" in d and "architect_model" not in d:
            d["architect_model"] = d.pop("architect")
        if "reviewer" in d and "reviewer_model" not in d:
            d["reviewer_model"] = d.pop("reviewer")
        if "workers" in d and "worker_models" not in d:
            d["worker_models"] = d.pop("workers")
        elif "coder_model" in d and "worker_models" not in d:
            raw = d.pop("coder_model")
            if isinstance(raw, str):
                d["worker_models"] = [p.strip() for p in raw.split("|") if p.strip()]
            elif isinstance(raw, list):
                d["worker_models"] = raw
        elif "coders" in d and "worker_models" not in d:
            d["worker_models"] = d.pop("coders")
        return d

    @field_validator("worker_models")
    @classmethod
    def _validate_worker_models(cls, v: Any) -> List[str]:
        if not isinstance(v, list):
            raise ValueError(
                "Worker models must be a list of model identifier strings."
            )
        validated: List[str] = []
        for item in v:
            s = str(item).strip()
            if not s:
                continue
            if ":" in s:
                parts = s.split(":")
                if len(parts) >= 2 and parts[-1].lower() in (
                    "low",
                    "medium",
                    "high",
                    "max",
                    "xhigh",
                ):
                    raise ValueError(
                        f"Worker model '{s}' must not specify an effort level (the Architect decides worker effort)."
                    )
            validated.append(s)
        return validated

    def effective_scout_model(self, harness: str = "agy") -> str:
        if self.scout_model:
            return self.scout_model
        return DEFAULT_ORCHESTRATION_MODELS.get(harness, {}).get("scout", "general")

    def effective_architect_model(self, harness: str = "agy") -> str:
        if self.architect_model:
            return self.architect_model
        return DEFAULT_ORCHESTRATION_MODELS.get(harness, {}).get("architect", "general")

    def effective_reviewer_model(self, harness: str = "agy") -> str:
        if self.reviewer_model:
            return self.reviewer_model
        return DEFAULT_ORCHESTRATION_MODELS.get(harness, {}).get("reviewer", "general")

    def effective_worker_models(self, harness: str = "agy") -> List[str]:
        if self.worker_models:
            return list(self.worker_models)
        coder = DEFAULT_ORCHESTRATION_MODELS.get(harness, {}).get("coder", "general")
        return [coder]


class OrchestrationConfig(BaseModel):
    """Configuration for single-harness native orchestration pipelines."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    harness: HarnessKind = HarnessKind.AGY
    default_harness: Optional[HarnessKind] = None
    scout_model: Optional[str] = None
    architect_model: Optional[str] = None
    worker_models: List[str] = Field(default_factory=list)
    reviewer_model: Optional[str] = None
    # Per-role reasoning effort (optional). Workers deliberately have none:
    # the Architect decides worker effort.
    scout_effort: Optional[str] = None
    architect_effort: Optional[str] = None
    reviewer_effort: Optional[str] = None
    harnesses: Dict[str, HarnessOrchestrationSettings] = Field(default_factory=dict)

    @field_validator(
        "scout_effort", "architect_effort", "reviewer_effort", mode="before"
    )
    @classmethod
    def _validate_role_effort(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if hasattr(v, "value"):
            v = v.value
        text = str(v).strip().lower()
        if not text or text in ("none", "default"):
            return None
        if text not in ORCHESTRATION_EFFORT_VALUES:
            raise ValueError(
                f"Unsupported effort '{v}'. Expected one of: "
                + ", ".join(ORCHESTRATION_EFFORT_VALUES)
                + "."
            )
        return text

    @property
    def scout(self) -> Optional[str]:
        return self.scout_model

    @scout.setter
    def scout(self, v: Optional[str]) -> None:
        self.scout_model = v

    @property
    def architect(self) -> Optional[str]:
        return self.architect_model

    @architect.setter
    def architect(self, v: Optional[str]) -> None:
        self.architect_model = v

    @property
    def reviewer(self) -> Optional[str]:
        return self.reviewer_model

    @reviewer.setter
    def reviewer(self, v: Optional[str]) -> None:
        self.reviewer_model = v

    @property
    def workers(self) -> List[str]:
        return self.worker_models

    @workers.setter
    def workers(self, v: List[str]) -> None:
        self.worker_models = v

    @property
    def effective_default_harness(self) -> HarnessKind:
        return self.default_harness or self.harness

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if "default_harness" in d and "harness" not in d:
            d["harness"] = d["default_harness"]
        elif "harness" in d and "default_harness" not in d:
            d["default_harness"] = d["harness"]

        if "per_harness" in d and "harnesses" not in d:
            d["harnesses"] = d.pop("per_harness")
        elif "profiles" in d and "harnesses" not in d:
            d["harnesses"] = d.pop("profiles")

        if "scout" in d and "scout_model" not in d:
            d["scout_model"] = d.pop("scout")
        if "architect" in d and "architect_model" not in d:
            d["architect_model"] = d.pop("architect")
        if "reviewer" in d and "reviewer_model" not in d:
            d["reviewer_model"] = d.pop("reviewer")
        if "workers" in d and "worker_models" not in d:
            d["worker_models"] = d.pop("workers")
        elif "coder_model" in d and "worker_models" not in d:
            raw = d.pop("coder_model")
            if isinstance(raw, str):
                d["worker_models"] = [p.strip() for p in raw.split("|") if p.strip()]
            elif isinstance(raw, list):
                d["worker_models"] = raw
        elif "coders" in d and "worker_models" not in d:
            d["worker_models"] = d.pop("coders")
        return d

    @field_validator("harness", "default_harness", mode="before")
    @classmethod
    def _validate_harness(cls, v: Any) -> Any:
        if v is None:
            return None
        kind = HarnessKind(v) if isinstance(v, str) else v
        spec = get_harness(kind.value)
        if not spec.supports_native:
            raise ValueError(
                f"Harness '{kind.value}' does not support native orchestration. "
                "Only native-capable harnesses (agy, pi, claude, opencode) may be selected."
            )
        return kind

    @field_validator("worker_models")
    @classmethod
    def _validate_worker_models(cls, v: Any) -> List[str]:
        if not isinstance(v, list):
            raise ValueError(
                "Worker models must be a list of model identifier strings."
            )
        validated: List[str] = []
        for item in v:
            s = str(item).strip()
            if not s:
                continue
            if ":" in s:
                parts = s.split(":")
                if len(parts) >= 2 and parts[-1].lower() in (
                    "low",
                    "medium",
                    "high",
                    "max",
                    "xhigh",
                ):
                    raise ValueError(
                        f"Worker model '{s}' must not specify an effort level (the Architect decides worker effort)."
                    )
            validated.append(s)
        return validated

    def model_post_init(self, __context: Any) -> None:
        if self.default_harness is None:
            self.default_harness = self.harness

        active_key = self.harness.value
        if active_key not in self.harnesses:
            if (
                self.scout_model
                or self.architect_model
                or self.worker_models
                or self.reviewer_model
                or self.scout_effort
                or self.architect_effort
                or self.reviewer_effort
            ):
                self.sync_active_to_harnesses()
        else:
            active_settings = self.harnesses[active_key]
            if not self.scout_model and active_settings.scout_model:
                self.scout_model = active_settings.scout_model
            if not self.architect_model and active_settings.architect_model:
                self.architect_model = active_settings.architect_model
            if not self.worker_models and active_settings.worker_models:
                self.worker_models = list(active_settings.worker_models)
            if not self.reviewer_model and active_settings.reviewer_model:
                self.reviewer_model = active_settings.reviewer_model
            if not self.scout_effort and active_settings.scout_effort:
                self.scout_effort = active_settings.scout_effort
            if not self.architect_effort and active_settings.architect_effort:
                self.architect_effort = active_settings.architect_effort
            if not self.reviewer_effort and active_settings.reviewer_effort:
                self.reviewer_effort = active_settings.reviewer_effort

    @model_serializer(mode="wrap")
    def _serialize(self, handler: Any) -> Any:
        self.sync_active_to_harnesses()
        if self.default_harness is None:
            self.default_harness = self.harness
        return handler(self)

    def sync_active_to_harnesses(self) -> None:
        """Sync top-level flat fields into the per-harness entry for the active harness."""
        active_key = self.harness.value
        self.harnesses[active_key] = HarnessOrchestrationSettings(
            scout_model=self.scout_model,
            architect_model=self.architect_model,
            worker_models=list(self.worker_models),
            reviewer_model=self.reviewer_model,
            scout_effort=self.scout_effort,
            architect_effort=self.architect_effort,
            reviewer_effort=self.reviewer_effort,
        )

    def sync_harness_to_active(
        self, harness: Optional[Union[HarnessKind, str]] = None
    ) -> None:
        """Sync per-harness settings for harness (or current active) into top-level flat fields."""
        if harness is not None:
            self.harness = HarnessKind(harness) if isinstance(harness, str) else harness
        active_key = self.harness.value
        settings = self.harnesses.get(active_key)
        if settings is not None:
            self.scout_model = settings.scout_model
            self.architect_model = settings.architect_model
            self.worker_models = list(settings.worker_models)
            self.reviewer_model = settings.reviewer_model
            self.scout_effort = settings.scout_effort
            self.architect_effort = settings.architect_effort
            self.reviewer_effort = settings.reviewer_effort

    def for_harness(
        self, harness: Union[HarnessKind, str]
    ) -> HarnessOrchestrationSettings:
        """Get the configuration settings for a specific harness, returning defaults if not set."""
        h_str = (
            harness.value
            if isinstance(harness, HarnessKind)
            else str(harness).strip().lower()
        )
        if h_str == self.harness.value:
            self.sync_active_to_harnesses()
        if h_str in self.harnesses:
            return self.harnesses[h_str]
        defaults = DEFAULT_ORCHESTRATION_MODELS.get(h_str, {})
        coder = defaults.get("coder", "general")
        return HarnessOrchestrationSettings(
            scout_model=defaults.get("scout"),
            architect_model=defaults.get("architect"),
            worker_models=[coder] if coder else [],
            reviewer_model=defaults.get("reviewer"),
        )

    def set_harness_settings(
        self,
        harness: Union[HarnessKind, str],
        settings: Union[HarnessOrchestrationSettings, Dict[str, Any]],
    ) -> None:
        """Store configuration settings for a specific harness."""
        h_str = (
            harness.value
            if isinstance(harness, HarnessKind)
            else str(harness).strip().lower()
        )
        if isinstance(settings, dict):
            settings = HarnessOrchestrationSettings.model_validate(settings)
        self.harnesses[h_str] = settings
        if h_str == self.harness.value:
            self.sync_harness_to_active(h_str)

    def switch_harness(
        self,
        new_harness: Union[HarnessKind, str],
        *,
        set_as_default: bool = False,
    ) -> None:
        """Switch active harness, preserving current harness settings and restoring target harness settings."""
        h_kind = (
            HarnessKind(new_harness) if isinstance(new_harness, str) else new_harness
        )
        self.sync_active_to_harnesses()
        self.harness = h_kind
        if set_as_default:
            self.default_harness = h_kind
        h_str = h_kind.value
        if h_str in self.harnesses:
            self.sync_harness_to_active(h_str)
        else:
            defaults = DEFAULT_ORCHESTRATION_MODELS.get(h_str, {})
            coder = defaults.get("coder", "general")
            new_settings = HarnessOrchestrationSettings(
                scout_model=defaults.get("scout"),
                architect_model=defaults.get("architect"),
                worker_models=[coder] if coder else [],
                reviewer_model=defaults.get("reviewer"),
                scout_effort=None,
                architect_effort=None,
                reviewer_effort=None,
            )
            self.harnesses[h_str] = new_settings
            self.sync_harness_to_active(h_str)

    def effective_scout_model(
        self, harness: Optional[Union[HarnessKind, str]] = None
    ) -> str:
        h = (
            harness.value
            if isinstance(harness, HarnessKind)
            else (str(harness).strip().lower() if harness else self.harness.value)
        )
        if h == self.harness.value and self.scout_model:
            return self.scout_model
        if h in self.harnesses and self.harnesses[h].scout_model:
            return self.harnesses[h].scout_model
        return DEFAULT_ORCHESTRATION_MODELS.get(h, {}).get("scout", "general")

    def effective_architect_model(
        self, harness: Optional[Union[HarnessKind, str]] = None
    ) -> str:
        h = (
            harness.value
            if isinstance(harness, HarnessKind)
            else (str(harness).strip().lower() if harness else self.harness.value)
        )
        if h == self.harness.value and self.architect_model:
            return self.architect_model
        if h in self.harnesses and self.harnesses[h].architect_model:
            return self.harnesses[h].architect_model
        return DEFAULT_ORCHESTRATION_MODELS.get(h, {}).get("architect", "general")

    def effective_reviewer_model(
        self, harness: Optional[Union[HarnessKind, str]] = None
    ) -> str:
        h = (
            harness.value
            if isinstance(harness, HarnessKind)
            else (str(harness).strip().lower() if harness else self.harness.value)
        )
        if h == self.harness.value and self.reviewer_model:
            return self.reviewer_model
        if h in self.harnesses and self.harnesses[h].reviewer_model:
            return self.harnesses[h].reviewer_model
        return DEFAULT_ORCHESTRATION_MODELS.get(h, {}).get("reviewer", "general")

    def effective_worker_models(
        self, harness: Optional[Union[HarnessKind, str]] = None
    ) -> List[str]:
        h = (
            harness.value
            if isinstance(harness, HarnessKind)
            else (str(harness).strip().lower() if harness else self.harness.value)
        )
        if h == self.harness.value and self.worker_models:
            return list(self.worker_models)
        if h in self.harnesses and self.harnesses[h].worker_models:
            return list(self.harnesses[h].worker_models)
        coder = DEFAULT_ORCHESTRATION_MODELS.get(h, {}).get("coder", "general")
        return [coder]


def _validate_council_binding(binding: HarnessBinding, role: str) -> None:
    kind_str = (
        binding.kind.value
        if isinstance(binding.kind, HarnessKind)
        else str(binding.kind)
    )
    effort_str = (
        binding.effort.value
        if isinstance(binding.effort, EffortLevel)
        else (str(binding.effort) if binding.effort else None)
    )
    ensure_herdr_routable(
        kind_str, binding.model, effort_str, role=role, require_effort=False
    )


class CouncilMember(HarnessBinding):
    """An identified member of the AI Council with a stable unique identity."""

    @classmethod
    def from_slug(
        cls,
        slug_str: str,
        default_effort: Optional[EffortLevel] = None,
        label: Optional[str] = None,
        description: Optional[str] = None,
        enabled: bool = True,
        order: int = 0,
    ) -> "CouncilMember":
        b = super().from_slug(
            slug_str,
            default_effort=default_effort,
            label=label,
            description=description,
            enabled=enabled,
            order=order,
        )
        return cls(**b.model_dump())

    @property
    def member_id(self) -> str:
        return self.label or ""

    @property
    def name(self) -> str:
        return self.label or ""


def default_council_coordinator() -> HarnessBinding:
    """Coordinator binding derived from product catalog architect."""
    b = _catalog_binding("architect")
    b.label = "coordinator"
    b.description = "Council Coordinator — orchestrates rounds and consensus"
    return b


def default_council_scout() -> HarnessBinding:
    """Scout binding derived from product catalog scout."""
    b = _catalog_binding("scout")
    b.label = "scout"
    b.description = "Council Scout — surveys problem space and context"
    return b


def default_council_members() -> List[CouncilMember]:
    """Two separately identified members derived from catalog bindings."""
    m1_raw = _catalog_binding("architect")
    m1 = CouncilMember(
        kind=m1_raw.kind,
        model=m1_raw.model,
        effort=m1_raw.effort,
        label="council-1",
        description="Council Member 1 — plan drafter and reviewer",
        enabled=True,
        order=0,
    )
    m2_raw = _catalog_binding("reviewer")
    m2 = CouncilMember(
        kind=m2_raw.kind,
        model=m2_raw.model,
        effort=m2_raw.effort,
        label="council-2",
        description="Council Member 2 — plan drafter and reviewer",
        enabled=True,
        order=1,
    )
    return [m1, m2]


class CouncilConfig(BaseModel):
    """Configuration for AI Council deliberation rounds."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    coordinator: HarnessBinding = Field(default_factory=default_council_coordinator)
    scout: HarnessBinding = Field(default_factory=default_council_scout)
    members: List[CouncilMember] = Field(default_factory=default_council_members)

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if isinstance(d.get("coordinator"), str):
            d["coordinator"] = HarnessBinding.from_slug(
                d["coordinator"], label="coordinator"
            ).model_dump()
        if isinstance(d.get("scout"), str):
            d["scout"] = HarnessBinding.from_slug(
                d["scout"], label="scout"
            ).model_dump()
        if "members" in d and isinstance(d["members"], list):
            coerced_members = []
            for idx, item in enumerate(d["members"]):
                if isinstance(item, str):
                    coerced_members.append(
                        CouncilMember.from_slug(
                            item, label=f"council-{idx + 1}", order=idx
                        )
                    )
                elif isinstance(item, CouncilMember):
                    coerced_members.append(item)
                elif isinstance(item, HarnessBinding):
                    coerced_members.append(CouncilMember(**item.model_dump()))
                elif isinstance(item, dict):
                    item_copy = dict(item)
                    if "label" not in item_copy and (
                        "name" in item_copy or "id" in item_copy
                    ):
                        item_copy["label"] = item_copy.get("name") or item_copy.get(
                            "id"
                        )
                    if "order" not in item_copy:
                        item_copy["order"] = idx
                    coerced_members.append(CouncilMember(**item_copy))
                else:
                    coerced_members.append(item)
            d["members"] = coerced_members
        return d

    @model_validator(mode="after")
    def _validate_invariants(self) -> "CouncilConfig":
        _validate_council_binding(self.coordinator, "coordinator")
        _validate_council_binding(self.scout, "scout")

        seen_identities: set[str] = set()
        for m in self.members:
            ident = (m.label or "").strip()
            if not ident:
                raise ValueError(
                    "Council members must have a non-blank identity (label)."
                )
            ident_key = ident.lower()
            if ident_key in seen_identities:
                raise ValueError(
                    f"Duplicate Council member identity '{ident}'. Member identities must be unique."
                )
            seen_identities.add(ident_key)
            _validate_council_binding(m, ident)

        enabled_members = [m for m in self.members if m.enabled]
        if len(enabled_members) < 2:
            raise ValueError(
                f"Council requires at least 2 enabled members (excluding coordinator and Scout), "
                f"but found {len(enabled_members)} enabled."
            )

        return self


DEFAULT_AI_HEAD_SOUL: str = (
    "Software Architect and Workflow Orchestrator obsessed with UX best practices, "
    "accessibility, clear interaction design, code reusability without speculative abstraction, "
    "and intelligent workflow selection across Herdr-Master capabilities "
    "(/orchestration, /ergo, /council, /herdr-master) with dynamic model and harness "
    "discovery from project configuration (models.default_squad, orchestration, catalog)"
)


def default_ai_head_binding() -> HarnessBinding:
    """AiHead binding derived from product catalog architect."""
    b = _catalog_binding("architect")
    b.label = "ai-head"
    b.description = "AiHead persona binding"
    return b


class AiHeadConfig(BaseModel):
    """Configuration for autonomous AiHead decision-maker."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    enabled: bool = False
    soul: str = DEFAULT_AI_HEAD_SOUL
    binding: HarnessBinding = Field(default_factory=default_ai_head_binding)
    mode: Optional[str] = None
    split: bool = True
    cwd: Optional[str] = None
    lesson_paths: List[str] = Field(default_factory=lambda: ["~/.herdr-master/lessons"])
    brief_before_action: bool = True
    autonomy: str = "routine"
    close_foreign_tabs: bool = False

    @property
    def folder(self) -> Optional[str]:
        return self.cwd

    @folder.setter
    def folder(self, value: Optional[str]) -> None:
        self.cwd = value

    @property
    def harness(self) -> HarnessKind:
        return self.binding.kind

    @harness.setter
    def harness(self, value: Union[HarnessKind, str]) -> None:
        kind = HarnessKind(value) if isinstance(value, str) else value
        self.binding.kind = kind

    @property
    def model(self) -> str:
        return self.binding.model

    @model.setter
    def model(self, value: str) -> None:
        self.binding.model = value

    @property
    def effort(self) -> Optional[EffortLevel]:
        return self.binding.effort

    @effort.setter
    def effort(self, value: Optional[Union[EffortLevel, str]]) -> None:
        if value is None:
            self.binding.effort = None
        elif isinstance(value, str):
            self.binding.effort = EffortLevel(value)
        else:
            self.binding.effort = value

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if isinstance(d.get("binding"), str):
            d["binding"] = HarnessBinding.from_slug(
                d["binding"], label="ai-head"
            ).model_dump()
        elif "binding" not in d and ("harness" in d or "model" in d or "kind" in d):
            base = default_ai_head_binding()
            kind_val = d.pop("harness", d.pop("kind", None))
            model_val = d.pop("model", None)
            effort_val = d.pop("effort", None)
            d["binding"] = {
                "kind": kind_val or base.kind.value,
                "model": model_val or base.model,
                "effort": (
                    effort_val
                    if effort_val is not None
                    else (base.effort.value if base.effort else None)
                ),
                "label": "ai-head",
                "description": base.description,
            }
        if "split" in d:
            val = d["split"]
            if isinstance(val, str):
                s = val.strip().lower()
                if s in ("true", "1", "yes", "on"):
                    d["split"] = True
                elif s in ("false", "0", "no", "off"):
                    d["split"] = False
            elif isinstance(val, (int, float)):
                d["split"] = bool(val)
        for key in ("folder", "working_directory", "directory", "path"):
            if key in d and "cwd" not in d:
                d["cwd"] = d.pop(key)
        if "cwd" in d:
            val = d["cwd"]
            if isinstance(val, (str, Path)):
                s = str(val).strip()
                d["cwd"] = str(Path(os.path.expanduser(s))) if s else None
            elif val is None:
                d["cwd"] = None
        return d

    @field_validator("cwd", mode="before")
    @classmethod
    def _validate_cwd(cls, v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, (str, Path)):
            s = str(v).strip()
            return str(Path(os.path.expanduser(s))) if s else None
        return str(v)

    @field_validator("split", mode="before")
    @classmethod
    def _validate_split(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in ("true", "1", "yes", "on"):
                return True
            if s in ("false", "0", "no", "off"):
                return False
            raise ValueError(f"Invalid boolean value for split: {v}")
        if isinstance(v, (int, float)):
            return bool(v)
        return bool(v)

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        s = v.strip().lower()
        if s not in ("herdr", "cli"):
            raise ValueError(
                f"Unsupported execution mode '{v}'. Expected 'herdr' or 'cli'."
            )
        return s

    @field_validator("lesson_paths", mode="before")
    @classmethod
    def _validate_lesson_paths(cls, v: Any) -> List[str]:
        if not isinstance(v, list):
            raise ValueError(
                f"lesson_paths must be a list of path strings, got {type(v).__name__}"
            )
        validated: List[str] = []
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise ValueError(
                    f"lesson_paths entry at index {i} must be a string, got {type(item).__name__}"
                )
            stripped = item.strip()
            if not stripped:
                raise ValueError(
                    f"lesson_paths entry at index {i} cannot be blank or empty"
                )
            validated.append(stripped)
        return validated

    @field_validator("autonomy")
    @classmethod
    def _validate_autonomy(cls, v: Any) -> str:
        if not isinstance(v, str):
            raise ValueError(
                f"Unsupported autonomy '{v}'. Expected 'routine' or 'ask'."
            )
        s = v.strip().lower()
        if s not in ("routine", "ask"):
            raise ValueError(
                f"Unsupported autonomy '{v}'. Expected 'routine' or 'ask'."
            )
        return s

    @model_validator(mode="after")
    def _validate_binding(self) -> "AiHeadConfig":
        kind_str = (
            self.binding.kind.value
            if isinstance(self.binding.kind, HarnessKind)
            else str(self.binding.kind)
        )
        effort_str = (
            self.binding.effort.value
            if isinstance(self.binding.effort, EffortLevel)
            else (str(self.binding.effort) if self.binding.effort else None)
        )
        ensure_herdr_routable(
            kind_str,
            self.binding.model,
            effort_str,
            role="ai-head",
            require_effort=False,
        )
        return self


class HerdrMasterConfig(BaseModel):
    """Root configuration model for .herdr-master.toml."""

    model_config = ConfigDict(extra="allow")

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    tui: TUIConfig = Field(default_factory=TUIConfig)
    projects: ProjectsConfig = Field(default_factory=ProjectsConfig)
    orchestration: OrchestrationConfig = Field(default_factory=OrchestrationConfig)
    council: CouncilConfig = Field(default_factory=CouncilConfig)
    ai_head: AiHeadConfig = Field(default_factory=AiHeadConfig)


def find_project_config_path(
    start_path: Optional[Union[str, Path]] = None,
) -> Optional[Path]:
    """Search upwards from start_path (or cwd) for .herdr-master.toml."""
    current = Path(start_path or Path.cwd()).resolve()
    if current.is_file():
        if current.name == ".herdr-master.toml":
            return current
        current = current.parent
    for parent in [current, *current.parents]:
        candidate = parent / ".herdr-master.toml"
        if candidate.is_file():
            return candidate
    return None


def load_project_config(path: Optional[Union[str, Path]] = None) -> HerdrMasterConfig:
    """Load HerdrMasterConfig from path or by searching upwards for .herdr-master.toml.

    Returns default HerdrMasterConfig when the file does not exist.
    Raises errors on malformed files to prevent overwriting user configuration.
    """
    resolved_path = Path(path).resolve() if path else None
    if resolved_path and resolved_path.is_dir():
        config_path = find_project_config_path(resolved_path)
    elif resolved_path:
        config_path = resolved_path
    else:
        config_path = find_project_config_path()

    if config_path is None or not config_path.is_file():
        return HerdrMasterConfig()

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    return HerdrMasterConfig.model_validate(data)


def save_project_config(
    config: HerdrMasterConfig,
    path: Optional[Union[str, Path]] = None,
) -> Path:
    """Save HerdrMasterConfig to TOML format atomically.

    Writes to a temporary file in the same directory and replaces the target file atomically.
    Preserves existing known settings and prevents partial/corrupt writes.
    """
    target_path = Path(
        path or find_project_config_path() or (Path.cwd() / ".herdr-master.toml")
    ).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    dumped = config.model_dump(exclude_none=True)
    toml_str = tomli_w.dumps(dumped)

    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            f.write(toml_str)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_path, target_path)
        temp_path = None
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    return target_path


def resolve_folder_path(
    folder: Union[str, Path],
    base_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Resolve a user-supplied folder path, expanding user directory and resolving relative paths."""
    p = Path(folder).expanduser()
    if not p.is_absolute():
        base = Path(base_dir or Path.cwd()).resolve()
        if base.is_file():
            base = base.parent
        p = (base / p).resolve()
    else:
        p = p.resolve()
    return p


def is_folder_available(
    folder: Union[str, Path],
    base_dir: Optional[Union[str, Path]] = None,
) -> bool:
    """Check whether a folder exists and is a directory, without raising on missing or unreadable paths."""
    try:
        resolved = resolve_folder_path(folder, base_dir)
        return resolved.is_dir()
    except (OSError, ValueError):
        return False


def resolve_effective_folder(
    explicit_folder: Optional[Union[str, Path]] = None,
    config: Optional[HerdrMasterConfig] = None,
    anchor_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Determine effective folder using strict precedence:
    explicit launch folder > configured default > cwd.

    Resolution is performed against the anchor directory before selecting a default folder.
    """
    anchor = Path(anchor_dir or Path.cwd()).resolve()
    if anchor.is_file():
        anchor = anchor.parent

    if explicit_folder is not None:
        s = str(explicit_folder).strip()
        if s:
            return resolve_folder_path(s, anchor)

    if config is not None and config.projects.default_folder:
        s = config.projects.default_folder.strip()
        if s:
            return resolve_folder_path(s, anchor)

    return anchor


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
        architect = HarnessBinding.from_slug(
            explicit["roles"]["architect"], label="architect"
        )
    if "reviewer" in explicit["roles"]:
        reviewer = HarnessBinding.from_slug(
            explicit["roles"]["reviewer"], label="reviewer"
        )
    pool = (
        product.worker_pool
        if explicit["worker_pool"] is None
        else explicit["worker_pool"]
    )
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
