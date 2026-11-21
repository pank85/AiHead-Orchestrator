"""Machine-readable catalog and squad resolution commands.

Register ``catalog_app`` on the root Typer app from the composition root.
This module does not import ``herdr_master.cli.main``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer

from herdr_master.cli.setup_catalog import discover_model_catalog
from herdr_master.core.available_models import enabled_model_ids
from herdr_master.core.catalog import (
    CatalogError,
    catalog_document,
    qualify_binding_slug,
    supported_harnesses,
)
from herdr_master.core.config import (
    find_project_config_path,
    load_effective_models_state,
    load_effective_project_config,
    save_project_config,
)
from herdr_master.core.model_ratings import (
    ModelRating,
    derive_recommended_roles,
    derive_worker_tier,
    detect_model_family,
    effective_worker_tier,
    find_unrated_models,
    format_rated_models_table,
    infer_supported_efforts,
    lookup_rating,
    merge_ratings,
    utc_now_iso,
)
from herdr_master.core.skill_resources import SkillResourceError
from herdr_master.core.slug import InvalidBindingSlugError, parse_binding
from herdr_master.pipeline.model_evaluator import (
    collect_target_models,
    evaluate_available_models,
)
from herdr_master.pipeline.squad import (
    SquadValidationError,
    describe_agent,
    parse_squad_overrides,
    resolve_frozen_squad,
    resolve_squad,
    save_run_squad,
)
from herdr_master.pipeline.triage import (
    RoutingPlan,
    select_triage_models,
    validate_route,
)

catalog_app = typer.Typer(
    help="Inspect the harness catalog and resolve squads.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, indent=2) + "\n")
    sys.stdout.flush()


def _fail(message: str, code: int = 1) -> None:
    text = message.strip()
    sys.stderr.write(text + "\n")
    sys.stderr.flush()
    _emit({"ok": False, "error": text})
    raise typer.Exit(code)


def _guard(callback):
    try:
        callback()
    except (
        CatalogError,
        SquadValidationError,
        InvalidBindingSlugError,
        SkillResourceError,
        FileNotFoundError,
        json.JSONDecodeError,
        ValueError,
    ) as err:
        _fail(str(err))


@catalog_app.command("show")
def show_cmd(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
) -> None:
    """Show harness identities, the product squad, and canonical skill paths."""

    def run() -> None:
        payload = catalog_document()
        if json_output:
            _emit(payload)
            return
        skills = ",".join(payload["canonical_skills"])
        sys.stdout.write(f"default_mode={payload['default_mode']} skills={skills}\n")

    _guard(run)


@catalog_app.command("squad")
def squad_cmd(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
    mode: Annotated[str, typer.Option("--mode", help="herdr or native.")] = "herdr",
    harness: Annotated[
        Optional[str],
        typer.Option("--harness", help="Native host. Required for --mode native."),
    ] = None,
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML. Absent model fields inherit the catalog."),
    ] = None,
    run_squad: Annotated[
        Optional[Path],
        typer.Option("--run-squad", help="Run squad.toml. Omitted fields inherit lower layers."),
    ] = None,
    models: Annotated[
        Optional[str],
        typer.Option("--models", help="Compact role:binding list. Individual flags win."),
    ] = None,
    scout: Annotated[Optional[str], typer.Option("--scout")] = None,
    architect: Annotated[Optional[str], typer.Option("--architect")] = None,
    workers: Annotated[Optional[str], typer.Option("--workers")] = None,
    coder: Annotated[
        Optional[str],
        typer.Option("--coder", "--coders", help="Alias for --workers."),
    ] = None,
    reviewer: Annotated[Optional[str], typer.Option("--reviewer")] = None,
    worker_mode: Annotated[
        Optional[str],
        typer.Option(
            "--worker-mode",
            help="Dynamic worker selection mode: inherit, balanced, quality, fast, or frugal.",
        ),
    ] = None,
    from_json: Annotated[
        Optional[str],
        typer.Option("--from-json", help="Frozen squad JSON path, or - for stdin."),
    ] = None,
    write_snapshot: Annotated[
        Optional[Path],
        typer.Option("--write-snapshot", help="Absolute path to write a run squad snapshot."),
    ] = None,
) -> None:
    """Resolve the product squad, project overrides, a run snapshot, or a frozen document."""

    def run() -> None:
        if from_json is not None:
            extras = [
                project,
                run_squad,
                models,
                scout,
                architect,
                workers,
                coder,
                reviewer,
                harness,
            ]
            if any(extras):
                _fail(
                    "Do not combine --from-json with --project, --run-squad, --models, "
                    "--harness, or role flags. The document is frozen and is not re-merged."
                )
            if from_json == "-":
                raw_text = sys.stdin.read()
            else:
                raw_text = Path(from_json).read_text(encoding="utf-8")
            document = json.loads(raw_text)
            resolved = resolve_frozen_squad(document)
        else:
            worker_spec = workers
            if coder is not None:
                if worker_spec is not None and worker_spec != coder:
                    _fail("--workers and --coder disagree. Pass only one worker override.")
                worker_spec = worker_spec or coder
            overrides = parse_squad_overrides(
                models=models,
                scout=scout,
                architect=architect,
                workers=worker_spec,
                reviewer=reviewer,
            )
            resolved = resolve_squad(
                project if project is not None else (find_project_config_path() or Path.cwd()),
                mode=mode,
                native_harness=harness,
                run_squad_path=run_squad,
                overrides=overrides or None,
                worker_mode=worker_mode,
            )
        if write_snapshot is not None:
            if not write_snapshot.is_absolute():
                _fail("--write-snapshot requires an absolute path.")
            save_run_squad(
                resolved.squad,
                write_snapshot,
                mode=resolved.mode,
                native_harness=resolved.native_harness,
                worker_mode=resolved.worker_mode,
            )
        payload = resolved.to_dict()
        if write_snapshot is not None:
            payload["snapshot_path"] = str(write_snapshot)
        if json_output:
            _emit(payload)
            return
        sys.stdout.write(
            f"mode={resolved.mode} scout={resolved.squad.scout.to_slug()} "
            f"workers={len(resolved.squad.worker_pool)}\n"
        )

    _guard(run)


@catalog_app.command("agent")
def agent_cmd(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
    mode: Annotated[str, typer.Option("--mode", help="herdr or native.")] = "herdr",
    harness: Annotated[
        Optional[str],
        typer.Option("--harness", help="Native host. Required for --mode native."),
    ] = None,
    binding: Annotated[
        Optional[str],
        typer.Option("--binding", help="kind:model[:effort] or a native name."),
    ] = None,
    role: Annotated[
        Optional[str],
        typer.Option("--role", help="scout, architect, coder, reviewer, master, or watcher."),
    ] = None,
) -> None:
    """Resolve one binding to Herdr argv or a native invocation descriptor."""

    def run() -> None:
        if not binding or not role:
            _fail("--binding and --role are required.")
        qualified = qualify_binding_slug(binding, mode=mode, native_harness=harness)
        parsed = parse_binding(qualified)
        payload = describe_agent(parsed, mode=mode, role=role, native_harness=harness)
        if json_output:
            _emit(payload)
            return
        sys.stdout.write(
            f"mode={payload.get('mode')} role={payload.get('role')} kind={payload.get('kind')}\n"
        )

    _guard(run)


def _resolve_explicit_project_path(project: Optional[Path]) -> Optional[Path]:
    if project is None:
        return None
    path = Path(project)
    if path.is_dir():
        candidate = path / ".herdr-master.toml"
        if not candidate.is_file():
            raise FileNotFoundError(f"Project configuration file not found: {candidate}")
        return candidate
    if not path.is_file():
        raise FileNotFoundError(f"Project configuration file not found: {path}")
    return path


@catalog_app.command("available")
def available_cmd(
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML. Absent means no project allowlist."),
    ] = None,
) -> None:
    """Inspect the project allowlist alongside discovered catalog models and ratings."""

    def run() -> None:
        cfg = None
        resolved_path = _resolve_explicit_project_path(project)
        if resolved_path is not None:
            cfg = load_effective_project_config(resolved_path)

        if resolved_path is not None:
            effective = load_effective_models_state(resolved_path, project_cfg=cfg)
            available_obj = effective.available
            effective_ratings = effective.ratings
            disabled_harnesses = effective.disabled_harnesses
        else:
            available_obj = None
            effective_ratings = {}
            disabled_harnesses = frozenset()

        restricted = available_obj is not None
        automode = bool(cfg.ai_head.automode) if cfg is not None else False
        available_data = available_obj.model_dump() if available_obj is not None else None

        catalog = discover_model_catalog(cfg)
        modes = {}
        for mode in ("herdr", "native"):
            modes[mode] = {}
            for harness in supported_harnesses(mode=mode):
                if harness in disabled_harnesses:
                    continue
                discovered = [opt.value for opt in catalog.models(harness, mode=mode)]
                enabled = enabled_model_ids(available_obj, mode, harness)
                modes[mode][harness] = {
                    "discovered": discovered,
                    "enabled": enabled,
                }

        target_models = {
            h: mids
            for h, mids in collect_target_models(
                available_obj, catalog, all_discovered=False
            ).items()
            if h not in disabled_harnesses
        }
        unrated_models = find_unrated_models(target_models, effective_ratings)
        ratings_data = {
            harness: {
                mid: rating.model_dump(exclude_none=True) for mid, rating in model_map.items()
            }
            for harness, model_map in effective_ratings.items()
            if harness not in disabled_harnesses
        }

        payload = {
            "restricted": restricted,
            "automode": automode,
            "disabled_harnesses": sorted(disabled_harnesses),
            "available": available_data,
            "live_counts": catalog.live_counts,
            "modes": modes,
            "ratings": ratings_data,
            "unrated_models": unrated_models,
        }
        if json_output:
            _emit(payload)
            return

        lines = [f"restricted={str(restricted).lower()} automode={str(automode).lower()}"]
        for mode_name, harness_map in modes.items():
            for harness_name, info in harness_map.items():
                if info["enabled"] is None:
                    status = "unrestricted"
                elif not info["enabled"]:
                    status = "none"
                else:
                    status = ",".join(info["enabled"])
                discovered = ",".join(info["discovered"])
                lines.append(
                    f"{mode_name}.{harness_name}: enabled={status} (discovered: {discovered})"
                )
        if unrated_models:
            unrated_total = sum(len(v) for v in unrated_models.values())
            lines.append(f"unrated_models={unrated_total} (run 'aihead catalog evaluate' to score)")
        sys.stdout.write("\n".join(lines) + "\n")

    _guard(run)


@catalog_app.command("evaluate")
def evaluate_cmd(
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML or directory. Defaults to upward discovery."),
    ] = None,
    harness: Annotated[
        Optional[str],
        typer.Option("--harness", help="Limit evaluation to one harness kind."),
    ] = None,
    all_discovered: Annotated[
        bool,
        typer.Option(
            "--all-discovered",
            help="Evaluate all discovered catalog models, not only allowlisted ones.",
        ),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Re-evaluate already-rated models (keeps manual overrides).",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Re-evaluate everything, including manual overrides."),
    ] = False,
    max_age: Annotated[
        Optional[int],
        typer.Option("--max-age", help="Re-evaluate ratings older than N days."),
    ] = None,
    probe: Annotated[
        bool,
        typer.Option("--probe", help="Run opt-in live latency & health micro-probes."),
    ] = False,
    no_llm: Annotated[
        bool,
        typer.Option(
            "--no-llm",
            help="Skip Layer-3 harness LLM evaluator; zero-auth sources only.",
        ),
    ] = False,
    evaluator: Annotated[
        Optional[str],
        typer.Option("--evaluator", help="Explicit harness:model[:effort] for Layer-3 evaluation."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Compute ratings without writing .herdr-master.toml."),
    ] = False,
    probe_live: Annotated[
        bool,
        typer.Option("--probe-live", help="Run live CLI model discovery before evaluating."),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
) -> None:
    """Incrementally evaluate available models missing stored ratings."""

    def run() -> None:
        resolved_path = _resolve_explicit_project_path(project)
        result = evaluate_available_models(
            project_path=resolved_path,
            harness_filter=harness,
            all_discovered=all_discovered,
            refresh=refresh,
            force=force,
            max_age_days=max_age,
            probe=probe,
            use_llm=not no_llm,
            evaluator_binding=evaluator,
            dry_run=dry_run,
            probe_live_catalog=probe_live,
        )
        if json_output:
            _emit(result.to_dict())
            return

        lines = [
            f"evaluated={result.evaluated_count} skipped={result.skipped_count} "
            f"unresolved={result.unresolved_count}"
        ]
        if result.saved_path:
            lines.append(f"saved={result.saved_path}")
        elif dry_run:
            lines.append("dry_run=true (no files written)")
        if result.evaluator_used:
            lines.append(f"evaluator={result.evaluator_used}")
        if result.evaluated:
            models_by_harness = {h: list(m.keys()) for h, m in result.evaluated.items()}
            lines.append(format_rated_models_table(models_by_harness, result.evaluated))
        for h_name, missing in result.unresolved.items():
            lines.append(f"unresolved.{h_name}: {', '.join(missing)}")
        for warn in result.warnings:
            lines.append(f"warning: {warn}")
        sys.stdout.write("\n".join(lines) + "\n")

    _guard(run)


def _rating_document(rating: ModelRating) -> dict:
    result = rating.model_dump(exclude_none=True)
    result["effective_worker_tier"] = effective_worker_tier(rating)
    result["worker_tier_source"] = (
        "manual"
        if rating.worker_tier_override
        else "derived"
        if rating.worker_tier is None
        else "auto"
    )
    return result


@catalog_app.command("ratings")
def ratings_cmd(
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML or directory. Defaults to upward discovery."),
    ] = None,
    harness: Annotated[
        Optional[str],
        typer.Option("--harness", help="Filter output to one harness."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
) -> None:
    """Display stored model evaluation ratings (including inherited ancestor ratings)."""

    def run() -> None:
        resolved_path = _resolve_explicit_project_path(project)
        effective = load_effective_models_state(resolved_path)
        ratings = effective.ratings
        if harness:
            h_key = harness.strip().lower()
            ratings = {h_key: ratings[h_key]} if h_key in ratings else {}

        if json_output:
            payload = {
                "config_path": str(effective.config_path) if effective.config_path else None,
                "inherited_paths": [str(p) for p in effective.inherited_paths],
                "billing": dict(effective.evaluation.billing),
                "ratings": {
                    h: {mid: _rating_document(r) for mid, r in mmap.items()}
                    for h, mmap in ratings.items()
                },
            }
            _emit(payload)
            return

        if not ratings:
            sys.stdout.write(
                "No model ratings stored. "
                "Run 'aihead catalog evaluate' to score available models.\n"
            )
            return
        models_by_harness = {h: list(m.keys()) for h, m in ratings.items()}
        sys.stdout.write(format_rated_models_table(models_by_harness, ratings) + "\n")
        for h, mmap in ratings.items():
            for mid, rating in mmap.items():
                doc = _rating_document(rating)
                sys.stdout.write(
                    f"{h}:{mid} billing_tier={rating.billing_tier} "
                    f"worker_tier={doc['effective_worker_tier']} ({doc['worker_tier_source']})\n"
                )

    _guard(run)


@catalog_app.command("rate")
def rate_cmd(
    binding: Annotated[
        str,
        typer.Option("--binding", help="Target <harness>:<model> (e.g. pi:openai/gpt-6.1-sol)."),
    ],
    intelligence: Annotated[Optional[int], typer.Option("--intelligence")] = None,
    coding: Annotated[Optional[int], typer.Option("--coding")] = None,
    agentic: Annotated[Optional[int], typer.Option("--agentic")] = None,
    speed: Annotated[Optional[int], typer.Option("--speed")] = None,
    cost_efficiency: Annotated[Optional[int], typer.Option("--cost-efficiency")] = None,
    context_handling: Annotated[Optional[int], typer.Option("--context-handling")] = None,
    billing_tier: Annotated[
        Optional[str],
        typer.Option("--billing-tier", help="free, subscription, or api."),
    ] = None,
    worker_tier: Annotated[
        Optional[int], typer.Option("--worker-tier", help="Lock worker tier: 1, 2, or 3.")
    ] = None,
    clear_worker_tier_override: Annotated[
        bool,
        typer.Option(
            "--clear-worker-tier-override",
            help="Unlock and recompute worker tier from current scores.",
        ),
    ] = False,
    family: Annotated[Optional[str], typer.Option("--family")] = None,
    context_window: Annotated[Optional[int], typer.Option("--context-window")] = None,
    notes: Annotated[Optional[str], typer.Option("--notes")] = None,
    clear_override: Annotated[
        bool,
        typer.Option("--clear-override", help="Unlock manual_override so auto-eval can refresh."),
    ] = False,
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML or directory."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
) -> None:
    """Manually set or override stored evaluation ratings for a single model."""

    def run() -> None:
        if worker_tier is not None and worker_tier not in (1, 2, 3):
            raise ValueError("--worker-tier must be 1, 2, or 3.")
        if worker_tier is not None and clear_worker_tier_override:
            raise ValueError("Cannot combine --worker-tier and --clear-worker-tier-override.")
        if ":" not in binding:
            raise ValueError("Expected --binding <harness>:<model> (for example pi:xai/grok-4.7).")
        harness_raw, model_raw = binding.split(":", 1)
        harness_key = harness_raw.strip().lower()
        model_id = model_raw.strip()
        if not model_id:
            raise ValueError("Model ID in --binding must not be blank.")

        resolved_path = _resolve_explicit_project_path(project) or find_project_config_path()
        cfg = (
            load_effective_project_config(resolved_path)
            if resolved_path and resolved_path.is_file()
            else load_effective_project_config()
        )
        effective = load_effective_models_state(resolved_path, project_cfg=cfg)
        existing = lookup_rating(effective.ratings, harness_key, model_id)

        def pick(new_val: Optional[int], attr: str, default: int = 75) -> int:
            if new_val is not None:
                return new_val
            if existing is not None:
                return getattr(existing, attr)
            return default

        tier_only = (
            (worker_tier is not None or clear_worker_tier_override)
            and all(
                value is None
                for value in (
                    intelligence,
                    coding,
                    agentic,
                    speed,
                    cost_efficiency,
                    context_handling,
                    billing_tier,
                    family,
                    context_window,
                    notes,
                )
            )
            and not clear_override
        )
        updated = ModelRating(
            intelligence=pick(intelligence, "intelligence"),
            coding=pick(coding, "coding"),
            agentic=pick(agentic, "agentic"),
            speed=pick(speed, "speed"),
            cost_efficiency=pick(cost_efficiency, "cost_efficiency"),
            context_handling=pick(context_handling, "context_handling"),
            family=(
                family
                if family is not None
                else (existing.family if existing else detect_model_family(harness_key, model_id))
            ),
            billing_tier=(
                billing_tier
                if billing_tier is not None
                else (
                    existing.billing_tier
                    if existing
                    else effective.evaluation.billing_for(harness_key)
                )
            ),  # type: ignore[arg-type]
            context_window=(
                context_window
                if context_window is not None
                else (existing.context_window if existing else None)
            ),
            input_price_per_1m=existing.input_price_per_1m if existing else None,
            output_price_per_1m=existing.output_price_per_1m if existing else None,
            output_tokens_per_sec=existing.output_tokens_per_sec if existing else None,
            supports_reasoning=existing.supports_reasoning if existing else False,
            supported_efforts=(
                existing.supported_efforts
                if existing
                else infer_supported_efforts(harness_key, model_id, mode="herdr")
            ),
            notes=notes if notes is not None else (existing.notes if existing else "Manual rating"),
            source="manual" if not clear_override else (existing.source if existing else "manual"),
            confidence="high",
            canonical_match=existing.canonical_match if existing else None,
            evaluated_at=utc_now_iso(),
            manual_override=not clear_override,
        )
        # Re-validate a merge with the complete effective record to retain extra fields and
        # provenance. model_copy(update=...) alone does not validate pydantic updates.
        data = existing.model_dump() if existing else {}
        generated = updated.model_dump()
        if existing:
            generated = {
                key: value
                for key, value in {
                    "intelligence": intelligence,
                    "coding": coding,
                    "agentic": agentic,
                    "speed": speed,
                    "cost_efficiency": cost_efficiency,
                    "context_handling": context_handling,
                    "billing_tier": billing_tier,
                    "family": family,
                    "context_window": context_window,
                    "notes": notes,
                }.items()
                if value is not None
            }
            if not tier_only:
                generated["manual_override"] = not clear_override
        data.update(generated)
        if existing:
            data["worker_tier"] = existing.worker_tier
            data["worker_tier_override"] = existing.worker_tier_override
        if worker_tier is not None:
            data.update(worker_tier=worker_tier, worker_tier_override=True)
        elif clear_worker_tier_override:
            data.update(worker_tier=None, worker_tier_override=False)
        if tier_only and not existing:
            data["manual_override"] = False
        updated = ModelRating.model_validate(data)
        if clear_worker_tier_override:
            data = updated.model_dump()
            data["worker_tier"] = derive_worker_tier(updated, model_id=model_id)
            updated = ModelRating.model_validate(data)
        if not tier_only:
            updated.recommended_roles = derive_recommended_roles(updated)

        cfg.models.ratings = merge_ratings(cfg.models.ratings, {harness_key: {model_id: updated}})
        saved_path = save_project_config(cfg, resolved_path)

        if json_output:
            _emit(
                {
                    "ok": True,
                    "binding": f"{harness_key}:{model_id}",
                    "saved_path": str(saved_path),
                    "rating": _rating_document(updated),
                }
            )
            return
        sys.stdout.write(
            f"Saved manual rating for {harness_key}:{model_id} ({updated.compact_badge()}) "
            f"to {saved_path}\n"
        )

    _guard(run)


@catalog_app.command("triage-models")
def triage_models_cmd(
    harness: Annotated[
        Optional[str],
        typer.Option("--harness", help="Active AiHead host harness. Defaults to ai_head.binding."),
    ] = None,
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML or directory."),
    ] = None,
    profile: Annotated[
        Optional[str],
        typer.Option(
            "--profile",
            help="Routing profile override: balanced, quality, fast, frugal.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
) -> None:
    """Select Stage-1 cheap Mini-Scout and Stage-2 strong Router models for a host harness."""

    def run() -> None:
        resolved_path = _resolve_explicit_project_path(project) or find_project_config_path()
        cfg = (
            load_effective_project_config(resolved_path)
            if resolved_path and resolved_path.is_file()
            else load_effective_project_config()
        )
        effective = load_effective_models_state(resolved_path, project_cfg=cfg)
        catalog = discover_model_catalog(cfg, probe_live=False)
        host = harness or cfg.ai_head.binding.kind.value
        active_profile = profile or cfg.ai_head.routing_profile
        sel = select_triage_models(
            host,
            available=effective.available,
            ratings=effective.ratings,
            agy_mode=cfg.orchestration.agy_mode,
            routing_profile=active_profile,
            catalog=catalog,
        )
        if json_output:
            _emit(sel.model_dump())
            return
        sys.stdout.write(
            f"host={sel.host_harness} adapter={sel.adapter} "
            f"scout={sel.scout_binding}({sel.scout_effort or 'default'}) "
            f"router={sel.router_binding}({sel.router_effort or 'default'})\n"
        )

    _guard(run)


@catalog_app.command("validate-route")
def validate_route_cmd(
    from_json: Annotated[
        str,
        typer.Option("--from-json", help="RoutingPlan JSON path, or - for stdin."),
    ],
    project: Annotated[
        Optional[Path],
        typer.Option("--project", help="Project TOML or directory."),
    ] = None,
    backend: Annotated[
        str,
        typer.Option("--backend", help="Active backend: herdr or core."),
    ] = "herdr",
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine JSON.")] = False,
) -> None:
    """Deterministically validate a Stage-2 RoutingPlan against allowlists and catalog rules."""

    def run() -> None:
        raw_text = sys.stdin.read() if from_json == "-" else Path(from_json).read_text("utf-8")
        plan = RoutingPlan.model_validate(json.loads(raw_text))
        resolved_path = _resolve_explicit_project_path(project) or find_project_config_path()
        cfg = (
            load_effective_project_config(resolved_path)
            if resolved_path and resolved_path.is_file()
            else load_effective_project_config()
        )
        effective = load_effective_models_state(resolved_path, project_cfg=cfg)
        res = validate_route(
            plan,
            available=effective.available,
            ratings=effective.ratings,
            agy_mode=cfg.orchestration.agy_mode,
            backend=backend,
            disabled_harnesses=effective.disabled_harnesses,
        )
        if json_output:
            _emit(res.model_dump())
        else:
            status = "valid" if res.valid else "invalid"
            lines = [f"status={status} workflow={plan.workflow}"]
            for err in res.errors:
                lines.append(f"error: {err}")
            for adv in res.advisories:
                lines.append(f"advisory: {adv}")
            sys.stdout.write("\n".join(lines) + "\n")
        if not res.valid:
            raise typer.Exit(1)

    _guard(run)
