# Settings skipped on some launch paths

These keys are not unused. Other commands do read them. This file only lists launch paths that save or load the key and then do not apply it. Do not treat a row here as "the setting does nothing."

Project file `/tmp/aihead-settings-docs/.herdr-master.toml` also shadows the home file for any omitted section. See `docs/settings-unused.md`. From this tree, home `orchestration.agy_mode = "swarm"`, `orchestration.worker_mode = "inherit"`, `ai_head.automode = true`, and `ai_head.routing_profile = "frugal"` are not the values `load_project_config` returns.

## `ai_head.automode`

Applied when an AiHead prompt is built:

- `launch_ai_head` reads `head_config.automode` unless the CLI passed an override (`src/herdr_master/pipeline/launcher.py:1355`).
- `ensure_core_aihead` does the same (`src/herdr_master/herdr/core_aihead.py:98`).
- AiHead discovery and reconcile pass `head_config.automode` into `build_interactive_ai_head_prompt` (`src/herdr_master/pipeline/ai_head.py:1627`, `1673`, `1745`).

Not applied:

- `launch_master` (`src/herdr_master/pipeline/launcher.py:788`) has no automode argument and does not read `ai_head.automode`.
- `launch_ergo` (`src/herdr_master/pipeline/launcher.py:875`) does not read it.
- `launch_orchestration` (`src/herdr_master/pipeline/launcher.py:970`) does not read it.
- `launch_council` (`src/herdr_master/pipeline/launcher.py:1163`) does not read it.
- `master_start`, `run_ergo`, and `run_orchestration` in `src/herdr_master/cli/main.py` call those launchers without passing automode.
- `_launch_skill` (`src/herdr_master/cli/main.py:1746`) starts ergo, council, orchestration, or master and never reads `ai_head.automode`. The plugin pane command `aihead --herdr-master` uses that master path.

## `orchestration.worker_mode` and `ergo.worker_mode`

Applied only inside `resolve_squad` (`src/herdr_master/pipeline/squad.py:760`): native mode reads `orchestration.worker_mode`; Herdr mode reads `ergo.worker_mode` when `ergo` is present; otherwise it falls through to `ai_head.routing_profile`. `run_ergo` reaches this through `_resolve_ergo_once`.

Not applied:

- `run_orchestration` loads `project_cfg.orchestration` and passes harness plus role models to `launch_orchestration`. It never reads `worker_mode`.
- `_launch_skill` orchestration branch does the same (`src/herdr_master/cli/main.py:1805` loads `orchestration`, then `launch_orchestration` at `src/herdr_master/cli/main.py:1808` with role models only).
- `launch_orchestration` has no `worker_mode` parameter, so a saved mode cannot change that pane's coder list.
- `launch_master` and `launch_council` do not read either worker mode.

## `orchestration.agy_mode`

Applied by `resolve_squad` (`src/herdr_master/pipeline/squad.py:763`) and by triage (`src/herdr_master/pipeline/triage.py:784`) when those functions run. The settings TUI also displays it.

Not applied:

- `run_orchestration` and `_launch_skill` never read `agy_mode`. They pass `orchestration.harness` (or the CLI harness) to `launch_orchestration`.
- `launch_orchestration` does not branch on `subagents` versus `swarm`.
- `launch_ai_head` does not put `agy_mode` in the AiHead prompt (`src/herdr_master/pipeline/ai_head.py` has no `agy_mode` reference).

## `ai_head.routing_profile`

Applied as the triage profile when `--profile` is omitted (`src/herdr_master/pipeline/triage.py:783`) and as the worker-mode fallback inside `resolve_squad` (`src/herdr_master/pipeline/squad.py:762`). Catalog commands can also read it.

Not applied:

- `build_interactive_ai_head_prompt` / `_format_automode_block` do not mention `routing_profile`. Interactive and Core AiHead launches therefore do not inject the saved profile into the prompt.
- `launch_master`, `launch_ergo`, `launch_orchestration`, and `launch_council` do not read it.
- `run_orchestration` does not pass it into the orchestration pane.

## `orchestration.default_harness`

This one is unused on every launch path, not only some of them. It is listed in `docs/settings-unused.md`. Launches that look like they should honor a default harness read `orchestration.harness` instead.
