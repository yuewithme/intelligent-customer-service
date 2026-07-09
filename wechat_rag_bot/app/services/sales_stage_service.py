from pydantic import BaseModel

from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.schemas.tag import TagResult


SALES_STAGES = {
    "unknown",
    "greeting",
    "need_discovery",
    "pain_confirmed",
    "solution_recommended",
    "price_discussed",
    "objection_handling",
    "order_intent",
    "after_sale",
    "human_pending",
}

NEED_READY_STAGES = {
    "pain_confirmed",
    "solution_recommended",
    "price_discussed",
    "objection_handling",
    "order_intent",
}

STAGE_ORDER = {
    "unknown": 0,
    "greeting": 1,
    "need_discovery": 2,
    "pain_confirmed": 3,
    "solution_recommended": 4,
    "price_discussed": 5,
    "objection_handling": 6,
    "order_intent": 7,
}


class SalesStageDecision(BaseModel):
    stage: str
    reason: str


def decide_sales_stage(
    *,
    user_state: UserState,
    intent: IntentResult,
    tag_result: TagResult,
) -> SalesStageDecision:
    current_stage = normalize_sales_stage(user_state.sales_stage)
    if intent.need_human or tag_result.risk_level == "high" or intent.route == "human":
        return SalesStageDecision(stage="human_pending", reason="human_required")

    if intent.primary_intent in {"ask_after_sale", "refund_request", "complaint"}:
        return SalesStageDecision(stage="after_sale", reason="after_sale_intent")

    if intent.primary_intent in {"order_intent", "payment_intent"}:
        return SalesStageDecision(stage="order_intent", reason="order_intent")

    if intent.primary_intent in {"price_objection", "discount_request", "hesitation", "trust_issue"}:
        return SalesStageDecision(stage="objection_handling", reason="objection_intent")

    if intent.primary_intent == "ask_price":
        if current_stage in NEED_READY_STAGES or _has_need_evidence(tag_result):
            return _advance_or_keep(
                current_stage,
                "price_discussed",
                "price_after_need_or_solution",
            )
        return _advance_or_keep(
            current_stage,
            "need_discovery",
            "price_before_need_discovery",
        )

    if _has_pain_evidence(tag_result):
        return _advance_or_keep(current_stage, "pain_confirmed", "pain_evidence")

    if _has_product_interest(tag_result) and current_stage in {"pain_confirmed", "need_discovery"}:
        return _advance_or_keep(
            current_stage,
            "solution_recommended",
            "product_interest_after_need",
        )

    stage_from_intent = normalize_sales_stage(intent.sales_stage)
    if stage_from_intent != "unknown":
        return _advance_or_keep(current_stage, stage_from_intent, "intent_sales_stage")

    return SalesStageDecision(stage=current_stage, reason="keep_current_stage")


def normalize_sales_stage(stage: str | None) -> str:
    return stage if isinstance(stage, str) and stage in SALES_STAGES else "unknown"


def _advance_or_keep(current_stage: str, next_stage: str, reason: str) -> SalesStageDecision:
    if STAGE_ORDER.get(next_stage, 0) < STAGE_ORDER.get(current_stage, 0):
        return SalesStageDecision(stage=current_stage, reason="keep_current_stage")
    return SalesStageDecision(stage=next_stage, reason=reason)


def _has_need_evidence(tag_result: TagResult) -> bool:
    return _has_pain_evidence(tag_result) or _has_product_interest(tag_result)


def _has_pain_evidence(tag_result: TagResult) -> bool:
    return any(label.startswith("pain_point:") for label in tag_result.labels)


def _has_product_interest(tag_result: TagResult) -> bool:
    return any(label.startswith("product_interest:") for label in tag_result.labels)
