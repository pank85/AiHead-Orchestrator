# `/orchestration` — user guide

The same phased pipeline as [`/ergo`](ergo.md), with **no Herdr and no `herdm`**: every role is
spawned by the current harness's own subagent tool.

Scout → Architect → plan you approve → parallel specialist Coders → blind Reviewer → single squash
commit.

- Skill source: [`skills/orchestration/SKILL.md`](../../skills/orchestration/SKILL.md)
- Related: [`/ergo`](ergo.md) · [`/herdr-master`](herdr-master.md)

---

## When to use it

| Use `/orchestration` | Use something else |
| :--- | :--- |
| You want a reviewed, planned multi-file change | One parallel master job → [`/herdr-master`](herdr-master.md) |
| No Herdr installed, or you do not want one | You need a specific model per role across harnesses → [`/ergo`](ergo.md) |
| The work should stay inside this agent and its subagents | A one-line fix → plain agent prompt |

## What it needs

> **Herdr dependency: none.** No `herdr` CLI, no `HERDR_ENV`, no workspace, no `herdm`, no catalog.
> This is the standalone pipeline — if you have Herdr and want cross-harness models per role, use
> [`/ergo`](ergo.md) instead.

Only three things: a shell, a git repository, and a **subagent tool**.

| Harness | Tool used for every role | Read-only handling |
| :--- | :--- | :--- |
| Claude Code | `Agent` | Instructed read-only; the orchestrator saves the reviewer's report |
| Antigravity (`agy`) | `invoke_subagent` | `EnableWriteTools: false`; tier IDs (`flash_lite`, `flash`, `pro`) only |
| Pi (`pi`) | `subagent` | Read-only agent for the reviewer |
| OpenCode (`opencode`) | `subagent` | `agent: "general"`, read-only instruction |

**No subagent tool → the skill stops** and says so. It will not pretend to run the pipeline by
playing all the roles in one context, because the reviewer's value is that it never saw the plan.

## Invoke

```
/orchestration Implement OAuth2 SSO with tests and migration notes
/orchestration --models scout=<model>,architect=<model>,reviewer=<model> "Add rate limiting"
```

Install it like the other skills:

```bash
herdm skills install     # symlinks /orchestration into agy, pi, claude, opencode
herdm skills list        # confirm each endpoint shows Active
```

## Model policy

- Pin models with `--models role=model` (`scout`, `architect`, `coder`, `reviewer`; `coder` may be
  a `|`-separated list). What you pin is what runs.
- Nothing pinned → the model parameter is omitted so each subagent inherits the **host default**.
- The skill never invents a model ID and never substitutes one silently. A rejected parameter is
  reported back to you.
- Effort, when you want it, is a prompt prefix on line 1: `think`, `think hard`, `think harder`,
  `ultrathink`.

## The pipeline

1. **Isolation** — clean tree required; `git worktree add` on `orchestration/<run-id>`; `RUN_DIR`
   created under the worktree. You are asked for `unitTestsMode` (`none` / `new` / `update-only`).
2. **Scout** (read-only) → `RUN_DIR/context.md` with `file:line` evidence, coverage and risks.
3. **Architect** (read-only) → `RUN_DIR/plan.md` with disjoint files, dependencies, a mandatory
   Expert Persona, and a coder model (yours or `host default`).
4. **Plan validation** — missing persona, overlapping files, or an unpinned model → back to the
   Architect.
5. **Approval gate** — the plan is shown to you and it **stops**. No Coder before your explicit
   approval.
6. **Parallel Coders** — all `INDEPENDENT` tasks in one turn, each confined to its files, each
   writing `RUN_DIR/coder_<N>_summary.md`.
7. **Blind Reviewer** — combined diff against the plan: `APPROVED` or `NEEDS FIXES` in
   `RUN_DIR/review.md`, plus `fixes_<N>.md`. Scoped fix loop, capped at 3 cycles. A read-only
   reviewer cannot save files, so the orchestrator persists its returned report instead of
   widening permissions.
8. **Land** — one commit on `orchestration/<run-id>`, sync the target branch (`develop` > `main` >
   `master`) into it, `git merge --squash` in the canonical checkout, then remove the worktree and
   branch. Never `git reset --hard`.

## Running it as a delegated job

`/herdr-master` can hand a job to this skill in its own tab:

```
/herdr-master do this job with orchestration
```

That tab then runs the full pipeline on its own harness's subagents. The child:

- adopts the supplied worktree, branch and `RUN_DIR` instead of creating them;
- stops after **one commit on its assigned branch** — it never touches `develop`;
- never closes, renames or resets a workspace/tab, and never deletes its branch or worktree;
- never starts another master or pipeline;
- reports commit SHA, branch, tests, reviewer verdict, risk and blockers.

The master keeps the final review gate and the squash-merge. All normal gates still apply inside
the child — delegation is not a shortcut.

## Guardrails

- Never skips Scout, the approval gate, or the Reviewer.
- Never codes in the orchestrator; coders never spawn subagents.
- Never invents a model ID; disjoint file ownership is mandatory.
- Read-only roles stay read-only; never `git reset --hard`; never pushes.

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| "no subagent tool" stop | Harness cannot spawn subagents | Use [`/ergo`](ergo.md) in Herdr, or a harness with subagents |
| Everything runs in one context | Subagent tool unavailable and the agent improvised | Check the host table above; re-run after fixing |
| Model parameter rejected | Host does not accept that model on that tool | Report it and pin a supported model, or omit `--models` |
| Reviewer file missing | Read-only reviewer could not write | Expected; the orchestrator persists `review.md` itself |
| Plan sent back repeatedly | Missing persona / overlapping files | Let the Architect fix it — do not hand-edit `plan.md` |

## See also

- [`/ergo`](ergo.md) — the Herdr-hosted version of this pipeline
- [`/herdr-master`](herdr-master.md) — multiple jobs, each in its own tab
