# Verifying a pipeline's claims before you believe them

Depth behind §6.1 (Verification) of SKILL.md. Use whenever a coordinator or reviewer
reports "all acceptance criteria PASS", "tests green", or "zero new regressions".

The pipeline's report is a claim about work it did in its own worktree, often with a
different cwd, environment and TMPDIR than yours. Three experiments separate a real
regression from an environment artefact; each is cheap and each is decisive.

## 1. Re-run the decisive checks yourself

Run in the canonical checkout on the target branch, with host state cleared:

```bash
cd <repo>
git status --porcelain                      # only the run/report dir may be untracked
git log --oneline -3                        # confirm the squash commit actually landed
env -u PYTHONPATH -u HERDR_PANE_ID -u HERDR_PARENT_PANE_ID \
    -u HERDR_WORKSPACE_ID -u HERDR_TAB_ID -u HERDR_SOCKET_PATH \
    /home/pank/projects/AiHead-Cockpit/.venv/bin/python3 -m pytest -q --tb=short
```

Quote the summary line verbatim in your report (`3 failed, 646 passed, 1 skipped in
498.95s`). A background run whose output you never saw is not a result: if the log is
missing or truncated (a killed run shows a bare `PYTEST_EXIT=143`), the run produced no
result — say so and re-run rather than repeating the pipeline's number.

## 2. Baseline experiment — is the failure even ours?

For each failing test, run the identical test at the commit BEFORE the change in a
disposable worktree. Same failures at baseline ⇒ pre-existing, not a regression:

```bash
git worktree add --detach /tmp/baseline-check <pre-change-sha>
cd /tmp/baseline-check
env -u PYTHONPATH /home/pank/projects/AiHead-Cockpit/.venv/bin/python3 -m pytest \
    tests/path::test_name -q --tb=line        # compare error text AND line numbers
cd - && git worktree remove --force /tmp/baseline-check
```

Compare the failure text and the source line numbers, not just the names — a matching
diff on both proves the cause predates the change. Never leave the probe worktree behind.

> [!NOTE]
> **Import provenance caveat**: When using a shared editable virtualenv interpreter (such as `/home/pank/projects/AiHead-Cockpit/.venv/bin/python3`), verify that the test runner actually imports package code from the baseline worktree rather than the canonical repository checkout, or the comparison will not reflect genuine baseline behavior.

## 3. Environment experiment — change ONE variable

When the assertion is about paths, environment or config rather than logic, re-run that
test with the single suspect variable changed. The classic: an assertion of
`assert str(Path.home()) not in path` fails whenever pytest's tmp dir lives under `$HOME`
(so every run under a scratch-in-home TMPDIR fails), and passes at once with
`TMPDIR=/tmp`. The result tells you which of the two you are looking at:

```bash
TMPDIR=/tmp env -u PYTHONPATH /home/pank/projects/AiHead-Cockpit/.venv/bin/python3 -m pytest tests/path::test_name -q --tb=line
```

Rule: report the cause (`environment: TMPDIR under $HOME`), not merely "still failing".
A test that only fails under your agent's runtime and passes in CI is not a shipped
regression — but it IS a test that cannot be trusted as a gate, so say which it is.

## 4. Probes that write

Some verification commands persist state. Non-interactive setup is the standing example:
`aihead --setup --defaults --yes` writes a full squad block into `.herdr-master.toml` and
can clobber explanatory comments. Before running any command whose help text mentions
`save`, `install`, `setup --defaults`, `init`, or `--write-*`:

- pass a scratch target when the command accepts one (`--config /tmp/probe.toml`), or
- snapshot first (`git status --porcelain`, `git diff -- <file>`) and restore with
  `git checkout -- <file>` immediately after, verifying `git status` is clean again.

Never leave a probe's output in a tracked file and then report the repo as clean.

## Reporting shape

- Lead with the number you measured yourself, then say whether it differs from the
  pipeline's claim and why.
- Split findings into: **new regressions** (fail at HEAD, pass at baseline),
  **pre-existing** (fail at both), **environment** (passes when the env var is corrected).
- If nothing regressed but the suite is not green, say exactly that — "zero new
  regressions, N pre-existing failures" — instead of "tests pass".
