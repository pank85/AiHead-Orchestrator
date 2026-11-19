"""Opt-in Core terminal reporting test using only disposable fake programs."""

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


def test_core_child_reports_to_direct_parent(tmp_path_factory):
    binary = os.environ.get("HC_TEST_BINARY")
    if not binary:
        pytest.skip("HC_TEST_BINARY is not set; real Core integration not run")
    if sys.platform != "linux":
        pytest.skip("isolated Core integration is only verified on Linux")
    binary = str(Path(binary).resolve(strict=True))
    root = tmp_path_factory.mktemp("hc")
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
    report = "REPORT: harmless child completion"
    child = r'''
import os, subprocess, sys
print("CHILD_READY " + os.environ["HC_TERMINAL_ID"], flush=True)
assert input() == "GO"
subprocess.run([sys.argv[1], "send", "--json", sys.argv[2], sys.argv[3]],
               check=True, timeout=10)
'''
    parent = r'''
import json, os, subprocess, sys
binary, cwd, child_code, report = sys.argv[1:]
parent_id = os.environ["HC_TERMINAL_ID"]
result = subprocess.run([binary, "run", "--bg", "--json", "--no-notify", "--cwd", cwd,
                         sys.executable, "-c", child_code, binary, parent_id, report],
                        capture_output=True, text=True, check=True, timeout=10)
print("CHILD_ADDRESS " + json.loads(result.stdout)["address"], flush=True)
print("PARENT_WAITING", flush=True)
result = subprocess.run([binary, "wait", "--children", "any", "--json", "--timeout", "10"],
                        capture_output=True, text=True, check=True, timeout=15)
print("WAIT_RESULT " + result.stdout.strip(), flush=True)
print("PARENT_REPORT " + json.dumps(input()), flush=True)
input()
'''

    def hc(*args, timeout=20):
        result = subprocess.run(
            [binary, *args], cwd=root, env=env, capture_output=True, text=True, timeout=timeout
        )
        assert result.returncode == 0, (args, result.stdout, result.stderr)
        return result.stdout

    def output_after(address, marker):
        hc("wait", address, "--output", marker, "--timeout", "8")
        output = hc("read", address)
        assert marker in output, output
        return output

    try:
        name = "parent-" + uuid.uuid4().hex[:12]
        created = json.loads(hc(
            "run", "--bg", "--json", "--name", name, "--cwd", str(root),
            sys.executable, "-c", parent, binary, str(root), child, report,
        ))
        parent_id = created["address"]
        output = output_after(parent_id, "PARENT_WAITING")
        child_id = next(line.split()[1] for line in output.splitlines()
                        if line.startswith("CHILD_ADDRESS "))
        child_info = json.loads(hc("get", child_id, "--json"))
        assert child_info["parent"] == parent_id
        output_after(child_id, "CHILD_READY " + child_id)
        hc("send", "--json", child_id, "GO")
        output = output_after(parent_id, "PARENT_REPORT ")
        received = next(line.removeprefix("PARENT_REPORT ") for line in output.splitlines()
                        if line.startswith("PARENT_REPORT "))
        assert json.loads(received) == report
        wait = next(line.removeprefix("WAIT_RESULT ") for line in output.splitlines()
                    if line.startswith("WAIT_RESULT "))
        result = json.loads(wait)
        assert result["reason"] == "children", result
        assert any(item["terminal"] == child_id and item["reason"] == "exit"
                   and item["exit_code"] == 0 for item in result["children"]), result
    finally:
        hc("server", "stop")
        assert not (root / "s").exists()
