"""Opt-in isolated Core integration test for single-hop Core delegation."""

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from herdr_master.herdr.core_client import terminal_kill
from herdr_master.herdr.core_delegation import (
    delegate_core_child,
    delegate_core_task,
    run_core_one_child_workflow,
    send_core_child,
)


def test_core_delegation_spawn_and_send_isolated(tmp_path_factory, monkeypatch):
    binary = os.environ.get("HC_TEST_BINARY")
    if not binary:
        pytest.skip("HC_TEST_BINARY is not set; real Core integration not run")
    if sys.platform != "linux":
        pytest.skip("isolated Core integration is only verified on Linux")
    binary = str(Path(binary).resolve(strict=True))
    root = tmp_path_factory.mktemp("hc-deleg")
    for name in ("home", "config", "data", "cache", "runtime"):
        (root / name).mkdir(mode=0o700)

    env = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_RUNTIME_DIR": str(root / "runtime"),
        "HC_SESSION": uuid.uuid4().hex[:12],
        "HC_SOCKET": str(root / "s"),
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    parent_script = (
        "import sys; print('PARENT_READY', flush=True); "
        "line = sys.stdin.readline(); print('PARENT_GOT ' + line.strip(), flush=True)"
    )
    child_script = (
        "import os, subprocess, sys; "
        "print('DELEG_READY', flush=True); "
        "line = sys.stdin.readline().strip(); "
        "print('DELEG_GOT ' + line, flush=True); "
        "subprocess.run("
        "[sys.argv[1], 'send', sys.argv[2], 'REPORT:' + line], check=True, timeout=10"
        ")"
    )

    async def run():
        import json

        parent_spawn = subprocess.run(
            [
                binary, "run", "--bg", "--json", "--cwd", str(root),
                sys.executable, "-u", "-c", parent_script,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        parent_id = json.loads(parent_spawn.stdout)["address"]
        subprocess.run(
            [binary, "wait", parent_id, "--output", "PARENT_READY", "--timeout", "8"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        res = await delegate_core_child(
            [sys.executable, "-u", "-c", child_script, binary, parent_id],
            cwd=root,
            name="deleg-child",
            parent=parent_id,
            executable=binary,
        )
        assert res.address.startswith("t") and res.state == "live"
        try:
            child_info = json.loads(
                subprocess.run(
                    [binary, "get", res.address, "--json"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                ).stdout
            )
            assert child_info["parent"] == parent_id, child_info

            subprocess.run(
                [binary, "wait", res.address, "--output", "DELEG_READY", "--timeout", "8"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            await send_core_child(res.address, "ping-1", executable=binary)
            subprocess.run(
                [
                    binary, "wait", parent_id,
                    "--output", "PARENT_GOT REPORT:ping-1", "--timeout", "8",
                ],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        finally:
            await terminal_kill(res.address, executable=binary)
            await terminal_kill(parent_id, executable=binary)

    try:
        asyncio.run(run())
    finally:
        stopped = subprocess.run(
            [binary, "server", "stop"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not (root / "s").exists()
def test_core_delegate_task_fake_pi_isolated(tmp_path_factory, monkeypatch):
    binary = os.environ.get("HC_TEST_BINARY")
    if not binary:
        pytest.skip("HC_TEST_BINARY is not set; real Core integration not run")
    if sys.platform != "linux":
        pytest.skip("isolated Core integration is only verified on Linux")
    binary = str(Path(binary).resolve(strict=True))
    root = tmp_path_factory.mktemp("hc-task")
    for name in ("home", "config", "data", "cache", "runtime", "bin"):
        (root / name).mkdir(mode=0o700)

    fake_pi = root / "bin" / "pi"
    fake_pi.write_text(
        f"#!{sys.executable}\n"
        "import subprocess, sys, time\n"
        "prompt = sys.argv[-1]\n"
        "assert 'preserve exact text' in prompt, prompt\n"
        "parent = [line.split()[2] for line in prompt.splitlines() "
        "if line.startswith('hc send ')][0]\n"
        f"subprocess.run([{binary!r}, 'send', parent, 'REPORT: fake pi done'], check=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    fake_pi.chmod(0o755)

    env = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_RUNTIME_DIR": str(root / "runtime"),
        "HC_SESSION": uuid.uuid4().hex[:12],
        "HC_SOCKET": str(root / "s"),
        "PATH": f"{root / 'bin'}:{os.defpath}",
        "LANG": "C.UTF-8",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    parent_script = (
        "import sys; print('PARENT_READY', flush=True); "
        "line = sys.stdin.readline(); print('PARENT_GOT ' + line.strip(), flush=True)"
    )

    async def run():
        import json

        parent_spawn = subprocess.run(
            [
                binary, "run", "--bg", "--json", "--cwd", str(root),
                sys.executable, "-u", "-c", parent_script,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        parent_id = json.loads(parent_spawn.stdout)["address"]
        try:
            subprocess.run(
                [binary, "wait", parent_id, "--output", "PARENT_READY", "--timeout", "8"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            res = await delegate_core_task(
                "preserve exact text",
                cwd=root,
                harness="pi",
                name="fake-pi-worker",
                parent=parent_id,
                executable=binary,
            )
            assert res.address.startswith("t") and res.state == "live"
            try:
                subprocess.run(
                    [
                        binary, "wait", parent_id,
                        "--output", "PARENT_GOT REPORT: fake pi done", "--timeout", "8",
                    ],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=True,
                )
            finally:
                await terminal_kill(res.address, executable=binary)
        finally:
            await terminal_kill(parent_id, executable=binary)

    try:
        asyncio.run(run())
    finally:
        stopped = subprocess.run(
            [binary, "server", "stop"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not (root / "s").exists()

def test_core_one_child_workflow_end_to_end_isolated(tmp_path_factory, monkeypatch):
    binary = os.environ.get("HC_TEST_BINARY")
    if not binary:
        pytest.skip("HC_TEST_BINARY is not set; real Core integration not run")
    if sys.platform != "linux":
        pytest.skip("isolated Core integration is only verified on Linux")
    binary = str(Path(binary).resolve(strict=True))
    root = tmp_path_factory.mktemp("hc-one-child")
    for name in ("home", "config", "data", "cache", "runtime"):
        (root / name).mkdir(mode=0o700)

    env = {
        "HOME": str(root / "home"),
        "XDG_CONFIG_HOME": str(root / "config"),
        "XDG_DATA_HOME": str(root / "data"),
        "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_RUNTIME_DIR": str(root / "runtime"),
        "HC_SESSION": uuid.uuid4().hex[:12],
        "HC_SOCKET": str(root / "s"),
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    parent_script = (
        "import sys; print('PARENT_READY', flush=True); "
        "line = sys.stdin.readline(); print('PARENT_GOT ' + line.strip(), flush=True)"
    )
    worker_script = (
        "import subprocess, sys; "
        "subprocess.run([sys.argv[1], 'send', sys.argv[2], 'WORKER_DONE'], check=True, timeout=10)"
    )

    async def run():
        import json

        parent_spawn = subprocess.run(
            [
                binary, "run", "--bg", "--json", "--cwd", str(root),
                sys.executable, "-u", "-c", parent_script,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        parent_id = json.loads(parent_spawn.stdout)["address"]
        try:
            subprocess.run(
                [binary, "wait", parent_id, "--output", "PARENT_READY", "--timeout", "8"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            report = await run_core_one_child_workflow(
                [sys.executable, "-u", "-c", worker_script, binary, parent_id],
                cwd=root,
                name="fake-worker",
                parent=parent_id,
                wait_timeout=10.0,
                executable=binary,
            )
            assert report.parent == parent_id
            assert report.wait_reason == "exit"
            subprocess.run(
                [binary, "wait", parent_id, "--output", "PARENT_GOT WORKER_DONE", "--timeout", "8"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        finally:
            await terminal_kill(parent_id, executable=binary)

    try:
        asyncio.run(run())
    finally:
        stopped = subprocess.run(
            [binary, "server", "stop"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not (root / "s").exists()
