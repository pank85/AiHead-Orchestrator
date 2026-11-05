"""CLI command and manager for installing standalone skills across agent harnesses."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, NamedTuple

import typer
from rich.console import Console
from rich.table import Table

from herdr_master.core.catalog import CANONICAL_SKILLS, HarnessKind, get_harness
from herdr_master.core.skill_resources import SkillResourceError, get_skill_source

console = Console()
skills_app = typer.Typer(
    help="Manage standalone skills and slash commands across AI agent harnesses."
)


class TargetLocation(NamedTuple):
    harness: str
    kind: str  # "skill" or "command"
    path: Path
    description: str


def find_repo_root() -> Path:
    """Find the root directory of herdr-master containing skills/."""
    current = Path(__file__).resolve().parent
    for p in [current, *current.parents]:
        if (p / "skills" / "herdr-master" / "SKILL.md").exists() and (
            p / "skills" / "ergo" / "SKILL.md"
        ).exists():
            return p
    raise FileNotFoundError("Could not locate herdr-master root with skills/ directory.")


def get_skill_targets(home: Path | None = None) -> Dict[str, List[TargetLocation]]:
    """Return map of skill_name -> target locations across supported harnesses from catalog."""
    base_home = home if home is not None else Path.home()
    targets_map: Dict[str, List[TargetLocation]] = {}

    for skill in CANONICAL_SKILLS:
        locs: List[TargetLocation] = []
        for kind in HarnessKind:
            spec = get_harness(kind.value)
            for target in spec.install_targets:
                resolved_path = Path(
                    target.path_template.format(home=str(base_home), skill=skill)
                )
                locs.append(
                    TargetLocation(
                        harness=f"{spec.label} ({spec.kind})",
                        kind=target.kind,
                        path=resolved_path,
                        description=f"/{skill} ({target.description})",
                    )
                )
        targets_map[skill] = locs

    return targets_map


def _evaluate_link_status(target_path: Path, canonical_path: Path | None) -> str:
    """Distinguish live active link from missing, broken, or stale links."""
    if target_path.is_symlink():
        try:
            resolved = target_path.resolve()
            if not target_path.exists():
                return "[red]✗ Broken[/red]"
            if canonical_path is not None and resolved == canonical_path.resolve():
                return "[green]✓ Active[/green]"
            return "[yellow]⚠ Stale[/yellow]"
        except Exception:
            return "[red]✗ Broken[/red]"
    elif target_path.exists():
        return "[yellow]⚠ Stale[/yellow]"
    return "[red]✗ Missing[/red]"


@skills_app.command("install")
def install_skills(
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        "-f",
        help="Overwrite or update existing symlinks",
    ),
) -> None:
    """Install and symlink standalone skills and commands across all supported harnesses."""
    targets_map = get_skill_targets()
    console.print(
        "[bold cyan]👑 Installing Standalone Skills & Commands across AI Harnesses...[/bold cyan]\n"
    )

    installed_count = 0
    has_error = False

    for skill_name, targets in targets_map.items():
        try:
            canonical_file = get_skill_source(skill_name)
        except SkillResourceError as e:
            console.print(f"[red]Error locating canonical skill '{skill_name}':[/red] {e}")
            has_error = True
            continue
        except Exception as e:
            console.print(f"[red]Unexpected error locating skill '{skill_name}':[/red] {e}")
            has_error = True
            continue

        for target in targets:
            try:
                target.path.parent.mkdir(parents=True, exist_ok=True)
                if target.path.is_symlink():
                    if force:
                        target.path.unlink()
                    else:
                        console.print(f"[dim]Skipping existing symlink {target.path}[/dim]")
                        continue
                elif target.path.exists():
                    if target.path.is_dir():
                        console.print(
                            f"[red]Refusing to overwrite existing directory {target.path}[/red]"
                        )
                        has_error = True
                        continue
                    if force:
                        target.path.unlink()
                    else:
                        console.print(f"[dim]Skipping existing file {target.path}[/dim]")
                        continue

                target.path.symlink_to(canonical_file)
                console.print(
                    f"[green]✓[/green] [bold]{target.harness}[/bold] ({target.kind}): "
                    f"[cyan]{target.description}[/cyan] -> [dim]{target.path}[/dim]"
                )
                installed_count += 1
            except Exception as e:
                console.print(f"[red]Failed to link {target.path}:[/red] {e}")
                has_error = True

    if has_error:
        console.print(
            f"\n[red]Completed with errors. {installed_count} skill/command links configured.[/red]"
        )
        raise typer.Exit(code=1)

    console.print(
        f"\n[bold green]✨ Done! {installed_count} skill/command links configured.[/bold green]"
    )
    console.print(
        "Available commands in [bold]agy[/bold], [bold]pi[/bold], "
        "[bold]claude[/bold], [bold]opencode[/bold]:"
    )
    console.print("  • [cyan]/herdr-master [task][/cyan] — Root orchestrator for multiple jobs")
    console.print("  • [cyan]/ergo [--native] [--models ...] [intent][/cyan] — Phased pipeline")
    console.print(
        "  • [cyan]/orchestration [--models ...] [intent][/cyan] — Phased pipeline, "
        "host subagents only (no Herdr)"
    )


@skills_app.command("list")
def list_skills() -> None:
    """List installation status of standalone skills and commands across harnesses."""
    targets_map = get_skill_targets()

    table = Table(title="👑 Herdr-Master & Ergo Standalone Harness Skills")
    table.add_column("Harness", style="cyan", no_wrap=True)
    table.add_column("Command / Role", style="magenta")
    table.add_column("Type", style="yellow")
    table.add_column("Installed Path", style="dim")
    table.add_column("Status", justify="center")

    for skill_name, targets in targets_map.items():
        try:
            canonical_file = get_skill_source(skill_name)
        except Exception:
            canonical_file = None

        for target in targets:
            status_badge = _evaluate_link_status(target.path, canonical_file)
            table.add_row(
                target.harness,
                f"/{skill_name}",
                target.kind,
                str(target.path),
                status_badge,
            )

    console.print(table)


@skills_app.command("remove")
def remove_skills() -> None:
    """Remove installed symlinks for herdr-master and ergo skills across harnesses."""
    targets_map = get_skill_targets()
    removed_count = 0
    for _, targets in targets_map.items():
        for target in targets:
            if target.path.is_symlink():
                target.path.unlink()
                removed_count += 1
                console.print(f"[yellow]Removed symlink:[/yellow] {target.path}")
    console.print(f"[green]✓ Cleaned up {removed_count} skill/command symlinks.[/green]")
