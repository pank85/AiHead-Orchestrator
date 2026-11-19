You are the Ergo orchestrator for a delegated job. Run the /ergo pipeline in this workspace.

== PARENT PROTOCOL (read first — no human is reachable) ==
Your direct parent does NOT run inside Herdr (it operates from Telegram, outside any Herdr pane). HERDR_PARENT_PANE_ID is intentionally unset. You must NOT call `herdr agent prompt $HERDR_PARENT_PANE_ID`, you must NOT open user question tabs or Update tabs, and you must NOT ask the human anything.

Instead, your reporting channel is this file:
  /home/pank/projects/AiHead-Orchestrator/.ai/agent-reports/ergo-herdm-migration/STATUS.md
Update its `state:`, `next_gate:` and `notes:` lines at every stage transition, at every gate, on every blocker, and on completion.

Your CHILDREN do have a valid parent: you, pane w2E:p1. They notify you with `herdr agent prompt w2E:p1`. Give every child `HERDR_PARENT_PANE_ID=w2E:p1`.

At the Plan Approval Gate (Step 4): do NOT skip it and do NOT treat the parent as unavailable. Write the plan summary to STATUS.md, then WAIT in this pane — your parent injects the decision into this same pane as a normal message starting with `APPROVED:` or `REVISE:`. No coder starts before that decision arrives.

== INVOCATION ==
Run exactly:
/ergo --no-ai-head --models "scout:agy:gemini-3.8-flash-medium:medium architect:opencode:openai/gpt-6.1-sol workers:agy:gemini-3.8-flash,opencode:openai/gpt-6.1-sol reviewer:agy:gemini-4-argon-high:high" "Complete the herdm -> aihead migration in this repository per the full intent below"

Reason for the overrides — the product default squad is partially broken in this environment, verified by live smoke tests today (do not "fix" by reverting to defaults):
- pi/openai-codex/gpt-6.1-sol (default Architect) -> 401 invalid_client; `pi auth check --provider openai-codex` returns invalid/invalid_state
- pi/xai/grok-4.7 (default Reviewer and worker-2) -> 403 out of credits
- claude/claude-opus-4-6 -> 400 invalid model name; `codex` CLI is not installed
Working routes, all smoke-tested with a PONG round-trip: agy/gemini-3.8-flash-medium, agy/gemini-3.8-flash, agy/gemini-4-argon-high, opencode/openai/gpt-6.1-sol.
All five bindings above already resolve through `herdm catalog squad --mode herdr --models "<spec>" --json` with advisories: [] and sources: override. Re-verify with that command rather than trusting this text.

== CONSTRAINTS ==
- DEFAULT SESSION ONLY. Never create a Herdr session. Everything happens in the existing default session.
- Job workspace is w2E (cwd /home/pank/projects/AiHead-Orchestrator). Every sub-job opens a NEW TAB inside w2E (`herdr tab create --workspace w2E ... --no-focus`). Never `herdr workspace create`, never a second workspace.
- Worktree/branch isolation and single squash-merge landing per the Ergo skill. Config: target_integration_branch=develop, create_develop_if_missing=true, disallow_hard_reset=true.
- unitTestsMode = new: existing tests must be updated in lockstep with the docs, and add focused coverage for the new surface.
- Follow AGENTS.md -> .ai/instructions/project-orientation.md -> README.md. Inspect existing tests before changing behavior.

== INTENT: complete `herdm` -> `aihead` migration in this repository ==

VERIFIED BASELINE (measured today; do not re-derive, but re-verify at the end):
207 matching lines across 43 files, excluding .venv/.git/__pycache__/.pytest_cache/.test-runtime.
Largest: README.md 20, ai_docs/04_CLI_AND_TUI_SPEC.md 18, tests/test_skill_contract.py 17, skills/ergo/SKILL.md 17, skills/aihead/SKILL.md 12, skills/herdr-master/SKILL.md 12, docs/council-aihead.md 10.
Working tree was clean at start.

ROOT CAUSE (verified live, not assumed): `aihead` is a SEPARATE Typer app (`aihead_app` in src/herdr_master/cli/main.py) with ZERO subcommands. `aihead catalog available` returns "No such command 'catalog'". The deprecated `herdm`/`herdr-master` (entry `app`) exposes 21 subcommands: init, status, ergo, orchestration, council, head, aihead, aihead-setup, aihead-cli, config, tui, workspace, install, master, pipeline, skills, catalog, council-state, plus protocol/daemon/worker/pipeline used in docs.

Therefore the migration has TWO PHASES, in this order:

PHASE 1 — close the CLI surface gap in code:
Give `aihead` the same subcommand surface so `aihead <sub>` behaves identically to `herdm <sub>`:
- forward/alias the whole command group onto `aihead_app` (catalog, skills, install, init, status, config, master, worker, pipeline, workspace, protocol, daemon, aihead-cli, council-state, head, tui, ergo, orchestration, council)
- add non-interactive setup flags `--defaults` / `--yes`; today only `herdm aihead-setup --defaults --yes` exists while `aihead --setup` is interactive-only (src/herdr_master/cli/setup_tui.py:2080 prints that exact hint)
- keep the `herdm` and `herdr-master` entry points in pyproject.toml for back-compat; `herdm` already prints "[deprecated] use aihead --help"
- prove it with real invocations, not only unit tests

PHASE 2 — replace `herdm` -> `aihead` across docs, skills and tests, per category:
- SAFE NOW (72 lines, 22 files): launcher invocations that already have an aihead equivalent. Mapping: `herdm ergo "X"` -> `aihead --ergo "X"`; `herdm council "X"` -> `aihead --council "X"`; `herdm orchestration "X"` -> `aihead --orchestration "X"`; `herdm tui` -> `aihead --tui`; `herdm head` -> `aihead`; `herdm aihead "X"` -> `aihead --task "X"`; bare `herdm` -> `aihead`.
- AFTER PHASE 1 (104 lines, 22 files): `herdm <sub>` where sub had no aihead equivalent. Becomes `aihead <sub>` only once Phase 1 makes it real. Touches README.md, ai_docs/04_CLI_AND_TUI_SPEC.md, docs/skills/{ergo,herdr-master,orchestration}.md, skills/*/SKILL.md, skills/*/README.md, scripts/install-skills.sh, scripts/launch-workspace.sh, src/herdr_master/pipeline/ai_head.py, src/herdr_master/cli/main.py, tests/*.
- KEEP (10 lines): branch naming prefix `herdm/{job_name}-{timestamp}` — .herdr-master.toml, src/herdr_master/core/config.py:54, ai_docs/01,02,03,05, GROK plan. It is a git branch prefix, not a CLI call. PROPOSE in the plan whether to rename it to `aihead/...`; let the approval gate decide, do not change it unilaterally.
- KEEP (8 lines): pyproject.toml `herdm = "herdr_master.cli.main:app"`, herdr-plugin.toml `command = ["herdm","head"]`, src/herdr_master/menu.py, and the tests asserting them (test_cli.py, test_menu.py, test_plugin_manifest.py, test_distribution_contract.py). Back-compat contract.
- PROSE (10 lines): judge individually — use `aihead` where it names the user-facing command, keep `herdr-master`/`Herdr-Master` where it names the engine or package.

KNOWN TEST LOCK-IN (must move in lockstep or the suite fails):
tests/test_skill_contract.py asserts `"herdm catalog squad" in body`, `"herdm catalog agent" in body`, `"Do NOT recursively invoke `herdm ergo`"`, `"herdm catalog show --json"`, `"herdm aihead" in aihead_body`, `"herdm skills install --no-force" in aihead_body`.
tests/test_skills_cli.py:333 asserts `run --project {REPO_ROOT} herdm skills install --no-force`.

DOC BUG found during analysis — in scope, fix it:
skills/aihead/README.md:90 and skills/ergo/README.md:55 show `--models "scout=agy:... architect=pi:..."`, but the parser REQUIRES `role:binding`. `herdm catalog squad --models "architect=opencode:openai/gpt-6.1-sol"` fails with: "Unknown role 'architect=opencode' ... Expected role:binding (for example scout:agy:gemini-3.8-flash-medium:medium)". Convert those examples to the colon form.

ACCEPTANCE CRITERIA — demonstrate each with real command output in your report:
1. `grep -rI herdm` over the repo (excluding .venv/.git/__pycache__/.pytest_cache/.test-runtime) leaves ONLY the deliberately-kept categories above; every other occurrence is `aihead`.
2. `aihead <sub>` works for every subcommand the docs advertise — actually run them (aihead catalog available, aihead skills list, aihead status, aihead config, aihead init --help, ...).
3. The non-interactive setup path works (document the exact new flags used).
4. Full test suite green — quote the actual pytest summary line.
5. README.md and every skills/*/README.md consistent with the new surface.
6. Nothing user-facing silently lost: `herdm`/`herdr-master` entry points still present, plugin manifest still resolves.

Write progress to STATUS.md throughout; at the Plan Approval Gate write the plan there and WAIT for APPROVED/REVISE in this pane.