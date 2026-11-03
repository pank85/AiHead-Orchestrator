The plan below is the build contract for turning the current stub (`herdm` init/status/tui printouts, partial models, IHCP envelope) into the Ergo cockpit. It is bound to Herdr **0.9.5 / Protocol 22 / schema_version 1** as shipped on this machine, and to the Ergo `--herdr` role contract in `/home/pank/projects/dotfiles/ergo/SKILL.md`.

# Herdr-Master Implementation Plan

## 0. Current baseline and non-negotiables

**Exists today**

- Specs: `ai_docs/00_OVERVIEW.md` through `ai_docs/05_HERDR_PLUGIN_AND_INTEGRATION.md`
- Stub CLI: `src/herdr_master/cli/main.py` (`init`, `status`, `tui` print only)
- Partial domain: `src/herdr_master/core/models.py` (`WorkerState`, `HarnessKind`, `EffortLevel`, `WorkerSession`, `MasterSession`)
- Partial IHCP: `src/herdr_master/protocol/envelope.py`
- Skill: `skills/herdr-master/SKILL.md`
- Deps already declared in `pyproject.toml`: Textual `>=0.52`, Rich `>=13.7`, Typer, Pydantic v2, psutil. Python `>=3.10`. No new runtime dependency unless a phase below says so.

**Invariants (every phase)**

- Session resolution: If running inside an existing Herdr session (`HERDR_ENV=1` / `HERDR_SOCKET_PATH`), use that **existing** session. Otherwise, default to session `default` (`~/.config/herdr/herdr.sock`). User may override with `--session <name>`, but `hermes` is not forced.
- Target branch is `develop`. Create it from `HEAD` only when missing. Never `git reset --hard`. Never force-remove a dirty worktree.
- Workers write only inside their own worktree. Parallel tasks have disjoint file ownership.
- Scout, Architect, and Reviewer are read-only on repository source. They write only under `RUN_DIR`.
- Landing is a serial `git merge --squash` onto a clean `develop`, only after `APPROVED`.
- `herdm` is the orchestrator. Do not shell out to an in-harness `/ergo` landing path. Reuse Ergo's briefs, `plan.md` contract, and `--models` semantics; the engine owns spawn, gates, and squash.
- Status is push-driven. A timer may only reopen a dead socket. It must not be the source of badge or deck updates.

**Slug grammar (load-bearing)**

```text
binding := kind ":" model_ref [ ":" effort ]
kind    := agy | pi | claude | opencode | codex
effort  := low | medium | high | max | xhigh   # xhigh aliases to max in storage
model_ref contains slashes, dots, dashes; no colon
```

Split on the first colon for kind. Strip a trailing `:` + effort token only. Do **not** treat `-high` inside `gemini-4-argon-high` as effort. Examples that must round-trip:

- `agy:gemini-3.8-flash-high`
- `pi:openai-codex/gpt-6.1-terra:high`
- `pi:xai/grok-4.7`
- `agy:gemini-4-argon-high` (model slug; effort comes from the role default)

---

## 1. Ergo pipeline inside the TUI

### 1.1 Role model

Four fixed roles plus an ordered worker catalog. The catalog is not 1:1 with tasks. The same binding may be assigned to several tasks; each task still gets its own tab, pane, branch, and worktree.

| Role | Cardinality | Default binding | Writes |
| :--- | :--- | :--- | :--- |
| Scout | 1 | `agy:gemini-3.8-flash-high` | `RUN_DIR/context.md` only |
| Architect | 1 | `pi:openai-codex/gpt-6.1-sol:high` | `RUN_DIR/plan.md` only |
| Worker pool | 1..N catalog entries | `pi:xai/grok-4.7`, `pi:openai-codex/gpt-6.1-terra`, `agy:gemini-4-argon-high` | assigned files + `coder_<N>_summary.md` + commits on its branch |
| Reviewer | 1 | `agy:gemini-4-argon-high` | `review.md`, `fixes_<N>.md` only |

Reviewer should be a different model family than Architect. The TUI warns and requires an explicit override; it does not silently swap.

### 1.2 What the operator configures

Per role (Scout, Architect, Reviewer) and per pool row:

- Harness: `agy | pi | claude | opencode | codex`
- Model slug (free text, verified when a catalog command is available)
- Effort: `low | medium | high | max`

Pool rows also have optional label, enabled flag, and order. Adding, removing, duplicating, and reordering are allowed until the Approval Gate closes. After approval, the pool is frozen for that run; a change requires Abort or a new run.

Persistence:

- Project default: `.herdr-master.toml` keys already specified in `ai_docs/05_HERDR_PLUGIN_AND_INTEGRATION.md` (`[models.default_squad]`, `[models.worker_pool]`)
- Run override: `.orchestration/<run-id>/squad.toml` (this is what the TUI edits)
- "Save as default" copies the run squad back to the project file

Named map in TOML is a preset. The run squad is an **ordered list**. CLI `--workers a,b,c` replaces that list; omitted roles keep defaults.

### 1.3 How the Architect picks a specialist

The engine does not pick models. It pastes the frozen pool into the Architect prompt as `ALLOWED CODER MODELS`, one exact `<kind>:<model>[:effort]` per row, plus a one-line capability hint from the row label.

`plan.md` task contract (reject the plan if any line is missing or out of pool; do not edit `plan.md` inside the engine):

- Description
- Files (disjoint from other `INDEPENDENT` tasks)
- Dependency: `INDEPENDENT` or `DEPENDS ON: <n>`
- Expert Persona
- Coder Model / Effort — exact pool id
- Interfaces
- Verification

The Pipeline View renders each task as a chip on the chosen pool row. Operator can reassign a chip in the Approval Gate; that writes a decision envelope, re-prompts Architect only if the operator asks for a rewrite, otherwise patches the assignment table the engine executes (the plan text stays the Architect's; the override is stored beside it in `squad.toml` as `[[overrides]]`).

### 1.4 Pipeline phases the view must show

`PREFLIGHT → SCOUT → ARCHITECT → APPROVAL_GATE → CODERS → REVIEW → FIX_LOOP → LANDING → CLEANUP`, plus `ABORTED` and `FAILED`.

Coder phase is a DAG, not a single bar: independent tasks run together; `DEPENDS ON` tasks stay `QUEUED` until upstream `coder_<N>_summary.md` is non-empty and the worker is `DONE`.

Fix loop cap: 5. Same pane, same binding, same persona. Reviewer relaunch is `MODE: fix-verification`. Exhaustion stops and preserves worktrees.

### 1.5 Prompt assembly

One formatter, used by CLI and TUI. Order matches Ergo §3:

1. Effort line (`low→think`, `medium→think hard`, `high→think harder`, `max|xhigh→ultrathink`) for `agy` and `claude`. Pi gets provider/model flags, not a fake effort word, unless the model id itself ends in `:effort`.
2. `Adopt this expert persona: …` on every coder prompt.
3. Shared boilerplate: absolute `WORKTREE`, absolute `RUN_DIR`, `unitTestsMode`, no `git reset --hard`, no merge to `develop`.
4. Role brief (vendored templates, not a runtime read of the Ergo skill).
5. Architect only: verbatim allowed-pool block.

Spawn commands (target session resolved from context: existing session or `default`):

- `agy`: `agent.start` kind `agy`, args `--model <slug>`
- `pi`: kind `pi`, args `--provider <provider> --model <model>` where provider is the segment before the first `/`
- `claude`: kind `claude`, args `--model <slug>`
- `opencode`: kind `opencode`, args `--model <slug>`
- `codex`: kind `codex`, args as verified in Phase 1 against `herdr agent start --help`

Do not pass `--dangerously-skip-permissions` from the TUI. Blocked approval dialogs surface as `BLOCKED` and wait for the operator.

---

## 2. TUI architecture

Textual app, one `AppState` store, widgets are projections. The socket reactor and the mailbox watcher are the only writers of live agent facts. Widgets post intents (`PromptRequested`, `ReviewRequested`, …); they do not call Herdr themselves.

### 2.1 Frame

```text
+--------------------------------------------------------------------------------------------------+
| HeaderBar: HERDR-MASTER 0.1.0 | session default | ws label/id | develop CLEAN|BEHIND n | agents | clock |
+---------------------------+----------------------------------------------------------------------+
| HierarchyTree             | Center Deck                                                          |
|  Root master              |  [1] Output  [2] Diff  [3] Pipeline  [4] Mailbox                     |
|   Scout DONE              |                                                                      |
|   Architect DONE          |  Pipeline mode: role cards + worker pool + task DAG                  |
|   worker pool             |  Other modes: PTY / diff / IHCP                                      |
|     coder-1 WORKING       |                                                                      |
|     coder-2 NEEDS_REVIEW  |                                                                      |
|   Reviewer STANDBY        |                                                                      |
+---------------------------+----------------------------------------------------------------------+
| Footer: Tab focus | p prompt | r review | m squash land | k kill | c config | 1-4 deck | q quit   |
+--------------------------------------------------------------------------------------------------+
```

Config drawer is a right-side `ModalScreen` / dock, not a fifth deck tab. It edits the selected role or pool row in place.

### 2.2 HeaderBar

`src/herdr_master/ui/tui/widgets/header_bar.py`

| Cell | Source | Update trigger |
| :--- | :--- | :--- |
| Session | `AppState.herdr_session` (resolved from context: existing session if inside Herdr, else `default`) | connect |
| Workspace | snapshot `workspaces[].label` + id for the run workspace `ergo-<run-id>` | `workspace_*` events |
| Branch | `develop` cleanliness: clean / dirty / behind N | worktree events, landing events, explicit `r`/`m` — not a timer |
| Agents | count of panes in this workspace whose mapped state is not `LANDED`/`FAILED` | status events |
| Clock | Textual clock, local, display only | widget timer, no I/O |

Socket down: header shows `SOCKET DOWN` and a reconnect countdown. Deck freezes last snapshot; it does not fake `IDLE`.

### 2.3 Hierarchy tree

`src/herdr_master/ui/tui/widgets/hierarchy.py`

Nodes: Master → pipeline roles → pool workers. Sub-masters nest when `MasterSession.role == sub-master`.

Badge map (do not collapse Herdr `done` and `idle`):

| Badge | WorkerState | Color |
| :--- | :--- | :--- |
| WORKING | `WORKING`, `SPAWNING`, `FIXING` | cyan |
| IDLE | `IDLE` | green |
| BLOCKED | `BLOCKED` | amber |
| NEEDS_REVIEW | `NEEDS_REVIEW` | magenta |
| APPROVED | `APPROVED`, `SQUASHING` | blue |
| LANDED | `LANDED` | dim |
| FAILED | `FAILED` | red |

`DONE` is internal. The tree shows `NEEDS_REVIEW` once the turn has settled **and** the expected artifact exists. Herdr `idle` alone is not completion: the reactor reads the pane before transitioning (same rule as the skill).

Each leaf shows binding (`pi:xai/grok-4.7`), branch, and worktree basename.

### 2.4 Center deck

`src/herdr_master/ui/tui/widgets/deck.py` switches on keys `1`–`4`.

**1 — Output.** Focused pane only. Bytes come from event-triggered `agent.read` / `pane.read` (`format=ansi`), appended, capped at `stream_buffer_lines` (default 1000). ANSI via Rich. Auto-scroll pauses when the operator scrolls up. Not a PTY attach: Protocol 22's 102 methods have no terminal-attach RPC. Push signal is `pane.scroll_changed` plus `pane.agent_status_changed` on the focused pane. Changing focus moves that subscription (see §3).

**2 — Diff.** `git diff develop...<branch>` for the selected worker, rendered by Rich `Syntax`. File list on the left of the pane, unified diff on the right. Refresh when the worker reaches `DONE` / `NEEDS_REVIEW`, and on `d`. Read-only.

**3 — Pipeline view (dedicated).** This is the Ergo surface, not a generic DAG widget with a caption.

- Three role cards (Scout, Architect, Reviewer): harness, model, effort, phase badge, elapsed, artifact path, last error.
- Worker pool strip: ordered rows, live badges, task chips assigned from `plan.md`.
- DAG under the strip: tasks as nodes, edges from `DEPENDS ON`, gate node between Architect and coders.
- Approval Gate bar: Proceed / Edit plan / Abort. Proceed is disabled until every task has a pool binding and disjoint files.
- Empty pool blocks Run.

**4 — Mailbox.** IHCP envelopes for the selected agent: inbox, outbox, dead-letter. Selecting a `CLARIFICATION_QUERY` or `APPROVAL_REQUEST` opens a reply/approve dialog. Reply writes `DECISION_REPLY` and injects it with `agent.prompt`.

### 2.5 Config drawer (`c`)

`src/herdr_master/ui/tui/modals/config_drawer.py`

- If a role card or pool row is selected, the form opens on that binding.
- Fields: Harness `Select`, Model `Input`, Effort `Select`, optional label.
- Actions: Verify, Save run, Save as default, Cancel.
- Verify shells to `agy models`, `pi --list-models`, `opencode models`, or `claude` model help on a worker thread. Unavailable catalog → warning badge, save still allowed.
- Pool mode adds Add, Remove, Move up, Move down. Duplicate `(kind, model, effort)` rows are rejected.
- Drawer is usable before a run (squad editor) and during `PREFLIGHT` / `APPROVAL_GATE`. Disabled during `CODERS` except "view".

### 2.6 Footer and keys

| Key | Action | Guard |
| :--- | :--- | :--- |
| `Tab` / `Shift+Tab` | Tree ↔ deck | always |
| `1`–`4` | Deck mode | always |
| `j`/`k` or arrows | Move selection | tree, pool, or DAG focused |
| `p` | Prompt dialog → `agent.prompt` on selected worker | not Scout/Architect/Reviewer unless operator confirms role follow-up |
| `r` | Master review on selected worker, or full Reviewer pass if a role card is selected | worker `DONE` or `NEEDS_REVIEW`; develop not dirty |
| `m` | Confirm, then serial squash land | state `APPROVED` only |
| `k` | Kill: close that tab, do not remove a dirty worktree | confirm |
| `c` | Config drawer | see §2.5 |
| `d` | Jump to diff | worker selected |
| `q` | Quit TUI | leaves agents and the socket server running |

`q` does not close the `ergo-<run-id>` workspace. Cleanup is an explicit pipeline step.

### 2.7 Widget rules

- No widget calls `herdr` or opens a socket.
- No widget parses `plan.md`. It receives `TaskView` models.
- Colors come from `src/herdr_master/ui/tui/theme.py` (cyan/magenta accent, dim chrome). No per-widget hex literals.
- All confirms (`m`, `k`, Abort, override different-family reviewer) are `ModalScreen`s, not `input()`.

---

## 3. Socket API and watcher engine

### 3.1 Wire

Control socket, newline-delimited JSON, one frame per line. Partial lines are buffered.

```json
{"id":"hm-…","method":"session.snapshot","params":{}}
```

Success: `{"id","result"}`. Failure: `{"id","error":{"code","message"}}`.

**RPC connections are one-shot.** Herdr closes the socket after a single response. Do not pool. Timeout 10s.

**`events.subscribe` is the exception.** It stays open and streams until we close it. A second `events.subscribe` on that same socket is silently ignored. Any subscription-set change closes the socket and opens a new one with the full set.

Socket path resolution, in order:

1. `HERDR_SOCKET_PATH` or `HERDR_SOCKET` (if inside an existing Herdr session)
2. `~/.config/herdr/sessions/<session>/herdr.sock` when explicit `--session <session>` is set
3. `~/.config/herdr/herdr.sock` (session `default`)

Windows is out of scope for v1 (named pipe). Linux and macOS only, matching the classifiers.

### 3.2 Methods the client wraps

Only these. Anything else goes through a typed method added in the same PR, not a stringly RPC helper scattered in the TUI.

| Method | Use |
| :--- | :--- |
| `ping` | liveness |
| `session.snapshot` | baseline and resync. Result is `{snapshot: {protocol, version, workspaces, tabs, panes, agents, layouts, focused_*}}`. Refuse to run if `protocol != 22` |
| `workspace.create/list/get/close` | one workspace `ergo-<run-id>`, `focus: false` |
| `tab.create/close` | one tab per role or coder, `focus: false` |
| `agent.start` | `name`, `kind`, `pane_id`, `args`, `timeout_ms` in `(3000, 300000]` |
| `agent.prompt` | bracketed text; `wait` is null (the reactor waits, not the RPC) |
| `agent.read` / `pane.read` | incremental scrollback for the focused deck |
| `agent.get` / `agent.list` | verify after a status edge; names are not unique across machines |
| `agent.send_keys` | only after the operator authorizes a specific `BLOCKED` dialog |
| `agent.wait` | not used on the TUI path (it blocks a connection). Allowed in CLI `--wait` as a one-shot RPC |
| `events.subscribe` | long-lived |
| `worktree.list` | display only; creation/removal stay in `worktree/manager.py` via git so the no-reset policy lives in one place |

### 3.3 Subscription set

Global (no `pane_id`):

- `workspace.created`, `workspace.closed`, `workspace.renamed`
- `tab.created`, `tab.closed`, `tab.renamed`, `tab.focused`
- `pane.created`, `pane.closed`, `pane.updated`, `pane.moved`, `pane.exited`, `pane.agent_detected`
- `worktree.created`, `worktree.opened`, `worktree.removed`
- `layout.updated`

Per known pane in this workspace:

- `{type: "pane.agent_status_changed", pane_id}` — **required**; a global status sub is not valid

Focused pane only (moved by reopening the socket):

- `{type: "pane.scroll_changed", pane_id}`

Do not subscribe `pane.output_matched` unless a future watcher needs a literal sentinel. It is not a byte stream.

Status frame (flat, not the structure-event shape):

```json
{"event":"pane.agent_status_changed","data":{"pane_id":"…","workspace_id":"…","agent_status":"working|idle|blocked|done|unknown","agent":null,"title":null,"state_labels":{}}}
```

`agent_status` enum is exactly `idle | working | blocked | done | unknown`. There is no `needs_review` on the wire. That state is ours.

### 3.4 Reactor

`src/herdr_master/watchers/reactor.py` — one asyncio task owned by the Textual app (or by `herdm daemon`).

```text
connect
  → ping
  → session.snapshot          # own connection
  → events.subscribe          # long connection, full set
  → on subscription_started: snapshot again, replay deltas vs last-told status
loop:
  event → reduce AppState → Textual message
  structural event (pane/tab/workspace/move) → debounce 500ms → close socket → resubscribe full set
  socket close or events_lost → backoff 250ms…5s → resubscribe → snapshot → replay
```

Rules copied from the live Protocol 22 client, because they are easy to get wrong:

- A snapshot can overtake an unread event. Wait 150ms before replaying a snapshot delta; an event that arrives in that window wins.
- Herdr may report a finish as `done` on the event and `idle` on a later snapshot. Do not alert on snapshot disagreement except across a **known gap** (our socket was down).
- `pane.moved` changes pane ids without close/create. Rebind worker records by terminal id / agent name, then resubscribe.
- Never send input, keys, or prompts from the reactor.
- One watcher logical stream per pane. Re-arm is implicit: the subscription stays up while the pane exists. Do not start a second `agent.wait` loop beside it (that is the harness-skill path, not the TUI path).

Mapping:

| Wire `agent_status` | Our edge |
| :--- | :--- |
| `working` | `IDLE/BLOCKED/SPAWNING → WORKING` |
| `blocked` | `→ BLOCKED`, then one `agent.read` to capture the dialog text into the mailbox view |
| `done` | not enough. Read pane. If a question is on screen, `BLOCKED`. If the role artifact is present, `DONE` then `NEEDS_REVIEW` for coders. If absent, stay `WORKING` and surface "settled without artifact" |
| `idle` | `WORKING → IDLE` only if no artifact and no question. Never `IDLE → APPROVED` |
| `unknown` | keep last badge, mark stale |

Mailbox files are the second push source. `protocol/fs_watch.py` uses stdlib inotify (Linux) or kqueue (macOS) on `.herdr-master/mailbox/` and `RUN_DIR`. No `sleep` poll. A new envelope is an event, same reducer.

Git status for the header is computed inside those same reducers (worktree event, landing, `r`/`m`), not on an interval.

The 60s backstop from other Herdr clients is **not** adopted. If the subscribe socket is alive, we do not snapshot. If it dies, reconnect is the backstop.

### 3.5 State machine

Implement `src/herdr_master/core/state_machine.py` as a pure function `(state, event) → state`. Illegal edges raise. The diagram in `ai_docs/01_ARCHITECTURE_AND_SPECS.md` is the spec, with two additions:

- `DONE → NEEDS_REVIEW` requires the artifact check above.
- `APPROVED → SQUASHING` requires the operator `m` (or `--auto-approve` on the CLI, default false). The reactor cannot land.

Pipeline-level states live on `PipelineRun`, not on `WorkerState`.

---

## 4. Module map

Extend the layout in `ai_docs/01_ARCHITECTURE_AND_SPECS.md`. Do not invent a second package.

```text
src/herdr_master/
  cli/main.py                         # Typer root; subapps mount here
  core/models.py                      # extend
  core/slug.py                        # binding parser
  core/config.py                      # toml load/save
  core/state_machine.py
  core/context.py                     # session, socket path, git root, develop
  herdr/socket.py                     # NDJSON, one-shot RPC, line buffer
  herdr/client.py                     # typed wrappers
  herdr/snapshot.py                   # SessionSnapshot pydantic models
  herdr/subscribe.py                  # long-lived subscribe + resubscribe
  watchers/reactor.py
  watchers/reducer.py
  adapters/base.py
  adapters/{agy,pi,claude,opencode,codex}.py
  adapters/prompt.py
  worktree/manager.py
  worktree/reviewer.py                # diff collect, squash, never reset
  protocol/mailbox.py
  protocol/bridge.py                  # prompt injection via client, not subprocess
  protocol/fs_watch.py
  pipeline/squad.py
  pipeline/plan_parser.py
  pipeline/prompts.py                 # vendored role briefs
  pipeline/gates.py
  pipeline/engine.py
  pipeline/ergo.py
  ui/tui/app.py
  ui/tui/state.py
  ui/tui/messages.py
  ui/tui/theme.py
  ui/tui/widgets/header_bar.py
  ui/tui/widgets/hierarchy.py
  ui/tui/widgets/deck.py
  ui/tui/widgets/output_stream.py
  ui/tui/widgets/diff_viewer.py
  ui/tui/widgets/pipeline_view.py
  ui/tui/widgets/dag.py
  ui/tui/widgets/mailbox_view.py
  ui/tui/widgets/footer.py
  ui/tui/modals/config_drawer.py
  ui/tui/modals/prompt_dialog.py
  ui/tui/modals/confirm.py
tests/
  test_slug.py
  test_state_machine.py
  test_plan_parser.py
  test_socket_framing.py
  test_reactor_replay.py
  test_squad_roundtrip.py
  test_worktree_policy.py             # asserts reset --hard is absent
  fixtures/protocol22/*.jsonl
```

CLI surface to grow on the existing Typer app (spec in `ai_docs/04_CLI_AND_TUI_SPEC.md`): `init`, `master`, `worker`, `pipeline`, `worktree`, `protocol`, `status`, `tui`. `herdm tui` constructs `HerdrMasterApp` and runs it. `herdm pipeline run ergo` calls the same `pipeline/ergo.py` the TUI calls.

---

## 5. Phases

Each phase is mergeable. Later phases must not reach into Herdr except through `herdr/client.py`.

### Phase 0 — Contracts and fixtures

**Deliverables:** `core/slug.py`, extensions to `core/models.py` (`HarnessBinding`, `Squad`, `PipelineRun`, `PipelinePhase`, `TaskNode`), `tests/test_slug.py`, `tests/fixtures/protocol22/` captured from `herdr api schema --json` (methods and event frames we actually handle, not the whole 272KB schema).

**Done when:** the four examples in §0 parse; `gemini-4-argon-high` does not lose a fake effort; `xhigh` stores as `max` and renders back as `max`.

### Phase 1 — Socket client

**Deliverables:** `herdr/socket.py`, `herdr/client.py`, `herdr/snapshot.py`, `herdr/subscribe.py`, `core/context.py`, `tests/test_socket_framing.py`.

**Behavior:** line reassembly, one-shot RPC against a fake Unix socket, subscribe that errors if a second subscribe is written on the same connection, protocol mismatch refusal.

**Spike (time-boxed, same phase):** confirm `herdr api snapshot --json` returns `protocol: 22`. Record one real `subscription_started` frame and one `pane.agent_status_changed` frame into fixtures. Do not block the phase on a live server in CI; fixtures are the contract.

### Phase 2 — Reactor and reducer

**Deliverables:** `watchers/reactor.py`, `watchers/reducer.py`, `core/state_machine.py`, `tests/test_state_machine.py`, `tests/test_reactor_replay.py`.

**Done when:** a scripted frame log produces the badge sequence `SPAWNING → WORKING → BLOCKED → WORKING → DONE → NEEDS_REVIEW` without any sleep-based poll; a snapshot that says `idle` after an unread `done` does not clobber `done` inside the 150ms window; pane-set change resubscribes once with the full set.

### Phase 3 — Config, mailbox, prompts

**Deliverables:** `core/config.py`, `pipeline/squad.py`, `pipeline/prompts.py`, `pipeline/plan_parser.py`, `protocol/mailbox.py`, `protocol/fs_watch.py`, `protocol/bridge.py`, `adapters/*`.

**Done when:** a squad round-trips TOML; a plan that names `codex:not-in-pool` is rejected with the task id; coder prompts start with the persona line; agy/claude prompts start with the effort word; pi prompts do not.

### Phase 4 — Worktree and review gate

**Deliverables:** `worktree/manager.py`, `worktree/reviewer.py`, `pipeline/gates.py`, `tests/test_worktree_policy.py`.

**Behavior:** branch `herdm/<run>/<task>-<timestamp>` under `<repo>/.herdr-worktrees/`. Create `develop` if missing. If `develop` advanced, merge or rebase **inside the worker worktree**. Squash only from a clean canonical `develop`. Refuse if porcelain is non-empty. Grep the package in CI for `reset --hard` and fail.

**Done when:** two workers can exist without sharing a worktree; a conflict stops and preserves both trees; squash is refused unless state is `APPROVED`.

### Phase 5 — Ergo engine

**Deliverables:** `pipeline/engine.py`, `pipeline/ergo.py`, CLI `pipeline run ergo`.

Sequence matches `ai_docs/03_ORCHESTRATION_AND_ERGO_PIPELINE.md` and Ergo steps 1–7, with herdm landing rules:

1. Preflight: clean tree or explicit isolate, `develop` present, Herdr socket ping, workspace `ergo-<run-id>` created unfocused in the active session.
2. Scout tab, prompt, wait for `context.md` via artifact event (one retry).
3. Architect with the pool block, wait for `plan.md`, validate.
4. Stop on Approval Gate. CLI blocks on stdin; TUI blocks on the gate bar. `--auto-approve` exists and defaults false.
5. Spawn coders for the ready set. `DEPENDS ON` waits for upstream summary. Each coder commits on its branch (engine instructs this in the coder brief — different from in-harness Ergo, where the orchestrator commits). This must be stated in the coder brief so a worker does not also try to squash.
6. Reviewer on `git diff develop...<branch>` per worker, then a combined review. `NEEDS FIXES` reuses the pane.
7. Land serially. Close only this workspace. `git worktree remove` without `--force`. Delete the temp branch only after the squash SHA is on `develop`.

**Done when:** a fixture run with fake client and fake git driver walks all seven steps and a rejected plan never creates a coder tab.

### Phase 6 — TUI shell

**Deliverables:** `ui/tui/app.py`, `state.py`, `messages.py`, `theme.py`, `header_bar.py`, `hierarchy.py`, `deck.py`, `footer.py`. `herdm tui` launches it. `herdm tui --demo` loads fixtures so the UI is reviewable with no server.

**Done when:** header, tree, four deck stubs, and footer render; `1`–`4`, `Tab`, `j`/`k`, `q` work in demo mode; quitting does not emit `workspace.close`.

### Phase 7 — Pipeline view and config drawer

**Deliverables:** `widgets/pipeline_view.py`, `widgets/dag.py`, `modals/config_drawer.py`, `tests/test_squad_roundtrip.py` UI-level via Textual pilot.

**Done when:** operator can build the example pool `[pi:xai/grok-4.7, pi:openai-codex/gpt-6.1-terra, agy:gemini-4-argon-high]`, set Architect to `pi:openai-codex/gpt-6.1-sol` at effort `high`, set Reviewer to `agy:gemini-4-argon-high`, save as default, reload, and see the same squad. DAG shows a `DEPENDS ON` edge. Proceed stays disabled until the gate's checks pass.

### Phase 8 — Live deck and actions

**Deliverables:** `output_stream.py`, `diff_viewer.py`, `mailbox_view.py`, `prompt_dialog.py`, `confirm.py`, action handlers in `app.py`.

Wire `p`, `r`, `m`, `k`, `c`, `d` to the engine. Output pane appends only on scroll/status events for the focused worker. Diff uses the worker branch. `m` is a no-op unless `APPROVED`. `k` closes the tab and leaves a dirty worktree in place with a visible warning.

**Done when:** a fixture status sequence updates the tree badge and the output pane without a polling callback in the widget tree (assert no `set_interval` whose callback calls the client).

### Phase 9 — Hardening

**Deliverables:** CLI/TUI parity tests, skill touch-up in `skills/herdr-master/SKILL.md` pointing operators at `herdm tui` for squad edits (do not duplicate the engine into the skill), `integration.toml` from `ai_docs/05` only if `herdr integration` accepts it in a spike — otherwise leave the manifest as a doc and do not pretend it is installed.

**Done when:** `pytest` is green offline; `ruff` clean; a manual smoke (not CI) can open the TUI, show a real snapshot from the active session, and quit without closing unrelated workspaces.

---

## 6. Failure policy

| Condition | Behavior |
| :--- | :--- |
| Socket missing | Header `SOCKET DOWN`. Config drawer still edits TOML. Run stays disabled |
| `protocol != 22` | Refuse connect. Do not guess fields |
| Subscribe silently ignored | Prevented by never writing a second subscribe; tested |
| `events_lost` / close | Reconnect, snapshot, replay across the gap only |
| Model catalog down | Save with unverified badge |
| Plan names a model outside the pool | Reject, re-prompt Architect once, then stop on the gate |
| Artifact missing after `done` | Stay out of `NEEDS_REVIEW`; show stalled; one operator retry |
| `develop` dirty | `m` refused |
| Conflict on rebase/merge in worktree | Stop, preserve, no reset |
| Fix loop > 5 | `FAILED`, workspace left open |
| `q` | Process exit only |

---

## 7. Acceptance for the four requested surfaces

1. **Pipeline view.** Deck `3` shows Scout, Architect, the ordered worker pool, and Reviewer, with phase badges driven by the reducer.
2. **Per-role config.** `c` edits harness, model slug, and effort for the selected role or pool row, and persists to `squad.toml` / optional project default.
3. **Dynamic pool.** Arbitrary length, including the three-binding example, frozen at approval, pasted verbatim to the Architect, validated on the way back.
4. **Socket reactor.** Protocol 22 NDJSON, snapshot on connect and after every resubscribe, `pane.agent_status_changed` per pane, no status polling, TUI badges are a pure function of those events plus mailbox and artifact events.

Out of scope for this plan: raw PTY attach, Windows named pipes, auto-approve by default, pushing `develop`, and implementing Ergo's in-harness subagent mode inside `herdm`.