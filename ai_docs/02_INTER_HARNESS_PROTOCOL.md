# Inter-Harness Communication Protocol (IHCP)

## 1. Overview & Purpose

Modern AI development utilizes diverse agent runtimes ("harnesses"), each with distinct CLI flags, terminal interaction styles, prompt conventions, and strengths:
- **Antigravity (`agy`)**: High-tier multi-modal Gemini & Claude models, artifact subsystem, fine-grained subagents.
- **Pi (`pi`)**: High-speed, high-throughput Grok 4.7 and Codex Terra models, resilient tool runtime.
- **Claude Code (`claude`)**: Anthropics' native agent with Sonnet/Opus, deep bash execution, compact context.
- **OpenCode (`opencode`)**: Open-source agent runner with multi-provider routing and lightweight models.
- **Codex CLI**: Specialized OpenAI Codex execution.

**The Inter-Harness Communication Protocol (IHCP)** defines how these heterogeneous harnesses exchange structured messages, delegate tasks, ask clarifying questions, request approvals, and share review findings without requiring individual harnesses to understand each other's native internal formats.

---

## 2. Transport Architecture

IHCP operates via a **hybrid dual-channel transport**:

```
+-----------------------------------------------------------------------------------+
|                            IHCP Transport Layer                                   |
+-----------------------------------------------------------------------------------+
|  1. Fast Control Channel (In-Band)   |  2. Shared Mailbox & Storage (Out-of-Band) |
|  - Herdr Socket API                  |  - Path: .herdr-master/mailbox/<agent-id>/ |
|  - `herdr agent prompt <pane>`       |  - Inbox, Outbox, and Dead-letter queues   |
|  - `herdr agent send-keys <pane>`    |  - Artifact Registry (.herdr-master/store) |
|  - Bracketed-paste safe injection    |  - Persistent JSONL message envelopes      |
+-----------------------------------------------------------------------------------+
```

### Channel 1: Control & Prompt Injection (In-Band)
- When a sender (e.g. Master) needs to deliver a task or response to an interactive agent, it uses `herdr agent prompt <pane-id> "<formatted_prompt>"`.
- Herdr's bracketed paste mode ensures clean delivery without terminal line-splitting or partial command execution.
- If an agent is blocked at a confirmation prompt, `herdr agent send-keys` sends the precise validated key codes (`enter`, `y`, `n`, `esc`).

### Channel 2: Structured File-Based Mailbox (Out-of-Band)
Every Herdr workspace managed by Herdr-Master contains a `.herdr-master/` runtime directory:
```text
.herdr-master/
├── config.toml
├── sessions/
│   └── <run-id>.json
├── mailbox/
│   ├── master/
│   │   ├── inbox/
│   │   └── outbox/
│   ├── worker-backend/
│   │   ├── inbox/
│   │   └── outbox/
│   └── reviewer/
│       ├── inbox/
│       └── outbox/
└── artifacts/
    ├── <artifact-hash>.json
    └── diffs/
```

- When an agent completes a phase or produces an artifact (e.g. `plan.md`, test report), it deposits a structured envelope in its outbox or updates the run directory.
- The Herdr-Master daemon or coordinating master reads the mailbox, dispatches events over the socket, and mirrors key milestones to the TUI.

---

## 3. Message Envelope Specification

All IHCP messages conform to the following JSON schema:

```json
{
  "$schema": "https://herdr-master.dev/schemas/ihcp-v1.json",
  "id": "msg-8f92b45e-a931-419b-b054-e0c2f8115682",
  "correlation_id": "req-20261103-001",
  "timestamp": "2026-11-03T12:30:00.000Z",
  "type": "TASK_DISPATCH",
  "sender": {
    "agent_id": "master-root",
    "pane_id": "p-01",
    "harness": "agy",
    "role": "orchestrator",
    "model": "gemini-4-argon"
  },
  "recipient": {
    "agent_id": "coder-backend",
    "pane_id": "p-02",
    "harness": "pi",
    "role": "specialist-coder",
    "model": "xai/grok-4.7"
  },
  "payload": {
    "task_id": "task-01",
    "worktree": "/home/pank/projects/repo/.herdr-worktrees/job-backend",
    "branch": "herdm/job-backend-20261103",
    "target_branch": "develop",
    "title": "Implement Redis Cache Layer",
    "brief": "Implement distributed caching in src/cache/ using redis-py.",
    "constraints": [
      "All reads must use fallback on cache miss",
      "Do not modify database models directly",
      "Do not run git reset --hard"
    ],
    "unit_tests_mode": "new",
    "acceptance_criteria": [
      "pytest tests/test_cache.py passes with 100% test pass rate",
      "Benchmarked cache hit latency < 2ms"
    ]
  },
  "attachments": [
    {
      "name": "architecture_spec",
      "path": ".orchestration/run-01/plan.md",
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
  ]
}
```

---

## 4. Message Types & Interaction Flows

### 1. `TASK_DISPATCH` (Master -> Worker)
Dispatches a concrete implementation scope to a worker. The prompt formatter converts the structured JSON payload into the harness's preferred prompt template (including effort levels and expert personas).

### 2. `CLARIFICATION_QUERY` (Worker -> Master)
Triggered when a worker encounters ambiguous specs, conflicting files, or missing credentials.
```json
{
  "type": "CLARIFICATION_QUERY",
  "payload": {
    "question": "The existing config defines both REDIS_URL and CACHE_HOST. Which should take precedence?",
    "options": ["REDIS_URL", "CACHE_HOST", "Merge both with REDIS_URL taking priority"],
    "impact": "Config parser behavior in production environments",
    "blocking": true
  }
}
```
**Handling**:
- The Master inspects the query.
- If the answer is documented in project specs or code, the Master answers directly with `DECISION_REPLY`.
- If the question involves trade-offs or business logic, the Master pauses, escalates to the user via chat/TUI, and forwards the user's decision back to the worker.

### 3. `DECISION_REPLY` (Master -> Worker)
Contains the definitive directive resolving a `CLARIFICATION_QUERY`. Injected directly into the worker's pane so work resumes immediately.

### 4. `APPROVAL_REQUEST` (Worker -> Master / User)
Used before potentially high-risk actions (e.g. running database migrations, modifying build scripts, external API calls):
```json
{
  "type": "APPROVAL_REQUEST",
  "payload": {
    "action": "RUN_MIGRATION",
    "command": "alembic upgrade head",
    "worktree": "/path/to/worktree",
    "justification": "Required to test new cache columns in dev database"
  }
}
```

### 5. `REVIEW_FEEDBACK` (Reviewer / Master -> Worker)
Sent following the mandatory Master Final Review Gate when changes require fixes:
```json
{
  "type": "REVIEW_FEEDBACK",
  "payload": {
    "status": "NEEDS_FIXES",
    "commit_sha": "a1b2c3d4",
    "findings": [
      {
        "file": "src/cache/redis.py",
        "line": 42,
        "severity": "CRITICAL",
        "issue": "Connection pool is not closed on shutdown, causing resource leak.",
        "fix_recommendation": "Register cleanup handler with atexit or FastAPI lifespan context."
      }
    ],
    "required_tests": ["tests/test_cache_lifecycle.py"]
  }
}
```
The existing worker session remains alive in its pane, reads the feedback, applies corrections, runs tests, creates a new commit on its branch, and re-submits for review.

### 6. `HANDOFF` (Worker -> Worker / Master)
Transfers intermediate data (e.g. Scout summary, Architect plan) to the subsequent stage of the pipeline without manual copy-pasting.

---

## 5. Harness Prompt Formatting Adapters

Because each agent harness parses user prompts with slightly different conventions, IHCP includes an outbound translation layer:

| Target Harness | Prompt Formatting Rules |
| :--- | :--- |
| **Antigravity (`agy`)** | First line: effort keyword (`think`, `think hard`, `think harder`, `ultrathink`). Formatted Markdown with file links and artifact tags. |
| **Pi (`pi`)** | CLI invocation uses `--provider <p> --model <m>`. Prompts feature persona injection at top (`Adopt this expert persona: <Expert Persona>`). |
| **Claude Code (`claude`)** | First line: literal effort trigger word (`low` -> `think`, `medium` -> `think hard`, `high` -> `think harder`, `xhigh` -> `ultrathink`). |
| **OpenCode (`opencode`)** | Formatted for CLI with task constraints and explicit test execution commands. |

This abstraction guarantees that an orchestrator running on Antigravity can dispatch tasks seamlessly to a Pi or Claude worker without the developer having to remember per-harness formatting idiosyncrasies.
