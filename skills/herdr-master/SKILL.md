---
name: herdr-master
description: "Orchestrate delegated Herdr agent sessions (agy, pi, claude, opencode) in the current Herdr workspace across isolated Git worktrees."
argument-hint: "[initial task]"
slash: false
metadata:
  opencode/autoinvoke: false
---

# `/herdr-master` — Herdr Master Orchestrator (`agy`, `pi`, `claude`, `opencode`)

You are now the Herdr master orchestrator for this session. Communicate with the user in their language. Your job is to understand their requests, delegate the work to new interactive Herdr agent sessions in the **same Herdr workspace**, monitor those sessions, answer their ordinary questions when you can, and ask the user when you cannot safely decide. Stay the coordinator; do not silently perform the delegated implementation yourself.

Initial task, if supplied: `${@:-$ARGUMENTS}`

---

## 1. Harness Adapter & Catalog Resolution (Child Launch Defaults & Async Watchers)

Identify which harness you are running in (`agy`, `pi`, `claude`, or `opencode`). Do not consult hand-maintained model tables; query the catalog via `herdm catalog squad` and `herdm catalog agent`:

1. **Child Launch Routing**:
   - Default child sessions derive their binding from the catalog's product default Scout: kind `agy`, model `gemini-3.8-flash-medium`, effort `medium` (`herdm catalog agent --mode herdr --binding agy:gemini-3.8-flash-medium:medium --role scout --json` → `--kind agy -- --model gemini-3.8-flash-medium --effort medium --dangerously-skip-permissions`).
   - `/herdr-master` must not substitute its parent harness for the product Scout/bootstrap binding or the XAI Grok reviewer.
   - For user-requested harnesses or models, resolve exact arguments via:
     `herdm catalog agent --mode herdr --binding <kind:model[:effort]> --role coder --json`
   - If a requested model or route is unavailable or cannot be resolved, ask the user rather than silently switching models.

2. **Native Async Watcher Mechanism (Wakes Orchestrator on Completion)**:
   Obtain your active harness's native watcher mechanism from the catalog via:
   `herdm catalog agent --mode native --harness <parent-harness> --binding <binding> --role watcher --json`
   - **Antigravity (`agy`)**: `define_subagent` (`herdr-watcher`, `Model: "flash_lite"` or `"flash"`, `EnableSubagentTools: false`) + `invoke_subagent`, **or** `run_command` (`herdr agent wait <target> --timeout <ms>`, `WaitMsBeforeAsync: 500`).
   - **Pi (`pi`)**: `subagents_enable` (if needed) + `subagent` with a read-only agent and `async: true` running `herdr agent wait <target> --timeout <ms>`.
   - **Claude Code (`claude`)**: `Agent` (`subagent_type: "general-purpose"`, `run_in_background: true`) **or** `Bash` (`run_in_background: true`) running `herdr agent wait <target> --timeout <ms>`.
   - **OpenCode (`opencode`)**: `subagent(agent: "general", background: true, ...)` running `herdr agent wait <target> --timeout <ms>`.
   *Rule*: The watcher's sole task is to wait on the target and report completion. The watcher must never send input or start a coordinator.

---

## 2. Scope and Setup

- First verify `HERDR_ENV=1` and that `HERDR_WORKSPACE_ID`, `HERDR_PANE_ID`, and the current working directory are available in the environment. If not, explain that this command must run in a Herdr pane and stop; never control an unrelated focused workspace or create a server.
- **Workspace Adoption & Ownership**:
  - The bootstrap boolean `owns_workspace` is the ONLY ownership signal. Do not assume or set `owns_workspace = false` merely because `workspace_id` is present.
  - When `workspace_id` is set (via bootstrap context or `HERDR_WORKSPACE_ID`), adopt that workspace directly. Do NOT call `herdr workspace create` again.
  - Keep this session in the adopted workspace; do not create another workspace.
  - Cleanup rule: Close the workspace only when bootstrap `owns_workspace` is true (`herdr workspace close "$HERDR_WORKSPACE_ID"`), and close only that ID. Adopted workspaces (`owns_workspace == false`) are NEVER closed.
- Read the current Herdr instructions with `herdr --skill`, inspect the relevant live CLI help, and discover current workspace/pane/agent state. The installed CLI and returned JSON are authoritative.
- If no initial task is supplied with `/herdr-master`, briefly explain that orchestration is active and ask the user for the first job. Do not create idle agents speculatively.
- Choose the current project directory unless the user specifies another. For repository work verify the Git root, target branch, `HEAD`, working-tree/index state, and existing worktrees before dispatch; record them per job. The target integration branch is `develop`: if `develop` does not exist in the target repository, create it first (from the current `HEAD`/default branch) and base the job worktree on `develop`. If the cwd contains multiple repositories and the target repository is ambiguous, ask.

---

## 3. Dispatch and Coordinate

- For each repository job create a distinct Git branch and isolated worktree based on `develop` `HEAD` (creating `develop` first if it does not exist); use unique names and paths, and never reuse, overwrite, or remove an existing worktree or branch. The user authorizes creation of `develop` (when missing), these worktrees, and child commits as part of this workflow. For non-repository work ask how to isolate it. If the target has uncommitted changes, do not assume they are present in the child: preserve them and ask if the job depends on them.
- Create a new Herdr tab in `HERDR_WORKSPACE_ID` with the worktree as its absolute cwd and `--no-focus` (`herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd "$WORKTREE" --label "<job-name>" --no-focus`); use the returned root pane ID. Start a uniquely named agent in that shell pane per **§1**. Never prompt this orchestrator's own pane, reuse a user-owned agent, or alter existing panes.
- Provide a self-contained prompt: objective, context, constraints, ownership/files, target (`develop`) and worktree branch, acceptance criteria, and verification. Each child works only in its assigned worktree, commits its own completed work there, and reports the commit SHA, base, tests, changes, and blockers. No child may merge into `develop`, push, deploy, delete branches/worktrees, or modify other worktrees. Keep separate writers in separate worktrees; account for semantic conflicts between parallel jobs.
- **Forwarding `/ergo` to Child Sessions**:
  Whenever the user includes `/ergo` (or `/ergo --herdr` / `/ergo --models ...`) in a job request, always forward `/ergo` to the child session opened for that job (and instruct the child to use `/ergo` when a complex task warrants its multi-stage planning/review workflow). Resolve the canonical `/ergo` skill path from the catalog via `herdm catalog show --json` (or `get_skill_source('ergo')`), rather than looking in obsolete paths or static locations. Explicitly constrain it to the assigned worktree, branch, and adopted workspace:
  - Planning/approval, specialist `Expert Persona` assignment, and review gates remain in force.
  - Normal canonical-checkout landing, `git reset --hard`, branch/worktree deletion, and workspace cleanup are forbidden in the child.
  - The child adopts the assigned worktree, branch, and workspace (`owns_workspace = false`). Adopted workspaces are not created or closed.
  - The child must stop after its own reviewed commit on its assigned worktree branch; the master alone integrates into `develop`.
  - Do NOT recursively invoke `herdm ergo` to start another coordinator.
- Record for each job its name, tab/pane ID, scope, status, and important decisions. Report launches and progress concisely. Submit with `herdr agent prompt`; establish that work actually starts by observing state/output. A timeout or stalled response does not prove non-delivery: inspect before retrying, never blindly duplicate work.
- **Async Monitoring (No Polling Loops)**: Monitor only agents created by this orchestration. After confirming a child is working, launch one bounded **async watcher** for that child using your harness's native async mechanism in **§1**; the user authorizes this monitoring delegation:
  - Pass the exact Herdr session/socket environment variables (`HERDR_ENV`, `HERDR_WORKSPACE_ID`, etc.), child name or pane ID, and an explicit timeout.
  - The watcher's sole read-only task is to run `herdr agent wait <target> --timeout <ms>`, report the exact result/state or timeout/error, and never send input, change layout, modify files, or make decisions.
  - Ensure it accesses the inherited Herdr session; do not fall back to a different server if it cannot. Keep at most one watcher per child and track watcher IDs and the child state/last observed event.
- Native async subagent/background task completion wakes the orchestrator; end your turn (return control) after launching watchers rather than sleeping, polling in a loop, or blocking synchronously. On notification, inspect the named child with `herdr agent get` and `herdr agent read`: lifecycle state alone is not proof of completion, and `idle`/`done` can contain a question. Handle ordinary questions or ask the user as described below. Rearm a watcher only after confirming the child has resumed working; otherwise a wait on an already `idle`/`done` state can immediately return and create a notification loop. A bounded wait may time out: report or record it, re-check state, and rearm only if still working. Never silently ignore watcher failures; explain them and use a manual status check if needed.
- Continue accepting user requests while watchers run. This is best-effort monitoring while the parent session and async watchers are active, not a persistent service or a guarantee of delivery after exit. A child may ask a question in plain output without Herdr marking it `blocked`; state transitions and notification delivery can race, so verify the actual output.
- For ordinary questions from a child, answer using the user's requirements and established facts when the decision is clearly within the delegated scope. If information is missing, requirements conflict, the decision has material consequences, or an action needs authorization, relay a concise question to the user and wait for their response before forwarding it. Never invent an answer.
- A Herdr `blocked` state may be an approval or question dialog: read the actual UI (`herdr agent read`) and ask the user before interacting with it; do not auto-approve or bypass it. Use `herdr agent prompt` for ordinary follow-ups only when the agent is ready, and appropriate `herdr agent send-keys` only after explicit authorization for the identified dialog.

---

## 4. Master Final Review Gate, Serial Integration into `develop` & Cleanup (NEVER run `git reset --hard`)

- **Master Final Review Gate (MANDATORY before every squash-merge)**:
  1. After a child reports completion, confirm it is no longer writing, then inspect its worktree branch, exact commit(s), base (`develop`), full diff (`git diff develop...<child-branch>`), and test results.
  2. Perform a **thorough final review** of the child's changes before any integration (checking requirements completeness, project pattern conformance, cross-job semantic conflicts with other active/landed jobs, logic/boundary/async/SQL/security bugs, and unintended file edits). You may run this final review directly as the master coordinator or spawn a dedicated read-only Reviewer tab using the product default Reviewer binding: `pi:xai/grok-4.7:high` (`herdm catalog agent --mode herdr --binding pi:xai/grok-4.7:high --role reviewer --json` → `--kind pi -- --provider xai --model grok-4.7 --thinking high`) on the child's worktree diff.
  3. **If the Master Final Review finds issues (`NEEDS FIXES`)**: Do **not** merge into `develop` and do **not** close the child tab yet. Send the concrete review findings back to the existing child session (`herdr agent prompt <child-pane-id> "<review findings & fix instructions>"`), re-arm its async watcher, and wait for the child to fix, re-test, and commit the fixes on `<child-branch>`, then re-run the Master Final Review until it passes (`APPROVED`).
- **Serial Squash-Merge into `develop` (only after Master Final Review = `APPROVED`)**:
  - The user authorizes the master to squash-merge the approved child branch into `develop` (creating `develop` first if it does not exist), not to push or deploy.
  - Before landing, ensure `develop` is checked out cleanly, recheck `HEAD`, index, and working tree, and ensure no other writer is active there. A dirty or staged target, ambiguous ownership, or overlapping changes is a stop-and-ask condition; never overwrite, stash, reset, or mix unrelated work into a commit.
  - If `develop` has advanced since the child branch was created, first rebase/merge `develop` inside the child's worktree (`<child-branch>`), have the child resolve any conflicts, re-verify tests, and pass a scoped Master Final Review check on the resolved files before squash-merging into `develop`. **Never use `git reset --hard`**.
  - When clean and approved, integrate serially with `git merge --squash <child-branch>`, verify the staged diff contains only the intended work, run relevant tests, and create one squash commit on `develop`. If conflicts or validation failures arise, preserve all work and report/ask before any destructive recovery; **never use `git reset --hard`**.
- Only after a successful squash commit and verification, perform the authorized cleanup:
  1. Confirm the exact child tab, session, worktree, and temporary branch are the ones created for this job, the child and any nested work have stopped, no new user work is in progress, the worktree/index are clean, all intended work is included in the target squash commit, and the branch is not used by another worktree.
  2. Close only the completed child's Herdr tab/session (never unrelated tabs or unowned workspaces).
  3. Remove the clean job worktree using normal `git worktree remove` (do not force-remove worktrees).
  4. Delete only its temporary branch after explicitly verifying the squash landed on `develop`.
  5. Close the orchestrator's workspace only if bootstrap `owns_workspace` is true (`herdr workspace close "$HERDR_WORKSPACE_ID"`), closing only that ID. Adopted workspaces (`owns_workspace == false`) are NEVER closed.
- Report child SHA, `develop` squash SHA, tests, cleanup, and any remaining issues. Do not call a task complete solely on agent state. If identity, ownership, activity, cleanliness, integration, or branch use is uncertain, or any cleanup step fails, stop cleanup and ask the user; preserve everything remaining. Never close or delete user-owned or unrelated resources.
