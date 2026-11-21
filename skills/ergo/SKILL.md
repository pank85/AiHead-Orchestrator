---
name: ergo
description: "Run the Ergo Scout -> Architect -> parallel specialist Coders -> Reviewer pipeline with worktree/branch isolation, Approval Gate, and single squash-commit landing (supports --models, --native, and default Herdr mode)."
argument-hint: "[--native] [--models ...] [--ai-head|--no-ai-head] [intent]"
slash: false
metadata:
  opencode/autoinvoke: false
---

# /ergo — Scout → Architect → Parallel Specialist Coders → Reviewer Loop

**Core boundary:** If `HC_TERMINAL_ID` is set or `--backend core` is selected, STOP and report that Ergo is not yet supported in Herdr Core, even with `--native`. Do not launch agents or use legacy Herdr operations as a fallback. Native Ergo outside Core remains supported as described below.

Launch with `aihead ergo "<intent>"` (or `aihead --ergo "<intent>"`); `herdm` is deprecated.

Run an Ergo pipeline for: `${@:-$ARGUMENTS}` (if no intent or spec path was supplied, ask what to build and stop).

You are the orchestrator. **Never implement code directly in the main conversation.**

**Dependencies:** `aihead` is always required (legacy `herdm` remains supported), and Herdr is required for the default mode (`--native` runs on the host's own subagents and needs no Herdr). If either is missing, STOP and report it — never substitute an embedded model table or a degraded single-context pipeline.

- **Default Mode (Herdr Cross-Harness)**: Plain `/ergo` defaults to Herdr mode. The mixed agy/Pi product default squad cannot run through a single host's native subagent tool, so every pipeline role runs as an interactive Herdr agent tab inside one Herdr workspace. `--herdr` is an explicit alias for this default.
- **Explicit `--native` Mode**: When `--native` is explicitly passed (e.g. `/ergo --native ...`), the pipeline runs using the parent harness's native subagent tool (`invoke_subagent` in `agy`, `subagent` in `pi`/`opencode`, `Agent` in `claude`). There is no alternate native default squad; `--native` requires explicit compatible role/worker overrides or a compatible run snapshot, and any inherited product binding incompatible with the native host is rejected by the resolver.
- **Herdr Core (`--backend core`)**: Ergo is not yet supported in Core, including with `--native`; follow the Core boundary above.
- **`--models` Inline Role & Worker Overrides**: Overrides any role (`scout`, `architect`, `reviewer`) or worker pool (`workers` / `coders`). Works in both default Herdr mode and `--native` mode.
- **`--ai-head` / `--no-ai-head` AiHead Consultation**: Configures autonomous technical decision consultation via AiHead (see §3).

## Coordinator identity (in-session vs delegated launch)

- Running `/ergo` inside an already-open harness session: that session IS the Ergo coordinator. Never launch a coordinator pane and never call `aihead ergo` / `herdm ergo` just to obtain a coordinator; the `[ergo] coordinator` binding does not apply.
- A coordinator pane is created only for a new pane/session launch (`aihead ergo`, `aihead --ergo`); that launch uses `[ergo] coordinator` or `--coordinator`, and falls back to the Scout binding when unset.

---

## 0. Configuration Hierarchy & Squad Resolution via `aihead catalog`

When resolving squad role models and worker pool bindings, follow this strict priority order:
1. **Inline CLI / Prompt Overrides (`--models`, `--scout`, `--architect`, `--workers`, `--reviewer`)**: Explicit arguments passed in the invocation command take highest priority.
2. **Run Snapshot (`--run-squad <path>`)**: If a run squad TOML snapshot path is supplied, its pinned bindings take priority over project defaults.
3. **Project Configuration (`.herdr-master.toml`)**: If `.herdr-master.toml` exists in the project root, its configured model overrides take priority over product defaults.
4. **Product Default Squad**: If none of the above are provided, use the authoritative product default squad from the catalog.

An AiHead automode run supplies the composition as `--models` or a run squad snapshot, which already outrank the project squad.

### Authoritative Squad Resolution (No Hand-Maintained Fallback Tables)
Do NOT maintain or consult hardcoded default model tables. The single authority for squad configuration, roles, and allowed coder models is `aihead catalog`:

1. **Resolve Effective Squad**:
   Execute:
   ```bash
   aihead catalog squad [--mode herdr|native] [--harness <native-host>] [--project .herdr-master.toml] [--models "<spec>"] [--run-squad <path>] --json
   ```
   - Default mode is `--mode herdr` (unless `--native` was explicitly passed).
   - If `--native` is passed, supply `--harness <active-harness>`.
   - Forward any user-specified overrides (`--models`, `--scout`, `--architect`, `--workers`, `--reviewer`).
   - **`--models` spec grammar**: role entries are WHITESPACE-separated and each is `role:binding` with COLONS — e.g. `scout:agy:gemini-3.8-flash-medium:medium architect:opencode:openai/gpt-6.1-sol reviewer:agy:gemini-4-argon-high:high`. `workers`/`coders` bindings are COMMA-separated with no spaces (e.g. `workers:agy:gemini-3.8-flash,opencode:openai/gpt-6.1-sol`). The resolver rejects `role=binding` with `Unknown role '<role>=<kind>'` — do not copy `role=...` examples from docs. Validate the whole spec BEFORE launching with `aihead catalog squad --mode herdr --models "<spec>" --json` and confirm every overridden role reports `sources: override` and the payload reports `advisories: []`.
   - If `aihead` is not found in the environment, **STOP and report an actionable installation prerequisite error** (e.g. install `aihead` via `uv` or activate a virtualenv containing `herdr-master`). Never fall back to an embedded table.

2. **Authoritative Product Default Squad (returned by `aihead catalog squad`)**:
   - **Scout**: `agy:gemini-3.8-flash-medium`, effort `medium` (Herdr argv: `--model gemini-3.8-flash-medium --effort medium`)
   - **Architect**: `pi:openai-codex/gpt-6.1-sol:max` (Herdr argv: `--provider openai-codex --model gpt-6.1-sol --thinking max`)
   - **Reviewer**: `pi:xai/grok-4.7:high` (Herdr argv: `--provider xai --model grok-4.7 --thinking high`)
   - **Worker Pool**: Exactly two ordered routes (pool entries have no fixed effort; the Architect assigns effort dynamically per task):
     - `agy:gemini-3.8-flash`: simple, mechanical, well-scoped work (per task: `low`, `medium`, or `high`, resolved to exact `gemini-3.8-flash-low`, `gemini-3.8-flash-medium`, or `gemini-3.8-flash-high` with matching `--effort`)
     - `pi:xai/grok-4.7`: complex, architectural, or subtle work (`--provider xai --model grok-4.7 --thinking <level>`)
   *Note*: `claude:claude-3-opus` and `agy:gemini-4-argon-high` are NOT defaults anywhere.

3. **`ALLOWED CODER MODELS` Block (Dynamic Rated Pool)**:
   Extract `.allowed_coder_models` directly from `aihead catalog squad [--worker-mode <mode>] --json` output. Unless explicit `--workers` overrides were passed, this block is built dynamically from effective `[models.available]` + `[models.ratings]` across enabled harnesses and ranked by `worker_mode` (`balanced` | `quality` | `fast` | `frugal`, inheriting `ai_head.routing_profile` when set to `inherit`). Paste this block verbatim into the Architect prompt so the Architect selects the best worker model and per-task effort using the stored ratings. Do not craft your own model list.

   The block reports `worker_tier` alongside `billing_tier`; these are distinct.
   Harnesses disabled in `[models.harnesses]` (`aihead --setup` → `[7] Harnesses`) are excluded from `ALLOWED CODER MODELS` and blocked on dispatch; never select a disabled harness.

   Tier 1 = Cheap/Fast, Tier 2 = Everyday/All-rounder, Tier 3 = Frontier

   Mode & intra-tier guidance (baseline outline — AiHead/Architect make the final call; `cost_efficiency` / price matters even on subscription limits):
   - `frugal`: always pick the **cheapest** model in the minimum viable tier (T1 default, T2 only if required; low effort). Roles: Scout T1 cheapest, Architect T2 cheapest, Reviewer T1–T2 cheapest cross-family.
   - `balanced`: pick **best value** within the task's required tier (T1 mechanical, T2 standard, T3 only if very hard). Roles: Scout T1 cheapest, Reviewer T2 best-value cross-family, Architect mid/value T3 (low/medium effort, or T2 if simple).
   - `quality`: pick **best capability** within the task's required tier (T2 default, T3 for hard/core). Roles: Scout T2 best, Architect top T3, Reviewer top T3 cross-family.
   - `fast`: pick **fastest** within T1/T2 with low effort.

   Thinking effort is guidance, not a one-to-one lock: AiHead or the Architect combines worker tier and effort.

4. **Resolving Agent Launch Descriptors**:
   For each role/task launch, resolve the exact launch arguments or native tool call from:
   ```bash
   aihead catalog agent --mode <mode> [--harness <native-host>] --binding <binding> --role <role> --json
   ```
   - In Herdr mode, use the resolved model and effort arguments; reject permission bypass flags unless separately and explicitly authorized by the user for the exact action and target. Use the resulting `argv` array in `herdr agent start <name> --kind <kind> --pane <pane-id> -- <argv...>`. Scope Hermes catalog checks strictly to when a Hermes child route is selected: when a Hermes child binding is requested, verify Hermes is present in the active catalog (via `aihead catalog show --json`). If the active catalog lacks `hermes`, report that Hermes catalog support is not yet integrated into the active environment and STOP for that Hermes route rather than fabricating launch arguments. Standard non-Hermes roles (e.g. default agy/pi roles) do not require Hermes in the catalog and proceed normally. When Hermes is supported and selected, child sessions use `--kind hermes -- --provider <provider> --model <model> --reasoning <effort>` (skip flags such as `--yolo` must not be used for ordinary launches; use `--no-skip-permissions` where supported). Skipping tool prompts cannot approve the Plan Approval Gate, Reviewer verdict, or landing. Provider/model splits on the first slash; do not use `--thinking`, `--resume`, or `--continue`.
   - In Native mode, the returned descriptor contains `tool`, `kwargs`, `setup`, `prompt_param`, and optional `effort_prefix`. Use these verbatim.

### Route preflight — smoke-test every distinct route before spawning

The catalog proves a binding RESOLVES, not that it WORKS: a product default squad can carry routes that no longer authenticate or have no quota left. Round-trip each distinct harness/provider/model route once before spawning any role:

```bash
agy --model <model> [--effort <level>] -p="Reply with exactly: PONG"
pi -p --provider <p> --model <m> [--thinking <level>] --no-tools --no-session "Reply with exactly: PONG"
opencode run --model <provider/model> "Reply with exactly: PONG"
pi auth check --provider <p> --json    # ready | not_ready | invalid  (exit 0 | 1 | 2)
```

- **Put every flag BEFORE the prompt.** `agy -p` consumes the next argv token as its prompt, so `-p "x" --model y` silently drops `--model` and runs the default model — the run looks healthy and is wrong.
- Treat a failed route as a ROUTING decision, not an auth incident to debug mid-run: substitute only bindings the catalog resolves, and re-verify the substitution with `aihead catalog squad --models "<spec>" --json`. If no catalog-resolvable route works for a role, STOP and ask instead of inventing a binding.
- Report discovery and live smoke results separately; a resolving catalog entry is not a working route.

---

## 1. Native Subagent Execution (`--native` Mode Only)

When `--native` is explicitly passed:
- Native execution uses the active host's subagent tool as described by `aihead catalog agent --mode native --harness <host> ...`.
- Native adapters preserve specific tool contracts:
  - **Antigravity (`agy`)**: `invoke_subagent` accepts tier IDs (`flash_lite`, `flash`, `pro`) and effort phrases (`think`, `think hard`, `think harder`, `ultrathink`) on line 1 of `Prompt`. Exact CLI slugs (e.g. `gemini-3.8-flash-medium`) are rejected in native mode; run via default Herdr mode to use exact CLI slugs.
  - **Pi (`pi`)**: uses `subagent({ agent, model, task })` with model effort suffixes (e.g. `xai/grok-4.7:high`).
  - **Claude Code (`claude`)**: uses `Agent` tool with prompt effort phrases on line 1.
  - **OpenCode (`opencode`)**: uses `subagent` tool with prompt effort phrases on line 1.
  - **Hermes (`hermes`)**: Currently has no catalog native-subagent adapter (`supports_native=False` in catalog; `describe_native_agent` raises an error). If `--native` is explicitly requested under Hermes, **STOP and report that native mode is unsupported by the catalog**. Do NOT silently switch execution mode to Herdr or alter bindings; require an explicit user decision before changing execution mode or bindings, and preserve any user-specified model/effort overrides if the user approves switching to Herdr mode.
- **Read-Only Reviewer Constraint**:
  Reviewer is spawned as read-only (in `agy`: `EnableWriteTools: false`; in `pi`: read-only agent). If a native reviewer cannot write its report (`RUN_DIR/review.md` and `RUN_DIR/fixes_<N>.md`) because its tools are read-only, the orchestrator persists the returned report text under the concrete `RUN_DIR` rather than granting source-write permissions.

---

## 2. Herdr Execution & Workspace Adoption Contract (Default Mode)

Plain `/ergo` and master-forwarded Ergo jobs execute in Herdr mode.

### Workspace & Session Adoption (No Duplicate Workspaces, No Blanket Closes)
- **Bootstrap Context Version 1**:
  When launched by a parent orchestrator (such as `launch_ergo` or `/herdr-master`), the orchestrator passes a JSON bootstrap context:
  `{ "schema_version": 1, "entrypoint": "ergo", "intent": "...", "origin_cwd": "...", "session": "...", "workspace_id": "...", "owns_workspace": false, "squad": {...}, "worktree": "...", "run_dir": "..." }`
- **Adoption & Ownership Rules**:
  - **Bootstrap `owns_workspace` is the ONLY ownership signal**: Do not set or infer `owns_workspace = false` merely because `workspace_id` is present. Read the bootstrap boolean `owns_workspace` directly.
  - **Adopt when `workspace_id` is present**: When `workspace_id` is set (via bootstrap context or `HERDR_WORKSPACE_ID`), adopt that workspace directly. Do NOT call `herdr workspace create` again when `workspace_id` is already set.
  - Only if no `workspace_id` is provided anywhere, create a temporary Herdr workspace `ergo-<run-id>` and mark `owns_workspace = true`.
  - **Cleanup Rules**:
    - Close the workspace only when bootstrap `owns_workspace` is true, and close only that specific ID: `herdr workspace close <workspace_id>`.
    - Adopted workspaces (`owns_workspace == false`) are NEVER closed.
    - Never close unrelated workspaces or tabs.
  - **No Recursive Coordinator Kickoff**:
    Do NOT recursively invoke `aihead ergo` to start another coordinator. The current session IS the orchestrator.

### Spawning Role Tabs (Non-Blocking & Strict One-Level-Up Hierarchy)

**Mandatory first-prompt contract.** Before any delegated Herdr spawn, bind the verified dispatching coordinator's own pane in the exact session; fail closed before spawn if the direct parent is missing or ambiguous. In the first submitted task prompt, replace `<ORCHESTRATOR_PANE_ID>` with that concrete pane ID in both the parent declaration and each `herdr agent prompt <ORCHESTRATOR_PANE_ID> "<message>"` command. The child sends every question, decision or approval request, blocker, and completion to that parent only; never contact any other pane or the user. Environment and bootstrap metadata only corroborate the first prompt; a follow-up cannot supply the missing directive. An intentional parentless root uses its own user/result channel, not an invented pane.
1. Ensure the Herdr server is accessible (`herdr status server`).
2. Create one tab per role in the workspace, forwarding the Ergo Orchestrator's own pane ID (`$HERDR_PANE_ID` as `ORCHESTRATOR_PANE_ID`) via `--env`:
   `herdr tab create --workspace <WS_ID> --cwd "$WORKTREE" --label <role> --env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID" --no-focus` → record `<PANE_ID>`.
3. Start the agent using the exact argv resolved from `aihead catalog agent`:
   `herdr agent start <name> --kind <kind> --pane <PANE_ID> -- <resolved_argv...>`
4. Send the complete first prompt from §4 with `herdr agent prompt <PANE_ID> "<prompt>"`. Verify startup, register the confirmed child, and arm or ensure the run-scoped watcher from Step 5 before returning; do this for Scout and Architect as well.
5. **Non-Blocking Parent Rule**: Parent sessions (including the Ergo Orchestrator) NEVER block or wait in polling loops after spawning/prompting children — return immediately after spawning/prompting a role tab and resume when the child sends its `herdr agent prompt <ORCHESTRATOR_PANE_ID> "..."` notification. For parallel `INDEPENDENT` tasks, start and prompt all coder tabs in the same turn and return immediately; verify completion via `herdr agent get/read` plus expected summary files when notified.
   **Same-turn handoff**: When a child reports completion or a blocker, act in that same turn. Verifying a summary and spawning the next role are actions, not a closing sentence. Do not end the turn on "I'll verify" or "I'll start the reviewer." If the next role is the Reviewer, create the tab, start the agent, register it, prompt it, and confirm `agent_status` is `working` plus the expected model in the footer before reporting that review started. A pane that exists but is idle with no prompt is not a running review.
6. **Strict One-Level-Up Hierarchy (NEVER Skip Levels)**:
   - **Level 3 workers (Scout, Architect, Coders, Reviewer)** notify **ONLY** the Level 2 Ergo Orchestrator (`ORCHESTRATOR_PANE_ID` = `$HERDR_PANE_ID`) via `herdr agent prompt <ORCHESTRATOR_PANE_ID> ...` for BOTH `"user/decision needed"` (questions, blockers) AND `"completed"` (stage/task finished) — **NEVER AiHead or Herdr-Master directly!**
   - **Level 2 (Ergo Orchestrator)** sends decisions, approval requests, blockers, and untagged completion reports only to the concrete direct-parent pane stated in its own first prompt (normally AiHead or Herdr-Master Coordinator). Use that exact pane ID as the `herdr agent prompt` target; `HERDR_PARENT_PANE_ID` and bootstrap metadata may corroborate but must never replace the first-prompt target. If that pane is absent, ambiguous, or conflicts with metadata, STOP and report the blocker without prompting any pane.
7. On Reviewer fix cycles, send fix prompts to the **existing** coder tabs rather than opening duplicate tabs.

---

## 3. AiHead Availability and Autonomous Decisions

The Ergo pipeline supports automated, intelligent technical decision consultation via AiHead:

- **Tri-State Flag & Discovery Descriptor**:
  - `--ai-head`: Explicitly enable AiHead consultation.
  - `--no-ai-head`: Explicitly disable AiHead consultation.
  - Omitted: Discovered from project configuration (`.herdr-master.toml`) or launcher bootstrap context descriptor.
- Automode uses the saved available-model allowlist and must not invent ids.
- **TUI Mode**:
  - Consults AiHead via its persistent Herdr tab (labelled `ai-head`) in the active workspace.
  - Automated requests are serialized with unique correlation IDs; never inject requests blindly into an active user conversation.
- **CLI Mode (100% Herdr-free)**:
  - Direct execution without Herdr. Commands or harnesses calling each other invoke the harness directly via its native headless CLI runner (e.g. `opencode run`, `agy`, etc.) or native agent tool call in a direct subprocess without any Herdr daemon or socket. Harnesses can call each other directly without Herdr.
- **Correlated Requests & Structured Decisions**:
  - Every consultation provides a unique correlation ID and awaits a structured artifact (`{ "decision": "...", "rationale": "...", "escalated": false }`).
- **Autonomous Decisions & Scope**:
  - Autonomous decisions are accepted for technical/architectural choices within delegated scope (such as resolving non-conflicting implementation trade-offs or plan approval within delegated intent).
  - Genuinely ambiguous requirements, conflicting requirements, or missing specifications go through the direct parent if delegated; only a standalone coordinator may ask the user directly.
- **Hard Guardrails**:
  - **Destructive operations, security bypasses, or out-of-scope actions always require explicit user approval.**
  - AiHead enablement never grants permission to bypass tests, land unreviewed code, push, or run `git reset --hard`.

---

## 4. The Spawn Recipe & Shared Boilerplate

Every initial Herdr task or native tool prompt must use this mandatory recipe (also for replacement workers and fix-verification reviewers), in this order:
1. **Effort phrase** on line 1 (`think`, `think hard`, `think harder`, or `ultrathink` when supported by the model/harness).
2. **Specialist Persona line** (MANDATORY on every Coder task using the task's `Expert Persona` from `plan.md`):
   `Adopt this expert persona: <Expert Persona>.`
3. **Shared Boilerplate** (verbatim below).
4. **Role Brief** (`Scout Brief`, `Architect Brief`, `Coder Brief`, or `Reviewer Brief` from **Agent Briefs** below).
5. **Verbatim `ALLOWED CODER MODELS` block** (when spawning the Architect).
6. **Run paths & inputs**: absolute `WORKTREE`, absolute `RUN_DIR`, `unitTestsMode` (`none` | `new` | `update-only`), and task-specific inputs.
7. **Parent Notification Requirement**: Render the appropriate route below into the first submitted prompt. On Herdr, substitute the concrete `<ORCHESTRATOR_PANE_ID>` verified in §2. In `--native` mode apply only the native variant; never inject the orchestrator's own Herdr parent or a pane command into a worker prompt.

### Shared Boilerplate
> **Worktree / Checkout:** This run lives in `WORKTREE`. Every path you read or write is under `WORKTREE` — never the canonical project root (when a worktree is in use) and never another worktree. Do not commit, push, merge, or create branches; the orchestrator lands the work.
>
> **Run directory:** Every `.orchestration/...` path is relative to the absolute `RUN_DIR` given in this prompt. Read and write there — never the bare `.orchestration/` root or sibling runs.
>
> **Large output — context-mode:** When `ctx_batch_execute` / `ctx_execute` tools are available, use them for commands producing large output (trees, test results, build logs) instead of raw shell.
>
> **C# projects:** Use the `csharp-lsp` LSP server when available for type resolution, go-to-definition, and diagnostics.
>
> **Project instructions & agents:** Read `AGENTS.md` / `CLAUDE.md` and `.ai/*.md` first for orientation, then verify in source code (code is the truth).
>
> **Unit tests — `unitTestsMode`:** Obey `unitTestsMode` (`none` / `new` / `update-only`): `none` = no test work at all; `update-only` = modify affected existing tests only, never add new ones; `new` = add/update tests as planned.
>
#### Herdr parent variant
> Your direct parent is the Ergo Orchestrator at `<ORCHESTRATOR_PANE_ID>`. Route all decisions, approvals, questions, blockers and completion only to this pane. Never contact the user or any other pane.
> - **Completed:** When your summary or artifact is ready, notify your direct parent. **Launcher-tagged Herdr:** use the current assignment's first-prompt `parent_contract complete` command with `<Role> finished: <brief outcome/artifact path/verdict>` as its message. **Untagged Herdr shell:** send `herdr agent prompt <ORCHESTRATOR_PANE_ID> "<Role> finished: <brief outcome/artifact path/verdict>"` to the verified direct parent, correlated to the exact launch/assignment. **Native:** return the same payload through the exact invoking supervisor's native result channel; no Herdr command. Apply the receiving completion gate before advancing.
> - **Decision / Question / Blocker Needed:** If you encounter an unresolvable blocker, ambiguity, or fatal error requiring a decision, notify your direct parent orchestrator immediately:
>   `herdr agent prompt <ORCHESTRATOR_PANE_ID> "<Role> blocked/decision needed: <reason or question>"`.
#### Native parent variant
> Your only supervisor is the Ergo Orchestrator that invoked this native tool call. Return all questions, decisions, approval requests, blockers and completion with the requested artifact payload only to that exact invoker via this tool's native result/message channel. Never contact the user, call Herdr, or route to a grandparent. If the tool has no child-to-parent mid-run messaging, return the blocker rather than waiting or seeking another route.

> **Herdr Core (`--backend core`)**: Ergo workflows are not yet Core-compatible; use legacy Herdr TUI or CLI mode.

---

## Pane launch verification (mandatory)

A spawn succeeded only when `herdr agent get <pane>` reports `agent_status: working` and `herdr agent read <pane>` shows the expected model with no startup, permission, or credit error. `agent_not_found` (or a vanished pane) means the launch FAILED. Never report a started job without that evidence; report the model slug, `agent_status`, and context usage.

---

## 5. Pipeline Sequence

**Shared-session parent routing:** The dispatching parent's own pane ID is the child's direct parent, never its grandparent. It may pass that pane to the child as `HERDR_PARENT_PANE_ID`, but for its own delegated decisions this Ergo coordinator must use the concrete parent pane ID stated in its own first prompt. Substitute that exact value for `<direct-parent-pane-id-from-first-prompt>` in every parent-directed command below; environment and bootstrap metadata may corroborate, never replace, it. If the pane is missing, ambiguous, or conflicts with the assignment, STOP and report the blocker without prompting a guessed pane. This is a practical shared-session trust model: same-session agents can impersonate one another or send prompts; there is no authenticated sender or security isolation claim, and this prose is not runtime enforcement.
1. A known delegated run uses its assigned worktree, branch and run directory and retains no-integration/no-cleanup restrictions. Only a genuinely standalone invocation may use standalone routing or landing. If delegation status or parent ID is missing, ambiguous, or conflicts with the assignment, STOP and report the blocker without guessing a pane.
2. Send the plan, its run and version, and the child pane ID to the direct parent with `herdr agent prompt <direct-parent-pane-id-from-first-prompt> "<approval request>"`. The parent reviews the exact plan and sends its decision to the child pane with a Herdr prompt to the recorded child pane containing `APPROVED/REVISE`, the run, plan version and guidance. Wait for an explicit decision; no Coder starts before approval. A changed plan requires fresh approval. If the parent or decision is unavailable, STOP.
3. Route routine questions and blocker reports to that same exact first-prompt pane. Use `parent_contract complete` for launcher-tagged terminal reports; for untagged shell reports, prompt the exact first-prompt pane directly. Destructive operations, security bypasses, and out-of-scope actions still require explicit user approval; a delegated coordinator obtains it through the parent, not a user tab.

A known delegated run retains its assigned worktree, branch, run directory, and no-integration/no-cleanup restrictions even when parent identity is unavailable. If delegation status is uncertain, STOP until clarified; never fall back to standalone or AiHead approval.

Track one checklist item per step and complete in order:

### Step 1: Isolation (`WORKTREE`), Branch (`ergo/<run-id>`), `RUN_DIR`, Snapshot & Preflight
1. **Adoption or Creation of Worktree/Branch**:
   - If a parent orchestrator provided `worktree` and `run_dir` (e.g. forwarded by `/herdr-master` or bootstrap context), **adopt** them directly.
   - Otherwise:
     - Require a clean working tree (`git status --porcelain` empty; if dirty and delegated, prompt the exact pane from this coordinator's first prompt with `herdr agent prompt <direct-parent-pane-id-from-first-prompt>`; if that pane is missing or uncertain, STOP as above; only if genuinely standalone ask the user to commit/stash).
     - If already inside an `ergo/*` worktree (`git rev-parse --git-dir` ≠ `--git-common-dir` and branch matches `ergo/*`), reuse it and keep its branch.
     - Otherwise, record `ORIGIN_BRANCH=$(git rev-parse --abbrev-ref HEAD)` and create an isolated worktree `WORKTREE=<project_parent>/<repo>-ergo-<run-id>` on branch `ergo/<run-id>`:
       `git worktree add "$WORKTREE" -b "ergo/<run-id>"`
     - Create `RUN_DIR=<WORKTREE>/.orchestration/ergo-<run-id>/` as an absolute path.
2. **Resolve Squad and Write Frozen Snapshot**:
   - Resolve the squad via `aihead catalog squad [--mode herdr|native] ... --json`.
   - Write the frozen snapshot to `$RUN_DIR/squad.toml`:
     ```bash
     aihead catalog squad --from-json - --write-snapshot "$RUN_DIR/squad.toml" --json <<EOF
     <resolved-squad-json>
     EOF
     ```
   - Every role receives concrete `WORKTREE`, `RUN_DIR`, the resolved squad's `allowed_coder_models` block, and `unitTestsMode`.
   - Do NOT create a preliminary snapshot in the canonical checkout outside the assigned worktree.
3. **Workspace Setup**:
   - In Herdr mode: if `workspace_id` is set, adopt that workspace. Do not call `herdr workspace create` when `workspace_id` is already set. Only if no `workspace_id` exists anywhere, create a temporary Herdr workspace `ergo-<run-id>` and record `owns_workspace = true`.
4. **Unit Tests Question**:
   - Unless stated in the prompt, determine `unitTestsMode ∈ {none, new, update-only}` (if delegated, ask the exact pane from this coordinator's first prompt via `herdr agent prompt <direct-parent-pane-id-from-first-prompt>`; if delegation or that pane is uncertain STOP as above; only if genuinely standalone ask user) and forward to all agents.

### Step 2: Scout — Fast Discovery (NEVER skip)
- Spawn **Scout** with confirmed intent, 3–5 specific questions to answer, `WORKTREE`, `RUN_DIR`, and `unitTestsMode`.
- On the Scout's terminal report verify `RUN_DIR/context.md`. Missing output is a blocker, not permission to retry an ambiguous launch; only a separately authorized, verified replacement uses the same first-prompt recipe. Verify `git status --porcelain` is clean (Scout is strictly read-only for source).

### Step 3: Architect — Design & Resource Allocation (NEVER skip)
- Spawn **Architect** with confirmed intent/spec, `unitTestsMode`, `RUN_DIR/context.md`, and the **verbatim `ALLOWED CODER MODELS` block** from the resolved squad.
- **Orchestrator Plan Validation**: Read `RUN_DIR/plan.md`. Every task **must** include:
  1. Disjoint **Files** ownership
  2. `INDEPENDENT` or `DEPENDS ON: <task#>`
  3. A mandatory, domain-specific **Expert Persona**
  4. An explicit **Coder Model** chosen strictly from the pasted `ALLOWED CODER MODELS` block.
  If any task omits an `Expert Persona` or names a model outside the allowed list, **reject the plan and send the Architect back to fix it**. Do not edit `plan.md` yourself to invent models or personas.

### Step 4: User Approval Gate (NEVER skip)

In automode, resolve routine, reversible in-scope decisions without asking the user. Batch consequential architectural or product questions into one plan checkpoint when possible: state the rationale, options, recommended default, and impact. Routine plans may be nonblocking updates; non-automode and explicitly gated runs retain their approval gates. Outside the checkpoint, pause affected work only when it cannot safely proceed or requires authorization; unrelated safe work may continue. Technical plan assent does not grant trust or permission. Irreversible destruction and security bypass require explicit user approval for the specific action and target, relayed one level at a time; no silence, preselected dialog, or technical assent substitutes. Use existing normal trust configuration as-is. Do not add trust, auto-confirm a prompt, or weaken permissions. If blocked, send the least-privilege alternative through the direct parent; a denial or timeout leaves state unchanged.

**Plan approval authority.** Only AiHead acting within its authorized scope or the human may explicitly approve a gated plan. A delegated coordinator sends the exact plan, run ID and plan version to its verified direct parent and waits for an explicit `APPROVED` from AiHead or the human, relayed one level at a time. Intermediaries may review, request revisions or relay the original decision with its provenance; they cannot mint approval. Coordinator self-approval, silence, a recommendation, or an approval for a different version is invalid. A changed plan requires fresh approval. Standalone coordinators use an explicitly enabled AiHead route when supported, or ask the human and wait; never use either to bypass a delegated parent. A routine automode plan that does not trigger a gate is a nonblocking update, not an approval.
- If `~/.claude/lib/render-md.js` exists, render `RUN_DIR/plan.md` to HTML and share the path alongside the summary.
- Present the plan in simple, plain language (e.g. 2–3 concise bullets: intent, files, what stays off), clearly listing each task's scope, disjoint files, assigned **Expert Persona**, and assigned **Coder Model / Effort**.
- **Architectural Alignment Verification**: Always verify that the plan fits seamlessly with the existing codebase architecture and conventions. If it does not fit, send the Architect back to find an architectural solution; if no solution exists, escalate the consequential decision via the direct parent (or to the user if standalone).
- **Approval Gate Routing (Mutually Exclusive Branches)**:
  Evaluate exactly one of the following mutually exclusive conditions in order:
  1. **Branch A — Parent Master Coordinator Gate (known delegation and direct parent)**:
     Submit the plan and child pane ID to the exact direct parent pane from this coordinator's first prompt:
     `herdr agent prompt <direct-parent-pane-id-from-first-prompt> "<plan>"`
     **STOP and wait for explicit AiHead or human approval relayed by the direct parent** for this run and plan version as specified above. Do NOT print `HERDR_NEEDS_USER`. No Coder starts without approval; if unavailable, STOP with delegated restrictions intact. A plan change invalidates approval.
  2. **Branch B — Delegated Autonomous AiHead Gate (`--ai-head` is enabled in a genuinely standalone invocation without a parent coordinator)**:
     An authorized AiHead decision within delegated scope evaluates the plan:
     - Verify architectural alignment with existing patterns, interfaces, and test conventions.
     - If requirements are ambiguous, destructive actions are involved, or scope expansions occur, escalate to the human user by printing `HERDR_NEEDS_USER: <specific question>` and **STOP to wait for explicit human approval.**
     - If the plan is approved within delegated scope, record the structured decision, architectural checks, and rationale in `RUN_DIR/aihead_decision.json` **before** proceeding to Step 5. Do NOT output `HERDR_NEEDS_USER` when AiHead approves without escalation.
  3. **Branch C — Direct Standalone Human Approval Gate (Default standalone / no `--ai-head`)**:
     Print `HERDR_NEEDS_USER: Please review and approve the implementation plan for <intent>` and **STOP and wait for explicit user approval.** The `HERDR_NEEDS_USER` marker strictly signals that execution is paused and awaiting user input — never output this marker while continuing execution in the same turn. A prior general instruction to work autonomously cannot pre-approve an unseen plan. Ask if any task's persona or coder model should be adjusted. No Coder may start before explicit user approval.

**Completion gate (Herdr children).** A Herdr child is not finished unless it has sent its terminal completion report via `herdr agent prompt <direct-parent-pane-id-from-first-prompt> "<outcome and required artifacts>"` to its verified direct parent, using the concrete pane ID from its first prompt. The parent withholds `done` and terminal completion acknowledgement until it receives that report and verifies the required outcome and artifacts. A file on disk, pane status or exit, and watcher alerts never substitute for the report. If the report is missing or delivery is uncertain, record the child as pending or blocked; do not advance any gate that depends on its completion. For a Python-launcher-tagged child, run the exact shell-quoted completion command and interpreter from the first prompt: `python -m herdr_master.pipeline.parent_contract complete --run-dir <launcher-provided-dir> --launch-token <launcher-provided-token> --message "<terminal outcome and required artifact paths>"`. The helper makes one actual direct-parent `herdr agent prompt` RPC, prefixes the launch correlation, checks the exact bound live parent pane/session and generation in the acceptance response, and persists a private correlated receipt. The receiver verifies the exact full received terminal payload (including the `HERDR TERMINAL REPORT` header) against the current assignment’s accepted-send receipt digest and tagged registry entry; acceptance alone is not parent observation. For a launcher-managed tagged row, after receiving the incoming `HERDR TERMINAL REPORT`, the receiving parent must run `python -m herdr_master.pipeline.parent_contract verify --run-dir <launcher-provided-dir> --report-id <id in incoming HERDR TERMINAL REPORT> --message "<exact full received HERDR TERMINAL REPORT payload, including header>"` before marking the child `done`. This verifies the exact parent environment, tagged ledger entry, and current assignment’s accepted-send receipt against the exact full received payload, including its header; the verifier requires `--message` to be the exact received payload. Do not paraphrase. If the report arrives before its receipt, recheck verification, not the send. If verification fails, withhold `done`; verification does not replace observing the incoming report or checking the required outcome and artifacts. This verification command does not apply to untagged shell launches. For a plain shell launch without a launcher token, send the actual prompt RPC directly to `herdr agent prompt <direct-parent-pane-id-from-first-prompt> "<outcome and required artifacts>"` and require the incoming report to correlate to the exact child launch; replace the placeholder with the exact concrete pane ID from the child's first prompt. If the pane is missing or ambiguous, fail closed and keep completion pending; do not invoke the tagged helper or invent a receipt. In both cases, transport acceptance alone is not evidence the parent observed the report. The parent must observe and reconcile the report before marking `done`. This shared-session route does not authenticate the sender or make target validation and delivery atomic. If the applicable command or correct route is unavailable, keep completion pending; do not substitute artifacts. In native mode, require the corresponding terminal report through the exact invoker's native return/message channel, not a Herdr prompt.

### Step 5: Parallel Specialist Coders & Planning Stage Tab Pruning (NEVER code in the orchestrator)

**Tier-gated Coder dispatch (mandatory; no direct starts).** Option 1: tiers are enforced only on the controlled dispatch path (aihead worker dispatch); a direct agent launch is not intercepted or blocked. Before dispatch, capture a fresh policy snapshot for the exact worktree and private run directory:
```bash
aihead worker snapshot --worktree "$WORKTREE" --output "$RUN_DIR/policy_snapshot.json"
```
Create the run-pool policy bound to this run and frozen squad; include `--workers` only when the run used an explicit worker override:
```bash
aihead worker run-policy create --run-id "$RUN_ID" --worktree "$WORKTREE" --mode herdr --run-squad "$RUN_DIR/squad.toml" [--workers "<exact explicit override, only if the run used one>"] --output "$RUN_DIR/run_policy.json"
```
Inspect the canonical manifest schema:
```bash
aihead worker manifest schema
```
The Architect writes `RUN_DIR/worker_manifest.json` using that schema. Include `run_policy_path`, `run_policy_digest`, `snapshot_digest`, and `worker_mode`; set `worker_mode` to the run's effective mode from `.worker_mode` in `aihead catalog squad --json`. Bind the exact approved task IDs, scopes/prompts, harness/model/effort, run/worktree/session, and all four strict Boolean difficulty facts: `cross_system_invariant`, `security_or_data_loss`, `novel_algorithm_or_ambiguous_architecture`, and `multiple_concurrent_state_boundaries`. Preserve the six-point quality ratings/billing tier; worker tier is separate.

Validate the complete manifest before seeking approval:
```bash
aihead worker manifest validate --manifest "$RUN_DIR/worker_manifest.json"
```
Validation prints manifest/policy digests and task eligibility; it is preflight only, not approval. Present the exact plan and both exact digests to the authorized approver. Only after explicit approval of that exact manifest and snapshot may the coordinator record approval:
```bash
aihead worker approval create --manifest "$RUN_DIR/worker_manifest.json" \
  --manifest-digest <exact-approved-manifest-digest> \
  --snapshot-digest <exact-approved-policy-snapshot-digest> \
  --approver-type <human|aihead> --approver <approver-name> \
  --confirm-approved --output "$RUN_DIR/worker_approval.json"
```
This command records a claimed external approval, checks current policy/digests, and writes a private exclusive file; it does **not** authenticate the approver. Do not hand-author, copy, or fabricate approval records, and do not treat validation, a plan-only approval, or `--confirm-approved` alone as authorization. A changed task, binding, effort, manifest, or policy snapshot requires fresh approval. If there is no explicit authorized approval for both digests, STOP before launching any Coder.

Dispatch each Herdr Coder only through:
```bash
aihead worker dispatch --manifest "$RUN_DIR/worker_manifest.json" --task-id "$TASK_ID" --approval "$RUN_DIR/worker_approval.json"
```
The dispatcher rechecks the current policy, exact assignment and routing; do not pass replacement launch argv. Never call `herdr agent start` or any other direct start for a Coder, including explicit worker overrides, retries, fixes, resumes, or route substitutions. Tiers 1 and 2 are ordinary-work choices. Tier 3 is allowed only when strict difficulty facts classify the task as very hard or the approved manifest has `worker_mode: quality`; unknown tiers block. If dispatch returns any rejection code, including `run_policy_unbound`, `approval_mismatch`, or `ineligible_tier`, no Coder started: stop and report the exact blocker; do not bypass or switch modes. Native dispatch is fail-closed unless the active host provides an in-process adapter that atomically validates and invokes the approved assignment; without one, it returns `native_unsupported`. These controls are an application-level boundary for launches through this helper, not an OS security sandbox.
- **Lifecycle Tab Pruning (Scout & Architect)**: In the same turn Coders have verifiably started, and only after AiHead or the human explicitly approves the exact plan version, the Ergo coordinator **closes its own finished Scout and Architect tabs/agents immediately** (`herdr tab close <scout-tab-id>`, `herdr tab close <architect-tab-id>`). Their planning phase is complete and they are no longer needed. Never leave idle planning tabs open during implementation.
- **Only the direct parent closes a pane/agent**: The Ergo orchestrator closes its own worker panes (Scout, Architect, Coders, Reviewer), and only after it has retained their work. No pane closes itself and no agent closes its own tab — AiHead (or the delegating parent) closes the Ergo pane once Ergo reports completion, and AiHead (or the level above) owns final integration.
- **Children registry (`RUN_DIR/children.tsv`)**: Create it when spawning the first child. One tab-separated line per child: `pane_id`, `role`, `tab_id`, `branch`, `worktree`, `run_dir`, `status`, `last_heartbeat`. Only the parent writes it. For Herdr, append the optional ninth field from the exact verified child pane: stable `terminal_id` or `agent_session.value`; The canonical child `pane_id` determines the child workspace (which may differ from the parent workspace); `tab_id` must belong to that child workspace. Launcher-tagged children keep those nine fields unchanged and add the literal tenth field `launcher-completion-v1`; the launch token stays in private launcher metadata, never in the TSV. For tagged rows, use `herdr_master.pipeline.parent_contract.completion_receipt_accepted(run_dir, parent_pane_id=..., child_pane_id=...)` to check the private correlated `agent_prompted` RPC receipt for the exact parent and child identities. This reads the current assignment pointer and checks its accepted-send receipt; it is transport evidence, not parent observation or sender authentication, and does not replace the parent completion gate. The watcher probes only the exact registered child in the verified shared session; it does not enumerate workspaces. Register confirmed children before arming supervision, and update the ledger with `status=acknowledged` only after reconciling a terminal outcome, or `status=closed` when actually closed. Remove a row only after that child is closed and its work is integrated.
- **Open and close update the list**: Opening a child session records a launch attempt; only a verified, confirmed launch is eligible for monitoring. Closing that session updates the same row in the same action: `status=closed`, the pane and tab ids, and `last_heartbeat`. Do not leave a closed session marked `working`. A missing update is a skill failure, not a later cleanup task.
**Run-scoped child health watcher (Herdr only).** Use a fresh, private, current-user-owned mode-700 `RUN_DIR` (`mkdir -m 700` or `mktemp -d`); never reuse it for another run. Record ambiguous launch attempts separately; never assume delivery or automatically retry. After registering the first confirmed child, start one detached process in the exact verified Herdr session environment (not a pane, subagent, or ledger entry). Set `RUN_ID_HEX` to a fresh 32-character lowercase hex UUID for this independent run and retain it for all further launches in the run. Python-launcher-managed runs use the launcher-provided run identity and its exact `launcher-run.json` binding; do not start a parallel shell watcher for those runs. Shell-owned example:
```bash
nohup python -m herdr_master.pipeline.child_watch --run-dir "$RUN_DIR" \
  --parent-pane "$PARENT" --parent-generation "$PARENT_GENERATION" \
  --run-id "$RUN_ID_HEX" --startup-timeout 5 \
  >"$RUN_DIR/child-watch.log" 2>&1 </dev/null &
```
Here `PARENT` is this spawning coordinator's verified pane, and `PARENT_GENERATION` is its stable `terminal_id` or `agent_session.value` from the exact pane query. Pin it for delayed/detached handoff. The owner keeps watching across all-terminal snapshots; later registrations and assignments use the same live watcher. `child-watch.lock` prevents overlap. Verify bound ownership with `herdr_master.pipeline.child_watch.watcher_ready(run_dir, run_id=..., parent_pane_id=..., parent_generation=...)`; a PID or successful detach is not readiness. Use a bounded startup handoff (5s startup budget; launcher waits at most 6s), never a health-interval poll. If readiness fails, report missing supervision and do not duplicate a child launch. `child-watch-state.json` persists notification deduplication; `child-watch.pid` is diagnostic, not authority to kill a process. At run teardown use `touch "$RUN_DIR/child-watch.stop"`; do not remove the sentinel to reuse a torn-down run. It stops on explicit teardown or loss of the bound parent, not when all children are terminal. If supervision cannot be established, report that limitation instead of claiming coverage.

Every 600 seconds the watcher checks only confirmed registered children. Positively absent, exited, or replaced children without accepted-send evidence or a reconciled terminal outcome are reported once per identity to this spawning parent only. Idle, busy, or blocked does not mean dead; transient RPC/transport errors mean unknown. For tagged children `completion_accepted` only suppresses missing-send alerts. Only the current receipt plus explicit parent ledger reconciliation (`acknowledged` or `reconciled`, not generic `completed` or `closed`) counts as `acknowledged`. A health event is not a completion callback, a work verdict, or permission to retry. An artifact alone is not success. The read-only watcher never closes panes, merges, pushes, or edits child work; it never changes the ledger or approves anything. Herdr notification is not atomic with identity validation: the watcher revalidates before sending and checks the returned identity, but a check-to-send race remains. Parents never block or poll.

**Native child health.** The invoking supervisor tracks only its own exact native run IDs. Start one nonblocking run-scoped health watcher on the first confirmed background child only if the native adapter exposes queryable status for those IDs; reconcile at 600-second intervals, separately from result callbacks. Stop on teardown, all children terminal, or loss of the invoking supervisor; ensure idempotent cancellation and restart after later confirmed launches. Report confirmed disappearance, exit, or replacement without an acknowledged terminal outcome once per identity only to the invoking supervisor. Busy, idle, and blocked are not death; transport uncertainty is unknown. The read-only watcher never closes panes, merges, pushes, or edits child work. It never changes the ledger, approves anything, or automatically retries. An artifact alone is not success. For blocking native tools, use the tool's terminal result/error callback; there is no detached poller. If a background route has no queryable status adapter, fail closed before dispatching it; choose a supported blocking route only if authorized. Never fabricate a pane mapping or spawn a monitor agent. Parents never block or poll for background children.
- **Git Lock Collision Avoidance (`index.lock`)**: Two parallel agents executing git commands in the same checkout collide on `.git/index.lock` even with disjoint files. Coders modify only files assigned to their task; worker prompts strictly forbid all git operations (`git add`, `git commit`, `git checkout`, `git reset`). Only the orchestrator lands and commits.
- For each approved task in `plan.md`, make the Coder a task-specific specialist by injecting `Adopt this expert persona: <Expert Persona from plan>.` at the top of its prompt.
- Use the exact model approved for that task in `plan.md`.
- Dispatch all approved `INDEPENDENT` tasks through the controlled helper **in parallel in a single turn**. Tasks marked `DEPENDS ON` wait until their upstream task finishes and read `RUN_DIR/coder_<upstream>_summary.md`; every new attempt still requires a current manifest and approval.
- Verify every coder wrote a non-empty `RUN_DIR/coder_<N>_summary.md` on verified completion. Missing output blocks the task; never automatically retry a health or delivery event.

### Step 6: Full Reviewer & Scoped Fix Loop (NEVER skip)
- Apply the **Same-turn handoff** rule above when the coders complete: actually launch and verify the Reviewer rather than ending on a promise to do so.
- Spawn one fresh **Reviewer** on the combined diff against the base commit, `RUN_DIR/plan.md`, all `coder_<N>_summary.md` files, and `unitTestsMode`.
- Output: `RUN_DIR/review.md` (`APPROVED` or `NEEDS FIXES`) and, per coder needing fixes, `RUN_DIR/fixes_<N>.md`.
- **Worker Lifecycle Pruning**: Only the direct spawning coordinator may close its own workers. No parent closes grandchildren and no pane closes itself. If a pane is already closed, reconcile the registry without issuing another close.
  - **A worker whose work is approved is no longer needed**: Close each Coder's tab/agent only after the Reviewer explicitly records `APPROVED` for that Coder's work in `RUN_DIR/review.md` and the parent retains the work, verification, and report. A completion message or absence of findings is not approval.
  - **Workers still in review or needing fixes remain active**: Keep each unreviewed or `NEEDS FIXES` Coder session open and reuse the same session for its fixes.
- **Read-Only Persistence**: If the reviewer runs natively with read-only tools and returns its report via output rather than writing files directly, the orchestrator writes the returned report text into `RUN_DIR/review.md` (and `RUN_DIR/fixes_<N>.md`). Never grant write permissions to a read-only role.
- **Scoped Fix Loop (up to 3–5 cycles)**:
  - If `NEEDS FIXES`, re-run only the affected active Coders (preserving each task's same Expert Persona and Coder Model; in Herdr mode, reuse existing tabs) with `RUN_DIR/fixes_<N>.md`. For a tagged same-session follow-up, the receiving coordinator first observes and verifies the current report and reconciles its outcome, then runs `python -m herdr_master.pipeline.parent_contract assign --run-dir <dir> --launch-token <current assignment token> --message "<new assignment>"`. This emits a fresh assignment directive and completion token; send that emitted directive as the new assignment. The current assignment pointer moves forward, so the old report is no longer current evidence; do not send a raw prompt instead. Advance only on the new assignment’s independently observed and verified report and fresh reconciliation. If assignment delivery is uncertain, stop and reconcile rather than replaying it. Untagged shell workers get the next task by exact parent-to-child prompt with a new task correlation; native workers use the native assignment/result path.
  - Relaunch the Reviewer in `MODE: fix-verification`. It checks only whether each finding is resolved and nothing new broke.
  - Apply the same per-Coder `APPROVED` gate after fix verification; retain the approved work before closing the Coder.
  - Once the Reviewer verdict is `APPROVED`, **close the Reviewer tab/agent immediately**.
  - If critical/major issues remain after the cycle cap, STOP, keep `WORKTREE` and `ergo/<run-id>` intact, and report the blocker only to the verified direct parent when delegated (use the terminal route if ending the run). Only an intentional standalone root may report it directly to the user.

### Step 7: Land — Sync `<TARGET_BRANCH>` & Single Squash Commit (MANDATORY squash-merge; NEVER run `git reset --hard`)
Only after the Reviewer verdict is **APPROVED**; for a delegated run preserve the assigned scope:
*(Note: If running as a delegated child session under `/herdr-master` with explicit worktree/branch constraints, commit once on the assigned worktree branch and stop as instructed by `/herdr-master`; master alone integrates into `develop`).* Otherwise, the orchestrator MUST squash-merge the completed work:
1. Delete `RUN_DIR` (after copying `.orchestration/carryover.md` to canonical checkout if present), then in `WORKTREE` on `ergo/<run-id>`:
   `git add -A` and create **exactly one** `git commit -m "<one-line summary>"` (body: plan task list).
2. **Resolve `TARGET_BRANCH`** in the canonical checkout: `develop` > `main` > `master`.
3. **Sync `<TARGET_BRANCH>` onto `ergo/<run-id>` FIRST (NEVER run `git reset --hard`)**:
   - Inside `WORKTREE` (on `ergo/<run-id>`), rebase or merge `<TARGET_BRANCH>`.
   - If conflicts arise, dispatch the affected **Coder** to resolve conflicts on `ergo/<run-id>`, run build/tests, and pass a scoped **Reviewer** (`MODE: fix-verification`) check on resolved files.
   - **NEVER run `git reset --hard`**. If a conflict is ambiguous, STOP and preserve state.
4. **Squash-Merge in the Canonical Checkout**:
   - In canonical checkout: `git checkout <TARGET_BRANCH>` → `git merge --squash ergo/<run-id>` → **one** `git commit -m "<summary>"`.
5. **Cleanup Temporary Traces (MANDATORY)**: **Post-integration cleanup is MANDATORY and automatic** — never optional and never a question to the user; do not ask "do you want cleanup". Cleanup is part of the definition of done. After the squash-merge lands, the parent closes the child tabs/panes it owns, removes the child worktrees with `git worktree remove`, deletes the temporary branches with `git branch -D`, and verifies `git branch --list` and `git worktree list` show no dangling branches or worktrees. Before deleting, confirm the work is genuinely integrated and that no unmerged, unowned or ambiguous content would be lost; if something cannot be verified, preserve it and report the blocker to the direct parent instead of deleting.
   - Remove worktree (`git worktree remove "$WORKTREE"`, never `--force`) and delete temp branch (`git branch -D ergo/<run-id>`) only after confirming the squash landed.
   - Close the workspace only when bootstrap `owns_workspace` is true, and close only that ID (`herdr workspace close <WS_ID>`). Adopted workspaces (`owns_workspace == false`) are NEVER closed.
   - Verify `git branch --list 'ergo/*'` is empty.
6. **Never push and never open a PR** unless explicitly requested.
   - **Remote Ref & Reflog Verification**: Before reporting to the user that a run is unpushed, independently inspect `git log origin/<TARGET_BRANCH> -1`, `git branch -r`, and `git reflog` (or `git ls-remote`). Child agents must never push; independently verify remote heads rather than assuming.
   - **Environment Isolation**: Before running project test suites, clear `PYTHONPATH` (`env -u PYTHONPATH pytest`) to prevent host site-package leakage.

### Step 8: Done & Carryover
- For a genuinely standalone run only, update `<canonical_root>/.orchestration/carryover.md` with intent, branches, squash commit SHA, and constraints; report what was built, `TARGET_BRANCH`, squash commit SHA, and cleanup.
- **Hierarchical Parent Notification (One Level Up)**: For a delegated run, after the assigned branch commit, notify only the direct parent under the shared-session protocol above; report the assigned branch, commit, review and actual verification, not a fictitious integration or cleanup:
  **Launcher-tagged Herdr:** use the current assignment's first-prompt `parent_contract complete` command with `Ergo run <run-id> finished on assigned branch: commit <SHA>, review APPROVED, verification <status>, artifacts <paths>.` as its message. **Untagged Herdr shell:** send `herdr agent prompt <verified-direct-parent-pane-id> "Ergo run <run-id> finished on assigned branch: commit <SHA>, review APPROVED, verification <status>, artifacts <paths>."` to the verified direct parent, correlated to the exact launch/assignment. **Native:** return the same payload through the exact invoking supervisor's native result channel; no Herdr command. Apply the receiving completion gate before advancing.
  Send a nonterminal blocker only through the verified direct parent’s ordinary channel; use the terminal route for a final blocked outcome (`Ergo run <run-id> blocked: <reason and available artifact paths>`). If the parent route is missing or ambiguous, record the blocker and STOP; do not guess a pane, ask the user, integrate, or clean up.

---

## 6. Hard Rules

- **NEVER skip the Scout, the Approval Gate, or the Reviewer.**
- **NEVER code directly in the orchestrator** — Coders only.
- **Only the orchestrator spawns pipeline agents** — workers never spawn subagents.
- **Mandatory Specialist Persona** — every task in `plan.md` must have a tailored `Expert Persona`, and every Coder prompt must start with `Adopt this expert persona: <Expert Persona>.`
- **Explicit Allowed Models Only** — every subagent/Herdr tab gets an explicit model from the active mode's allowed list; any call without a model or outside the list is a pipeline failure.
- **Disjoint file ownership** — two parallel coders never touch the same file; overlapping tasks must be serialized (`DEPENDS ON`) or repartitioned.
- **Read-only roles stay read-only** — Scout, Architect, and Reviewer must never modify repository source files.
- **NEVER run `git reset --hard`** — sync `<TARGET_BRANCH>` inside `ergo/<run-id>` first and resolve conflicts via Coder + Reviewer; if unresolvable, STOP and preserve state.
- **Adopted workspaces are NEVER closed.** Close only when bootstrap `owns_workspace` is true, and only that workspace ID.
- **Single squash commit on `<TARGET_BRANCH>` only after `APPROVED` and only for a genuinely standalone run**; delegated runs never integrate or clean up. Never push, never open a PR unless asked.
- **Never leave completed work unmerged**: Review and integrate approved work **before** cleanup. For a delegated run, the reviewed commit on the assigned branch is the handoff; if integration is genuinely blocked (failing gates, conflicts, missing authorization), preserve the work — the branch, worktree, and commit stay intact — report the blocker to the direct parent, and never discard it.

---

## 7. Agent Briefs

The quoted role messages below are payloads, not commands. Prefix every assembled first prompt with the applicable §4 routing directive: in Herdr give the verified direct parent and a concrete `herdr agent prompt <ORCHESTRATOR_PANE_ID> "<message>"` invocation with the ID replaced; in native give only the exact invoking supervisor's result/steer channel. Apply this to every Scout, Architect, Coder (including parallel coders), Reviewer, and fix-verification request. Preserve the assigned persona, model/effort, scope, and artifacts.

### Scout Brief
**READ-ONLY: never edit source code.** You are the Scout — fast discovery so the Architect doesn't burn context on grep. **Facts, not judgements** — paths, `file:line`, existing patterns, test coverage, and risks. No design recommendations. You write **only** `RUN_DIR/context.md`.
- Read project docs (`AGENTS.md`, `CLAUDE.md`, `.ai/*.md`) first for orientation, then **always verify in source code** (code is truth).
- Answer the orchestrator's 3–5 specific questions first.
- If unsure about something, mark `Coverage: Low` and state what could not be determined — never guess.
- Keep output dense (reference by `file:lineRange`, no multi-line code dumps).

Write `RUN_DIR/context.md` with these sections:
```markdown
## Scout Confidence
- Coverage: <High/Medium/Low> — <gaps if not High>

## Answers to Asked Questions
- <question> → <answer with file:line>

## Relevant Files & Modules
- <path>:<lineRange> — <purpose>

## Existing Patterns & Conventions (cover UI and/or Backend as touched)
- <layer> — <pattern in one sentence> — exemplar at <file>:<line>

## Test Coverage
- Covered: <test paths> | Gaps: <untested areas>

## Risks & Constraints
- <constraints the Architect must design around>
```
- **Notify Direct Parent Orchestrator (Level 3 -> Level 2 ONLY, NEVER AiHead)**: Immediately upon writing `RUN_DIR/context.md`, notify ONLY your direct parent Ergo Orchestrator:
  "Scout finished: context written to RUN_DIR/context.md"
  If you hit a blocker or question requiring a decision, notify ONLY your direct parent via the routing directive in the first prompt:
  "Scout blocked/decision needed: <reason>"

### Architect Brief
**READ-ONLY: never edit source code.** You are the Architect: you design the change AND allocate the specialist personas and coder models to build it. You write **only** `RUN_DIR/plan.md`.
1. Read `RUN_DIR/context.md` first. Explore only gaps or `Coverage: Low` items.
2. Design the smallest complete change that satisfies the intent. Establish the existing project patterns for every touched layer (UI and/or backend), citing each exemplar by `file:line` or doc section. Never invent a pattern where the project already has one.
3. Write `RUN_DIR/plan.md` containing:
   - **Goal** — one paragraph stating the confirmed intent.
   - **Patterns to follow** — per touched layer: `<layer> — <pattern> — source: <file:line | doc section>`. Binding contract for Coders and Reviewer.
   - **Tasks** — numbered. Prefer fewer, cohesive tasks; split only for genuine parallelism or disjoint ownership. Every task **MUST** include:
     - **Description**
     - **Files** (exact paths owned by this task — strictly disjoint from all other parallel tasks)
     - **Dependency**: `INDEPENDENT` or `DEPENDS ON: <task#>`
     - **Expert Persona** (MANDATORY: a specific domain/stack specialist persona tailored to this task, e.g. `"Senior .NET async/await & EF Core specialist"`, `"React/Tailwind UI accessibility & state specialist"`)
     - **Coder Model / Effort** (MANDATORY: choose **only** an exact ID from the `ALLOWED CODER MODELS` block pasted in your prompt, cite that model's `worker_tier`, and give a brief 1-sentence justification)
     - Pick each task's worker tier using the mode and role guidance (P2, P5), and combine tier with effort per P3. For Herdr controlled dispatch, write `RUN_DIR/worker_manifest.json` according to Step 5.
     - **Interfaces** (exact signatures/contracts consumed by other tasks)
     - **Verification** (exact build/test command or behavior check)
     - Select each task's worker tier from the mode guidance (P2 and P5) and combine tier with effort per P3. For Herdr controlled dispatch, write `RUN_DIR/worker_manifest.json` as specified in Step 5.
   - **Out of scope** — what was deliberately excluded.
4. Honor `unitTestsMode` (`none` = plan no test work). Do not implement, review, commit, or spawn.
5. **Notify Direct Parent Orchestrator (Level 3 -> Level 2 ONLY, NEVER AiHead)**: Immediately upon writing `RUN_DIR/plan.md`, notify ONLY your direct parent Ergo Orchestrator:
   "Architect finished: plan written to RUN_DIR/plan.md"
   If you hit a blocker or architectural question requiring a decision, notify ONLY your direct parent via the routing directive in the first prompt:
   "Architect blocked/decision needed: <reason>"

### Coder Brief
You are a specialist Coder implementing **ONE** approved task from `plan.md`.
1. Adopt the **Expert Persona** stated at the top of your prompt before doing anything else.
2. Read your assigned task section in `RUN_DIR/plan.md` (and any upstream `RUN_DIR/coder_<M>_summary.md` listed in `DEPENDS ON`).
3. Modify **ONLY** the files assigned to your task. Honor planned interfaces and cited project patterns strictly — no scope creep, no unrequested abstractions.
4. Run build/compile/targeted test checks where supported. Fix anything your changes broke. Honor `unitTestsMode`.
5. Do **not** run `git commit`, `git push`, `git merge`, `git reset --hard`, or switch branches, and do **not** spawn subagents.
6. Write `RUN_DIR/coder_<N>_summary.md`: files changed (with brief per-file notes), decisions or deviations from the plan (flag prominently), verification run, and anything the Reviewer should scrutinize.
7. **Notify Direct Parent Orchestrator (Level 3 -> Level 2 ONLY, NEVER AiHead)**: Immediately upon saving `coder_<N>_summary.md`, notify ONLY your direct parent orchestrator:
   "Coder <N> finished: <brief outcome>, summary at RUN_DIR/coder_<N>_summary.md, verification passed"
   If you encounter a blocker or question requiring a decision, notify ONLY your direct parent via the routing directive in the first prompt:
   "Coder <N> blocked/decision needed: <reason>"

### Reviewer Brief
In the full review and scoped fix-verification, record an explicit per-Coder `APPROVED` or `NEEDS FIXES` for each Coder whose work was reviewed. Keep a separate overall verdict; do not mark unreviewed work approved.
**READ-ONLY: never edit source code.** You are the final quality gate, independent of the Architect and Coders. You write **only** `RUN_DIR/review.md` and (when fixes are needed) `RUN_DIR/fixes_<N>.md`.

- **When `MODE: fix-verification` is stated in your prompt (fix cycles & conflict-resolution checks only)**:
  Read only the `fixes_*.md` briefs, the coders' updated summaries, and the explicit files listed in your prompt. Judge ONLY whether each finding/conflict is cleanly resolved and nothing new broke in those files. Mark each finding `RESOLVED` or `NOT RESOLVED` with reason. Rewrite `RUN_DIR/review.md` with verdict (`APPROVED` / `NEEDS FIXES`) and refresh `RUN_DIR/fixes_<N>.md` only for unresolved findings. Notify ONLY your direct parent orchestrator (NEVER AiHead): "Reviewer fix-verification finished: verdict <APPROVED|NEEDS FIXES>".

- **Full First-Pass Review**:
  1. Read `RUN_DIR/plan.md` and every `RUN_DIR/coder_<N>_summary.md`. Inspect the actual `git diff` and changed files.
  2. **Plan & Pattern Conformance**: Verify every task is complete, interfaces match, verification criteria are met, and every cited exemplar in **Patterns to follow** was respected.
  3. **Independent Diff-First Correctness Pass**: Go line-by-line over the diff hunting for: logic and off-by-one errors; null/empty/boundary inputs; missing `await`, sync-over-async, disposal/lifetime bugs; swallowed exceptions; money/decimal rounding errors; SQL/EF correctness (N+1, missing index, batch limits, transaction/concurrency bugs); security (OWASP, trust-boundary validation, hardcoded secrets); and reinvented utilities. Do not flag missing tests when `unitTestsMode = none`.
  4. Write `RUN_DIR/review.md` with verdict (**`APPROVED`** or **`NEEDS FIXES`**) and findings (severity + `file:line`).
  5. For each coder needing fixes, write `RUN_DIR/fixes_<N>.md` with concrete, actionable instructions (file, problem, expected fix). Approve when no critical/major issues remain (list minor nitpicks as optional notes).
  6. **Notify Direct Parent Orchestrator (Level 3 -> Level 2 ONLY, NEVER AiHead)**: Immediately upon saving `RUN_DIR/review.md`, notify ONLY your direct parent orchestrator:
     "Reviewer finished: verdict <APPROVED|NEEDS FIXES> at RUN_DIR/review.md"
     If you encounter a blocker or question requiring a decision, notify ONLY your direct parent via the routing directive in the first prompt:
     "Reviewer blocked/decision needed: <reason>"
