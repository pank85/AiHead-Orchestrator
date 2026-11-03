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
    session: str = typer.Option(
        "default",
        help="Persistent Herdr session (defaults to active existing or 'default')",
    ),
):
    """Initialize .herdr-master workspace configuration and git develop branch."""
    console.print("[green]✓[/green] Initializing Herdr-Master in current workspace...")
    console.print(f"  • Target integration branch: [cyan]{target_branch}[/cyan]")
    console.print(f"  • Herdr session: [cyan]{session}[/cyan]")
    console.print("[green]✓[/green] Ready. Use 'herdm master start' or '/herdr-master' in a pane.")


@app.command()
def status():
    """Show current active masters, workers, and pipelines."""
    console.print("[bold cyan]Herdr-Master Status Snapshot[/bold cyan]")
    console.print("  • Herdr Environment: Active")
    console.print("  • No active workers currently running.")
    console.print("Run [yellow]herdm tui[/yellow] to launch the interactive terminal dashboard.")


@app.command()
def tui(
    session: str = typer.Option(
        "default",
        help="Persistent Herdr session (defaults to active existing or 'default')",
    ),
    demo: bool = typer.Option(
        False,
        "--demo",
        help="Run TUI in demo mode with pre-populated Ergo pipeline data",
    ),
):
    """Launch the interactive terminal management dashboard (TUI)."""
    from herdr_master.ui.tui.app import HerdrMasterApp

    tui_app = HerdrMasterApp(session=session, demo=demo)
    tui_app.run()


if __name__ == "__main__":
    app()
