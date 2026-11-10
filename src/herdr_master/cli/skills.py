"""CLI command and manager for installing standalone skills across agent harnesses."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple

import typer
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
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
        if (
            (p / "skills" / "herdr-master" / "SKILL.md").exists()
            and (p / "skills" / "ergo" / "SKILL.md").exists()
            and (p / "skills" / "council" / "SKILL.md").exists()
        ):
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


def _hermes_frontmatter_name(path: Path) -> str | None:
    """Read a Hermes skill name using its YAML 1.1 safe-loader semantics.

    Unlike Hermes' permissive malformed-YAML fallback, installation must fail
    closed when metadata cannot be trusted for collision detection.
    """
    content = path.read_text(encoding="utf-8-sig")
    if not content.startswith("---"):
        return None
    end = re.search(r"\n---\s*\n", content[3:])
    if end is None:
        raise ValueError(f"Unterminated frontmatter in {path}")
    try:
        parser = YAML(typ="safe", pure=True)
        parser.version = (1, 1)
        parser.allow_duplicate_keys = False
        metadata = parser.load(content[3 : end.start() + 3])
    except (YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError(f"Malformed or ambiguous frontmatter in {path}: {exc}") from exc
    if metadata is None:
        return None
    if not isinstance(metadata, dict) or any(not isinstance(key, str) for key in metadata):
        raise ValueError(f"Malformed or ambiguous frontmatter mapping in {path}")
    declared = metadata.get("name")
    if declared is None or declared == "":
        return None
    if not isinstance(declared, str) or not declared.strip() or declared != declared.strip():
        raise ValueError(f"Malformed or ambiguous frontmatter name in {path}")
    return declared


def _hermes_collision(root: Path, target: Path, name: str) -> Path | None:
    """Conservatively detect same-name local Hermes candidates before writing."""
    if root.is_symlink():
        raise OSError(f"Cannot safely inspect symlinked Hermes skills root: {root}")
    if not root.exists():
        return None

    def fail(error: OSError) -> None:
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        base = Path(directory)
        excluded = {".git"}
        if "SKILL.md" in files:
            excluded |= {"references", "templates", "assets", "scripts"}
        dirs[:] = [d for d in dirs if d not in excluded]
        for dirname in dirs:
            if (base / dirname).is_symlink():
                raise OSError(f"Cannot safely inspect symlinked skill directory: {base / dirname}")
        for filename in files:
            path = base / filename
            if path == target:
                continue
            if filename == f"{name}.md":
                return path
            if filename != "SKILL.md":
                continue
            if base.name == name:
                return path
            # Previously installed canonical files are known inputs.
            if (path.is_symlink() and base.parent == root and base.name in CANONICAL_SKILLS
                    and path.resolve() == get_skill_source(base.name).resolve()):
                continue
            if _hermes_frontmatter_name(path) == name:
                return path
    return None


@skills_app.command("install")
def install_skills(
    force: bool = typer.Option(
        True,
        "--force/--no-force",
        "-f",
        help="Overwrite existing non-Hermes files or links; Hermes unowned targets are always protected",
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
                if target.harness == "Hermes (hermes)":
                    root = target.path.parent.parent
                    collision = _hermes_collision(root, target.path, skill_name)
                    if collision:
                        console.print(f"[red]Hermes skill collision for {skill_name}: {collision}[/red]")
                        has_error = True
                        continue
                    if target.path.is_symlink() and target.path.resolve() == canonical_file.resolve():
                        console.print(f"[dim]Already installed: {target.path}[/dim]")
                        installed_count += 1
                        continue
                    if target.path.is_symlink() or target.path.exists():
                        console.print(f"[red]Refusing to overwrite unowned Hermes target {target.path}[/red]")
                        has_error = True
                        continue
                    if target.path.parent.is_symlink():
                        raise OSError(f"Refusing symlinked Hermes skill directory: {target.path.parent}")
                    target.path.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation: never unlink a Hermes user's file, even with --force.
                    target.path.symlink_to(canonical_file)
                    console.print(f"[green]✓[/green] {target.harness}: {target.path}")
                    installed_count += 1
                    continue
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
        "[yellow]Hermes collision checks cover only the default HOME/.hermes/skills root; "
        "active profiles, project and external roots are not checked.[/yellow]"
    )
    console.print(
        "Available commands in [bold]agy[/bold], [bold]pi[/bold], "
        "[bold]claude[/bold], [bold]opencode[/bold], [bold]hermes[/bold]:"
    )
    console.print("  • [cyan]/herdr-master [task][/cyan] — Root orchestrator for multiple jobs")
    console.print("  • [cyan]/ergo [--native] [--models ...] [intent][/cyan] — Phased pipeline")
    console.print(
        "  • [cyan]/orchestration [--models ...] [intent][/cyan] — Phased pipeline, "
        "host subagents only (no Herdr)"
    )
    console.print(
        "  • [cyan]/council [intent][/cyan] — Multi-agent plan synthesis and consensus council"
    )


@skills_app.command("list")
def list_skills() -> None:
    """List installation status of standalone skills and commands across harnesses."""
    targets_map = get_skill_targets()

    table = Table(title="👑 Herdr-Master, Ergo & Council Standalone Harness Skills")
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
    """Remove installed symlinks for standalone skills across harnesses."""
    targets_map = get_skill_targets()
    removed_count = 0
    for _, targets in targets_map.items():
        for target in targets:
            if target.path.is_symlink():
                target.path.unlink()
                removed_count += 1
                console.print(f"[yellow]Removed symlink:[/yellow] {target.path}")
    console.print(f"[green]✓ Cleaned up {removed_count} skill/command symlinks.[/green]")
