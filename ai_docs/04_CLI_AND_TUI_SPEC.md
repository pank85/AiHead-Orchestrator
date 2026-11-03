# CLI & Terminal User Interface (TUI) Specification

## 1. CLI Commands Specification (`herdm` / `herdr-master`)

The `herdm` command-line utility provides rapid, scriptable control over multi-agent orchestration, worktrees, and Herdr panes.

```text
Usage: herdm [OPTIONS] COMMAND [ARGS]...

Universal multi-master, multi-worker cross-harness AI orchestrator built on Herdr.

Options:
  --session TEXT      Herdr persistent session name [default: default]
  --workspace TEXT    Target Herdr workspace ID or label
  --json              Output responses in structured JSON format
  -v, --verbose       Enable verbose debug logging
  --help              Show this message and exit

Commands:
  init                Initialize .herdr-master workspace configuration
  master              Manage master orchestrator sessions
  worker              Spawn and coordinate worker agent sessions
  pipeline            Run and monitor multi-role agent pipelines
  worktree            Inspect and manage isolated Git worktrees
  protocol            Inspect inter-harness mailbox messages and events
  status              Display current multi-agent topology and states
  tui                 Launch the interactive terminal management dashboard
```

---

### Command Details

#### `herdm init`
Initializes `.herdr-master/` in the current git repository, creates default `config.toml`, sets up mailbox directories, and verifies `develop` branch existence.

```bash
herdm init [--target-branch develop] [--session default]
```

#### `herdm master start`
Registers or launches a Master Orchestrator session in a designated pane or background process.

```bash
herdm master start \
  --kind agy \
  --model gemini-4-argon \
  --role root-orchestrator \
  [--task "Implement distributed caching"]
```

#### `herdm worker start`
Spawns an isolated worker in a new Herdr tab with an independent Git worktree.

```bash
herdm worker start <worker-name> \
  --kind <agy|pi|claude|opencode|codex> \
  --model <model-slug> \
  --effort <low|medium|high|max> \
  [--worktree-branch <branch-name>] \
  [--task "Task brief"] \
  [--auto-watch]
```

#### `herdm worker prompt`
Submits instructions or follow-ups to an active worker tab using bracketed-paste safe injection.

```bash
herdm worker prompt <worker-name> "Run unit tests and report failure logs" --wait
```

#### `herdm worker diff`
Displays the real-time git diff of the worker's worktree against `develop`.

```bash
herdm worker diff <worker-name> [--stat]
```

#### `herdm pipeline run`
Executes an end-to-end multi-role pipeline (such as Ergo) across heterogeneous harnesses.

```bash
herdm pipeline run ergo \
  --intent "Migrate auth service to JWT tokens" \
  --scout agy:gemini-3.8-flash \
  --architect pi:openai-codex/gpt-6.1-sol \
  --workers pi:xai/grok-4.7,pi:openai-codex/gpt-6.1-terra \
  --reviewer agy:gemini-4-argon \
  --auto-approve=false
```

#### `herdm status`
Outputs a clean visual or JSON snapshot of all active masters, workers, panes, worktrees, and watcher states.

---

## 2. Terminal UI (TUI) Design Specification

The TUI provides a visual command center inspired by Herdr's lean, high-density terminal layout, built with Python's [Textual](https://textual.textualize.io/) and [Rich](https://rich.readthedocs.io/).

### TUI Screen Layout Wireframe

```
+----------------------------------------------------------------------------------------------------+
| HERDR-MASTER v0.1.0  | Session: default | WS: ws-main | Target: develop [CLEAN] | Agents: 4 Active | 14:30 |
+----------------------------------------------------------------------------------------------------+
| MASTERS & WORKERS                  | ACTIVE VIEW: [1] Output  [2] Diff  [3] Pipeline DAG  [4] Mailbox      |
|------------------------------------|---------------------------------------------------------------|
| ▼ [Root] Master (agy: argon)       | [worker-backend (pi: grok-4.7)]                               |
|   ├─ ● Scout [DONE]                |                                                               |
|   ├─ ● Architect [DONE]            | $ pytest tests/test_cache.py                                  |
|   ├─ ▲ worker-backend [WORKING]    | ========================= test session starts =============== |
|   │    wt: .herdr-wt/wt-backend    | collected 14 items                                            |
|   │    br: herdm/task-backend      | tests/test_cache.py ..............                     [100%] |
|   │    model: pi:xai/grok-4.7      |                                                               |
|   ├─ ◆ worker-ui [NEEDS_REVIEW]    | 14 passed in 0.42s                                            |
|   │    wt: .herdr-wt/wt-ui         |                                                               |
|   │    br: herdm/task-ui           | Commit created: 4e91f0a feat(cache): redis pool manager       |
|   │    model: agy:gemini-4-argon   | Status: Turn settled (idle)                                   |
|   └─ ⚪ Reviewer [STANDBY]          |                                                               |
|                                    |                                                               |
|------------------------------------|---------------------------------------------------------------|
| RECENT EVENTS & LOGS               | WORKER ACTIONS / REVIEW                                       |
|------------------------------------|---------------------------------------------------------------|
| [14:28:10] worker-backend started  | Diff: +142 lines / -18 lines (3 files changed)                |
| [14:29:40] worker-ui created 4e91  | Review Gate: [p] Send Prompt  [r] Trigger Review              |
| [14:29:55] Watcher w-01 triggered  |              [m] Squash Land to develop   [k] Kill Worker     |
+----------------------------------------------------------------------------------------------------+
| [Tab] Focus  [j/k] Navigate  [p] Prompt  [r] Review  [m] Squash-Merge  [d] Show Diff  [q] Quit     |
+----------------------------------------------------------------------------------------------------+
```

---

## 3. Visual Components & Styling

### 1. Header Bar (`HeaderWidget`)
- Product branding (`HERDR-MASTER`) with accent color (cyan/magenta).
- Connected Herdr session name (`hermes`, `default`).
- Active workspace ID and label.
- Git integration target branch status (`develop [CLEAN]` or `develop [BEHIND 2]`).
- Active agent count and live system clock.

### 2. Hierarchy Tree (`HierarchyTreeWidget`)
Interactive tree displaying:
- Root Master node.
- Nested Sub-Masters (if present).
- Leaf Worker nodes with status icons and colors:
  - `● [cyan]` `WORKING`: Agent actively executing tools / processing.
  - `● [green]` `IDLE`: Ready for new prompt.
  - `▲ [amber]` `BLOCKED`: Waiting for user decision or approval dialog.
  - `◆ [magenta]` `NEEDS_REVIEW`: Turn complete, diff waiting for Master review.
  - `✔ [blue]` `APPROVED`: Passed Master review, ready to squash land.
  - `✔ [dim]` `LANDED`: Successfully squashed into `develop`, awaiting cleanup.
  - `✖ [red]` `FAILED`: Stalled, error, or cancelled.

### 3. Multi-View Main Deck (`DeckWidget`)
Tabbed view switcher toggled with numbers `1..4` or mouse click:
- **Tab 1: Live Output Streamer**:
  - Connects to `herdr agent read <worker>` or socket stream.
  - ANSI terminal color rendering via Rich.
  - Auto-scrolls with pause-on-scroll capability.
- **Tab 2: Interactive Diff Viewer**:
  - Displays `git diff develop...<worker-branch>` with side-by-side or unified view.
  - Highlights modified files, syntax coloration, and added/deleted lines.
- **Tab 3: Pipeline DAG Tracker**:
  - Step-by-step progress cards (`Scout` -> `Architect` -> `Coders` -> `Reviewer` -> `Landing`).
  - Progress percentage, elapsed time, and status checkpoints.
- **Tab 4: Inter-Harness Mailbox**:
  - Displays structured IHCP JSON messages sent between agents.
  - Allows inspecting clarification queries and approval requests.

### 4. Interactive Action & Bottom Bar (`FooterWidget`)
- Single-letter hotkeys for instant operator intervention:
  - `p`: Open floating prompt dialog to send instructions to the selected worker.
  - `r`: Trigger an immediate Master Review Gate on the selected worker's branch.
  - `m`: Trigger serial squash-landing into `develop` (with confirmation prompt).
  - `k`: Gracefully terminate the selected worker and clean up worktree.
  - `d`: Jump directly to Diff view.
  - `Tab` / `Shift+Tab`: Cycle focus between Tree and Main Deck.
  - `j` / `k` (or Up/Down arrows): Navigate items.
  - `q`: Exit TUI (leaves background agents running untouched).
