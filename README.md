# 👑 Herdr-Master (`herdm`)

> **Universal Multi-Master, Multi-Worker Cross-Harness AI Agent Orchestrator & TUI Dashboard built on [Herdr](https://herdr.dev).**

[![GitHub Repo](https://img.shields.io/badge/GitHub-pank85%2Fherdr--master-181717?style=flat&logo=github)](https://github.com/pank85/herdr-master)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Herdr: v0.9.5+](https://img.shields.io/badge/herdr-v0.9.5+-8A2BE2.svg)](https://herdr.dev)
[![TUI: Textual](https://img.shields.io/badge/TUI-Textual%208.x-00D4AA.svg)](https://textual.textualize.io/)
[![Tests: 30 passed](https://img.shields.io/badge/tests-30%20passed-success.svg)](#-testing--verification)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

---

## 📖 Overview

**Herdr-Master (`herdm`)** standardizes and elevates the orchestration of multiple autonomous AI coding agents (Master Orchestrators and Worker Squads) running atop the **[Herdr](https://herdr.dev)** terminal agent manager.

Whether coordinating multiple background jobs simultaneously or driving a structured multi-stage coding pipeline, Herdr-Master connects different agent harnesses (**Antigravity**, **Pi**, **Claude Code**, and **OpenCode**) under a unified protocol with strict Git worktree sandboxing.

### 🌟 Key Highlights

1. **Dual Orchestration Modes**:
   - **`👑 New Herdr Master` (Multiple Jobs)**: A persistent root orchestrator that delegates diverse subtasks to parallel workers and subordinate sub-masters across isolated Herdr tabs.
   - **`⚡ New Ergo Pipeline` (Single Major Job)**: An end-to-end phased pipeline (`Scout` → `Architect` → `Parallel Coders` → `Reviewer` → `Squash Integration`) designed to conquer a complex objective.
2. **Interactive TUI Cockpit & 2-Column Squad Editor**:
   - **Full-Width Landing Deck**: A clean, 100% full-width first page with 3 quick-launch action cards.
   - **2-Column Settings Modal**: 
     - **Top 75%**: Squad Topology Tree (left) + Live Agent Detail Editor (right) for harness, model, reasoning effort, and worker pool scaling.
     - **Bottom 25%**: Objective prompt input, workspace label, and immediate execution buttons.
3. **Session Isolation & Seamless Focus**:
   - **Outside Herdr**: Automatically boots a dedicated, isolated named session (`herdr --session ergo-...`) hiding unrelated workspaces and projects, then immediately attaches to provide an uncluttered view.
   - **Inside Herdr**: Seamlessly registers the worker tab within the active session, invoking `workspace.focus` and `tab.focus` RPC endpoints so your viewport instantly tracks the active agent without terminal freeze.
4. **Cross-Harness Interoperability (IHCP)**:
   - Mix and match agent harnesses within the same workflow (e.g., Scout on Gemini Flash via `agy`, Architect on Grok 4.7 via `pi`, parallel Coders on Codex & Claude Sonnet, and Reviewer on Gemini Argon).
5. **Git Worktree Isolation & Mandatory Review Gate**:
   - Every worker operates in an isolated temporary Git worktree (`herdm/<job>-<timestamp>`). No agent touches the primary working tree.
   - All changes must pass a cumulative blind diff review before squash-landing to `develop`.
   - **Zero `git reset --hard`**: Preserves code integrity at all times.

---

## 🖥️ Interactive TUI Cockpit

Launch the interactive terminal dashboard at any time:

```bash
herdm tui
```

### 1. Main Cockpit Screen (Full-Width Landing View)
The primary landing view gives an uncluttered, high-density dashboard with three prominent quick-launch cards:

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 👑 HERDR-MASTER COCKPIT        [● CONNECTED]  Workspaces: 1  Agents: 0    17:30       │
├────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                        │
│  ╭─ 👑 New Herdr Master ──╮  ╭─ ⚡ New Ergo Pipeline ──╮  ╭─ ⚙️ Configure Squad ─────╮  │
│  │ Multiple Jobs           │  │ Single Major Job        │  │ Default Harness & Squad  │  │
│  │ Main orchestrator that  │  │ Dedicated workspace &   │  │ Configure default roles, │  │
│  │ delegates to multiple   │  │ multi-tab Ergo pipeline │  │ harnesses, models and    │  │
│  │ worker tabs & squads.   │  │ (Scout→Architect→Pool). │  │ worker squad topology.   │  │
│  │                         │  │                         │  │                          │  │
│  │  [ 👑 Launch Master ]   │  │  [ ⚡ Launch Ergo ]     │  │  [ ⚙️ Configure Squad ]  │  │
│  ╰─────────────────────────╯  ╰─────────────────────────╯  ╰──────────────────────────╯  │
│                                                                                        │
│  [1] Pipeline Graph    [2] Cumulative Diff    [3] Agent Output Streamer                │
└────────────────────────────────────────────────────────────────────────────────────────┘
│ [n] New Master  [e] New Ergo  [s/c] Settings  [1..4] View Modes  [q] Quit              │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

### 2. 2-Column Squad Topology & Pipeline Launcher Modal
Press `[ ⚡ New Ergo Pipeline ]`, `[ ⚙️ Configure Squad ]`, or keys `e` / `s`:

```text
┌─ ⚡ ERGO SQUAD TOPOLOGY & PIPELINE LAUNCHER ───────────────────────────────────────────┐
│                                                                                        │
│ ┌─ Top 75%: Squad Configuration ─────────────────────────────────────────────────────┐ │
│ │  Topology Tree (Left Column)      │  Agent Details (Right Column)                  │ │
│ │                                   │                                                │ │
│ │  👑 Herdr-Master Root             │  Role: Scout                                   │ │
│ │  ├── ● Scout (agy:gemini-flash)   │  Harness:  [ agy ▼ ]                           │ │
│ │  ├── ● Architect (pi:grok-4.7)    │  Model:    [ gemini-3.8-flash-high           ] │ │
│ │  ├── 📦 Worker Pool (2 workers)   │  Effort:   [ high ▼ ]                          │ │
│ │  │   ├── ● worker-1 (pi:codex)    │  Label:    [ scout                           ] │ │
│ │  │   └── ● worker-2 (claude:sonn) │                                                │ │
│ │  └── ● Reviewer (agy:argon)       │  [+ Add Worker]  [- Remove Worker]             │ │
│ └───────────────────────────────────┴────────────────────────────────────────────────┘ │
│                                                                                        │
│ ┌─ Bottom 25%: Execution Deck ───────────────────────────────────────────────────────┐ │
│ │  Objective Prompt: [ Implement OAuth2 SSO & JWT authentication                  ]  │ │
│ │  Workspace Label:  [ ergo-auth-pipeline     ]                                      │ │
│ │                                                                                    │ │
│ │  [ ⚡ Start Ergo Pipeline ]         [ 💾 Save Default ]          [ Cancel ]        │ │
│ └────────────────────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 🏛️ System Architecture

```mermaid
graph TD
    User["Developer / Operator"] -->|CLI / TUI| Orchestrator["Herdr-Master Engine"]
    
    subgraph ExecutionContext ["Herdr Session Management"]
        direction TB
        Orchestrator -->|Outside Herdr| IsolatedSession["Isolated Session (herdr --session ergo-xyz)"]
        Orchestrator -->|Inside Herdr| ActiveSession["Active Workspace (auto-focus tab)"]
    end

    subgraph ErgoPipeline ["Ergo Multi-Harness Pipeline"]
        Scout["1. Scout Agent<br/>(agy: gemini-3.8-flash)<br/>Exploration & Context Survey"]
        Architect["2. Architect Agent<br/>(pi: xai/grok-4.7)<br/>System Design & Work Breakdown"]
        Pool["3. Parallel Workers<br/>(pi / claude / opencode)<br/>Isolated Git Worktrees"]
        Reviewer["4. Blind Reviewer<br/>(agy: gemini-4-argon)<br/>Cumulative Diff Review Gate"]
        Squash["5. Integration Landing<br/>Verified Squash Landing to develop"]

        Scout --> Architect --> Pool --> Reviewer --> Squash
    end

    IsolatedSession --> ErgoPipeline
    ActiveSession --> ErgoPipeline

    subgraph IHCP ["Inter-Harness Communication Protocol (IHCP)"]
        Socket["Herdr Unix Domain Socket API"]
        Mailbox[".herdr-master/mailbox/"]
        PTY["Bracketed-Paste Safe Injection"]
    end

    Pool <--> IHCP
    Reviewer <--> IHCP
```

---

## ⚡ Slugs & Supported Harnesses

Define any agent using the compact slug format: `<harness>:<model>[:<effort>]`

| Harness | Slug Prefix | Example Slugs | Ideal Roles & Strengths |
| :--- | :--- | :--- | :--- |
| **Antigravity** | `agy` | `agy:gemini-3.8-flash-high`<br/>`agy:gemini-4-argon-high` | Rapid context survey, deep architectural audit, strict diff reviews |
| **Pi Agent** | `pi` | `pi:xai/grok-4.7`<br/>`pi:openai-codex/gpt-6.1-terra` | Complex refactoring, fast code synthesis, test authoring |
| **Claude Code** | `claude` | `claude:claude-3-7-sonnet`<br/>`claude:claude-3-opus` | Thoughtful implementation, nuanced edge-case analysis |
| **OpenCode** | `opencode` | `opencode:deepseek-r1`<br/>`opencode:qwen-2.5-coder` | Local models and private open-weights execution |

---

## 🚀 Quickstart & Installation

### Option 1: Standalone CLI & TUI Installation (Recommended)

```bash
# Clone repository
git clone https://github.com/pank85/herdr-master.git
cd herdr-master

# Install with uv (fastest)
uv pip install -e .

# Or install with pipx
pipx install --editable .
```

Verify your installation:
```bash
herdm --help
```

### Option 2: Herdr Plugin / Agent Skill Installation

Install the canonical `/herdr-master` skill into all local agent harnesses (`agy`, `pi`, `claude`):

```bash
cd herdr-master
chmod +x scripts/install-skill.sh
./scripts/install-skill.sh
```

Now, inside any Herdr pane with `HERDR_ENV=1`, invoke directly:
```bash
/herdr-master "Refactor auth middleware to use Redis session store"
```

---

## 💻 CLI Usage Guide

### 1. Launch Ergo Pipeline (Single Major Job)
```bash
# Simple launch using default squad configuration
herdm ergo "Implement Redis rate limiting middleware"

# Full custom squad across diverse harnesses & models
herdm ergo "Add WebSocket notification server" \
  --scout agy:gemini-3.8-flash-high \
  --architect pi:xai/grok-4.7 \
  --workers pi:openai-codex/gpt-6.1-terra,claude:claude-3-7-sonnet \
  --reviewer agy:gemini-4-argon-high \
  --workspace-label real-time-notifications
```

### 2. Launch Root Herdr Master (Multiple Jobs)
```bash
herdm master start backend-lead \
  --harness agy \
  --model gemini-4-argon-high \
  --initial-task "Audit microservices and prepare refactoring roadmap"
```

### 3. Interactive TUI Cockpit
```bash
# Launch interactive cockpit connected to Herdr
herdm tui

# Launch in demo mode with mock data
herdm tui --demo
```

### 4. Repository Initialization
```bash
cd /path/to/my-project
herdm init --target-branch develop
```

---

## 🧪 Testing & Verification

The project includes an automated test suite covering state machines, framing protocols, launcher lifecycles, and Textual pilot interactions:

```bash
# Run full test suite (30 passed)
uv run pytest tests/

# Code quality check with Ruff
uv run ruff check .
```

```text
============================== 30 passed in 6.06s ==============================
```

---

## 📚 Deep-Dive Documentation

For detailed architectural specifications and design documents, explore the [`ai_docs/`](file:///home/pank/projects/herdr-master/ai_docs/) directory:

* [**00. System Overview & Vision**](file:///home/pank/projects/herdr-master/ai_docs/00_OVERVIEW.md) — Vision, tenets, and principles.
* [**01. Architecture & Core Specifications**](file:///home/pank/projects/herdr-master/ai_docs/01_ARCHITECTURE_AND_SPECS.md) — System components, worker states, and lifecycle.
* [**02. Inter-Harness Communication Protocol (IHCP)**](file:///home/pank/projects/herdr-master/ai_docs/02_INTER_HARNESS_PROTOCOL.md) — Message envelopes, mailboxes, and PTY injection.
* [**03. Orchestration Pipelines & Ergo Integration**](file:///home/pank/projects/herdr-master/ai_docs/03_ORCHESTRATION_AND_ERGO_PIPELINE.md) — Phased pipeline execution flow.
* [**04. CLI & Terminal UI (TUI) Specification**](file:///home/pank/projects/herdr-master/ai_docs/04_CLI_AND_TUI_SPEC.md) — Textual UI widgets, actions, and modal specs.
* [**05. Herdr Plugin & Integration Packaging**](file:///home/pank/projects/herdr-master/ai_docs/05_HERDR_PLUGIN_AND_INTEGRATION.md) — Packaging as Herdr plugin and skill.

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](file:///home/pank/projects/herdr-master/LICENSE) for more details.
