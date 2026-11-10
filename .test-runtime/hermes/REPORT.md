# Hermes interactive AiHead implementation

## Changes owned by this task

- /home/pank/projects/herdr-master/src/herdr_master/core/catalog.py — Hermes enum/spec, interactive Herdr launch arguments, provider/model split, reasoning/permission flags, skill target and capability notes. No native adapter or headless runner added; existing default models/squad unchanged.
- /home/pank/projects/herdr-master/src/herdr_master/cli/main.py — both interactive AiHead entrypoint help strings.
- /home/pank/projects/herdr-master/src/herdr_master/cli/skills.py — list Hermes among skill-install harnesses.
- /home/pank/projects/herdr-master/skills/aihead/SKILL.md — ONLY the new separate interactive AiHead launch section; all concurrent parent/child directive edits preserved.
- /home/pank/projects/herdr-master/tests/test_hermes.py — new offline catalog/config/CLI flag and headless/native guard tests.
- /home/pank/projects/herdr-master/tests/test_catalog.py — registry and skill-target contracts.
- /home/pank/projects/herdr-master/tests/test_skills_cli.py — six destinations per skill (30 links), including Hermes.
- /home/pank/projects/herdr-master/tests/test_ai_head_interactive.py — parameterize binding override regression for Hermes, mock registration readiness, verify actual argv and bootstrap delivery.
- /home/pank/projects/AiHeadDev/src/aiheaddev/ui/tui/modals/settings.py — provider/model input tooltip showing Hermes example; selectors already derive from shared catalog.
- /home/pank/projects/AiHeadDev/tests/test_settings_modal.py — catalog options/native exclusion/effort regression, parameterized Claude/Hermes UI save and disk persistence.

Runtime logs and temporary artifacts are retained under /home/pank/projects/herdr-master/.test-runtime/hermes and /home/pank/projects/AiHeadDev/.test-runtime/hermes. No cleanup/deletion requested or performed by the agent. No commits, pushes, deployments or branch changes. Concurrent modifications to skills/ergo/SKILL.md, skills/orchestration/SKILL.md and existing parts of skills/aihead/SKILL.md were preserved, not authored here.

## Launch contract

Verified local CLI help using `command -v hermes; hermes --help` (no inference). The installed executable was /home/pank/.local/bin/hermes. Default interactive launch arguments:

```text
hermes --provider openai-codex --model gpt-6.1-terra --reasoning high --yolo
```

`--yolo` is omitted with `--no-skip-permissions`. Shared project effort levels are low/medium/high/max (xhigh normalizes to max), sent using Hermes's actual `--reasoning`, not Pi's `--thinking`. Provider/model splits on the first slash. Skill installation points to ~/.hermes/skills/<skill>/SKILL.md; no actual user skill installation was performed. Herdr receives kind=hermes separately from argv and sends the AiHead bootstrap prompt after registration. This starts a separate process; it does not become or resume the existing user conversation.

Example (documentation only, NOT executed):

```sh
aihead --harness hermes --model openai-codex/gpt-6.1-terra --effort high
```

## Final successful verification commands and results

Run from /home/pank/projects/herdr-master:

```sh
HOME=$PWD/.test-runtime/hermes/home TMPDIR=$PWD/.test-runtime/hermes/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD/src .venv/bin/python -B -m pytest -p no:cacheprovider --basetemp=/home/pank/projects/AiHeadDev/.test-runtime/hermes/herdr-contracts -q tests/test_hermes.py tests/test_catalog.py tests/test_skills_cli.py tests/test_ai_head_interactive.py::test_launch_ai_head_binding_overrides
```

Result: 51 passed, 2 warnings, 0.80s. Warnings: `Lesson path not found: ~/.herdr-master/lessons` in isolated HOME. Output retained in /home/pank/projects/herdr-master/.test-runtime/hermes/contracts.log.

```sh
HOME=$PWD/.test-runtime/hermes/home TMPDIR=$PWD/.test-runtime/hermes/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$PWD/src .venv/bin/python -B -m pytest -p no:cacheprovider --basetemp=$PWD/.test-runtime/hermes/run5 -q tests/test_ai_head_interactive.py -k 'not launch and not unix_socket'
```

Result: 50 passed, 11 deselected, 3 isolated-lesson-path warnings, 0.66s. Output retained in /home/pank/projects/herdr-master/.test-runtime/hermes/run5.log.

Run from /home/pank/projects/AiHeadDev:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/pank/projects/herdr-master/src:/home/pank/projects/AiHeadDev/src UI_SANDBOX_RUNTIME_PARENT=$PWD/.test-runtime/hermes .venv/bin/python -B tests/ui_sandbox.py -- tests/test_settings_modal.py -q
```

Result: 20 passed in 29.08s. Shared source explicitly selected so no package install was necessary. Tests used the mandated isolated UI sandbox, not live application services/databases.

`git diff --check` passed in both repositories. Branches remain develop (Herdr) and main (AiHeadDev).

## Earlier diagnostic runs (not claimed as passing)

- Broad initial selection: test_hermes.py, test_catalog.py, test_skills_cli.py, test_ai_head_interactive.py with `-k 'not shell'`, isolated HOME/TMPDIR and --basetemp=$PWD/.test-runtime/hermes/run1. Timed out at 120 seconds after two failure markers. No full failure report was emitted. Legacy launch tests wait for registration (up to 15 seconds each) when mocks omit ready status; the changed binding regression now explicitly mocks readiness. Full interactive suite is NOT claimed passing.
- Narrower same selection without test_ai_head_interactive.py, --basetemp=$PWD/.test-runtime/hermes/run2: 46 passed, 2 deselected, 1 failure. `test_find_repo_root_raises_without_hardcoded_fallback` failed because its temporary path was intentionally inside the Herdr repository, making ancestor discovery succeed. Final contract command places this temporary directory inside the other authorized repo, allowing all skill tests to pass without altering their discovery logic.
- Binding-only run3 timed out after 30 seconds before mock readiness was added.
- Run4 excluded legacy interactive tests and the ancestor-discovery test: 48 passed, 62 deselected, 2 warnings, 0.78s. Superseded by complete final contract selection.
- Initial UI subset before adding catalog regression: 2 passed, 17 deselected in 3.56s. Superseded by all 20 settings tests passing.

No real model inference, external service, database, or live Herdr launch was used for verification. Unit-test Herdr calls were mocked; installer tests used temporary destinations/fake wrapper executables.
