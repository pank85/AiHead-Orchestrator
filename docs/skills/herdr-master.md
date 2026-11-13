# `/herdr-master` — user guide

Root orchestrator for **multiple jobs at once** inside a Herdr workspace. You give it work; it
opens one tab and one isolated git worktree per job, supervises each child, runs a mandatory final
review gate on the accumulated diff, and squash-merges only what passes into `develop`.

- Skill source: [`skills/herdr-master/SKILL.md`](../../skills/herdr-master/SKILL.md)
- Related: [`/ergo`](ergo.md) · [`/orchestration`](orchestration.md)

---

## When to use it

| Use `/herdr-master` | Use something else |
| :--- | :--- |
| Several independent jobs in one session | One large feature → [`/ergo`](ergo.md) or [`/orchestration`](orchestration.md) |
| You want to keep assigning work while agents run | A quick single-file fix → plain agent prompt |
| Cross-harness squad (agy + Pi + Claude + OpenCode) | No Herdr installed → [`/orchestration`](orchestration.md) |

## Prerequisites

> **Herdr is a hard dependency.** This skill does nothing without it: it needs `HERDR_ENV=1`,
> `HERDR_WORKSPACE_ID` and `HERDR_PANE_ID`, i.e. you must be inside a Herdr pane. There is no
> degraded mode and no fallback — for a Herdr-free pipeline use
> [`/orchestration`](orchestration.md).

1. **Herdr** — the skill refuses to run unless those three variables are present. It never creates a
   server or takes over an unrelated workspace.
2. **`herdm`** — the CLI from this repo, used for catalog resolution and skill installation.
3. **A git repository** with a clean working tree. The integration branch is `develop`; if it does
   not exist it is created from the current `HEAD`.

## Install

```bash
# from the checkout
uv run herdm skills install

# or, after installing the package
herdm skills install

# verify each harness endpoint
herdm skills list
```

This symlinks the skill + slash command into Antigravity, Pi, Claude Code, and OpenCode.

## Invoke

Inside a Herdr pane:

```
/herdr-master                       # blank: boots, verifies repo state, asks for the first job
/herdr-master Audit the billing module and propose a refactor plan
```

Or launch it from the CLI, outside or inside Herdr:

```bash
herdm master start                          # waits for interactive delegation
herdm master start --task "Audit billing"   # with an initial job
herdm master start --name backend-lead --harness claude --model <model>
```

Interactive alternative: `herdm tui` opens the cockpit, where **New Master** takes a project
folder and starts a master there.

## What it does with a job

1. **Verify** the repo, target branch, `HEAD`, index and existing worktrees. Ambiguity → a question tab in the coordinator workspace.
2. **Isolate** — new branch and worktree based on `develop`, never reusing or overwriting one.
3. **Dispatch** — one new Herdr workspace per job (`herdr workspace create --no-focus`) with a
   self-contained prompt: objective, constraints, owned files, target branch, acceptance criteria,
   verification. Sub-jobs of that job stay in that job workspace. Questions and updates for the user
   open a new tab in the coordinator workspace; when the user answers, the main session records it,
   closes that tab, and forwards the answer.
4. **Monitor reactively** — one bounded async watcher per child running `herdr agent wait`, no
   polling loops. It ends its turn and resumes when a child finishes or asks something.
5. **Review gate** — checks the full diff, requirements completeness, pattern conformance,
   cross-job semantic conflicts, and logic/security defects. `NEEDS FIXES` goes back to the same
   child; the loop repeats until `APPROVED`.
6. **Land serially** — `git merge --squash <child-branch>` into `develop`, one commit, tests run,
   then the child tabs, job workspace, worktree and temporary branch are removed.
7. **Report** — child SHA, `develop` squash SHA, tests, cleanup, remaining risk.

## Per-job pipelines

Any job can run as a phased pipeline instead of plain work — just say so:

```
/herdr-master do this job with ergo
/herdr-master do this job with orchestration
```

The master still creates the tab and worktree, but prompts the child with `/ergo <task>` or
`/orchestration <task>`, so that tab runs Scout → Architect → parallel Coders → blind Reviewer.
The child stops after its own reviewed commit; **the master alone** runs the final gate and
integrates into `develop`. The plan approval gate is relayed to you either way.

## Guardrails

- Adopts the existing workspace (`owns_workspace = false`) and **never** closes it.
- Never `git reset --hard`, never pushes, never deploys, never deletes user-owned work.
- A dirty or ambiguous `develop`, an unrecognised approval dialog, or a stalled child without
  evidence → stop and ask, preserving everything.
- `idle`/`done` agent state is not proof of completion; it reads the actual output.

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| "must run in a Herdr pane", then stop | `HERDR_ENV` unset | Launch from inside Herdr, or use `/orchestration` |
| Child never starts | Prompt not delivered | Inspect state with `herdr agent get/read` before retrying — do not duplicate the job |
| Review loops | Findings not addressed | Read the findings; they are forwarded verbatim to the child |
| Worktree exists already | Stale run | Remove the stale worktree manually, then re-dispatch |

## See also

- [`/ergo`](ergo.md) — single major job, phased pipeline inside Herdr
- [`/orchestration`](orchestration.md) — same pipeline, no Herdr at all
- [Architecture](../../ai_docs/01_ARCHITECTURE_AND_SPECS.md) · [Orchestration pipeline](../../ai_docs/03_ORCHESTRATION_AND_ERGO_PIPELINE.md)
