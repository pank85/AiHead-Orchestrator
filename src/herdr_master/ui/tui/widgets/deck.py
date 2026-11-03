"""Center Deck widget multiplexing Output, Diff, Pipeline, and Mailbox views."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Static

from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.diff_viewer import DiffViewerWidget
from herdr_master.ui.tui.widgets.mailbox_view import MailboxViewWidget
from herdr_master.ui.tui.widgets.output_stream import OutputStreamWidget
from herdr_master.ui.tui.widgets.pipeline_view import PipelineView


class DeckTabBar(Static):
    """Visual tab selector at the top of the Center Deck."""

    DEFAULT_CSS = """
    DeckTabBar {
        height: 1;
        background: #1e1e1e;
        padding: 0 1;
        color: #888888;
    }
    """

    def __init__(self, active_mode: int = 3, **kwargs) -> None:
        super().__init__(**kwargs)
        self.active_mode = active_mode

    def set_active_mode(self, mode: int) -> None:
        self.active_mode = mode
        self.refresh_display()

    def on_mount(self) -> None:
        self.refresh_display()

    def refresh_display(self) -> None:
        t = Text()
        modes = [(1, "Output"), (2, "Diff"), (3, "Pipeline"), (4, "Mailbox")]
        for mode, name in modes:
            if mode == self.active_mode:
                t.append(f" [{mode}] {name} ", style="bold cyan reverse")
            else:
                t.append(f" [{mode}] {name} ", style="dim")
            t.append(" ")
        self.update(t)


class CenterDeck(Vertical):
    """Multiplexed center deck supporting 4 views."""

    DEFAULT_CSS = """
    CenterDeck {
        width: 1fr;
        height: 1fr;
        background: #111111;
    }
    .deck-content {
        height: 1fr;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._tab_bar = DeckTabBar(active_mode=app_state.active_deck_mode)
        self._output_view = OutputStreamWidget(app_state)
        self._diff_view = DiffViewerWidget(app_state)
        self._pipeline_view = PipelineView(app_state)
        self._mailbox_view = MailboxViewWidget(app_state)
        self._container = Container(classes="deck-content")

    def compose(self) -> ComposeResult:
        yield self._tab_bar
        with self._container:
            yield self._output_view
            yield self._diff_view
            yield self._pipeline_view
            yield self._mailbox_view

    def on_mount(self) -> None:
        self.switch_mode(self.app_state.active_deck_mode)

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self._tab_bar.set_active_mode(app_state.active_deck_mode)
        self._output_view.update_state(app_state)
        self._diff_view.update_state(app_state)
        self._pipeline_view.update_state(app_state)
        self._mailbox_view.update_state(app_state)
        self.switch_mode(app_state.active_deck_mode)

    def switch_mode(self, mode: int) -> None:
        """Toggle visibility between views based on active mode (1..4)."""
        self._tab_bar.set_active_mode(mode)
        self._output_view.display = mode == 1
        self._diff_view.display = mode == 2
        self._pipeline_view.display = mode == 3
        self._mailbox_view.display = mode == 4
