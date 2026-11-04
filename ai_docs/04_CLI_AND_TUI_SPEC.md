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
| [ New Master ]               [ New Ergo ]               [ Squad Settings ]                         |
| [ Open in Herdr [o] ]  Workspace: project-a (default) · 2 panes                                    |
| Open switches to Herdr; another-session/outside-Herdr attach replaces this cockpit. Run `herdm tui` |
| 2/2 sessions online · 3 workspaces · 4 agents · Click row to select; [o] to open                   |
| ▼ ● running [Master] project-a · default · 2 panes                                                 |
|   ├── ● running  Builder · Implement · w1:p1 · /projects/project-a                                 |
|   └── ○ idle     Reviewer · Review · w1:p2 · /projects/project-a                                  |
| ▶ ○ idle  project-b · default · 1 pane                                                             |
| ▶ ● running [Ergo] backend · ergo-backend · 1 pane                                                 |
|                                                                                                    |
+----------------------------------------------------------------------------------------------------+
| [n] New Master  [e] New Ergo  [s/c] Settings  [o] Open in Herdr  [q] Quit                          |
+----------------------------------------------------------------------------------------------------+
```

---

## 3. Visual Components & Styling

### 1. Header Bar (`HeaderBar`)
- Product branding (`HERDR-MASTER`) with accent color (cyan/magenta).
- Connected Herdr session name (`default`, etc.).
- Active workspace ID and label.
- Git integration target branch status (`develop [CLEAN]`).
- Active agent count and live system clock.

### 2. Quick Launch Bar (`QuickLaunchBar`)
- Single-row toolbar with keyboard/mouse launchers:
  - `[ New Master ]`: Opens the Launch Herdr Master dialog (`n`).
  - `[ New Ergo ]`: Opens the Squad Settings & Ergo Pipeline Launcher dialog (`e`).
  - `[ Squad Settings ]`: Configures project squad defaults (`s` or `c`).

### 3. Open in Herdr Strip & Boundary Help
- Visible button `#btn-open-herdr` labeled **Open in Herdr [o]**.
- Live selection summary showing the currently highlighted workspace or pane.
- Explicit handoff guidance: *Open switches to Herdr; another-session/outside-Herdr attach replaces this cockpit. Run `herdm tui` to return.*

### 4. Workspace Tree (`WorkspaceBrowser`)
Interactive full-width tree displaying live Herdr sessions, workspaces, and panes:
- Polled every 3 seconds across discovered UNIX sockets without blocking UI interaction.
- Status icons:
  - `● [green]` `running`: Active execution in progress.
  - `○ [dim]` `idle`: Turn settled / waiting for prompt.
  - `! [amber]` `blocked`: Waiting on external condition.
  - `✓ [blue]` `done`: Execution complete.
  - `? [dim]` `unknown`: Unrecognized status.
- Socket-scoped identity badges:
  - `[Master]`: Master coordinator detected in workspace.
  - `[Ergo]`: Ergo orchestrator detected in workspace.
  - Badges are identity indicators derived from snapshot evidence and live launch records, not an authoritative Protocol 22 role registry. Custom-named Masters launched elsewhere or surviving a cockpit restart may be unidentifiable, and absence of a badge does not prove absence.
- Selection behavior:
  - Clicking, arrowing, and Enter expand/collapse or select rows.
  - Selection alone never executes focus RPCs or leaves the cockpit.
  - Pressing `o` or clicking **Open in Herdr** focuses the exact workspace (preserving its active terminal) or exact leaf pane. Same-socket focus stays open; other-socket attach hands off cleanly.

### 5. Action Footer (`ActionFooter`)
- Main cockpit action hints:
  - `[n]`: New Master launcher modal.
  - `[e]`: New Ergo launcher modal.
  - `[s/c]`: Squad settings modal.
  - `[o]`: Open selected workspace/pane in Herdr.
  - `[q]`: Quit cockpit.
- Resize adaptation: automatically computes shorter hints at narrower widths (preserving Open and Quit visibility down to 40 columns).
