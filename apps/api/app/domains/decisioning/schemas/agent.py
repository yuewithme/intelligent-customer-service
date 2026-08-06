from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


CustomerSignal = Literal[
    "none",
    "soft_refusal",
    "explicit_refusal",
]


class AgentToolCall(BaseModel):
    call_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)


class AgentVisibleMessage(BaseModel):
    type: Literal["text", "prepared"]
    content: str | None = None
    ref: str | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.type == "text" and not str(self.content or "").strip():
            raise ValueError("text message requires content")
        if self.type == "prepared" and not str(self.ref or "").strip():
            raise ValueError("prepared message requires ref")
        return self


class AgentFinalResponse(BaseModel):
    messages: list[AgentVisibleMessage] = Field(min_length=1, max_length=5)
    need_human: bool = False
    handoff_reason: str | None = None
    next_action: str | None = None


class AgentTurnDecision(BaseModel):
    commercial_judgment: str = Field(min_length=1, max_length=800)
    relationship_purpose: str = Field(min_length=1, max_length=400)
    customer_signal: CustomerSignal = "none"
    tool_calls: list[AgentToolCall] = Field(default_factory=list, max_length=4)
    final_response: AgentFinalResponse | None = None

    @model_validator(mode="after")
    def require_progress(self):
        if not self.tool_calls and self.final_response is None:
            raise ValueError("decision must call a tool or provide a final response")
        if self.tool_calls and self.final_response is not None:
            raise ValueError(
                "tool calls and final response are mutually exclusive"
            )
        return self


class AgentToolResult(BaseModel):
    call_id: str
    tool: str
    status: Literal[
        "found",
        "not_found",
        "invalid_arguments",
        "temporarily_unavailable",
        "forbidden",
        "prepared",
        "recorded",
        "scheduled",
        "notified",
        "pending",
    ]
    data: dict[str, Any] = Field(default_factory=dict)
    prepared_refs: list[str] = Field(default_factory=list)
