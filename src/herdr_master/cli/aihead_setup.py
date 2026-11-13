"""AiHead squad setup wizard: persist per-role models to .herdr-master.toml."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import typer
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

from herdr_master.core.catalog import (
    CatalogError,
    HarnessKind,
    describe_herdr_agent,
    ensure_herdr_routable,
    get_harness,
    native_binding_error,
    normalize_effort,
    supported_harnesses,
)
from herdr_master.core.config import (
    DEFAULT_ORCHESTRATION_MODELS,
    CouncilMember,
    ErgoConfig,
    load_project_config,
    project_model_overrides,
    save_project_config,
    squad_to_project_config,
)
from herdr_master.core.models import HarnessBinding, Squad
from herdr_master.core.slug import InvalidBindingSlugError
from herdr_master.pipeline.squad import resolve_squad, save_squad_as_default, validate_squad

console = Console()

# Role defaults verified against `opencode models`.
ROLE_DEFAULTS: Dict[str, Tuple[str, str, Optional[str]]] = {
    "scout": ("opencode", "opencode/beam-free", "medium"),
    "architect": ("opencode", "openai/gpt-6.1-sol", "high"),
    "reviewer": ("opencode", "openai/gpt-6.1-sol", "medium"),
}
WORKER_HARNESS = "opencode"
DEFAULT_WORKERS: Tuple[str, ...] = ("opencode/deepseek-v4.1-flash-free",)

RoleState = Dict[str, object]


class SetupError(ValueError):
    """Raised when the wizard input cannot be saved."""


def _target_config_path(path: Optional[Path]) -> Optional[Path]:
    if path is None:
        return None
    resolved = Path(path).expanduser()
    if resolved.is_dir() or str(path).endswith(os.sep):
        return resolved / ".herdr-master.toml"
    return resolved


def _opencode_models() -> Optional[set[str]]:
    """Return installed opencode model slugs, or None when opencode is unavailable."""
    try:
        proc = subprocess.run(
            ["opencode", "models"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    models = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return models or None


def _default_state() -> RoleState:
    state: RoleState = {role: default for role, default in ROLE_DEFAULTS.items()}
    state["workers"] = list(DEFAULT_WORKERS)
    return state


def _current_state(config_path: Optional[Path]) -> RoleState:
    overrides = project_model_overrides(load_project_config(config_path))
    state: RoleState = {}
    for role, (harness, model, effort) in ROLE_DEFAULTS.items():
        slug = overrides["roles"].get(role)
        if slug:
            binding = HarnessBinding.from_slug(slug)
            state[role] = (
                binding.kind.value,
                binding.model,
                binding.effort.value if binding.effort else None,
            )
        else:
            state[role] = (harness, model, effort)
    pool = overrides["worker_pool"]
    if pool:
        state["workers"] = [worker.model for worker in pool]
    else:
        state["workers"] = list(DEFAULT_WORKERS)
    return state


def _ask(label: str, default: str) -> str:
    return Prompt.ask(label, default=default)


def _confirm(label: str, default: bool = True) -> bool:
    return Confirm.ask(label, default=default)


def _ask_model(
    role: str,
    default: str,
    harness: str,
    models: Optional[set[str]],
) -> str:
    for _ in range(3):
        answer = _ask(f"{role.capitalize()} model", default).strip() or default
        if not answer:
            raise SetupError(f"{role.capitalize()} model cannot be empty.")
        if harness == "opencode" and models is not None and answer not in models:
            console.print(
                f"[red]Unknown opencode model '{answer}'.[/red] "
                "Run `opencode models` to list valid slugs."
            )
            continue
        return answer
    raise SetupError(f"{role.capitalize()} model '{default}' is not in `opencode models`.")


def _prompt_role(
    role: str,
    current: Tuple[str, str, Optional[str]],
    models: Optional[set[str]],
    harnesses: Sequence[str],
) -> Tuple[str, str, Optional[str]]:
    harness, model, effort = current
    answer = _ask(f"{role.capitalize()} harness", harness).strip().lower() or harness
    if answer not in harnesses:
        raise SetupError(
            f"Unsupported harness '{answer}'. Expected one of: {', '.join(harnesses)}."
        )
    harness = answer
    model = _ask_model(role, model, harness, models)
    raw_effort = _ask(
        f"{role.capitalize()} effort (low/medium/high/max, empty = none)",
        effort or "",
    )
    try:
        resolved_effort = normalize_effort(raw_effort)
    except CatalogError as err:
        raise SetupError(f"{role.capitalize()} effort: {err}") from err
    return harness, model, resolved_effort


def _prompt_workers(current: Sequence[str]) -> List[str]:
    default = ", ".join(current) or ", ".join(DEFAULT_WORKERS)
    raw = _ask("Coder models (comma-separated)", default)
    workers = [item.strip() for item in raw.split(",") if item.strip()]
    if not workers:
        raise SetupError("At least one coder model is required.")
    return workers


def _validate_workers(
    workers: Sequence[str],
    models: Optional[set[str]],
    *,
    interactive: bool,
) -> List[str]:
    if not interactive:
        return list(workers)
    if models is None:
        return list(workers)
    invalid = [worker for worker in workers if worker not in models]
    if invalid:
        raise SetupError(
            "Unknown opencode coder model(s): "
            + ", ".join(invalid)
            + ". Run `opencode models` to list valid slugs."
        )
    return list(workers)


def _build_squad(state: RoleState) -> Squad:
    def binding(role: str) -> HarnessBinding:
        harness, model, effort = state[role]  # type: ignore[misc]
        slug = f"{harness}:{model}" + (f":{effort}" if effort else "")
        return HarnessBinding.from_slug(slug, label=role)

    workers = state["workers"]  # type: ignore[assignment]
    pool = [
        HarnessBinding.from_slug(
            f"{WORKER_HARNESS}:{model}", label=f"worker-{idx + 1}", order=idx
        )
        for idx, model in enumerate(workers)
    ]
    return Squad(
        scout=binding("scout"),
        architect=binding("architect"),
        reviewer=binding("reviewer"),
        worker_pool=pool,
    )


def _prepare_orchestration(config_path: Optional[Path], state: RoleState) -> object:
    """Mirror single-harness settings into [orchestration], validated but not written."""
    cfg = load_project_config(config_path)
    orch = cfg.orchestration
    scout_harness, scout_model, _ = state["scout"]  # type: ignore[misc]
    orch.harness = HarnessKind(get_harness(str(scout_harness)).kind)
    orch.default_harness = orch.harness
    orch.scout_model = str(scout_model)
    orch.scout_effort = state["scout"][2]  # type: ignore[index]
    orch.architect_model = str(state["architect"][1])  # type: ignore[index]
    orch.architect_effort = state["architect"][2]  # type: ignore[index]
    orch.reviewer_model = str(state["reviewer"][1])  # type: ignore[index]
    orch.reviewer_effort = state["reviewer"][2]  # type: ignore[index]
    orch.worker_models = [str(model) for model in state["workers"]]  # type: ignore[union-attr]
    orch.sync_active_to_harnesses()
    # Force validation before touching disk so bad values leave the file intact.
    type(cfg).model_validate(cfg.model_dump())
    return cfg


def _print_summary(state: RoleState) -> None:
    table = Table(title="AiHead squad")
    table.add_column("Role")
    table.add_column("Binding")
    for role in ROLE_DEFAULTS:
        harness, model, effort = state[role]  # type: ignore[misc]
        suffix = f" (effort {effort})" if effort else ""
        table.add_row(role.capitalize(), f"{harness}:{model}{suffix}")
    table.add_row("Coders", ", ".join(state["workers"]))  # type: ignore[arg-type]
    console.print(table)


def run_aihead_setup(
    *,
    defaults: bool = False,
    yes: bool = False,
    config: Optional[Path] = None,
) -> None:
    """Interactively configure per-role harnesses/models and save them."""
    target = _target_config_path(config)
    try:
        state = _default_state() if defaults else _current_state(target)
        if not defaults:
            models = _opencode_models()
            harnesses = supported_harnesses(mode="native")
            for role in ROLE_DEFAULTS:
                state[role] = _prompt_role(role, state[role], models, harnesses)  # type: ignore[arg-type]
            state["workers"] = _validate_workers(
                _prompt_workers(state["workers"]),  # type: ignore[arg-type]
                models,
                interactive=True,
            )

        squad = _build_squad(state)
        cfg = _prepare_orchestration(target, state)
        _print_summary(state)
        if not yes and not _confirm("Save this squad to .herdr-master.toml?", True):
            console.print("[yellow]Aborted: configuration not saved.[/yellow]")
            raise typer.Exit(0)
        save_project_config(cfg, target)
        save_squad_as_default(squad, target)
    except (SetupError, CatalogError, InvalidBindingSlugError, ValueError, OSError) as err:
        console.print(f"[red]AiHead setup failed:[/red] {err}")
        raise typer.Exit(1) from err

    console.print(f"[green]✓[/green] Saved to [cyan]{target or '.herdr-master.toml'}[/cyan]")


def run_skill_setup(*, config: Optional[Path] = None) -> None:
    """Configure selected skills together, validate, then perform one atomic save."""
    from herdr_master.pipeline.launcher import resolve_coordinator_binding

    target = _target_config_path(config)
    try:
        cfg = load_project_config(target)
        skills = ("aihead", "ergo", "orchestration", "council", "herdr-master")
        selected = Prompt.ask("Skill", choices=[*skills, "all"], default="aihead")
        chosen = skills if selected == "all" else (selected,)
        preview = {}

        def ask_binding(label: str, current: HarnessBinding, *, worker: bool = False):
            value = Prompt.ask(
                f"{label} binding (harness:model[:effort])", default=current.to_slug()
            )
            binding = HarnessBinding.from_slug(
                value,
                label=current.label,
                description=current.description,
                enabled=current.enabled,
                order=current.order,
            )
            spec = get_harness(binding.kind.value)
            if not spec.supports_herdr or not binding.model.strip():
                raise SetupError(f"{label} requires a valid Herdr harness and model.")
            if worker and binding.effort is not None:
                raise SetupError("Workers cannot specify effort; the architect chooses it.")
            describe_herdr_agent(
                binding.kind.value,
                binding.model,
                binding.effort.value if binding.effort else None,
                "master",
            )
            return current.model_copy(
                update={"kind": binding.kind, "model": binding.model, "effort": binding.effort}
            )

        if "aihead" in chosen:
            cfg.ai_head.binding = ask_binding("AiHead", cfg.ai_head.binding)
            resolve_coordinator_binding(
                cfg.ai_head.binding.kind, cfg.ai_head.binding.model, cfg.ai_head.binding.effort
            )
            preview["ai_head.binding"] = cfg.ai_head.binding.to_slug()

        if "ergo" in chosen:
            squad = resolve_squad(target).squad
            for role in ("scout", "architect", "reviewer"):
                setattr(squad, role, ask_binding(f"Ergo {role}", getattr(squad, role)))
            default = ", ".join(w.to_slug() for w in squad.worker_pool)
            raw = Prompt.ask("Ergo workers (comma-separated bindings, no effort)", default=default)
            workers = []
            for idx, slug in enumerate(raw.split(",")):
                if not slug.strip():
                    raise SetupError("Ergo workers cannot be empty.")
                old = (
                    squad.worker_pool[idx]
                    if idx < len(squad.worker_pool)
                    else HarnessBinding.from_slug(
                        slug.strip(), label=f"worker-{idx + 1}", order=idx
                    )
                )
                binding = HarnessBinding.from_slug(
                    slug.strip(),
                    label=old.label or f"worker-{idx + 1}",
                    description=old.description,
                    enabled=old.enabled,
                    order=old.order,
                )
                if binding.effort or not get_harness(binding.kind.value).supports_herdr:
                    raise SetupError("Workers require Herdr bindings without effort.")
                workers.append(binding)
            squad.worker_pool = workers
            errors = validate_squad(squad)
            if errors:
                raise SetupError(" ".join(errors))
            for role in ("scout", "architect", "reviewer"):
                binding = getattr(squad, role)
                ensure_herdr_routable(
                    binding.kind.value,
                    binding.model,
                    binding.effort.value if binding.effort else None,
                    role=role,
                    require_effort=True,
                )
            for binding in squad.worker_pool:
                if binding.enabled:
                    ensure_herdr_routable(
                        binding.kind.value,
                        binding.model,
                        None,
                        role=binding.label or "worker",
                        require_effort=False,
                    )
            old_pool = dict(cfg.models.worker_pool or {})
            cfg = squad_to_project_config(squad, existing_config=cfg)
            for key, entry in cfg.models.worker_pool.items():
                previous = old_pool.get(key)
                if isinstance(previous, dict):
                    cfg.models.worker_pool[key] = {**previous, **entry}
            current_coordinator = cfg.ergo.coordinator if cfg.ergo else None
            raw_coordinator = Prompt.ask(
                "Ergo coordinator binding (harness:model[:effort], empty = same as Scout)",
                default=current_coordinator.to_slug() if current_coordinator else "",
            ).strip()
            if not raw_coordinator:
                cfg.ergo = None
            else:
                parsed = HarnessBinding.from_slug(
                    raw_coordinator, label="ergo-coordinator"
                )
                kind, model, effort = resolve_coordinator_binding(
                    parsed.kind, parsed.model, parsed.effort
                )
                # The catalog role vocabulary has no "coordinator"; use its host role.
                describe_herdr_agent(
                    kind.value, model, effort.value if effort else None, "master"
                )
                cfg.ergo = ErgoConfig(
                    coordinator=HarnessBinding(
                        kind=kind, model=model, effort=effort, label="ergo-coordinator"
                    )
                )
                preview["ergo"] = {"coordinator": cfg.ergo.coordinator.to_slug()}
            preview["models"] = {
                "roles": cfg.models.default_squad,
                "worker_pool": cfg.models.worker_pool,
            }

        if "orchestration" in chosen:
            orch = cfg.orchestration
            native = supported_harnesses(mode="native")
            host = Prompt.ask(
                "Orchestration harness", choices=list(native), default=orch.harness.value
            )
            if host != orch.harness.value:
                orch.sync_active_to_harnesses()
                if host in orch.harnesses:
                    orch.sync_harness_to_active(host)
                else:
                    orch.harnesses[host] = orch.for_harness(host)
                    orch.sync_harness_to_active(host)
            orch.default_harness = orch.harness
            defaults = DEFAULT_ORCHESTRATION_MODELS.get(host, {})
            for role in ("scout", "architect", "reviewer"):
                model = Prompt.ask(
                    f"Orchestration {role} model",
                    default=getattr(orch, f"{role}_model") or defaults.get(role, ""),
                ).strip()
                if not model or any(
                    model.startswith(f"{kind}:") for kind in supported_harnesses(mode="herdr")
                ):
                    raise SetupError(
                        "Orchestration models must be model slugs for its single harness, "
                        "not harness bindings."
                    )
                setattr(orch, f"{role}_model", model)
                effort = Prompt.ask(
                    f"Orchestration {role} effort (none/low/medium/high/max)",
                    default=getattr(orch, f"{role}_effort") or "none",
                )
                setattr(
                    orch,
                    f"{role}_effort",
                    None if effort.strip().lower() == "none" else normalize_effort(effort),
                )
            raw = Prompt.ask(
                "Orchestration workers (comma-separated model slugs)",
                default=", ".join(orch.worker_models) or defaults.get("coder", ""),
            )
            orch.worker_models = [x.strip() for x in raw.split(",")]
            if any(
                not x or any(x.startswith(f"{k}:") for k in supported_harnesses(mode="herdr"))
                for x in orch.worker_models
            ):
                raise SetupError(
                    "Orchestration workers must be model slugs for its single harness."
                )
            for role in ("scout", "architect", "reviewer"):
                problem = native_binding_error(
                    host, getattr(orch, f"{role}_model"), role=role, native_harness=host
                )
                if problem:
                    raise SetupError(problem)
            for worker in orch.worker_models:
                problem = native_binding_error(host, worker, role="coder", native_harness=host)
                if problem:
                    raise SetupError(problem)
            orch.sync_active_to_harnesses()
            preview["orchestration"] = orch.model_dump(exclude_none=True)

        if "council" in chosen:
            council = cfg.council
            council.coordinator = ask_binding("Council coordinator", council.coordinator)
            council.scout = ask_binding("Council scout", council.scout)
            default = ", ".join(m.to_slug() for m in council.members)
            raw = Prompt.ask("Council members (comma-separated bindings)", default=default)
            members = []
            for idx, slug in enumerate(raw.split(",")):
                if not slug.strip():
                    raise SetupError("Council members cannot be empty.")
                old = council.members[idx] if idx < len(council.members) else None
                binding = HarnessBinding.from_slug(
                    slug.strip(),
                    label=old.label if old else f"council-{idx + 1}",
                    description=old.description if old else None,
                    enabled=old.enabled if old else True,
                    order=old.order if old else idx,
                )
                if not get_harness(binding.kind.value).supports_herdr:
                    raise SetupError("Council members require Herdr-capable harnesses.")
                describe_herdr_agent(
                    binding.kind.value,
                    binding.model,
                    binding.effort.value if binding.effort else None,
                    "master",
                )
                members.append(
                    old.model_copy(
                        update={
                            "kind": binding.kind,
                            "model": binding.model,
                            "effort": binding.effort,
                        }
                    )
                    if old
                    else CouncilMember(**binding.model_dump())
                )
            council.members = members
            preview["council"] = council.model_dump(exclude_none=True)

        if "herdr-master" in chosen:
            if cfg.master_coordinator is None:
                kind, model, effort = resolve_coordinator_binding()
                cfg.master_coordinator = HarnessBinding(kind=kind, model=model, effort=effort)
            cfg.master_coordinator = ask_binding("Master coordinator", cfg.master_coordinator)
            resolve_coordinator_binding(
                cfg.master_coordinator.kind,
                cfg.master_coordinator.model,
                cfg.master_coordinator.effort,
            )
            describe_herdr_agent(
                cfg.master_coordinator.kind.value,
                cfg.master_coordinator.model,
                cfg.master_coordinator.effort.value if cfg.master_coordinator.effort else None,
                "master",
            )
            preview["master_coordinator"] = cfg.master_coordinator.to_slug()

        cfg = type(cfg).model_validate(cfg.model_dump())
        console.print("[bold]Setup preview[/bold]", preview)
        if not Confirm.ask("Save these settings?", default=False):
            console.print("[yellow]Cancelled; configuration unchanged.[/yellow]")
            return
        saved = save_project_config(cfg, target)
        console.print(f"[green]✓[/green] Saved to [cyan]{saved}[/cyan]")
    except (SetupError, CatalogError, InvalidBindingSlugError, ValueError, OSError) as err:
        console.print(f"[red]Setup failed:[/red] {err}")
        raise typer.Exit(1) from err
