"""Single-screen interactive Python TUI for Herdr-Master & AiHead skill setup."""

from __future__ import annotations

import io
import os
import select
import shutil
import signal
import sys
import termios
import tty
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from rich.box import ROUNDED
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.text import Text

from herdr_master.cli.setup_catalog import (
    ChoiceOption,
    ModelCatalog,
    discover_harness_models,
    discover_model_catalog,
)
from herdr_master.core.available_models import (
    enabled_model_ids,
    filter_model_ids,
    is_all_enabled,
    materialize_toggle,
    materialize_toggle_all,
)
from herdr_master.core.catalog import (
    _FLASH_BASE,
    CatalogError,
    HarnessKind,
    _allowed_efforts,
    describe_herdr_agent,
    ensure_herdr_routable,
    get_harness,
    native_binding_error,
    supported_harnesses,
)
from herdr_master.core.config import (
    DEFAULT_ORCHESTRATION_MODELS,
    CouncilConfig,
    CouncilMember,
    ErgoConfig,
    HerdrMasterConfig,
    load_effective_models_state,
    load_project_config,
    save_project_config,
    squad_from_project_config,
    squad_to_project_config,
)
from herdr_master.core.model_ratings import (
    BILLING_TIERS,
    ModelEvaluationConfig,
    ModelRating,
    derive_worker_tier,
    detect_model_family,
    effective_worker_tier,
    format_context_window,
    infer_supported_efforts,
    lookup_rating,
    merge_ratings,
)
from herdr_master.core.models import EffortLevel, HarnessBinding, Squad
from herdr_master.core.slug import InvalidBindingSlugError
from herdr_master.pipeline.model_evaluator import EvaluationResult, evaluate_available_models
from herdr_master.pipeline.squad import save_squad_as_default, validate_squad

WORKER_TIER_LABELS: Dict[int, str] = {
    1: "T1 Cheap/Fast",
    2: "T2 Everyday",
    3: "T3 Frontier",
}

SECTIONS: Tuple[Tuple[str, str], ...] = (
    ("aihead", "aihead"),
    ("ergo", "ergo"),
    ("orchestration", "orchestration"),
    ("council", "council"),
    ("herdr-master", "herdr-master"),
    ("models", "Available"),
)

ADD_CUSTOM_MODEL_VALUE: str = "__custom__"
ADD_CUSTOM_OPTION = ChoiceOption(
    value=ADD_CUSTOM_MODEL_VALUE,
    label="[+] Add custom model...",
    detail="enter custom model ID",
)


@dataclass
class FieldRow:
    """One interactive field or action row inside a section."""

    key: str
    group: str
    label: str
    kind: str  # "choice", "bool", "action", "info"
    value_text: str
    options: List[ChoiceOption] = field(default_factory=list)
    current_value: Any = None
    hint: str = ""
    deletable_id: Optional[Tuple[str, int]] = None
    badge: str = ""


@dataclass
class RatingInspectState:
    """Read-only modal displaying full rating breakdown for a single model."""

    harness: str
    model_id: str
    rating: Optional[ModelRating]


@dataclass
class PickerState:
    """Inline choice picker overlay for selecting harness, model, effort, or enum."""

    field_key: str
    title: str
    options: List[ChoiceOption]
    cursor: int = 0
    filter_text: str = ""
    scroll_offset: int = 0

    def filtered(self) -> List[ChoiceOption]:
        query = self.filter_text.strip().lower()
        if not query:
            return self.options
        return [
            opt
            for opt in self.options
            if query in opt.label.lower() or query in opt.detail.lower()
        ]


@dataclass
class TextPromptState:
    """Inline single-line text input prompt overlay."""

    field_key: str
    title: str
    prompt: str
    value: str = ""


def _effort_enum(value: Optional[str]) -> Optional[EffortLevel]:
    return EffortLevel(value) if value else None


def _effort_str(binding: HarnessBinding) -> Optional[str]:
    return binding.effort.value if binding.effort else None


_ENCODED_EFFORT_SUFFIXES = ("-low", "-medium", "-high", "-max", "-xhigh")


def _herdr_effort_separate(kind: str, model: str) -> bool:
    """Whether Herdr mode has a separate Effort selector for this binding.

    ``agy`` slugs such as ``gemini-3.8-flash-high`` or ``claude-opus-5-5-medium``
    already encode the tier. An empty allowed-effort list means the same thing:
    there is nothing to pick, so the row would only restate the slug.
    """
    if kind != "agy":
        return True
    allowed = _allowed_efforts(kind, model or "", mode="herdr")
    if not allowed:
        return False
    slug = (model or "").strip().lower()
    return not slug.endswith(_ENCODED_EFFORT_SUFFIXES)


class SetupTuiApp:
    """One-screen interactive setup state machine, renderer, and headless driver."""

    def __init__(
        self,
        config_path: Optional[Path] = None,
        *,
        catalog: Optional[ModelCatalog] = None,
        cfg: Optional[HerdrMasterConfig] = None,
        squad: Optional[Squad] = None,
        save_project_fn: Optional[Callable[[HerdrMasterConfig, Optional[Path]], Path]] = None,
        save_squad_fn: Optional[Callable[[Squad, Optional[Path]], Path]] = None,
        discover_harness_fn: Optional[Callable[[str], List[ChoiceOption]]] = None,
        evaluate_fn: Optional[Callable[..., EvaluationResult]] = None,
    ) -> None:
        self.config_path = config_path
        self._save_project_fn = (
            save_project_config if save_project_fn is None else save_project_fn
        )
        self._save_squad_fn = (
            save_squad_as_default if save_squad_fn is None else save_squad_fn
        )
        self._evaluate_fn = evaluate_available_models if evaluate_fn is None else evaluate_fn
        self._load_initial_state(cfg=cfg, squad=squad)
        if catalog is not None:
            self.catalog = catalog
            self._lazy_discovery_enabled = discover_harness_fn is not None
        else:
            self.catalog = discover_model_catalog(self.cfg, self.squad, probe_live=False)
            self._lazy_discovery_enabled = True
        self._discover_harness_fn = discover_harness_fn or discover_harness_models
        self._discovered_harnesses: set[str] = set()

        self.section_index: int = 0
        self.focus_pane: str = "sections"  # "sections" | "options"
        self.field_cursors: Dict[str, int] = {sec_id: 0 for sec_id, _ in SECTIONS}
        self.field_scroll: Dict[str, int] = {sec_id: 0 for sec_id, _ in SECTIONS}
        self.picker: Optional[PickerState] = None
        self.text_prompt: Optional[TextPromptState] = None
        self.rating_inspector: Optional[RatingInspectState] = None
        # View-only Available filters. Mode and harness are not written to TOML.
        self.available_view_mode: str = "herdr"
        self.available_view_harness: Optional[str] = None
        # Parallel to available.toggle.<index> so model ids never enter dotted keys.
        self._available_toggle_ids: List[str] = []
        self._available_toggle_mode: str = "herdr"
        self._available_toggle_harness: str = ""

        self.dirty: bool = False
        self.done: bool = False
        self.saved: bool = False
        self.saved_path: Optional[Path] = None
        self.status_message: str = (
            "1-6/↑↓ section  Tab/Enter edit  ←→ cycle  Space/Enter pick  S save  Q quit"
        )
        self.status_is_error: bool = False

    def _load_initial_state(
        self,
        *,
        cfg: Optional[HerdrMasterConfig] = None,
        squad: Optional[Squad] = None,
    ) -> None:
        from herdr_master.pipeline.launcher import resolve_coordinator_binding

        loaded_cfg = (
            cfg.model_copy(deep=True)
            if cfg is not None
            else load_project_config(self.config_path)
        )
        loaded_squad = (
            squad.model_copy(deep=True)
            if squad is not None
            else squad_from_project_config(loaded_cfg)
        )
        for worker in loaded_squad.worker_pool:
            worker.effort = None

        orch = loaded_cfg.orchestration
        if not orch.scout_model:
            orch.scout_model = orch.effective_scout_model()
        if not orch.architect_model:
            orch.architect_model = orch.effective_architect_model()
        if not orch.reviewer_model:
            orch.reviewer_model = orch.effective_reviewer_model()
        if not orch.worker_models:
            orch.worker_models = orch.effective_worker_models()

        self.cfg: HerdrMasterConfig = loaded_cfg
        self.squad: Squad = loaded_squad
        self._old_worker_pool_meta: Dict[str, Any] = dict(loaded_cfg.models.worker_pool or {})

        if loaded_cfg.ergo and loaded_cfg.ergo.coordinator:
            self.ergo_custom_coordinator = True
            self.ergo_coordinator = loaded_cfg.ergo.coordinator.model_copy(deep=True)
        else:
            self.ergo_custom_coordinator = False
            self.ergo_coordinator = loaded_squad.scout.model_copy(
                update={"label": "ergo-coordinator"}
            )
        self.ergo_worker_mode: str = (
            loaded_cfg.ergo.worker_mode if loaded_cfg.ergo is not None else "inherit"
        )

        if loaded_cfg.master_coordinator is not None:
            self.master_custom_coordinator = True
            self.master_coordinator = loaded_cfg.master_coordinator.model_copy(deep=True)
        else:
            self.master_custom_coordinator = False
            kind, model, effort = resolve_coordinator_binding()
            self.master_coordinator = HarnessBinding(
                kind=kind, model=model, effort=effort, label="master-coordinator"
            )
        self._clear_redundant_herdr_efforts()

    def _clear_redundant_herdr_efforts(self) -> None:
        """Drop effort that an agy Herdr slug already encodes.

        Worker-pool effort stays unset so the Architect can choose it per task.
        Persistence remains ``[models.worker_pool]``.
        """
        bindings = [
            self.cfg.ai_head.binding,
            self.squad.scout,
            self.squad.architect,
            self.squad.reviewer,
            self.ergo_coordinator,
            self.master_coordinator,
            self.cfg.council.coordinator,
            self.cfg.council.scout,
            *self.cfg.council.members,
        ]
        for binding in bindings:
            if not _herdr_effort_separate(binding.kind.value, binding.model):
                binding.effort = None
        for worker in self.squad.worker_pool:
            worker.effort = None

    @property
    def current_section(self) -> str:
        return SECTIONS[self.section_index][0]

    def _set_status(self, message: str, *, error: bool = False) -> None:
        self.status_message = message
        self.status_is_error = error

    def _ensure_harness_discovered(self, kind: str) -> None:
        if not self._lazy_discovery_enabled:
            return
        kind_key = str(kind).strip().lower()
        if not kind_key or kind_key in self._discovered_harnesses:
            return
        self._discovered_harnesses.add(kind_key)
        live = self._discover_harness_fn(kind_key)
        if live:
            self.catalog.merge_live_models(kind_key, live)

    def _reconcile_binding(
        self,
        binding: HarnessBinding,
        *,
        allow_effort: bool = True,
        allow_none_effort: bool = True,
    ) -> None:
        kind = binding.kind.value
        model_opts, _ = self._filtered_model_options(kind, mode="herdr", current=None)
        models = [opt for opt in model_opts if opt.value != ADD_CUSTOM_MODEL_VALUE]
        if models and all(opt.value != binding.model for opt in models):
            binding.model = str(models[0].value)

        if allow_effort and binding.kind.value == "agy" and binding.model == _FLASH_BASE:
            allow_none_effort = False
        if not allow_effort or not _herdr_effort_separate(binding.kind.value, binding.model):
            binding.effort = None
            return

        eff_options = self.catalog.efforts(
            kind, binding.model, mode="herdr", allow_none=allow_none_effort
        )
        allowed_values = [opt.value for opt in eff_options]
        current_eff = _effort_str(binding)
        if current_eff not in allowed_values:
            non_none = [val for val in allowed_values if val is not None]
            if not allow_none_effort and non_none:
                binding.effort = _effort_enum(str(non_none[0]))
            elif non_none and current_eff is not None:
                binding.effort = _effort_enum(str(non_none[0]))
            else:
                binding.effort = _effort_enum(allowed_values[0]) if allowed_values else None

    def _orch_model_mode(self, host: str) -> str:
        if host == "agy" and self.cfg.orchestration.agy_mode == "swarm":
            return "herdr"
        return "native"

    def _reconcile_orchestration(self) -> None:
        orch = self.cfg.orchestration
        host = orch.harness.value
        model_mode = self._orch_model_mode(host)
        model_opts, _ = self._filtered_model_options(host, mode=model_mode, current=None)
        models = [opt for opt in model_opts if opt.value != ADD_CUSTOM_MODEL_VALUE]
        valid_values = {str(opt.value) for opt in models}
        defaults = DEFAULT_ORCHESTRATION_MODELS.get(host, {})

        for role in ("scout", "architect", "reviewer"):
            cur_model = getattr(orch, f"{role}_model")
            if not cur_model or (valid_values and cur_model not in valid_values):
                default_for_role = defaults.get(role)
                if default_for_role and valid_values and default_for_role not in valid_values:
                    default_for_role = None
                fallback = default_for_role or (str(models[0].value) if models else "")
                setattr(orch, f"{role}_model", fallback)
            eff_options = self.catalog.efforts(
                host, getattr(orch, f"{role}_model") or "", mode=model_mode, allow_none=True
            )
            allowed_effs = [opt.value for opt in eff_options]
            cur_eff = getattr(orch, f"{role}_effort")
            if cur_eff not in allowed_effs:
                setattr(orch, f"{role}_effort", None)

        coder_default = defaults.get("coder")
        if coder_default and valid_values and coder_default not in valid_values:
            coder_default = None
        if not orch.worker_models:
            fallback_coder = coder_default or (str(models[0].value) if models else "")
            if fallback_coder:
                orch.worker_models = [fallback_coder]
        elif valid_values:
            fallback_coder = coder_default or str(models[0].value)
            orch.worker_models = [
                w if w in valid_values else fallback_coder for w in orch.worker_models
            ]

    def _filtered_model_options(
        self,
        kind: str,
        *,
        mode: str,
        current: Optional[str] = None,
    ) -> Tuple[List[ChoiceOption], str]:
        """Intersect a catalog model picker with the allowlist.

        Unrestricted scopes return the catalog list unchanged. A restricted
        scope keeps discovered order, then appends ``current`` when missing.
        An empty intersection sets the Available hint even if ``current`` remains.
        """
        allowlist = enabled_model_ids(self.cfg.models.available, mode, kind)
        if allowlist is None:
            self._ensure_harness_discovered(kind)
        options = self.catalog.models(kind, mode=mode, current=current)
        discovered = [str(opt.value) for opt in options]
        filtered = filter_model_ids(
            self.cfg.models.available,
            mode,
            kind,
            discovered,
            current=current,
        )
        if filtered is discovered:
            return list(options) + [ADD_CUSTOM_OPTION], ""
        by_value = {str(opt.value): opt for opt in options}
        rebuilt = [
            by_value[model_id]
            if model_id in by_value
            else ChoiceOption(value=model_id, label=model_id, detail="current")
            for model_id in filtered
        ]
        rebuilt.append(ADD_CUSTOM_OPTION)
        intersection_empty = allowlist is not None and not any(
            model_id in set(allowlist) for model_id in discovered
        )
        if intersection_empty:
            return rebuilt, "no models enabled in Available"
        return rebuilt, ""

    def _available_enabled_summary(self) -> str:
        """Sidebar text: unrestricted, or a short count of saved enabled ids."""
        available = self.cfg.models.available
        if available is None:
            return "unrestricted"
        total = 0
        restricted = 0
        for mode in ("herdr", "native"):
            mode_map = getattr(available, mode, None) or {}
            for ids in mode_map.values():
                restricted += 1
                total += len(ids)
        if restricted == 0:
            return "unrestricted"
        return f"{total} enabled"

    def _effective_ratings_and_eval(
        self,
    ) -> Tuple[Dict[str, Dict[str, ModelRating]], ModelEvaluationConfig]:
        effective = load_effective_models_state(
            self.config_path,
            project_cfg=self.cfg,
            inherit_ancestors=self.config_path is not None,
        )
        return effective.ratings, effective.evaluation

    def _available_rows(self) -> List[FieldRow]:
        """Rebuild Available rows. Bool keys are indexes, never model ids."""
        mode = self.available_view_mode
        if mode not in ("herdr", "native"):
            mode = "herdr"
        self.available_view_mode = mode
        harnesses = self.catalog.harnesses(mode=mode)
        harness_values = [str(opt.value) for opt in harnesses]
        harness = self.available_view_harness or ""
        if harness not in harness_values:
            harness = ""
        if not harness and harness_values:
            harness = harness_values[0]
        self.available_view_harness = harness or None
        if harness:
            self._ensure_harness_discovered(harness)
        models = self.catalog.models(harness, mode=mode, current=None) if harness else []
        ids = [str(opt.value) for opt in models]
        self._available_toggle_ids = ids
        self._available_toggle_mode = mode
        self._available_toggle_harness = harness
        enabled = (
            enabled_model_ids(self.cfg.models.available, mode, harness) if harness else None
        )
        enabled_set = None if enabled is None else set(enabled)
        all_enabled = (
            is_all_enabled(self.cfg.models.available, mode, harness, ids)
            if harness
            else True
        )
        effective_ratings, effective_eval = self._effective_ratings_and_eval()
        billing_tier = effective_eval.billing_for(harness) if harness else "api"

        rows = [
            FieldRow(
                key="available.view.mode",
                group="Allowlist View",
                label="Mode",
                kind="choice",
                value_text=mode,
                options=[
                    ChoiceOption("herdr", "herdr", "Herdr agent bindings"),
                    ChoiceOption("native", "native", "native orchestration ids"),
                ],
                current_value=mode,
                hint="View herdr or native models (not saved)",
            ),
            FieldRow(
                key="available.view.harness",
                group="Allowlist View",
                label="Harness",
                kind="choice",
                value_text=harness or "(none)",
                options=harnesses,
                current_value=harness,
                hint="View one harness allowlist (not saved)",
            ),
            FieldRow(
                key="available.all",
                group="Models",
                label="All",
                kind="bool",
                value_text="true" if all_enabled else "false",
                options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                current_value=all_enabled,
                hint="Enable or disable all models for this harness",
            ),
        ]
        unrated_count = 0
        for index, opt in enumerate(models):
            model_id = ids[index]
            checked = enabled_set is None or model_id in enabled_set
            rating = lookup_rating(effective_ratings, harness, model_id) if harness else None
            if rating is None:
                if checked:
                    unrated_count += 1
                badge = "[unrated]"
                hint = "Toggle allowlist | [T] set tier | [V] inspect | [E] evaluate unrated models"
            else:
                tier = effective_worker_tier(rating)
                tier_tag = WORKER_TIER_LABELS.get(tier, f"T{tier}")
                if rating.worker_tier_override:
                    tier_tag = f"{tier_tag}*"
                badge = f"[{tier_tag}] {rating.compact_badge()}"
                ctx_str = format_context_window(rating.context_window)
                hint = (
                    f"{rating.family} | A:{rating.agentic} Ctx:{ctx_str} "
                    f"({rating.confidence}) | [T] set tier | [V] inspect"
                )
            rows.append(
                FieldRow(
                    key=f"available.toggle.{index}",
                    group="Models",
                    label=opt.label,
                    kind="bool",
                    value_text="true" if checked else "false",
                    options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                    current_value=checked,
                    hint=hint,
                    badge=badge,
                )
            )
        rows.append(
            FieldRow(
                key="available.billing",
                group="Model Evaluation",
                label="Billing Tier",
                kind="choice",
                value_text=billing_tier,
                options=[
                    ChoiceOption("api", "api", "pay-per-token API pricing"),
                    ChoiceOption("subscription", "subscription", "included subscription quota"),
                    ChoiceOption("free", "free", "free route / $0 marginal cost"),
                ],
                current_value=billing_tier,
                hint=f"Cost-efficiency billing tier for {harness or 'this harness'}",
            )
        )
        rows.append(
            FieldRow(
                key="available.evaluate",
                group="Model Evaluation",
                label="Evaluate Unrated Models",
                kind="action",
                value_text="Enter / E",
                hint=(
                    f"Evaluate {unrated_count} unrated enabled model(s) in {harness or 'catalog'} "
                    "(zero-auth benchmarks + Layer-3 fallback)"
                ),
            )
        )
        rows.append(
            FieldRow(
                key="available.add_custom",
                group="Models",
                label="+ Add Custom Model",
                kind="action",
                value_text="Enter / A",
                hint="Add a custom model ID to the allowlist for this harness",
            )
        )
        return rows

    def _apply_available_field(self, parts: List[str], value: Any) -> bool:
        """Apply an Available field. False means view-only or rejected (not dirty)."""
        if len(parts) == 2 and parts[1] == "billing":
            harness = self._available_toggle_harness
            if not harness:
                self._set_status("No harness selected for billing tier.", error=True)
                return False
            tier = str(value).strip().lower()
            if tier not in BILLING_TIERS:
                self._set_status(f"Unsupported billing tier {value!r}.", error=True)
                return False
            if self.cfg.models.evaluation is None:
                self.cfg.models.evaluation = ModelEvaluationConfig()
            self.cfg.models.evaluation.billing[harness] = tier  # type: ignore[assignment]
            self._set_status(f"Set billing tier for {harness} to {tier}")
            return True
        if (len(parts) == 2 and parts[1] == "all") or (
            len(parts) == 3 and parts[1] == "toggle" and parts[2] == "all"
        ):
            harness = self._available_toggle_harness
            if not harness:
                self._set_status("No harness selected for Available.", error=True)
                return False
            self.cfg.models.available = materialize_toggle_all(
                self.cfg.models.available,
                self._available_toggle_mode,
                harness,
                bool(value),
                list(self._available_toggle_ids),
            )
            return True
        if len(parts) < 3:
            self._set_status(f"Unknown available field {'.'.join(parts)}", error=True)
            return False
        if parts[1] == "view":
            if parts[2] == "mode":
                self.available_view_mode = str(value)
                harnesses = self.catalog.harnesses(mode=self.available_view_mode)
                valid = {str(opt.value) for opt in harnesses}
                if self.available_view_harness not in valid:
                    self.available_view_harness = (
                        str(harnesses[0].value) if harnesses else None
                    )
            elif parts[2] == "harness":
                self.available_view_harness = str(value)
            else:
                self._set_status(f"Unknown available view {parts[2]}", error=True)
                return False
            shown = self.available_view_harness or "-"
            self._set_status(f"Viewing {self.available_view_mode} {shown}")
            return False
        if parts[1] == "tier":
            try:
                index = int(parts[2])
            except ValueError:
                self._set_status("Available tier key must be an index.", error=True)
                return False
            if index < 0 or index >= len(self._available_toggle_ids):
                self._set_status("Available model list changed; select tier again.", error=True)
                return False
            harness = self._available_toggle_harness
            if not harness:
                self._set_status("No harness selected for Available.", error=True)
                return False
            model_id = self._available_toggle_ids[index]
            return self._apply_available_worker_tier(harness, model_id, value)
        if parts[1] != "toggle":
            self._set_status(f"Unknown available field {'.'.join(parts)}", error=True)
            return False
        try:
            index = int(parts[2])
        except ValueError:
            self._set_status("Available toggle key must be an index.", error=True)
            return False
        if index < 0 or index >= len(self._available_toggle_ids):
            self._set_status("Available model list changed; toggle again.", error=True)
            return False
        harness = self._available_toggle_harness
        if not harness:
            self._set_status("No harness selected for Available.", error=True)
            return False
        model_id = self._available_toggle_ids[index]
        self.cfg.models.available = materialize_toggle(
            self.cfg.models.available,
            self._available_toggle_mode,
            harness,
            model_id,
            bool(value),
            list(self._available_toggle_ids),
        )
        return True

    def _binding_rows(
        self,
        prefix: str,
        group: str,
        binding: HarnessBinding,
        *,
        label_prefix: str = "",
        allow_effort: bool = True,
        allow_none_effort: bool = True,
        deletable_id: Optional[Tuple[str, int]] = None,
    ) -> List[FieldRow]:
        kind = binding.kind.value
        harnesses = self.catalog.harnesses(mode="herdr")
        models, empty_hint = self._filtered_model_options(
            kind, mode="herdr", current=binding.model
        )
        eff_options = self.catalog.efforts(
            kind, binding.model, mode="herdr", allow_none=allow_none_effort
        )
        prefix_lbl = f"{label_prefix} " if label_prefix else ""
        rows: List[FieldRow] = [
            FieldRow(
                key=f"{prefix}.kind",
                group=group,
                label=f"{prefix_lbl}Harness",
                kind="choice",
                value_text=kind,
                options=harnesses,
                current_value=kind,
                hint="Select Herdr harness from catalog",
                deletable_id=deletable_id,
            ),
            FieldRow(
                key=f"{prefix}.model",
                group=group,
                label=f"{prefix_lbl}Model",
                kind="choice",
                value_text=binding.model,
                options=models,
                current_value=binding.model,
                hint=empty_hint or f"Select {kind} model from live catalog/models",
                deletable_id=deletable_id,
            ),
        ]
        show_effort = allow_effort and _herdr_effort_separate(kind, binding.model)
        if show_effort:
            cur_eff = _effort_str(binding)
            rows.append(
                FieldRow(
                    key=f"{prefix}.effort",
                    group=group,
                    label=f"{prefix_lbl}Effort",
                    kind="choice",
                    value_text=cur_eff or "none",
                    options=eff_options,
                    current_value=cur_eff,
                    hint="Select reasoning effort",
                    deletable_id=deletable_id,
                )
            )
        return rows

    def _section_rows(self, section: str) -> List[FieldRow]:
        if section == "aihead":
            ah = self.cfg.ai_head
            rows = self._binding_rows("aihead", "Coordinator Binding", ah.binding)
            rows.extend(
                [
                    FieldRow(
                        key="aihead.enabled",
                        group="AiHead Options",
                        label="Enabled",
                        kind="bool",
                        value_text="true" if ah.enabled else "false",
                        options=[
                            ChoiceOption(True, "true"),
                            ChoiceOption(False, "false"),
                        ],
                        current_value=ah.enabled,
                        hint="Enable AiHead discovery/auto-launch",
                    ),
                    FieldRow(
                        key="aihead.mode",
                        group="AiHead Options",
                        label="Mode",
                        kind="choice",
                        value_text=ah.mode or "auto (unset)",
                        options=[
                            ChoiceOption(None, "auto (unset)", "inherit default execution mode"),
                            ChoiceOption("herdr", "herdr", "launch inside Herdr workspace"),
                            ChoiceOption("cli", "cli", "direct CLI execution"),
                        ],
                        current_value=ah.mode,
                        hint="Select AiHead execution mode",
                    ),
                    FieldRow(
                        key="aihead.split",
                        group="AiHead Options",
                        label="Split Pane",
                        kind="bool",
                        value_text="true" if ah.split else "false",
                        options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                        current_value=ah.split,
                        hint="Split Herdr screen when launching AiHead",
                    ),
                    FieldRow(
                        key="aihead.autonomy",
                        group="AiHead Options",
                        label="Autonomy",
                        kind="choice",
                        value_text=ah.autonomy,
                        options=[
                            ChoiceOption(
                                "routine",
                                "routine",
                                "proceed automatically on routine tasks",
                            ),
                            ChoiceOption("ask", "ask", "ask before taking actions"),
                        ],
                        current_value=ah.autonomy,
                        hint="Select AiHead autonomy policy",
                    ),
                    FieldRow(
                        key="aihead.brief_before_action",
                        group="AiHead Options",
                        label="Brief Before Action",
                        kind="bool",
                        value_text="true" if ah.brief_before_action else "false",
                        options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                        current_value=ah.brief_before_action,
                        hint="Present brief before executing changes",
                    ),
                    FieldRow(
                        key="aihead.close_foreign_tabs",
                        group="AiHead Options",
                        label="Close Foreign Tabs",
                        kind="bool",
                        value_text="true" if ah.close_foreign_tabs else "false",
                        options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                        current_value=ah.close_foreign_tabs,
                        hint="Close foreign tabs on AiHead start",
                    ),
                    FieldRow(
                        key="aihead.dangerously_skip_permissions",
                        group="AiHead Options",
                        label="Skip Permissions",
                        kind="bool",
                        value_text="true" if ah.dangerously_skip_permissions else "false",
                        options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                        current_value=ah.dangerously_skip_permissions,
                        hint="Pass permission-skip flag to harness",
                    ),
                    FieldRow(
                        key="aihead.automode",
                        group="AiHead Options",
                        label="Automode",
                        kind="bool",
                        value_text="true" if ah.automode else "false",
                        options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                        current_value=ah.automode,
                        hint="Compose Ergo/Orchestration squads from Available models",
                    ),
                    FieldRow(
                        key="aihead.routing_profile",
                        group="AiHead Options",
                        label="Routing Profile",
                        kind="choice",
                        value_text=ah.routing_profile,
                        options=[
                            ChoiceOption(
                                "balanced", "balanced", "match model & effort to task complexity"
                            ),
                            ChoiceOption(
                                "quality", "quality", "prefer highest intelligence & coding ratings"
                            ),
                            ChoiceOption("fast", "fast", "prefer highest speed & low latency"),
                            ChoiceOption(
                                "frugal", "frugal", "prefer highest cost_efficiency / free routes"
                            ),
                        ],
                        current_value=ah.routing_profile,
                        hint="Default optimization profile for AiHead triage & inherited pipelines",
                    ),
                ]
            )
            return rows

        if section == "ergo":
            rows: List[FieldRow] = [
                FieldRow(
                    key="ergo.coord_mode",
                    group="Ergo Coordinator",
                    label="Coordinator",
                    kind="choice",
                    value_text="Custom" if self.ergo_custom_coordinator else "Same as Scout",
                    options=[
                        ChoiceOption(False, "Same as Scout", "default: inherit Scout binding"),
                        ChoiceOption(True, "Custom", "use dedicated Ergo coordinator binding"),
                    ],
                    current_value=self.ergo_custom_coordinator,
                    hint="Choose whether Ergo coordinator inherits Scout or uses custom binding",
                )
            ]
            if self.ergo_custom_coordinator:
                rows.extend(
                    self._binding_rows(
                        "ergo.coord",
                        "Ergo Coordinator",
                        self.ergo_coordinator,
                        label_prefix="Coordinator",
                    )
                )
            for role in ("scout", "architect", "reviewer"):
                binding = getattr(self.squad, role)
                needs_effort = binding.kind.value == "agy" and binding.model == _FLASH_BASE
                rows.extend(
                    self._binding_rows(
                        f"ergo.{role}",
                        role.capitalize(),
                        binding,
                        allow_none_effort=not needs_effort,
                    )
                )
            rows.append(
                FieldRow(
                    key="ergo.worker_mode",
                    group="Dynamic Workers (Architect picks from Available + Ratings)",
                    label="Worker Mode",
                    kind="choice",
                    value_text=self.ergo_worker_mode,
                    options=[
                        ChoiceOption(
                            "inherit",
                            "inherit",
                            "follow AiHead routing_profile (or balanced standalone)",
                        ),
                        ChoiceOption(
                            "balanced", "balanced", "match worker model & effort to task complexity"
                        ),
                        ChoiceOption(
                            "quality", "quality", "prefer top coding & intelligence ratings"
                        ),
                        ChoiceOption("fast", "fast", "prefer fastest models"),
                        ChoiceOption("frugal", "frugal", "prefer lowest cost / free models"),
                    ],
                    current_value=self.ergo_worker_mode,
                    hint="Optimization bias for Architect dynamic worker selection (all harnesses)",
                )
            )
            rows.append(
                FieldRow(
                    key="ergo.workers_info",
                    group="Dynamic Workers (Architect picks from Available + Ratings)",
                    label="Worker Pool",
                    kind="info",
                    value_text="Dynamic (Available models)",
                    hint="Manage allowed models & ratings in [6] Available",
                )
            )
            return rows

        if section == "orchestration":
            orch = self.cfg.orchestration
            host = orch.harness.value
            native_harnesses = self.catalog.harnesses(mode="native")
            model_mode = self._orch_model_mode(host)
            defaults = DEFAULT_ORCHESTRATION_MODELS.get(host, {})
            rows = [
                FieldRow(
                    key="orch.harness",
                    group="Native Host",
                    label="Harness",
                    kind="choice",
                    value_text=host,
                    options=native_harnesses,
                    current_value=host,
                    hint="Switch active native harness (preserves per-harness profiles)",
                )
            ]
            if host == "agy":
                rows.append(
                    FieldRow(
                        key="orch.agy_mode",
                        group="Native Host",
                        label="AGY Mode",
                        kind="choice",
                        value_text=orch.agy_mode,
                        options=[
                            ChoiceOption(
                                "subagents",
                                "subagents",
                                "invoke_subagent (tier IDs: flash_lite, flash, pro)",
                            ),
                            ChoiceOption(
                                "swarm",
                                "swarm",
                                "native swarm (exact agy models + shared board)",
                            ),
                        ],
                        current_value=orch.agy_mode,
                        hint="Choose subagents (tier IDs) or swarm (exact agy models)",
                    )
                )
            for role in ("scout", "architect", "reviewer"):
                cur_model = getattr(orch, f"{role}_model") or defaults.get(role, "")
                cur_eff = getattr(orch, f"{role}_effort")
                model_opts, empty_hint = self._filtered_model_options(
                    host, mode=model_mode, current=cur_model
                )
                eff_opts = self.catalog.efforts(
                    host, cur_model, mode=model_mode, allow_none=True
                )
                rows.append(
                    FieldRow(
                        key=f"orch.{role}_model",
                        group=role.capitalize(),
                        label="Model",
                        kind="choice",
                        value_text=cur_model or "(unset)",
                        options=model_opts,
                        current_value=cur_model,
                        hint=empty_hint or f"Select {host} {model_mode} model for {role}",
                    )
                )
                rows.append(
                    FieldRow(
                        key=f"orch.{role}_effort",
                        group=role.capitalize(),
                        label="Effort",
                        kind="choice",
                        value_text=cur_eff or "none",
                        options=eff_opts,
                        current_value=cur_eff,
                        hint=f"Select reasoning effort for {role}",
                    )
                )

            rows.append(
                FieldRow(
                    key="orch.worker_mode",
                    group="Dynamic Workers (Architect picks from Available + Ratings)",
                    label="Worker Mode",
                    kind="choice",
                    value_text=orch.worker_mode,
                    options=[
                        ChoiceOption(
                            "inherit",
                            "inherit",
                            "follow AiHead routing_profile (or balanced standalone)",
                        ),
                        ChoiceOption(
                            "balanced", "balanced", "match worker model & effort to task complexity"
                        ),
                        ChoiceOption(
                            "quality", "quality", "prefer top coding & intelligence ratings"
                        ),
                        ChoiceOption("fast", "fast", "prefer fastest models"),
                        ChoiceOption("frugal", "frugal", "prefer lowest cost / free models"),
                    ],
                    current_value=orch.worker_mode,
                    hint=f"Optimization bias for Architect worker selection ({host} only)",
                )
            )
            rows.append(
                FieldRow(
                    key="orch.workers_info",
                    group="Dynamic Workers (Architect picks from Available + Ratings)",
                    label="Worker Pool",
                    kind="info",
                    value_text=f"Dynamic ({host} Available only)",
                    hint="Manage allowed models & ratings in [6] Available",
                )
            )
            return rows

        if section == "council":
            council = self.cfg.council
            rows = []
            rows.extend(self._binding_rows("council.coord", "Coordinator", council.coordinator))
            rows.extend(self._binding_rows("council.scout", "Scout", council.scout))
            for idx, member in enumerate(council.members):
                label = member.label or f"council-{idx + 1}"
                tag = ("council_member", idx)
                rows.extend(
                    self._binding_rows(
                        f"council.member.{idx}",
                        "Council Members (>= 2 enabled)",
                        member,
                        label_prefix=label,
                        deletable_id=tag,
                    )
                )
                rows.append(
                    FieldRow(
                        key=f"council.member.{idx}.enabled",
                        group="Council Members (>= 2 enabled)",
                        label=f"{label} Enabled",
                        kind="bool",
                        value_text="true" if member.enabled else "false",
                        options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                        current_value=member.enabled,
                        hint="Toggle council member active state",
                        deletable_id=tag,
                    )
                )
            rows.append(
                FieldRow(
                    key="council.member_add",
                    group="Council Members (>= 2 enabled)",
                    label="+ Add Council Member",
                    kind="action",
                    value_text="Enter / A",
                    hint="Add a new council member",
                )
            )
            if len(council.members) > 2:
                rows.append(
                    FieldRow(
                        key="council.member_remove",
                        group="Council Members (>= 2 enabled)",
                        label="- Remove Last Member",
                        kind="action",
                        value_text="Enter / D",
                        hint="Remove the last council member",
                    )
                )
            return rows

        if section == "herdr-master":
            rows = [
                FieldRow(
                    key="master.mode",
                    group="Master Coordinator",
                    label="Override Mode",
                    kind="choice",
                    value_text=(
                        "Custom"
                        if self.master_custom_coordinator
                        else "Default (inherit Scout)"
                    ),
                    options=[
                        ChoiceOption(
                            False, "Default (inherit Scout)", "use catalog Scout coordinator"
                        ),
                        ChoiceOption(
                            True, "Custom", "persist explicit [master_coordinator]"
                        ),
                    ],
                    current_value=self.master_custom_coordinator,
                    hint="Choose Default (unset) or Custom master coordinator",
                )
            ]
            rows.extend(
                self._binding_rows(
                    "master", "Master Coordinator", self.master_coordinator
                )
            )
            return rows

        if section == "models":
            return self._available_rows()

        return []

    def _worker_pool_rows(self) -> List[FieldRow]:
        rows: List[FieldRow] = []
        for idx, worker in enumerate(self.squad.worker_pool):
            label = worker.label or f"worker-{idx + 1}"
            tag = ("pool_worker", idx)
            rows.extend(
                self._binding_rows(
                    f"pool.{idx}",
                    "Worker Pool (effort chosen per task)",
                    worker,
                    label_prefix=label,
                    allow_effort=False,
                    deletable_id=tag,
                )
            )
            rows.append(
                FieldRow(
                    key=f"pool.{idx}.enabled",
                    group="Worker Pool (effort chosen per task)",
                    label=f"{label} Enabled",
                    kind="bool",
                    value_text="true" if worker.enabled else "false",
                    options=[ChoiceOption(True, "true"), ChoiceOption(False, "false")],
                    current_value=worker.enabled,
                    hint="Toggle worker active state",
                    deletable_id=tag,
                )
            )
        rows.append(
            FieldRow(
                key="pool.add",
                group="Worker Pool (effort chosen per task)",
                label="+ Add Worker",
                kind="action",
                value_text="Enter / A",
                hint="Add a worker to the shared worker pool",
            )
        )
        if len(self.squad.worker_pool) > 1:
            rows.append(
                FieldRow(
                    key="pool.remove",
                    group="Worker Pool (effort chosen per task)",
                    label="- Remove Last Worker",
                    kind="action",
                    value_text="Enter / D",
                    hint="Remove the last worker from the pool",
                )
            )
        return rows

    def _current_rows(self) -> List[FieldRow]:
        return self._section_rows(self.current_section)

    def _current_row(self) -> Optional[FieldRow]:
        rows = self._current_rows()
        if not rows:
            return None
        idx = max(0, min(self.field_cursors[self.current_section], len(rows) - 1))
        self.field_cursors[self.current_section] = idx
        return rows[idx]

    def _apply_binding_change(
        self,
        binding: HarnessBinding,
        attr: str,
        value: Any,
        *,
        allow_effort: bool = True,
        allow_none_effort: bool = True,
    ) -> None:
        if attr == "kind":
            binding.kind = HarnessKind(str(value))
            self._reconcile_binding(
                binding, allow_effort=allow_effort, allow_none_effort=allow_none_effort
            )
        elif attr == "model":
            binding.model = str(value)
            self._reconcile_binding(
                binding, allow_effort=allow_effort, allow_none_effort=allow_none_effort
            )
        elif attr == "effort":
            binding.effort = _effort_enum(value)

    def _apply_field_value(self, key: str, value: Any) -> None:
        parts = key.split(".")
        root = parts[0]

        if root == "aihead":
            attr = parts[1]
            if attr in ("kind", "model", "effort"):
                self._apply_binding_change(self.cfg.ai_head.binding, attr, value)
            else:
                setattr(self.cfg.ai_head, attr, value)

        elif root == "ergo":
            if parts[1] == "coord_mode":
                self.ergo_custom_coordinator = bool(value)
                if self.ergo_custom_coordinator and not self.ergo_coordinator.model:
                    self.ergo_coordinator = self.squad.scout.model_copy(
                        update={"label": "ergo-coordinator"}
                    )
            elif parts[1] == "worker_mode":
                self.ergo_worker_mode = str(value)
            elif parts[1] == "coord":
                self._apply_binding_change(self.ergo_coordinator, parts[2], value)
            else:
                role, attr = parts[1], parts[2]
                binding = getattr(self.squad, role)
                self._apply_binding_change(binding, attr, value)

        elif root == "orch":
            orch = self.cfg.orchestration
            if parts[1] == "harness":
                orch.switch_harness(str(value), set_as_default=True)
                self._reconcile_orchestration()
            elif parts[1] == "agy_mode":
                orch.agy_mode = str(value)
                self._reconcile_orchestration()
            elif parts[1] == "worker_mode":
                orch.worker_mode = str(value)
            elif parts[1] == "worker":
                idx = int(parts[2])
                if not orch.worker_models:
                    orch.worker_models = [str(value)]
                elif 0 <= idx < len(orch.worker_models):
                    orch.worker_models[idx] = str(value)
            elif parts[1].endswith("_model"):
                setattr(orch, parts[1], str(value))
                role = parts[1].replace("_model", "")
                eff_options = self.catalog.efforts(
                    orch.harness.value,
                    str(value),
                    mode=self._orch_model_mode(orch.harness.value),
                    allow_none=True,
                )
                if getattr(orch, f"{role}_effort") not in [opt.value for opt in eff_options]:
                    setattr(orch, f"{role}_effort", None)
            elif parts[1].endswith("_effort"):
                setattr(orch, parts[1], str(value) if value else None)

        elif root == "council":
            if parts[1] == "coord":
                self._apply_binding_change(self.cfg.council.coordinator, parts[2], value)
            elif parts[1] == "scout":
                self._apply_binding_change(self.cfg.council.scout, parts[2], value)
            elif parts[1] == "member":
                idx, attr = int(parts[2]), parts[3]
                member = self.cfg.council.members[idx]
                if attr == "enabled":
                    member.enabled = bool(value)
                else:
                    self._apply_binding_change(member, attr, value)

        elif root == "master":
            if parts[1] == "mode":
                self.master_custom_coordinator = bool(value)
                if not self.master_custom_coordinator:
                    from herdr_master.pipeline.launcher import resolve_coordinator_binding

                    kind, model, effort = resolve_coordinator_binding()
                    self.master_coordinator = HarnessBinding(
                        kind=kind, model=model, effort=effort, label="master-coordinator"
                    )
            else:
                self.master_custom_coordinator = True
                self._apply_binding_change(self.master_coordinator, parts[1], value)

        elif root == "available":
            if not self._apply_available_field(parts, value):
                return
            if len(parts) >= 2 and parts[1] == "tier":
                self.dirty = True
                return

        elif root == "pool":
            idx, attr = int(parts[1]), parts[2]
            worker = self.squad.worker_pool[idx]
            if attr == "enabled":
                worker.enabled = bool(value)
            else:
                self._apply_binding_change(worker, attr, value, allow_effort=False)

        self.dirty = True
        self._set_status(f"Updated {key}")

    def _run_action(self, action_key: str) -> None:
        if action_key == "orch.worker_add":
            orch = self.cfg.orchestration
            host = orch.harness.value
            model_opts, _ = self._filtered_model_options(
                host, mode=self._orch_model_mode(host), current=None
            )
            models = [opt for opt in model_opts if opt.value != ADD_CUSTOM_MODEL_VALUE]
            existing = set(orch.worker_models)
            choice = next(
                (str(opt.value) for opt in models if str(opt.value) not in existing),
                str(models[0].value) if models else "general",
            )
            orch.worker_models.append(choice)
            self.dirty = True
            self._set_status(f"Added orchestration worker: {choice}")
            return

        if action_key == "orch.worker_remove":
            self._delete_item("orch_worker", len(self.cfg.orchestration.worker_models) - 1)
            return

        if action_key == "council.member_add":
            members = self.cfg.council.members
            next_num = len(members) + 1
            existing_labels = {(m.label or "").lower() for m in members}
            while f"council-{next_num}" in existing_labels:
                next_num += 1
            base = members[-1] if members else self.cfg.council.coordinator
            members.append(
                CouncilMember(
                    kind=base.kind,
                    model=base.model,
                    effort=base.effort,
                    label=f"council-{next_num}",
                    description=f"Council Member {next_num}",
                    enabled=True,
                    order=len(members),
                )
            )
            self.dirty = True
            self._set_status(f"Added council-{next_num}")
            return

        if action_key == "council.member_remove":
            self._delete_item("council_member", len(self.cfg.council.members) - 1)
            return

        if action_key == "pool.add":
            pool = self.squad.worker_pool
            next_num = len(pool) + 1
            existing_labels = {(w.label or "").lower() for w in pool}
            while f"worker-{next_num}" in existing_labels:
                next_num += 1
            existing_pairs = {(w.kind.value, w.model) for w in pool}
            # Pick a distinct catalog model so validate_squad won't see a duplicate.
            new_kind = HarnessKind.PI
            new_model = "xai/grok-4.7"
            found = False
            for h_opt in self.catalog.harnesses(mode="herdr"):
                h_kind = str(h_opt.value)
                model_opts, _ = self._filtered_model_options(h_kind, mode="herdr", current=None)
                for m_opt in model_opts:
                    if m_opt.value == ADD_CUSTOM_MODEL_VALUE:
                        continue
                    if (h_kind, str(m_opt.value)) not in existing_pairs:
                        new_kind = HarnessKind(h_kind)
                        new_model = str(m_opt.value)
                        found = True
                        break
                if found:
                    break
            pool.append(
                HarnessBinding(
                    kind=new_kind,
                    model=new_model,
                    effort=None,
                    label=f"worker-{next_num}",
                    enabled=True,
                    order=len(pool),
                )
            )
            self.dirty = True
            self._set_status(f"Added worker-{next_num} ({new_kind.value}:{new_model})")
            return

        if action_key == "pool.remove":
            self._delete_item("pool_worker", len(self.squad.worker_pool) - 1)
            return

        if action_key == "available.add_custom":
            self._open_custom_model_prompt("available.add_custom")
            return

        if action_key == "available.evaluate":
            self._evaluate_unrated_models()
            return

    def _evaluate_unrated_models(self) -> None:
        try:
            res = self._evaluate_fn(
                project_path=self.config_path,
                cfg=self.cfg,
                catalog=self.catalog,
                dry_run=True,
                probe_live_catalog=False,
                inherit_ancestors=self.config_path is not None,
            )
        except Exception as err:
            self._set_status(f"Evaluation failed: {err}", error=True)
            return

        if res.evaluated:
            self.cfg.models.ratings = merge_ratings(self.cfg.models.ratings, res.evaluated)
            self.dirty = True
            self._set_status(
                f"Evaluated {res.evaluated_count} model(s), skipped {res.skipped_count}, "
                f"unresolved {res.unresolved_count}. Press S to save."
            )
        elif res.unresolved_count > 0:
            self._set_status(
                f"No new ratings resolved ({res.unresolved_count} unresolved, "
                f"{res.skipped_count} already rated).",
                error=True,
            )
        else:
            self._set_status(
                f"All target models already rated ({res.skipped_count} skipped)."
            )

    def _open_tier_picker_for_current_available(self) -> None:
        row = self._current_row()
        if row is None or not row.key.startswith("available.toggle."):
            self._set_status("Select a model row in Available to set its worker tier.")
            return
        try:
            index = int(row.key.split(".")[2])
            model_id = self._available_toggle_ids[index]
        except (ValueError, IndexError):
            return
        harness = self._available_toggle_harness
        if not harness:
            self._set_status("No harness selected for Available.", error=True)
            return
        effective_ratings, _ = self._effective_ratings_and_eval()
        rating = lookup_rating(effective_ratings, harness, model_id)
        options = [
            ChoiceOption(1, "1. Cheap / Fast", "Tier 1 — quick, low-cost tasks & scouts"),
            ChoiceOption(
                2, "2. Everyday / All-rounder", "Tier 2 — general coding & balanced default"
            ),
            ChoiceOption(3, "3. Frontier", "Tier 3 — complex architecture & hard tasks"),
            ChoiceOption("auto", "Auto (derived)", "Recompute tier automatically from ratings"),
        ]
        cursor = 0
        if rating is not None:
            current_val: Any = (
                effective_worker_tier(rating) if rating.worker_tier_override else "auto"
            )
            for idx, opt in enumerate(options):
                if opt.value == current_val:
                    cursor = idx
                    break
        self.picker = PickerState(
            field_key=f"available.tier.{index}",
            title=f"Worker Tier — {harness}:{model_id}",
            options=options,
            cursor=cursor,
        )
        self._set_status(f"Select worker tier for {harness}:{model_id} (Enter confirm, Esc cancel)")

    def _apply_available_worker_tier(self, harness: str, model_id: str, value: Any) -> bool:
        effective_ratings, effective_eval = self._effective_ratings_and_eval()
        existing = lookup_rating(effective_ratings, harness, model_id)
        if value == "auto":
            if existing is None:
                self._set_status("No rating stored yet; evaluate or choose tier 1/2/3.", error=True)
                return False
            recomputed = derive_worker_tier(existing)
            updated = existing.model_copy(
                update={"worker_tier": recomputed, "worker_tier_override": False}
            )
            if self.cfg.models.ratings is None:
                self.cfg.models.ratings = {}
            self.cfg.models.ratings.setdefault(harness, {})[model_id] = updated
            self._set_status(
                f"Worker tier for {harness}:{model_id} set to Auto "
                f"({WORKER_TIER_LABELS[recomputed]})"
            )
            return True
        if value not in (1, 2, 3):
            self._set_status(f"Invalid worker tier {value!r}.", error=True)
            return False
        if existing is not None:
            updated = existing.model_copy(
                update={"worker_tier": value, "worker_tier_override": True}
            )
        else:
            mode = (
                self._available_toggle_mode
                if self._available_toggle_mode in ("herdr", "native")
                else "herdr"
            )
            updated = ModelRating(
                intelligence=75,
                coding=75,
                agentic=75,
                speed=75,
                cost_efficiency=75,
                context_handling=75,
                family=detect_model_family(harness, model_id),
                billing_tier=effective_eval.billing_for(harness),
                worker_tier=value,
                worker_tier_override=True,
                supported_efforts=infer_supported_efforts(harness, model_id, mode=mode),
                notes="Manual worker tier",
                source="manual_tier",
                confidence="low",
                manual_override=False,
            )
        if self.cfg.models.ratings is None:
            self.cfg.models.ratings = {}
        self.cfg.models.ratings.setdefault(harness, {})[model_id] = updated
        self._set_status(
            f"Worker tier for {harness}:{model_id} set to {WORKER_TIER_LABELS[value]}"
        )
        return True

    def _inspect_current_available_rating(self) -> None:
        row = self._current_row()
        if row is None or not row.key.startswith("available.toggle."):
            self._set_status("Select a model row in Available to inspect its rating.")
            return
        try:
            index = int(row.key.split(".")[2])
            model_id = self._available_toggle_ids[index]
        except (ValueError, IndexError):
            return
        harness = self._available_toggle_harness
        effective_ratings, _ = self._effective_ratings_and_eval()
        rating = lookup_rating(effective_ratings, harness, model_id)
        self.rating_inspector = RatingInspectState(
            harness=harness,
            model_id=model_id,
            rating=rating,
        )
        self._set_status(
            f"Inspecting rating for {harness}:{model_id}. Press Esc or Enter to close."
        )

    def _delete_item(self, kind_tag: str, idx: int) -> None:
        if kind_tag == "orch_worker":
            workers = self.cfg.orchestration.worker_models
            if len(workers) <= 1:
                self._set_status("Orchestration requires at least 1 worker.", error=True)
                return
            removed = workers.pop(idx)
            self.dirty = True
            self._set_status(f"Removed orchestration worker {removed}")
            self._clamp_cursor()
            return

        if kind_tag == "council_member":
            members = self.cfg.council.members
            if len(members) <= 2:
                self._set_status("Council requires at least 2 members.", error=True)
                return
            remaining = [m for i, m in enumerate(members) if i != idx]
            if sum(1 for m in remaining if m.enabled) < 2:
                self._set_status("Council requires at least 2 enabled members.", error=True)
                return
            removed = members.pop(idx)
            self.dirty = True
            self._set_status(f"Removed {removed.label or 'member'}")
            self._clamp_cursor()
            return

        if kind_tag == "pool_worker":
            pool = self.squad.worker_pool
            if len(pool) <= 1:
                self._set_status("Worker pool requires at least 1 worker.", error=True)
                return
            remaining = [w for i, w in enumerate(pool) if i != idx]
            if not any(w.enabled for w in remaining):
                self._set_status("Worker pool requires at least 1 enabled worker.", error=True)
                return
            removed = pool.pop(idx)
            self.dirty = True
            self._set_status(f"Removed {removed.label or 'worker'}")
            self._clamp_cursor()

    def _clamp_cursor(self) -> None:
        rows = self._current_rows()
        if not rows:
            self.field_cursors[self.current_section] = 0
            return
        cur = self.field_cursors[self.current_section]
        if cur >= len(rows):
            self.field_cursors[self.current_section] = len(rows) - 1

    def _harness_and_mode_for_field(self, field_key: str) -> Tuple[str, str]:
        parts = field_key.split(".")
        root = parts[0]
        if root == "aihead":
            return self.cfg.ai_head.binding.kind.value, "herdr"
        if root == "ergo":
            if len(parts) >= 2 and parts[1] == "coord":
                return self.ergo_coordinator.kind.value, "herdr"
            if len(parts) >= 2 and parts[1] in ("scout", "architect", "reviewer"):
                return getattr(self.squad, parts[1]).kind.value, "herdr"
            return self.squad.scout.kind.value, "herdr"
        if root == "orch":
            host = self.cfg.orchestration.harness.value
            return host, self._orch_model_mode(host)
        if root == "council":
            if len(parts) >= 2 and parts[1] == "coord":
                return self.cfg.council.coordinator.kind.value, "herdr"
            if len(parts) >= 2 and parts[1] == "scout":
                return self.cfg.council.scout.kind.value, "herdr"
            if len(parts) >= 3 and parts[1] == "member":
                try:
                    idx = int(parts[2])
                    return self.cfg.council.members[idx].kind.value, "herdr"
                except (ValueError, IndexError):
                    pass
            return self.cfg.council.coordinator.kind.value, "herdr"
        if root == "master":
            return self.master_coordinator.kind.value, "herdr"
        if root == "pool":
            if len(parts) >= 2:
                try:
                    idx = int(parts[1])
                    return self.squad.worker_pool[idx].kind.value, "herdr"
                except (ValueError, IndexError):
                    pass
            return self.squad.worker_pool[0].kind.value, "herdr"
        if root == "available":
            mode = (
                self.available_view_mode
                if self.available_view_mode in ("herdr", "native")
                else "herdr"
            )
            harness = self.available_view_harness or self._available_toggle_harness
            if not harness:
                harnesses = self.catalog.harnesses(mode=mode)
                harness = str(harnesses[0].value) if harnesses else "agy"
            return harness, mode
        return "agy", "herdr"

    def _open_custom_model_prompt(self, field_key: str) -> None:
        harness, mode = self._harness_and_mode_for_field(field_key)
        self.text_prompt = TextPromptState(
            field_key=field_key,
            title=f"Add Custom Model ({harness} - {mode})",
            prompt=f"Enter custom model ID for {harness} [{mode}]:",
            value="",
        )
        self._set_status(
            f"Enter custom model ID for {harness} ({mode}). Press Enter to confirm, Esc to cancel."
        )

    def _open_picker(self, row: FieldRow) -> None:
        if not row.options:
            self._set_status(f"No choices available for {row.label}.", error=True)
            return
        if len(row.options) == 1 and row.options[0].value == ADD_CUSTOM_MODEL_VALUE:
            self._open_custom_model_prompt(row.key)
            return
        cursor = 0
        for idx, opt in enumerate(row.options):
            if opt.value == row.current_value:
                cursor = idx
                break
        self.picker = PickerState(
            field_key=row.key,
            title=f"{self.current_section} — Select {row.label}",
            options=row.options,
            cursor=cursor,
        )

    def _cycle_row(self, row: FieldRow, delta: int) -> None:
        if row.kind == "bool":
            self._apply_field_value(row.key, not bool(row.current_value))
            return
        if row.kind == "choice" and row.options:
            concrete = [opt for opt in row.options if opt.value != ADD_CUSTOM_MODEL_VALUE]
            if not concrete:
                return
            cur_idx = 0
            for idx, opt in enumerate(concrete):
                if opt.value == row.current_value:
                    cur_idx = idx
                    break
            next_idx = (cur_idx + delta) % len(concrete)
            self._apply_field_value(row.key, concrete[next_idx].value)

    def validate_and_build(self) -> Tuple[HerdrMasterConfig, Squad]:
        """Validate every skill and return a fresh validated (cfg, squad) pair."""
        from herdr_master.pipeline.launcher import resolve_coordinator_binding

        self._clear_redundant_herdr_efforts()
        cfg = self.cfg.model_copy(deep=True)
        squad = self.squad.model_copy(deep=True)
        for worker in squad.worker_pool:
            worker.effort = None

        # 1. AiHead
        ah_bind = cfg.ai_head.binding
        if not get_harness(ah_bind.kind.value).supports_herdr or not ah_bind.model.strip():
            raise ValueError("AiHead requires a valid Herdr harness and model.")
        resolve_coordinator_binding(ah_bind.kind, ah_bind.model, ah_bind.effort)
        describe_herdr_agent(
            ah_bind.kind.value,
            ah_bind.model,
            _effort_str(ah_bind),
            "master",
        )

        # 2. Ergo & Worker Pool
        errors = validate_squad(squad)
        if errors:
            raise ValueError(" ".join(errors))
        for role in ("scout", "architect", "reviewer"):
            binding = getattr(squad, role)
            ensure_herdr_routable(
                binding.kind.value,
                binding.model,
                _effort_str(binding),
                role=role,
                require_effort=True,
            )
        for worker in squad.worker_pool:
            if worker.enabled:
                ensure_herdr_routable(
                    worker.kind.value,
                    worker.model,
                    None,
                    role=worker.label or "worker",
                    require_effort=False,
                )
        if self.ergo_custom_coordinator:
            kind, model, effort = resolve_coordinator_binding(
                self.ergo_coordinator.kind,
                self.ergo_coordinator.model,
                self.ergo_coordinator.effort,
            )
            describe_herdr_agent(
                kind.value, model, effort.value if effort else None, "master"
            )
            cfg.ergo = ErgoConfig(
                coordinator=HarnessBinding(
                    kind=kind, model=model, effort=effort, label="ergo-coordinator"
                ),
                worker_mode=self.ergo_worker_mode,
            )
        elif self.ergo_worker_mode != "inherit":
            cfg.ergo = ErgoConfig(coordinator=None, worker_mode=self.ergo_worker_mode)
        else:
            cfg.ergo = None

        # 3. Orchestration
        orch = cfg.orchestration
        host = orch.harness.value
        if host not in supported_harnesses(mode="native"):
            raise ValueError(f"Orchestration harness '{host}' does not support native mode.")
        orch.default_harness = orch.harness
        is_agy_swarm = host == "agy" and orch.agy_mode == "swarm"
        for role in ("scout", "architect", "reviewer"):
            model_val = getattr(orch, f"{role}_model") or ""
            if not model_val or any(
                model_val.startswith(f"{k}:") for k in supported_harnesses(mode="herdr")
            ):
                raise ValueError(
                    f"Orchestration {role} model must be a native model ID for {host}."
                )
            if is_agy_swarm:
                describe_herdr_agent(host, model_val, getattr(orch, f"{role}_effort"), "master")
            else:
                problem = native_binding_error(host, model_val, role=role, native_harness=host)
                if problem:
                    raise ValueError(problem)
        # Workers are picked dynamically by the Architect from [models.available] + ratings;
        # stop persisting static worker_models lists.
        orch.worker_models = []
        orch.sync_active_to_harnesses()

        # 4. Council
        for label, b in (("coordinator", cfg.council.coordinator), ("scout", cfg.council.scout)):
            if not get_harness(b.kind.value).supports_herdr or not b.model.strip():
                raise ValueError(f"Council {label} requires a valid Herdr harness and model.")
            describe_herdr_agent(b.kind.value, b.model, _effort_str(b), "master")
        for member in cfg.council.members:
            if not get_harness(member.kind.value).supports_herdr or not member.model.strip():
                raise ValueError(
                    f"Council member '{member.label}' requires a valid Herdr harness and model."
                )
            describe_herdr_agent(member.kind.value, member.model, _effort_str(member), "master")
        CouncilConfig.model_validate(cfg.council.model_dump())

        # 5. Herdr-Master
        if self.master_custom_coordinator:
            kind, model, effort = resolve_coordinator_binding(
                self.master_coordinator.kind,
                self.master_coordinator.model,
                self.master_coordinator.effort,
            )
            describe_herdr_agent(
                kind.value, model, effort.value if effort else None, "master"
            )
            cfg.master_coordinator = HarnessBinding(
                kind=kind, model=model, effort=effort
            )
        else:
            cfg.master_coordinator = None

        # Merge squad role bindings into cfg; omit static [models.worker_pool] since
        # the Architect dynamically picks workers from [models.available] + [models.ratings].
        cfg = squad_to_project_config(squad, existing_config=cfg, include_worker_pool=False)

        validated_cfg = type(cfg).model_validate(cfg.model_dump())
        return validated_cfg, squad

    def save(self) -> bool:
        """Validate and save once via save_squad_as_default + save_project_config."""
        try:
            validated_cfg, squad = self.validate_and_build()
            self._save_squad_fn(squad, self.config_path)
            saved_path = self._save_project_fn(validated_cfg, self.config_path)
        except (CatalogError, InvalidBindingSlugError, ValueError, OSError) as err:
            self._set_status(f"Validation/save error: {err}", error=True)
            return False

        self.cfg = validated_cfg
        self.squad = squad
        self.dirty = False
        self.saved = True
        self.saved_path = saved_path
        self._set_status(f"Saved to {saved_path} (press Q to quit)")
        return True

    def _handle_picker_key(self, key: str) -> None:
        picker = self.picker
        if picker is None:
            return
        items = picker.filtered()
        if key == "ESC":
            self.picker = None
            self._set_status("Picker cancelled.")
            return
        if key == "UP":
            if items:
                picker.cursor = (picker.cursor - 1) % len(items)
            return
        if key == "DOWN":
            if items:
                picker.cursor = (picker.cursor + 1) % len(items)
            return
        if key == "PAGE_UP":
            if items:
                picker.cursor = max(0, picker.cursor - 8)
            return
        if key == "PAGE_DOWN":
            if items:
                picker.cursor = min(len(items) - 1, picker.cursor + 8)
            return
        if key == "HOME":
            picker.cursor = 0
            return
        if key == "END":
            if items:
                picker.cursor = len(items) - 1
            return
        if key == "ENTER":
            if items:
                chosen = items[max(0, min(picker.cursor, len(items) - 1))]
                field_key = picker.field_key
                self.picker = None
                if chosen.value == ADD_CUSTOM_MODEL_VALUE:
                    self._open_custom_model_prompt(field_key)
                else:
                    self._apply_field_value(field_key, chosen.value)
            else:
                self.picker = None
            return
        if key == "BACKSPACE":
            if picker.filter_text:
                picker.filter_text = picker.filter_text[:-1]
                picker.cursor = 0
            return
        if len(key) == 1 and key.isprintable():
            picker.filter_text += key
            picker.cursor = 0

    def _handle_text_prompt_key(self, key: str) -> None:
        prompt_state = self.text_prompt
        if prompt_state is None:
            return
        if key == "ESC":
            self.text_prompt = None
            self._set_status("Prompt cancelled.")
            return
        if key in ("BACKSPACE", "KEY_BACKSPACE", "\b", "127"):
            if prompt_state.value:
                prompt_state.value = prompt_state.value[:-1]
            return
        if key in ("ENTER", "KEY_ENTER", "\n", "\r"):
            field_key = prompt_state.field_key
            clean = prompt_state.value.strip()
            if clean:
                harness, mode = self._harness_and_mode_for_field(field_key)
                try:
                    self.catalog.add_custom_model(harness, clean, mode=mode)
                except ValueError as err:
                    self.text_prompt = None
                    self._set_status(f"Invalid model ID: {err}", error=True)
                    return
                if field_key == "available.add_custom":
                    catalog_models = self.catalog.models(harness, mode=mode, current=None)
                    discovered = [str(opt.value) for opt in catalog_models]
                    self.cfg.models.available = materialize_toggle(
                        self.cfg.models.available,
                        mode,
                        harness,
                        clean,
                        True,
                        discovered,
                    )
                    self.dirty = True
                    self._set_status(f"Added custom model {clean} to {harness} ({mode}) allowlist")
                else:
                    if enabled_model_ids(self.cfg.models.available, mode, harness) is not None:
                        catalog_models = self.catalog.models(harness, mode=mode, current=None)
                        discovered = [str(opt.value) for opt in catalog_models]
                        self.cfg.models.available = materialize_toggle(
                            self.cfg.models.available,
                            mode,
                            harness,
                            clean,
                            True,
                            discovered,
                        )
                    self._apply_field_value(field_key, clean)
                    self._set_status(f"Set {field_key} to custom model {clean}")
                self.text_prompt = None
            else:
                self.text_prompt = None
                self._set_status("Prompt cancelled.")
            return
        if len(key) == 1 and (32 <= ord(key) <= 126 or key.isprintable()):
            prompt_state.value += key

    def handle_key(self, key: str) -> None:
        """Process one normalized key event."""
        if self.done:
            return

        if key == "CTRL_C":
            raise KeyboardInterrupt

        if self.rating_inspector is not None:
            if key in ("ESC", "ENTER", "SPACE", "v", "V", "q", "Q"):
                self.rating_inspector = None
                self._set_status("Closed rating inspector.")
            return

        if self.text_prompt is not None:
            self._handle_text_prompt_key(key)
            return

        if self.picker is not None:
            self._handle_picker_key(key)
            return

        if key in ("q", "Q"):
            self.done = True
            return

        if key in ("s", "S", "CTRL_S"):
            self.save()
            return

        if key in ("r", "R"):
            self._load_initial_state()
            self.dirty = False
            self._clamp_cursor()
            self._set_status("Reset edits to saved configuration.")
            return

        if key in ("1", "2", "3", "4", "5", "6"):
            self.section_index = int(key) - 1
            self.focus_pane = "options"
            self._clamp_cursor()
            return

        if key == "TAB":
            self.focus_pane = "options" if self.focus_pane == "sections" else "sections"
            return

        if key == "SHIFT_TAB":
            self.focus_pane = "sections" if self.focus_pane == "options" else "options"
            return

        if self.focus_pane == "sections":
            if key in ("UP", "k"):
                self.section_index = (self.section_index - 1) % len(SECTIONS)
                self._clamp_cursor()
            elif key in ("DOWN", "j"):
                self.section_index = (self.section_index + 1) % len(SECTIONS)
                self._clamp_cursor()
            elif key in ("ENTER", "RIGHT", "SPACE"):
                self.focus_pane = "options"
                self._clamp_cursor()
            return

        # focus_pane == "options"
        rows = self._current_rows()
        if not rows:
            return
        row = self._current_row()
        if key in ("UP", "k"):
            cur = self.field_cursors[self.current_section]
            self.field_cursors[self.current_section] = (cur - 1) % len(rows)
        elif key in ("DOWN", "j"):
            cur = self.field_cursors[self.current_section]
            self.field_cursors[self.current_section] = (cur + 1) % len(rows)
        elif key == "HOME":
            self.field_cursors[self.current_section] = 0
        elif key == "END":
            self.field_cursors[self.current_section] = len(rows) - 1
        elif key == "ESC":
            self.focus_pane = "sections"
        elif key == "LEFT" and row is not None:
            self._cycle_row(row, -1)
        elif key == "RIGHT" and row is not None:
            self._cycle_row(row, 1)
        elif key in ("ENTER", "SPACE") and row is not None:
            if row.kind == "bool":
                self._apply_field_value(row.key, not bool(row.current_value))
            elif row.kind == "choice":
                self._open_picker(row)
            elif row.kind == "action":
                self._run_action(row.key)
        elif key in ("e", "E") and self.current_section == "models":
            self._run_action("available.evaluate")
        elif key in ("t", "T") and self.current_section == "models":
            self._open_tier_picker_for_current_available()
        elif key in ("v", "V") and self.current_section == "models":
            self._inspect_current_available_rating()
        elif key in ("a", "A"):
            if self.current_section == "council":
                self._run_action("council.member_add")
            elif self.current_section == "models":
                self._run_action("available.add_custom")
        elif key in ("d", "D", "DELETE", "BACKSPACE") and row is not None:
            if row.deletable_id is not None:
                self._delete_item(*row.deletable_id)

    def drive(self, keys: Sequence[str]) -> "SetupTuiApp":
        """Drive the TUI headlessly with a sequence of keys (no real terminal required)."""
        for key in keys:
            if self.done:
                break
            self.handle_key(key)
        return self

    def _section_summary(self, section: str) -> str:
        if section == "aihead":
            b = self.cfg.ai_head.binding
            text = f"{b.kind.value}:{b.model}"
        elif section == "ergo":
            text = (
                f"s={self.squad.scout.kind.value} "
                f"a={self.squad.architect.kind.value} "
                f"m={self.ergo_worker_mode}"
            )
        elif section == "orchestration":
            text = f"{self.cfg.orchestration.harness.value} (native)"
        elif section == "council":
            enabled = sum(1 for m in self.cfg.council.members if m.enabled)
            text = f"{enabled} members"
        elif section == "herdr-master":
            if not self.master_custom_coordinator:
                text = "default (scout)"
            else:
                text = f"{self.master_coordinator.kind.value}:{self.master_coordinator.model}"
        elif section == "models":
            text = self._available_enabled_summary()
        else:
            text = ""
        return text if len(text) <= 20 else text[:19] + "…"

    def _render_sidebar(self) -> Panel:
        lines: List[Text] = []
        for idx, (sec_id, sec_label) in enumerate(SECTIONS):
            selected = idx == self.section_index
            marker = ">" if selected else " "
            line = Text(no_wrap=True, overflow="ellipsis")
            style = "bold cyan reverse" if (selected and self.focus_pane == "sections") else (
                "bold cyan" if selected else "white"
            )
            line.append(f"{marker} [{idx + 1}] {sec_label:<13}", style=style)
            lines.append(line)
            summary_line = Text(
                f"      {self._section_summary(sec_id)}",
                style="dim",
                no_wrap=True,
                overflow="ellipsis",
            )
            lines.append(summary_line)

        lines.append(Text(""))
        lines.append(Text("Catalog & Live Models", style="bold yellow", no_wrap=True))
        lines.append(
            Text(
                f"  Herdr: {len(self.catalog.herdr_harnesses)}  "
                f"Native: {len(self.catalog.native_harnesses)}",
                style="dim",
                no_wrap=True,
            )
        )
        if self.catalog.live_counts:
            counts = " ".join(f"{k}:{v}" for k, v in sorted(self.catalog.live_counts.items()))
            if len(counts) > 20:
                counts = counts[:19] + "…"
            lines.append(Text(f"  Live: {counts}", style="dim", no_wrap=True))
        lines.append(Text(""))
        lines.append(Text("[S] Save  [R] Reset", style="green", no_wrap=True))
        lines.append(Text("[Q] Quit", style="yellow", no_wrap=True))

        body = Text("\n").join(lines)
        border = "cyan" if self.focus_pane == "sections" else "bright_black"
        return Panel(body, title="Sections", border_style=border, box=ROUNDED)

    def _render_picker(self, body_height: int) -> Panel:
        assert self.picker is not None
        picker = self.picker
        items = picker.filtered()
        if items:
            picker.cursor = max(0, min(picker.cursor, len(items) - 1))
        else:
            picker.cursor = 0

        list_height = max(4, body_height - 6)
        if picker.cursor < picker.scroll_offset:
            picker.scroll_offset = picker.cursor
        elif picker.cursor >= picker.scroll_offset + list_height:
            picker.scroll_offset = picker.cursor - list_height + 1

        visible = items[picker.scroll_offset : picker.scroll_offset + list_height]
        lines: List[Text] = []
        filter_line = Text()
        filter_line.append("Filter: ", style="bold yellow")
        filter_line.append(
            picker.filter_text or "(type to filter)",
            style="white" if picker.filter_text else "dim",
        )
        filter_line.append(f"  [{len(items)}/{len(picker.options)}]", style="dim")
        lines.append(filter_line)
        lines.append(Text(""))

        if not visible:
            lines.append(Text("  No matching options.", style="red"))
        else:
            for offset, opt in enumerate(visible):
                abs_idx = picker.scroll_offset + offset
                selected = abs_idx == picker.cursor
                row_text = Text()
                row_text.append(
                    "> " if selected else "  ",
                    style="bold cyan" if selected else "dim",
                )
                row_text.append(
                    opt.label,
                    style="bold cyan reverse" if selected else "white",
                )
                if opt.detail:
                    row_text.append(f"  — {opt.detail}", style="dim")
                lines.append(row_text)

        content = Text("\n").join(lines)
        return Panel(
            content,
            title=picker.title,
            subtitle="↑↓/PgUp/PgDn Move  Enter Select  Esc Cancel",
            border_style="yellow",
            box=ROUNDED,
        )

    def _render_text_prompt(self, body_height: int) -> Panel:
        assert self.text_prompt is not None
        prompt_state = self.text_prompt
        lines: List[Text] = [
            Text(""),
            Text(f"  {prompt_state.prompt}", style="bold white"),
            Text(""),
        ]
        input_line = Text()
        input_line.append("  > ", style="bold cyan")
        if prompt_state.value:
            input_line.append(prompt_state.value, style="bold cyan reverse")
        else:
            input_line.append("(type model ID)", style="dim")
        lines.append(input_line)
        lines.append(Text(""))
        lines.append(Text("  Type the model ID and press Enter to confirm.", style="dim"))
        lines.append(Text("  Press Esc to cancel.", style="dim"))
        content = Text("\n").join(lines)
        return Panel(
            content,
            title=prompt_state.title,
            subtitle="Enter Confirm  Esc Cancel  Backspace Delete",
            border_style="cyan",
            box=ROUNDED,
        )

    def _render_rating_inspector(self) -> Panel:
        assert self.rating_inspector is not None
        insp = self.rating_inspector
        lines: List[Text] = [
            Text(f"  Model: {insp.harness}:{insp.model_id}", style="bold cyan"),
            Text(""),
        ]
        if insp.rating is None:
            lines.append(Text("  Status: Unrated", style="yellow"))
            lines.append(
                Text(
                    "  Press [E] in Available or run `aihead catalog evaluate` to score.",
                    style="dim",
                )
            )
        else:
            r = insp.rating
            lines.append(
                Text(
                    f"  Intelligence: {r.intelligence:>3}/100    Coding:   {r.coding:>3}/100",
                    style="white",
                )
            )
            lines.append(
                Text(
                    f"  Agentic:      {r.agentic:>3}/100    Speed:    {r.speed:>3}/100",
                    style="white",
                )
            )
            lines.append(
                Text(
                    f"  Cost Eff:     {r.cost_efficiency:>3}/100    Context:  "
                    f"{r.context_handling:>3}/100",
                    style="white",
                )
            )
            lines.append(Text(""))
            tier_source = (
                "manual" if r.worker_tier_override
                else "derived" if r.worker_tier is None
                else "auto"
            )
            lines.append(
                Text(
                    f"  Family: {r.family}  |  billing_tier={r.billing_tier}  |  "
                    f"worker_tier={effective_worker_tier(r)} ({tier_source})  |  "
                    f"Context Window: {format_context_window(r.context_window)}",
                    style="cyan",
                )
            )
            eff = ", ".join(r.supported_efforts) if r.supported_efforts else "fixed / none"
            roles = ", ".join(r.recommended_roles) if r.recommended_roles else "-"
            lines.append(Text(f"  Efforts: {eff}  |  Roles: {roles}", style="white"))
            lines.append(
                Text(
                    f"  Source: {r.source}  |  Confidence: {r.confidence}  |  "
                    f"Manual: {str(r.manual_override).lower()}",
                    style="dim",
                )
            )
            if r.notes:
                lines.append(Text(f"  Notes: {r.notes}", style="dim"))
            lines.append(Text(f"  Evaluated: {r.evaluated_at}", style="dim"))

        return Panel(
            Text("\n").join(lines),
            title="Model Rating Details",
            subtitle="Esc / Enter / V Close",
            border_style="cyan",
            box=ROUNDED,
        )

    def _render_options(self, body_height: int) -> Panel:
        if self.rating_inspector is not None:
            return self._render_rating_inspector()
        if self.text_prompt is not None:
            return self._render_text_prompt(body_height)
        if self.picker is not None:
            return self._render_picker(body_height)

        sec = self.current_section
        rows = self._current_rows()
        cursor = max(0, min(self.field_cursors[sec], len(rows) - 1)) if rows else 0
        self.field_cursors[sec] = cursor

        # Build visual lines with group headers and remember which visual line holds each row.
        visual_lines: List[Tuple[Optional[int], Text]] = []
        last_group: Optional[str] = None
        cursor_visual_idx = 0

        for idx, row in enumerate(rows):
            if row.group != last_group:
                if last_group is not None:
                    visual_lines.append((None, Text("")))
                visual_lines.append((None, Text(f"  {row.group}", style="bold magenta")))
                last_group = row.group

            is_cur = idx == cursor and self.focus_pane == "options"
            if idx == cursor:
                cursor_visual_idx = len(visual_lines)

            line = Text()
            line.append("  > " if idx == cursor else "    ", style="bold cyan" if is_cur else "dim")
            if row.kind == "action":
                action_style = "bold green reverse" if is_cur else "bold green"
                line.append(f"[{row.label}]", style=action_style)
                line.append(f"  ({row.value_text})", style="dim")
            else:
                lbl_style = "bold white" if is_cur else "white"
                val_style = "bold cyan reverse" if is_cur else "cyan"
                line.append(f"{row.label:<22}", style=lbl_style)
                line.append(": ", style="dim")
                line.append(f"[ {row.value_text} ]", style=val_style)
                if row.badge:
                    badge_style = "yellow" if row.badge == "[unrated]" else "dim green"
                    line.append(f"  {row.badge}", style=badge_style)
            visual_lines.append((idx, line))

        viewport = max(5, body_height - 4)
        scroll = self.field_scroll[sec]
        if cursor_visual_idx < scroll + 1:
            scroll = max(0, cursor_visual_idx - 1)
        elif cursor_visual_idx >= scroll + viewport:
            scroll = cursor_visual_idx - viewport + 1
        self.field_scroll[sec] = scroll

        sliced = [txt for _, txt in visual_lines[scroll : scroll + viewport]]
        body = Text("\n").join(sliced)
        border = "cyan" if self.focus_pane == "options" else "bright_black"
        subtitle = rows[cursor].hint if rows else ""
        return Panel(
            body,
            title=f"Section: {sec}",
            subtitle=subtitle,
            border_style=border,
            box=ROUNDED,
        )

    def render(self, width: int = 100, height: int = 30, *, ansi: bool = False) -> str:
        """Render the complete single-screen setup UI to a string."""
        width = max(72, width)
        height = max(20, height)

        target_name = str(self.config_path or ".herdr-master.toml")
        dirty_mark = " *" if self.dirty else ""
        header_txt = Text()
        header_txt.append("Herdr-Master Unified Skill Setup", style="bold white")
        header_txt.append("   |   ", style="dim")
        header_txt.append(f"Config: {target_name}{dirty_mark}", style="cyan")

        footer_style = "bold red" if self.status_is_error else "green"
        footer_txt = Text(self.status_message, style=footer_style)

        layout = Layout()
        layout.split_column(
            Layout(Panel(header_txt, box=ROUNDED, border_style="blue"), size=3),
            Layout(name="body", ratio=1),
            Layout(Panel(footer_txt, box=ROUNDED, border_style="blue"), size=3),
        )
        body_height = max(10, height - 6)
        layout["body"].split_row(
            Layout(self._render_sidebar(), size=28),
            Layout(self._render_options(body_height), ratio=1),
        )

        buf = io.StringIO()
        console = Console(
            file=buf,
            force_terminal=ansi,
            no_color=not ansi,
            color_system="standard" if ansi else None,
            width=width,
            height=height,
            legacy_windows=False,
        )
        console.print(layout, end="")
        return buf.getvalue()


def _decode_key(fd: int) -> str:
    """Read one normalized key event from a raw terminal file descriptor."""
    ch = os.read(fd, 1)
    if not ch:
        return ""
    if ch == b"\x03":
        return "CTRL_C"
    if ch == b"\x13":
        return "CTRL_S"
    if ch in (b"\r", b"\n"):
        return "ENTER"
    if ch == b"\t":
        return "TAB"
    if ch == b" ":
        return "SPACE"
    if ch in (b"\x7f", b"\x08"):
        return "BACKSPACE"
    if ch == b"\x1b":
        ready, _, _ = select.select([fd], [], [], 0.04)
        if not ready:
            return "ESC"
        seq = os.read(fd, 8)
        if seq.startswith(b"[A") or seq.startswith(b"OA"):
            return "UP"
        if seq.startswith(b"[B") or seq.startswith(b"OB"):
            return "DOWN"
        if seq.startswith(b"[C") or seq.startswith(b"OC"):
            return "RIGHT"
        if seq.startswith(b"[D") or seq.startswith(b"OD"):
            return "LEFT"
        if seq.startswith(b"[Z"):
            return "SHIFT_TAB"
        if seq.startswith(b"[5~"):
            return "PAGE_UP"
        if seq.startswith(b"[6~"):
            return "PAGE_DOWN"
        if seq.startswith(b"[H") or seq.startswith(b"[1~"):
            return "HOME"
        if seq.startswith(b"[F") or seq.startswith(b"[4~"):
            return "END"
        if seq.startswith(b"[3~"):
            return "DELETE"
        return "ESC"

    # Handle UTF-8 multi-byte character if needed.
    first = ch[0]
    if first >= 0xC0:
        extra = 1 if (first & 0xE0) == 0xC0 else (2 if (first & 0xF0) == 0xE0 else 3)
        ch += os.read(fd, extra)
    try:
        return ch.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def run_terminal_tui(app: SetupTuiApp) -> SetupTuiApp:
    """Run SetupTuiApp interactively on the active terminal."""
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    resized = False

    def _on_winch(_signum: int, _frame: Any) -> None:
        nonlocal resized
        resized = True

    old_winch = signal.getsignal(signal.SIGWINCH)
    signal.signal(signal.SIGWINCH, _on_winch)
    sys.stdout.write("\x1b[?1049h\x1b[?25l")
    sys.stdout.flush()

    try:
        tty.setcbreak(fd)
        while not app.done:
            size = shutil.get_terminal_size((100, 30))
            frame = app.render(width=size.columns, height=size.lines, ansi=True)
            sys.stdout.write("\x1b[H" + frame + "\x1b[J")
            sys.stdout.flush()
            resized = False
            while True:
                ready, _, _ = select.select([fd], [], [], 0.25)
                if resized:
                    break
                if ready:
                    key = _decode_key(fd)
                    if key:
                        app.handle_key(key)
                    break
    finally:
        signal.signal(signal.SIGWINCH, old_winch)
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
    return app


def run_setup_tui(
    config_path: Optional[Path] = None,
    *,
    catalog: Optional[ModelCatalog] = None,
    driver: Optional[Callable[[SetupTuiApp], SetupTuiApp]] = None,
    save_project_fn: Optional[Callable[[HerdrMasterConfig, Optional[Path]], Path]] = None,
    save_squad_fn: Optional[Callable[[Squad, Optional[Path]], Path]] = None,
) -> SetupTuiApp:
    """Construct and run the single-screen setup TUI (live terminal or test driver)."""
    app = SetupTuiApp(
        config_path=config_path,
        catalog=catalog,
        save_project_fn=save_project_fn,
        save_squad_fn=save_squad_fn,
    )
    if driver is not None:
        return driver(app)
    if not sys.stdin.isatty():
        raise RuntimeError(
            "Interactive setup TUI requires a terminal. "
            "Use `aihead setup --defaults --yes` for non-interactive setup."
        )
    return run_terminal_tui(app)
