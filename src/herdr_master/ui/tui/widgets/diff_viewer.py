"""Diff viewer widget rendering git diff develop...<branch> for the selected worker."""

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from herdr_master.ui.tui.state import AppState


class DiffViewerWidget(Static):
    """Side-by-side or unified diff viewer showing worker worktree diff against develop."""

    DEFAULT_CSS = """
    DiffViewerWidget {
        background: #0f0f0f;
        padding: 0 1;
    }
    .diff-header {
        height: 1;
        background: #1e1e1e;
        color: #00e5ff;
        padding: 0 1;
    }
    .diff-body {
        scrollbar-gutter: stable;
    }
    """

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._diff_header = Static("Git Diff Inspection", classes="diff-header")
        self._diff_content = Static(classes="diff-body")

    def compose(self) -> ComposeResult:
        yield self._diff_header
        yield self._diff_content

    def on_mount(self) -> None:
        self.render_diff()

    def update_state(self, app_state: AppState) -> None:
        self.app_state = app_state
        self.render_diff()

    def render_diff(self) -> None:
        target = self.app_state.selected_target_id or "selected-worker"
        worker = self.app_state.workers.get(target)
        branch = worker.branch if worker else f"herdm/{target}"

        self._diff_header.update(f"Diff: develop...{branch} (Worker: {target})")

        raw_diff = self.app_state.diff_content.get(target, "")
        if raw_diff:
            syntax = Syntax(raw_diff, "diff", theme="monokai", line_numbers=True)
            self._diff_content.update(syntax)
        else:
            self._diff_content.update(
                Text(
                    f"No pending diff recorded between develop and {branch}.\n"
                    "Press 'd' to refresh git diff.",
                    style="dim italic",
                )
            )
