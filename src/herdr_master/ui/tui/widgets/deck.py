"""Center Deck hosting the WorkspaceBrowser."""

from textual.app import ComposeResult
from textual.containers import Vertical

from herdr_master.ui.tui.state import AppState
from herdr_master.ui.tui.widgets.workspaces import WorkspaceBrowser


class CenterDeck(Vertical):
    """Single-child workspace browser host."""

    DEFAULT_CSS = """
    CenterDeck {
        width: 1fr;
        height: 1fr;
        background: #111111;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._workspace_view = WorkspaceBrowser(app_state)

    def compose(self) -> ComposeResult:
        yield self._workspace_view

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self._workspace_view.update_state(app_state)
