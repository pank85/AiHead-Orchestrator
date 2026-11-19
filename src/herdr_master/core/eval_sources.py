"""Zero-auth public benchmark & metadata fetchers with local disk caching.

Supported zero-auth sources (zero external API keys required):
1. models.dev (`https://models.dev/api.json`) — specs, pricing, context, reasoning/tools.
2. OpenRouter (`https://openrouter.ai/api/v1/models?sort=throughput-high-to-low`) —
   specs, live pricing, context windows, and relative throughput ordering.
3. SWE-bench (`SWE-bench/swe-bench.github.io` `data/leaderboards.json`) —
   real-world multi-file coding & agentic issue resolution.
4. Aider Polyglot (`Aider-AI/aider` `polyglot_leaderboard.yml`) —
   multi-language code editing accuracy.
5. LiveBench (`LiveBench/LiveBench` public leaderboard CSV) —
   contamination-free general reasoning and coding scores.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from herdr_master.core.model_ratings import (
    BillingTier,
    ConfidenceLevel,
    ModelRating,
    NormalizedModel,
    clamp_score,
    derive_recommended_roles,
    detect_model_family,
    infer_supported_efforts,
    score_context_handling,
    score_cost_efficiency,
    utc_now_iso,
)

__all__ = [
    "DEFAULT_CACHE_DIR",
    "SOURCE_URLS",
    "BenchmarkEvidence",
    "SourceCatalog",
    "fetch_source_catalog",
    "normalize_benchmark_key",
    "parse_aider_polyglot",
    "parse_livebench_csv",
    "parse_models_dev",
    "parse_openrouter_models",
    "parse_swebench_leaderboards",
    "synthesize_rating",
]

DEFAULT_CACHE_DIR = Path(os.path.expanduser("~/.cache/herdr-master/model-eval-sources"))

SOURCE_URLS: Dict[str, str] = {
    "models_dev": "https://models.dev/api.json",
    "openrouter": "https://openrouter.ai/api/v1/models?sort=throughput-high-to-low",
    "swebench": (
        "https://raw.githubusercontent.com/SWE-bench/swe-bench.github.io/"
        "master/data/leaderboards.json"
    ),
    "aider": (
        "https://raw.githubusercontent.com/Aider-AI/aider/main/"
        "aider/website/_data/polyglot_leaderboard.yml"
    ),
    "livebench": (
        "https://raw.githubusercontent.com/LiveBench/livebench.github.io/main/"
        "public/table_2026_06_25.csv"
    ),
}

_DATE_STAMP_RE = re.compile(r"(?:^|[-_@])202\d(?:[-_]?\d{2}){1,2}(?:$|[-_])")
_PAREN_NOTE_RE = re.compile(r"\([^)]*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9.]+")
_AIDER_CMD_MODEL_RE = re.compile(r"--model\s+([^\s]+)")
_LIVEBENCH_CODING_COLS = frozenset(
    {"code_completion", "code_generation", "javascript", "python", "typescript"}
)
_PROVIDER_PREFIXES = (
    "openai/",
    "anthropic/",
    "google/",
    "x-ai/",
    "xai/",
    "deepseek/",
    "qwen/",
    "meta-llama/",
    "mistralai/",
    "nousresearch/",
    "moonshotai/",
    "z-ai/",
)
_NOISE_TOKENS = frozenset(
    {
        "preview",
        "exp",
        "experimental",
        "latest",
        "beta",
        "instruct",
        "chat",
        "thinking",
        "reasoning",
        "non",
        "standard",
        "high",
        "medium",
        "low",
        "max",
        "xhigh",
        "free",
        "effort",
        "auto",
        "64k",
        "32k",
    }
)


@dataclass
class BenchmarkEvidence:
    """Merged deterministic facts & benchmark scores for one canonical model."""

    canonical_id: str
    display_name: str = ""
    description: str = ""
    release_date: Optional[str] = None
    family: str = "unknown"
    sources: Set[str] = field(default_factory=set)
    context_window: Optional[int] = None
    input_price_per_1m: Optional[float] = None
    output_price_per_1m: Optional[float] = None
    supports_reasoning: Optional[bool] = None
    supports_tools: Optional[bool] = None
    output_tokens_per_sec: Optional[float] = None
    speed_percentile: Optional[float] = None
    swebench_score: Optional[float] = None
    aider_score: Optional[float] = None
    livebench_coding: Optional[float] = None
    livebench_global: Optional[float] = None

    def has_quality_benchmarks(self) -> bool:
        """Return True if at least one independent quality/coding benchmark is present."""
        return any(
            score is not None
            for score in (
                self.swebench_score,
                self.aider_score,
                self.livebench_coding,
                self.livebench_global,
            )
        )

    def merge_from(self, other: "BenchmarkEvidence") -> None:
        """Merge non-null attributes from ``other`` into ``self``."""
        self.sources.update(other.sources)
        if not self.display_name and other.display_name:
            self.display_name = other.display_name
        if not self.description and other.description:
            self.description = other.description
        if self.release_date is None and other.release_date is not None:
            self.release_date = other.release_date
        if self.family == "unknown" and other.family != "unknown":
            self.family = other.family
        if other.context_window is not None and (
            self.context_window is None or other.context_window > self.context_window
        ):
            self.context_window = other.context_window
        if self.input_price_per_1m is None and other.input_price_per_1m is not None:
            self.input_price_per_1m = other.input_price_per_1m
        if self.output_price_per_1m is None and other.output_price_per_1m is not None:
            self.output_price_per_1m = other.output_price_per_1m
        if other.supports_reasoning is True or (
            self.supports_reasoning is None and other.supports_reasoning is not None
        ):
            self.supports_reasoning = other.supports_reasoning
        if other.supports_tools is True or (
            self.supports_tools is None and other.supports_tools is not None
        ):
            self.supports_tools = other.supports_tools
        if self.output_tokens_per_sec is None and other.output_tokens_per_sec is not None:
            self.output_tokens_per_sec = other.output_tokens_per_sec
        if self.speed_percentile is None and other.speed_percentile is not None:
            self.speed_percentile = other.speed_percentile
        if other.swebench_score is not None and (
            self.swebench_score is None or other.swebench_score > self.swebench_score
        ):
            self.swebench_score = other.swebench_score
        if other.aider_score is not None and (
            self.aider_score is None or other.aider_score > self.aider_score
        ):
            self.aider_score = other.aider_score
        if other.livebench_coding is not None and (
            self.livebench_coding is None or other.livebench_coding > self.livebench_coding
        ):
            self.livebench_coding = other.livebench_coding
        if other.livebench_global is not None and (
            self.livebench_global is None or other.livebench_global > self.livebench_global
        ):
            self.livebench_global = other.livebench_global


def normalize_benchmark_key(raw_name: str) -> str:
    """Normalize an upstream model/leaderboard name to a comparable canonical key."""
    text = raw_name.strip().lower()
    text = _PAREN_NOTE_RE.sub(" ", text)
    # Remove agent scaffold prefixes like "OpenHands + Claude Sonnet 4"
    if "+" in text:
        text = text.split("+")[-1]
    if ":" in text and not text.startswith(("http:", "https:")):
        left, right = text.split(":", 1)
        if right.strip() in ("free", "nitro", "floor", "extended", "thinking"):
            text = left
    text = text.replace("kilo/~", "")
    for prefix in _PROVIDER_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    if "/" in text:
        text = text.split("/", 1)[1]
    text = _DATE_STAMP_RE.sub("-", text)
    text = re.sub(r"(?<=\d)-(?=\d)", ".", text)
    text = _NON_ALNUM_RE.sub("-", text).strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text


def _core_tokens(key: str) -> Tuple[str, ...]:
    """Extract distinctive identity tokens (model name + version numbers)."""
    raw_tokens = [tok for tok in key.split("-") if tok]
    filtered = [tok for tok in raw_tokens if tok not in _NOISE_TOKENS]
    return tuple(filtered or raw_tokens)


def _safe_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN check
        return None
    return parsed


def _safe_int(value: Any) -> Optional[int]:
    parsed = _safe_float(value)
    if parsed is None or parsed <= 0:
        return None
    return int(round(parsed))


def _upsert_evidence(
    store: Dict[str, BenchmarkEvidence],
    key: str,
    evidence: BenchmarkEvidence,
) -> None:
    if not key:
        return
    existing = store.get(key)
    if existing is None:
        store[key] = evidence
    else:
        existing.merge_from(evidence)


def parse_models_dev(payload: Any) -> Dict[str, BenchmarkEvidence]:
    """Parse ``https://models.dev/api.json`` into normalized ``BenchmarkEvidence``."""
    results: Dict[str, BenchmarkEvidence] = {}
    if not isinstance(payload, dict):
        return results

    for prov_id, prov_data in payload.items():
        if not isinstance(prov_data, dict):
            continue
        models_obj = prov_data.get("models")
        if isinstance(models_obj, dict):
            model_entries = list(models_obj.items())
        elif isinstance(models_obj, list):
            model_entries = [
                (str(item.get("id") or ""), item)
                for item in models_obj
                if isinstance(item, dict)
            ]
        else:
            continue

        for raw_model_id, entry in model_entries:
            if not isinstance(entry, dict):
                continue
            model_id = str(entry.get("id") or raw_model_id).strip()
            if not model_id:
                continue
            canonical = str(
                entry.get("canonical_model_id")
                or (f"{prov_id}/{model_id}" if "/" not in model_id else model_id)
            ).strip()
            cost = entry.get("cost") if isinstance(entry.get("cost"), dict) else {}
            limit = entry.get("limit") if isinstance(entry.get("limit"), dict) else {}
            inp_cost = _safe_float(cost.get("input"))
            out_cost = _safe_float(cost.get("output"))
            if inp_cost is not None and inp_cost < 0:
                inp_cost = None
            if out_cost is not None and out_cost < 0:
                out_cost = None

            ev = BenchmarkEvidence(
                canonical_id=canonical,
                display_name=str(entry.get("name") or model_id),
                description=str(entry.get("description") or "").strip()[:240],
                release_date=(
                    str(entry.get("release_date")).strip()
                    if entry.get("release_date")
                    else None
                ),
                family=detect_model_family("pi", canonical),
                sources={"models_dev"},
                context_window=_safe_int(limit.get("context")),
                input_price_per_1m=inp_cost,
                output_price_per_1m=out_cost,
                supports_reasoning=(
                    bool(entry["reasoning"]) if isinstance(entry.get("reasoning"), bool) else None
                ),
                supports_tools=(
                    bool(entry["tool_call"]) if isinstance(entry.get("tool_call"), bool) else None
                ),
            )
            _upsert_evidence(results, normalize_benchmark_key(canonical), ev)
            if model_id != canonical:
                _upsert_evidence(results, normalize_benchmark_key(model_id), ev)
    return results


def parse_openrouter_models(payload: Any) -> Dict[str, BenchmarkEvidence]:
    """Parse ``https://openrouter.ai/api/v1/models`` (sorted by throughput when requested)."""
    results: Dict[str, BenchmarkEvidence] = {}
    if not isinstance(payload, dict):
        return results
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return results

    total = len(data)
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else {}
        raw_prompt = _safe_float(pricing.get("prompt"))
        raw_completion = _safe_float(pricing.get("completion"))
        inp_per_1m = (
            round(raw_prompt * 1_000_000, 4)
            if raw_prompt is not None and raw_prompt >= 0
            else None
        )
        out_per_1m = (
            round(raw_completion * 1_000_000, 4)
            if raw_completion is not None and raw_completion >= 0
            else None
        )
        supported_params = entry.get("supported_parameters")
        params_set = (
            {str(p).lower() for p in supported_params}
            if isinstance(supported_params, list)
            else set()
        )
        supports_reasoning = (
            True if ("reasoning" in params_set or "include_reasoning" in params_set) else None
        )
        supports_tools = True if ("tools" in params_set or "tool_choice" in params_set) else None

        # Relative throughput rank percentile (100 = fastest, 10 = slowest)
        speed_pct = 95.0 - (idx / max(1, total - 1)) * 75.0 if total > 1 else 65.0

        canonical = str(entry.get("canonical_slug") or model_id).strip()
        ev = BenchmarkEvidence(
            canonical_id=model_id,
            display_name=str(entry.get("name") or model_id),
            family=detect_model_family("pi", model_id),
            sources={"openrouter"},
            context_window=_safe_int(entry.get("context_length")),
            input_price_per_1m=inp_per_1m,
            output_price_per_1m=out_per_1m,
            supports_reasoning=supports_reasoning,
            supports_tools=supports_tools,
            speed_percentile=round(speed_pct, 1),
        )
        _upsert_evidence(results, normalize_benchmark_key(model_id), ev)
        if canonical and canonical != model_id:
            _upsert_evidence(results, normalize_benchmark_key(canonical), ev)
    return results


def parse_swebench_leaderboards(payload: Any) -> Dict[str, BenchmarkEvidence]:
    """Parse SWE-bench ``leaderboards.json`` (preferring Verified > Bash Only > Lite)."""
    results: Dict[str, BenchmarkEvidence] = {}
    boards: List[Tuple[int, List[Any]]] = []

    def board_priority(name: str) -> int:
        low = name.lower()
        if "verified" in low:
            return 0
        if "bash" in low:
            return 1
        if "multilingual" in low:
            return 2
        if "lite" in low:
            return 3
        return 4

    if isinstance(payload, dict) and isinstance(payload.get("leaderboards"), list):
        for board in payload["leaderboards"]:
            if isinstance(board, dict) and isinstance(board.get("results"), list):
                boards.append((board_priority(str(board.get("name") or "")), board["results"]))
    elif isinstance(payload, dict):
        for name, val in payload.items():
            if isinstance(val, list):
                boards.append((board_priority(str(name)), val))
    elif isinstance(payload, list):
        boards.append((0, payload))

    boards.sort(key=lambda item: item[0])
    seen_priority: Dict[str, int] = {}

    for prio, rows in boards:
        for row in rows:
            if not isinstance(row, dict):
                continue
            resolved = _safe_float(row.get("resolved"))
            if resolved is None:
                continue
            raw_names: List[str] = []
            tags = row.get("tags")
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, str) and tag.lower().startswith("model:"):
                        tagged = tag.split(":", 1)[1].strip()
                        if tagged:
                            raw_names.append(tagged)
            for field_name in ("model_display", "name", "model"):
                val = str(row.get(field_name) or "").strip()
                if val and val not in raw_names:
                    raw_names.append(val)
            if not raw_names:
                continue
            canonical_name = raw_names[0]
            for raw_name in raw_names:
                key = normalize_benchmark_key(raw_name)
                if not key:
                    continue
                prev_prio = seen_priority.get(key)
                if prev_prio is not None and prio > prev_prio:
                    continue
                seen_priority[key] = prio
                ev = BenchmarkEvidence(
                    canonical_id=canonical_name,
                    display_name=str(row.get("model_display") or canonical_name),
                    family=detect_model_family("pi", canonical_name),
                    sources={"swebench"},
                    swebench_score=resolved,
                )
                _upsert_evidence(results, key, ev)
    return results


def parse_aider_polyglot(yaml_text: str) -> Dict[str, BenchmarkEvidence]:
    """Parse Aider ``polyglot_leaderboard.yml`` using ``ruamel.yaml``."""
    results: Dict[str, BenchmarkEvidence] = {}
    if not yaml_text.strip():
        return results
    try:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        parsed = yaml.load(yaml_text)
    except Exception:
        return results
    if not isinstance(parsed, list):
        return results

    for row in parsed:
        if not isinstance(row, dict):
            continue
        raw_model = str(row.get("model") or "").strip()
        if not raw_model:
            continue
        pass_rate = _safe_float(row.get("pass_rate_2"))
        if pass_rate is None:
            pass_rate = _safe_float(row.get("pass_rate_1"))
        if pass_rate is None:
            continue
        candidate_names: List[str] = [raw_model]
        cmd_str = str(row.get("command") or "")
        cmd_match = _AIDER_CMD_MODEL_RE.search(cmd_str)
        if cmd_match:
            slug_from_cmd = cmd_match.group(1).strip().strip("\"'")
            if slug_from_cmd and slug_from_cmd not in candidate_names:
                candidate_names.append(slug_from_cmd)
        for name_val in candidate_names:
            key = normalize_benchmark_key(name_val)
            if not key:
                continue
            ev = BenchmarkEvidence(
                canonical_id=raw_model,
                display_name=raw_model,
                family=detect_model_family("pi", name_val),
                sources={"aider"},
                aider_score=pass_rate,
            )
            _upsert_evidence(results, key, ev)
    return results


def parse_livebench_csv(csv_text: str) -> Dict[str, BenchmarkEvidence]:
    """Parse LiveBench public leaderboard CSV (both grouped summary and per-task tables)."""
    results: Dict[str, BenchmarkEvidence] = {}
    if not csv_text.strip():
        return results
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return results

    lower_fields = {f.strip().lower(): f for f in reader.fieldnames if f}
    model_col = (
        lower_fields.get("model")
        or lower_fields.get("model_name")
        or lower_fields.get("name")
    )
    if not model_col:
        return results

    coding_col = lower_fields.get("coding") or lower_fields.get("coding average")
    global_col = (
        lower_fields.get("global average")
        or lower_fields.get("global")
        or lower_fields.get("average")
    )
    reasoning_col = lower_fields.get("reasoning") or lower_fields.get("reasoning average")
    task_coding_cols = [
        orig for low, orig in lower_fields.items() if low in _LIVEBENCH_CODING_COLS
    ]
    all_task_cols = [orig for orig in reader.fieldnames if orig and orig != model_col]

    for row in reader:
        raw_model = str(row.get(model_col) or "").strip()
        if not raw_model:
            continue
        coding = _safe_float(row.get(coding_col)) if coding_col else None
        glob = _safe_float(row.get(global_col)) if global_col else None
        if glob is None and reasoning_col:
            glob = _safe_float(row.get(reasoning_col))

        # Per-task LiveBench table fallback (e.g. table_2026_06_25.csv)
        if coding is None and task_coding_cols:
            c_vals = [_safe_float(row.get(col)) for col in task_coding_cols]
            c_nums = [v for v in c_vals if v is not None]
            if c_nums:
                coding = round(sum(c_nums) / len(c_nums), 2)
        if glob is None and all_task_cols:
            g_vals = [_safe_float(row.get(col)) for col in all_task_cols]
            g_nums = [v for v in g_vals if v is not None]
            if g_nums:
                glob = round(sum(g_nums) / len(g_nums), 2)

        if coding is None and glob is None:
            continue
        key = normalize_benchmark_key(raw_model)
        if not key:
            continue
        ev = BenchmarkEvidence(
            canonical_id=raw_model,
            display_name=raw_model,
            family=detect_model_family("pi", raw_model),
            sources={"livebench"},
            livebench_coding=coding,
            livebench_global=glob,
        )
        _upsert_evidence(results, key, ev)
    return results


@dataclass
class SourceCatalog:
    """Unified index of deterministic evidence across all fetched zero-auth sources."""

    entries: Dict[str, BenchmarkEvidence] = field(default_factory=dict)
    source_status: Dict[str, str] = field(default_factory=dict)
    _token_index: Dict[Tuple[str, ...], List[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self._token_index.clear()
        for key in self.entries:
            tokens = _core_tokens(key)
            if tokens:
                self._token_index.setdefault(tokens, []).append(key)

    def merge_entries(self, incoming: Dict[str, BenchmarkEvidence]) -> None:
        for key, ev in incoming.items():
            _upsert_evidence(self.entries, key, ev)
        self._rebuild_index()

    def match(
        self,
        normalized: NormalizedModel,
    ) -> Optional[Tuple[BenchmarkEvidence, ConfidenceLevel]]:
        """Match a normalized model slug against indexed benchmark evidence."""
        # 1. Exact normalized key match
        for cand in normalized.canonical_candidates:
            key = normalize_benchmark_key(cand)
            if key in self.entries:
                ev = self.entries[key]
                conf: ConfidenceLevel = "high" if ev.has_quality_benchmarks() else "medium"
                return ev, conf

        # 2. Exact core-token match (requires identical family + version numbers)
        for cand in normalized.canonical_candidates:
            key = normalize_benchmark_key(cand)
            tokens = _core_tokens(key)
            if len(tokens) < 2:
                continue
            hits = self._token_index.get(tokens)
            if hits:
                # Prefer hit that has quality benchmarks & most sources
                best_key = max(
                    hits,
                    key=lambda k: (
                        1 if self.entries[k].has_quality_benchmarks() else 0,
                        len(self.entries[k].sources),
                    ),
                )
                return self.entries[best_key], "medium"

        return None


def _default_http_get(url: str, timeout: float) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AiHead-Orchestrator/0.2 (+https://github.com/pank85/AiHead-Orchestrator)",
            "Accept": "application/json, text/plain, */*",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read()
        encoding = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(encoding, errors="replace")


def _read_or_fetch(
    name: str,
    url: str,
    *,
    cache_dir: Path,
    ttl_hours: int,
    refresh: bool,
    timeout: float,
    http_get: Callable[[str, float], str],
) -> Tuple[Optional[str], str]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    ext = "yml" if name == "aider" else ("csv" if name == "livebench" else "json")
    cache_file = cache_dir / f"{name}.{ext}"

    if not refresh and ttl_hours > 0 and cache_file.is_file():
        age_sec = time.time() - cache_file.stat().st_mtime
        if age_sec <= ttl_hours * 3600:
            try:
                return cache_file.read_text(encoding="utf-8"), "cached"
            except OSError:
                pass

    try:
        text = http_get(url, timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as err:
        # Stale cache fallback if network is unavailable
        if cache_file.is_file():
            try:
                return cache_file.read_text(encoding="utf-8"), f"stale_cache ({err})"
            except OSError:
                pass
        return None, f"error: {err}"

    try:
        tmp = cache_file.with_suffix(f".{ext}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, cache_file)
    except OSError:
        pass
    return text, "fetched"


def fetch_source_catalog(
    *,
    cache_dir: Optional[Path] = None,
    ttl_hours: int = 24,
    refresh: bool = False,
    timeout: float = 12.0,
    sources: Optional[Iterable[str]] = None,
    http_get: Optional[Callable[[str, float], str]] = None,
) -> SourceCatalog:
    """Fetch and merge all zero-auth benchmark & metadata sources concurrently."""
    target_dir = cache_dir or DEFAULT_CACHE_DIR
    getter = http_get or _default_http_get
    selected = list(sources) if sources is not None else list(SOURCE_URLS.keys())

    catalog = SourceCatalog()
    raw_texts: Dict[str, Optional[str]] = {}

    with ThreadPoolExecutor(max_workers=min(5, max(1, len(selected)))) as pool:
        futures = {
            name: pool.submit(
                _read_or_fetch,
                name,
                SOURCE_URLS[name],
                cache_dir=target_dir,
                ttl_hours=ttl_hours,
                refresh=refresh,
                timeout=timeout,
                http_get=getter,
            )
            for name in selected
            if name in SOURCE_URLS
        }
        for name, fut in futures.items():
            text, status = fut.result()
            raw_texts[name] = text
            catalog.source_status[name] = status

    for name in selected:
        text = raw_texts.get(name)
        if not text:
            continue
        try:
            if name == "models_dev":
                catalog.merge_entries(parse_models_dev(json.loads(text)))
            elif name == "openrouter":
                catalog.merge_entries(parse_openrouter_models(json.loads(text)))
            elif name == "swebench":
                catalog.merge_entries(parse_swebench_leaderboards(json.loads(text)))
            elif name == "aider":
                catalog.merge_entries(parse_aider_polyglot(text))
            elif name == "livebench":
                catalog.merge_entries(parse_livebench_csv(text))
        except Exception as err:
            catalog.source_status[name] = f"parse_error: {err}"

    return catalog


def _scale_swebench(score: float) -> float:
    """Map SWE-bench Verified % (roughly 20–85%) to a 0–100 coding/agentic scale."""
    return max(30.0, min(99.0, 28.0 + score * 0.90))


def _scale_aider(score: float) -> float:
    """Map Aider Polyglot pass_rate_2 % (roughly 15–90%) to a 0–100 coding scale."""
    return max(30.0, min(99.0, 25.0 + score * 0.85))


def _scale_livebench(score: float) -> float:
    """Map LiveBench score (roughly 25–85) to a 0–100 scale."""
    return max(30.0, min(99.0, 22.0 + score * 0.95))


def _estimate_speed_from_evidence(
    evidence: BenchmarkEvidence,
    normalized: NormalizedModel,
    *,
    observed_tok_per_sec: Optional[float] = None,
) -> int:
    tps = observed_tok_per_sec or evidence.output_tokens_per_sec
    if tps is not None and tps > 0:
        # 20 tok/s -> ~45, 60 tok/s -> ~70, 120 tok/s -> ~88, 200+ tok/s -> ~98
        if tps >= 200:
            return 98
        if tps >= 120:
            return clamp_score(84 + (tps - 120) * 0.17)
        if tps >= 60:
            return clamp_score(66 + (tps - 60) * 0.30)
        return clamp_score(30 + tps * 0.60)
    if evidence.speed_percentile is not None:
        return clamp_score(evidence.speed_percentile)
    low_id = normalized.base_model.lower()
    if any(tok in low_id for tok in ("flash-lite", "flash_lite", "nano", "mini", "haiku", "beam")):
        return 90
    if any(tok in low_id for tok in ("flash", "fast", "turbo", "hydrogen")):
        return 82
    if any(tok in low_id for tok in ("opus", "o1", "o3", "max")):
        return 55
    return 70


def synthesize_rating(
    normalized: NormalizedModel,
    evidence: BenchmarkEvidence,
    *,
    confidence: ConfidenceLevel,
    billing_tier: BillingTier,
    mode: str = "herdr",
    cli_context_window: Optional[int] = None,
    cli_supports_reasoning: Optional[bool] = None,
    observed_tok_per_sec: Optional[float] = None,
) -> Optional[ModelRating]:
    """Synthesize a full ``ModelRating`` from deterministic benchmark evidence.

    Returns ``None`` if the evidence has metadata only (e.g. context/pricing)
    without any quality benchmark scores so Layer 3 can fill in quality ratings
    while preserving the hard Layer-1 facts.
    """
    if not evidence.has_quality_benchmarks():
        return None

    coding_signals: List[Tuple[float, float]] = []
    if evidence.swebench_score is not None:
        coding_signals.append((_scale_swebench(evidence.swebench_score), 0.45))
    if evidence.aider_score is not None:
        coding_signals.append((_scale_aider(evidence.aider_score), 0.35))
    if evidence.livebench_coding is not None:
        coding_signals.append((_scale_livebench(evidence.livebench_coding), 0.20))

    if coding_signals:
        total_w = sum(w for _, w in coding_signals)
        coding = clamp_score(sum(val * w for val, w in coding_signals) / total_w)
    else:
        coding = clamp_score(_scale_livebench(evidence.livebench_global or 60.0))

    intel_signals: List[Tuple[float, float]] = []
    if evidence.livebench_global is not None:
        intel_signals.append((_scale_livebench(evidence.livebench_global), 0.50))
    if evidence.aider_score is not None:
        intel_signals.append((_scale_aider(evidence.aider_score), 0.25))
    if evidence.swebench_score is not None:
        intel_signals.append((_scale_swebench(evidence.swebench_score), 0.25))
    total_iw = sum(w for _, w in intel_signals)
    intelligence = clamp_score(sum(val * w for val, w in intel_signals) / total_iw)

    # Agentic score rewards SWE-bench (real repo loops) + tool support
    if evidence.swebench_score is not None:
        agentic_raw = 0.65 * _scale_swebench(evidence.swebench_score) + 0.35 * coding
    else:
        agentic_raw = 0.65 * coding + 0.35 * intelligence
    if evidence.supports_tools is True:
        agentic_raw += 2.0
    elif evidence.supports_tools is False:
        agentic_raw -= 10.0
    agentic = clamp_score(agentic_raw)

    speed = _estimate_speed_from_evidence(
        evidence,
        normalized,
        observed_tok_per_sec=observed_tok_per_sec,
    )
    context_window = evidence.context_window or cli_context_window
    context_handling = score_context_handling(context_window, intelligence)
    effective_billing: BillingTier = "free" if normalized.is_free_route else billing_tier
    cost_efficiency = score_cost_efficiency(
        effective_billing,
        input_price_per_1m=evidence.input_price_per_1m,
        output_price_per_1m=evidence.output_price_per_1m,
        is_free_route=normalized.is_free_route,
        intelligence=intelligence,
        encoded_effort=normalized.encoded_effort,
    )

    supports_reasoning = bool(
        evidence.supports_reasoning
        or cli_supports_reasoning
        or normalized.encoded_effort is not None
    )
    supported_efforts = infer_supported_efforts(normalized.harness, normalized.model_id, mode=mode)
    family = evidence.family if evidence.family != "unknown" else normalized.family

    src_list = sorted(evidence.sources)
    if observed_tok_per_sec is not None:
        src_list.append("probe")

    note_parts: List[str] = []
    if evidence.swebench_score is not None:
        note_parts.append(f"SWE-bench {evidence.swebench_score:.1f}%")
    if evidence.aider_score is not None:
        note_parts.append(f"Aider {evidence.aider_score:.1f}%")
    if evidence.livebench_global is not None:
        note_parts.append(f"LiveBench {evidence.livebench_global:.1f}")
    notes = "; ".join(note_parts) if note_parts else f"Matched {evidence.canonical_id}"

    rating = ModelRating(
        intelligence=intelligence,
        coding=coding,
        agentic=agentic,
        speed=speed,
        cost_efficiency=cost_efficiency,
        context_handling=context_handling,
        family=family,
        billing_tier=effective_billing,
        context_window=context_window,
        input_price_per_1m=evidence.input_price_per_1m,
        output_price_per_1m=evidence.output_price_per_1m,
        output_tokens_per_sec=observed_tok_per_sec or evidence.output_tokens_per_sec,
        supports_reasoning=supports_reasoning,
        supported_efforts=supported_efforts,
        notes=notes,
        source="+".join(src_list),
        confidence=confidence,
        canonical_match=evidence.canonical_id,
        evaluated_at=utc_now_iso(),
        manual_override=False,
    )
    rating.recommended_roles = derive_recommended_roles(rating)
    return rating
