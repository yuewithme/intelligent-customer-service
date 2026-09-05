from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.reply import OutboundMessage


@dataclass
class AgentExecutionContext:
    message: NormalizedMessage
    user_state: UserState
    workspace: dict[str, Any]
    prepared: dict[str, list[OutboundMessage]] = field(default_factory=dict)
    tool_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    handoff: dict[str, Any] | None = None
