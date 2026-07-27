from typing import Literal

from pydantic import BaseModel, Field


ShadowReviewVerdict = Literal[
    "primary_better",
    "shadow_better",
    "tie",
    "both_bad",
    "uncertain",
    "excluded",
]


class ShadowFollowUp(BaseModel):
    needed: bool = False
    action: str | None = Field(default=None, max_length=256)
    due_in_hours: int | None = Field(default=None, ge=1, le=24 * 30)
    cancel_conditions: list[str] = Field(default_factory=list, max_length=8)


class ReplyShadowDecision(BaseModel):
    sales_stage: str = Field(min_length=1, max_length=128)
    route: Literal[
        "template_reply",
        "rag_answer",
        "human",
        "chitchat",
        "unsupported",
        "clarify",
    ]
    sales_action: str = Field(min_length=1, max_length=128)
    reply: str = Field(default="", max_length=8000)
    need_human: bool = False
    next_action: str | None = Field(default=None, max_length=256)
    follow_up: ShadowFollowUp = Field(default_factory=ShadowFollowUp)
    facts_used: list[str] = Field(default_factory=list, max_length=16)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2000)


class ReplyShadowAnnotationRequest(BaseModel):
    verdict: ShadowReviewVerdict
    error_tags: list[str] = Field(default_factory=list, max_length=16)
    note: str | None = Field(default=None, max_length=2000)
    annotator_id: str = Field(min_length=1, max_length=128)
