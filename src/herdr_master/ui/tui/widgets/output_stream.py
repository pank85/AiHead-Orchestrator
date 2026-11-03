"""Output stream widget rendering ANSI scrollback from the selected agent."""

from rich.text import Text
from textual.widgets import RichLog

from herdr_master.ui.tui.state import AppState


class OutputStreamWidget(RichLog):
    """Terminal output stream view for the selected agent pane."""

    DEFAULT_CSS = """
    OutputStreamWidget {
        background: #0d0d0d;
        color: #f0f0f0;
        scrollbar-gutter: stable;
        padding: 0 1;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(max_lines=1000, highlight=True, markup=True, **kwargs)
        self.app_state = app_state
        self._current_target: str = ""

    def update_state(self, app_state: AppState) -> None:
        """Update stream content when selected worker or buffer changes."""
        self.app_state = app_state
        target = app_state.selected_target_id or ""
        text = app_state.output_scrollback.get(target, "")

        if target != self._current_target or text:
            self._current_target = target
            self.clear()
            if text:
                self.write(Text.from_ansi(text))
            else:
                msg = f"No stream scrollback available for {target or 'active worker'}"
                self.write(Text(msg, style="dim italic"))
