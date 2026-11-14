"""Manifest contract tests for the AiHead Herdr plugin.

Verifies plugin metadata, exact 6 panes and 6 actions, command dispatch,
and portability constraints.
"""

from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "herdr-plugin.toml"

EXPECTED_PANES: dict[str, tuple[str, list[str]]] = {
    "head": ("AiHead", ["herdm", "head"]),
    "master": ("New Master", ["aihead", "--herdr-master"]),
    "ergo": ("New Ergo", ["python3", "src/herdr_master/menu.py", "ergo"]),
    "orchestration": ("Orchestrate", ["python3", "src/herdr_master/menu.py", "orchestration"]),
    "council": ("AI Council", ["python3", "src/herdr_master/menu.py", "council"]),
    "settings": ("Settings", ["python3", "src/herdr_master/menu.py", "settings"]),
}

EXPECTED_ACTIONS: dict[str, tuple[str, list[str]]] = {
    "head": (
        "AiHead",
        [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "pank85.aihead-cockpit",
            "--entrypoint",
            "head",
            "--placement",
            "tab",
            "--focus",
        ],
    ),
    "master": (
        "New Master",
        [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "pank85.aihead-cockpit",
            "--entrypoint",
            "master",
            "--placement",
            "tab",
            "--focus",
        ],
    ),
    "ergo": (
        "New Ergo",
        [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "pank85.aihead-cockpit",
            "--entrypoint",
            "ergo",
            "--placement",
            "tab",
            "--focus",
        ],
    ),
    "orchestration": (
        "Orchestrate",
        [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "pank85.aihead-cockpit",
            "--entrypoint",
            "orchestration",
            "--placement",
            "tab",
            "--focus",
        ],
    ),
    "council": (
        "AI Council",
        [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "pank85.aihead-cockpit",
            "--entrypoint",
            "council",
            "--placement",
            "tab",
            "--focus",
        ],
    ),
    "settings": (
        "Settings",
        ["python3", "src/herdr_master/menu.py", "settings-open"],
    ),
}


def _load_manifest() -> dict:
    assert MANIFEST_PATH.exists(), f"Missing manifest at {MANIFEST_PATH}"
    with open(MANIFEST_PATH, "rb") as f:
        return tomllib.load(f)


def test_manifest_metadata():
    manifest = _load_manifest()
    assert manifest["id"] == "pank85.aihead-cockpit"
    assert manifest["name"] == "AiHead"
    assert manifest["version"] == "0.1.0"
    assert manifest["min_herdr_version"] == "0.9.5"
    assert manifest["description"] == "AiHead orchestration actions for Herdr workspaces"
    assert manifest["platforms"] == ["linux", "macos"]


def test_manifest_panes_exact_contract():
    manifest = _load_manifest()
    panes = manifest.get("panes", [])
    assert len(panes) == 6
    pane_ids = [p["id"] for p in panes]
    assert set(pane_ids) == set(EXPECTED_PANES.keys())

    for pane in panes:
        pid = pane["id"]
        expected_title, expected_cmd = EXPECTED_PANES[pid]
        assert pane["title"] == expected_title
        assert pane["command"] == expected_cmd
        assert pane["placement"] == "tab"
        assert pane["platforms"] == ["linux", "macos"]


def test_manifest_actions_exact_contract():
    manifest = _load_manifest()
    actions = manifest.get("actions", [])
    assert len(actions) == 6
    action_ids = [a["id"] for a in actions]
    assert set(action_ids) == set(EXPECTED_ACTIONS.keys())

    for action in actions:
        aid = action["id"]
        expected_title, expected_cmd = EXPECTED_ACTIONS[aid]
        assert action["title"] == expected_title
        assert action["command"] == expected_cmd
        assert action["contexts"] == ["workspace"]
        assert action["platforms"] == ["linux", "macos"]


def test_manifest_portability_and_relative_paths():
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert "/home" not in text
    assert "/Users" not in text
    assert "/root" not in text

    manifest = tomllib.loads(text)
    allowed_executables = {"herdm", "aihead", "python3", "herdr"}
    for section in manifest.get("panes", []) + manifest.get("actions", []):
        command = section["command"]
        assert command[0] in allowed_executables
        for part in command:
            assert not part.startswith("/home")
            assert not part.startswith("/Users")
            assert not part.startswith("/root")
            if part.endswith(".py"):
                script_path = REPO_ROOT / part
                assert not str(part).startswith("/")
                assert ".." not in part
                assert part == "src/herdr_master/menu.py"
                assert script_path.parent.is_dir()
                assert script_path.resolve().is_relative_to(REPO_ROOT)
                assert script_path.is_file()
