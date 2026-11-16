# Coordinator bindings — resolution order, code paths, verification

Depth behind the "Coordinator bindings" section of SKILL.md. Applies to the
`herdr-master` / AiHead-Orchestrator codebase that provides `aihead`.

## Where the code lives

The launchers are installed **editable** into a separate venv, so edits in the repo take
effect immediately and `grep` in the repo is searching the running code:

```bash
head -1 "$(command -v aihead)"        # shebang names the venv interpreter
/home/pank/projects/AiHead-Cockpit/.venv/bin/python3 -c \
  "import herdr_master, os; print(os.path.dirname(herdr_master.__file__))"
```

If the printed path is the project's `src/herdr_master`, the repo is the live source.

## Resolution order (highest wins)

1. Inline CLI override — `--coordinator <harness:model[:effort]>` (`aihead ergo`),
   `--models/--scout/--architect/--workers/--reviewer` for squad roles.
2. Run snapshot — `--run-squad <path>` (pinned bindings for one run).
3. Project `.herdr-master.toml` — found by searching upward from the cwd, so the
   file that governs a launch depends on where the command runs.
4. Product catalog defaults (`aihead catalog squad --json` / the catalog's own binding).

Never maintain a hand-written fallback table; query the catalog.

## Code paths

| Concern | Location |
| --- | --- |
| Ergo pane host binding | `pipeline/launcher.py` → `launch_ergo()`; host = coordinator binding, fallback `squad.scout` |
| Coordinator normalization | `pipeline/launcher.py` → `resolve_coordinator_binding()` (raises rather than inventing a model for a harness with no product binding) |
| Master coordinator | `pipeline/launcher.py` → `resolve_master_binding()` (config `[master_coordinator]`) |
| CLI entry points | `cli/main.py` → `run_ergo()` (`aihead ergo`) and `_launch_skill()` (`aihead --ergo` delegation) |
| Per-skill wizard | `cli/aihead_setup.py` → `run_skill_setup()` (prompts per skill, validates, saves once) and `run_aihead_setup()` (squad roles only) |
| Config models | `core/config.py` → `HerdrMasterConfig`, `CouncilConfig`, `AiHeadConfig`, `master_coordinator` |

`HarnessBinding` values are written either as a slug string or as a table. String form needs a
`model_validator(mode="before")` on the section model that calls
`HarnessBinding.from_slug(...)`, mirroring `CouncilConfig._coerce_fields`; without it the
plain TOML string fails validation.

Example binding string alternatives (configured separately; do not duplicate keys within a single TOML section):

```toml
# Pi coordinator with explicit provider, model, and reasoning effort
coordinator = "pi:openai/gpt-6.1-sol:high"
```

```toml
# Codex coordinator (conditional on active catalog/allowlist support; no effort flag)
coordinator = "codex:gpt-6.1-sol"
```

```toml
# Kilo coordinator (conditional on active catalog/allowlist support; no effort flag)
coordinator = "kilo:opencode/gpt-6.1-terra"
```

*Note on Codex and Kilo bindings:* `codex` and `kilo` entries are conditional on active catalog and allowlist support in the runtime environment. Neither harness defines an invented default model, interactive effort flag, native adapter, or installed-skill path.

Keep `HerdrMasterConfig` additions `Optional` so an unset section is dropped by
`save_project_config()`'s `model_dump(exclude_none=True)` instead of writing an empty table.

## Adding a new binding key — checklist

1. Config model + string coercion; field on `HerdrMasterConfig`.
2. Resolver helper next to `resolve_master_binding` returning `None` when unset, so the
   launcher falls back to the previous binding and default behavior does not change.
3. Wire **every** call site of the launcher (`run_ergo` *and* `_launch_skill`); a new flag
   alone leaves the delegation path on the old binding.
4. Wizard prompt with an "empty = same as <fallback>" default; raise the wizard's own
   `SetupError` so the atomic validate-then-save path stays intact.
5. Tests: launcher host choice with and without the binding, resolver override-wins, config
   round-trip, CLI forwarding, wizard write.

## Verification in this repo

```bash
cd /home/pank/projects/AiHead-Orchestrator
/home/pank/projects/AiHead-Cockpit/.venv/bin/python3 -m pytest tests/ -q
/home/pank/projects/AiHead-Cockpit/.venv/bin/python3 -m ruff check src tests
```

Use the venv interpreter directly, **not** `uv run`: `uv run` re-resolves and rewrites
`uv.lock`, which pollutes the change under review. Check `git status` first — the tree may
already carry unrelated uncommitted edits (`uv.lock`, skill markdown), so when delegating
implementation forbid `git commit/add/stash/checkout` in the prompt and review the diff
before landing anything.
