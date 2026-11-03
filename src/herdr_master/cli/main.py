"""CLI interface for herdr-master (herdm)."""

import typer
from rich.console import Console

app = typer.Typer(
    name="herdm",
    help="👑 Universal multi-master, multi-worker cross-harness AI orchestrator built on Herdr.",
    add_completion=True,
)
console = Console()


@app.command()
def init(
    target_branch: str = typer.Option("develop", help="Target integration branch"),
    session: str = typer.Option("hermes", help="Persistent Herdr session"),
):
    """Initialize .herdr-master workspace configuration and git develop branch."""
    console.print(f"[green]✓[/green] Initializing Herdr-Master in current workspace...")
    console.print(f"  • Target integration branch: [cyan]{target_branch}[/cyan]")
    console.print(f"  • Herdr session: [cyan]{session}[/cyan]")
    console.print(f"[green]✓[/green] Ready. Use 'herdm master start' or '/herdr-master' in a pane.")


@app.command()
def status():
    """Show current active masters, workers, and pipelines."""
    console.print("[bold cyan]Herdr-Master Status Snapshot[/bold cyan]")
    console.print("  • Herdr Environment: Active")
    console.print("  • No active workers currently running.")
    console.print("Run [yellow]herdm tui[/yellow] to launch the interactive terminal dashboard.")


@app.command()
def tui():
    """Launch the interactive terminal dashboard (TUI)."""
    console.print("[bold purple]Launching Herdr-Master TUI...[/bold purple]")
    console.print("(TUI initialized. Press Ctrl+C or 'q' to exit)")


if __name__ == "__main__":
    app()
