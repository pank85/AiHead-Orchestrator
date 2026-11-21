# `/council` — Multi-Agent AI Council Planning & Consensus Protocol

The `/council` skill coordinates a multi-agent architectural deliberation protocol. Multiple specialized AI agents independently draft, cross-review, synthesize, and converge upon an optimal technical architecture and implementation plan.

> [!IMPORTANT]
> **Strict Planning Scope**: The AI Council produces an authoritative **plan** (`RUN_DIR/final_plan.md`) **ONLY**. It does NOT perform code implementation, create commits, or merge branches.

---

## 1. Council Roster & Agent Roles

The Council operates with a strictly partitioned, multi-level hierarchy:

```
+─────────────────────────────────────────────────────────────────────────────+
|                     Council Coordinator (Level 2)                           |
|       (Neutral facilitator; drives state transitions; NEVER votes)          |
+─────────────────────────────────────────────────────────────────────────────+
         │                                       │
         ▼                                       ▼
+─────────────────+              +───────────────────────────────+
|  Scout (Level 3)|              | Council Members (Level 3)     |
|  - Read-only    |              | - Member 1 (e.g. Anthropic)   |
|  - context.md   |              | - Member 2 (e.g. OpenAI)      |
|  - NEVER votes  |              | - Member 3 (e.g. xAI / Google)|
+─────────────────+              +───────────────────────────────+
```

### 1. Coordinator (Level 2)
- Facilitates deliberation, manages state transitions via `aihead council-state`, distributes artifacts, synthesizes unified feedback, enforces cycle caps, conducts votes if needed, and finalizes the chosen plan.
- **The Coordinator is strictly neutral and NEVER votes.**

### 2. Scout (Level 3)
- Fast codebase discovery specialist operating in read-only mode.
- Inspects repository conventions (`AGENTS.md`, `CLAUDE.md`, `.ai/*.md`), APIs, schemas, and test harnesses.
- Produces `RUN_DIR/context.md`.
- **The Scout NEVER votes.**

### 3. Dynamic Council Members ($\ge$ 2) (Level 3)
- Two or more capable planning agents across diverse harnesses (e.g., `member-1`, `member-2`, `member-3`).
- In Herdr mode, members run as isolated tabs across supported harnesses (`agy`, `pi`, `claude`, `opencode`, and `hermes`).
- Each member independently drafts an initial plan (Round 0), cross-reviews peer proposals (Rounds 1–5), revises plans toward consensus, and casts exactly one ballot if deliberation reaches a tie-breaking vote.

---

## 2. State-Gate Coordination Engine (`council-state`)

All protocol transitions are validated, run-scoped, and enforced atomically by the `aihead council-state` CLI. The Coordinator must execute and receive a successful transition before dispatching each round or action, and must stop immediately on gate refusal.

```
       init
         │
         ▼
    record-scout
         │
         ▼
    open-round (Round 0) ──► submit-plan (Rev 0)
         │
         ▼
  ┌─► open-round (Round N: 1..5)
  │      │
  │      ▼
  │   submit-feedback (with --selected-plan)
  │      │
  │      ▼
  │   consensus? ───────────► YES ──────────┐
  │      │                                  │
  │      ▼ NO                               │
  │   submit-unified-feedback               │
  │      │                                  │
  │      ▼                                  │
  │   submit-plan (Rev N)                   │
  │      │                                  │
  │      ▼                                  │
  └── Cycle < 5?                            │
         │                                  │
         ▼ NO (Cycle 5 exhausted)           │
       vote (1-round fallback)              │
         │                                  │
         ▼                                  ▼
      finalize ─────────────────────────────┘
         │
         ▼
   final_plan.md
```

### CLI Command Reference
1. **Initialize State**:
   ```bash
   aihead council-state init --run-dir <RUN_DIR> --roster "member-1,member-2,member-3" [--coordinator <ID>] [--scout <ID>]
   ```
   Locks run configuration; rejects rosters with fewer than 2 members.
2. **Record Scout Context**:
   ```bash
   aihead council-state record-scout --run-dir <RUN_DIR> --context <CONTEXT_PATH>
   ```
   Records discovery prerequisite; required before opening Round 0.
3. **Open Round**:
   ```bash
   aihead council-state open-round --run-dir <RUN_DIR> --round <N>
   ```
   Opens drafting (`0`) or revision cycles (`1`–`5`). **Strictly rejects `round 6`**.
4. **Submit Plan Revision**:
   ```bash
   aihead council-state submit-plan --run-dir <RUN_DIR> --member <ID> --plan <PATH> --revision <REV>
   ```
   Records an independent draft (revision 0) or revised proposal (revisions 1–5).
5. **Submit Feedback**:
   ```bash
   aihead council-state submit-feedback --run-dir <RUN_DIR> --round <N> --member <ID> --feedback <PATH> --selected-plan <PLAN_ID>
   ```
   Records member critique and preferred plan ID for that cycle.
6. **Check Consensus**:
   ```bash
   aihead council-state consensus --run-dir <RUN_DIR> --round <N>
   ```
   Verifies whether all members explicitly support the exact same plan revision.
7. **Submit Unified Feedback**:
   ```bash
   aihead council-state submit-unified-feedback --run-dir <RUN_DIR> --round <N> --feedback <PATH>
   ```
   Records coordinator synthesis of peer reviews; required before members submit revisions.
8. **Fallback Vote**:
   ```bash
   aihead council-state vote --run-dir <RUN_DIR> --member <ID> --ballot <CANDIDATE_PLAN_ID>
   ```
   Conducts 1-round vote if Cycle 5 ends without consensus.
9. **Finalize**:
   ```bash
   aihead council-state finalize --run-dir <RUN_DIR>
   ```
   Assembles `RUN_DIR/final_plan.md` and seals run state against further mutations.
10. **Status**:
    ```bash
    aihead council-state status --run-dir <RUN_DIR>
    ```

---

## 3. Multi-Round Council Deliberation Sequence

### Step 1: Initialization & Scout Discovery
1. Create unique run directory: `RUN_DIR=.orchestration/council-<run-id>/`.
2. Initialize state: `aihead council-state init --run-dir "$RUN_DIR" --roster "member-1,member-2,member-3"`.
3. Spawn **Scout** tab with `--no-focus` and `--env "HERDR_PARENT_PANE_ID=$HERDR_PANE_ID"`.
4. Scout produces `RUN_DIR/context.md` covering repository conventions, APIs, schemas, and constraints, then notifies `<COORDINATOR_PANE_ID>`.
5. Close Scout tab immediately (`herdr tab close <scout-tab-id>`).
6. Record Scout prerequisite: `aihead council-state record-scout --run-dir "$RUN_DIR" --context "$RUN_DIR/context.md"`.

### Step 2: Independent Plan Drafting (Round 0)
1. Open Round 0: `aihead council-state open-round --run-dir "$RUN_DIR" --round 0`.
2. Spawn all Council Member tabs in parallel, distributing intent and `context.md` identically.
3. Each member independently drafts a complete technical plan without visibility into peer proposals.
4. Members save drafts to `RUN_DIR/plans/<member>_v0.md` and notify `<COORDINATOR_PANE_ID>`.
5. Register each draft: `aihead council-state submit-plan --run-dir "$RUN_DIR" --member "<member>" --plan "$RUN_DIR/plans/<member>_v0.md" --revision 0`.

### Step 3: Cross-Review & Iterative Synthesis Loop (Max 5 Cycles)
For feedback cycles 1 through 5:
1. Open round: `aihead council-state open-round --run-dir "$RUN_DIR" --round "$CYCLE"`.
2. **Cross-Review Distribution**:
   - Coordinator distributes peer plan snapshots to each member.
   - Each member reviews peer proposals, identifies edge cases/strengths, writes `RUN_DIR/feedback/<member>_round_<CYCLE>.md`, selects an explicit preferred plan, and notifies the Coordinator.
   - Register feedback: `aihead council-state submit-feedback --run-dir "$RUN_DIR" --round "$CYCLE" --member "<member>" --feedback "<path>" --selected-plan "<plan_id>"`.
3. **Consensus Check**:
   - Check consensus: `aihead council-state consensus --run-dir "$RUN_DIR" --round "$CYCLE"`.
   - **Immediate Stop on Consensus**: If all members unanimously select the same plan revision, deliberation succeeds immediately! Proceed directly to **Step 5 (Finalize)**.
4. **Unified Feedback & Revision**:
   - If consensus is not reached:
     - Coordinator synthesizes member critiques into unified feedback: `RUN_DIR/feedback/unified_round_<CYCLE>.md`.
     - Record unified feedback: `aihead council-state submit-unified-feedback --run-dir "$RUN_DIR" --round "$CYCLE" --feedback "<path>"`.
     - Send unified feedback to all members. Each member writes revised plan `RUN_DIR/plans/<member>_v<CYCLE>.md`.
     - Register revised plans: `aihead council-state submit-plan ... --revision "$CYCLE"`.
     - If `CYCLE < 5`, advance to next cycle.
     - If `CYCLE == 5` and consensus remains unreached, proceed to **Step 4 (Fallback Vote)**.

### Step 4: Fallback 1-Round Vote (After 5 Cycles Without Consensus)
If 5 full cycles conclude without unanimous agreement:
1. Coordinator initiates a single-round vote among candidate plans from Cycle 5.
2. Each Council Member casts one ballot:
   ```bash
   aihead council-state vote --run-dir "$RUN_DIR" --member "<member>" --ballot "<candidate_plan_id>"
   ```
3. Coordinator and Scout **do NOT vote**.
4. The plan with the highest tally wins. Deterministic tie-breaking applies based on configured roster order.

### Step 5: Finalization & Deliverable
Read-only discovery and deliberation do not require prior approval. The vote or consensus result selects a candidate only. Before final endorsement or action, get approval of the exact selected plan version.

Only AiHead acting within its authorized scope or the human can approve a gated exact plan version. A delegated coordinator sends that plan and its run/version to its direct parent and waits for explicit approval. Intermediaries can relay the original decision with provenance, but neither they nor the coordinator can self-approve. Standalone coordinators use enabled AiHead where supported, or ask the human and wait. A routine automode update outside a gate is not approval.

1. After approval, finalize the run: `aihead council-state finalize --run-dir "$RUN_DIR"`.
2. Close all Council Member tabs immediately.
3. Assembles `RUN_DIR/final_plan.md`, documenting:
   - Full architecture specification and task breakdown.
   - Outcome type (`UNANIMOUS_CONSENSUS` or `MAJORITY_VOTE`).
   - Deliberation history, cycle counts, and provenance of synthesized ideas.
4. Hand `final_plan.md` to the direct parent or user.

---

## 4. Strict One-Level-Up Communication Hierarchy

The first submitted task prompt names the actual immediate parent and embeds its concrete `herdr agent prompt <pane-id> "<message>"` route. Send all decisions, questions, blockers and completion to that parent only; do not ask the user directly or notify another pane. Environment and bootstrap only corroborate the prompt. An unresolved delegated route stops before spawn. Automode resolves routine reversible decisions, batching consequential choices into a plan checkpoint; keep explicit and non-automode gates. Only explicit user approval for the particular action and target can authorize irreversible destruction or a security bypass. Technical approval and preselected trust dialogs do not count.

The parent registers its first confirmed child and detaches one `python -m herdr_master.pipeline.child_watch --run-dir "$RUN_DIR" --parent-pane "$PARENT" --parent-generation "$PARENT_GENERATION"` process in the exact verified session with a private mode-700 run directory. Every 600 seconds it checks those identities; a confirmed loss event is not a completion callback or permission to retry, and idle/blocked/transport uncertainty is not proof of exit. The parent stops it with `touch "$RUN_DIR/child-watch.stop"`; never reuse that run directory or kill by its PID. No watcher pane/subagent is launched.

The initial prompt contract applies to the Scout, initial member drafts, and authorized replacements during feedback, revisions, and voting. Completion remains a plan artifact; Council never lands code.

Council deliberation enforces strict hierarchical parent-child notifications:

```
Level 1: AiHead / Master Coordinator (concrete direct-parent pane named in the Council Coordinator's first prompt)
             ▲
             │ herdr agent prompt <direct-parent-pane-id-from-Council's-first-prompt> "..."
             ▼
Level 2: Council Coordinator ($HERDR_PANE_ID / COORDINATOR_PANE_ID)
             ▲
             │ herdr agent prompt <COORDINATOR_PANE_ID> "..."
             ▼
Level 3: Council Scout & Council Members
```

- **Level 3 workers notify ONLY Level 2 (Council Coordinator)**. Level 3 workers **NEVER** message AiHead or grandparent coordinators directly.
- **Level 2 Coordinator notifies Level 1** using the concrete direct-parent pane named in its first prompt upon completing deliberation or encountering a blocking specification issue. Environment/bootstrap values only corroborate, never replace, that target; missing or ambiguous identity fails closed. Launcher-tagged terminal reports use `parent_contract complete`.
- **Non-blocking parents**: Parents dispatch prompts and return control immediately; wakeups occur reactively upon prompt notifications.

---

## 5. Hard Protocol Rules

- **Maximum 5 Cycles**: Deliberation never exceeds 5 feedback cycles. `council-state` rejects any round 6 attempt.
- **Strict Consensus Definition**: Consensus requires all members to explicitly select the identical plan revision. Similar text is NOT consensus.
- **Voting Neutrality**: Coordinator and Scout NEVER vote.
- **Plans Only**: The Council never writes code, runs test suites, creates git commits, or modifies branches.
