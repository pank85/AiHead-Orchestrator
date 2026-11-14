"""Live catalog and available-model discovery for the unified setup TUI."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from herdr_master.core.catalog import (
    _AGY_TIERS,
    _EFFORT_BY_FLASH,
    _FLASH_BASE,
    _allowed_efforts,
    compatibility_presets,
    default_squad_data,
    effort_phrase,
    get_harness,
    supported_harnesses,
)
from herdr_master.core.config import DEFAULT_ORCHESTRATION_MODELS, HerdrMasterConfig
from herdr_master.core.models import Squad

_PROBE_TIMEOUT_SEC = 8.0

_CLAUDE_FALLBACK_MODELS: tuple[str, ...] = (
    "claude-sonnet-4-6",
    "claude-opus-4-6",
    "claude-haiku-4-5",
    "sonnet",
    "opus",
    "haiku",
)

_HERMES_FALLBACK_MODELS: tuple[str, ...] = (
    "nousresearch/hermes-4-70b",
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-5.4",
)

_CODEX_FALLBACK_MODELS: tuple[str, ...] = (
    "gpt-5.4",
    "gpt-5.3-codex",
    "o3",
)

_GEMINI_FALLBACK_MODELS: tuple[str, ...] = (
    "gemini-2.5-pro",
    "gemini-2.5-flash",
)


@dataclass(frozen=True)
class ChoiceOption:
    """One selectable item in a harness, model, effort, or option picker."""

    value: Any
    label: str
    detail: str = ""


def _parse_opencode_models(stdout: str) -> List[ChoiceOption]:
    options: List[ChoiceOption] = []
    for raw in stdout.splitlines():
        slug = raw.strip()
        if not slug:
            continue
        options.append(ChoiceOption(value=slug, label=slug, detail="opencode live"))
    return options


def _parse_pi_models(stdout: str) -> List[ChoiceOption]:
    options: List[ChoiceOption] = []
    for idx, raw in enumerate(stdout.splitlines()):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if idx == 0 and parts[0].lower() == "provider":
            continue
        if len(parts) < 2:
            continue
        provider, model = parts[0], parts[1]
        # Catalog explicitly refuses litellm routes in favor of native providers.
        if provider.lower() == "litellm":
            continue
        slug = f"{provider}/{model}"
        meta: List[str] = ["pi live"]
        if len(parts) >= 3:
            meta.append(f"ctx {parts[2]}")
        if len(parts) >= 5:
            meta.append(f"thinking {parts[4]}")
        options.append(ChoiceOption(value=slug, label=slug, detail=" | ".join(meta)))
    return options


def _parse_agy_models(stdout: str) -> List[ChoiceOption]:
    options: List[ChoiceOption] = []
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or line.lower().startswith("fetching "):
            continue
        if "\t" in line:
            slug, display = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            slug, display = parts[0], parts[1]
        slug = slug.strip()
        display = display.strip()
        if not slug:
            continue
        options.append(ChoiceOption(value=slug, label=slug, detail=display))
    return options


def _run_probe(cmd: Sequence[str], timeout: float = _PROBE_TIMEOUT_SEC) -> Optional[str]:
    try:
        proc = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _probe_hermes_config_models() -> List[ChoiceOption]:
    cfg_path = Path(os.path.expanduser("~/.hermes/config.yaml"))
    if not cfg_path.is_file():
        return []
    try:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        data = yaml.load(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    options: List[ChoiceOption] = []
    model_section = data.get("model")
    if isinstance(model_section, dict):
        default = model_section.get("default")
        provider = model_section.get("provider")
        if isinstance(default, str) and default.strip():
            val = default.strip()
            if isinstance(provider, str) and provider.strip() and "/" not in val:
                val = f"{provider.strip()}/{val}"
            options.append(ChoiceOption(value=val, label=val, detail="hermes config"))
    elif isinstance(model_section, str) and model_section.strip():
        val = model_section.strip()
        options.append(ChoiceOption(value=val, label=val, detail="hermes config"))
    return options


def _dedupe_options(items: Sequence[ChoiceOption]) -> List[ChoiceOption]:
    seen: set[Any] = set()
    result: List[ChoiceOption] = []
    for item in items:
        if item.value in seen:
            continue
        seen.add(item.value)
        result.append(item)
    return result


def _catalog_herdr_models() -> Dict[str, List[ChoiceOption]]:
    by_kind: Dict[str, List[ChoiceOption]] = {}

    def add(kind: str, model: str, detail: str) -> None:
        by_kind.setdefault(kind, []).append(ChoiceOption(value=model, label=model, detail=detail))

    squad = default_squad_data()
    for role in ("scout", "architect", "reviewer"):
        rec = squad[role]
        add(str(rec["kind"]), str(rec["model"]), f"catalog {role} default")
    for worker in squad["worker_pool"]:
        add(str(worker["kind"]), str(worker["model"]), f"catalog {worker['label']}")

    add("agy", _FLASH_BASE, "agy Flash worker family (effort per task)")
    for slug, eff in _EFFORT_BY_FLASH.items():
        add("agy", slug, f"catalog agy flash ({eff})")

    for preset in compatibility_presets():
        kind = str(preset.get("kind", ""))
        model = preset.get("model")
        if kind and isinstance(model, str) and model:
            add(kind, model, str(preset.get("note") or "catalog preset"))

    # Include known opencode defaults verified for setup.
    add("opencode", "opencode/beam-free", "setup default scout")
    add("opencode", "openai/gpt-6.1-sol", "setup default architect/reviewer")
    add("opencode", "opencode/deepseek-v4.1-flash-free", "setup default coder")

    for model in _CLAUDE_FALLBACK_MODELS:
        add("claude", model, "claude catalog/alias")
    for model in _HERMES_FALLBACK_MODELS:
        add("hermes", model, "hermes catalog fallback")
    for model in _CODEX_FALLBACK_MODELS:
        add("codex", model, "codex catalog fallback")
    for model in _GEMINI_FALLBACK_MODELS:
        add("gemini", model, "gemini catalog fallback")

    return by_kind


def _catalog_native_models() -> Dict[str, List[ChoiceOption]]:
    by_kind: Dict[str, List[ChoiceOption]] = {
        "agy": [
            ChoiceOption(value=tier, label=tier, detail="native agy tier ID")
            for tier in _AGY_TIERS
        ]
    }
    for kind, roles in DEFAULT_ORCHESTRATION_MODELS.items():
        if kind == "agy":
            continue
        items = by_kind.setdefault(kind, [])
        for role, model in roles.items():
            items.append(
                ChoiceOption(value=model, label=model, detail=f"native {role} default")
            )
    for model in _CLAUDE_FALLBACK_MODELS:
        by_kind.setdefault("claude", []).append(
            ChoiceOption(value=model, label=model, detail="claude alias")
        )
    return by_kind


def _configured_models(
    cfg: Optional[HerdrMasterConfig], squad: Optional[Squad]
) -> tuple[Dict[str, List[ChoiceOption]], Dict[str, List[ChoiceOption]]]:
    herdr: Dict[str, List[ChoiceOption]] = {}
    native: Dict[str, List[ChoiceOption]] = {}

    def add_herdr(kind: str, model: Optional[str]) -> None:
        if model and model.strip():
            m = model.strip()
            herdr.setdefault(kind, []).append(
                ChoiceOption(value=m, label=m, detail="configured")
            )

    if squad is not None:
        for binding in (squad.scout, squad.architect, squad.reviewer, *squad.worker_pool):
            add_herdr(binding.kind.value, binding.model)

    if cfg is not None:
        add_herdr(cfg.ai_head.binding.kind.value, cfg.ai_head.binding.model)
        if cfg.ergo and cfg.ergo.coordinator:
            add_herdr(cfg.ergo.coordinator.kind.value, cfg.ergo.coordinator.model)
        if cfg.master_coordinator:
            add_herdr(cfg.master_coordinator.kind.value, cfg.master_coordinator.model)
        for binding in (cfg.council.coordinator, cfg.council.scout, *cfg.council.members):
            add_herdr(binding.kind.value, binding.model)

        orch = cfg.orchestration
        active_kind = orch.harness.value
        for model in (
            orch.scout_model,
            orch.architect_model,
            orch.reviewer_model,
            *orch.worker_models,
        ):
            if model and model.strip():
                native.setdefault(active_kind, []).append(
                    ChoiceOption(value=model.strip(), label=model.strip(), detail="configured")
                )
        for h_kind, settings in orch.harnesses.items():
            for model in (
                settings.scout_model,
                settings.architect_model,
                settings.reviewer_model,
                *settings.worker_models,
            ):
                if model and model.strip():
                    native.setdefault(h_kind, []).append(
                        ChoiceOption(value=model.strip(), label=model.strip(), detail="configured")
                    )

    return herdr, native


@dataclass
class ModelCatalog:
    """Selectable harnesses, models, and efforts built from live catalog + CLI probes."""

    herdr_harnesses: List[ChoiceOption]
    native_harnesses: List[ChoiceOption]
    herdr_models: Dict[str, List[ChoiceOption]] = field(default_factory=dict)
    native_models: Dict[str, List[ChoiceOption]] = field(default_factory=dict)
    live_counts: Dict[str, int] = field(default_factory=dict)

    def harnesses(self, mode: str = "herdr") -> List[ChoiceOption]:
        return list(self.native_harnesses if mode == "native" else self.herdr_harnesses)

    def models(
        self, kind: str, *, mode: str = "herdr", current: Optional[str] = None
    ) -> List[ChoiceOption]:
        pool = self.native_models if mode == "native" else self.herdr_models
        items = list(pool.get(kind, ()))
        if current and current.strip():
            # Native agy only permits tier IDs; avoid injecting invalid exact slugs into agy native.
            if not (mode == "native" and kind == "agy" and current not in _AGY_TIERS):
                if not (mode == "herdr" and kind == "agy" and current in _AGY_TIERS):
                    if all(opt.value != current for opt in items):
                        items.insert(
                            0, ChoiceOption(value=current, label=current, detail="current")
                        )
        return items

    def efforts(
        self,
        kind: str,
        model: str,
        *,
        mode: str = "herdr",
        allow_none: bool = True,
    ) -> List[ChoiceOption]:
        allowed = _allowed_efforts(kind, model, mode=mode)
        options: List[ChoiceOption] = []
        if allow_none or not allowed:
            options.append(ChoiceOption(value=None, label="none", detail="unset / default"))
        for eff in allowed:
            phrase = effort_phrase(eff)
            options.append(
                ChoiceOption(value=eff, label=eff, detail=phrase or "")
            )
        return options


def _build_harness_options(mode: str) -> List[ChoiceOption]:
    options: List[ChoiceOption] = []
    for kind in supported_harnesses(mode=mode):
        spec = get_harness(kind)
        options.append(ChoiceOption(value=spec.kind, label=spec.kind, detail=spec.label))
    return options


def discover_model_catalog(
    cfg: Optional[HerdrMasterConfig] = None,
    squad: Optional[Squad] = None,
    *,
    probe_live: bool = True,
) -> ModelCatalog:
    """Build a ModelCatalog from `core.catalog` plus live CLI model discovery."""
    herdr_base = _catalog_herdr_models()
    native_base = _catalog_native_models()
    cfg_herdr, cfg_native = _configured_models(cfg, squad)

    live_results: Dict[str, List[ChoiceOption]] = {}
    if probe_live:
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_oc = pool.submit(_run_probe, ["opencode", "models"])
            fut_pi = pool.submit(_run_probe, ["pi", "--list-models"])
            fut_agy = pool.submit(_run_probe, ["agy", "models"])

            oc_out = fut_oc.result()
            pi_out = fut_pi.result()
            agy_out = fut_agy.result()

        if oc_out:
            live_results["opencode"] = _parse_opencode_models(oc_out)
        if pi_out:
            live_results["pi"] = _parse_pi_models(pi_out)
        if agy_out:
            live_results["agy"] = _parse_agy_models(agy_out)
        hermes_cfg = _probe_hermes_config_models()
        if hermes_cfg:
            live_results["hermes"] = hermes_cfg

    herdr_models: Dict[str, List[ChoiceOption]] = {}
    for kind in supported_harnesses(mode="herdr"):
        combined: List[ChoiceOption] = []
        combined.extend(cfg_herdr.get(kind, ()))
        combined.extend(herdr_base.get(kind, ()))
        combined.extend(live_results.get(kind, ()))
        # Exclude native-only tier IDs from Herdr agy selection.
        if kind == "agy":
            combined = [opt for opt in combined if opt.value not in _AGY_TIERS]
        herdr_models[kind] = _dedupe_options(combined)

    native_models: Dict[str, List[ChoiceOption]] = {}
    for kind in supported_harnesses(mode="native"):
        if kind == "agy":
            native_models[kind] = list(native_base["agy"])
            continue
        combined = []
        combined.extend(cfg_native.get(kind, ()))
        combined.extend(native_base.get(kind, ()))
        combined.extend(live_results.get(kind, ()))
        native_models[kind] = _dedupe_options(combined)

    live_counts = {kind: len(items) for kind, items in live_results.items()}
    return ModelCatalog(
        herdr_harnesses=_build_harness_options("herdr"),
        native_harnesses=_build_harness_options("native"),
        herdr_models=herdr_models,
        native_models=native_models,
        live_counts=live_counts,
    )
