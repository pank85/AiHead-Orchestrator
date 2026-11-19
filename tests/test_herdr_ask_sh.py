from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "herdr-ask.sh"


def _make_fake_hc(tmp_path: Path, exit_code: int = 0) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "hc.log"
    hc = bin_dir / "hc"
    hc.write_text(
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> {str(log_file)!r}
exit {exit_code}
""",
        encoding="utf-8",
    )
    hc.chmod(0o755)
    return bin_dir, log_file


def _run_ask(
    tmp_path: Path,
    args: list[str],
    *,
    stdin_text: str | None = None,
    hc_exit: int = 0,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    md = tmp_path / "prompt.md"
    md.write_text("Question?\n", encoding="utf-8")
    bin_dir, log_file = _make_fake_hc(tmp_path, exit_code=hc_exit)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    proc = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--file", str(md), "--to", "t1", "--tag", "approval-1", *args],
        input=stdin_text,
        text=True,
        capture_output=True,
        env=env,
        timeout=5,
    )
    return proc, log_file


def test_herdr_ask_eof_without_info_exits_nonzero(tmp_path: Path) -> None:
    proc, log_file = _run_ask(tmp_path, [], stdin_text="")
    assert proc.returncode == 1
    assert "EOF" in proc.stderr
    assert not log_file.exists()


def test_herdr_ask_eof_with_info_exits_nonzero(tmp_path: Path) -> None:
    proc, log_file = _run_ask(tmp_path, ["--info"], stdin_text="")
    assert proc.returncode == 1
    assert "EOF" in proc.stderr
    assert not log_file.exists()


def test_herdr_ask_preserves_hc_exit_code(tmp_path: Path) -> None:
    proc, _ = _run_ask(tmp_path, ["--answer", "yes"], stdin_text="", hc_exit=42)
    assert proc.returncode == 1
    assert "hc exit 42" in proc.stdout


def test_herdr_ask_happy_path_with_answer(tmp_path: Path) -> None:
    proc, log_file = _run_ask(tmp_path, ["--answer", "approved"])
    assert proc.returncode == 0
    assert log_file.read_text(encoding="utf-8").strip() == "send t1 approval-1: approved"


def test_herdr_ask_info_empty_line_sends_ok(tmp_path: Path) -> None:
    proc, log_file = _run_ask(tmp_path, ["--info"], stdin_text="\n")
    assert proc.returncode == 0
    assert log_file.read_text(encoding="utf-8").strip() == "send t1 approval-1: οκ"
