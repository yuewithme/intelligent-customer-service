from typing import Literal

from pydantic import BaseModel, Field


TalkScriptStatus = Literal["matched", "handoff", "pass_through"]


class CandidateQuestion(BaseModel):
    question_id: str
    scene_id: str
    sub_scene_name: str | None = None
    standard_question: str
    core_intent: str | None = None
    positive_examples: str | None = None
    negative_examples: str | None = None
    required_conditions: str | None = None
    exclude_conditions: str | None = None
    confidence_threshold: float = 0.75
    priority: int = 0


class ClassifierDecision(BaseModel):
    matched: bool
    question_id: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    need_slot_filling: bool = False
    need_human: bool = False
    reason: str | None = None


class TalkScriptMatchResult(BaseModel):
    status: TalkScriptStatus
    success: bool = False
    scene_id: str | None = None
    question_id: str | None = None
    template_id: str | None = None
    answer: str = ""
    confidence: float = 0.0
    need_slot_filling: bool = False
    need_human: bool = False
    reason: str | None = None
    candidate_question_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class HumanHandoffResult(BaseModel):
    requested: bool
    status: str = "pending"
    reason: str
    metadata: dict = Field(default_factory=dict)
