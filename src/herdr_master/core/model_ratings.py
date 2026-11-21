"""Normalized model evaluation profiles, slug normalization, and rating utilities.

Ratings are stored alongside ``[models.available]`` under ``[models.ratings]``
and ``[models.evaluation]`` in ``.herdr-master.toml``. Existing configs without
ratings load identically with ``None`` and keep unrestricted allowlist semantics.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from herdr_master.core.catalog import (
    _AGY_TIERS,
    HarnessKind,
    _allowed_efforts,
)

__all__ = [
    "BILLING_TIERS",
    "EFFORT_DELTAS",
    "RATING_SCHEMA_VERSION",
    "RECOMMENDED_ROLES",
    "BillingTier",
    "ConfidenceLevel",
    "ModelEvaluationConfig",
    "ModelRating",
    "ModelRatingsMap",
    "NormalizedModel",
    "apply_effort_calibration",
    "clamp_score",
    "PROVIDER_DEFAULT_WORKER_TIERS",
    "default_worker_tier_for_model",
    "derive_recommended_roles",
    "derive_worker_tier",
    "effective_worker_tier",
    "detect_model_family",
    "find_unrated_models",
    "format_context_window",
    "format_rated_models_table",
    "infer_supported_efforts",
    "lookup_rating",
    "merge_evaluation_configs",
    "merge_ratings",
    "normalize_model_slug",
    "score_context_handling",
    "score_cost_efficiency",
    "split_effort_suffix",
    "utc_now_iso",
]

RATING_SCHEMA_VERSION = 1
BILLING_TIERS: Tuple[str, ...] = ("free", "subscription", "api")
RECOMMENDED_ROLES: Tuple[str, ...] = (
    "scout",
    "direct",
    "coder",
    "architect",
    "reviewer",
    "council",
    "router",
)

BillingTier = Literal["free", "subscription", "api"]
ConfidenceLevel = Literal["high", "medium", "low"]

_HARNESS_VALUES = {kind.value for kind in HarnessKind}

_ENCODED_EFFORT_MAP: Dict[str, str] = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}

EFFORT_DELTAS: Dict[str, Dict[str, int]] = {
    "low": {
        "intelligence": -6,
        "coding": -5,
        "agentic": -4,
        "speed": 15,
        "cost_efficiency": 15,
        "context_handling": 0,
    },
    "medium": {
        "intelligence": 0,
        "coding": 0,
        "agentic": 0,
        "speed": 0,
        "cost_efficiency": 0,
        "context_handling": 0,
    },
    "high": {
        "intelligence": 4,
        "coding": 4,
        "agentic": 3,
        "speed": -10,
        "cost_efficiency": -10,
        "context_handling": 1,
    },
    "max": {
        "intelligence": 7,
        "coding": 6,
        "agentic": 5,
        "speed": -20,
        "cost_efficiency": -18,
        "context_handling": 2,
    },
}

_FREE_ROUTE_PATTERNS = (
    re.compile(r"(?:^|[:/_-])free(?:$|[:/_-])", re.IGNORECASE),
)

_PROVIDER_PREFIX_ALIASES: Dict[str, str] = {
    "openai-codex": "openai",
    "google-gemini": "google",
    "google-vertex": "google",
    "vertex": "google",
    "x-ai": "xai",
    "anthropic-claude": "anthropic",
}


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 Zulu format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def clamp_score(value: Union[int, float]) -> int:
    """Round and clamp a rating score into [0, 100]."""
    return max(0, min(100, int(round(float(value)))))


def _require_harness_key(value: Any) -> str:
    if isinstance(value, HarnessKind):
        return value.value
    if isinstance(value, str):
        key = value.strip()
        if key in _HARNESS_VALUES:
            return key
    valid = ", ".join(kind.value for kind in HarnessKind)
    raise ValueError(f"Unknown harness key {value!r}. Expected one of: {valid}.")


def _clean_model_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Model id must be a non-empty string, got {type(value).__name__}.")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Model id must not be blank.")
    return cleaned


class ModelRating(BaseModel):
    """Normalized capability, speed, cost, and provenance profile for one model."""

    model_config = ConfigDict(extra="allow")

    intelligence: int = Field(..., ge=0, le=100)
    coding: int = Field(..., ge=0, le=100)
    agentic: int = Field(..., ge=0, le=100)
    speed: int = Field(..., ge=0, le=100)
    cost_efficiency: int = Field(..., ge=0, le=100)
    context_handling: int = Field(..., ge=0, le=100)

    family: str = "unknown"
    billing_tier: BillingTier = "api"
    worker_tier: Optional[Literal[1, 2, 3]] = None
    worker_tier_override: bool = False
    context_window: Optional[int] = None
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    output_tokens_per_sec: Optional[float] = None
    supports_reasoning: bool = False
    supported_efforts: List[str] = Field(default_factory=list)
    recommended_roles: List[str] = Field(default_factory=list)
    notes: str = ""

    source: str = "unknown"
    confidence: ConfidenceLevel = "low"
    canonical_match: Optional[str] = None
    evaluated_at: str = Field(default_factory=utc_now_iso)
    manual_override: bool = False
    schema_version: int = RATING_SCHEMA_VERSION

    @field_validator(
        "intelligence",
        "coding",
        "agentic",
        "speed",
        "cost_efficiency",
        "context_handling",
        mode="before",
    )
    @classmethod
    def _coerce_score(cls, value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Rating score must be numeric, got {value!r}.")
        return clamp_score(value)

    @field_validator("worker_tier", mode="before")
    @classmethod
    def _validate_worker_tier(cls, value: Any) -> Any:
        if value is not None and (type(value) is not int or value not in (1, 2, 3)):
            raise ValueError("worker_tier must be 1, 2, 3, or None.")
        return value

    @model_validator(mode="after")
    def _validate_worker_tier_override(self) -> ModelRating:
        if self.worker_tier_override and self.worker_tier is None:
            raise ValueError("worker_tier_override requires worker_tier.")
        return self

    @field_validator("billing_tier", mode="before")
    @classmethod
    def _coerce_billing_tier(cls, value: Any) -> str:
        if value is None:
            return "api"
        token = str(value).strip().lower()
        if token not in BILLING_TIERS:
            raise ValueError(
                f"Unsupported billing_tier {value!r}. Expected one of: {', '.join(BILLING_TIERS)}."
            )
        return token

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, value: Any) -> str:
        if value is None:
            return "low"
        token = str(value).strip().lower()
        if token not in ("high", "medium", "low"):
            raise ValueError(
                f"Unsupported confidence {value!r}. Expected 'high', 'medium', or 'low'."
            )
        return token

    @field_validator("family", mode="before")
    @classmethod
    def _clean_family(cls, value: Any) -> str:
        if value is None:
            return "unknown"
        text = str(value).strip().lower()
        return text or "unknown"

    @field_validator("supported_efforts", mode="before")
    @classmethod
    def _clean_efforts(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("supported_efforts must be a list of effort strings.")
        result: List[str] = []
        for item in value:
            eff = str(item).strip().lower()
            if eff == "xhigh":
                eff = "max"
            if eff in ("low", "medium", "high", "max") and eff not in result:
                result.append(eff)
        return result

    @field_validator("recommended_roles", mode="before")
    @classmethod
    def _clean_roles(cls, value: Any) -> List[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("recommended_roles must be a list of role strings.")
        result: List[str] = []
        for item in value:
            role = str(item).strip().lower()
            if role in RECOMMENDED_ROLES and role not in result:
                result.append(role)
        return result

    @field_validator("notes", mode="before")
    @classmethod
    def _clean_notes(cls, value: Any) -> str:
        if value is None:
            return ""
        text = " ".join(str(value).split())
        return text[:200]

    def compact_badge(self) -> str:
        """Short inline summary suitable for TUI rows and compact listings."""
        mark = "*" if self.manual_override else ""
        return (
            f"I:{self.intelligence} C:{self.coding} "
            f"S:{self.speed} $:{self.cost_efficiency}{mark}"
        )

    def is_stale(
        self,
        max_age_days: int,
        *,
        now: Optional[datetime] = None,
    ) -> bool:
        """Return True if evaluated_at is missing/unparseable or older than max_age_days."""
        if max_age_days <= 0:
            return True
        if not self.evaluated_at:
            return True
        try:
            ts = datetime.fromisoformat(self.evaluated_at.replace("Z", "+00:00"))
        except ValueError:
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ref = now or datetime.now(timezone.utc)
        return (ref - ts) > timedelta(days=max_age_days)


class ModelEvaluationConfig(BaseModel):
    """Per-project or global settings governing model evaluation and billing tiers."""

    model_config = ConfigDict(extra="allow")

    billing: Dict[str, BillingTier] = Field(default_factory=dict)
    cache_ttl_hours: int = Field(default=24, ge=0)
    max_age_days: int = Field(default=30, ge=1)

    @field_validator("billing", mode="before")
    @classmethod
    def _validate_billing(cls, value: Any) -> Dict[str, BillingTier]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("models.evaluation.billing must be a mapping of harness -> tier.")
        cleaned: Dict[str, BillingTier] = {}
        for key, tier in value.items():
            harness = _require_harness_key(key)
            tier_str = str(tier).strip().lower()
            if tier_str not in BILLING_TIERS:
                raise ValueError(
                    f"Invalid billing tier {tier!r} for harness {harness!r}. "
                    f"Expected one of: {', '.join(BILLING_TIERS)}."
                )
            cleaned[harness] = tier_str  # type: ignore[assignment]
        return cleaned

    def billing_for(self, harness: Union[HarnessKind, str]) -> BillingTier:
        """Return configured billing tier for a harness, defaulting to 'api'."""
        key = harness.value if isinstance(harness, HarnessKind) else str(harness).strip().lower()
        return self.billing.get(key, "api")


ModelRatingsMap = Dict[str, Dict[str, ModelRating]]


def validate_ratings_map(value: Any) -> Optional[ModelRatingsMap]:
    """Validate a raw ``[models.ratings]`` table into ``{harness: {model_id: ModelRating}}``."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("models.ratings must be a table of harness keys to model rating tables.")
    cleaned: ModelRatingsMap = {}
    for raw_harness, raw_models in value.items():
        harness = _require_harness_key(raw_harness)
        if not isinstance(raw_models, dict):
            raise ValueError(
                f"Ratings for harness {harness!r} must be a table of model_id -> rating."
            )
        harness_map: Dict[str, ModelRating] = {}
        for raw_model_id, raw_rating in raw_models.items():
            model_id = _clean_model_id(raw_model_id)
            if isinstance(raw_rating, ModelRating):
                harness_map[model_id] = raw_rating
            elif isinstance(raw_rating, dict):
                harness_map[model_id] = ModelRating.model_validate(raw_rating)
            else:
                raise ValueError(
                    f"Rating for {harness}:{model_id} must be a table, "
                    f"got {type(raw_rating).__name__}."
                )
        cleaned[harness] = harness_map
    return cleaned


@dataclass(frozen=True)
class NormalizedModel:
    """Decomposed model identifier used for benchmark matching and calibration."""

    harness: str
    model_id: str
    base_model: str
    encoded_effort: Optional[str]
    canonical_candidates: Tuple[str, ...]
    family: str
    is_free_route: bool


def split_effort_suffix(model_id: str) -> Tuple[str, Optional[str]]:
    """Split a trailing effort suffix (``-low``, ``-medium``, ``-high``, ``-xhigh``, ``-max``)."""
    cleaned = model_id.strip()
    lowered = cleaned.lower()
    for suffix in ("-xhigh", "-medium", "-high", "-low", "-max"):
        if lowered.endswith(suffix) and len(cleaned) > len(suffix):
            base = cleaned[: -len(suffix)]
            # Guard against degenerate stems like "claude-max" or bare provider prefixes.
            if not base or base.endswith(("/", ":", "-")):
                continue
            return base, _ENCODED_EFFORT_MAP[suffix[1:]]
    return cleaned, None


def detect_model_family(harness: Union[HarnessKind, str], model_id: str) -> str:
    """Identify the broad model lineage for cross-family diversity checks."""
    h_key = harness.value if isinstance(harness, HarnessKind) else str(harness).strip().lower()
    lowered = model_id.strip().lower()
    # Strip kilo gateway tildes like "kilo/~anthropic/claude-..."
    cleaned = lowered.replace("kilo/~", "").replace("kilo/", "")

    checks: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
        ("anthropic", ("anthropic/", "claude", "opus", "sonnet", "haiku")),
        ("google", ("google/", "gemini", "gemma")),
        ("xai", ("xai/", "x-ai/", "grok")),
        ("deepseek", ("deepseek",)),
        ("qwen", ("qwen", "qwq")),
        ("meta", ("meta-llama/", "meta/", "llama")),
        ("mistral", ("mistral", "codestral", "devstral", "mixtral")),
        ("nous", ("nousresearch/", "hermes-")),
        ("moonshot", ("moonshot", "kimi")),
        ("zhipu", ("zhipu", "glm-")),
        (
            "openai",
            ("openai/", "openai-codex/", "gpt-", "gpt4", "gpt5", "gpt6", "codex", "o1", "o3", "o4"),
        ),
    )
    for family, needles in checks:
        for needle in needles:
            if needle in cleaned:
                # Avoid false positive "o1"/"o3"/"o4" inside unrelated words.
                if needle in ("o1", "o3", "o4"):
                    if re.search(rf"(?:^|[/:-]){needle}(?:$|[-_./:])", cleaned):
                        return family
                    continue
                return family

    if h_key == "agy" and lowered in _AGY_TIERS:
        return "google"
    if h_key == "claude":
        return "anthropic"
    if h_key == "gemini":
        return "google"
    if h_key == "codex":
        return "openai"
    return "unknown"


def _is_free_route(model_id: str) -> bool:
    return any(pattern.search(model_id) for pattern in _FREE_ROUTE_PATTERNS)


def normalize_model_slug(
    harness: Union[HarnessKind, str],
    model_id: str,
) -> NormalizedModel:
    """Normalize a harness model ID into canonical lookup candidates and effort info."""
    h_key = harness.value if isinstance(harness, HarnessKind) else str(harness).strip().lower()
    cleaned = _clean_model_id(model_id)
    is_free = _is_free_route(cleaned)
    base_model, encoded_effort = split_effort_suffix(cleaned)
    family = detect_model_family(h_key, cleaned)

    candidates: List[str] = []

    def add(key: str) -> None:
        norm = key.strip().lower()
        if norm and norm not in candidates:
            candidates.append(norm)

    # Raw & base forms
    add(cleaned)
    add(base_model)

    # Normalize kilo/~provider/model and kilo/provider/model
    stripped = base_model
    lowered = base_model.lower()
    if lowered.startswith("kilo/~"):
        stripped = base_model[len("kilo/~") :]
        add(stripped)
    elif lowered.startswith("kilo/") and lowered.count("/") >= 2:
        stripped = base_model[len("kilo/") :]
        add(stripped)

    # Strip trailing :free / :nitro / :floor / -latest
    for raw_cand in list(candidates):
        if ":" in raw_cand:
            add(raw_cand.split(":", 1)[0])
        if raw_cand.endswith("-latest"):
            add(raw_cand[: -len("-latest")])

    # Provider normalization (e.g. openai-codex/gpt-5.4 -> openai/gpt-5.4 + gpt-5.4)
    if "/" in stripped:
        provider, rest = stripped.split("/", 1)
        prov_norm = _PROVIDER_PREFIX_ALIASES.get(provider.lower(), provider.lower())
        add(f"{prov_norm}/{rest}")
        add(rest)
        if rest.endswith("-latest"):
            add(f"{prov_norm}/{rest[:-len('-latest')]}")
            add(rest[: -len("-latest")])
    else:
        if family != "unknown":
            add(f"{family}/{stripped}")

    # Harness-specific short aliases
    if h_key == "claude":
        alias_map = {
            "opus": (
                "anthropic/claude-opus-5-5",
                "claude-opus-5-5",
                "anthropic/claude-opus-4-8",
                "claude-opus-4-8",
                "anthropic/claude-opus-4-6",
                "claude-opus-4-6",
                "claude-opus-4.6",
                "claude-opus",
            ),
            "sonnet": (
                "anthropic/claude-sonnet-5-5",
                "claude-sonnet-5-5",
                "anthropic/claude-sonnet-5",
                "claude-sonnet-5",
                "anthropic/claude-sonnet-4-6",
                "claude-sonnet-4-6",
                "claude-sonnet-4.6",
                "claude-sonnet",
            ),
            "haiku": (
                "anthropic/claude-haiku-5-5",
                "claude-haiku-5-5",
                "anthropic/claude-haiku-4-5",
                "claude-haiku-4-5",
                "claude-haiku-4.5",
                "claude-haiku",
            ),
        }
        for alias_target in alias_map.get(cleaned.lower(), ()):
            add(alias_target)
    elif h_key == "agy":
        tier_map = {
            "flash_lite": ("google/gemini-2.5-flash-lite", "gemini-2.5-flash-lite"),
            "flash": ("google/gemini-2.5-flash", "gemini-2.5-flash"),
            "pro": ("google/gemini-2.5-pro", "gemini-2.5-pro"),
        }
        for tier_target in tier_map.get(cleaned.lower(), ()):
            add(tier_target)

    # Dot/dash version equivalence (e.g., claude-sonnet-4-6 <-> claude-sonnet-4.6)
    for cand in list(candidates):
        dotted = re.sub(r"(?<=\d)-(?=\d)", ".", cand)
        dashed = re.sub(r"(?<=\d)\.(?=\d)", "-", cand)
        add(dotted)
        add(dashed)

    return NormalizedModel(
        harness=h_key,
        model_id=cleaned,
        base_model=base_model,
        encoded_effort=encoded_effort,
        canonical_candidates=tuple(candidates),
        family=family,
        is_free_route=is_free,
    )


def infer_supported_efforts(
    harness: Union[HarnessKind, str],
    model_id: str,
    *,
    mode: str = "herdr",
) -> List[str]:
    """Return configurable effort levels for ``(harness, model_id)`` (empty if fixed)."""
    h_key = harness.value if isinstance(harness, HarnessKind) else str(harness).strip().lower()
    cleaned = model_id.strip()
    if h_key == "agy" and mode == "herdr":
        _, encoded = split_effort_suffix(cleaned)
        if encoded is not None:
            return []
    try:
        efforts = _allowed_efforts(h_key, cleaned, mode=mode)
    except Exception:
        return []
    return list(efforts)


def score_cost_efficiency(
    billing_tier: BillingTier,
    *,
    input_price_per_1m: Optional[float] = None,
    output_price_per_1m: Optional[float] = None,
    is_free_route: bool = False,
    intelligence: int = 75,
    encoded_effort: Optional[str] = None,
) -> int:
    """Compute a 0–100 cost efficiency score aware of free, subscription, and API billing."""
    if is_free_route or billing_tier == "free":
        return 98 if encoded_effort != "max" else 94

    if (
        input_price_per_1m is not None
        and output_price_per_1m is not None
        and input_price_per_1m <= 0.0
        and output_price_per_1m <= 0.0
    ):
        return 98

    if billing_tier == "subscription":
        # Subscription models don't incur marginal $/token, but heavy flagship/max
        # tiers burn rate-limit quota much faster than flash/low tiers.
        if intelligence >= 92:
            base = 58
        elif intelligence >= 85:
            base = 70
        elif intelligence >= 75:
            base = 82
        else:
            base = 90
        if encoded_effort == "low":
            base += 10
        elif encoded_effort == "high":
            base -= 8
        elif encoded_effort == "max":
            base -= 16
        return clamp_score(base)

    # API billing tier: inverse log-scaled blended cost (3:1 input:output typical coding ratio)
    if input_price_per_1m is not None or output_price_per_1m is not None:
        inp = float(input_price_per_1m or 0.0)
        out = float(output_price_per_1m if output_price_per_1m is not None else inp * 3.0)
        blended = max(0.01, 0.75 * inp + 0.25 * out)
        # Anchor: $0.05 -> ~98, $0.50 -> ~83, $2.50 -> ~70, $10 -> ~56, $30 -> ~43
        raw = 81.0 - 17.5 * math.log10(blended)
        # Mild quality-per-dollar adjustment so strong mid-price models beat weak cheap ones
        quality_bonus = (intelligence - 75) * 0.15
        return clamp_score(raw + quality_bonus)

    # Unknown API pricing fallback based on intelligence tier
    if intelligence >= 92:
        return 50
    if intelligence >= 84:
        return 66
    if intelligence >= 74:
        return 80
    return 88


def score_context_handling(
    context_window: Optional[int],
    intelligence: int = 75,
) -> int:
    """Compute a 0–100 context handling score from context window size and model capability."""
    if context_window is None or context_window <= 0:
        return clamp_score(60 + (intelligence - 70) * 0.35)
    if context_window >= 1_000_000:
        win_score = 96
    elif context_window >= 400_000:
        win_score = 90
    elif context_window >= 180_000:
        win_score = 84
    elif context_window >= 120_000:
        win_score = 78
    elif context_window >= 60_000:
        win_score = 66
    elif context_window >= 30_000:
        win_score = 54
    else:
        win_score = 40
    return clamp_score(0.8 * win_score + 0.2 * intelligence)


def derive_recommended_roles(rating: ModelRating) -> List[str]:
    """Derive recommended AiHead / pipeline roles from a model's 6 core scores."""
    roles: List[str] = []
    if (
        rating.speed >= 70
        and rating.cost_efficiency >= 65
        and rating.context_handling >= 65
        and rating.intelligence >= 50
    ):
        roles.append("scout")
    if rating.speed >= 65 and rating.coding >= 65 and rating.cost_efficiency >= 60:
        roles.append("direct")
    if rating.coding >= 72 and rating.agentic >= 65:
        roles.append("coder")
    if rating.intelligence >= 85 and rating.coding >= 80:
        roles.append("architect")
    if rating.coding >= 82 and rating.intelligence >= 80:
        roles.append("reviewer")
    if rating.intelligence >= 85:
        roles.append("council")
    if rating.intelligence >= 82 and rating.coding >= 75:
        roles.append("router")

    if not roles:
        if rating.coding >= rating.speed:
            roles.append("coder")
        else:
            roles.append("direct")
    return roles


def apply_effort_calibration(
    base: ModelRating,
    encoded_effort: str,
    *,
    harness: Union[HarnessKind, str],
    model_id: str,
) -> ModelRating:
    """Derive a calibrated rating for an effort-encoded slug from its base model rating."""
    effort_key = _ENCODED_EFFORT_MAP.get(encoded_effort.strip().lower(), "medium")
    deltas = EFFORT_DELTAS[effort_key]
    calibrated = base.model_copy(
        update={
            "intelligence": clamp_score(base.intelligence + deltas["intelligence"]),
            "coding": clamp_score(base.coding + deltas["coding"]),
            "agentic": clamp_score(base.agentic + deltas["agentic"]),
            "speed": clamp_score(base.speed + deltas["speed"]),
            "cost_efficiency": clamp_score(base.cost_efficiency + deltas["cost_efficiency"]),
            "context_handling": clamp_score(base.context_handling + deltas["context_handling"]),
            "supports_reasoning": True,
            "supported_efforts": infer_supported_efforts(harness, model_id, mode="herdr"),
        }
    )
    calibrated.recommended_roles = derive_recommended_roles(calibrated)
    return calibrated


def merge_ratings(
    base: Optional[ModelRatingsMap],
    override: Optional[ModelRatingsMap],
) -> ModelRatingsMap:
    """Deep-merge two rating maps; entries in ``override`` win per ``(harness, model_id)``."""
    merged: ModelRatingsMap = {}
    for layer in (base, override):
        if not layer:
            continue
        for harness, models in layer.items():
            target = merged.setdefault(str(harness), {})
            for model_id, rating in models.items():
                target[str(model_id)] = rating.model_copy(deep=True)
    return merged


def merge_evaluation_configs(
    base: Optional[ModelEvaluationConfig],
    override: Optional[ModelEvaluationConfig],
) -> ModelEvaluationConfig:
    """Merge home and project evaluation configs (project billing and settings win)."""
    billing: Dict[str, BillingTier] = {}
    cache_ttl = 24
    max_age = 30
    if base is not None:
        billing.update(base.billing)
        cache_ttl = base.cache_ttl_hours
        max_age = base.max_age_days
    if override is not None:
        billing.update(override.billing)
        cache_ttl = override.cache_ttl_hours
        max_age = override.max_age_days
    return ModelEvaluationConfig(
        billing=billing,
        cache_ttl_hours=cache_ttl,
        max_age_days=max_age,
    )


def lookup_rating(
    ratings: Optional[ModelRatingsMap],
    harness: Union[HarnessKind, str],
    model_id: str,
) -> Optional[ModelRating]:
    """Look up a stored rating for ``(harness, model_id)``."""
    if not ratings:
        return None
    h_key = harness.value if isinstance(harness, HarnessKind) else str(harness).strip().lower()
    harness_map = ratings.get(h_key)
    if not harness_map:
        return None
    return harness_map.get(model_id.strip())


def find_unrated_models(
    target_models: Mapping[str, Sequence[str]],
    ratings: Optional[ModelRatingsMap],
    *,
    refresh: bool = False,
    force: bool = False,
    max_age_days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, List[str]]:
    """Return ``{harness: [model_ids]}`` needing evaluation.

    - Models with no stored rating are always included.
    - Models with ``manual_override=True`` are skipped unless ``force=True``.
    - Models with existing ratings are skipped unless ``refresh=True``,
      ``force=True``, or older than ``max_age_days``.
    """
    pending: Dict[str, List[str]] = {}
    for harness, model_ids in target_models.items():
        h_key = str(harness).strip().lower()
        need: List[str] = []
        seen: set[str] = set()
        for raw_id in model_ids:
            model_id = str(raw_id).strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            existing = lookup_rating(ratings, h_key, model_id)
            if existing is None:
                need.append(model_id)
                continue
            if existing.manual_override and not force:
                continue
            if force or refresh:
                need.append(model_id)
                continue
            if max_age_days is not None and existing.is_stale(max_age_days, now=now):
                need.append(model_id)
        if need:
            pending[h_key] = need
    return pending


# Default initial worker-tier classification per provider model family.
# Tier 1 = Cheap / Fast, Tier 2 = Everyday / All-rounder, Tier 3 = Frontier.
# Used for initial setup and auto-derivation before/during evaluation; explicit
# per-model overrides (`worker_tier_override = true`) always take precedence.
PROVIDER_DEFAULT_WORKER_TIERS: Dict[str, Dict[int, Tuple[str, ...]]] = {
    "anthropic": {
        1: ("haiku",),
        2: ("sonnet",),
        3: ("opus",),
    },
    "openai": {
        1: ("luna", "mini", "nano"),
        2: ("terra",),
        3: ("sol",),
    },
    "google": {
        1: ("hydrogen", "flash-lite", "flash_lite"),
        2: ("neon", "flash"),
        3: ("carbon", "argon", "pro"),
    },
}


def default_worker_tier_for_model(model_id: str) -> Optional[int]:
    """Return the built-in default worker tier (1, 2, or 3) for a known provider model slug."""
    slug = model_id.strip().lower()
    if not slug:
        return None
    if slug.endswith(":free") or "/free" in slug:
        return 1
    tokens = set(re.split(r"[^a-z0-9]+", slug))
    for tier_map in PROVIDER_DEFAULT_WORKER_TIERS.values():
        for tier in (1, 2, 3):
            for pattern in tier_map.get(tier, ()):
                if pattern in tokens or (
                    ("-" in pattern or "_" in pattern) and pattern in slug
                ):
                    return tier
    return None


def derive_worker_tier(rating: ModelRating, model_id: Optional[str] = None) -> int:
    """Derive provisional routing class; prefers known provider presets when model_id matches."""
    if model_id:
        preset = default_worker_tier_for_model(model_id)
        if preset is not None:
            return preset
    capability = (rating.coding + rating.intelligence + rating.agentic) / 3
    if capability >= 88:
        return 3
    if capability < 75 or (rating.speed >= 80 and rating.cost_efficiency >= 80):
        return 1
    return 2


def effective_worker_tier(rating: ModelRating) -> int:
    """Use a stored tier, or lazily derive one for a legacy rating."""
    return rating.worker_tier if rating.worker_tier is not None else derive_worker_tier(rating)


def format_context_window(context_window: Optional[int]) -> str:
    """Format a token count compactly (e.g. ``200k``, ``1M``, ``?``)."""
    if context_window is None or context_window <= 0:
        return "?"
    if context_window >= 1_000_000:
        millions = round(context_window / 1_000_000, 2)
        return f"{millions:g}M"
    if context_window >= 1_000:
        return f"{int(round(context_window / 1_000))}k"
    return str(context_window)


def format_rated_models_table(
    models_by_harness: Mapping[str, Sequence[str]],
    ratings: Optional[ModelRatingsMap],
    *,
    mode: str = "herdr",
) -> str:
    """Render a compact 1-line-per-model rated table for CLI display and prompts."""
    lines: List[str] = []
    ordered_harnesses = [k.value for k in HarnessKind if k.value in models_by_harness]
    ordered_harnesses.extend(k for k in models_by_harness if k not in ordered_harnesses)

    for harness in ordered_harnesses:
        for model_id in models_by_harness[harness]:
            rating = lookup_rating(ratings, harness, model_id)
            slug = f"{harness}:{model_id}" if mode != "native" else f"{harness}:{model_id}"
            if rating is None:
                lines.append(f"- {slug} | [unrated]")
                continue
            ctx = format_context_window(rating.context_window)
            eff = (
                f"choose {'|'.join(rating.supported_efforts)}"
                if rating.supported_efforts
                else "fixed/none"
            )
            roles = ",".join(rating.recommended_roles) if rating.recommended_roles else "-"
            tier_src = (
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
                f"worker_tier={effective_worker_tier(rating)} ({tier_src}) | "
                f"effort: {eff} | roles={roles}"
            )
    return "\n".join(lines) if lines else "(no models)"
