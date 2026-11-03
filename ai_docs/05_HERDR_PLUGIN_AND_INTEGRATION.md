# Herdr Plugin & Integration Specification

## 1. Distribution & Packaging Modalities

To provide maximum flexibility to developers and AI agents, **Herdr-Master** is distributed through two complementary modalities:

```
+-----------------------------------------------------------------------------------+
|                            Herdr-Master Distribution                              |
+-----------------------------------------------------------------------------------+
|  Modality A: Herdr Skill & Agent Integration |  Modality B: Standalone CLI & TUI  |
|  - Invocable as `/herdr-master` in any pane   |  - Standalone binary / Python CLI  |
|  - Symlinked to ~/.gemini, ~/.claude, etc.   |  - Installed via pipx / uv / cargo |
|  - Autonomous conversational coordination    |  - Interactive terminal dashboard  |
|  - Native async watchers per harness         |  - Background daemon & socket hook |
+-----------------------------------------------------------------------------------+
```

Both modalities share the exact same underlying invariants:
- Zero `git reset --hard` guarantee.
- Git worktree isolation per worker.
- Master Final Review Gate before squash-merging.
- Target branch default: `develop`.

---

## 2. Modality A: Herdr Native Skill & Agent Integration

### How it Works
When a human developer or script runs `/herdr-master` in any interactive agent pane where `HERDR_ENV=1`:
1. The agent reads its installed skill instructions (`SKILL.md`).
2. The agent verifies its environment (`HERDR_WORKSPACE_ID`, `HERDR_PANE_ID`).
3. The agent assumes the role of **Master Coordinator** for that workspace.
4. When jobs are assigned, it calls Herdr CLI subcommands (`herdr tab create`, `herdr agent start`, `herdr agent prompt`, `herdr agent wait`) to spawn workers in child tabs.

### Multi-Harness Symlink Matrix
A single canonical file (`skills/herdr-master/SKILL.md`) is maintained in the repository and symlinked into all supported agent configurations:

```bash
# Automated install script (scripts/install-skill.sh)
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# 1. Antigravity (agy)
mkdir -p ~/.gemini/config/skills/herdr-master
ln -sf "$REPO_DIR/skills/herdr-master/SKILL.md" ~/.gemini/config/skills/herdr-master/SKILL.md

# 2. Pi (pi)
mkdir -p ~/.pi/agent/prompts
ln -sf "$REPO_DIR/skills/herdr-master/SKILL.md" ~/.pi/agent/prompts/herdr-master.md

# 3. Claude Code (claude)
mkdir -p ~/.claude/commands
ln -sf "$REPO_DIR/skills/herdr-master/SKILL.md" ~/.claude/commands/herdr-master.md

# 4. OpenCode (opencode)
mkdir -p ~/.config/opencode/skills/herdr-master ~/.config/opencode/commands
ln -sf "$REPO_DIR/skills/herdr-master/SKILL.md" ~/.config/opencode/skills/herdr-master/SKILL.md
ln -sf "$REPO_DIR/skills/herdr-master/SKILL.md" ~/.config/opencode/commands/herdr-master.md
```

### Herdr Integration Descriptor (`integration.toml`)
Herdr includes an integration management system (`herdr integration`). Herdr-Master provides an integration manifest enabling Herdr to recognize and manage it natively:

```toml
[integration]
id = "herdr-master"
name = "Herdr Master Orchestrator"
version = "0.1.0"
description = "Multi-master, multi-worker cross-harness AI orchestrator"
binary = "herdm"
socket_protocol = 22

[integration.hooks]
on_agent_blocked = "herdm protocol notify-blocked --pane {pane_id}"
on_turn_settled = "herdm protocol notify-settled --pane {pane_id}"

[integration.commands]
orchestrate = "herdm master start"
tui = "herdm tui"
```

---

## 3. Modality B: Standalone CLI & TUI Tool (`herdm`)

For scenarios where the developer wants an interactive terminal dashboard or wants to script orchestration from outside an agent chat, Herdr-Master runs as a standalone CLI tool.

### Installation Options

#### Option 1: Via `pipx` (Recommended for Python environments)
```bash
pipx install git+https://github.com/<owner>/herdr-master.git
# or locally during development:
pipx install --editable /home/pank/projects/herdr-master
```

#### Option 2: Standalone uv / virtualenv
```bash
git clone https://github.com/<owner>/herdr-master.git
cd herdr-master
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Background Daemon / Socket Bridge
`herdm daemon` runs as a lightweight background process that connects directly to the Herdr Unix socket:
- Listens to lifecycle events (`agent_prompt_stalled`, `working`, `idle`, `blocked`, `done`).
- Updates the central status registry (`.herdr-master/sessions/`).
- Dispatches notifications to the active TUI and to parent orchestrators.
- Requires no polling loops.

---

## 4. Configuration Schema (`.herdr-master.toml`)

Project-level or global configuration can be stored in `.herdr-master.toml` (or `~/.config/herdr-master/config.toml`):

```toml
[general]
default_session = "default"
target_integration_branch = "develop"
create_develop_if_missing = true
disallow_hard_reset = true

[worktree]
base_directory = ".herdr-worktrees"
naming_template = "herdm/{job_name}-{timestamp}"
auto_clean_on_success = true

[models.default_squad]
scout = "agy:gemini-3.8-flash-high"
architect = "pi:openai-codex/gpt-6.1-sol"
reviewer = "agy:gemini-4-argon-high"

[models.worker_pool]
general_backend = "pi:xai/grok-4.7"
concurrency_logic = "pi:openai-codex/gpt-6.1-terra"
frontend_multimodal = "agy:gemini-4-argon-high"
mechanical_edits = "agy:gemini-3.8-flash-high"
free_tier = "pi:opencode-go/deepseek-v4.1-flash"

[tui]
refresh_rate_ms = 250
theme = "dark"
stream_buffer_lines = 1000
```
