# Handoff: AiHead Herdr Core migration

## Current state

- Legacy Herdr TUI backend remains the default.
- Experimental Core backend: `aihead --backend core --harness pi`.
- `ensure_core_aihead` finds or starts a Core terminal for the project.
- Initial Core-specific prompt is passed in Pi argv.
- `aihead core terminals` lists Core terminals.
- Herdr App cockpit finds labeled AiHead terminals.
- `scripts/herdr-ask.sh` EOF handling and `hc send` exit code fixed and covered by `tests/test_herdr_ask_sh.py`.
- Isolated Core 0.1.21 integration tests (`test_core_herdr_integration.py`, `test_core_child_reporting_integration.py`, `test_core_herdr_ask_integration.py`) pass with `HC_TEST_BINARY=$(command -v hc)` and reliably stop their isolated Core server (`hc server stop`).
- `src/herdr_master/herdr/core_client.py` now provides `terminal_run`, `terminal_send`, and `terminal_kill` RPC helpers (`terminal.send` / `terminal.kill` use `"terminal"` parameter, verified against isolated Core 0.1.21).
- `src/herdr_master/herdr/core_delegation.py` provides `delegate_core_child`, `send_core_child`, and `run_core_one_child_workflow` (requires a verified direct parent via `parent` or `HC_TERMINAL_ID`, labels owned worker with `aihead.role=worker` and `aihead.parent`, waits via `terminal.wait`, no silent retries), covered by unit tests (`tests/test_core_delegation.py`) and isolated Core integration (`tests/test_core_delegation_integration.py` verifying `child.parent == parent_id`, child-to-parent report delivery, and `wait_reason == "exit"`).
- `aihead core work "<objective>"` (`src/herdr_master/cli/main.py` -> `delegate_core_task` in `core_delegation.py`) discovers `.herdr-master.toml` upwards from `--cwd` via `find_project_config_path` (never falling back to caller process cwd) and resolves the worker binding via `resolve_squad(mode="core")` + `worker_pool` + `models.available` allowlist, failing closed on invalid config/model or non-Pi/disabled workers. Spawns non-blocking (`wait=False`) with `hc send <parent_id>` report instructions, verified with fake interactive `pi` (`tests/test_core_delegation_integration.py`, `tests/test_core_delegation.py`).
- Full Ergo/Council/Herdr-Master workflows remain disabled on `--backend core`.
- Verified: Ruff passed, `HC_TEST_BINARY=$(command -v hc) uv run pytest tests/test_core_*.py tests/test_herdr_ask_sh.py` (137 passed), and legacy regressions `tests/test_ai_head_interactive.py tests/test_aihead_entrypoint.py tests/test_unified_aihead.py` (157 passed).

## Important limitations

- Only Pi is supported.
- A successful spawn does not prove the prompt was processed.
- Reusing a head with `--task` is rejected.
- Ergo/Council/Herdr-Master workflows are not yet Core-compatible.
- `delegate_core_child` / `send_core_child` are verified in unit + isolated Core integration tests; wiring them into `herdr-master` / `ergo` CLI dispatch is the next step.

## Next steps

1. Add `aihead core send` (wrapping `send_core_child`) alongside `aihead core delegate` as the smallest coherent end-to-end single-hop Core delegation workflow (`delegate` + `send`), with explicit opt-in boundaries:
   - Scope: `aihead core delegate` + `aihead core send` only; preserves direct-parent identity (`HC_TERMINAL_ID` / `--parent`), no silent retries, and fail-closed validation.
   - Non-goals: does not alter legacy `herdm`/`herdr` TUI workflows or claim full Ergo/Council/Herdr-Master Core compatibility.
2. Add harnesses only after verified interactive startup.
3. Run real isolated Core tests and App window check.

## Useful commands

```bash
aihead --backend core --harness pi
aihead --backend core --harness pi --no-attach
aihead core terminals
```


## Ovis Web UI Inspection (Read-Only)

- **Managed plugin**: `~/.config/herdr/plugins/github/devswha.herdr-web-ui-210f619d6b7b` (`herdr-web-ui` v`0.4.8`).
- **Active release**: `~/.config/herdr-web-ui/updates/d614bddd4ee33545`.
- **Core support**: None (`rg -n "herdr-core|hc "` in `server/` returned no matches); supports legacy `herdr` only.
- **Safe migration path**: Keep live `herdr-web-ui` v`0.4.8` untouched on legacy `herdr` to preserve the current website and running sessions. Develop/verify any `herdr-core` (`hc connect`) adapter in an isolated branch/worktree against an isolated `hc` instance before rollout.


## Skills Core Migration Inventory & Plan

- **`aihead`**: Core-ready for `pi` (`--backend core --harness pi`, `aihead core terminals|delegate|send|work`).
- **`orchestration`**: Host-native subagents only (no Herdr/Core dependency).
- **`herdr-master`**: Legacy-only (fail-closed on `--backend core`). Plan: wire opt-in `--backend core` single-worker dispatch via `delegate_core_task` + `hc send` reporting.
- **`ergo`**: Legacy-only / `--native` (fail-closed on `--backend core`). Plan: wire opt-in `--backend core` Scout/Architect/Coder/Reviewer stages via `delegate_core_task` + `hc wait` + `herdr-ask.sh` Approval Gate.
- **`council`**: Legacy-only (fail-closed on `--backend core`). Plan: wire opt-in `--backend core` member spawn + `hc send`/`hc wait` synthesis.
