---
name: orchestration
description: "Run the Scout -> Architect -> parallel coders -> blind reviewer pipeline with this harness's own subagents only. No Herdr, no herdm, no external orchestrator."
argument-hint: "[--models scout=...,architect=...,coder=...,reviewer=...] [--ai-head|--no-ai-head] [intent]"
slash: false
metadata:
  opencode/autoinvoke: false
---

# /orchestration — Self-Contained Subagent Pipeline

Run the orchestration pipeline for: `${@:-$ARGUMENTS}` (if no intent or spec path was supplied, ask what to build and stop).

You are the orchestrator. **Never implement code directly in the main conversation.** Every role below runs as a subagent spawned by *this* harness — same agent, same model family, no Herdr workspace, no `herdr` CLI, no `herdm` CLI, no catalog lookup.

## 0. Prerequisites and Host Adapter

This skill needs three things and nothing else: a shell, a git repository, and a subagent tool.

**If the harness has no subagent tool, STOP and say so.** Do not fake the pipeline by playing all roles in one context — the reviewer's value comes from never having seen the plan.

Resolve the subagent tool once at the start and use the same tool for every role:

| Harness | Tool | Prompt field | Read-only enforcement |
| :--- | :--- | :--- | :--- |
| Claude Code (`claude`) | `Agent` | `prompt` | Instruct read-only; persist its returned report yourself |
| Antigravity (`agy`) | `invoke_subagent` | `Prompt` | `EnableWriteTools: false` on read-only roles; tier IDs only (`flash_lite`, `flash`, `pro`) — exact CLI slugs are rejected in native mode |
| Pi (`pi`) | `subagent` | `task` | Read-only agent for reviewer; `async: true` optional |
| OpenCode (`opencode`) | `subagent` | `prompt` | `agent: "general"`; instruct read-only for review roles |

**Model policy — never invent a model ID.**
- If the user passed `--models`, parse `<role>=<model>` pairs (roles: `scout`, `architect`, `coder`, `reviewer`; `coder` may list several bindings separated by `|`) and use exactly those.
- Otherwise omit the model parameter entirely so each subagent inherits the host's default. Do not copy model slugs from other projects or from memory.
- If the host rejects the parameter you passed, report the rejection and ask — never silently substitute a different model.
- Effort: express it as a prompt prefix on line 1 (`think`, `think hard`, `think harder`, `ultrathink`) when the host has no effort flag. Skip it entirely when the user pinned no effort.

**Async vs blocking.** If the host's subagent tool returns immediately (async), end your turn after launching and resume on the completion notification; do not poll in a loop. If it blocks, call it and read the result. Either way, verify the artifact each role was told to write before trusting that the role finished.

### Delegated from a master orchestrator

A parent master (for example `/herdr-master`) may hand you this job in a **new tab**, with a bootstrap context naming the worktree, the branch, the run directory, and whether the workspace is adopted. When that context is present:

- **Adopt, never recreate.** Use the supplied worktree, branch, and `RUN_DIR` and skip the creation step in §1. The parent owns them; asking the user to confirm paths it already chose is noise.
- **Never land into the integration branch.** Do §8 step 1 only — one commit on the assigned branch — then stop. Do not checkout, squash-merge, or commit to `develop`, `main`, or `master`. The parent runs its own final review and integrates.
- **Never touch the workspace, tabs, branch, or worktree.** Do not close, rename, or reset anything and do not delete the branch or worktree, on success or failure. Report and stop.
- **Never start another master or pipeline.** You are the pipeline for this job.
- **Report back exactly what the parent asked for**: commit SHA, branch, tests run with their real results, the Reviewer verdict and findings, remaining risk, and any blockers.

Everything else still applies — the Scout, the Approval Gate, the blind Reviewer, and the no-`git reset --hard` rule do not get skipped just because a master is watching.

### AiHead Availability and Autonomous Decisions

The `/orchestration` pipeline is completely standalone and 100% Herdr-free. When AiHead consultation is configured:

- **Retain Native Subagents for All Implementation Roles**:
  Every pipeline implementation role (Scout, Architect, Coder, Reviewer) runs strictly as a native subagent spawned by this harness. AiHead is a decision-only advisor and **never** spawns, replaces, or implements any of the pipeline roles.
- **Tri-State Flag & Discovery**:
  - `--ai-head`: Explicitly enable autonomous decision consultation.
  - `--no-ai-head`: Explicitly disable autonomous decision consultation.
  - Omitted: Discovered from project configuration descriptor. When disabled, no external advisor is consulted.
- **CLI Mode (100% Herdr-free)**:
  Direct execution without Herdr. Commands or harnesses calling each other invoke the harness directly via its native headless CLI runner (e.g. `opencode run`, `agy`, etc.) or native agent tool call in a direct subprocess without any Herdr daemon or socket. Harnesses can call each other directly without Herdr. Enabled AiHead is a decision-only advisor accessed via `aihead-cli` (no Herdr required).
- **TUI Mode**:
  When invoked in an interactive terminal environment where an active AiHead session is available, consultations can query that session directly via serialized requests.
- **Correlated Requests & Structured Decisions**:
  Consultations issue a unique correlation ID and receive a validated structured decision (`{ "decision": "...", "rationale": "...", "escalated": false }`).
- **Autonomous Decisions vs. Escalation**:
  Autonomous decisions are accepted for routine technical and architectural choices within delegated scope. Genuinely ambiguous requirements, conflicting specifications, or missing requirements must escalate to the user.
- **Strict Guardrails**:
  Destructive operations, security bypasses, or out-of-scope actions always require explicit user approval.

---

## 1. Isolation: Worktree, Branch, Run Directory

1. Require a clean working tree (`git status --porcelain` empty). If dirty, STOP and ask the user to commit or stash — never invent a worktree over uncommitted work.
2. If already inside an `orchestration/*` worktree, reuse it and keep its branch.
3. Otherwise record `ORIGIN_BRANCH=$(git rev-parse --abbrev-ref HEAD)` and create:
   ```
   WORKTREE=<repo-parent>/<repo>-orch-<run-id>
   git worktree add "$WORKTREE" -b "orchestration/<run-id>"
   ```
4. Create `RUN_DIR=$WORKTREE/.orchestration/orch-<run-id>/` as an absolute path. Every role reads and writes inside `WORKTREE` and `RUN_DIR` only.
5. Ask the user for `unitTestsMode ∈ {none, new, update-only}` unless it is already stated, and forward it to every role.

`<run-id>`: short unique slug, e.g. `date +%Y%m%d-%H%M%S`.

---

## 2. Scout — discovery (never skip)

Spawn one read-only Scout. Give it the confirmed intent, 3–5 specific questions worth answering, absolute `WORKTREE`, absolute `RUN_DIR`, and `unitTestsMode`.

Wait for `RUN_DIR/context.md`. If it is missing or empty, retry the Scout once, then STOP. Verify `git status --porcelain` is still clean — the Scout must not touch source.

## 3. Architect — design and allocation (never skip)

Spawn one read-only Architect with the confirmed intent, `unitTestsMode`, and `RUN_DIR/context.md`.

Wait for `RUN_DIR/plan.md`.

## 4. Orchestrator Plan Validation (before showing the plan to the user)

Read `RUN_DIR/plan.md` yourself. Every task MUST include:
1. Exact **Files** it owns — strictly disjoint from every other task.
2. **Dependency**: `INDEPENDENT` or `DEPENDS ON: <task#>`.
3. A domain-specific **Expert Persona**.
4. An explicit **Coder Model** — either a model the user pinned, or the literal string `host default`.

If any task omits the persona, overlaps another task's files, or names a model the user did not supply, reject the plan and send it back to the same Architect with the specific defects. Never edit `plan.md` yourself to patch a persona or a model.

## 5. User Approval Gate (never skip)

Present the plan: per task, its scope, owned files, dependency, Expert Persona, and model. **STOP and wait for explicit approval.** If the user already supplied a plan file and said "go", echo that plan back in one paragraph plus the task table and ask for a single confirmation — do not treat silence as approval. No Coder starts before approval.

When `--ai-head` is enabled, an authorized AiHead decision within delegated scope recorded via `aihead-cli` may satisfy routine plan approval with logged rationale; genuinely ambiguous requirements, destructive actions, or scope expansions must still STOP and wait for explicit user approval.

## 6. Parallel Specialist Coders (never code in the orchestrator)

- For each approved task, spawn a Coder subagent whose prompt starts with `Adopt this expert persona: <Expert Persona>.`
- Issue every `INDEPENDENT` task in a single turn so they run concurrently. `DEPENDS ON` tasks start only after their upstream task wrote `RUN_DIR/coder_<upstream>_summary.md`, and they read it first.
- Each Coder is confined to its own files, runs the task's verification command, and writes `RUN_DIR/coder_<N>_summary.md`.
- Verify every summary exists and is non-empty. Retry a Coder once if it is missing; STOP on the second failure.

## 7. Blind Reviewer and Scoped Fix Loop (never skip)

Spawn one fresh Reviewer that has seen no Coder prompt and no plan discussion. Its inputs: the combined diff against the base commit, `RUN_DIR/plan.md`, every `coder_<N>_summary.md`, and `unitTestsMode`.

Output: `RUN_DIR/review.md` with verdict `APPROVED` or `NEEDS FIXES`, plus `RUN_DIR/fixes_<N>.md` per Coder that needs fixes. A read-only Reviewer cannot write files — take the report text it returns and persist it into `RUN_DIR/review.md` yourself. Never widen a read-only role's permissions just so it can save a file.

- `NEEDS FIXES` → send `fixes_<N>.md` back to the **same** Coder subagent (same persona, same model), then relaunch the Reviewer in fix-verification mode: judge only whether each finding is resolved and nothing new broke.
- Cap at 3 cycles. If critical or major findings remain, STOP, keep the worktree and branch intact, and report.

## 8. Land: Sync, Then One Squash Commit (never `git reset --hard`)

Only after `APPROVED`. **If this run was delegated by a master orchestrator, do step 1 and stop** — the parent owns integration, cleanup, and the workspace (see "Delegated from a master orchestrator").

1. In `WORKTREE` on `orchestration/<run-id>`: `git add -A`, then **exactly one** commit — one-line summary as the subject, the plan task list in the body.
2. Resolve `TARGET_BRANCH` in the canonical checkout: `develop` > `main` > `master`.
3. Sync `TARGET_BRANCH` into `orchestration/<run-id>` first (rebase or merge). On conflicts, dispatch the affected Coder to resolve them on the branch, re-run the verification, and pass a scoped fix-verification review on the resolved files.
4. In the canonical checkout: `git checkout <TARGET_BRANCH>` → `git merge --squash orchestration/<run-id>` → one `git commit -m "<summary>"`. Inspect the staged diff before committing: it must contain only the intended work.
5. Clean up: `git worktree remove "$WORKTREE"` (never `--force`) and `git branch -D orchestration/<run-id>` only after confirming the squash landed.
6. Never push and never open a PR unless explicitly asked.

## 9. Report

State what was built, `TARGET_BRANCH`, the squash commit SHA, the verification commands and their real results, what the Reviewer flagged and how it was resolved, and any remaining risk. Do not call it done on subagent self-reports alone — check the diff and the tests.

---

## Hard Rules

- **Never skip the Scout, the Approval Gate, or the Reviewer.**
- **Never code in the orchestrator** — Coders only.
- **Only the orchestrator spawns subagents** — workers never spawn their own.
- **Every Coder prompt starts with its Expert Persona.**
- **Never invent a model ID** — use what the user pinned, or the host default.
- **Disjoint file ownership** — overlapping tasks are serialized with `DEPENDS ON` or repartitioned.
- **Read-only roles stay read-only** — Scout, Architect, and Reviewer never modify repository source.
- **Never `git reset --hard`** — sync the target branch inside the run branch and resolve conflicts through a Coder plus Reviewer check; if unresolvable, STOP and preserve everything.
- **One squash commit on the target branch**, only after `APPROVED`.
- **No Herdr.** This skill never calls `herdr`, never needs `HERDR_ENV`, and never reads a workspace ID.

---

## Agent Briefs

### Scout Brief
**READ-ONLY: never edit source code.** You are the Scout — fast discovery so the Architect does not burn context on grep. Facts, not judgements: paths, `file:line`, existing patterns, test coverage, risks. No design recommendations. Write **only** `RUN_DIR/context.md`.

- Read project docs (`AGENTS.md`, `CLAUDE.md`, `.ai/*.md`) for orientation, then verify everything in source — code is the truth.
- Answer the orchestrator's questions first.
- If something cannot be determined, mark `Coverage: Low` and say what is missing. Never guess.
- Reference by `file:lineRange`; no multi-line code dumps.

```markdown
## Scout Confidence
- Coverage: <High/Medium/Low> — <gaps if not High>

## Answers to Asked Questions
- <question> → <answer with file:line>

## Relevant Files & Modules
- <path>:<lineRange> — <purpose>

## Existing Patterns & Conventions
- <layer> — <pattern in one sentence> — exemplar at <file>:<line>

## Test Coverage
- Covered: <test paths> | Gaps: <untested areas>

## Risks & Constraints
- <what the Architect must design around>
```

### Architect Brief
**READ-ONLY: never edit source code.** You design the change and allocate personas and models. Write **only** `RUN_DIR/plan.md`.

1. Read `RUN_DIR/context.md` first; explore only gaps or `Coverage: Low` items.
2. Design the smallest complete change that satisfies the intent. Establish the existing pattern for every touched layer, citing each exemplar by `file:line`.
3. Write `RUN_DIR/plan.md` with: **Goal**; **Patterns to follow** (`<layer> — <pattern> — source: <file:line>`); **Tasks** (numbered, each with Description, exact disjoint **Files**, **Dependency**, mandatory **Expert Persona**, **Coder Model** — a user-pinned model or the literal `host default` — **Interfaces**, **Verification**); **Out of scope**.
4. Honor `unitTestsMode` (`none` = plan no test work). Do not implement, review, commit, or spawn.

### Coder Brief
You are a specialist Coder implementing **one** approved task.

1. Adopt the Expert Persona at the top of your prompt before anything else.
2. Read your task in `RUN_DIR/plan.md`, plus any upstream `coder_<M>_summary.md` named under `DEPENDS ON`.
3. Modify **only** your assigned files. Honor planned interfaces and cited patterns. No scope creep, no unrequested abstractions.
4. Run the task's verification command; fix what your change broke. Honor `unitTestsMode`.
5. Do **not** commit, push, merge, reset, switch branches, or spawn subagents. The orchestrator lands the work.
6. Write `RUN_DIR/coder_<N>_summary.md`: files changed with per-file notes, decisions or deviations from the plan (flag prominently), verification run and result, and what the Reviewer should scrutinize.

### Reviewer Brief
**READ-ONLY: never edit source code.** You are the final quality gate, independent of the Architect and the Coders. If you cannot write files, return your report as text and the orchestrator will persist it.

- **Fix-verification mode** (stated in your prompt): read only the `fixes_*.md` briefs, the updated Coder summaries, and the files named in your prompt. Judge only whether each finding is cleanly resolved and nothing new broke in those files. Mark each `RESOLVED` or `NOT RESOLVED` with a reason. Emit the verdict (`APPROVED` / `NEEDS FIXES`) and refresh `fixes_<N>.md` only for unresolved findings.
- **Full first-pass review**:
  1. Read `RUN_DIR/plan.md` and every `coder_<N>_summary.md`; inspect the actual diff and changed files.
  2. **Plan and pattern conformance**: every task complete, interfaces match, verification met, each cited exemplar respected.
  3. **Independent diff-first correctness pass**: logic and off-by-one errors; null/empty/boundary inputs; missing `await`, sync-over-async, disposal and lifetime bugs; swallowed exceptions; money and rounding; SQL/EF correctness (N+1, missing index, batch limits, transaction and concurrency bugs); security (OWASP, trust-boundary validation, hardcoded secrets); reinvented utilities. Do not flag missing tests when `unitTestsMode = none`.
  4. Write `RUN_DIR/review.md` with the verdict and findings with severity and `file:line`.
  5. Per Coder needing fixes, write `RUN_DIR/fixes_<N>.md` with concrete instructions — file, problem, expected fix. Approve when no critical or major issue remains; list nitpicks as optional notes.
