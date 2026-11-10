---
name: council
description: "AI Council multi-agent planning protocol: Scout discovery, independent member plans, cross-review, multi-turn synthesis feedback cycles (max 5), consensus check or 1-round vote, and final plan generation."
argument-hint: "[--ai-head|--no-ai-head] [specifications/idea]"
slash: false
metadata:
  opencode/autoinvoke: false
---

# /council — Multi-Agent AI Council Planning & Consensus Protocol

Run an AI Council planning deliberation for: `${@:-$ARGUMENTS}` (if no specification, plan intent, or idea path was supplied, prompt the user for requirements and stop).

The AI Council coordinates multiple specialized agent members to independently draft, cross-review, synthesize, and converge upon an optimal technical architecture and implementation plan.

> **Scope Note:** The AI Council produces an authoritative **plan** (`RUN_DIR/final_plan.md`). It does NOT perform code implementation, commits, or repository integration.

---

## 1. Council Roster & Agent Roles

The Council operates with a strictly partitioned roster:
- **1 Coordinator**: Hosts this canonical skill session (Level 2). Initializes the run, orchestrates state transitions via `herdr-master council-state`, tasks the Scout, distributes context and candidate plans to Council Members, compiles unified feedback, enforces cycle caps, conducts votes when needed, and finalizes the chosen plan. The Coordinator is a neutral facilitator and **never votes**.
- **1 Scout**: Fast codebase discovery specialist (Level 3). Operates in read-only mode to inspect existing code patterns, project guidelines, dependencies, interfaces, and constraints. Produces `RUN_DIR/context.md`. The Scout **never votes**.
- **Dynamic Council Members (> 1)**: Two or more diverse, capable planning agents (Level 3, e.g. `member-1`, `member-2`, `member-3`). Each member drafts an independent plan, reviews peer proposals, contributes constructive feedback, revises plans toward consensus, and casts exactly one ballot if deliberation reaches a tie-breaking vote. In Herdr mode, members run as isolated tabs across supported harnesses (`agy`, `pi`, `claude`, `opencode`, and `hermes` when Hermes catalog support is integrated into the active catalog, via `--kind hermes -- --provider <provider> --model <model> --reasoning <level>` with `--yolo` enabled by default to bypass tool confirmation prompts; pass `--no-skip-permissions` to require interactive prompt approvals). Note: Hermes has no catalog native adapter (`supports_native=False`); council runs involving Hermes members route through Herdr tabs rather than a catalog native adapter. Scope this catalog check strictly to requested Hermes member routes: if a Hermes member is requested and the active catalog lacks `hermes`, report that Hermes catalog support is not yet integrated into the active environment and STOP for that Hermes member route. Standard non-Hermes members (e.g. agy, pi, claude, opencode) do not require Hermes in the catalog and proceed normally. (Within a standalone Hermes host session, `delegate_task` is an internal tool, not a catalog native mode).

### Spawning Role Tabs & Strict One-Level-Up Hierarchy (Non-Blocking Parents)
1. **Non-Blocking Parent Rule**: Parent sessions (including the Council Coordinator) **NEVER block or wait in polling loops** after spawning or prompting children — return immediately after spawning/prompting the Scout or Council Members and resume when each child sends its `herdr agent prompt <COORDINATOR_PANE_ID> ...` notification.
2. **Spawning Level 3 Role Tabs (`Scout` and `Council Members`)**:
   - Create each tab in the adopted workspace (`$HERDR_WORKSPACE_ID`) with `--no-focus`, passing the Coordinator's own pane ID (`$HERDR_PANE_ID` as `COORDINATOR_PANE_ID`) via `--env`:
     `herdr tab create --workspace "$HERDR_WORKSPACE_ID" --cwd "$ORIGIN_CWD" --label "<role-or-member>" --env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID" --no-focus`
   - Start the agent using the exact argv resolved from `herdm catalog agent`:
     `herdr agent start <name> --kind <kind> --pane <PANE_ID> -- <resolved_argv...>`
   - Prompt the agent with `herdr agent prompt <PANE_ID> "<prompt>"` and **return immediately without polling**.
3. **Strict One-Level-Up Hierarchy (NEVER Skip Levels!)**:
   - **Level 3 (`Council Scout` and `Council Members`) -> Level 2 ONLY (`Council Coordinator` at `COORDINATOR_PANE_ID`)**:
     Every prompt sent to the Scout or any Council Member (in Step 1 Scout discovery, Step 2 Round 0 drafting, Step 3 cross-review feedback and plan revision, and Step 4 fallback voting) **MUST** explicitly provide `COORDINATOR_PANE_ID` (`$HERDR_PANE_ID` / child's `$HERDR_PARENT_PANE_ID`) and instruct the child to notify **ONLY** the Council Coordinator via `herdr agent prompt <COORDINATOR_PANE_ID> ...` for **BOTH**:
     - **Completed**: `herdr agent prompt <COORDINATOR_PANE_ID> "<Scout|Member-ID> finished: <artifact path / selected plan / ballot>"`
     - **User / Decision / Blocker Needed**: `herdr agent prompt <COORDINATOR_PANE_ID> "<Scout|Member-ID> blocked/decision needed: <question or blocker>"`
     Level 3 workers **NEVER notify AiHead or Herdr-Master directly!**
   - **Level 2 (`Council Coordinator`) -> Level 1 (`AiHead` or `Herdr-Master` at `HERDR_PARENT_PANE_ID`)**:
     When the Council Coordinator is delegated under a parent (`HERDR_PARENT_PANE_ID` / bootstrap `parent_pane_id`), the Coordinator notifies its direct Level 1 parent via `herdr agent prompt "$HERDR_PARENT_PANE_ID" ...` for **BOTH** `"user/decision needed"` (ambiguous specifications, blockers, approval/consultation) **AND** `"completed"` (`RUN_DIR/final_plan.md` finalized).

---

## 2. State-Gate Coordination Commands (`council-state`)

All protocol transitions are validated, run-scoped, and enforced atomically by the `herdr-master council-state` CLI. The Coordinator **MUST** execute and receive a successful transition before dispatching each round or action, and **MUST stop immediately on gate refusal**.

The state gate enforces:
1. `herdr-master council-state init --run-dir <RUN_DIR> --roster <MEMBER_LIST> [--coordinator <ID>] [--scout <ID>]`
   - Initializes run state and locks the roster.
   - Rejects missing run directories, empty rosters, or rosters with fewer than 2 members.
2. `herdr-master council-state record-scout --run-dir <RUN_DIR> --context <CONTEXT_PATH>`
   - Records Scout codebase discovery context prerequisite. Must be executed before opening Round 0.
3. `herdr-master council-state open-round --run-dir <RUN_DIR> --round <N>`
   - Opens drafting (`round 0`) or feedback/revision cycles (`round 1` through `5`).
   - Strictly rejects `round 6`: the maximum number of feedback cycles is 5.
   - Rejects premature round opening before previous round submissions are complete (and requires Scout context before opening Round 0).
4. `herdr-master council-state submit-plan --run-dir <RUN_DIR> --member <MEMBER_ID> --plan <PLAN_PATH> --revision <REV>`
   - Records an independently drafted (revision 0) or revised (revisions 1–5) plan for a registered member.
   - Validates path containment within `RUN_DIR` and rejects duplicate or stale submissions.
5. `herdr-master council-state submit-feedback --run-dir <RUN_DIR> --round <N> --member <MEMBER_ID> --feedback <FEEDBACK_PATH> --selected-plan <PLAN_ID>`
   - Records member feedback on peer plans along with an explicit preferred plan identifier/revision for the round.
   - Validates that the selected plan ID corresponds to a valid candidate in the active round.
6. `herdr-master council-state consensus --run-dir <RUN_DIR> --round <N>`
   - Verifies whether all council members explicitly support the exact same plan revision in round N.
   - If unanimous consensus is achieved, transitions state to ready for finalization.
   - Rejects false consensus: similar prose or coordinator preference is not consensus.
7. `herdr-master council-state submit-unified-feedback --run-dir <RUN_DIR> --round <N> --feedback <FEEDBACK_PATH>`
   - Records coordinator unified feedback synthesizing member reviews for round N. Prerequisite before members submit round N revisions.
8. `herdr-master council-state vote --run-dir <RUN_DIR> --member <MEMBER_ID> --ballot <CANDIDATE_PLAN_ID>`
   - Conducts a 1-round fallback vote if cycle 5 completes without consensus and all round 5 revisions have been submitted.
   - Council members cast exactly one ballot each. Coordinator and Scout ballots are rejected.
   - Calculates highest tally; breaks ties deterministically using configured candidate/member ordering recorded transparently.
9. `herdr-master council-state finalize --run-dir <RUN_DIR>`
   - Assembles and writes `RUN_DIR/final_plan.md` documenting the chosen plan, complete deliberation history, cycle count, consensus/vote tallies, and provenance.
   - Seals the state; any subsequent mutation attempts are rejected.
10. `herdr-master council-state status --run-dir <RUN_DIR>`
    - Inspects current run status, active round, submitted artifacts, and transition readiness.

---

## 3. Multi-Round Council Protocol

Track and execute the deliberation through the following disciplined sequence:

### Step 1: Initialization & Scout Discovery
1. Create a unique, isolated run directory: `RUN_DIR=.orchestration/council-<run-id>/`.
2. Initialize run state:
   ```bash
   herdr-master council-state init --run-dir "$RUN_DIR" --roster "member-1,member-2,member-3"
   ```
3. Spawn the **Scout** tab (`--env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID" --no-focus`) and direct it to investigate repository structure, existing conventions (`AGENTS.md`, `CLAUDE.md`, `.ai/*.md`), APIs, schemas, and test harnesses. Instruct the Scout to notify **ONLY** the Council Coordinator (`herdr agent prompt <COORDINATOR_PANE_ID> ...`, NEVER AiHead directly) both when `RUN_DIR/context.md` is complete and if any blocker/question arises; return immediately after prompting without polling.
4. Scout produces `RUN_DIR/context.md` detailing verified facts, module paths, interfaces, and architectural constraints, and notifies `<COORDINATOR_PANE_ID>`. Once notified, close the Scout tab immediately (`herdr tab close <scout-tab-id>`).
5. Record Scout codebase discovery context prerequisite (required before Round 0 can be opened):
   ```bash
   herdr-master council-state record-scout --run-dir "$RUN_DIR" --context "$RUN_DIR/context.md"
   ```

### Step 2: Independent Plan Drafting (Round 0)
1. Open the drafting round:
   ```bash
   herdr-master council-state open-round --run-dir "$RUN_DIR" --round 0
   ```
2. Spawn/prompt all Council Member tabs (`--env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID" --no-focus`), distributing the user's specification/intent and `RUN_DIR/context.md` identically to each council member. Instruct each member to notify **ONLY** `<COORDINATOR_PANE_ID>` (NEVER AiHead directly) upon saving `RUN_DIR/plans/<member>_v0.md` or if blocked/needing a decision; return immediately after prompting all members in the turn without polling.
3. Each member independently drafts a comprehensive technical plan without visibility into other members' work.
4. Members save their drafts to `RUN_DIR/plans/<member>_v0.md` and notify `<COORDINATOR_PANE_ID>`.
5. Register each draft:
   ```bash
   herdr-master council-state submit-plan --run-dir "$RUN_DIR" --member "<member>" --plan "$RUN_DIR/plans/<member>_v0.md" --revision 0
   ```

### Step 3: Cross-Review & Iterative Synthesis Feedback Loop (Max 5 Cycles: Rounds 1 to 5)
For feedback cycles 1 through 5:
1. Open the feedback round:
   ```bash
   herdr-master council-state open-round --run-dir "$RUN_DIR" --round "$CYCLE"
   ```
2. **Cross-Review Distribution**:
   - Coordinator distributes every other member's current plan snapshot to each member and returns immediately without polling.
   - Each member reviews peer proposals, identifies strengths, weaknesses, edge cases, and synergy opportunities, and indicates which plan it considers best or synthesizes a stronger merged approach.
   - Each member writes `RUN_DIR/feedback/<member>_round_<CYCLE>.md`, selects an explicit preferred plan, and notifies **ONLY** `<COORDINATOR_PANE_ID>` (`herdr agent prompt <COORDINATOR_PANE_ID> "Member <member> round <CYCLE> feedback finished: preferred <preferred_plan_id>"`, or reports a blocker).
   - Submit feedback:
     ```bash
     herdr-master council-state submit-feedback --run-dir "$RUN_DIR" --round "$CYCLE" --member "<member>" --feedback "$RUN_DIR/feedback/<member>_round_<CYCLE>.md" --selected-plan "<preferred_plan_id>"
     ```
3. **Consensus Check**:
   - Coordinator checks consensus:
     ```bash
     herdr-master council-state consensus --run-dir "$RUN_DIR" --round "$CYCLE"
     ```
   - **Immediate Stop on Consensus**: If all members explicitly agree on the same canonical plan revision, the deliberation succeeds immediately! Proceed directly to **Step 5 (Finalize)**.
4. **Unified Feedback & Revision**:
   - If consensus is not yet achieved:
     - Coordinator synthesizes member reviews into a single unified feedback artifact: `RUN_DIR/feedback/unified_round_<CYCLE>.md`.
     - Coordinator records unified feedback (required by state gate before member revisions are accepted):
       ```bash
       herdr-master council-state submit-unified-feedback --run-dir "$RUN_DIR" --round "$CYCLE" --feedback "$RUN_DIR/feedback/unified_round_<CYCLE>.md"
       ```
     - Coordinator sends this identical unified feedback document to all members and returns immediately without polling.
     - Each member revises its plan addressing the collective feedback, writes `RUN_DIR/plans/<member>_v<CYCLE>.md`, and notifies **ONLY** `<COORDINATOR_PANE_ID>` (`herdr agent prompt <COORDINATOR_PANE_ID> "Member <member> revision v<CYCLE> finished"`, or reports a blocker).
     - Register revised plans:
       ```bash
       herdr-master council-state submit-plan --run-dir "$RUN_DIR" --member "<member>" --plan "$RUN_DIR/plans/<member>_v<CYCLE>.md" --revision "$CYCLE"
       ```
     - If `CYCLE < 5`, advance to the next cycle (`CYCLE + 1`).
     - If `CYCLE == 5` and unanimous consensus was not reached, all members must still complete and submit their Round 5 revised plans before deliberation transitions to **Step 4 (Fallback 1-Round Vote)**.

### Step 4: Fallback 1-Round Vote (After 5 Cycles Without Consensus)
If 5 full feedback cycles conclude without unanimous agreement (and all Round 5 revised plans have been registered):
1. The Coordinator initiates a final single-round vote among candidate plans from the 5th cycle (prompting each member and returning immediately without polling; each member notifies **ONLY** `<COORDINATOR_PANE_ID>` with its ballot).
2. Each council member casts one vote:
   ```bash
   herdr-master council-state vote --run-dir "$RUN_DIR" --member "<member>" --ballot "<candidate_plan_id>"
   ```
3. **Voting Rules**:
   - Only registered Council Members may vote (1 ballot per member).
   - Coordinator and Scout do NOT vote.
   - The candidate plan with the highest tally wins.
   - In case of a tie, deterministic tie-breaking is applied based on the configured member/candidate order and logged in the state record.

### Step 5: Finalization & Plan Artifact
1. Finalize the run (and close all Council Member tabs immediately once finalized):
   ```bash
   herdr-master council-state finalize --run-dir "$RUN_DIR"
   ```
2. The Coordinator presents `RUN_DIR/final_plan.md` to the user, highlighting:
   - Selected plan overview, architecture, and task breakdown.
   - Outcome type: `UNANIMOUS_CONSENSUS` or `MAJORITY_VOTE` (with vote tally and tie-break details if applicable).
   - Cycles utilized (1 to 5).
   - Provenance of ideas synthesized across all member drafts.
3. **Hierarchical Parent Notification (One Level Up — Level 2 -> Level 1)**:
   If this Council run was launched by or has a direct parent orchestrator / AiHead (e.g. `HERDR_PARENT_PANE_ID` / bootstrap `parent_pane_id` / `PARENT_PANE_ID` is set):
   - For **completed** deliberation, the Coordinator **MUST** immediately notify its direct parent session upon finalization:
     `herdr agent prompt "$HERDR_PARENT_PANE_ID" "Council deliberation finished: <outcome> in <N> cycles, final plan at <RUN_DIR>/final_plan.md"`
   - For **user/decision needed** (ambiguous specs, missing requirements, or blockers during the run), the Coordinator **MUST** immediately notify its direct parent session:
     `herdr agent prompt "$HERDR_PARENT_PANE_ID" "Council decision/blocker needed: <question or blocker>"`

---

## 4. AiHead Availability and Autonomous Decisions

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
   - Genuinely ambiguous requirements, conflicting high-level business goals, or missing user specifications must escalate to the user with a concise prompt.
   - **Destructive operations, security bypasses, or out-of-scope actions always require explicit user approval.**

---

## 5. Hard Protocol Rules

- **Maximum 5 Cycles**: Deliberation never exceeds 5 feedback cycles. The `herdr-master council-state` gate strictly rejects any round 6 attempt.
- **Strict Consensus Definition**: Consensus requires all members to explicitly adopt the same canonical plan revision. Similar prose or coordinator preference is NOT consensus.
- **Voting Neutrality**: Coordinator and Scout NEVER vote. Voting is strictly reserved for Council Members.
- **Atomic State Transitions**: Every round opening, submission, consensus check, and vote must be validated through `herdr-master council-state`.
- **Plans Only**: The Council produces planning artifacts only. It never modifies codebase source files, runs commits, or lands branches.
