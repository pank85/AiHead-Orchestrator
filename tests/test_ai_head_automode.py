"""Automode prompt policy, launcher threading, and CLI override contract."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from herdr_master.cli.main import aihead_app, app
from herdr_master.core.available_models import AvailableModels
from herdr_master.core.context import HerdrContext
from herdr_master.herdr.client import HerdrClient
from herdr_master.pipeline.ai_head import (
    build_ai_head_system_prompt,
    build_interactive_ai_head_prompt,
)
from herdr_master.pipeline.launcher import _Allocated, launch_ai_head

runner = CliRunner()

_POLICY = "OPERATING PREFERENCES & POLICIES:"
_RESTRICTED = AvailableModels(native={"pi": ["flash", "pro"]})


def _assert_default_policy(prompt: str) -> None:
    assert "automode = false" in prompt
    assert "unrestricted (no allowlist saved)" in prompt
    assert prompt.count(_POLICY) == 1
    assert "AUTOMODE:" in prompt
    assert "AVAILABLE MODELS:" in prompt


def _assert_true_restricted(prompt: str) -> None:
    assert "automode = true" in prompt
    assert "native.pi: flash, pro" in prompt
    assert "role:binding" in prompt
    assert "scout=" in prompt
    assert prompt.count(_POLICY) == 1


def test_default_prompts_keep_saved_squad_policy_and_one_preference_block():
    system = build_ai_head_system_prompt()
    interactive = build_interactive_ai_head_prompt()

    _assert_default_policy(system)
    _assert_default_policy(interactive)
    schema = system.split("Strict JSON Response Schema:", 1)[1]
    assert '"request_id"' in schema
    assert "automode" not in schema.split("OPERATING INSTRUCTIONS:", 1)[0]


def test_true_automode_lists_allowlist_and_both_flag_formats():
    system = build_ai_head_system_prompt(automode=True, available=_RESTRICTED)
    interactive = build_interactive_ai_head_prompt(automode=True, available=_RESTRICTED)

    _assert_true_restricted(system)
    _assert_true_restricted(interactive)


def _saved_config(path: Path, automode: bool) -> None:
    path.write_text(f"[ai_head]\nautomode = {'true' if automode else 'false'}\n", encoding="utf-8")


def _inside(tmp_path: Path) -> HerdrContext:
    return HerdrContext(
        is_inside_herdr=True,
        session_name="default",
        socket_path=tmp_path / "herdr.sock",
        workspace_id="ws-1",
        pane_id="ws-1:p1",
        tab_id="tab-1",
    )


def _launch_mock() -> AsyncMock:
    return AsyncMock(
        return_value={
            "session": "default",
            "workspace_id": "ws-1",
            "pane_id": "ws-1:p1",
            "split": False,
        }
    )


def _live_client() -> AsyncMock:
    client = AsyncMock()
    client.ping = AsyncMock(return_value={})
    client.get_snapshot = AsyncMock(
        return_value=SimpleNamespace(
            workspaces=[SimpleNamespace(workspace_id="ws-1", label="desk")],
            focused_workspace_id="ws-1",
        )
    )
    return client


def _invoke(command, args: list[str], tmp_path: Path):
    launch = _launch_mock()
    with (
        patch("herdr_master.cli.main.resolve_launch_context", return_value=_inside(tmp_path)),
        patch("herdr_master.cli.main.HerdrClient", return_value=_live_client()),
        patch("herdr_master.cli.main.launch_ai_head", launch),
    ):
        result = runner.invoke(command, args)
    return result, launch


@pytest.mark.parametrize(
    ("command", "prefix"),
    ((app, ["aihead"]), (aihead_app, ["--no-attach"])),
)
def test_automode_flag_overrides_saved_false_without_writing(tmp_path: Path, command, prefix):
    cfg = tmp_path / ".herdr-master.toml"
    _saved_config(cfg, False)
    before = cfg.read_bytes()

    result, launch = _invoke(
        command,
        [*prefix, "--automode", "--config", str(cfg), "--no-split"],
        tmp_path,
    )

    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["automode"] is True
    assert cfg.read_bytes() == before


@pytest.mark.parametrize(
    ("command", "prefix"),
    ((app, ["aihead"]), (aihead_app, ["--no-attach"])),
)
def test_no_automode_flag_overrides_saved_true_without_writing(tmp_path: Path, command, prefix):
    cfg = tmp_path / ".herdr-master.toml"
    _saved_config(cfg, True)
    before = cfg.read_bytes()

    result, launch = _invoke(
        command,
        [*prefix, "--no-automode", "--config", str(cfg), "--no-split"],
        tmp_path,
    )

    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["automode"] is False
    assert cfg.read_bytes() == before


@pytest.mark.parametrize(
    ("command", "prefix"),
    ((app, ["aihead"]), (aihead_app, ["--no-attach"])),
)
def test_omitted_automode_flag_passes_none(tmp_path: Path, command, prefix):
    cfg = tmp_path / ".herdr-master.toml"
    _saved_config(cfg, True)
    before = cfg.read_bytes()

    result, launch = _invoke(command, [*prefix, "--config", str(cfg), "--no-split"], tmp_path)

    assert result.exit_code == 0, result.output
    assert launch.await_args.kwargs["automode"] is None
    assert cfg.read_bytes() == before


@pytest.mark.asyncio
async def test_launch_ai_head_threads_override_and_allowlist_without_writing(tmp_path: Path):
    cfg = tmp_path / ".herdr-master.toml"
    cfg.write_text(
        "[ai_head]\nautomode = false\n\n[models.available.native]\npi = [\"flash\"]\n",
        encoding="utf-8",
    )
    before = cfg.read_bytes()
    client = AsyncMock(spec=HerdrClient)
    allocated = _Allocated(
        client=client,
        session_name="work-session",
        socket_path=tmp_path / "herdr.sock",
        workspace_id="ws-1",
        pane_id="pane-1",
        tab_id="tab-1",
        owns_workspace=False,
    )
    ctx = HerdrContext(
        session_name="work-session",
        socket_path=tmp_path / "herdr.sock",
        is_inside_herdr=True,
        workspace_id="ws-1",
        tab_id="tab-1",
        pane_id="pane-1",
    )
    with (
        patch("herdr_master.pipeline.launcher.resolve_launch_context", return_value=ctx),
        patch(
            "herdr_master.pipeline.launcher._allocate_launch_target",
            new_callable=AsyncMock,
            return_value=allocated,
        ),
        patch(
            "herdr_master.pipeline.launcher._start_and_prompt",
            new_callable=AsyncMock,
        ) as started,
    ):
        await launch_ai_head(
            client=client,
            split=False,
            cwd=str(tmp_path),
            config_path=cfg,
            automode=True,
        )

    prompt = started.await_args.kwargs["prompt"]
    assert "automode = true" in prompt
    assert "native.pi: flash" in prompt
    assert cfg.read_bytes() == before
