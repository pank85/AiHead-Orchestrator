"""Reusable project-folder input and keyboard-accessible directory picker."""

from pathlib import Path
from typing import Iterable, Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, Static


def initial_project_folder(project_path: Optional[Path] = None) -> Path:
    path = Path(project_path).expanduser() if project_path is not None else Path.cwd()
    return (path.parent if path.suffix == ".toml" else path).resolve()


def validate_project_folder(value: str) -> Path:
    if not value.strip():
        raise ValueError("Choose a project folder.")
    path = Path(value.strip()).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Project folder does not exist or is not a directory: {path}")
    return path


class FolderTree(DirectoryTree):
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir()]


class ProjectFolderPicker(ModalScreen[Optional[Path]]):
    """Select a directory, never a file; browsing does not change process cwd."""

    DEFAULT_CSS = """
    ProjectFolderPicker { align: center middle; background: rgba(0, 0, 0, 0.8); }
    ProjectFolderPicker > Vertical {
        width: 90%; height: 90%; border: thick cyan; background: #181818; padding: 0 1;
    }
    ProjectFolderPicker Label { height: 1; }
    ProjectFolderPicker DirectoryTree { height: 1fr; }
    ProjectFolderPicker Horizontal { height: 3; }
    ProjectFolderPicker Button { margin-right: 1; min-width: 8; }
    #folder-error { height: 2; color: #ff8888; }
    """

    def __init__(self, folder: Path) -> None:
        super().__init__()
        self.folder = folder if folder.is_dir() else Path.home()
        self._path = Input(value=str(self.folder), id="folder-location")
        self._tree = FolderTree(self.folder, id="folder-tree")
        self._error = Static("", id="folder-error", markup=False)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Choose project folder (select a directory, then Use Folder)")
            yield self._path
            with Horizontal():
                yield Button("Go", id="folder-go")
                yield Button("Up", id="folder-up")
                yield Button("Home", id="folder-home")
            yield self._tree
            yield self._error
            with Horizontal():
                yield Button("Use Folder", id="folder-select", variant="primary")
                yield Button("Cancel", id="folder-cancel")

    def _go(self, value: str) -> None:
        try:
            self.folder = validate_project_folder(value)
            self._tree.path = self.folder
            self._path.value = str(self.folder)
            self._error.update("")
        except (ValueError, OSError) as exc:
            self._error.update(str(exc))

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        event.stop()
        self._path.value = str(event.path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._go(self._path.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        action = event.button.id
        if action == "folder-cancel":
            self.dismiss(None)
        elif action == "folder-go":
            self._go(self._path.value)
        elif action == "folder-up":
            try:
                self._go(str(validate_project_folder(self._path.value).parent))
            except (ValueError, OSError) as exc:
                self._error.update(str(exc))
        elif action == "folder-home":
            self._go(str(Path.home()))
        elif action == "folder-select":
            try:
                folder = validate_project_folder(self._path.value)
            except (ValueError, OSError) as exc:
                self._error.update(str(exc))
                return
            self.dismiss(folder)


class ProjectFolderField(Vertical):
    DEFAULT_CSS = """
    ProjectFolderField { height: 4; }
    ProjectFolderField Label { height: 1; }
    ProjectFolderField Horizontal { height: 3; }
    ProjectFolderField Input { width: 1fr; }
    ProjectFolderField Button { width: 12; min-width: 10; margin-left: 1; }
    """

    class Changed(Message):
        pass

    def __init__(self, project_path: Optional[Path] = None) -> None:
        super().__init__()
        self._input = Input(value=str(initial_project_folder(project_path)), id="project-path")
        self._input.tooltip = "Working directory for this launch. Type a path or browse folders."

    def compose(self) -> ComposeResult:
        yield Label("Project folder:")
        with Horizontal():
            yield self._input
            yield Button("Browse…", id="btn-browse-project")

    def selected_path(self) -> Path:
        return validate_project_folder(self._input.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.Changed())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        try:
            folder = self.selected_path()
        except (ValueError, OSError):
            folder = Path.home()

        def selected(path: Optional[Path]) -> None:
            if path is not None:
                self._input.value = str(path)

        self.app.push_screen(ProjectFolderPicker(folder), selected)
