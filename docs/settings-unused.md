# UNUSED settings

This file lists settings that are saved and loaded, then never consulted to change launch or runtime behavior. It is not a catalog of settings that work. Working settings are omitted on purpose.

Verified against `src/herdr_master/core/config.py`, `src/herdr_master/pipeline/launcher.py`, `src/herdr_master/cli/main.py`, and the two config files:

- project: `/tmp/aihead-settings-docs/.herdr-master.toml`
- home: `/home/pank/.herdr-master.toml`

No runtime code was changed.

## Why home values disappear when this project file exists

`find_project_config_path` (`src/herdr_master/core/config.py:1222`) returns the nearest `.herdr-master.toml`. `load_project_config` (`src/herdr_master/core/config.py:1338`) validates that file alone. Ancestor merge in `load_effective_models_state` (`src/herdr_master/core/config.py:1269`) copies only `models.available`, `models.ratings`, and `models.evaluation`.

This project's file omits `[general]`, `[worktree]`, `[models]`, `[tui]`, `[projects]`, `[orchestration]`, `[council]`, `[master_coordinator]`, and `[ergo]`. Those omitted sections become model defaults. They do not inherit the home file. Home values in those sections are therefore skipped for any launch that starts in this tree.

`[ai_head]` is present in the project file, so home `ai_head` keys that the project file omits also become model defaults, not the home values. Examples: home `automode = true` and `routing_profile = "frugal"` are not seen; the loaded values are `automode = false` and `routing_profile = "balanced"`.

## Never consulted

Each row is a key that can be written to `.herdr-master.toml` and round-tripped by `save_project_config`. Nothing outside schema validation, the settings screen save path, or tests reads it to choose a branch, path, model, or prompt.

| Config key | Saved in | Code path that skips it |
| --- | --- | --- |
| `general.default_session` | both files, `"default"` | No reader under `src/`. Session selection uses the Herdr context / `--session`, not this key. `load_project_config` keeps the field and never passes it on. |
| `general.target_integration_branch` | both files, `"develop"` | Defined only at `src/herdr_master/core/config.py:53`. No launch or git path reads `config.general`. |
| `general.create_develop_if_missing` | both files, `true` | Defined only at `src/herdr_master/core/config.py:54`. No caller. |
| `general.disallow_hard_reset` | both files, `true` | Defined only at `src/herdr_master/core/config.py:55`. No caller. |
| `worktree.base_directory` | both files, `".herdr-worktrees"` | Defined only at `src/herdr_master/core/config.py:61`. Worktree creation does not read `config.worktree`. |
| `worktree.naming_template` | both files, `"herdm/{job_name}-{timestamp}"` | Defined only at `src/herdr_master/core/config.py:62`. No caller. |
| `worktree.auto_clean_on_success` | both files, `true` | Defined only at `src/herdr_master/core/config.py:63`. No caller. |
| `tui.refresh_rate_ms` | both files, `250` | Defined only at `src/herdr_master/core/config.py:97`. The settings TUI does not read `config.tui`. |
| `tui.theme` | both files, `"dark"` | Defined only at `src/herdr_master/core/config.py:98`. No caller. |
| `tui.stream_buffer_lines` | both files, `1000` | Defined only at `src/herdr_master/core/config.py:99`. No caller. |
| `projects.favorites` | home file, `[]`; project file omits the section | Validated in `ProjectsConfig._validate_unique_favorites` (`src/herdr_master/core/config.py:146`). No menu, launch, or folder picker reads the list. |
| `projects.default_folder` | home file omits it (default `None`); project file omits the section | The only consumer is `resolve_effective_folder` (`src/herdr_master/core/config.py:1455`). That function has no caller under `src/`. Launch cwd uses `ai_head.cwd` or the process cwd (`src/herdr_master/pipeline/launcher.py:1322`), not this key. |
| `orchestration.default_harness` | home file, `"agy"`; project file omits `[orchestration]`, so the loaded value is the model default (`agy`, copied from `harness`) | `OrchestrationConfig.effective_default_harness` (`src/herdr_master/core/config.py:475`) has no caller. Saves write it equal to `harness` (`src/herdr_master/cli/setup_tui.py:1704`, `src/herdr_master/cli/aihead_setup.py:89`) and never read it back for a launch. `run_orchestration` uses `orch_cfg.harness` (`src/herdr_master/cli/main.py:607`). `_launch_skill` uses `harness or orch.harness` (`src/herdr_master/cli/main.py:1808`). |

`[general]`, `[worktree]`, and `[tui]` have no consulted keys. `[projects]` has no consulted key either: `default_folder` is dead because its only helper is never called.

## Not unused

These were checked and do change behavior, so they are not in the table above:

- `ai_head.enabled`, `soul`, `binding` (kind/model/effort), `mode`, `split`, `cwd`, `lesson_paths`, `brief_before_action`, `autonomy`, `close_foreign_tabs`, `dangerously_skip_permissions`
- `ai_head.automode` on AiHead prompt launch (`launch_ai_head`, `ensure_core_aihead`, AiHead discovery). It is still skipped on other launch commands; that list is in `docs/settings-skipped-on-launch.md`.
- `ai_head.routing_profile`, `orchestration.agy_mode`, `orchestration.worker_mode`, and `ergo.worker_mode` on squad resolution, triage, or catalog paths. Same skipped-path file.
- `orchestration.harness` and the role model/effort fields on `aihead orchestration`
- `models.*` explicit squad fields, and ancestor inheritance of `models.available`, `models.ratings`, and `models.evaluation`
- `council.*`, `master_coordinator`, and `ergo.coordinator` on their own launch commands
