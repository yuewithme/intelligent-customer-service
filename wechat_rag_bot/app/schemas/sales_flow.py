from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SalesStage(str, Enum):
    RAPPORT = "rapport"
    NEED_DISCOVERY = "need_discovery"
    PAIN_DISCOVERY = "pain_discovery"
    SOLUTION_RECOMMENDED = "solution_recommended"
    VALUE_BUILT = "value_built"
    TRIAL_CLOSE = "trial_close"
    CLOSING = "closing"


class SalesInterruptionType(str, Enum):
    AFTER_SALE = "after_sale"
    HUMAN_PENDING = "human_pending"


class SalesOpportunityStatus(str, Enum):
    ACTIVE = "active"
    WON = "won"
    LOST = "lost"
    PAUSED = "paused"
    EXPIRED = "expired"


class CustomerSignal(str, Enum):
    RESPONDED = "responded"
    SERVICE_NEED = "service_need"
    PRODUCT_NEED = "product_need"
    COMBINED_NEED = "combined_need"
    PAIN_REVEALED = "pain_revealed"
    PREFERENCE_REVEALED = "preference_revealed"
    RECOMMENDATION_ENGAGED = "recommendation_engaged"
    VALUE_ACKNOWLEDGED = "value_acknowledged"
    PRICE_INTEREST = "price_interest"
    READY_TO_BUY = "ready_to_buy"
    OBJECTION = "objection"
    PURCHASE_REJECTED = "purchase_rejected"
    PURCHASED = "purchased"


class SalesAction(str, Enum):
    ANSWER_CURRENT_QUESTION = "answer_current_question"
    BUILD_RAPPORT = "build_rapport"
    DISCOVER_NEED_TRACK = "discover_need_track"
    DISCOVER_PAIN = "discover_pain"
    RECOMMEND_SOLUTION = "recommend_solution"
    BUILD_VALUE = "build_value"
    TRIAL_CLOSE = "trial_close"
    RESOLVE_BLOCKER = "resolve_blocker"
    CLOSE_ORDER = "close_order"


class SalesStageDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: SalesStage
    display_name: str
    sequence: int = Field(ge=1)
    objective: str
    entry_evidence_any: list[str] = Field(default_factory=list)
    exit_evidence_any: list[str] = Field(default_factory=list)
    allowed_actions: list[SalesAction] = Field(default_factory=list)
    required_slot_groups: list[list[str]] = Field(default_factory=list)
    prohibited_behaviors: list[str] = Field(default_factory=list)


class SalesStageEvidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    source: Literal["message", "profile", "opportunity", "commerce", "manual"]
    value: Any = None
    trusted: bool = False


class SalesInterruption(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: SalesInterruptionType
    reason: str
    resume_stage: SalesStage
    started_at: datetime


class SalesStageDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    stage: SalesStage | None = None
    previous_stage: SalesStage | None = None
    reason: str
    evidence: list[SalesStageEvidence] = Field(default_factory=list)
    signals: list[CustomerSignal] = Field(default_factory=list)
    interruption: SalesInterruption | None = None


class SalesStageNormalization(BaseModel):
    model_config = ConfigDict(frozen=True)

    original_value: str | None = None
    stage: SalesStage | None = None
    interruption_type: SalesInterruptionType | None = None
    signals: list[CustomerSignal] = Field(default_factory=list)
    is_legacy: bool = False
