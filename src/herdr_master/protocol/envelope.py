"""Inter-Harness Communication Protocol (IHCP) Message Envelopes."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class MessageType(str, Enum):
    TASK_DISPATCH = "TASK_DISPATCH"
    CLARIFICATION_QUERY = "CLARIFICATION_QUERY"
    DECISION_REPLY = "DECISION_REPLY"
    APPROVAL_REQUEST = "APPROVAL_REQUEST"
    REVIEW_FEEDBACK = "REVIEW_FEEDBACK"
    HANDOFF = "HANDOFF"
    HEARTBEAT_STATUS = "HEARTBEAT_STATUS"


class AgentEndpoint(BaseModel):
    agent_id: str
    pane_id: Optional[str] = None
    harness: str
    role: str
    model: Optional[str] = None


class Attachment(BaseModel):
    name: str
    path: str
    sha256: Optional[str] = None


class IHCPMessage(BaseModel):
    schema_version: str = "1.0"
    id: str = Field(default_factory=lambda: f"msg-{uuid4()}")
    correlation_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    type: MessageType
    sender: AgentEndpoint
    recipient: AgentEndpoint
    payload: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[Attachment] = Field(default_factory=list)
