"""AppState store for Herdr-Master TUI."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from herdr_master.core.models import (
    EffortLevel,
    HarnessBinding,
    HarnessKind,
    MasterSession,
    PipelinePhase,
    PipelineRun,
    Squad,
    TaskNode,
    WorkerSession,
    WorkerState,
)


class AppState(BaseModel):
    """Central reactive application state store for the TUI."""

    session_name: str = "default"
    workspace_label: str = "ergo-cockpit"
    workspace_id: str = "ws-01"
    target_branch: str = "develop"
    branch_status: str = "CLEAN"
    socket_connected: bool = True
    active_agents_count: int = 0
    active_deck_mode: int = 3  # 1: Output, 2: Diff, 3: Pipeline, 4: Mailbox
    selected_target_id: Optional[str] = "scout"

    squad: Squad = Field(default_factory=Squad)
    pipeline_run: Optional[PipelineRun] = None
    masters: Dict[str, MasterSession] = Field(default_factory=dict)
    workers: Dict[str, WorkerSession] = Field(default_factory=dict)
    output_scrollback: Dict[str, str] = Field(default_factory=dict)
    diff_content: Dict[str, str] = Field(default_factory=dict)
    mailbox_envelopes: List[Dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def create_demo_state(cls) -> "AppState":
        """Generate rich demo state for offline testing and visualization."""
        squad = Squad(
            scout=HarnessBinding.from_slug("agy:gemini-3.8-flash-high", label="codebase_recon"),
            architect=HarnessBinding.from_slug(
                "pi:openai-codex/gpt-6.1-sol:high", label="plan_design"
            ),
            reviewer=HarnessBinding.from_slug("agy:gemini-4-argon-high", label="code_review_gate"),
            worker_pool=[
                HarnessBinding.from_slug("pi:xai/grok-4.7", label="general_backend", order=0),
                HarnessBinding.from_slug(
                    "pi:openai-codex/gpt-6.1-terra:high", label="concurrency_logic", order=1
                ),
                HarnessBinding.from_slug(
                    "agy:gemini-4-argon-high", label="frontend_multimodal", order=2
                ),
            ],
        )

        tasks = [
            TaskNode(
                task_id="task-1",
                description="Core domain models & slug parser",
                files=["src/herdr_master/core/slug.py", "src/herdr_master/core/models.py"],
                dependency="INDEPENDENT",
                expert_persona="Protocol & Grammar Specialist",
                binding=squad.worker_pool[0],
                status=WorkerState.DONE,
                worker_id="coder-1",
            ),
            TaskNode(
                task_id="task-2",
                description="Herdr Socket Client (Protocol 22)",
                files=["src/herdr_master/herdr/socket.py", "src/herdr_master/herdr/client.py"],
                dependency="INDEPENDENT",
                expert_persona="Async Networking & Protocol Engineer",
                binding=squad.worker_pool[1],
                status=WorkerState.WORKING,
                worker_id="coder-2",
            ),
            TaskNode(
                task_id="task-3",
                description="Textual TUI Cockpit & Ergo pipeline view",
                files=[
                    "src/herdr_master/ui/tui/app.py",
                    "src/herdr_master/ui/tui/widgets/pipeline_view.py",
                ],
                dependency="DEPENDS ON: task-1",
                expert_persona="TUI & Visual Layout Architect",
                binding=squad.worker_pool[2],
                status=WorkerState.SPAWNING,
                worker_id="coder-3",
            ),
        ]

        run = PipelineRun(
            run_id="run-20261103-01",
            phase=PipelinePhase.CODERS,
            intent=(
                "Implement Herdr-Master core functionality and cockpit "
                "according to GROK_IMPLEMENTATION_PLAN.md"
            ),
            squad=squad,
            tasks=tasks,
            workspace_id="ws-01",
        )

        workers = {
            "coder-1": WorkerSession(
                worker_id="coder-1",
                pane_id="ws-01:p1",
                tab_id="ws-01:t1",
                harness=HarnessKind.PI,
                model="xai/grok-4.7",
                worktree_path=".herdr-worktrees/herdm-coder-1",
                branch="herdm/run-01/task-1",
                state=WorkerState.DONE,
            ),
            "coder-2": WorkerSession(
                worker_id="coder-2",
                pane_id="ws-01:p2",
                tab_id="ws-01:t2",
                harness=HarnessKind.PI,
                model="openai-codex/gpt-6.1-terra",
                effort=EffortLevel.HIGH,
                worktree_path=".herdr-worktrees/herdm-coder-2",
                branch="herdm/run-01/task-2",
                state=WorkerState.WORKING,
            ),
            "coder-3": WorkerSession(
                worker_id="coder-3",
                pane_id="ws-01:p3",
                tab_id="ws-01:t3",
                harness=HarnessKind.AGY,
                model="gemini-4-argon-high",
                worktree_path=".herdr-worktrees/herdm-coder-3",
                branch="herdm/run-01/task-3",
                state=WorkerState.SPAWNING,
            ),
        }

        output_scrollback = {
            "coder-1": (
                "\x1b[32m✓\x1b[0m Unit tests passed: 17 passed in 0.45s\n"
                "\x1b[34m[INFO]\x1b[0m Worktree committed: a9b42c1 feat(core): slug parser\n"
                "Status: Turn settled (idle)"
            ),
            "coder-2": (
                "[2026-11-03 14:55:10] Connecting to Unix socket\n"
                "[2026-11-03 14:55:12] Handshake complete: Protocol 22 acknowledged.\n"
                "Streaming events..."
            ),
            "coder-3": "[2026-11-03 14:56:00] Initializing git worktree for task-3...",
        }

        diff_content = {
            "coder-1": """diff --git a/src/herdr_master/core/slug.py b/src/herdr_master/core/slug.py
new file mode 100644
--- /dev/null
+++ b/src/herdr_master/core/slug.py
@@ -0,0 +1,45 @@
+def parse_binding(raw: str):
+    # Implement binding grammar: kind:model_ref[:effort]
+    ...
""",
        }

        masters = {
            "master-main": MasterSession(
                session_id="master-main",
                herdr_session="default",
                workspace_id="ws-01",
                pane_id="p-master",
                harness=HarnessKind.AGY,
                role="root-orchestrator",
                child_workers=["coder-1", "coder-2", "coder-3"],
            )
        }

        return cls(
            session_name="default",
            workspace_label="ergo-20261103",
            workspace_id="ws-01",
            target_branch="develop",
            branch_status="CLEAN",
            socket_connected=True,
            active_agents_count=3,
            active_deck_mode=3,
            selected_target_id="coder-2",
            squad=squad,
            pipeline_run=run,
            masters=masters,
            workers=workers,
            output_scrollback=output_scrollback,
            diff_content=diff_content,
        )
