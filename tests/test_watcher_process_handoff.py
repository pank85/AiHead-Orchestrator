"""Offline subprocess integration of the launcher and real watcher owner locks."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from herdr_master.pipeline import parent_contract as contract
from herdr_master.pipeline.child_watch import watcher_ready

PARENT = 'w2Q:pE'
GEN = 'herdr-v1:["terminal",null]'


def fake_herdr(tmp_path, monkeypatch, *, good):
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    executable = bindir / 'herdr'
    if good:
        output = '{"result":{"type":"pane_info","pane":{"pane_id":"w2Q:pE","workspace_id":"w2Q","terminal_id":"terminal","agent":"pi","agent_status":"idle"}}}'
        script = f'#!{sys.executable}\nimport sys\nprint({output!r})\n'
    else:
        script = f'#!{sys.executable}\nimport sys\nprint("{{}}")\nsys.exit(1)\n'
    executable.write_text(script)
    executable.chmod(0o700)
    monkeypatch.setenv('PATH', str(bindir) + os.pathsep + os.environ.get('PATH', ''))
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1] / 'src'))


def ready(directory, run_id):
    return watcher_ready(directory, run_id=run_id, parent_pane_id=PARENT, parent_generation=GEN)


def test_real_owner_ready_persists_and_new_run_after_stop(tmp_path, monkeypatch):
    fake_herdr(tmp_path, monkeypatch, good=True)
    processes = []
    real_popen = subprocess.Popen
    def capture(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        processes.append(proc)
        return proc
    monkeypatch.setattr(contract.subprocess, 'Popen', capture)
    try:
        for i in (1, 2):
            run_id = f'{i:032x}'
            directory = tmp_path / f'run-{i}'
            directory.mkdir(mode=0o700)
            contract._ensure_watcher(directory, PARENT, GEN, tmp_path / 'fake.sock', run_id)
            assert ready(directory, run_id)
            assert not ready(directory, f'{i + 10:032x}')
            # Empty/all-terminal ledger must not make the owner exit while launch is possible.
            time.sleep(.15)
            assert processes[-1].poll() is None and ready(directory, run_id)
            (directory / 'child-watch.stop').touch()
            assert processes[-1].wait(timeout=4) == 0
            assert not ready(directory, run_id)
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)


def test_real_owner_startup_failure_is_bounded_and_unready(tmp_path, monkeypatch):
    fake_herdr(tmp_path, monkeypatch, good=False)
    directory = tmp_path / 'run'
    directory.mkdir(mode=0o700)
    run_id = 'a' * 32
    started = time.monotonic()
    with pytest.raises(RuntimeError, match='bounded run-scoped ownership'):
        contract._ensure_watcher(directory, PARENT, GEN, tmp_path / 'fake.sock', run_id)
    assert time.monotonic() - started < 7
    assert not ready(directory, run_id)
