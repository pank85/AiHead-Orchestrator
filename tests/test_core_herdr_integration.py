"""Opt-in real Core test; never uses the user's Core session or AI tools."""

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def scratch():
    if not os.environ.get("HC_TEST_BINARY"):
        pytest.skip("HC_TEST_BINARY is not set; real herdr-core integration not run")
    if sys.platform != "linux":
        pytest.skip("isolated herdr-core integration is only verified on Linux")
    with tempfile.TemporaryDirectory(prefix="hc-test-", dir="/tmp") as directory:
        yield Path(directory)


def test_isolated_core_head_lifecycle(scratch):
    binary = os.environ.get("HC_TEST_BINARY")
    assert binary
    binary = str(Path(binary).resolve(strict=True))
    version = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10)
    assert version.returncode == 0 and "0.1.21" in version.stdout, version.stdout + version.stderr

    home = scratch / "home"
    home.mkdir()
    bindir = scratch / "bin"
    bindir.mkdir()
    pi = bindir / "pi"
    pi.write_text(f"#!{sys.executable}\nimport time\nwhile True: time.sleep(1)\n")
    pi.chmod(0o755)
    project = scratch / "project"
    project.mkdir()
    env = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / "config"),
        "XDG_DATA_HOME": str(home / "data"),
        "XDG_CACHE_HOME": str(home / "cache"),
        "XDG_RUNTIME_DIR": str(home / "runtime"),
        "HC_SESSION": uuid.uuid4().hex[:12],
        "PATH": str(bindir) + os.pathsep + os.defpath,
        "PYTHONPATH": os.pathsep.join(
            [str(Path(__file__).resolve().parents[1] / "src")]
            + [str(Path(p).resolve()) for p in sys.path if p]
        ),
    }
    Path(env["XDG_RUNTIME_DIR"]).mkdir(mode=0o700)
    # A separate interpreter keeps HOME, PATH, and cwd out of the pytest process.
    worker = r'''
import asyncio
from hashlib import sha256
from pathlib import Path
import sys
from herdr_master.herdr.core_aihead import CoreAiHeadError, ensure_core_aihead
from herdr_master.herdr.core_client import terminal_list

async def run():
    binary, project = sys.argv[1:]
    project = Path(project).resolve()
    options = dict(backend="core", cwd=project, kind="pi", model="xai/test",
                   executable=binary, timeout=15)
    initial = await terminal_list(executable=binary, timeout=15)
    assert initial["terminals"] == [], initial
    assert initial.get("machines", []) == [], initial
    created = await ensure_core_aihead(**options, task="isolated test task")
    assert created.created and created.state == "live", created
    assert created.prompt_status == "argv_unconfirmed", created
    listing = await terminal_list(executable=binary, timeout=15)
    assert len(listing["terminals"]) == 1, listing
    terminal = listing["terminals"][0]
    digest = sha256(str(project).encode()).hexdigest()
    assert terminal["name"] == "aihead-" + digest[:24], terminal
    assert terminal["labels"] == {"aihead.role": "head", "aihead.project": digest}, terminal
    assert terminal["address"] == created.address and terminal["state"] == "live", terminal
    reused = await ensure_core_aihead(**options)
    assert not reused.created and reused.address == created.address, reused
    assert reused.prompt_status == "not_sent", reused
    try:
        await ensure_core_aihead(**options, task="must not be sent")
    except CoreAiHeadError as exc:
        assert "task was not sent" in str(exc), exc
    else:
        raise AssertionError("reuse with a task must be rejected")
    assert len((await terminal_list(executable=binary, timeout=15))["terminals"]) == 1

asyncio.run(run())
'''
    try:
        result = subprocess.run(
            [sys.executable, "-c", worker, binary, str(project)], cwd=project,
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        stopped = subprocess.run(
            [binary, "server", "stop"], cwd=project, env=env,
            capture_output=True, text=True, timeout=15,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
