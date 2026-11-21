"""Shared test fixtures isolating tests from live Herdr/Core host environments."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_herdr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip inherited Herdr / Herdr Core environment variables for deterministic runs."""
    for key in list(os.environ):
        if key.startswith("HERDR_") or key in {"HC_TERMINAL_SOCKET", "HERDR_CORE_SOCKET"}:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide an isolated HOME directory with no host ~/.herdr-master.toml leakage."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(home / ".cache"))
    return home
