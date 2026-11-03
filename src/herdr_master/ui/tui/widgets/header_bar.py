"""HeaderBar widget displaying product branding, session, branch, and status."""

from datetime import datetime

from rich.text import Text
from textual.widgets import Static

from herdr_master.ui.tui.state import AppState


class HeaderBar(Static):
    """Header bar at the top of the TUI Cockpit."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        dock: top;
        background: #1a1a1a;
        color: #e0e0e0;
        padding: 0 1;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._clock_str = datetime.now().strftime("%H:%M:%S")

    def on_mount(self) -> None:
        """Start local clock timer without network I/O."""
        self.set_interval(1.0, self._tick_clock)
        self.refresh_display()

    def _tick_clock(self) -> None:
        self._clock_str = datetime.now().strftime("%H:%M:%S")
        self.refresh_display()

    def update_state(self, app_state: AppState) -> None:
        """Update header bar state projection."""
        self.app_state = app_state
        self.refresh_display()

    def refresh_display(self) -> None:
        """Render the status cells in the header bar."""
        # Branding
        t = Text()
        t.append("HERDR-MASTER ", style="bold cyan")
        t.append("0.1.0", style="dim")
        t.append(" │ ", style="dim")

        # Session
        t.append("session: ", style="dim")
        t.append(f"{self.app_state.session_name} ", style="bold white")
        t.append("│ ", style="dim")

        # Workspace
        t.append("ws: ", style="dim")
        ws_display = self.app_state.workspace_label or self.app_state.workspace_id or "default"
        t.append(f"{ws_display} ", style="cyan")
        t.append("│ ", style="dim")

        # Target branch cleanliness
        t.append(f"{self.app_state.target_branch} ", style="magenta")
        branch_clean = "CLEAN" in self.app_state.branch_status.upper()
        clean_color = "green" if branch_clean else "red"
        t.append(f"[{self.app_state.branch_status}] ", style=f"bold {clean_color}")
        t.append("│ ", style="dim")

        # Socket status
        if self.app_state.socket_connected:
            t.append("● socket: ", style="dim")
            t.append("ONLINE ", style="green")
        else:
            t.append("✖ SOCKET DOWN ", style="bold red")
        t.append("│ ", style="dim")

        # Active agents count
        t.append("agents: ", style="dim")
        t.append(f"{self.app_state.active_agents_count} ", style="bold white")

        # Clock right aligned
        self.update(t)
