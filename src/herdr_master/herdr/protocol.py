"""Strict Protocol 22 response records used at safety-sensitive boundaries."""

from __future__ import annotations

from typing import Annotated, Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from herdr_master.herdr.socket import HerdrConnectionError

UInt64 = Annotated[int, Field(ge=0, le=2**64 - 1)]
TokenKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,32}$")]


class _ProtocolRecord(BaseModel):
    # Protocol 22 permits extra fields. Required and known fields are not coerced.
    model_config = ConfigDict(strict=True, extra="ignore")


class AgentSessionInfo(_ProtocolRecord):
    source: str
    agent: str
    kind: Literal["id", "path"]
    value: str


class AgentInfo(_ProtocolRecord):
    terminal_id: str
    agent_status: Literal["idle", "working", "blocked", "done", "unknown"]
    workspace_id: str
    tab_id: str
    pane_id: str
    focused: bool
    revision: UInt64
    agent: Optional[str] = None
    agent_session: Optional[AgentSessionInfo] = None
    completion_seq: Optional[UInt64] = None
    cwd: Optional[str] = None
    display_agent: Optional[str] = None
    foreground_cwd: Optional[str] = None
    interactive_ready: bool = False
    launch_pending: bool = False
    name: Optional[str] = None
    screen_detection_skipped: bool = False
    state_change_seq: UInt64 = 0
    state_labels: Dict[str, str] = Field(default_factory=dict)
    terminal_title: Optional[str] = None
    terminal_title_stripped: Optional[str] = None
    title: Optional[str] = None
    tokens: Dict[TokenKey, str] = Field(default_factory=dict, max_length=32)


class _AgentPrompted(_ProtocolRecord):
    type: Literal["agent_prompted"]
    agent: AgentInfo


class PaneRead(_ProtocolRecord):
    pane_id: str
    workspace_id: str
    tab_id: str
    source: Literal["visible", "recent", "recent_unwrapped", "detection"]
    format: Literal["text", "ansi"]
    text: str
    revision: UInt64
    truncated: bool


class _PaneReadResult(_ProtocolRecord):
    type: Literal["pane_read"]
    read: PaneRead


def parse_prompt_result(result: Any) -> AgentInfo:
    """Validate a prompt success without exposing prompt or agent contents in errors."""
    try:
        return _AgentPrompted.model_validate(result).agent
    except ValidationError as exc:
        raise HerdrConnectionError("Invalid agent.prompt result") from exc


def parse_pane_read_result(result: Any) -> PaneRead:
    """Validate a tagged Protocol 22 pane.read result."""
    try:
        return _PaneReadResult.model_validate(result).read
    except ValidationError as exc:
        raise HerdrConnectionError("Invalid pane.read result") from exc


def parse_pane_read(read: Any) -> PaneRead:
    """Validate an unwrapped pane.read record at the controller boundary."""
    try:
        return PaneRead.model_validate(read)
    except ValidationError as exc:
        raise HerdrConnectionError("Invalid pane.read record") from exc
