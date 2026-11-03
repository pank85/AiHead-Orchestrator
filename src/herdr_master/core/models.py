"""Domain models for Herdr-Master sessions, workers, and worktrees."""

from datetime import datetime
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MasterSession(BaseModel):
    session_id: str
    herdr_session: str = "default"
    workspace_id: str
    pane_id: str
    harness: HarnessKind
    role: str = "root-orchestrator"
    active_workers: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)
