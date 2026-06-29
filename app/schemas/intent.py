from typing import Literal

from pydantic import BaseModel, Field


RouteName = Literal[
    "template_reply",
    "rag_answer",
    "template_then_rag",
    "clarify",
    "human",
    "chitchat",
    "unsupported",
]


class IntentResult(BaseModel):
    route: RouteName
    primary_intent: str
    secondary_intents: list[str] = Field(default_factory=list)
    sales_stage: str = "unknown"
    customer_sentiment: str | None = None
    confidence: float = Field(ge=0, le=1)
    need_template: bool = False
    need_rag: bool = False
    need_human: bool = False
    slots: dict = Field(default_factory=dict)
    reason: str | None = None
