"""AiHead & skill setup: single-screen TUI plus non-interactive --defaults."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.table import Table

from herdr_master.cli.setup_catalog import ModelCatalog
from herdr_master.cli.setup_tui import SetupTuiApp, run_setup_tui
from herdr_master.core.catalog import (
    CatalogError,
    HarnessKind,
    get_harness,
)
from herdr_master.core.config import (
    load_project_config,
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
    """Raised when setup configuration cannot be launched or saved."""


def _target_config_path(path: Optional[Path]) -> Optional[Path]:
    if path is None:
        return None
    resolved = Path(path).expanduser()
    if resolved.is_dir() or str(path).endswith(os.sep):
        return resolved / ".herdr-master.toml"
    return resolved


def _default_state() -> RoleState:
    state: RoleState = {role: default for role, default in ROLE_DEFAULTS.items()}
    state["workers"] = list(DEFAULT_WORKERS)
    return state


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


def _run_interactive_tui(
    target: Optional[Path],
    *,
    catalog: Optional[ModelCatalog] = None,
    driver: Optional[Callable[[SetupTuiApp], SetupTuiApp]] = None,
) -> None:
    try:
        app = run_setup_tui(
            target,
            catalog=catalog,
            driver=driver,
            save_project_fn=save_project_config,
            save_squad_fn=save_squad_as_default,
        )
    except KeyboardInterrupt as err:
        console.print("[yellow]Cancelled; configuration unchanged.[/yellow]")
        raise typer.Exit(1) from err
    except (
        SetupError,
        CatalogError,
        InvalidBindingSlugError,
        ValueError,
        OSError,
        RuntimeError,
    ) as err:
        console.print(f"[red]Setup failed:[/red] {err}")
        raise typer.Exit(1) from err

    if not app.saved:
        console.print("[yellow]Cancelled; configuration unchanged.[/yellow]")
        return
    console.print(
        f"[green]✓[/green] Saved to [cyan]{app.saved_path or target or '.herdr-master.toml'}[/cyan]"
    )


def run_aihead_setup(
    *,
    defaults: bool = False,
    yes: bool = False,
    config: Optional[Path] = None,
    catalog: Optional[ModelCatalog] = None,
    driver: Optional[Callable[[SetupTuiApp], SetupTuiApp]] = None,
) -> None:
    """Configure per-role harnesses/models via --defaults or the single-screen TUI."""
    target = _target_config_path(config)
    if not defaults:
        _run_interactive_tui(target, catalog=catalog, driver=driver)
        return

    try:
        state = _default_state()
        squad = _build_squad(state)
        cfg = _prepare_orchestration(target, state)
        _print_summary(state)
        if not yes and not Confirm.ask("Save this squad to .herdr-master.toml?", True):
            console.print("[yellow]Aborted: configuration not saved.[/yellow]")
            raise typer.Exit(0)
        save_project_config(cfg, target)
        save_squad_as_default(squad, target)
    except (SetupError, CatalogError, InvalidBindingSlugError, ValueError, OSError) as err:
        console.print(f"[red]AiHead setup failed:[/red] {err}")
        raise typer.Exit(1) from err

    console.print(f"[green]✓[/green] Saved to [cyan]{target or '.herdr-master.toml'}[/cyan]")


def run_skill_setup(
    *,
    config: Optional[Path] = None,
    catalog: Optional[ModelCatalog] = None,
    driver: Optional[Callable[[SetupTuiApp], SetupTuiApp]] = None,
) -> None:
    """Launch the single-screen setup TUI for all skills and the worker pool."""
    target = _target_config_path(config)
    _run_interactive_tui(target, catalog=catalog, driver=driver)
