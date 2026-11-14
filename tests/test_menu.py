"""Mock-only contract tests for the AiHead Herdr command-pane menu."""

from __future__ import annotations

import ast
import inspect
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from herdr_master import menu

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "src" / "herdr_master" / "menu.py"


class FakeScreen:
    def __init__(self, keys: list[int], height: int = 24, width: int = 80) -> None:
        self.keys = list(keys)
        self.height = height
        self.width = width
        self.drawn: list[tuple[int, int, str]] = []
        self.keypad_enabled = False

    def keypad(self, enabled: bool) -> None:
        self.keypad_enabled = enabled

    def erase(self) -> None:
        return None

    def getmaxyx(self) -> tuple[int, int]:
        return self.height, self.width

    def addstr(self, y: int, x: int, text: str) -> None:
        if y >= self.height or x >= self.width:
            raise menu.curses.error("addstr out of range")
        self.drawn.append((y, x, text))

    def refresh(self) -> None:
        return None

    def getch(self) -> int:
        if not self.keys:
            raise AssertionError("getch exhausted")
        return self.keys.pop(0)


@pytest.fixture(autouse=True)
def _block_live_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"live subprocess: {args!r} {kwargs!r}")

    monkeypatch.setattr(menu.subprocess, "run", fail)
    monkeypatch.setattr(menu.curses, "wrapper", fail)
    monkeypatch.delenv(menu.PIN_ENV, raising=False)
    monkeypatch.delenv("HERDR_WORKSPACE_ID", raising=False)


@pytest.fixture(autouse=True)
def _cwd_unchanged() -> object:
    before = Path.cwd()
    yield
    assert Path.cwd() == before


def _completed(
    argv: list[str],
    stdout: str = "",
    code: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, code, stdout=stdout, stderr="")


def _workspace(
    workspace_id: str,
    *,
    focused: bool = True,
    tab: str | None = None,
) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "focused": focused,
        "active_tab_id": tab if tab is not None else f"{workspace_id}:t1",
        "label": workspace_id,
    }


def _pane(
    workspace_id: str,
    cwd: str,
    *,
    focused: bool = True,
    tab: str | None = None,
    pane_id: str | None = None,
) -> dict[str, object]:
    return {
        "workspace_id": workspace_id,
        "pane_id": pane_id or f"{workspace_id}:p1",
        "tab_id": tab if tab is not None else f"{workspace_id}:t1",
        "focused": focused,
        "cwd": cwd,
        "foreground_cwd": "/plugin/root",
    }


def _install_herdr(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspaces: list[dict[str, object]],
    panes: list[dict[str, object]],
    fail: Exception | None = None,
    bad_stdout: str | None = None,
    status: int = 0,
) -> list[tuple[list[str], dict[str, object]]]:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded = list(argv)
        calls.append((recorded, kwargs))
        assert kwargs.get("shell", False) is False
        assert isinstance(argv, list)
        if fail is not None and argv[:3] == ["herdr", "workspace", "list"]:
            raise fail
        if bad_stdout is not None and argv[0] == "herdr" and "open" not in argv:
            return _completed(recorded, bad_stdout, 0 if status == 0 else status)
        if status != 0 and argv[0] == "herdr" and "open" not in argv:
            return _completed(recorded, "", status)
        if argv[:3] == ["herdr", "workspace", "list"]:
            body = json.dumps({"id": "cli:workspace:list", "result": {"workspaces": workspaces}})
            return _completed(recorded, body)
        if argv[:3] == ["herdr", "pane", "list"]:
            body = json.dumps({"id": "cli:pane:list", "result": {"panes": panes}})
            return _completed(recorded, body)
        return _completed(recorded)

    monkeypatch.setattr(menu.subprocess, "run", fake_run)
    return calls


def _project(tmp_path: Path, name: str = "proj", *, config: bool = True) -> Path:
    project = tmp_path / name
    project.mkdir()
    if config:
        (project / menu.CONFIG_NAME).write_text("general = {}\n", encoding="utf-8")
    return project


def _bind_project(
    monkeypatch: pytest.MonkeyPatch,
    project: Path,
    workspace_id: str = "w1",
) -> list[tuple[list[str], dict[str, object]]]:
    return _install_herdr(
        monkeypatch,
        workspaces=[_workspace(workspace_id)],
        panes=[_pane(workspace_id, str(project))],
    )


def _names(calls: list[tuple[list[str], dict[str, object]]]) -> list[list[str]]:
    return [argv for argv, _kwargs in calls]


def test_options_order_and_commands() -> None:
    assert menu.OPTIONS == (
        ("AiHead", ("herdm", "head")),
        ("New Master", ("aihead", "--herdr-master")),
        ("New Ergo", ("aihead", "--ergo")),
        ("Orchestrate", ("aihead", "--orchestration")),
        ("AI Council", ("aihead", "--council")),
        ("Settings", ("aihead", "--setup")),
    )
    assert menu.PLUGIN_ID == "pank85.aihead-cockpit"
    assert inspect.signature(menu.run_menu).parameters["action"].default is None
    assert list(inspect.signature(menu.select_action).parameters) == ["screen"]


def test_source_has_no_package_or_shell_dependency() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            assert name != "system"
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    assert not (
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is True
                    )
    assert all(not name.startswith(("herdr_master", "aiheaddev")) for name in imported)
    assert "aiheaddev" not in source
    assert "--tui" not in source
    assert "foreground_cwd" not in source


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ([10], 0),
        ([13], 0),
        ([menu.curses.KEY_ENTER], 0),
        ([ord("q")], None),
        ([ord("Q")], None),
        ([27], None),
        ([menu.curses.KEY_UP, 10], 0),
        ([menu.curses.KEY_DOWN, menu.curses.KEY_DOWN, menu.curses.KEY_UP, 10], 1),
        ([menu.curses.KEY_DOWN] * 20 + [10], 5),
    ],
)
def test_select_action_bounds_enter_and_quit(keys: list[int], expected: int | None) -> None:
    screen = FakeScreen(keys)
    assert menu.select_action(screen) == expected
    assert screen.keypad_enabled is True
    labels = [text for _y, _x, text in screen.drawn]
    assert any(label.endswith("AiHead") for label in labels)
    assert any(label.endswith("Settings") for label in labels)


def test_select_action_survives_tiny_terminal() -> None:
    screen = FakeScreen([ord("q")], height=1, width=1)
    assert menu.select_action(screen) is None


def test_interactive_quit_and_interrupt_launch_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(menu.curses, "wrapper", lambda _fn: None)
    menu.run_menu()

    def _interrupt(_fn: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(menu.curses, "wrapper", _interrupt)
    menu.run_menu()


def test_interactive_selection_launches_exact_option_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    calls = _bind_project(monkeypatch, project)
    monkeypatch.setattr("builtins.input", lambda _prompt="": 'ship "it"; rm -rf /')
    expected = {
        0: ["herdm", "head"],
        1: ["aihead", "--herdr-master", "--config", str((project / menu.CONFIG_NAME).resolve())],
        2: [
            "aihead",
            "--ergo",
            'ship "it"; rm -rf /',
            "--config",
            str((project / menu.CONFIG_NAME).resolve()),
        ],
        3: [
            "aihead",
            "--orchestration",
            'ship "it"; rm -rf /',
            "--config",
            str((project / menu.CONFIG_NAME).resolve()),
        ],
        4: [
            "aihead",
            "--council",
            'ship "it"; rm -rf /',
            "--config",
            str((project / menu.CONFIG_NAME).resolve()),
        ],
        5: ["aihead", "--setup", "--config", str((project / menu.CONFIG_NAME).resolve())],
    }
    for index, argv in expected.items():
        calls.clear()
        monkeypatch.setattr(menu.curses, "wrapper", lambda _fn, index=index: index)
        menu.run_menu()
        assert _names(calls)[-1] == argv
        assert all(call[1].get("shell", False) is False for call in calls)


@pytest.mark.parametrize("value", ["", "   "])
def test_objective_blank_cancels(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt="": value)
    menu.run_menu(action="ergo")


@pytest.mark.parametrize("exc", [EOFError, KeyboardInterrupt])
def test_objective_cancel_on_eof_or_interrupt(
    monkeypatch: pytest.MonkeyPatch,
    exc: type[BaseException],
) -> None:
    def _input(_prompt: str = "") -> str:
        raise exc

    monkeypatch.setattr("builtins.input", _input)
    menu.run_menu(action="orchestration")
    menu.run_menu(action="council")


def test_objective_preserves_position_without_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, config=False)
    calls = _bind_project(monkeypatch, project)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "one objective")
    menu.run_menu(action="ergo")
    assert _names(calls)[-1] == ["aihead", "--ergo", "one objective"]
    assert not (project / menu.CONFIG_NAME).exists()


def test_direct_slugs_bypass_curses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    calls = _bind_project(monkeypatch, project)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "review the gate")

    def fail_wrapper(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("direct slug used curses")

    monkeypatch.setattr(menu.curses, "wrapper", fail_wrapper)
    menu.run_menu(action="settings")
    menu.run_menu(action="council")
    launched = _names(calls)
    setup = ["aihead", "--setup", "--config", str((project / menu.CONFIG_NAME).resolve())]
    assert setup in launched
    assert [
        "aihead",
        "--council",
        "review the gate",
        "--config",
        str((project / menu.CONFIG_NAME).resolve()),
    ] in launched


def test_bad_slug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["herdr_master.menu", "nope"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("herdr_master.menu", run_name="__main__")
    assert caught.value.code == 1
    with pytest.raises(menu.MenuError, match="invalid slug"):
        menu.run_menu(action="Ergo")
    with pytest.raises(menu.MenuError, match="invalid slug"):
        menu.main(["settings", "extra"])


def test_script_dispatch_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = _project(tmp_path, config=False)
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-script")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "settings-open"])
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        assert kwargs.get("shell", False) is False
        if argv[:3] == ["herdr", "pane", "list"]:
            body = json.dumps(
                {"result": {"panes": [_pane("w-script", str(project))]}}
            )
            return _completed(list(argv), body)
        return _completed(list(argv))

    monkeypatch.setattr(subprocess, "run", fake_run)
    before = sorted(path.name for path in project.iterdir())
    runpy.run_path(str(SCRIPT), run_name="__main__")
    assert sorted(path.name for path in project.iterdir()) == before
    assert calls[-1] == [
        "herdr",
        "plugin",
        "pane",
        "open",
        "--plugin",
        "pank85.aihead-cockpit",
        "--entrypoint",
        "settings",
        "--workspace",
        "w-script",
        "--placement",
        "tab",
        "--focus",
        "--env",
        f"{menu.PIN_ENV}={(project / menu.CONFIG_NAME).resolve()}",
    ]
    assert menu.PIN_ENV not in os.environ


def test_missing_executable_and_malformed_herdr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = _install_herdr(
        monkeypatch,
        workspaces=[],
        panes=[],
        fail=FileNotFoundError("herdr"),
    )
    with pytest.raises(menu.MenuError, match="missing executable"):
        menu.settings_open()
    assert _names(calls) == [] or all("open" not in argv for argv in _names(calls))

    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w1")],
        panes=[_pane("w1", str(tmp_path))],
        bad_stdout="{",
    )
    with pytest.raises(menu.MenuError, match="malformed"):
        menu.project_directory()
    assert all("open" not in argv for argv in _names(calls))

    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w1")],
        panes=[_pane("w1", str(tmp_path))],
        status=3,
    )
    with pytest.raises(menu.MenuError, match="failed with status 3"):
        menu.settings_open()
    assert all("open" not in argv for argv in _names(calls))


def test_env_selected_workspace_ignores_other_focus(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    origin = _project(tmp_path, "origin")
    other = _project(tmp_path, "other")
    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w-focus"), _workspace("w-env", focused=False)],
        panes=[_pane("w-env", str(origin))],
    )
    monkeypatch.setenv("HERDR_WORKSPACE_ID", "w-env")
    assert menu.project_directory() == origin.resolve()
    assert _names(calls) == [["herdr", "pane", "list", "--workspace", "w-env"]]
    assert other.resolve() != menu.project_directory()


def test_focus_selected_workspace_and_ambiguous_discovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, config=False)
    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w-a", focused=False), _workspace("w-b")],
        panes=[_pane("w-b", str(project))],
    )
    assert menu.project_directory() == project.resolve()
    assert ["herdr", "pane", "list", "--workspace", "w-b"] in _names(calls)

    _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w-a"), _workspace("w-b")],
        panes=[_pane("w-a", str(project))],
    )
    with pytest.raises(menu.MenuError, match="ambiguous or missing focused workspace"):
        menu.project_directory()

    _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w-a", focused=False)],
        panes=[_pane("w-a", str(project))],
    )
    with pytest.raises(menu.MenuError, match="ambiguous or missing focused workspace"):
        menu.settings_open()


def test_active_tab_fallback_and_ambiguous_panes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, config=False)
    _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w1", tab="w1:t2")],
        panes=[
            _pane("w1", str(tmp_path), focused=False, tab="w1:t1", pane_id="w1:p1"),
            _pane("w1", str(project), focused=False, tab="w1:t2", pane_id="w1:p2"),
        ],
    )
    assert menu.project_directory() == project.resolve()

    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w1")],
        panes=[
            _pane("w1", str(project), focused=True, pane_id="w1:p1"),
            _pane("w1", str(tmp_path), focused=True, pane_id="w1:p2"),
        ],
    )
    with pytest.raises(menu.MenuError, match="ambiguous focused pane"):
        menu.settings_open()
    assert all("open" not in argv for argv in _names(calls))

    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w1")],
        panes=[
            _pane("w1", str(project), focused=False, pane_id="w1:p1"),
            _pane("w1", str(tmp_path), focused=False, pane_id="w1:p2"),
        ],
    )
    with pytest.raises(menu.MenuError, match="ambiguous project pane"):
        menu.project_directory()
    assert all("open" not in argv for argv in _names(calls))


@pytest.mark.parametrize(
    "cwd",
    ["relative/proj", "", None],
)
def test_invalid_project_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cwd: str | None,
) -> None:
    pane = _pane("w1", cwd or "")
    if cwd is None:
        pane = _pane("w1", str(tmp_path))
        del pane["cwd"]
    calls = _install_herdr(monkeypatch, workspaces=[_workspace("w1")], panes=[pane])
    with pytest.raises(menu.MenuError):
        menu.project_directory()
    missing = tmp_path / "missing"
    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w1")],
        panes=[_pane("w1", str(missing))],
    )
    with pytest.raises(menu.MenuError, match="not accessible"):
        menu.settings_open()
    file_path = tmp_path / "file"
    file_path.write_text("x", encoding="utf-8")
    _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w1")],
        panes=[_pane("w1", str(file_path))],
    )
    with pytest.raises(menu.MenuError, match="not a directory"):
        menu.project_directory()
    assert all("open" not in argv for argv in _names(calls))


def test_ancestor_config_discovery(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root_config = tmp_path / menu.CONFIG_NAME
    root_config.write_text("root = true\n", encoding="utf-8")
    mid = tmp_path / "mid"
    mid.mkdir()
    mid_config = mid / menu.CONFIG_NAME
    mid_config.write_text("mid = true\n", encoding="utf-8")
    project = mid / "proj"
    project.mkdir()
    _bind_project(monkeypatch, project)
    assert menu.project_config() == mid_config.resolve()

    local = project / menu.CONFIG_NAME
    local.write_text("local = true\n", encoding="utf-8")
    assert menu.project_config() == local.resolve()


def test_invalid_config_is_not_treated_as_absence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    ancestor = tmp_path / menu.CONFIG_NAME
    ancestor.write_text("ok = true\n", encoding="utf-8")
    dangling = project / menu.CONFIG_NAME
    dangling.symlink_to(project / "missing.toml")
    _bind_project(monkeypatch, project)
    with pytest.raises(menu.MenuError, match="dangling"):
        menu.project_config()

    dangling.unlink()
    dangling.mkdir()
    with pytest.raises(menu.MenuError, match="not a file"):
        menu.project_config()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file mode")
def test_unreadable_config_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = _project(tmp_path)
    config = project / menu.CONFIG_NAME
    config.chmod(0)
    _bind_project(monkeypatch, project)
    try:
        with pytest.raises(menu.MenuError, match="not readable"):
            menu.project_config()
    finally:
        config.chmod(0o600)


def test_no_config_setup_target_is_not_created(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, config=False)
    calls = _bind_project(monkeypatch, project)
    target = (project / menu.CONFIG_NAME).resolve()
    assert menu.project_config() == target
    assert not target.exists()
    menu.settings_open()
    assert not target.exists()
    assert _names(calls)[-1][-1] == f"{menu.PIN_ENV}={target}"
    assert Path.cwd() == Path.cwd()


def test_explicit_pin_precedence_and_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "discovered")
    pinned = tmp_path / "pinned" / menu.CONFIG_NAME
    pinned.parent.mkdir()
    pinned.write_text("pin = true\n", encoding="utf-8")
    _bind_project(monkeypatch, project)
    monkeypatch.setenv(menu.PIN_ENV, str(pinned))
    assert menu.project_config() == pinned.resolve()

    monkeypatch.setenv(menu.PIN_ENV, "relative/.herdr-master.toml")
    with pytest.raises(menu.MenuError, match="not absolute"):
        menu.project_config()

    missing_parent = tmp_path / "gone" / menu.CONFIG_NAME
    monkeypatch.setenv(menu.PIN_ENV, str(missing_parent))
    with pytest.raises(menu.MenuError, match="not accessible"):
        menu.project_config()

    monkeypatch.setenv(menu.PIN_ENV, str(tmp_path / "not-config.txt"))
    with pytest.raises(menu.MenuError, match="not accessible"):
        menu.run_menu(action="settings")


def test_settings_open_snapshots_focused_pane_and_pins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    origin = _project(tmp_path, "origin")
    other = _project(tmp_path, "other", config=False)
    spaced = tmp_path / "my project"
    spaced.mkdir()
    origin = spaced
    (origin / menu.CONFIG_NAME).write_text("origin = true\n", encoding="utf-8")
    calls = _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w-origin"), _workspace("w-other", focused=False)],
        panes=[_pane("w-origin", str(origin))],
    )
    menu.settings_open()
    opened = _names(calls)[-1]
    pin = (origin / menu.CONFIG_NAME).resolve()
    assert opened == [
        "herdr",
        "plugin",
        "pane",
        "open",
        "--plugin",
        "pank85.aihead-cockpit",
        "--entrypoint",
        "settings",
        "--workspace",
        "w-origin",
        "--placement",
        "tab",
        "--focus",
        "--env",
        f"{menu.PIN_ENV}={pin}",
    ]
    assert '"' not in opened[-1]
    assert menu.PIN_ENV not in os.environ

    calls.clear()

    def pinned_only(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((list(argv), kwargs))
        if argv[0] == "herdr":
            raise AssertionError("settings pane rediscovered Herdr after the pin")
        return _completed(list(argv))

    monkeypatch.setenv(menu.PIN_ENV, str(pin))
    monkeypatch.setattr(menu.subprocess, "run", pinned_only)
    _install_herdr(
        monkeypatch,
        workspaces=[_workspace("w-other")],
        panes=[_pane("w-other", str(other))],
    )
    monkeypatch.setattr(menu.subprocess, "run", pinned_only)
    menu.run_menu(action="settings")
    assert _names(calls) == [["aihead", "--setup", "--config", str(pin)]]
    wrote_other = any(
        path.name == menu.CONFIG_NAME and path.parent == other for path in other.iterdir()
    )
    assert not wrote_other


def test_stale_pin_does_not_override_settings_open_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    stale = tmp_path / "stale" / menu.CONFIG_NAME
    stale.parent.mkdir()
    stale.write_text("stale = true\n", encoding="utf-8")
    calls = _bind_project(monkeypatch, project, "w-live")
    monkeypatch.setenv(menu.PIN_ENV, str(stale))
    menu.settings_open()
    assert f"{menu.PIN_ENV}={(project / menu.CONFIG_NAME).resolve()}" in _names(calls)[-1]


def test_launch_failure_does_not_write_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, config=False)
    calls = _bind_project(monkeypatch, project)

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((list(argv), kwargs))
        if argv[:4] == ["herdr", "plugin", "pane", "open"]:
            raise FileNotFoundError(argv[0])
        if argv[:3] == ["herdr", "pane", "list"]:
            body = json.dumps({"result": {"panes": [_pane("w1", str(project))]}})
            return _completed(list(argv), body)
        if argv[:3] == ["herdr", "workspace", "list"]:
            body = json.dumps({"result": {"workspaces": [_workspace("w1")]}})
            return _completed(list(argv), body)
        return _completed(list(argv), code=7)

    monkeypatch.setattr(menu.subprocess, "run", fake_run)
    with pytest.raises(menu.MenuError, match="missing executable"):
        menu.settings_open()
    assert not (project / menu.CONFIG_NAME).exists()
    monkeypatch.setattr("builtins.input", lambda _prompt="": "go")
    with pytest.raises(menu.MenuError, match="failed with status 7"):
        menu.run_menu(action="ergo")
