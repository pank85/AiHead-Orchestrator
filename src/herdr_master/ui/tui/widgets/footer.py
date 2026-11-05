"""Action Footer widget displaying keybindings and status hints."""

from rich.text import Text
from textual.events import Resize
from textual.widgets import Static


class ActionFooter(Static):
    """Bottom footer bar indicating available keybindings and context actions."""

    DEFAULT_CSS = """
    ActionFooter {
        height: 1;
        dock: bottom;
        background: #1a1a1a;
        color: #aaaaaa;
        padding: 0 1;
        overflow: hidden hidden;
    }
    """

    def on_mount(self) -> None:
        self.render_hints(self.size.width if self.size.width > 0 else 80)

    def on_resize(self, event: Resize) -> None:
        self.render_hints(event.size.width)

    def render_hints(self, width: int) -> None:
        t = Text()
        if width >= 75:
            keys = [
                ("n", "New Master"),
                ("e", "New Ergo"),
                ("s/c", "Settings"),
                ("o", "Open in Herdr"),
                ("q", "Quit"),
            ]
        elif width >= 55:
            keys = [
                ("n", "Master"),
                ("e", "Ergo"),
                ("s/c", "Settings"),
                ("o", "Open"),
                ("q", "Quit"),
            ]
        else:
            keys = [
                ("o", "Open"),
                ("q", "Quit"),
                ("n", "M"),
                ("e", "E"),
                ("s", "Set"),
            ]
        for key, label in keys:
            t.append(f"[{key}] ", style="bold cyan")
            t.append(f"{label}  ", style="white")
        self.update(t)
