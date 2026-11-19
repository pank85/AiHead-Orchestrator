"""Incremental multi-layer evaluation engine for available harness models.

Evaluation order (strictly incremental — already-rated models and manual
overrides are skipped unless explicitly refreshed/forced):
1. Layers 1 & 2 (Zero-auth public datasets): models.dev, OpenRouter,
   SWE-bench, Aider Polyglot, and LiveBench (cached locally on disk).
2. Local CLI metadata harvested from ``setup_catalog.ModelCatalog``.
3. Optional live micro-probe (``probe=True``) measuring real latency & health.
4. Layer 3 (Batched bounded harness LLM evaluator, default unless ``use_llm=False``)
   for models unmatched in Layers 1 & 2 (e.g. preview codenames, proxies, free routers).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from herdr_master.cli.setup_catalog import ModelCatalog, discover_model_catalog
from herdr_master.core.available_models import AvailableModels, enabled_model_ids
from herdr_master.core.catalog import HarnessKind, supported_harnesses
from herdr_master.core.config import (
    AiHeadConfig,
    HerdrMasterConfig,
    find_project_config_path,
    load_effective_models_state,
    load_project_config,
    save_project_config,
)
from herdr_master.core.eval_sources import (
    BenchmarkEvidence,
    SourceCatalog,
    fetch_source_catalog,
    synthesize_rating,
)
from herdr_master.core.model_ratings import (
    BillingTier,
    ModelEvaluationConfig,
    ModelRating,
    ModelRatingsMap,
    NormalizedModel,
    apply_effort_calibration,
    clamp_score,
    derive_recommended_roles,
    find_unrated_models,
    infer_supported_efforts,
    lookup_rating,
    merge_ratings,
    normalize_model_slug,
    score_context_handling,
    score_cost_efficiency,
    utc_now_iso,
)
from herdr_master.core.models import HarnessBinding
from herdr_master.pipeline.ai_head import SUPPORTED_CLI_HARNESSES, build_headless_cli_argv

__all__ = [
    "EvaluationResult",
    "ProbeOutcome",
    "collect_target_models",
    "evaluate_available_models",
    "extract_cli_metadata",
    "parse_llm_eval_response",
    "select_evaluator_binding",
]

_CTX_RE = re.compile(r"\bctx\s+(\d+(?:\.\d+)?)\s*([kKmM])?\b")
_THINKING_RE = re.compile(r"\bthinking\s+(yes|no|true|false)\b", re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


@dataclass(frozen=True)
class ProbeOutcome:
    """Result of an opt-in live latency & health micro-probe for one model."""

    harness: str
    model_id: str
    ok: bool
    latency_sec: Optional[float] = None
    estimated_tps: Optional[float] = None
    error: Optional[str] = None


@dataclass
class EvaluationResult:
    """Structured summary returned by :func:`evaluate_available_models`."""

    evaluated: ModelRatingsMap = field(default_factory=dict)
    skipped_rated: Dict[str, List[str]] = field(default_factory=dict)
    skipped_manual: Dict[str, List[str]] = field(default_factory=dict)
    unresolved: Dict[str, List[str]] = field(default_factory=dict)
    source_status: Dict[str, str] = field(default_factory=dict)
    probe_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    saved_path: Optional[Path] = None
    evaluator_used: Optional[str] = None

    @property
    def evaluated_count(self) -> int:
        return sum(len(items) for items in self.evaluated.values())

    @property
    def skipped_count(self) -> int:
        return sum(len(items) for items in self.skipped_rated.values()) + sum(
            len(items) for items in self.skipped_manual.values()
        )

    @property
    def unresolved_count(self) -> int:
        return sum(len(items) for items in self.unresolved.values())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "evaluated_count": self.evaluated_count,
            "skipped_count": self.skipped_count,
            "unresolved_count": self.unresolved_count,
            "evaluator_used": self.evaluator_used,
            "saved_path": str(self.saved_path) if self.saved_path else None,
            "source_status": dict(self.source_status),
            "evaluated": {
                harness: {mid: rating.model_dump(exclude_none=True) for mid, rating in m.items()}
                for harness, m in self.evaluated.items()
            },
            "skipped_rated": dict(self.skipped_rated),
            "skipped_manual": dict(self.skipped_manual),
            "unresolved": dict(self.unresolved),
            "probe_results": dict(self.probe_results),
            "warnings": list(self.warnings),
        }


def extract_cli_metadata(catalog: Optional[ModelCatalog]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Harvest context window, thinking support, and display name from ``ModelCatalog``."""
    meta: Dict[Tuple[str, str], Dict[str, Any]] = {}
    if catalog is None:
        return meta

    for pool in (catalog.herdr_models, catalog.native_models):
        for harness, options in pool.items():
            for opt in options:
                key = (str(harness).strip().lower(), str(opt.value).strip())
                detail = str(opt.detail or "").strip()
                record = meta.setdefault(key, {})
                if detail and "display_detail" not in record:
                    record["display_detail"] = detail
                ctx_match = _CTX_RE.search(detail)
                if ctx_match and "context_window" not in record:
                    num = float(ctx_match.group(1))
                    unit = (ctx_match.group(2) or "").lower()
                    mult = 1_000_000 if unit == "m" else (1_000 if unit == "k" else 1)
                    record["context_window"] = int(round(num * mult))
                think_match = _THINKING_RE.search(detail)
                if think_match and "supports_reasoning" not in record:
                    record["supports_reasoning"] = think_match.group(1).lower() in ("yes", "true")
    return meta


def collect_target_models(
    available: Optional[AvailableModels],
    catalog: Optional[ModelCatalog] = None,
    *,
    harness_filter: Optional[str] = None,
    all_discovered: bool = False,
) -> Dict[str, List[str]]:
    """Collect ``{harness: [model_ids]}`` to consider for evaluation.

    - When ``available`` defines an allowlist for a mode/harness, only its
      explicitly enabled models are included (explicit ``[]`` yields none).
    - When ``available`` is ``None`` (unrestricted) or a harness is unrestricted
      and ``all_discovered=True``, discovered catalog models are used.
    """
    filter_key = harness_filter.strip().lower() if harness_filter else None
    targets: Dict[str, List[str]] = {}

    def add_ids(harness: str, ids: Sequence[str]) -> None:
        if filter_key and harness != filter_key:
            return
        bucket = targets.setdefault(harness, [])
        for raw in ids:
            mid = str(raw).strip()
            if mid and mid not in bucket:
                bucket.append(mid)

    for mode in ("herdr", "native"):
        for harness in supported_harnesses(mode=mode):
            if filter_key and harness != filter_key:
                continue
            allow = enabled_model_ids(available, mode, harness)
            if allow is not None:
                # Explicit allowlist (including empty [])
                add_ids(harness, allow)
            elif all_discovered or available is None:
                if catalog is not None:
                    discovered = [str(opt.value) for opt in catalog.models(harness, mode=mode)]
                    add_ids(harness, discovered)

    return {h: ids for h, ids in targets.items() if ids}


def select_evaluator_binding(
    cfg: HerdrMasterConfig,
    *,
    explicit_binding: Optional[str] = None,
    available: Optional[AvailableModels] = None,
    ratings: Optional[ModelRatingsMap] = None,
) -> Optional[HarnessBinding]:
    """Select a headless-CLI-capable harness/model to run Layer-3 batched evaluation."""
    if explicit_binding and explicit_binding.strip():
        binding = HarnessBinding.from_slug(explicit_binding.strip(), label="model-evaluator")
        if binding.kind.value not in SUPPORTED_CLI_HARNESSES:
            raise ValueError(
                f"Evaluator harness '{binding.kind.value}' does not support headless CLI. "
                f"Supported headless harnesses: {', '.join(sorted(SUPPORTED_CLI_HARNESSES))}."
            )
        return binding

    # 1. Prefer configured AiHead binding when headless-capable
    ah_bind = cfg.ai_head.binding
    if ah_bind.kind.value in SUPPORTED_CLI_HARNESSES and ah_bind.model.strip():
        return ah_bind.model_copy(deep=True)

    # 2. Prefer highest-intelligence already-rated model on a headless-capable harness
    best_rated: Optional[Tuple[int, str, str]] = None
    if ratings:
        for harness in SUPPORTED_CLI_HARNESSES:
            allowed = enabled_model_ids(available, "herdr", harness)
            for model_id, rating in ratings.get(harness, {}).items():
                if allowed is not None and model_id not in allowed:
                    continue
                score = rating.intelligence
                if best_rated is None or score > best_rated[0]:
                    best_rated = (score, harness, model_id)
    if best_rated is not None:
        return HarnessBinding(
            kind=HarnessKind(best_rated[1]),
            model=best_rated[2],
            effort=None,
            label="model-evaluator",
        )

    # 3. Pick first enabled model on a headless-capable harness
    for harness in ("agy", "pi", "claude", "opencode"):
        allowed = enabled_model_ids(available, "herdr", harness)
        if allowed:
            return HarnessBinding(
                kind=HarnessKind(harness),
                model=allowed[0],
                effort=None,
                label="model-evaluator",
            )

    return None


def _run_micro_probe(
    harness: str,
    model_id: str,
    *,
    timeout: float = 20.0,
) -> ProbeOutcome:
    """Run a tiny bounded headless CLI probe to observe latency and health."""
    if harness not in SUPPORTED_CLI_HARNESSES:
        return ProbeOutcome(
            harness=harness,
            model_id=model_id,
            ok=False,
            error=f"Harness '{harness}' has no verified headless probe runner.",
        )
    try:
        head_cfg = AiHeadConfig(
            binding=HarnessBinding(kind=HarnessKind(harness), model=model_id, effort=None),
            dangerously_skip_permissions=True,
        )
        cmd = build_headless_cli_argv(head_cfg, prompt="Reply with exactly: PONG")
    except Exception as err:
        return ProbeOutcome(harness=harness, model_id=model_id, ok=False, error=str(err))

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        return ProbeOutcome(harness=harness, model_id=model_id, ok=False, error=str(err))
    elapsed = max(0.05, time.monotonic() - start)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        err_msg = stderr or stdout or f"exit code {proc.returncode}"
        return ProbeOutcome(
            harness=harness,
            model_id=model_id,
            ok=False,
            latency_sec=round(elapsed, 2),
            error=err_msg[:200],
        )
    # Approximate responsiveness proxy from short round-trip latency
    # <1.5s -> ~150 tps, 3s -> ~95 tps, 6s -> ~60 tps, 12s -> ~35 tps
    est_tps = round(max(15.0, min(220.0, 260.0 / (elapsed + 0.6))), 1)
    return ProbeOutcome(
        harness=harness,
        model_id=model_id,
        ok=True,
        latency_sec=round(elapsed, 2),
        estimated_tps=est_tps,
    )


def _build_llm_eval_prompt(items: Sequence[Dict[str, Any]]) -> str:
    payload_json = json.dumps(items, indent=2)
    return (
        "You are evaluating AI coding/reasoning models for an autonomous orchestrator's "
        "model routing registry.\n"
        "If web search is available in your tools, you may briefly check public benchmarks "
        "for unfamiliar models. Be realistic and calibrated against frontier baselines "
        "(top frontier coding/reasoning models ~90-96, strong mid-tier ~78-88, fast/flash ~68-82, "
        "tiny/free ~55-74).\n\n"
        "Evaluate ONLY the following unrated base models:\n"
        f"{payload_json}\n\n"
        "Respond ONLY with a single valid JSON object (no prose outside JSON) mapping each "
        "item's exact `key` to an object with these fields:\n"
        "{\n"
        '  "<key>": {\n'
        '    "intelligence": <int 0-100>,\n'
        '    "coding": <int 0-100>,\n'
        '    "agentic": <int 0-100>,\n'
        '    "speed": <int 0-100>,\n'
        '    "cost_efficiency": <int 0-100>,\n'
        '    "context_handling": <int 0-100>,\n'
        '    "family": "<openai|anthropic|google|xai|deepseek|qwen|meta|mistral|unknown>",\n'
        '    "context_window": <int or null>,\n'
        '    "supports_reasoning": <bool>,\n'
        '    "confidence": "<medium|low>",\n'
        '    "notes": "<concise 1-line strengths & trade-offs, <=120 chars>"\n'
        "  }\n"
        "}\n"
    )


def _assistant_text_from_message(msg: Any) -> str:
    if not isinstance(msg, dict):
        return ""
    if msg.get("role") not in (None, "assistant"):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type", "text") == "text"
            and isinstance(part.get("text"), str)
        ]
        return "\n".join(parts).strip()
    return ""


def _extract_text_from_cli_output(raw_stdout: str) -> str:
    """Unwrap JSON/JSONL envelopes emitted by headless CLI runners (agy/pi/opencode/claude)."""
    text = raw_stdout.strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Handle JSONL streams (e.g., `pi --mode json` / `opencode run --format json`).
        # Prefer the final assistant message rather than concatenating duplicate events
        # or mixing user prompt echoes.
        last_assistant: str = ""
        fallback_chunks: List[str] = []
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if obj.get("type") == "agent_end" and isinstance(obj.get("messages"), list):
                for item in reversed(obj["messages"]):
                    extracted = _assistant_text_from_message(item)
                    if extracted:
                        last_assistant = extracted
                        break
                continue
            msg = obj.get("message")
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                extracted = _assistant_text_from_message(msg)
                if extracted:
                    last_assistant = extracted
                continue
            for key in ("result", "response", "text", "output"):
                val = obj.get(key)
                if isinstance(val, str) and val.strip():
                    fallback_chunks.append(val.strip())
        if last_assistant:
            return last_assistant
        return fallback_chunks[-1] if fallback_chunks else text

    if isinstance(parsed, dict):
        for key in ("result", "response", "text", "content", "output"):
            val = parsed.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return text


def parse_llm_eval_response(
    raw_output: str,
    expected_keys: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    """Parse strict JSON model evaluation dictionary from LLM runner stdout."""
    unwrapped = _extract_text_from_cli_output(raw_output)
    if not unwrapped:
        return {}

    candidates: List[str] = [unwrapped]
    for match in _JSON_FENCE_RE.finditer(unwrapped):
        candidates.append(match.group(1))
    first_brace = unwrapped.find("{")
    last_brace = unwrapped.rfind("}")
    if first_brace != -1 and last_brace > first_brace:
        candidates.append(unwrapped[first_brace : last_brace + 1])

    expected_set = set(expected_keys)
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            # Support optional wrapper `{"models": {...}}` or `{"ratings": {...}}`
            if not any(k in expected_set for k in data):
                for wrap_key in ("ratings", "models", "results"):
                    inner = data.get(wrap_key)
                    if isinstance(inner, dict):
                        data = inner
                        break
            matched = {
                str(k): v
                for k, v in data.items()
                if str(k) in expected_set and isinstance(v, dict)
            }
            if matched:
                return matched
    return {}


def _run_llm_evaluator(
    binding: HarnessBinding,
    prompt: str,
    *,
    timeout: float = 90.0,
) -> str:
    head_cfg = AiHeadConfig(binding=binding, dangerously_skip_permissions=True)
    cmd = build_headless_cli_argv(head_cfg, prompt=prompt)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise RuntimeError(f"Harness evaluator ({binding.to_slug()}) failed: {err[:240]}")
    return proc.stdout or ""


def _rating_from_llm_entry(
    normalized: NormalizedModel,
    raw_entry: Dict[str, Any],
    *,
    billing_tier: BillingTier,
    partial_evidence: Optional[BenchmarkEvidence],
    cli_meta: Dict[str, Any],
    probe_outcome: Optional[ProbeOutcome],
    mode: str,
) -> ModelRating:
    """Validate an LLM entry and overlay deterministic Layer-1/CLI/probe facts."""
    intel = clamp_score(raw_entry.get("intelligence", 70))
    coding = clamp_score(raw_entry.get("coding", intel))
    agentic = clamp_score(raw_entry.get("agentic", round(0.6 * coding + 0.4 * intel)))
    speed = clamp_score(raw_entry.get("speed", 70))

    # Hard context window & pricing facts always override LLM guesses
    context_window = (
        (partial_evidence.context_window if partial_evidence else None)
        or cli_meta.get("context_window")
        or (
            int(raw_entry["context_window"])
            if isinstance(raw_entry.get("context_window"), (int, float))
            and raw_entry["context_window"] > 0
            else None
        )
    )
    inp_price = partial_evidence.input_price_per_1m if partial_evidence else None
    out_price = partial_evidence.output_price_per_1m if partial_evidence else None

    effective_billing: BillingTier = "free" if normalized.is_free_route else billing_tier
    if (
        effective_billing in ("free", "subscription")
        or inp_price is not None
        or out_price is not None
    ):
        cost_eff = score_cost_efficiency(
            effective_billing,
            input_price_per_1m=inp_price,
            output_price_per_1m=out_price,
            is_free_route=normalized.is_free_route,
            intelligence=intel,
            encoded_effort=normalized.encoded_effort,
        )
    else:
        cost_eff = clamp_score(raw_entry.get("cost_efficiency", 68))

    if context_window is not None:
        context_handling = score_context_handling(context_window, intel)
    else:
        context_handling = clamp_score(raw_entry.get("context_handling", 70))

    observed_tps = (
        probe_outcome.estimated_tps
        if probe_outcome is not None and probe_outcome.ok
        else None
    )
    if observed_tps is not None:
        if observed_tps >= 180:
            speed = max(speed, 94)
        elif observed_tps >= 110:
            speed = clamp_score(0.5 * speed + 0.5 * 86)
        elif observed_tps >= 60:
            speed = clamp_score(0.5 * speed + 0.5 * 70)
        else:
            speed = clamp_score(0.5 * speed + 0.5 * 48)

    supports_reasoning = bool(
        (partial_evidence.supports_reasoning if partial_evidence else False)
        or cli_meta.get("supports_reasoning")
        or raw_entry.get("supports_reasoning")
        or normalized.encoded_effort is not None
    )
    supported_efforts = infer_supported_efforts(normalized.harness, normalized.model_id, mode=mode)

    family = normalized.family
    if family == "unknown":
        cand_fam = str(raw_entry.get("family") or "").strip().lower()
        if cand_fam:
            family = cand_fam
        elif partial_evidence and partial_evidence.family != "unknown":
            family = partial_evidence.family

    src_parts: List[str] = []
    if partial_evidence:
        src_parts.extend(sorted(partial_evidence.sources))
    if probe_outcome is not None and probe_outcome.ok:
        src_parts.append("probe")
    src_parts.append("harness_eval")

    raw_conf = str(raw_entry.get("confidence") or "low").strip().lower()
    confidence = "medium" if raw_conf == "medium" else "low"

    rating = ModelRating(
        intelligence=intel,
        coding=coding,
        agentic=agentic,
        speed=speed,
        cost_efficiency=cost_eff,
        context_handling=context_handling,
        family=family,
        billing_tier=effective_billing,
        context_window=context_window,
        input_price_per_1m=inp_price,
        output_price_per_1m=out_price,
        output_tokens_per_sec=observed_tps,
        supports_reasoning=supports_reasoning,
        supported_efforts=supported_efforts,
        notes=str(raw_entry.get("notes") or "Evaluated by harness model evaluator."),
        source="+".join(src_parts),
        confidence=confidence,  # type: ignore[arg-type]
        canonical_match=partial_evidence.canonical_id if partial_evidence else None,
        evaluated_at=utc_now_iso(),
        manual_override=False,
    )
    rating.recommended_roles = derive_recommended_roles(rating)
    return rating


def evaluate_available_models(
    *,
    project_path: Optional[Path] = None,
    cfg: Optional[HerdrMasterConfig] = None,
    catalog: Optional[ModelCatalog] = None,
    harness_filter: Optional[str] = None,
    all_discovered: bool = False,
    refresh: bool = False,
    force: bool = False,
    max_age_days: Optional[int] = None,
    probe: bool = False,
    use_llm: bool = True,
    evaluator_binding: Optional[str] = None,
    dry_run: bool = False,
    probe_live_catalog: bool = False,
    inherit_ancestors: bool = True,
    cache_dir: Optional[Path] = None,
    source_catalog: Optional[SourceCatalog] = None,
    http_get: Optional[Callable[[str, float], str]] = None,
    probe_fn: Optional[Callable[[str, str], ProbeOutcome]] = None,
    llm_eval_fn: Optional[Callable[[HarnessBinding, str], str]] = None,
    now: Optional[datetime] = None,
) -> EvaluationResult:
    """Evaluate available models that lack stored ratings and persist atomically."""
    resolved_path: Optional[Path] = None
    if project_path is not None:
        cand = Path(project_path).resolve()
        resolved_path = cand / ".herdr-master.toml" if cand.is_dir() else cand
    elif cfg is None:
        resolved_path = find_project_config_path()

    raw_cfg = (
        cfg.model_copy(deep=True)
        if cfg is not None
        else (
            load_project_config(resolved_path)
            if resolved_path is not None and resolved_path.is_file()
            else HerdrMasterConfig()
        )
    )

    effective = load_effective_models_state(
        resolved_path,
        project_cfg=raw_cfg,
        inherit_ancestors=inherit_ancestors,
    )

    active_catalog = (
        catalog
        if catalog is not None
        else discover_model_catalog(raw_cfg, probe_live=probe_live_catalog)
    )

    target_models = collect_target_models(
        effective.available,
        active_catalog,
        harness_filter=harness_filter,
        all_discovered=all_discovered,
    )

    result = EvaluationResult()
    if not target_models:
        result.warnings.append("No target models found to evaluate.")
        return result

    pending = find_unrated_models(
        target_models,
        effective.ratings,
        refresh=refresh,
        force=force,
        max_age_days=max_age_days,
        now=now,
    )

    # Record skipped models for transparent reporting
    for harness, model_ids in target_models.items():
        pending_set = set(pending.get(harness, ()))
        for mid in model_ids:
            if mid in pending_set:
                continue
            existing = lookup_rating(effective.ratings, harness, mid)
            if existing is not None and existing.manual_override:
                result.skipped_manual.setdefault(harness, []).append(mid)
            else:
                result.skipped_rated.setdefault(harness, []).append(mid)

    if not pending:
        return result

    # 1. Fetch Layers 1 & 2 (zero-auth sources)
    src_cat = (
        source_catalog
        if source_catalog is not None
        else fetch_source_catalog(
            cache_dir=cache_dir,
            ttl_hours=effective.evaluation.cache_ttl_hours,
            refresh=refresh,
            http_get=http_get,
        )
    )
    result.source_status = dict(src_cat.source_status)

    # 2. Extract local CLI metadata & group pending slugs by (harness, base_model)
    cli_meta_map = extract_cli_metadata(active_catalog)
    native_only_sets = {
        h: {str(opt.value) for opt in opts}
        for h, opts in active_catalog.native_models.items()
    }

    # Group: (harness, base_model) -> list of (model_id, NormalizedModel)
    groups: Dict[Tuple[str, str], List[Tuple[str, NormalizedModel]]] = {}
    for harness, model_ids in pending.items():
        for mid in model_ids:
            norm = normalize_model_slug(harness, mid)
            groups.setdefault((harness, norm.base_model), []).append((mid, norm))

    # Optional micro-probe per unrated model (or representative base)
    prober = probe_fn or _run_micro_probe
    probe_by_slug: Dict[Tuple[str, str], ProbeOutcome] = {}
    if probe:
        for harness, model_ids in pending.items():
            for mid in model_ids:
                outcome = prober(harness, mid)
                probe_by_slug[(harness, mid)] = outcome
                result.probe_results[f"{harness}:{mid}"] = {
                    "ok": outcome.ok,
                    "latency_sec": outcome.latency_sec,
                    "estimated_tps": outcome.estimated_tps,
                    "error": outcome.error,
                }
                if not outcome.ok and outcome.error:
                    result.warnings.append(f"Probe failed for {harness}:{mid}: {outcome.error}")

    # 3. Synthesize from Layers 1 & 2; collect unmatched base groups for Layer 3
    unmatched_groups: List[
        Tuple[
            str,
            str,
            List[Tuple[str, NormalizedModel]],
            Optional[BenchmarkEvidence],
            Dict[str, Any],
        ]
    ] = []

    eval_cfg: ModelEvaluationConfig = effective.evaluation

    for (harness, base_model), entries in groups.items():
        billing_tier: BillingTier = eval_cfg.billing_for(harness)
        rep_mid, rep_norm = entries[0]
        # Prefer base-level normalized representation for matching
        base_norm = normalize_model_slug(harness, base_model)
        cli_meta = cli_meta_map.get((harness, rep_mid), {}) or cli_meta_map.get(
            (harness, base_model), {}
        )
        rep_probe = probe_by_slug.get((harness, rep_mid))
        rep_tps = rep_probe.estimated_tps if rep_probe and rep_probe.ok else None

        match = src_cat.match(base_norm)
        partial_ev: Optional[BenchmarkEvidence] = None
        base_rating: Optional[ModelRating] = None

        if match is not None:
            evidence, confidence = match
            partial_ev = evidence
            mode = (
                "native"
                if harness == "agy" and rep_mid in native_only_sets.get("agy", set())
                else "herdr"
            )
            base_rating = synthesize_rating(
                base_norm,
                evidence,
                confidence=confidence,
                billing_tier=billing_tier,
                mode=mode,
                cli_context_window=cli_meta.get("context_window"),
                cli_supports_reasoning=cli_meta.get("supports_reasoning"),
                observed_tok_per_sec=rep_tps,
            )

        if base_rating is not None:
            for mid, norm in entries:
                if norm.encoded_effort is not None:
                    final_rating = apply_effort_calibration(
                        base_rating,
                        norm.encoded_effort,
                        harness=harness,
                        model_id=mid,
                    )
                else:
                    mode = (
                        "native"
                        if harness == "agy" and mid in native_only_sets.get("agy", set())
                        else "herdr"
                    )
                    final_rating = base_rating.model_copy(
                        update={
                            "supported_efforts": infer_supported_efforts(
                                harness, mid, mode=mode
                            )
                        }
                    )
                    final_rating.recommended_roles = derive_recommended_roles(final_rating)
                result.evaluated.setdefault(harness, {})[mid] = final_rating
        else:
            unmatched_groups.append((harness, base_model, entries, partial_ev, cli_meta))

    # 4. Layer 3: Batched bounded harness LLM evaluator for remaining unmatched base models
    if unmatched_groups:
        if not use_llm:
            for harness, _, entries, _, _ in unmatched_groups:
                for mid, _ in entries:
                    result.unresolved.setdefault(harness, []).append(mid)
            result.warnings.append(
                f"{result.unresolved_count} model(s) had no zero-auth benchmark match "
                "(--no-llm disabled Layer-3 harness evaluation)."
            )
        else:
            try:
                eval_bind = select_evaluator_binding(
                    raw_cfg,
                    explicit_binding=evaluator_binding,
                    available=effective.available,
                    ratings=merge_ratings(effective.ratings, result.evaluated),
                )
            except ValueError as err:
                eval_bind = None
                result.warnings.append(str(err))

            if eval_bind is None:
                for harness, _, entries, _, _ in unmatched_groups:
                    for mid, _ in entries:
                        result.unresolved.setdefault(harness, []).append(mid)
                result.warnings.append(
                    "No headless-CLI-capable evaluator binding available for Layer-3 evaluation. "
                    "Pass --evaluator <harness:model> (agy/pi/claude/opencode) or use --no-llm."
                )
            else:
                result.evaluator_used = eval_bind.to_slug()
                batch_items: List[Dict[str, Any]] = []
                group_by_key: Dict[str, Any] = {}
                for item_tuple in unmatched_groups:
                    harness, base_model, entries, partial_ev, cli_meta = item_tuple
                    key = f"{harness}:{base_model}"
                    group_by_key[key] = item_tuple
                    rep_mid, rep_norm = entries[0]
                    rep_probe = probe_by_slug.get((harness, rep_mid))
                    batch_items.append(
                        {
                            "key": key,
                            "harness": harness,
                            "base_model": base_model,
                            "variants": [mid for mid, _ in entries],
                            "family_hint": rep_norm.family,
                            "billing_tier": (
                                "free" if rep_norm.is_free_route else eval_cfg.billing_for(harness)
                            ),
                            "known_context_window": (
                                (partial_ev.context_window if partial_ev else None)
                                or cli_meta.get("context_window")
                            ),
                            "known_input_price_per_1m": (
                                partial_ev.input_price_per_1m if partial_ev else None
                            ),
                            "known_output_price_per_1m": (
                                partial_ev.output_price_per_1m if partial_ev else None
                            ),
                            "known_description": (
                                partial_ev.description
                                if partial_ev and partial_ev.description
                                else None
                            ),
                            "known_release_date": (
                                partial_ev.release_date if partial_ev else None
                            ),
                            "cli_detail": cli_meta.get("display_detail"),
                            "observed_latency_sec": (
                                rep_probe.latency_sec if rep_probe and rep_probe.ok else None
                            ),
                        }
                    )

                prompt = _build_llm_eval_prompt(batch_items)
                runner = llm_eval_fn or _run_llm_evaluator
                parsed_llm: Dict[str, Dict[str, Any]] = {}
                try:
                    raw_out = runner(eval_bind, prompt)
                    parsed_llm = parse_llm_eval_response(raw_out, list(group_by_key.keys()))
                except Exception as err:
                    result.warnings.append(f"Layer-3 harness evaluator error: {err}")

                for key, item_tuple in group_by_key.items():
                    harness, base_model, entries, partial_ev, cli_meta = item_tuple
                    llm_entry = parsed_llm.get(key)
                    if llm_entry is None:
                        for mid, _ in entries:
                            result.unresolved.setdefault(harness, []).append(mid)
                        continue

                    base_norm = normalize_model_slug(harness, base_model)
                    rep_mid, _ = entries[0]
                    rep_probe = probe_by_slug.get((harness, rep_mid))
                    mode = (
                        "native"
                        if harness == "agy" and rep_mid in native_only_sets.get("agy", set())
                        else "herdr"
                    )
                    base_rating = _rating_from_llm_entry(
                        base_norm,
                        llm_entry,
                        billing_tier=eval_cfg.billing_for(harness),
                        partial_evidence=partial_ev,
                        cli_meta=cli_meta,
                        probe_outcome=rep_probe,
                        mode=mode,
                    )
                    for mid, norm in entries:
                        if norm.encoded_effort is not None:
                            final_rating = apply_effort_calibration(
                                base_rating,
                                norm.encoded_effort,
                                harness=harness,
                                model_id=mid,
                            )
                        else:
                            m_mode = (
                                "native"
                                if harness == "agy" and mid in native_only_sets.get("agy", set())
                                else "herdr"
                            )
                            final_rating = base_rating.model_copy(
                                update={
                                    "supported_efforts": infer_supported_efforts(
                                        harness, mid, mode=m_mode
                                    )
                                }
                            )
                            final_rating.recommended_roles = derive_recommended_roles(final_rating)
                        result.evaluated.setdefault(harness, {})[mid] = final_rating

    # 5. Atomic persistence into target .herdr-master.toml
    if not dry_run and result.evaluated:
        updated_ratings = merge_ratings(raw_cfg.models.ratings, result.evaluated)
        raw_cfg.models.ratings = updated_ratings
        if resolved_path is not None or cfg is None:
            saved = save_project_config(raw_cfg, resolved_path)
            result.saved_path = saved
        if cfg is not None:
            cfg.models.ratings = updated_ratings

    return result
