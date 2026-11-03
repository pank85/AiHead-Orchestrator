"""Domain models for Herdr-Master sessions, workers, worktrees, and Ergo pipelines."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkerState(str, Enum):
    SPAWNING = "SPAWNING"
    IDLE = "IDLE"
    WORKING = "WORKING"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FIXING = "FIXING"
    APPROVED = "APPROVED"
    SQUASHING = "SQUASHING"
    LANDED = "LANDED"
    FAILED = "FAILED"


class HarnessKind(str, Enum):
    AGY = "agy"
    PI = "pi"
    CLAUDE = "claude"
    OPENCODE = "opencode"
    CODEX = "codex"
    GEMINI = "gemini"


class EffortLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"


class HarnessBinding(BaseModel):
    """Specification of an agent harness, model slug, and reasoning effort."""

    kind: HarnessKind
    model: str
    effort: Optional[EffortLevel] = None
    label: Optional[str] = None
    enabled: bool = True
    order: int = 0

    def to_slug(self, include_effort: bool = True) -> str:
        """Render binding as kind:model[:effort]."""
        from herdr_master.core.slug import format_binding

        return format_binding(self, include_effort=include_effort)

    @classmethod
    def from_slug(
        cls,
        slug_str: str,
        default_effort: Optional[EffortLevel] = None,
        label: Optional[str] = None,
        enabled: bool = True,
        order: int = 0,
    ) -> "HarnessBinding":
        """Parse binding from kind:model[:effort] string."""
        from herdr_master.core.slug import parse_binding

        return parse_binding(
            slug_str,
            default_effort=default_effort,
            label=label,
            enabled=enabled,
            order=order,
        )


class Squad(BaseModel):
    """Squad configuration containing core Ergo pipeline roles and ordered worker pool."""

    scout: HarnessBinding = Field(
        default_factory=lambda: HarnessBinding.from_slug("agy:gemini-3.8-flash-high")
    )
    architect: HarnessBinding = Field(
        default_factory=lambda: HarnessBinding.from_slug("pi:openai-codex/gpt-6.1-sol:high")
    )
    reviewer: HarnessBinding = Field(
        default_factory=lambda: HarnessBinding.from_slug("agy:gemini-4-argon-high")
    )
    worker_pool: List[HarnessBinding] = Field(
        default_factory=lambda: [
            HarnessBinding.from_slug("pi:xai/grok-4.7", label="general_backend", order=0),
            HarnessBinding.from_slug(
                "pi:openai-codex/gpt-6.1-terra", label="concurrency_logic", order=1
            ),
            HarnessBinding.from_slug(
                "agy:gemini-4-argon-high", label="frontend_multimodal", order=2
            ),
        ]
    )

    def reviewer_model_family(self) -> str:
        """Extract broad family prefix for reviewer model."""
        model = self.reviewer.model.lower()
        if "/" in model:
            return model.split("/")[0]
        for family in ("gemini", "claude", "gpt", "grok", "deepseek", "codex"):
            if family in model:
                return family
        return self.reviewer.kind.value

    def architect_model_family(self) -> str:
        """Extract broad family prefix for architect model."""
        model = self.architect.model.lower()
        if "/" in model:
            return model.split("/")[0]
        for family in ("gemini", "claude", "gpt", "grok", "deepseek", "codex"):
            if family in model:
                return family
        return self.architect.kind.value

    def has_reviewer_model_family_conflict(self) -> bool:
        """Check if Reviewer and Architect share the same model family."""
        return self.reviewer_model_family() == self.architect_model_family()


class TaskNode(BaseModel):
    """A planned task node in an Ergo pipeline run."""

    task_id: str
    description: str
    files: List[str] = Field(default_factory=list)
    dependency: str = "INDEPENDENT"
    expert_persona: Optional[str] = None
    binding: Optional[HarnessBinding] = None
    interfaces: Optional[str] = None
    verification: Optional[str] = None
    status: WorkerState = WorkerState.SPAWNING
    worker_id: Optional[str] = None


class PipelinePhase(str, Enum):
    PREFLIGHT = "PREFLIGHT"
    SCOUT = "SCOUT"
    ARCHITECT = "ARCHITECT"
    APPROVAL_GATE = "APPROVAL_GATE"
    CODERS = "CODERS"
    REVIEW = "REVIEW"
    FIX_LOOP = "FIX_LOOP"
    LANDING = "LANDING"
    CLEANUP = "CLEANUP"
    ABORTED = "ABORTED"
    FAILED = "FAILED"


class PipelineRun(BaseModel):
    """Execution state of an Ergo pipeline run."""

    run_id: str
    phase: PipelinePhase = PipelinePhase.PREFLIGHT
    intent: str
    squad: Squad = Field(default_factory=Squad)
    tasks: List[TaskNode] = Field(default_factory=list)
    workspace_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WorkerSession(BaseModel):
    worker_id: str
    pane_id: str
    tab_id: Optional[str] = None
    harness: HarnessKind
    model: str
    effort: EffortLevel = EffortLevel.HIGH
    worktree_path: str
    branch: str
    state: WorkerState = WorkerState.SPAWNING
    watcher_id: Optional[str] = None
    last_commit_sha: Optional[str] = None
    review_status: str = "PENDING"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MasterSession(BaseModel):
    session_id: str
    herdr_session: str = "default"
    workspace_id: str
    pane_id: str
    harness: HarnessKind
    role: str = "root-orchestrator"
    active_workers: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)
