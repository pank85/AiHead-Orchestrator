from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from herdr_master.herdr.core_client import CoreClientError
from herdr_master.herdr.core_delegation import (
    CoreDelegationError,
    delegate_core_child,
    delegate_core_task,
    run_core_one_child_workflow,
    send_core_child,
)


async def test_delegate_core_child_happy_path(monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    with patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run:
        run.return_value = {"address": "t2", "state": "live", "reason": None}
        res = await delegate_core_child(["pi", "--", "hi"], cwd="/tmp/proj", name="c1")
    assert res.address == "t2"
    assert res.state == "live"
    run.assert_awaited_once()
    assert run.call_args.kwargs["parent"] == "t1"
    assert run.call_args.kwargs["no_parent"] is False


async def test_delegate_core_child_requires_parent_unless_standalone(monkeypatch):
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    with pytest.raises(CoreDelegationError, match="direct parent"):
        await delegate_core_child(["pi"], cwd="/tmp/proj")
    with pytest.raises(CoreDelegationError, match="cannot combine"):
        await delegate_core_child(["pi"], cwd="/tmp/proj", parent="t1", allow_standalone=True)
    with patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run:
        run.return_value = {"address": "t2", "state": "live", "reason": None}
        res = await delegate_core_child(["pi"], cwd="/tmp/proj", allow_standalone=True)
    assert res.address == "t2"
    assert run.call_args.kwargs["parent"] is None
    assert run.call_args.kwargs["no_parent"] is True


async def test_delegate_core_child_rejects_invalid_inputs():
    with pytest.raises(CoreDelegationError, match="argv"):
        await delegate_core_child([], cwd="/tmp/proj")
    with pytest.raises(CoreDelegationError, match="cwd"):
        await delegate_core_child(["pi"], cwd="   ")


async def test_delegate_core_child_no_retry_on_error_or_ambiguous(monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    with patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run:
        run.side_effect = CoreClientError("timeout")
        with pytest.raises(CoreDelegationError, match="inspect before retrying"):
            await delegate_core_child(["pi"], cwd="/tmp/proj")
        run.assert_awaited_once()

    with patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run:
        run.return_value = {"address": "t2", "reason": "spawn_pending"}
        with pytest.raises(CoreDelegationError, match="spawn is pending"):
            await delegate_core_child(["pi"], cwd="/tmp/proj")
        run.assert_awaited_once()

    with patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run:
        run.return_value = {"state": "live"}
        with pytest.raises(CoreDelegationError, match="missing address"):
            await delegate_core_child(["pi"], cwd="/tmp/proj")
        run.assert_awaited_once()


async def test_send_core_child_no_retry_on_error_and_validates():
    with pytest.raises(CoreDelegationError, match="target"):
        await send_core_child("  ", "hello")
    with pytest.raises(CoreDelegationError, match="text"):
        await send_core_child("t2", "")

    with patch("herdr_master.herdr.core_client.terminal_send", new_callable=AsyncMock) as send:
        send.side_effect = CoreClientError("broken pipe")
        with pytest.raises(CoreDelegationError, match="inspect before retrying"):
            await send_core_child("t2", "hello")
        send.assert_awaited_once()


async def test_run_core_one_child_workflow_requires_parent(monkeypatch):
    monkeypatch.delenv("HC_TERMINAL_ID", raising=False)
    with pytest.raises(CoreDelegationError, match="direct parent"):
        await run_core_one_child_workflow(["pi"], cwd="/tmp/proj")


async def test_run_core_one_child_workflow_happy_path(monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    with (
        patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run,
        patch("herdr_master.herdr.core_client.request", new_callable=AsyncMock) as req,
    ):
        run.return_value = {"address": "t2", "state": "live", "reason": None}
        req.side_effect = [{"reason": "exit"}, {"exit_code": 0}]
        report = await run_core_one_child_workflow(["pi"], cwd="/tmp/proj", name="w1")
    assert report.parent == "t1"
    assert report.child == "t2"
    assert report.exit_code == 0
    assert report.wait_reason == "exit"
    assert run.call_args.kwargs["labels"] == {"aihead.role": "worker", "aihead.parent": "t1"}


async def test_run_core_one_child_workflow_rejects_timeout_and_nonzero_exit(monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    with (
        patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run,
        patch("herdr_master.herdr.core_client.request", new_callable=AsyncMock) as req,
    ):
        run.return_value = {"address": "t2", "state": "live", "reason": None}
        req.return_value = {"reason": "timeout"}
        with pytest.raises(CoreDelegationError, match="did not exit cleanly"):
            await run_core_one_child_workflow(["pi"], cwd="/tmp/proj")

    for bad_info in [
        {},
        {"exit_code": None},
        {"exit_code": False},
        {"exit_code": "0"},
        {"exit_code": 2},
    ]:
        with (
            patch("herdr_master.herdr.core_client.terminal_run", new_callable=AsyncMock) as run,
            patch("herdr_master.herdr.core_client.request", new_callable=AsyncMock) as req,
        ):
            run.return_value = {"address": "t2", "state": "live", "reason": None}
            req.side_effect = [{"reason": "exit"}, bad_info]
            with pytest.raises(CoreDelegationError, match="t2.*exit_code="):
                await run_core_one_child_workflow(["pi"], cwd="/tmp/proj")


async def test_delegate_core_task_validates_harness_and_preserves_objective(tmp_path, monkeypatch):
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    with pytest.raises(CoreDelegationError, match="only supports harness 'pi'"):
        await delegate_core_task("fix bug", cwd=tmp_path, harness="claude")
    with pytest.raises(CoreDelegationError, match="objective"):
        await delegate_core_task("   ", cwd=tmp_path)

    with (
        patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"),
        patch(
            "herdr_master.herdr.core_delegation.delegate_core_child",
            new_callable=AsyncMock,
        ) as deleg,
    ):
        await delegate_core_task(
            "exact user objective", cwd=tmp_path, effort="high", name="w1"
        )
    argv = deleg.call_args.args[0]
    assert argv[0] == "/usr/bin/pi"
    assert argv[-2] == "--"
    assert "hc send t1" in argv[-1]
    assert "exact user objective" in argv[-1]
    assert deleg.call_args.kwargs["wait"] is False


async def test_delegate_core_task_uses_cwd_worker_binding_and_fails_closed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HC_TERMINAL_ID", "t1")
    other_dir = tmp_path / "process_cwd"
    other_dir.mkdir()
    (other_dir / ".herdr-master.toml").write_text(
        '[ai_head.binding]\nkind = "pi"\nmodel = "coord-model"\n'
        '[models]\nworker_models = ["wrong-worker"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(other_dir)

    proj_dir = tmp_path / "project_cwd"
    nested_dir = proj_dir / "nested" / "sub"
    nested_dir.mkdir(parents=True)
    (proj_dir / ".herdr-master.toml").write_text(
        '[ai_head.binding]\nkind = "pi"\nmodel = "coord-model"\n'
        '[models.default_squad]\ncoder = "pi:sonnet"\n',
        encoding="utf-8",
    )

    with (
        patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"),
        patch(
            "herdr_master.herdr.core_delegation.delegate_core_child",
            new_callable=AsyncMock,
        ) as deleg,
    ):
        await delegate_core_task("task", cwd=nested_dir)
    argv = deleg.call_args.args[0]
    assert "coord-model" not in argv
    assert "wrong-worker" not in argv

    # Missing config in cwd does not fall back to caller process cwd
    # (caller process cwd has a conflicting Pi worker_pool model)
    (other_dir / ".herdr-master.toml").write_text(
        '[models.worker_pool.w1]\nkind = "pi"\nmodel = "caller-only-pi"\nenabled = true\n',
        encoding="utf-8",
    )
    no_cfg_dir = tmp_path / "no_cfg"
    no_cfg_dir.mkdir()
    with (
        patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"),
        patch(
            "herdr_master.herdr.core_delegation.delegate_core_child",
            new_callable=AsyncMock,
        ) as deleg2,
    ):
        await delegate_core_task("task", cwd=no_cfg_dir)
    assert "caller-only-pi" not in deleg2.call_args.args[0]

    # Explicit --model not in resolved worker_pool fails closed
    with patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"):
        with pytest.raises(CoreDelegationError, match="not a permitted enabled Pi worker"):
            await delegate_core_task("task", cwd=proj_dir, model="unlisted-model")

    # models.available.core.pi allowlist: allowed, denied, empty, unrelated harness, qualified slug
    allow_dir = tmp_path / "allow_cfg"
    allow_dir.mkdir()
    (allow_dir / ".herdr-master.toml").write_text(
        '[models.worker_pool.w1]\nslug = "pi:m-denied"\nenabled = true\n'
        '[models.worker_pool.w2]\nslug = "pi:m-allowed"\nenabled = true\n'
        '[models.available.core]\npi = ["m-allowed"]\nclaude = ["other"]\n',
        encoding="utf-8",
    )
    with (
        patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"),
        patch(
            "herdr_master.herdr.core_delegation.delegate_core_child",
            new_callable=AsyncMock,
        ) as deleg3,
    ):
        await delegate_core_task("task", cwd=allow_dir)
        assert "m-allowed" in deleg3.call_args.args[0]
        await delegate_core_task("task", cwd=allow_dir, model="pi:m-allowed")

    with patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"):
        with pytest.raises(CoreDelegationError, match="not a permitted enabled Pi worker"):
            await delegate_core_task("task", cwd=allow_dir, model="m-denied")

    empty_allow_dir = tmp_path / "empty_allow"
    empty_allow_dir.mkdir()
    (empty_allow_dir / ".herdr-master.toml").write_text(
        '[models.worker_pool.w1]\nslug = "pi:m-allowed"\nenabled = true\n'
        '[models.available.core]\npi = []\n',
        encoding="utf-8",
    )
    with patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"):
        with pytest.raises(CoreDelegationError, match="No permitted enabled Pi worker"):
            await delegate_core_task("task", cwd=empty_allow_dir)

    # Disallowed/non-Pi worker binding fails closed
    non_pi_dir = tmp_path / "non_pi"
    non_pi_dir.mkdir()
    (non_pi_dir / ".herdr-master.toml").write_text(
        '[models.worker_pool.w1]\nkind = "claude"\nmodel = "sonnet"\nenabled = true\n',
        encoding="utf-8",
    )
    with patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"):
        with pytest.raises(
            CoreDelegationError,
            match="permitted enabled Pi worker|Invalid Core worker configuration",
        ):
            await delegate_core_task("task", cwd=non_pi_dir)

    # Invalid effort fails closed
    with patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"):
        with pytest.raises(CoreDelegationError, match="Invalid Pi worker binding"):
            await delegate_core_task("task", cwd=proj_dir, effort="bogus")

    # Invalid TOML config fails closed
    bad_dir = tmp_path / "bad_cfg"
    bad_dir.mkdir()
    (bad_dir / ".herdr-master.toml").write_text("not = [valid toml", encoding="utf-8")
    with patch("herdr_master.herdr.core_delegation.shutil.which", return_value="/usr/bin/pi"):
        with pytest.raises(CoreDelegationError, match="Invalid Core worker configuration"):
            await delegate_core_task("task", cwd=bad_dir)
