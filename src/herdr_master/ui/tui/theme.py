"""Visual styling and color palette tokens for Herdr-Master TUI."""

from herdr_master.core.models import WorkerState

# Color Palette Tokens
PRIMARY_ACCENT = "cyan"
SECONDARY_ACCENT = "magenta"
BACKGROUND_DARK = "#121212"
SURFACE_DARK = "#1e1e1e"
PANEL_BORDER = "dim"
TEXT_PRIMARY = "#ffffff"
TEXT_MUTED = "dim"

# State Badge Colors
STATE_COLORS = {
    WorkerState.SPAWNING: "cyan",
    WorkerState.IDLE: "green",
    WorkerState.WORKING: "cyan",
    WorkerState.BLOCKED: "yellow",
    WorkerState.DONE: "blue",
    WorkerState.NEEDS_REVIEW: "magenta",
    WorkerState.FIXING: "cyan",
    WorkerState.APPROVED: "bright_blue",
    WorkerState.SQUASHING: "blue",
    WorkerState.LANDED: "dim",
    WorkerState.FAILED: "red",
}

# State Icons
STATE_ICONS = {
    WorkerState.SPAWNING: "○",
    WorkerState.IDLE: "●",
    WorkerState.WORKING: "◐",
    WorkerState.BLOCKED: "▲",
    WorkerState.DONE: "✔",
    WorkerState.NEEDS_REVIEW: "◆",
    WorkerState.FIXING: "🔧",
    WorkerState.APPROVED: "★",
    WorkerState.SQUASHING: "⇥",
    WorkerState.LANDED: "✔",
    WorkerState.FAILED: "✖",
}


def badge_markup(state: WorkerState) -> str:
    """Format a styled Rich markup badge for a given WorkerState."""
    color = STATE_COLORS.get(state, "white")
    icon = STATE_ICONS.get(state, "•")
    return f"[{color}]{icon} {state.value}[/{color}]"
