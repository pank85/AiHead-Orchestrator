"""SessionSnapshot Pydantic models for Herdr Protocol 22."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProtocolMismatchError(ValueError):
    """Raised when Herdr protocol version does not match expected Protocol 22."""

    pass


class BaseSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class WorkspaceSnapshot(BaseSnapshotModel):
    workspace_id: str
    label: Optional[str] = None
    number: Optional[int] = None
    active_tab_id: Optional[str] = None
    pane_count: Optional[int] = 0
    tab_count: Optional[int] = 0
    focused: bool = False
    agent_status: Optional[str] = None


class TabSnapshot(BaseSnapshotModel):
    tab_id: str
    workspace_id: Optional[str] = None
    label: Optional[str] = None
    number: Optional[int] = None
    pane_count: Optional[int] = 0
    focused: bool = False
    agent_status: Optional[str] = None


class PaneSnapshot(BaseSnapshotModel):
    pane_id: str
    workspace_id: Optional[str] = None
    tab_id: Optional[str] = None
    agent: Optional[str] = None
    agent_status: Optional[str] = None
    cwd: Optional[str] = None
    focused: bool = False
    terminal_id: Optional[str] = None
    terminal_title: Optional[str] = None
    terminal_title_stripped: Optional[str] = None
    scroll: Optional[Dict[str, Any]] = None


class AgentSnapshot(BaseSnapshotModel):
    pane_id: str
    workspace_id: Optional[str] = None
    tab_id: Optional[str] = None
    agent: Optional[str] = None
    name: Optional[str] = None
    agent_status: Optional[str] = None
    cwd: Optional[str] = None
    focused: bool = False
    interactive_ready: Optional[bool] = None


class SessionSnapshot(BaseSnapshotModel):
    protocol: int
    version: str = "0.9.5"
    workspaces: List[WorkspaceSnapshot] = Field(default_factory=list)
    tabs: List[TabSnapshot] = Field(default_factory=list)
    panes: List[PaneSnapshot] = Field(default_factory=list)
    agents: List[AgentSnapshot] = Field(default_factory=list)
    layouts: List[Dict[str, Any]] = Field(default_factory=list)
    focused_pane_id: Optional[str] = None
    focused_tab_id: Optional[str] = None
    focused_workspace_id: Optional[str] = None

    def __init__(self, **data: Any) -> None:
        proto = data.get("protocol")
        if proto is not None and proto != 22:
            raise ProtocolMismatchError(f"Unsupported Herdr protocol version {proto}; expected 22")
        super().__init__(**data)

    @model_validator(mode="after")
    def validate_protocol_22(self) -> "SessionSnapshot":
        if self.protocol != 22:
            raise ProtocolMismatchError(
                f"Unsupported Herdr protocol version {self.protocol}; expected 22"
            )
        return self

    @classmethod
    def from_response(cls, response: Dict[str, Any]) -> "SessionSnapshot":
        """Parse from RPC response structure, unwrapping result/snapshot keys."""
        data = response
        if "result" in data and isinstance(data["result"], dict):
            data = data["result"]
        if "snapshot" in data and isinstance(data["snapshot"], dict):
            data = data["snapshot"]
        proto = data.get("protocol")
        if proto is not None and proto != 22:
            raise ProtocolMismatchError(f"Unsupported Herdr protocol version {proto}; expected 22")
        try:
            return cls.model_validate(data)
        except Exception as exc:
            if "expected 22" in str(exc):
                raise ProtocolMismatchError(
                    f"Unsupported Herdr protocol version {data.get('protocol')}; expected 22"
                ) from exc
            raise
