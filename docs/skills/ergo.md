# `/ergo` — user guide

Phased pipeline for **one major job**, run inside Herdr as cross-harness agent tabs:
Scout → Architect → parallel specialist Coders → blind Reviewer → single squash commit.

- Skill source: [`skills/ergo/SKILL.md`](../../skills/ergo/SKILL.md)
- Related: [`/herdr-master`](herdr-master.md) · [`/orchestration`](orchestration.md)

---

## When to use it

| Use `/ergo` | Use something else |
| :--- | :--- |
| One large feature or migration, multiple files | Several parallel jobs → [`/herdr-master`](herdr-master.md) |
| You want a different model per role (cross-harness) | No Herdr installed → [`/orchestration`](orchestration.md) |
| You want a plan you approve before code is written | A one-line fix → plain agent prompt |

## Prerequisites

| Dependency | Default mode (Herdr cross-harness) | `--native` mode |
| :--- | :--- | :--- |
| `herdm` CLI | **required** (squad resolution) | **required** (squad resolution) |
| Herdr | **required** — roles launch as Herdr agent tabs | not used |
| git repo with clean tree | required | required |

Without `herdm` the skill stops with an installation error; it never falls back to a hardcoded
model table. If you do not want Herdr **or** `herdm`, use [`/orchestration`](orchestration.md).

The run happens in a `git worktree` on branch `ergo/<run-id>`.

## Invoke

```
/ergo Implement Redis rate limiting middleware
/ergo --models scout=<binding>,architect=<binding>,reviewer=<binding> "Add WebSocket notifications"
/ergo --native "Refactor the invoice calculator"
```

CLI equivalent:

```bash
herdm ergo "Implement Redis rate limiting middleware"
herdm ergo "Add WebSocket notifications" \
  --scout agy:gemini-3.8-flash-medium:medium \
  --architect pi:openai-codex/gpt-6.1-sol:max \
  --workers agy:gemini-3.8-flash,pi:xai/grok-4.7 \
  --reviewer pi:xai/grok-4.7:high
```

Useful flags: `--models` (compact `role:binding` list), `--scout` / `--architect` / `--workers`
(`--coders` alias) / `--reviewer` (individual flags win over `--models`), `--run-squad <path>`
(pinned snapshot), `--label`, `--session`, `--attach/--no-attach`.

## Execution modes

- **Default — Herdr cross-harness.** Every role runs as an interactive Herdr agent tab in one
  workspace. `--herdr` is an explicit alias for this default.
- **`--native` — host subagents.** Roles run through the parent harness's own subagent tool
  (`invoke_subagent` in agy, `subagent` in pi/opencode, `Agent` in claude). Because the product
  squad mixes agy and Pi, `--native` requires compatible role overrides or a run snapshot; an
  incompatible inherited binding is rejected rather than rewritten.

If you want the pipeline with no Herdr at all and no overrides to manage, use
[`/orchestration`](orchestration.md) instead.

## Binding resolution order

Highest priority first — earlier sources win:

1. Inline flags (`--models`, `--scout`, `--architect`, `--workers`, `--reviewer`)
2. Run snapshot (`--run-squad <path>`)
3. Project config (`.herdr-master.toml` in the repo root)
4. Product default squad

Query the authority rather than trusting tables in docs:

```bash
herdm catalog squad --json
herdm catalog agent --mode herdr --binding agy:gemini-3.8-flash-medium:medium --role scout --json
```

Product default squad:

| Role | Binding |
| :--- | :--- |
| Scout | `agy:gemini-3.8-flash-medium` (effort `medium`) |
| Architect | `pi:openai-codex/gpt-6.1-sol:max` |
| Reviewer | `pi:xai/grok-4.7:high` |
| Workers | `agy:gemini-3.8-flash` (mechanical) and `pi:xai/grok-4.7` (complex), effort chosen per task |

## The pipeline

1. **Isolation** — clean tree required, `git worktree add` on `ergo/<run-id>`, `RUN_DIR` created,
   squad resolved and frozen to `RUN_DIR/squad.toml`.
2. **Scout** (read-only) — answers 3–5 targeted questions with `file:line` evidence into
   `RUN_DIR/context.md`.
3. **Architect** (read-only) — writes `RUN_DIR/plan.md`: per task, disjoint files, dependency,
   mandatory Expert Persona, and a coder model from the allowed block.
4. **Plan validation** — the orchestrator rejects any task missing a persona/model or overlapping
   another task's files.
5. **Approval gate** — you see the plan and **must approve**; no Coder starts before that.
6. **Parallel Coders** — one specialist per task, confined to its files, each writing
   `RUN_DIR/coder_<N>_summary.md`.
7. **Blind Reviewer** — a fresh agent reviews the combined diff against the plan and returns
   `APPROVED` or `NEEDS FIXES` in `RUN_DIR/review.md`, with `fixes_<N>.md` per coder. Fix loop is
   scoped and capped (3–5 cycles).
8. **Land** — one commit on `ergo/<run-id>`, sync the target branch (`develop` > `main` >
   `master`) into it, then `git merge --squash` in the canonical checkout. Cleanup follows, and
   `carryover.md` records the outcome.

Everything the run produces is under `RUN_DIR` — inspect those files if a stage looks wrong.

## Guardrails

- Never skips Scout, the approval gate, or the Reviewer.
- Never codes in the orchestrator; coders never spawn subagents.
- Never invents a model: every role gets an explicit binding from the allowed list.
- Never `git reset --hard`; conflicts are resolved by a Coder and re-checked by the Reviewer.
- Never pushes and never opens a PR unless asked.

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| "actionable installation prerequisite error" | `herdm` not found | Install/activate the package, then re-run |
| Binding rejected in `--native` | Product squad mixes harnesses | Pass compatible `--scout/--architect/--reviewer/--workers` or use default Herdr mode |
| Plan sent back | Missing persona or overlapping files | Let the Architect fix it — do not hand-edit `plan.md` |
| Fix loop caps out | Critical findings unresolved | Worktree and branch are preserved; inspect `review.md` |
| Dirty tree refusal | Uncommitted changes | Commit or stash, then re-run |

## See also

- [`/orchestration`](orchestration.md) — same pipeline on host subagents only
- [`/herdr-master`](herdr-master.md) — many jobs, one worktree each
- [Orchestration pipeline spec](../../ai_docs/03_ORCHESTRATION_AND_ERGO_PIPELINE.md)
