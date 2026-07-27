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


IntentScope = Literal["in_scope", "ambiguous", "out_of_scope"]


class IntentEvidence(BaseModel):
    text: str
    dimension: Literal["domain", "goal", "issue"]
    label: str


class IntentResult(BaseModel):
    route: RouteName
    primary_intent: str
    secondary_intents: list[str] = Field(default_factory=list)
    primary_domain: str | None = None
    secondary_domains: list[str] = Field(default_factory=list)
    primary_goal: str | None = None
    secondary_goals: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    scope: IntentScope = "in_scope"
    evidence: list[IntentEvidence] = Field(default_factory=list)
    taxonomy_version: str = "2.0"
    classifier_source: str = "unknown"
    classifier_provider: str | None = None
    classifier_model: str | None = None
    raw_prediction: dict = Field(default_factory=dict, exclude=True)
    sales_stage: str = "unknown"
    sales_signals: list[str] = Field(default_factory=list)
    customer_sentiment: str | None = None
    confidence: float = Field(ge=0, le=1)
    need_template: bool = False
    need_rag: bool = False
    need_human: bool = False
    slots: dict = Field(default_factory=dict)
    reason: str | None = None
