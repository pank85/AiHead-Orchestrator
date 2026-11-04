"""CLI command and manager for installing standalone skills across agent harnesses."""

from pathlib import Path
from typing import Dict, List, NamedTuple

import typer
from rich.console import Console
from rich.table import Table

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
        if (p / "skills" / "herdr-master" / "SKILL.md").exists():
            return p
    # Fallback to standard project path
    fallback = Path("/home/pank/projects/herdr-master")
    if (fallback / "skills" / "herdr-master" / "SKILL.md").exists():
        return fallback
    raise FileNotFoundError("Could not locate herdr-master root with skills/ directory.")


def get_skill_targets() -> Dict[str, List[TargetLocation]]:
    """Return map of skill_name -> target locations across all 4 supported harnesses."""
    home = Path.home()
    return {
        "herdr-master": [
            TargetLocation(
                harness="Antigravity (agy)",
                kind="skill",
                path=home / ".gemini" / "config" / "skills" / "herdr-master" / "SKILL.md",
                description="Slash command /herdr-master",
            ),
            TargetLocation(
                harness="Pi (pi)",
                kind="command",
                path=home / ".pi" / "agent" / "prompts" / "herdr-master.md",
                description="Slash command /herdr-master",
            ),
            TargetLocation(
                harness="Claude Code (claude)",
                kind="command",
                path=home / ".claude" / "commands" / "herdr-master.md",
                description="Slash command /herdr-master",
            ),
            TargetLocation(
                harness="OpenCode (opencode)",
                kind="skill",
                path=home / ".config" / "opencode" / "skills" / "herdr-master" / "SKILL.md",
                description="Skill /herdr-master",
            ),
            TargetLocation(
                harness="OpenCode (opencode)",
                kind="command",
                path=home / ".config" / "opencode" / "commands" / "herdr-master.md",
                description="Command /herdr-master",
            ),
        ],
        "ergo": [
            TargetLocation(
                harness="Antigravity (agy)",
                kind="skill",
                path=home / ".gemini" / "config" / "skills" / "ergo" / "SKILL.md",
                description="Slash command /ergo",
            ),
            TargetLocation(
                harness="Pi (pi)",
                kind="command",
                path=home / ".pi" / "agent" / "prompts" / "ergo.md",
                description="Slash command /ergo",
            ),
            TargetLocation(
                harness="Claude Code (claude)",
                kind="command",
                path=home / ".claude" / "commands" / "ergo.md",
                description="Slash command /ergo",
            ),
            TargetLocation(
                harness="OpenCode (opencode)",
                kind="skill",
                path=home / ".config" / "opencode" / "skills" / "ergo" / "SKILL.md",
                description="Skill /ergo",
            ),
            TargetLocation(
                harness="OpenCode (opencode)",
                kind="command",
                path=home / ".config" / "opencode" / "commands" / "ergo.md",
                description="Command /ergo",
            ),
        ],
    }


@skills_app.command("install")
def install_skills(
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        "-f",
        help="Overwrite or update existing symlinks",
    ),
) -> None:
    """Install and symlink standalone skills and commands across all 4 harnesses."""
    try:
        repo_root = find_repo_root()
    except Exception as e:
        console.print(f"[red]Error locating skills:[/red] {e}")
        raise typer.Exit(code=1) from e

    targets_map = get_skill_targets()
    console.print(
        "[bold cyan]👑 Installing Standalone Skills & Commands across AI Harnesses...[/bold cyan]\n"
    )

    installed_count = 0
    for skill_name, targets in targets_map.items():
        canonical_file = repo_root / "skills" / skill_name / "SKILL.md"
        if not canonical_file.exists():
            console.print(f"[red]Warning: Canonical skill not found at {canonical_file}[/red]")
            continue

        for target in targets:
            try:
                target.path.parent.mkdir(parents=True, exist_ok=True)
                if target.path.is_symlink() or target.path.exists():
                    if force:
                        target.path.unlink()
                    else:
                        console.print(f"[dim]Skipping existing {target.path}[/dim]")
                        continue
                target.path.symlink_to(canonical_file)
                console.print(
                    f"[green]✓[/green] [bold]{target.harness}[/bold] ({target.kind}): "
                    f"[cyan]{target.description}[/cyan] -> [dim]{target.path}[/dim]"
                )
                installed_count += 1
            except Exception as e:
                console.print(f"[red]Failed to link {target.path}:[/red] {e}")

    console.print(
        f"\n[bold green]✨ Done! {installed_count} skill/command links configured.[/bold green]"
    )
    console.print(
        "Available commands in [bold]agy[/bold], [bold]pi[/bold], "
        "[bold]claude[/bold], [bold]opencode[/bold]:"
    )
    console.print("  • [cyan]/herdr-master [task][/cyan] — Root orchestrator for multiple jobs")
    console.print("  • [cyan]/ergo [--herdr] [intent][/cyan] — Phased pipeline")


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
        for target in targets:
            is_installed = target.path.exists() or target.path.is_symlink()
            status_badge = "[green]✓ Active[/green]" if is_installed else "[red]✗ Missing[/red]"
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
