---
name: council
description: "AI Council multi-agent planning protocol: Scout discovery, independent member plans, cross-review, multi-turn synthesis feedback cycles (max 5), consensus check or 1-round vote, and final plan generation."
argument-hint: "[--ai-head|--no-ai-head] [specifications/idea]"
slash: false
metadata:
  opencode/autoinvoke: false
---

# /council — Multi-Agent AI Council Planning & Consensus Protocol

**Core boundary:** If `HC_TERMINAL_ID` is set or `--backend core` is selected, STOP and report that Council is not yet supported in Herdr Core. Do not launch or use legacy Herdr operations as a fallback. Outside Core, follow the legacy Council workflow below.

Launch with `aihead --council "<intent>"`; `herdm` is deprecated.

Run an AI Council planning deliberation for: `${@:-$ARGUMENTS}` (if no specification, plan intent, or idea path was supplied, prompt the user for requirements and stop).

The AI Council coordinates multiple specialized agent members to independently draft, cross-review, synthesize, and converge upon an optimal technical architecture and implementation plan.

> **Scope Note:** The AI Council produces an authoritative **plan** (`RUN_DIR/final_plan.md`). It does NOT perform code implementation, commits, or repository integration.

---

## 1. Council Roster & Agent Roles

The Council operates with a strictly partitioned roster:
- **1 Coordinator**: Hosts this canonical skill session (Level 2). Initializes the run, orchestrates state transitions via `aihead council-state`, tasks the Scout, distributes context and candidate plans to Council Members, compiles unified feedback, enforces cycle caps, conducts votes when needed, and finalizes the chosen plan. The Coordinator is a neutral facilitator and **never votes**.
- **1 Scout**: Fast codebase discovery specialist (Level 3). Operates in read-only mode to inspect existing code patterns, project guidelines, dependencies, interfaces, and constraints. Produces `RUN_DIR/context.md`. The Scout **never votes**.
- **Dynamic Council Members (> 1)**: Two or more diverse, capable planning agents (Level 3, e.g. `member-1`, `member-2`, `member-3`). Each member drafts an independent plan, reviews peer proposals, contributes constructive feedback, revises plans toward consensus, and casts exactly one ballot if deliberation reaches a tie-breaking vote. In Herdr mode, members run as isolated tabs across supported harnesses (`agy`, `pi`, `claude`, `opencode`, and `hermes` when Hermes catalog support is integrated into the active catalog, via `--kind hermes -- --provider <provider> --model <model> --reasoning <level>` with normal tool confirmations enabled). Note: Hermes has no catalog native adapter (`supports_native=False`); council runs involving Hermes members route through Herdr tabs rather than a catalog native adapter. Scope this catalog check strictly to requested Hermes member routes: if a Hermes member is requested and the active catalog lacks `hermes`, report that Hermes catalog support is not yet integrated into the active environment and STOP for that Hermes member route. Standard non-Hermes members (e.g. agy, pi, claude, opencode) do not require Hermes in the catalog and proceed normally. (Within a standalone Hermes host session, `delegate_task` is an internal tool, not a catalog native mode).

### Spawning Role Tabs & Strict One-Level-Up Hierarchy (Non-Blocking Parents)

**Mandatory first-prompt contract.** Before any delegated Herdr spawn, bind the verified dispatching coordinator's own pane in the exact session; fail closed before spawn if the direct parent is missing or ambiguous. In the first submitted task prompt, replace `<COORDINATOR_PANE_ID>` with that concrete pane ID in both the parent declaration and each `herdr agent prompt <COORDINATOR_PANE_ID> "<message>"` command. The child sends every question, decision or approval request, blocker, and completion to that parent only; never contact any other pane or the user. Environment and bootstrap metadata only corroborate the first prompt; a follow-up cannot supply the missing directive. An intentional parentless root uses its own user/result channel, not an invented pane.
1. **Non-Blocking Parent Rule**: Parent sessions (including the Council Coordinator) **NEVER block or wait in polling loops** after spawning or prompting children — return immediately after spawning/prompting the Scout or Council Members and resume when each child sends its `herdr agent prompt <COORDINATOR_PANE_ID> ...` notification.
2. **Spawning Level 3 Role Tabs (`Scout` and `Council Members`)**:
   - Create each tab in the adopted workspace (`$HERDR_WORKSPACE_ID`) with `--no-focus`, passing the Coordinator's own pane ID (`$HERDR_PANE_ID` as `COORDINATOR_PANE_ID`) via `--env`:
     `herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd "$ORIGIN_CWD" --label "<role-or-member>" --env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID" --no-focus`
   - Start the agent using the model and effort argv resolved from `aihead catalog agent`. Drop legacy permission-bypass flags; never use them without exact user authorization:
     `herdr agent start <name> --kind <kind> --pane <PANE_ID> -- <resolved_argv...>`
   - Submit the complete first prompt with `herdr agent prompt <PANE_ID> "<prompt>"`. Verify startup, register the child, and start/ensure the watcher below; **return immediately without polling**.
   - Apply this recipe to the Scout, initial member drafts, and any authorized replacement during drafting, feedback, revision, or voting. A replacement needs verified termination/non-delivery, the coordinator's approval within the locked roster and state gate, and the applicable current round's brief; never auto-replace a member from a health alert.
3. **Strict One-Level-Up Hierarchy (NEVER Skip Levels!)**:
   - **Level 3 (`Council Scout` and `Council Members`) -> Level 2 ONLY (`Council Coordinator` at `COORDINATOR_PANE_ID`)**:
     Every prompt sent to the Scout or any Council Member (in Step 1 Scout discovery, Step 2 Round 0 drafting, Step 3 cross-review feedback and plan revision, and Step 4 fallback voting) **MUST** explicitly provide `COORDINATOR_PANE_ID` (`$HERDR_PANE_ID` / child's `$HERDR_PARENT_PANE_ID`) and instruct the child to notify **ONLY** its verified Council Coordinator. Apply these routes:
     - **Completed**: **Launcher-tagged Herdr:** use the current assignment's first-prompt `parent_contract complete` command with `<Scout|Member-ID> finished: <artifact path / selected plan / ballot>` as its message. **Untagged Herdr shell:** send `herdr agent prompt <COORDINATOR_PANE_ID> "<Scout|Member-ID> finished: <artifact path / selected plan / ballot>"` to the verified direct parent, correlated to the exact launch/assignment. **Native:** return the same payload through the exact invoking supervisor's native result channel; no Herdr command. Apply the receiving completion gate before advancing.
     - **User / Decision / Blocker Needed**: `herdr agent prompt <COORDINATOR_PANE_ID> "<Scout|Member-ID> blocked/decision needed: <question or blocker>"`
     Level 3 workers **NEVER notify AiHead, Herdr-Master, or the user directly!**
   - **Level 2 (`Council Coordinator`) -> Level 1 (`AiHead` or `Herdr-Master`)**:
     The Coordinator routes decisions and untagged messages to the concrete direct-parent pane named in its own first prompt; `HERDR_PARENT_PANE_ID` / bootstrap `parent_pane_id` is context only and must never replace that pane as the notification target. Use the decision and transport-specific terminal routes in Step 5.

---

**Completion gate (Herdr children).** A Herdr child is not finished unless it has sent its terminal completion report via `herdr agent prompt <direct-parent-pane-id> "<outcome and required artifacts>"` to its verified direct parent, using the concrete pane ID from its first prompt. The parent withholds `done` and terminal completion acknowledgement until it receives that report and verifies the required outcome and artifacts. A file on disk, pane status or exit, and watcher alerts never substitute for the report. If the report is missing or delivery is uncertain, record the child as pending or blocked; do not advance any gate that depends on its completion. For a Python-launcher-tagged child, run the exact shell-quoted completion command and interpreter from the first prompt: `python -m herdr_master.pipeline.parent_contract complete --run-dir <launcher-provided-dir> --launch-token <launcher-provided-token> --message "<terminal outcome and required artifact paths>"`. The helper makes one actual direct-parent `herdr agent prompt` RPC, prefixes the launch correlation, checks the exact bound live parent pane/session and generation in the acceptance response, and persists a private correlated receipt. The receiver verifies the exact full received terminal payload (including the `HERDR TERMINAL REPORT` header) against the current assignment’s accepted-send receipt digest and tagged registry entry; acceptance alone is not parent observation. For a launcher-managed tagged row, after receiving the incoming `HERDR TERMINAL REPORT`, the receiving parent must run `python -m herdr_master.pipeline.parent_contract verify --run-dir <launcher-provided-dir> --report-id <id in incoming HERDR TERMINAL REPORT> --message "<exact full received HERDR TERMINAL REPORT payload, including header>"` before marking the child `done`. This verifies the exact parent environment, tagged ledger entry, and current assignment’s accepted-send receipt against the exact full received payload, including its header; the verifier requires `--message` to be the exact received payload. Do not paraphrase. If the report arrives before its receipt, recheck verification, not the send. If verification fails, withhold `done`; verification does not replace observing the incoming report or checking the required outcome and artifacts. This verification command does not apply to untagged shell launches. For a plain shell launch without a launcher token, send the actual prompt RPC directly and require the incoming report to correlate to the exact child launch; do not invoke the tagged helper or invent a receipt. In both cases, transport acceptance alone is not evidence the parent observed the report. The parent must observe and reconcile the report before marking `done`. This shared-session route does not authenticate the sender or make target validation and delivery atomic. If the applicable command or correct route is unavailable, keep completion pending; do not substitute artifacts.

**Run-scoped child health watcher (Herdr only).** Use a fresh, private, current-user-owned mode-700 `RUN_DIR` (`mkdir -m 700` or `mktemp -d`); never reuse it for another run. Record ambiguous launch attempts separately; never assume delivery or automatically retry. After registering the first confirmed child, start one detached process in the exact verified Herdr session environment (not a pane, subagent, or ledger entry). Set `RUN_ID_HEX` to a fresh 32-character lowercase hex UUID for this independent run and retain it for all further launches in the run. Python-launcher-managed runs use the launcher-provided run identity and its exact `launcher-run.json` binding; do not start a parallel shell watcher for those runs. Shell-owned example:
```bash
nohup python -m herdr_master.pipeline.child_watch --run-dir "$RUN_DIR" \
  --parent-pane "$PARENT" --parent-generation "$PARENT_GENERATION" \
  --run-id "$RUN_ID_HEX" --startup-timeout 5 \
  >"$RUN_DIR/child-watch.log" 2>&1 </dev/null &
```
Here `PARENT` is this spawning coordinator's verified pane, and `PARENT_GENERATION` is its stable `terminal_id` or `agent_session.value` from the exact pane query. Pin it for delayed/detached handoff. The owner keeps watching across all-terminal snapshots; later registrations and assignments use the same live watcher. `child-watch.lock` prevents overlap. Verify bound ownership with `herdr_master.pipeline.child_watch.watcher_ready(run_dir, run_id=..., parent_pane_id=..., parent_generation=...)`; a PID or successful detach is not readiness. Use a bounded startup handoff (5s startup budget; launcher waits at most 6s), never a health-interval poll. If readiness fails, report missing supervision and do not duplicate a child launch. `child-watch-state.json` persists notification deduplication; `child-watch.pid` is diagnostic, not authority to kill a process. At run teardown use `touch "$RUN_DIR/child-watch.stop"`; do not remove the sentinel to reuse a torn-down run. It stops on explicit teardown or loss of the bound parent, not when all children are terminal. If supervision cannot be established, report that limitation instead of claiming coverage.

Every 600 seconds the watcher checks only confirmed registered children. Positively absent, exited, or replaced children without accepted-send evidence or a reconciled terminal outcome are reported once per identity to this spawning parent only. Idle, busy, or blocked does not mean dead; transient RPC/transport errors mean unknown. For tagged children `completion_accepted` only suppresses missing-send alerts. Only the current receipt plus explicit parent ledger reconciliation (`acknowledged` or `reconciled`, not generic `completed` or `closed`) counts as `acknowledged`. A health event is not a completion callback, a work verdict, or permission to retry. An artifact alone is not success. The read-only watcher never closes panes, merges, pushes, or edits child work; it never changes the ledger or approves anything. Herdr notification is not atomic with identity validation: the watcher revalidates before sending and checks the returned identity, but a check-to-send race remains. Parents never block or poll.

## Pane launch verification (mandatory)

A spawn succeeded only when `herdr agent get <pane>` reports `agent_status: working` and `herdr agent read <pane>` shows the expected model with no startup, permission, or credit error. `agent_not_found` (or a vanished pane) means the launch FAILED. Never report a started job without that evidence; report the model slug, `agent_status`, and context usage.

---

## 2. State-Gate Coordination Commands (`council-state`)

All protocol transitions are validated, run-scoped, and enforced atomically by the `aihead council-state` CLI. The Coordinator **MUST** execute and receive a successful transition before dispatching each round or action, and **MUST stop immediately on gate refusal**.

The state gate enforces:
1. `aihead council-state init --run-dir <RUN_DIR> --roster <MEMBER_LIST> [--coordinator <ID>] [--scout <ID>]`
   - Initializes run state and locks the roster.
   - Rejects missing run directories, empty rosters, or rosters with fewer than 2 members.
2. `aihead council-state record-scout --run-dir <RUN_DIR> --context <CONTEXT_PATH>`
   - Records Scout codebase discovery context prerequisite. Must be executed before opening Round 0.
3. `aihead council-state open-round --run-dir <RUN_DIR> --round <N>`
   - Opens drafting (`round 0`) or feedback/revision cycles (`round 1` through `5`).
   - Strictly rejects `round 6`: the maximum number of feedback cycles is 5.
   - Rejects premature round opening before previous round submissions are complete (and requires Scout context before opening Round 0).
4. `aihead council-state submit-plan --run-dir <RUN_DIR> --member <MEMBER_ID> --plan <PLAN_PATH> --revision <REV>`
   - Records an independently drafted (revision 0) or revised (revisions 1–5) plan for a registered member.
   - Validates path containment within `RUN_DIR` and rejects duplicate or stale submissions.
5. `aihead council-state submit-feedback --run-dir <RUN_DIR> --round <N> --member <MEMBER_ID> --feedback <FEEDBACK_PATH> --selected-plan <PLAN_ID>`
   - Records member feedback on peer plans along with an explicit preferred plan identifier/revision for the round.
   - Validates that the selected plan ID corresponds to a valid candidate in the active round.
6. `aihead council-state consensus --run-dir <RUN_DIR> --round <N>`
   - Verifies whether all council members explicitly support the exact same plan revision in round N.
   - If unanimous consensus is achieved, transitions state to ready for finalization.
   - Rejects false consensus: similar prose or coordinator preference is not consensus.
7. `aihead council-state submit-unified-feedback --run-dir <RUN_DIR> --round <N> --feedback <FEEDBACK_PATH>`
   - Records coordinator unified feedback synthesizing member reviews for round N. Prerequisite before members submit round N revisions.
8. `aihead council-state vote --run-dir <RUN_DIR> --member <MEMBER_ID> --ballot <CANDIDATE_PLAN_ID>`
   - Conducts a 1-round fallback vote if cycle 5 completes without consensus and all round 5 revisions have been submitted.
   - Council members cast exactly one ballot each. Coordinator and Scout ballots are rejected.
   - Calculates highest tally; breaks ties deterministically using configured candidate/member ordering recorded transparently.
9. `aihead council-state finalize --run-dir <RUN_DIR>`
   - Assembles and writes `RUN_DIR/final_plan.md` documenting the chosen plan, complete deliberation history, cycle count, consensus/vote tallies, and provenance.
   - Seals the state; any subsequent mutation attempts are rejected.
10. `aihead council-state status --run-dir <RUN_DIR>`
    - Inspects current run status, active round, submitted artifacts, and transition readiness.

---

## 3. Multi-Round Council Protocol

Track and execute the deliberation through the following disciplined sequence:

### Step 1: Initialization & Scout Discovery
1. Create a private, unique, current-user-owned mode-700 run directory, e.g. `RUN_DIR=.orchestration/council-<run-id>/`; do not reuse it.
2. Initialize run state:
   ```bash
   aihead council-state init --run-dir "$RUN_DIR" --roster "member-1,member-2,member-3"
   ```
3. Spawn the **Scout** tab (`--env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID" --no-focus`) and direct it to investigate repository structure, existing conventions (`AGENTS.md`, `CLAUDE.md`, `.ai/*.md`), APIs, schemas, and test harnesses. Instruct the Scout to notify **ONLY** the Council Coordinator (`herdr agent prompt <COORDINATOR_PANE_ID> ...`, NEVER AiHead directly) both when `RUN_DIR/context.md` is complete and if any blocker/question arises; return immediately after prompting without polling.
4. Scout produces `RUN_DIR/context.md` detailing verified facts, module paths, interfaces, and architectural constraints, and notifies `<COORDINATOR_PANE_ID>`. Once notified, close the Scout tab immediately (`herdr tab close <scout-tab-id>`).
5. Record Scout codebase discovery context prerequisite (required before Round 0 can be opened):
   ```bash
   aihead council-state record-scout --run-dir "$RUN_DIR" --context "$RUN_DIR/context.md"
   ```

### Step 2: Independent Plan Drafting (Round 0)
1. Open the drafting round:
   ```bash
   aihead council-state open-round --run-dir "$RUN_DIR" --round 0
   ```
2. Spawn/prompt all Council Member tabs (`--env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID" --no-focus`), distributing the user's specification/intent and `RUN_DIR/context.md` identically to each council member. Instruct each member to notify **ONLY** `<COORDINATOR_PANE_ID>` (NEVER AiHead directly) upon saving `RUN_DIR/plans/<member>_v0.md` or if blocked/needing a decision; return immediately after prompting all members in the turn without polling.
3. Each member independently drafts a comprehensive technical plan without visibility into other members' work.
4. Members save their drafts to `RUN_DIR/plans/<member>_v0.md` and notify `<COORDINATOR_PANE_ID>`.
5. Register each draft:
   ```bash
   aihead council-state submit-plan --run-dir "$RUN_DIR" --member "<member>" --plan "$RUN_DIR/plans/<member>_v0.md" --revision 0
   ```

### Step 3: Cross-Review & Iterative Synthesis Feedback Loop (Max 5 Cycles: Rounds 1 to 5)
For feedback cycles 1 through 5:
1. Open the feedback round:
   ```bash
   aihead council-state open-round --run-dir "$RUN_DIR" --round "$CYCLE"
   ```
2. **Cross-Review Distribution**:
   - Coordinator distributes every other member's current plan snapshot to each member and returns immediately without polling.
   - Each member reviews peer proposals, identifies strengths, weaknesses, edge cases, and synergy opportunities, and indicates which plan it considers best or synthesizes a stronger merged approach.
   - Each member writes `RUN_DIR/feedback/<member>_round_<CYCLE>.md`, selects an explicit preferred plan, and notifies **ONLY** `<COORDINATOR_PANE_ID>` (`herdr agent prompt <COORDINATOR_PANE_ID> "Member <member> round <CYCLE> feedback finished: preferred <preferred_plan_id>"`, or reports a blocker).
   - Submit feedback:
     ```bash
     aihead council-state submit-feedback --run-dir "$RUN_DIR" --round "$CYCLE" --member "<member>" --feedback "$RUN_DIR/feedback/<member>_round_<CYCLE>.md" --selected-plan "<preferred_plan_id>"
     ```
3. **Consensus Check**:
   - Coordinator checks consensus:
     ```bash
     aihead council-state consensus --run-dir "$RUN_DIR" --round "$CYCLE"
     ```
   - **Immediate Stop on Consensus**: If all members explicitly agree on the same canonical plan revision, the deliberation succeeds immediately! Proceed directly to **Step 5 (Finalize)**.
4. **Unified Feedback & Revision**:
   - If consensus is not yet achieved:
     - Coordinator synthesizes member reviews into a single unified feedback artifact: `RUN_DIR/feedback/unified_round_<CYCLE>.md`.
     - Coordinator records unified feedback (required by state gate before member revisions are accepted):
       ```bash
       aihead council-state submit-unified-feedback --run-dir "$RUN_DIR" --round "$CYCLE" --feedback "$RUN_DIR/feedback/unified_round_<CYCLE>.md"
       ```
     - Coordinator sends this identical unified feedback document to all members and returns immediately without polling.
     - Each member revises its plan addressing the collective feedback, writes `RUN_DIR/plans/<member>_v<CYCLE>.md`, and notifies **ONLY** `<COORDINATOR_PANE_ID>` (`herdr agent prompt <COORDINATOR_PANE_ID> "Member <member> revision v<CYCLE> finished"`, or reports a blocker).
     - Register revised plans:
       ```bash
       aihead council-state submit-plan --run-dir "$RUN_DIR" --member "<member>" --plan "$RUN_DIR/plans/<member>_v<CYCLE>.md" --revision "$CYCLE"
       ```
     - If `CYCLE < 5`, advance to the next cycle (`CYCLE + 1`).
     - If `CYCLE == 5` and unanimous consensus was not reached, all members must still complete and submit their Round 5 revised plans before deliberation transitions to **Step 4 (Fallback 1-Round Vote)**.

### Step 4: Fallback 1-Round Vote (After 5 Cycles Without Consensus)
If 5 full feedback cycles conclude without unanimous agreement (and all Round 5 revised plans have been registered):
1. The Coordinator initiates a final single-round vote among candidate plans from the 5th cycle (prompting each member and returning immediately without polling; each member notifies **ONLY** `<COORDINATOR_PANE_ID>` with its ballot).
2. Each council member casts one vote:
   ```bash
   aihead council-state vote --run-dir "$RUN_DIR" --member "<member>" --ballot "<candidate_plan_id>"
   ```
3. **Voting Rules**:
   - Only registered Council Members may vote (1 ballot per member).
   - Coordinator and Scout do NOT vote.
   - The candidate plan with the highest tally wins.
   - In case of a tie, deterministic tie-breaking is applied based on the configured member/candidate order and logged in the state record.

### Step 5: Finalization & Plan Artifact

Read-only Scout discovery and deliberation do not require prior plan approval. Consensus or a vote selects a candidate, not authorization. Before endorsing or taking action on the resulting exact plan version, present the selected artifact with its run/version for approval.

**Plan approval authority.** Only AiHead acting within its authorized scope or the human may explicitly approve a gated plan. A delegated coordinator sends the exact plan, run ID and plan version to its verified direct parent and waits for an explicit `APPROVED` from AiHead or the human, relayed one level at a time. Intermediaries may review, request revisions or relay the original decision with its provenance; they cannot mint approval. Coordinator self-approval, silence, a recommendation, or an approval for a different version is invalid. A changed plan requires fresh approval. Standalone coordinators use an explicitly enabled AiHead route when supported, or ask the human and wait; never use either to bypass a delegated parent. A routine automode plan that does not trigger a gate is a nonblocking update, not an approval.

1. After the gate, finalize the run (and close all Council Member tabs immediately once finalized):
   ```bash
   aihead council-state finalize --run-dir "$RUN_DIR"
   ```
2. **Pane/Agent Lifecycle Ownership**: Only the direct parent closes a pane/agent — the Coordinator closes its own Scout and Council Member tabs, and no pane closes itself and no agent closes its own tab. AiHead (or the delegating parent) closes the Coordinator pane once the Council reports completion, and AiHead (or the level above) owns final integration of the selected plan; the Council lands no code.
3. **Never leave completed work unmerged**: Finalize the plan and hand `RUN_DIR/final_plan.md` to the direct parent for integration **before** cleanup. If the run is genuinely blocked (missing requirements, failed consensus gates, missing authorization), preserve the work — the run directory and plan artifacts stay intact — report the blocker to the direct parent, and never discard it.
   **Post-integration cleanup is MANDATORY and automatic** — never optional and never a question to the user; do not ask "do you want cleanup". Cleanup is part of the definition of done. After the selected plan's implementation is squash-merged, the parent closes the child tabs/panes it owns, removes the child worktrees with `git worktree remove`, deletes the temporary branches with `git branch -D`, and verifies `git branch --list` and `git worktree list` show no dangling branches or worktrees. Before deleting, confirm the work is genuinely integrated and that no unmerged, unowned or ambiguous content would be lost; if something cannot be verified, preserve it and report the blocker to the direct parent instead of deleting.
4. **Children registry (`RUN_DIR/children.tsv`)**: Create it when spawning the first child. One tab-separated line per child: `pane_id`, `role`, `tab_id`, `branch`, `worktree`, `run_dir`, `status`, `last_heartbeat`. Only the parent writes it. For Herdr, append the optional ninth field from the exact verified child pane: stable `terminal_id` or `agent_session.value`; The canonical child `pane_id` determines the child workspace (which may differ from the parent workspace); `tab_id` must belong to that child workspace. Launcher-tagged children keep those nine fields unchanged and add the literal tenth field `launcher-completion-v1`; the launch token stays in private launcher metadata, never in the TSV. For tagged rows, use `herdr_master.pipeline.parent_contract.completion_receipt_accepted(run_dir, parent_pane_id=..., child_pane_id=...)` to check the private correlated `agent_prompted` RPC receipt for the exact parent and child identities. This reads the current assignment pointer and checks its accepted-send receipt; it is transport evidence, not parent observation or sender authentication, and does not replace the parent completion gate. The watcher probes only the exact registered child in the verified shared session; it does not enumerate workspaces. Register confirmed children before arming supervision, and update the ledger with `status=acknowledged` only after reconciling a terminal outcome, or `status=closed` when actually closed. Remove a row only after that child is closed and its work is integrated.
5. **Open and close update the list**: Opening a child session records a launch attempt; only a verified, confirmed launch is eligible for monitoring. Closing that session updates the same row in the same action: `status=closed`, the pane and tab ids, and `last_heartbeat`. Do not leave a closed session marked `working`. A missing update is a skill failure, not a later cleanup task.
6. Apply the run-scoped child health watcher above from the first confirmed Scout launch, not from finalization.
7. Stop it at finalization, cancellation, or abort.
8. A parentless Coordinator presents `RUN_DIR/final_plan.md` to the user. A delegated Coordinator reports only to its direct parent. Highlight:
   - Selected plan overview, architecture, and task breakdown.
   - Outcome type: `UNANIMOUS_CONSENSUS` or `MAJORITY_VOTE` (with vote tally and tie-break details if applicable).
   - Cycles utilized (1 to 5).
   - Provenance of ideas synthesized across all member drafts.
9. **Hierarchical Parent Notification (One Level Up — Level 2 -> Level 1)**:
   If this Council run has a verified direct parent, send every question, decision, approval request, and blocker only to that parent through its ordinary Herdr prompt or the exact native invoker's message channel. A terminal result is distinct from a progress/question message:
   - **Launcher-tagged Herdr:** use the current assignment's first-prompt `parent_contract complete` command with `Council deliberation finished: <outcome> in <N> cycles, final plan at <RUN_DIR>/final_plan.md` as its message. Do not send a raw terminal `herdr agent prompt`.
   - **Untagged Herdr shell:** send the same terminal payload with `herdr agent prompt <verified-direct-parent-pane-id> "Council deliberation finished: <outcome> in <N> cycles, final plan at <RUN_DIR>/final_plan.md"`, correlated to the exact launch/assignment.
   - **Native:** return the same terminal payload through the exact invoking supervisor's native result channel; no Herdr command.
   The receiving parent must observe and verify the report and required artifacts before marking completion. For a nonterminal blocker, use the ordinary direct-parent channel; if the run ends blocked, report that as the terminal outcome using the transport-specific terminal route above. Only a verified parentless root reports directly to the user.

---

## 4. AiHead Availability and Autonomous Decisions

In automode, resolve routine, reversible in-scope decisions without asking the user. Batch consequential architectural or product questions into one plan checkpoint when possible: state the rationale, options, recommended default, and impact. Routine plans may be nonblocking updates; non-automode and explicitly gated runs retain their approval gates. Outside the checkpoint, pause affected work only when it cannot safely proceed or requires authorization; unrelated safe work may continue. Technical plan assent does not grant trust or permission. Irreversible destruction and security bypass require explicit user approval for the specific action and target, relayed one level at a time; no silence, preselected dialog, or technical assent substitutes. Use existing normal trust configuration as-is. Do not add trust, auto-confirm a prompt, or weaken permissions. If blocked, send the least-privilege alternative through the direct parent; a denial or timeout leaves state unchanged.

The Council protocol supports automated, intelligent decision consultation via AiHead when enabled:

1. **Enablement & Tri-State Flag**:
   - `--ai-head`: Explicitly enable AiHead consultation.
   - `--no-ai-head`: Explicitly disable AiHead consultation.
   - Default: Discovered from project configuration (`.herdr-master.toml`) or launcher bootstrap context descriptor.
2. **Consultation Modes**:
   - **TUI Mode**: Consults AiHead via its persistent Herdr tab (labelled `ai-head`) in the active workspace. Automated requests are serialized with unique correlation IDs.
   - **CLI Mode (100% Herdr-free)**: Direct execution without Herdr. Commands or harnesses calling each other invoke the harness directly via its native headless CLI runner (e.g. `opencode run`, `agy`, etc.) or native agent tool call in a direct subprocess without any Herdr daemon, socket, or workspace dependencies. Harnesses can call each other directly without Herdr. (Note: Hermes does not provide a headless CLI runner; direct consultation involving Hermes uses interactive Herdr tabs or direct subagent tools, not headless CLI invocation).
3. **Correlated Requests & Structured Decisions**:
   - Every request to AiHead contains a unique correlation ID, run context, and specific technical question.
   - AiHead returns a structured decision object containing: `{ "decision": "...", "rationale": "...", "escalated": false }`.
4. **Delegated Scope & User Escalation**:
   - Autonomous decisions are accepted for routine technical and architectural choices within the council's delegated scope.
   - Genuinely ambiguous requirements, conflicting high-level business goals, or missing user specifications must go through the direct parent if delegated; only a standalone coordinator may ask the user directly.
   - **Destructive operations, security bypasses, or out-of-scope actions always require explicit user approval.**

---

## 5. Hard Protocol Rules

- **Maximum 5 Cycles**: Deliberation never exceeds 5 feedback cycles. The `aihead council-state` gate strictly rejects any round 6 attempt.
- **Strict Consensus Definition**: Consensus requires all members to explicitly adopt the same canonical plan revision. Similar prose or coordinator preference is NOT consensus.
- **Voting Neutrality**: Coordinator and Scout NEVER vote. Voting is strictly reserved for Council Members.
- **Atomic State Transitions**: Every round opening, submission, consensus check, and vote must be validated through `aihead council-state`.
- **Plans Only**: The Council produces planning artifacts only. It never modifies codebase source files, runs commits, or lands branches.
