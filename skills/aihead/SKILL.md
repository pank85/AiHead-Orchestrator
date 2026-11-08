---
name: aihead
description: "Launch interactive AiHead to direct work, answer architecture questions, and manage tasks from a Herdr workspace."
argument-hint: "[--harness <harness>] [--model <model>] [--effort <effort>] [--no-attach] [initial task]"
slash: false
metadata:
  opencode/autoinvoke: false
---

# `/aihead` — Interactive AiHead Technical Director & Advisor

Launch an interactive AiHead agent to direct technical work, supervise tasks, and provide architectural guidance from a Herdr workspace.

Initial task, if supplied: `${@:-$ARGUMENTS}`

---

## 1. Overview & Verification

The `aihead` command launches an interactive AiHead session in Herdr:
- **Command Verification**: Verify availability and inspect supported flags via `aihead --help`.
- **Harness Prerequisite**: Requires a supported, configured, and authenticated agent harness (`agy`, `pi`, `claude`, or `opencode`).
- **No AiHeadDev Required**: AiHead runs directly under Herdr using herdr-master. No separate AiHeadDev UI or repository is required or used. Never attempt to run `herdm tui`, install `aiheaddev`, or clone another repository.

---

## 2. Invocation & Dispatch Contract

When invoking AiHead from the command line or within an agent workflow:
- **Launch At Most Once**: Launch at most once per requested operation; do not spawn redundant or nested sessions.
- **Agent Invocation (`--no-attach`)**: When an agent invokes `aihead` programmatically on behalf of a user or task, always include `--no-attach`. This ensures the new session starts in the background and does not hijack the agent's active terminal.
- **Interactive User Invocation**: For human interactive use, `aihead` attaches by default so the user is placed directly into the interactive AiHead session.
- **Initial Task Forwarding**:
  - **With task**: `aihead --no-attach --task "${@:-$ARGUMENTS}"` launches AiHead with the initial objective ready for execution.
  - **Blank (no task)**: `aihead --no-attach` starts AiHead in an interactive ready state, awaiting interactive delegation and prompts.

---

## 3. Workspace Adoption & Server Startup

The `aihead` launcher manages Herdr workspaces automatically:
- **Adopt Existing Workspace**: If a usable Herdr session and workspace already exist (such as when already running inside Herdr or when a session has active workspaces), `aihead` adopts that workspace and opens an `ai-head` tab there. It will not create a duplicate workspace.
- **Automatic Server Startup & Workspace Creation**: If Herdr is not currently running or no workspace exists, `aihead` automatically starts the Herdr daemon, creates a new workspace, and launches the interactive AiHead session inside it.
- **Direction from Herdr**: Once launched, AiHead runs as a full interactive agent in its Herdr workspace pane, directing work, reviewing plans, and collaborating interactively.
