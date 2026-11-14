"""Curses command-pane menu for AiHead Herdr actions.

File and module execution both dispatch the same slugs:

    python3 src/herdr_master/menu.py ergo
    python3 -m herdr_master.menu settings-open

This module stays import-light so a plugin-root file path works without putting
the repository ``src`` directory on ``sys.path``. Settings transport is an
environment value on the opened pane, never a cwd or config mutation here.
"""

from __future__ import annotations

import curses
import json
import os
import subprocess
import sys
from pathlib import Path

PLUGIN_ID = "pank85.aihead-cockpit"
PIN_ENV = "AIHEAD_SETTINGS_CONFIG"
CONFIG_NAME = ".herdr-master.toml"

OPTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AiHead", ("herdm", "head")),
    ("New Master", ("aihead", "--herdr-master")),
    ("New Ergo", ("aihead", "--ergo")),
    ("Orchestrate", ("aihead", "--orchestration")),
    ("AI Council", ("aihead", "--council")),
    ("Settings", ("aihead", "--setup")),
)

_SLUGS = {
    "ergo": 2,
    "orchestration": 3,
    "council": 4,
    "settings": 5,
}
_OBJECTIVE_FLAGS = {"--ergo", "--orchestration", "--council"}


class MenuError(RuntimeError):
    """Clean menu failure. Callers must not treat this as a successful launch."""


def select_action(screen: object) -> int | None:
    """Return a bounded OPTIONS index, or None when the operator quits."""
    keypad = getattr(screen, "keypad", None)
    if callable(keypad):
        try:
            keypad(True)
        except curses.error:
            pass
    index = 0
    last = len(OPTIONS) - 1
    while True:
        _draw_menu(screen, index)
        try:
            key = screen.getch()
        except KeyboardInterrupt:
            return None
        if key in {ord("q"), ord("Q"), 27}:
            return None
        if key in {curses.KEY_ENTER, 10, 13}:
            return index
        if key == curses.KEY_UP:
            index = max(0, index - 1)
        elif key == curses.KEY_DOWN:
            index = min(last, index + 1)


def project_directory() -> Path:
    """Return the focused pane cwd for the active or env-selected workspace."""
    return _project_directory_for(_workspace_id())


def project_config() -> Path:
    """Return the pinned config, else the project ancestor or setup target.

    A set ``AIHEAD_SETTINGS_CONFIG`` is transport for the settings pane. It is
    validated and returned as-is; an invalid pin never falls back to discovery.
    """
    if PIN_ENV in os.environ:
        return _validate_pin(os.environ[PIN_ENV])
    return _config_for_project(project_directory())


def settings_open() -> None:
    """Snapshot the originating project, then focus the settings pane.

    Discovery runs before the only pane-open call. An already-set pin is not
    consulted: this helper creates the pin for the pane that will consume it.
    """
    workspace_id = _workspace_id()
    project = _project_directory_for(workspace_id)
    config = _config_for_project(project)
    _spawn(
        [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            PLUGIN_ID,
            "--entrypoint",
            "settings",
            "--workspace",
            workspace_id,
            "--placement",
            "tab",
            "--focus",
            "--env",
            f"{PIN_ENV}={config}",
        ],
        capture=False,
    )


def run_menu(action: str | None = None) -> None:
    """Launch one menu action. ``None`` selects interactively and may quit."""
    if action is None:
        try:
            choice = curses.wrapper(select_action)
        except KeyboardInterrupt:
            return
        if choice is None:
            return
        _launch_index(choice)
        return
    if action == "settings-open":
        settings_open()
        return
    try:
        index = _SLUGS[action]
    except KeyError:
        raise MenuError(f"invalid slug: {action}") from None
    _launch_index(index)


def main(argv: list[str] | None = None) -> None:
    """Dispatch zero or one slug. Extra arguments fail before any spawn."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) > 1:
        raise MenuError("invalid slug")
    run_menu(args[0] if args else None)


def _launch_index(index: object) -> None:
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(OPTIONS):
        raise MenuError("selection out of range")
    command = OPTIONS[index][1]
    argv = list(command)
    if command[0] != "aihead":
        _spawn(argv, capture=False)
        return
    if command[-1] in _OBJECTIVE_FLAGS:
        objective = _prompt_objective()
        if objective is None:
            return
        argv.append(objective)
        config = _existing_project_config()
        if config is not None:
            argv.extend(["--config", str(config)])
    elif command[-1] == "--setup":
        argv.extend(["--config", str(project_config())])
    else:
        config = _existing_project_config()
        if config is not None:
            argv.extend(["--config", str(config)])
    _spawn(argv, capture=False)


def _prompt_objective() -> str | None:
    try:
        value = input("Objective: ")
    except (EOFError, KeyboardInterrupt):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _existing_project_config() -> Path | None:
    """Discover a readable ancestor config. Do not consume the settings pin."""
    return _find_config(project_directory())


def _workspace_id() -> str:
    selected = os.environ.get("HERDR_WORKSPACE_ID")
    if isinstance(selected, str) and selected.strip():
        return selected.strip()
    focused = [
        row
        for row in _rows(_query(["herdr", "workspace", "list"]), "workspaces")
        if row.get("focused") is True
    ]
    if len(focused) != 1:
        raise MenuError("ambiguous or missing focused workspace")
    workspace_id = focused[0].get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        raise MenuError("malformed Herdr response")
    return workspace_id.strip()


def _project_directory_for(workspace_id: str) -> Path:
    panes = _scoped_panes(
        _rows(_query(["herdr", "pane", "list", "--workspace", workspace_id]), "panes"),
        workspace_id,
    )
    focused = [pane for pane in panes if pane.get("focused") is True]
    if len(focused) > 1:
        raise MenuError("ambiguous focused pane")
    if len(focused) == 1:
        return _validate_directory(focused[0].get("cwd"))
    tab_id = _active_tab_id(workspace_id)
    in_tab = [pane for pane in panes if tab_id and pane.get("tab_id") == tab_id]
    if len(in_tab) != 1:
        if in_tab:
            raise MenuError("ambiguous project pane")
        raise MenuError("no usable focused project")
    return _validate_directory(in_tab[0].get("cwd"))


def _active_tab_id(workspace_id: str) -> str | None:
    matches = [
        row
        for row in _rows(_query(["herdr", "workspace", "list"]), "workspaces")
        if row.get("workspace_id") == workspace_id
    ]
    if len(matches) != 1:
        raise MenuError("ambiguous or missing workspace")
    tab_id = matches[0].get("active_tab_id")
    if tab_id is None:
        return None
    if not isinstance(tab_id, str) or not tab_id:
        raise MenuError("malformed Herdr response")
    return tab_id


def _scoped_panes(panes: list[dict[str, object]], workspace_id: str) -> list[dict[str, object]]:
    scoped: list[dict[str, object]] = []
    for pane in panes:
        owner = pane.get("workspace_id")
        if owner is None or owner == workspace_id:
            scoped.append(pane)
    return scoped


def _config_for_project(project: Path) -> Path:
    found = _find_config(project)
    if found is not None:
        return found
    return (project / CONFIG_NAME).resolve()


def _find_config(project: Path) -> Path | None:
    for parent in (project, *project.parents):
        candidate = parent / CONFIG_NAME
        found = _classify_config(candidate)
        if found is not None:
            return found
    return None


def _classify_config(candidate: Path) -> Path | None:
    try:
        dangling = candidate.is_symlink() and not candidate.exists()
        exists = candidate.exists()
    except OSError as exc:
        raise MenuError(f"config path is not accessible: {candidate}") from exc
    if dangling:
        raise MenuError(f"dangling config symlink: {candidate}")
    if not exists:
        return None
    return _require_readable_file(candidate)


def _require_readable_file(candidate: Path) -> Path:
    try:
        if not candidate.is_file():
            raise MenuError(f"config path is not a file: {candidate}")
        if not os.access(candidate, os.R_OK):
            raise MenuError(f"config path is not readable: {candidate}")
        return candidate.resolve(strict=True)
    except MenuError:
        raise
    except OSError as exc:
        raise MenuError(f"config path is not accessible: {candidate}") from exc


def _validate_pin(raw: object) -> Path:
    if not isinstance(raw, str) or raw != raw.strip() or not raw:
        raise MenuError("pinned config is not absolute")
    path = Path(raw)
    if not path.is_absolute():
        raise MenuError(f"pinned config is not absolute: {raw}")
    try:
        if path.is_symlink() and not path.exists():
            raise MenuError(f"dangling config symlink: {path}")
        if path.exists():
            return _require_readable_file(path)
    except MenuError:
        raise
    except OSError as exc:
        raise MenuError(f"pinned config is not accessible: {raw}") from exc
    if path.name != CONFIG_NAME or not path.parent.is_dir():
        raise MenuError(f"pinned config is not accessible: {path}")
    return path.resolve()


def _validate_directory(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise MenuError("project directory missing")
    path = Path(value)
    if not path.is_absolute():
        raise MenuError(f"project directory is not absolute: {value}")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MenuError(f"project directory is not accessible: {value}") from exc
    if not resolved.is_dir():
        raise MenuError(f"project directory is not a directory: {value}")
    return resolved


def _query(argv: list[str]) -> dict[str, object]:
    completed = _spawn(argv, capture=True)
    try:
        payload = json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise MenuError("malformed Herdr response") from exc
    if not isinstance(payload, dict):
        raise MenuError("malformed Herdr response")
    return payload


def _rows(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    body = payload.get("result", payload)
    if not isinstance(body, dict):
        raise MenuError("malformed Herdr response")
    rows = body.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise MenuError("malformed Herdr response")
    return rows


def _spawn(argv: list[str], *, capture: bool) -> subprocess.CompletedProcess[str]:
    if not argv or any(not isinstance(part, str) for part in argv):
        raise MenuError("argv must be strings")
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=capture,
            text=True if capture else None,
        )
    except OSError as exc:
        raise MenuError(f"missing executable: {argv[0]}") from exc
    if completed.returncode != 0:
        raise MenuError(f"{argv[0]} failed with status {completed.returncode}")
    return completed


def _draw_menu(screen: object, index: int) -> None:
    try:
        screen.erase()
        height, width = screen.getmaxyx()
    except curses.error:
        return
    _add(screen, height, width, 0, 0, "AiHead")
    for row, (label, _command) in enumerate(OPTIONS, start=2):
        marker = ">" if row - 2 == index else " "
        _add(screen, height, width, row, 0, f"{marker} {label}")
    try:
        screen.refresh()
    except curses.error:
        return


def _add(screen: object, height: int, width: int, y: int, x: int, text: str) -> None:
    if y < 0 or x < 0 or y >= height or width <= 1:
        return
    try:
        screen.addstr(y, x, text[: width - 1 - x])
    except curses.error:
        return


def _entry(argv: list[str] | None = None) -> None:
    try:
        main(argv)
    except MenuError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    _entry()
