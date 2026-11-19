"""Opt-in Core operator-question integration test using isolated hc and herdr-ask.sh."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "herdr-ask.sh"


def test_core_herdr_ask_sends_answer_to_parent(tmp_path_factory):
    binary = os.environ.get("HC_TEST_BINARY")
    if not binary:
        pytest.skip("HC_TEST_BINARY is not set; real Core integration not run")
    if sys.platform != "linux":
        pytest.skip("isolated Core integration is only verified on Linux")
    binary = str(Path(binary).resolve(strict=True))
    root = tmp_path_factory.mktemp("hc-ask")
    for name in ("home", "config", "data", "cache", "runtime", "bin"):
        (root / name).mkdir(mode=0o700)
    hc_link = root / "bin" / "hc"
    hc_link.symlink_to(binary)

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
    md = root / "question.md"
    md.write_text("Approve deployment?\n", encoding="utf-8")

    parent_script = r"""
import os, sys
print("PARENT_READY " + os.environ["HC_TERMINAL_ID"], flush=True)
line = sys.stdin.readline()
print("RECEIVED " + line.strip(), flush=True)
"""
    import json

    parent_id = None
    try:
        spawn = subprocess.run(
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
        parent_id = json.loads(spawn.stdout)["address"]
        assert parent_id.startswith("t"), spawn.stdout

        subprocess.run(
            [binary, "wait", parent_id, "--output", "PARENT_READY", "--timeout", "8"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )

        proc = subprocess.run(
            [
                "bash",
                str(SCRIPT_PATH),
                "--file",
                str(md),
                "--to",
                parent_id,
                "--tag",
                "approval-42",
                "--answer",
                "ship it",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert f"στάλθηκε → {parent_id}" in proc.stdout

        subprocess.run(
            [
                binary, "wait", parent_id,
                "--output", "RECEIVED approval-42: ship it", "--timeout", "8",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        logs = subprocess.run(
            [binary, "read", parent_id],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        assert "RECEIVED approval-42: ship it" in logs.stdout, logs.stdout
    finally:
        if parent_id:
            subprocess.run([binary, "kill", parent_id], env=env, timeout=10)
        stopped = subprocess.run(
            [binary, "server", "stop"],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not (root / "s").exists()
