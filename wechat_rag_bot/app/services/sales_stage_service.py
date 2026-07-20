from datetime import datetime, timezone

from app.schemas.intent import IntentResult
from app.schemas.sales_flow import (
    CustomerSignal,
    SalesInterruption,
    SalesInterruptionType,
    SalesStage,
    SalesStageDecision,
)
from app.schemas.state import UserState
from app.schemas.tag import TagResult
from app.services.sales_stage_catalog import normalize_sales_stage_reference


NEED_READY_STAGES = {
    SalesStage.PAIN_DISCOVERY.value,
    SalesStage.SOLUTION_RECOMMENDED.value,
    SalesStage.VALUE_BUILT.value,
    SalesStage.TRIAL_CLOSE.value,
    SalesStage.CLOSING.value,
}

STAGE_ORDER = {
    "unknown": 0,
    **{stage.value: position for position, stage in enumerate(SalesStage, start=1)},
}


def decide_sales_stage(
    *,
    user_state: UserState,
    intent: IntentResult,
    tag_result: TagResult,
) -> SalesStageDecision:
    current_stage = normalize_sales_stage(user_state.sales_stage)
    active_stage = current_stage if current_stage != "unknown" else SalesStage.RAPPORT.value

    if intent.need_human or tag_result.risk_level == "high" or intent.route == "human":
        return _interruption_decision(
            active_stage,
            SalesInterruptionType.HUMAN_PENDING,
            "human_required",
        )

    if intent.primary_intent in {"ask_after_sale", "refund_request", "complaint"}:
        return _interruption_decision(
            active_stage,
            SalesInterruptionType.AFTER_SALE,
            "after_sale_intent",
        )

    if intent.primary_intent in {"order_intent", "payment_intent"}:
        return _decision(
            SalesStage.CLOSING,
            "order_intent",
            previous_stage=current_stage,
            signals=(CustomerSignal.READY_TO_BUY,),
        )

    if intent.primary_intent in {"price_objection", "discount_request", "hesitation", "trust_issue"}:
        return _decision(
            SalesStage.CLOSING,
            "objection_intent",
            previous_stage=current_stage,
            signals=(CustomerSignal.OBJECTION,),
        )

    if intent.primary_intent == "ask_price":
        if current_stage in NEED_READY_STAGES or _has_need_evidence(tag_result):
            return _advance_or_keep(
                current_stage,
                SalesStage.TRIAL_CLOSE.value,
                "price_after_need_or_solution",
            )
        return _advance_or_keep(
            current_stage,
            SalesStage.NEED_DISCOVERY.value,
            "price_before_need_discovery",
        )

    if _has_pain_evidence(tag_result):
        return _advance_or_keep(
            current_stage,
            SalesStage.PAIN_DISCOVERY.value,
            "pain_evidence",
        )

    if _has_product_interest(tag_result) and current_stage in {
        SalesStage.PAIN_DISCOVERY.value,
        SalesStage.NEED_DISCOVERY.value,
    }:
        return _advance_or_keep(
            current_stage,
            SalesStage.SOLUTION_RECOMMENDED.value,
            "product_interest_after_need",
        )

    stage_from_intent = normalize_sales_stage(intent.sales_stage)
    if stage_from_intent != "unknown":
        return _advance_or_keep(current_stage, stage_from_intent, "intent_sales_stage")

    return _decision(active_stage, "keep_current_stage", previous_stage=current_stage)


def normalize_sales_stage(stage: str | SalesStage | None) -> str:
    normalized = normalize_sales_stage_reference(stage)
    return normalized.stage.value if normalized.stage is not None else "unknown"


def _decision(
    stage: str | SalesStage,
    reason: str,
    *,
    previous_stage: str | SalesStage | None = None,
    signals: tuple[CustomerSignal, ...] = (),
) -> SalesStageDecision:
    normalized_stage = normalize_sales_stage(stage)
    if normalized_stage == "unknown":
        normalized_stage = SalesStage.RAPPORT.value
    normalized_previous = normalize_sales_stage(previous_stage)
    return SalesStageDecision(
        stage=SalesStage(normalized_stage),
        previous_stage=(
            SalesStage(normalized_previous)
            if normalized_previous != "unknown"
            else None
        ),
        reason=reason,
        signals=signals,
    )


def _interruption_decision(
    resume_stage: str,
    interruption_type: SalesInterruptionType,
    reason: str,
) -> SalesStageDecision:
    stage = SalesStage(normalize_sales_stage(resume_stage))
    return SalesStageDecision(
        stage=stage,
        previous_stage=stage,
        reason=reason,
        interruption=SalesInterruption(
            type=interruption_type,
            reason=reason,
            resume_stage=stage,
            started_at=datetime.now(timezone.utc),
        ),
    )


def _advance_or_keep(current_stage: str, next_stage: str, reason: str) -> SalesStageDecision:
    current_stage = normalize_sales_stage(current_stage)
    next_stage = normalize_sales_stage(next_stage)
    if STAGE_ORDER.get(next_stage, 0) < STAGE_ORDER.get(current_stage, 0):
        return _decision(current_stage, "keep_current_stage", previous_stage=current_stage)
    return _decision(next_stage, reason, previous_stage=current_stage)


def _has_need_evidence(tag_result: TagResult) -> bool:
    return _has_pain_evidence(tag_result) or _has_product_interest(tag_result)


def _has_pain_evidence(tag_result: TagResult) -> bool:
    return any(label.startswith("pain_point:") for label in tag_result.labels)


def _has_product_interest(tag_result: TagResult) -> bool:
    return any(label.startswith("product_interest:") for label in tag_result.labels)
