"""Action Footer widget displaying keybindings and status hints."""

from rich.text import Text
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
    }
    """

    def on_mount(self) -> None:
        t = Text()
        keys = [
            ("Tab", "Focus"),
            ("n", "New Master"),
            ("e", "New Ergo"),
            ("c", "Config"),
            ("1-4", "Deck"),
            ("p", "Prompt"),
            ("r", "Review"),
            ("m", "Squash Land"),
            ("k", "Kill"),
            ("q", "Quit"),
        ]
        for key, label in keys:
            t.append(f"[{key}] ", style="bold cyan")
            t.append(f"{label}  ", style="white")
        self.update(t)
