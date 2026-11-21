"""Two-stage smart triage & deterministic route validation for AiHead.

Stage 1 (Mini-Scout): Cheap, fast read-only subagent / headless runner surveys
the workspace and returns a structured :class:`TriageScoutReport`.
Stage 2 (Routing Decider): Strong read-only subagent / headless runner combines
the user request, scout report, rated available-model table, and routing profile
into a :class:`RoutingPlan`.
Gate (:func:`validate_route`): Deterministically verifies every chosen binding
against ``[models.available]``, ``core/catalog.py`` rules, and pipeline invariants
before AiHead dispatches anything.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from herdr_master.cli.setup_catalog import ModelCatalog, discover_model_catalog
from herdr_master.core.available_models import AvailableModels, enabled_model_ids
from herdr_master.core.catalog import (
    CatalogError,
    ensure_herdr_routable,
    get_harness,
    native_binding_error,
    supported_harnesses,
)
from herdr_master.core.config import (
    AiHeadConfig,
    load_effective_models_state,
    load_effective_project_config,
    validate_routing_profile,
)
from herdr_master.core.model_ratings import (
    ModelRating,
    ModelRatingsMap,
    detect_model_family,
    format_rated_models_table,
    infer_supported_efforts,
    lookup_rating,
)
from herdr_master.core.models import EffortLevel, HarnessBinding
from herdr_master.core.slug import InvalidBindingSlugError, parse_binding
from herdr_master.pipeline.ai_head import SUPPORTED_CLI_HARNESSES, build_headless_cli_argv
from herdr_master.pipeline.model_evaluator import _extract_text_from_cli_output

__all__ = [
    "NATIVE_TRIAGE_HARNESSES",
    "RouteValidationResult",
    "RoutingPlan",
    "TriageModelsSelection",
    "TriageRunResult",
    "TriageScoutReport",
    "build_mini_scout_prompt",
    "build_router_prompt",
    "parse_json_payload",
    "run_triage",
    "select_triage_models",
    "validate_route",
]

NATIVE_TRIAGE_HARNESSES = frozenset({"agy", "pi", "claude", "opencode"})
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class TriageScoutReport(BaseModel):
    """Structured read-only survey produced by Stage-1 cheap Mini-Scout."""

    model_config = ConfigDict(extra="allow")

    scope_size: Literal["atomic", "small", "medium", "large"] = "small"
    estimated_files: int = Field(default=1, ge=0)
    touched_areas: List[str] = Field(default_factory=list)
    requires_design: bool = False
    ambiguity: Literal["low", "medium", "high"] = "low"
    ambiguity_reasons: List[str] = Field(default_factory=list)
    multi_job_detected: bool = False
    distinct_jobs: List[str] = Field(default_factory=list)
    risk_flags: List[str] = Field(default_factory=list)
    test_framework: Optional[str] = None
    summary: str = ""


class RoutingPlan(BaseModel):
    """Structured workflow + role binding decision produced by Stage-2 Router."""

    model_config = ConfigDict(extra="allow")

    workflow: Literal["direct", "orchestration", "ergo", "council", "herdr-master"]
    confidence: Literal["high", "medium", "low"] = "medium"
    needs_user: bool = False
    question: Optional[str] = None
    rationale: str = ""
    target_mode: Literal["herdr", "native"] = "herdr"
    target_harness: Optional[str] = None
    worker_mode: Literal["balanced", "quality", "fast", "frugal"] = "balanced"

    coordinator: Optional[str] = None
    scout: Optional[str] = None
    architect: Optional[str] = None
    reviewer: Optional[str] = None
    council_members: List[str] = Field(default_factory=list)
    direct_binding: Optional[str] = None
    direct_reviewer: Optional[str] = None
    sub_jobs: List[Dict[str, str]] = Field(default_factory=list)


class TriageModelsSelection(BaseModel):
    """Deterministic selection of Stage-1 Mini-Scout and Stage-2 Router models."""

    host_harness: str
    adapter: Literal["native_subagent", "headless_cli"]
    scout_binding: str
    scout_effort: Optional[str] = None
    router_binding: str
    router_effort: Optional[str] = None
    agy_mode: Optional[str] = None
    notes: List[str] = Field(default_factory=list)


class RouteValidationResult(BaseModel):
    """Deterministic validation result for a candidate :class:`RoutingPlan`."""

    valid: bool
    errors: List[str] = Field(default_factory=list)
    advisories: List[str] = Field(default_factory=list)
    normalized_plan: Optional[RoutingPlan] = None


class TriageRunResult(BaseModel):
    """Complete result of running :func:`run_triage` (headless CLI or orchestration)."""

    ok: bool
    triage_models: TriageModelsSelection
    scout_report: Optional[TriageScoutReport] = None
    routing_plan: Optional[RoutingPlan] = None
    validation: Optional[RouteValidationResult] = None
    retries_used: int = 0
    artifact_dir: Optional[str] = None
    error: Optional[str] = None


def _scout_score(rating: Optional[ModelRating], routing_profile: str) -> float:
    if rating is None:
        return 50.0
    if rating.intelligence < 45:
        return -100.0 + rating.intelligence
    if routing_profile == "frugal":
        return 0.35 * rating.speed + 0.55 * rating.cost_efficiency + 0.10 * rating.context_handling
    if routing_profile == "fast":
        return 0.60 * rating.speed + 0.30 * rating.cost_efficiency + 0.10 * rating.context_handling
    return 0.45 * rating.speed + 0.45 * rating.cost_efficiency + 0.10 * rating.context_handling


def _router_score(rating: Optional[ModelRating], routing_profile: str) -> float:
    if rating is None:
        return 50.0
    if routing_profile == "frugal":
        return (
            0.40 * rating.intelligence
            + 0.25 * rating.coding
            + 0.10 * rating.agentic
            + 0.25 * rating.cost_efficiency
        )
    if routing_profile == "fast":
        return (
            0.40 * rating.intelligence
            + 0.25 * rating.coding
            + 0.10 * rating.agentic
            + 0.25 * rating.speed
        )
    return 0.55 * rating.intelligence + 0.30 * rating.coding + 0.15 * rating.agentic


def _pick_effort(
    harness: str,
    model_id: str,
    desired: str,
    *,
    mode: str,
) -> Optional[str]:
    allowed = infer_supported_efforts(harness, model_id, mode=mode)
    if not allowed:
        return None
    if desired in allowed:
        return desired
    return allowed[0]


def _candidates_for_harness(
    harness: str,
    *,
    mode: str,
    available: Optional[AvailableModels],
    ratings: Optional[ModelRatingsMap],
    catalog: Optional[ModelCatalog],
) -> List[str]:
    allow = enabled_model_ids(available, mode, harness)
    if allow is None and mode == "native" and harness != "agy":
        allow = enabled_model_ids(available, "herdr", harness)
    if allow is not None:
        return list(allow)
    rated_ids = list((ratings or {}).get(harness, {}).keys())
    if rated_ids:
        if harness == "agy" and mode == "native":
            rated_ids = [m for m in rated_ids if m in ("flash_lite", "flash", "pro")]
        elif harness == "agy" and mode == "herdr":
            rated_ids = [m for m in rated_ids if m not in ("flash_lite", "flash", "pro")]
        if rated_ids:
            return rated_ids
    if catalog is not None:
        return [str(opt.value) for opt in catalog.models(harness, mode=mode)]
    return []


def select_triage_models(
    host_harness: str,
    *,
    available: Optional[AvailableModels] = None,
    ratings: Optional[ModelRatingsMap] = None,
    agy_mode: str = "subagents",
    routing_profile: str = "balanced",
    catalog: Optional[ModelCatalog] = None,
) -> TriageModelsSelection:
    """Deterministically pick Stage-1 Mini-Scout and Stage-2 Router models."""
    host = get_harness(host_harness).kind
    profile = validate_routing_profile(routing_profile)
    notes: List[str] = []

    if host in NATIVE_TRIAGE_HARNESSES:
        adapter: Literal["native_subagent", "headless_cli"] = "native_subagent"
        pool_mode = "herdr" if (host == "agy" and agy_mode == "swarm") else "native"
        models = _candidates_for_harness(
            host, mode=pool_mode, available=available, ratings=ratings, catalog=catalog
        )
        if not models:
            raise CatalogError(
                f"No available models found on host harness '{host}' (mode={pool_mode}) "
                "for two-stage triage. Enable at least one model in [models.available]."
            )
        scout_mid = max(
            models,
            key=lambda m: (
                _scout_score(lookup_rating(ratings, host, m), profile),
                lookup_rating(ratings, host, m).coding if lookup_rating(ratings, host, m) else 0,
            ),
        )
        router_mid = max(
            models,
            key=lambda m: (
                _router_score(lookup_rating(ratings, host, m), profile),
                (
                    lookup_rating(ratings, host, m).cost_efficiency
                    if lookup_rating(ratings, host, m)
                    else 0
                ),
            ),
        )
        scout_eff = _pick_effort(host, scout_mid, "low", mode=pool_mode)
        router_desired = "high" if (profile == "quality" or scout_mid == router_mid) else "medium"
        router_eff = _pick_effort(host, router_mid, router_desired, mode=pool_mode)
        if len(models) == 1:
            notes.append(
                f"Single enabled model on {host}; reusing {scout_mid} with low vs high effort."
            )
        return TriageModelsSelection(
            host_harness=host,
            adapter=adapter,
            scout_binding=f"{host}:{scout_mid}",
            scout_effort=scout_eff,
            router_binding=f"{host}:{router_mid}",
            router_effort=router_eff,
            agy_mode=agy_mode if host == "agy" else None,
            notes=notes,
        )

    # Non-native host harness (e.g. kilo, codex, hermes, gemini): headless CLI fallback
    adapter = "headless_cli"
    notes.append(
        f"Host harness '{host}' has no per-model native subagent tool; "
        "falling back to `aihead triage` headless CLI execution."
    )
    pool_pairs: List[Tuple[str, str]] = []
    for h_kind in ("agy", "pi", "claude", "opencode"):
        for mid in _candidates_for_harness(
            h_kind, mode="herdr", available=available, ratings=ratings, catalog=catalog
        ):
            try:
                ensure_herdr_routable(h_kind, mid, None, role="scout", require_effort=False)
            except CatalogError:
                continue
            pool_pairs.append((h_kind, mid))

    if not pool_pairs:
        raise CatalogError(
            f"Host harness '{host}' requires headless CLI triage fallback, "
            "but no models are available on supported headless harnesses "
            f"({', '.join(sorted(SUPPORTED_CLI_HARNESSES))})."
        )

    scout_h, scout_m = max(
        pool_pairs,
        key=lambda p: (
            _scout_score(lookup_rating(ratings, p[0], p[1]), profile),
            lookup_rating(ratings, p[0], p[1]).coding if lookup_rating(ratings, p[0], p[1]) else 0,
        ),
    )
    router_h, router_m = max(
        pool_pairs,
        key=lambda p: (
            _router_score(lookup_rating(ratings, p[0], p[1]), profile),
            (
                lookup_rating(ratings, p[0], p[1]).cost_efficiency
                if lookup_rating(ratings, p[0], p[1])
                else 0
            ),
        ),
    )
    scout_eff = _pick_effort(scout_h, scout_m, "low", mode="herdr")
    router_desired = "high" if profile == "quality" else "medium"
    router_eff = _pick_effort(router_h, router_m, router_desired, mode="herdr")
    return TriageModelsSelection(
        host_harness=host,
        adapter=adapter,
        scout_binding=f"{scout_h}:{scout_m}",
        scout_effort=scout_eff,
        router_binding=f"{router_h}:{router_m}",
        router_effort=router_eff,
        notes=notes,
    )


def _check_allowlist_and_route(
    raw_slug: str,
    *,
    role: str,
    mode: Literal["herdr", "native"],
    target_harness: Optional[str],
    agy_mode: str,
    available: Optional[AvailableModels],
    errors: List[str],
) -> Optional[HarnessBinding]:
    try:
        binding = parse_binding(raw_slug, label=role)
    except InvalidBindingSlugError as err:
        errors.append(f"{role}: invalid binding '{raw_slug}': {err}")
        return None

    kind = binding.kind.value
    model = binding.model
    effort = binding.effort.value if binding.effort else None

    if mode == "native":
        if target_harness and kind != target_harness:
            errors.append(
                f"{role}: binding '{raw_slug}' uses harness '{kind}', "
                f"but orchestration requires single harness '{target_harness}'."
            )
            return None
        pool_mode = "herdr" if (kind == "agy" and agy_mode == "swarm") else "native"
        allow = enabled_model_ids(available, pool_mode, kind)
        if allow is None and pool_mode == "native" and kind != "agy":
            allow = enabled_model_ids(available, "herdr", kind)
        if allow is not None and model not in allow:
            errors.append(
                f"{role}: model '{model}' is not in [models.available.{pool_mode}.{kind}]."
            )
            return None
        if kind == "agy" and agy_mode == "swarm":
            try:
                ensure_herdr_routable(kind, model, effort, role=role, require_effort=False)
            except CatalogError as err:
                errors.append(f"{role}: {err}")
                return None
        else:
            prob = native_binding_error(
                kind, model, role=role, native_harness=target_harness or kind
            )
            if prob:
                errors.append(f"{role}: {prob}")
                return None
        return binding

    # Herdr mode
    allow = enabled_model_ids(available, "herdr", kind)
    if allow is not None and model not in allow:
        errors.append(f"{role}: model '{model}' is not in [models.available.herdr.{kind}].")
        return None
    try:
        ensure_herdr_routable(kind, model, effort, role=role, require_effort=False)
    except CatalogError as err:
        errors.append(f"{role}: {err}")
        return None
    return binding


def validate_route(
    plan: RoutingPlan,
    *,
    available: Optional[AvailableModels] = None,
    ratings: Optional[ModelRatingsMap] = None,
    agy_mode: str = "subagents",
    backend: str = "herdr",
) -> RouteValidationResult:
    """Deterministically validate a Stage-2 :class:`RoutingPlan` against all invariants."""
    errors: List[str] = []
    advisories: List[str] = []
    backend_key = backend.strip().lower()

    if backend_key == "core" and plan.workflow in ("ergo", "council", "herdr-master"):
        errors.append(
            f"Workflow '/{plan.workflow}' is not supported on Herdr Core (--backend core). "
            "Use 'direct', '/orchestration' (if native subagents exist), or legacy Herdr."
        )

    if plan.needs_user:
        if not (plan.question and plan.question.strip()):
            errors.append("needs_user=true requires a non-empty clarifying question.")
        return RouteValidationResult(
            valid=not errors,
            errors=errors,
            advisories=advisories,
            normalized_plan=plan if not errors else None,
        )

    arch_binding: Optional[HarnessBinding] = None
    rev_binding: Optional[HarnessBinding] = None

    if plan.workflow == "direct":
        if not plan.direct_binding:
            errors.append("Workflow 'direct' requires 'direct_binding'.")
        else:
            _check_allowlist_and_route(
                plan.direct_binding,
                role="direct_binding",
                mode="herdr",
                target_harness=None,
                agy_mode=agy_mode,
                available=available,
                errors=errors,
            )
        if plan.direct_reviewer:
            _check_allowlist_and_route(
                plan.direct_reviewer,
                role="direct_reviewer",
                mode="herdr",
                target_harness=None,
                agy_mode=agy_mode,
                available=available,
                errors=errors,
            )

    elif plan.workflow == "orchestration":
        if not plan.target_harness:
            errors.append("Workflow 'orchestration' requires 'target_harness'.")
        else:
            try:
                spec = get_harness(plan.target_harness)
            except CatalogError as err:
                errors.append(str(err))
                spec = None
            if spec is not None and not spec.supports_native:
                errors.append(
                    f"Harness '{spec.kind}' does not support native /orchestration "
                    f"(supported: {', '.join(supported_harnesses(mode='native'))})."
                )
            elif spec is not None:
                for role_name in ("scout", "architect", "reviewer"):
                    raw_slug = getattr(plan, role_name)
                    if not raw_slug:
                        errors.append(f"Workflow 'orchestration' requires '{role_name}'.")
                        continue
                    # Auto-qualify unqualified native model IDs with target_harness
                    slug = raw_slug if ":" in raw_slug else f"{spec.kind}:{raw_slug}"
                    bound = _check_allowlist_and_route(
                        slug,
                        role=role_name,
                        mode="native",
                        target_harness=spec.kind,
                        agy_mode=agy_mode,
                        available=available,
                        errors=errors,
                    )
                    if role_name == "architect":
                        arch_binding = bound
                    elif role_name == "reviewer":
                        rev_binding = bound

    elif plan.workflow == "ergo":
        for role_name in ("scout", "architect", "reviewer"):
            raw_slug = getattr(plan, role_name)
            if not raw_slug:
                errors.append(f"Workflow 'ergo' requires '{role_name}'.")
                continue
            bound = _check_allowlist_and_route(
                raw_slug,
                role=role_name,
                mode="herdr",
                target_harness=None,
                agy_mode=agy_mode,
                available=available,
                errors=errors,
            )
            if role_name == "architect":
                arch_binding = bound
            elif role_name == "reviewer":
                rev_binding = bound
        if plan.coordinator:
            _check_allowlist_and_route(
                plan.coordinator,
                role="coordinator",
                mode="herdr",
                target_harness=None,
                agy_mode=agy_mode,
                available=available,
                errors=errors,
            )

    elif plan.workflow == "council":
        for role_name in ("coordinator", "scout"):
            raw_slug = getattr(plan, role_name)
            if not raw_slug:
                errors.append(f"Workflow 'council' requires '{role_name}'.")
                continue
            _check_allowlist_and_route(
                raw_slug,
                role=role_name,
                mode="herdr",
                target_harness=None,
                agy_mode=agy_mode,
                available=available,
                errors=errors,
            )
        if len(plan.council_members) < 2:
            errors.append("Workflow 'council' requires at least 2 council_members.")
        else:
            member_families: set[str] = set()
            for idx, m_slug in enumerate(plan.council_members):
                bound = _check_allowlist_and_route(
                    m_slug,
                    role=f"council_member_{idx + 1}",
                    mode="herdr",
                    target_harness=None,
                    agy_mode=agy_mode,
                    available=available,
                    errors=errors,
                )
                if bound is not None:
                    member_families.add(detect_model_family(bound.kind.value, bound.model))
            if len(member_families) < 2:
                advisories.append(
                    "All selected council_members belong to the same model family; "
                    "prefer >=2 distinct families when available."
                )

    elif plan.workflow == "herdr-master":
        if plan.coordinator:
            _check_allowlist_and_route(
                plan.coordinator,
                role="coordinator",
                mode="herdr",
                target_harness=None,
                agy_mode=agy_mode,
                available=available,
                errors=errors,
            )

    # Cross-family Architect vs Reviewer advisory
    if arch_binding is not None and rev_binding is not None:
        arch_fam = detect_model_family(arch_binding.kind.value, arch_binding.model)
        rev_fam = detect_model_family(rev_binding.kind.value, rev_binding.model)
        if arch_fam != "unknown" and arch_fam == rev_fam:
            advisories.append(
                f"Architect ({arch_binding.to_slug()}) and Reviewer ({rev_binding.to_slug()}) "
                f"share model family '{arch_fam}'. Consider cross-family review when available."
            )

    return RouteValidationResult(
        valid=not errors,
        errors=errors,
        advisories=advisories,
        normalized_plan=plan if not errors else None,
    )


def build_mini_scout_prompt(task: str, cwd: str) -> str:
    """Build the read-only Stage-1 Mini-Scout prompt."""
    return (
        "You are Stage-1 Mini-Scout for AiHead triage.\n"
        "STRICT RULE: Read-only inspection ONLY. Do NOT edit files, run builds, or commit.\n"
        f"Working Directory: {cwd}\n"
        f"User Request: {task.strip()}\n\n"
        "Quickly inspect git status, directory layout, affected files/modules, AGENTS.md/README, "
        "and test framework to assess scope, coupling, ambiguity, and whether multiple decoupled "
        "jobs are present.\n\n"
        "Respond ONLY with a single valid JSON object matching this schema:\n"
        "{\n"
        '  "scope_size": "atomic" | "small" | "medium" | "large",\n'
        '  "estimated_files": <int>,\n'
        '  "touched_areas": ["<path or module>", ...],\n'
        '  "requires_design": <bool>,\n'
        '  "ambiguity": "low" | "medium" | "high",\n'
        '  "ambiguity_reasons": ["<reason>", ...],\n'
        '  "multi_job_detected": <bool>,\n'
        '  "distinct_jobs": ["<job summary>", ...],\n'
        '  "risk_flags": ["<flag>", ...],\n'
        '  "test_framework": "<pytest|jest|cargo|go|null>",\n'
        '  "summary": "<concise 3-5 line factual survey>"\n'
        "}\n"
    )


def build_router_prompt(
    task: str,
    scout_report: TriageScoutReport,
    rated_models_table: str,
    *,
    routing_profile: str = "balanced",
    backend: str = "herdr",
    agy_mode: str = "subagents",
    previous_errors: Optional[Sequence[str]] = None,
) -> str:
    """Build the Stage-2 Strong Routing Decider prompt."""
    profile = validate_routing_profile(routing_profile)
    repair_block = ""
    if previous_errors:
        joined = "\n".join(f"- {err}" for err in previous_errors)
        repair_block = (
            "\nPREVIOUS ROUTE VALIDATION FAILED — FIX EVERY ERROR BELOW:\n"
            f"{joined}\n"
        )

    return (
        "You are Stage-2 Routing Decider for AiHead.\n"
        "Select the optimal workflow, harnesses, models, and effort levels strictly from the "
        "RATED AVAILABLE MODELS list below. NEVER invent a model ID.\n"
        "Do NOT specify static worker lists for /ergo or /orchestration — the Architect picks "
        "per-task workers dynamically from Available models + ratings using `worker_mode`.\n\n"
        f"ACTIVE CONSTRAINTS:\n"
        f"- backend: {backend} (if 'core', do NOT choose ergo/council/herdr-master)\n"
        f"- routing_profile: {profile}\n"
        f"- orchestration agy_mode: {agy_mode}\n"
        f"- native-capable harnesses for /orchestration: "
        f"{', '.join(supported_harnesses(mode='native'))}\n\n"
        "WORKFLOW RUBRIC:\n"
        "- direct: atomic 1-2 file mechanical change, requires_design=false, ambiguity=low.\n"
        "- orchestration: small/medium cohesive multi-file task where ONE native harness covers "
        "scout/architect/reviewer strongly.\n"
        "- ergo: medium/large feature, high risk, or cross-harness best-of-breed & cross-family "
        "Architect/Reviewer diversity beats a single harness.\n"
        "- council: ambiguity=high, major RFC/architecture trade-offs before coding (>=2 members "
        "from distinct model families).\n"
        "- herdr-master: multi_job_detected=true (multiple independent parallel workstreams).\n\n"
        f"USER REQUEST:\n{task.strip()}\n\n"
        f"STAGE-1 MINI-SCOUT REPORT:\n{scout_report.model_dump_json(indent=2)}\n\n"
        f"RATED AVAILABLE MODELS:\n{rated_models_table}\n"
        f"{repair_block}\n"
        "Respond ONLY with a single valid JSON object matching this schema:\n"
        "{\n"
        '  "workflow": "direct" | "orchestration" | "ergo" | "council" | "herdr-master",\n'
        '  "confidence": "high" | "medium" | "low",\n'
        '  "needs_user": <bool>,\n'
        '  "question": "<string or null>",\n'
        '  "rationale": "<concise plain-language rationale>",\n'
        '  "target_mode": "herdr" | "native",\n'
        '  "target_harness": "<native harness for orchestration, else null>",\n'
        '  "worker_mode": "balanced" | "quality" | "fast" | "frugal",\n'
        '  "coordinator": "<harness:model[:effort] or null>",\n'
        '  "scout": "<harness:model[:effort] or null>",\n'
        '  "architect": "<harness:model[:effort] or null>",\n'
        '  "reviewer": "<harness:model[:effort] or null>",\n'
        '  "council_members": ["<harness:model[:effort]>", ...],\n'
        '  "direct_binding": "<harness:model[:effort] or null>",\n'
        '  "direct_reviewer": "<harness:model[:effort] or null>",\n'
        '  "sub_jobs": [{"name": "...", "workflow": "..."}]\n'
        "}\n"
    )


def parse_json_payload(raw_output: str) -> Dict[str, Any]:
    """Extract and parse a JSON object from raw LLM / headless CLI stdout."""
    unwrapped = _extract_text_from_cli_output(raw_output)
    if not unwrapped:
        raise ValueError("Empty response from triage runner.")
    candidates: List[str] = [unwrapped]
    for match in _JSON_FENCE_RE.finditer(unwrapped):
        candidates.append(match.group(1))
    first = unwrapped.find("{")
    last = unwrapped.rfind("}")
    if first != -1 and last > first:
        candidates.append(unwrapped[first : last + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("Could not parse valid JSON object from triage runner output.")


def _default_headless_runner(
    binding_slug: str,
    effort: Optional[str],
    prompt: str,
    cwd: str,
    timeout: float = 90.0,
) -> str:
    binding = parse_binding(binding_slug)
    if effort and binding.effort is None:
        binding.effort = EffortLevel(effort)
    head_cfg = AiHeadConfig(binding=binding, dangerously_skip_permissions=True)
    cmd = build_headless_cli_argv(head_cfg, prompt=prompt)
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise RuntimeError(f"Triage runner ({binding.to_slug()}) failed: {err[:240]}")
    return proc.stdout or ""


def _rated_models_map(
    available: Optional[AvailableModels],
    ratings: Optional[ModelRatingsMap],
    catalog: ModelCatalog,
) -> Dict[str, List[str]]:
    models_by_harness: Dict[str, List[str]] = {}
    for mode in ("herdr", "native"):
        for harness in supported_harnesses(mode=mode):
            allow = enabled_model_ids(available, mode, harness)
            ids = (
                list(allow)
                if allow is not None
                else (
                    list((ratings or {}).get(harness, {}).keys())
                    or [str(opt.value) for opt in catalog.models(harness, mode=mode)[:5]]
                )
            )
            bucket = models_by_harness.setdefault(harness, [])
            for mid in ids:
                if mid not in bucket:
                    bucket.append(mid)
    return {h: ids for h, ids in models_by_harness.items() if ids}


def run_triage(
    task: str,
    *,
    cwd: Optional[Path] = None,
    project_path: Optional[Path] = None,
    host_harness: Optional[str] = None,
    routing_profile: Optional[str] = None,
    backend: str = "herdr",
    dry_run: bool = False,
    runner_fn: Optional[Callable[[str, Optional[str], str, str], str]] = None,
) -> TriageRunResult:
    """Execute Stage-1 Mini-Scout -> Stage-2 Router -> Deterministic Validation Gate."""
    work_dir = Path(cwd or Path.cwd()).resolve()
    raw_cfg = (
        load_effective_project_config(project_path)
        if project_path is not None
        else load_effective_project_config(work_dir)
    )
    effective = load_effective_models_state(
        project_path or work_dir,
        project_cfg=raw_cfg,
        inherit_ancestors=True,
    )
    catalog = discover_model_catalog(raw_cfg, probe_live=False)

    active_host = host_harness or raw_cfg.ai_head.binding.kind.value
    active_profile = validate_routing_profile(routing_profile or raw_cfg.ai_head.routing_profile)
    agy_mode = raw_cfg.orchestration.agy_mode

    selection = select_triage_models(
        active_host,
        available=effective.available,
        ratings=effective.ratings,
        agy_mode=agy_mode,
        routing_profile=active_profile,
        catalog=catalog,
    )

    # Headless execution always needs headless-capable bindings
    exec_selection = selection
    if selection.adapter == "native_subagent" and (
        parse_binding(selection.scout_binding).kind.value not in SUPPORTED_CLI_HARNESSES
        or parse_binding(selection.router_binding).kind.value not in SUPPORTED_CLI_HARNESSES
    ):
        exec_selection = select_triage_models(
            "kilo",
            available=effective.available,
            ratings=effective.ratings,
            agy_mode=agy_mode,
            routing_profile=active_profile,
            catalog=catalog,
        )

    runner = runner_fn or _default_headless_runner

    # Stage 1: Cheap Mini-Scout
    scout_prompt = build_mini_scout_prompt(task, str(work_dir))
    try:
        raw_scout = runner(
            exec_selection.scout_binding,
            exec_selection.scout_effort,
            scout_prompt,
            str(work_dir),
        )
        scout_report = TriageScoutReport.model_validate(parse_json_payload(raw_scout))
    except Exception as err:
        return TriageRunResult(
            ok=False,
            triage_models=selection,
            error=f"Stage-1 Mini-Scout failed: {err}",
        )

    # Stage 2: Strong Routing Decider + Deterministic Validation Gate (max 1 bounded retry)
    models_map = _rated_models_map(effective.available, effective.ratings, catalog)
    rated_table = format_rated_models_table(models_map, effective.ratings)

    prev_errors: Optional[List[str]] = None
    retries_used = 0
    last_plan: Optional[RoutingPlan] = None
    last_validation: Optional[RouteValidationResult] = None

    for attempt in range(2):
        router_prompt = build_router_prompt(
            task,
            scout_report,
            rated_table,
            routing_profile=active_profile,
            backend=backend,
            agy_mode=agy_mode,
            previous_errors=prev_errors,
        )
        router_effort = exec_selection.router_effort
        if scout_report.ambiguity == "high" and router_effort == "medium":
            r_bind = parse_binding(exec_selection.router_binding)
            router_effort = _pick_effort(
                r_bind.kind.value, r_bind.model, "high", mode="herdr"
            ) or router_effort
        try:
            raw_router = runner(
                exec_selection.router_binding,
                router_effort,
                router_prompt,
                str(work_dir),
            )
            last_plan = RoutingPlan.model_validate(parse_json_payload(raw_router))
        except Exception as err:
            if attempt == 0:
                prev_errors = [f"Malformed RoutingPlan JSON: {err}"]
                retries_used = 1
                continue
            return TriageRunResult(
                ok=False,
                triage_models=selection,
                scout_report=scout_report,
                retries_used=retries_used,
                error=f"Stage-2 Router failed after retry: {err}",
            )

        last_validation = validate_route(
            last_plan,
            available=effective.available,
            ratings=effective.ratings,
            agy_mode=agy_mode,
            backend=backend,
        )
        if last_validation.valid:
            break
        if attempt == 0:
            prev_errors = list(last_validation.errors)
            retries_used = 1
        else:
            break

    artifact_dir_str: Optional[str] = None
    if not dry_run:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        art_dir = work_dir / ".orchestration" / f"triage-{ts}"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "scout.json").write_text(
            scout_report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        if last_plan is not None:
            (art_dir / "route.json").write_text(
                last_plan.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
        artifact_dir_str = str(art_dir)

    ok = bool(last_validation and last_validation.valid)
    return TriageRunResult(
        ok=ok,
        triage_models=selection,
        scout_report=scout_report,
        routing_plan=last_plan,
        validation=last_validation,
        retries_used=retries_used,
        artifact_dir=artifact_dir_str,
        error=(
            None
            if ok
            else "Route validation failed after retry: "
            + "; ".join(last_validation.errors if last_validation else ["unknown error"])
        ),
    )
