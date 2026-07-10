from typing import Any, Literal

from pydantic import BaseModel, Field


ReplyAction = Literal[
    "template_reply",
    "rag_answer",
    "human",
    "chitchat",
    "unsupported",
    "clarify",
]


class BusinessFacts(BaseModel):
    snapshot: str = ""
    tool_state: dict[str, Any] = Field(default_factory=dict)
    skus: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.snapshot or self.tool_state or self.skus)


class DecisionStep(BaseModel):
    source: str
    proposed_action: str
    reason: str
    accepted: bool = True


class ReplyPlan(BaseModel):
    action: ReplyAction
    original_route: str | None = None
    reason: str
    need_human: bool = False
    next_action: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    template_ids: list[str] = Field(default_factory=list)
    prompt_block_ids: list[str] = Field(default_factory=list)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    business_facts: BusinessFacts = Field(default_factory=BusinessFacts)
    decision_trace: list[DecisionStep] = Field(default_factory=list)
