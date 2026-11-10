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
    get_harness,
    normalize_effort,
    supported_harnesses,
)
from herdr_master.core.config import (
    load_project_config,
    project_model_overrides,
    save_project_config,
)
from herdr_master.core.models import HarnessBinding, Squad
from herdr_master.core.slug import InvalidBindingSlugError
from herdr_master.pipeline.squad import save_squad_as_default

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
        state = _current_state(target)
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

    console.print(
        f"[green]✓[/green] Saved to [cyan]{target or '.herdr-master.toml'}[/cyan]"
    )
