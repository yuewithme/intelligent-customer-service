from datetime import datetime, timezone
from typing import Any

from app.schemas.intent import IntentResult
from app.schemas.reply_plan import BusinessFacts
from app.schemas.sales_flow import (
    CustomerSignal,
    SalesInterruption,
    SalesInterruptionType,
    SalesOpportunityStatus,
    SalesSignalResult,
    SalesStage,
    SalesStageDecision,
    SalesTransitionType,
)
from app.schemas.state import UserState
from app.schemas.tag import TagResult
from app.services.sales_signal_service import normalize_sales_signals
from app.services.sales_stage_catalog import normalize_sales_stage_reference


STAGE_ORDER = {stage.value: position for position, stage in enumerate(SalesStage, start=1)}
AFTER_SALE_INTENTS = {"ask_after_sale", "refund_request", "complaint"}
ENVIRONMENT_AND_FIT_SLOTS = {
    "region",
    "placement",
    "light",
    "ventilation",
    "temperature",
    "color_preference",
    "fragrance_preference",
    "difficulty_preference",
    "collection_preference",
    "budget",
    "selected_product_id",
    "selected_sku_id",
}


def decide_sales_stage(
    *,
    user_state: UserState,
    intent: IntentResult,
    tag_result: TagResult,
    signal_result: SalesSignalResult | None = None,
    business_facts: BusinessFacts | None = None,
) -> SalesStageDecision:
    """Return the only authoritative first-order stage decision."""

    normalized = signal_result or normalize_sales_signals(
        user_state=user_state,
        intent=intent,
        tag_result=tag_result,
        business_facts=business_facts,
    )
    opportunity = _active_opportunity(user_state)
    current_stage = _current_stage(user_state, opportunity)
    current_status = _opportunity_status(opportunity)
    signals = set(normalized.signals)

    interruption_type = _requested_interruption(intent, tag_result)
    if interruption_type is not None:
        existing = _parse_interruption(opportunity.get("interruption"))
        interruption = existing or SalesInterruption(
            type=interruption_type,
            reason=(
                "human_required"
                if interruption_type is SalesInterruptionType.HUMAN_PENDING
                else "after_sale_intent"
            ),
            resume_stage=current_stage,
            started_at=datetime.now(timezone.utc),
        )
        return _decision(
            current_stage,
            interruption.reason,
            previous_stage=current_stage,
            transition_type="interrupt",
            status=SalesOpportunityStatus.PAUSED,
            interruption=interruption,
            normalized=normalized,
        )

    existing_interruption = _parse_interruption(opportunity.get("interruption"))
    if existing_interruption is not None and current_status is SalesOpportunityStatus.PAUSED:
        if _interruption_resolved(normalized.slots):
            return _decision(
                existing_interruption.resume_stage,
                "interruption_resolved",
                previous_stage=current_stage,
                transition_type="resume",
                status=SalesOpportunityStatus.ACTIVE,
                normalized=normalized,
            )
        return _decision(
            current_stage,
            "interruption_active",
            previous_stage=current_stage,
            transition_type="interrupt",
            status=SalesOpportunityStatus.PAUSED,
            interruption=existing_interruption,
            normalized=normalized,
        )

    if CustomerSignal.PURCHASED in signals:
        return _decision(
            SalesStage.CLOSING,
            "trusted_purchase_confirmed",
            previous_stage=current_stage,
            transition_type="close",
            status=SalesOpportunityStatus.WON,
            normalized=normalized,
        )

    if CustomerSignal.PURCHASE_REJECTED in signals:
        return _decision(
            current_stage,
            "purchase_rejected",
            previous_stage=current_stage,
            transition_type="close",
            status=SalesOpportunityStatus.LOST,
            normalized=normalized,
        )

    loop_decision = _controlled_loop(current_stage, opportunity, normalized)
    if loop_decision is not None:
        stage, reason = loop_decision
        return _decision(
            stage,
            reason,
            previous_stage=current_stage,
            transition_type="loop",
            normalized=normalized,
        )

    starts_new = current_status in {
        SalesOpportunityStatus.WON,
        SalesOpportunityStatus.LOST,
        SalesOpportunityStatus.EXPIRED,
    } and _starts_new_opportunity(signals)
    decision_base = SalesStage.RAPPORT if starts_new else current_stage
    target, reason = _strongest_supported_stage(decision_base, normalized)
    transition_type = _transition_type(decision_base, target)
    if starts_new:
        current_status = SalesOpportunityStatus.ACTIVE
        reason = "new_first_order_opportunity"

    return _decision(
        target,
        reason,
        previous_stage=current_stage,
        transition_type=transition_type,
        status=current_status,
        normalized=normalized,
    )


def normalize_sales_stage(stage: str | SalesStage | None) -> str:
    normalized = normalize_sales_stage_reference(stage)
    return normalized.stage.value if normalized.stage is not None else "unknown"


def _strongest_supported_stage(
    current_stage: SalesStage,
    normalized: SalesSignalResult,
) -> tuple[SalesStage, str]:
    signals = set(normalized.signals)
    slots = normalized.slots

    if CustomerSignal.PAYMENT_CLAIMED in signals:
        return SalesStage.CLOSING, "payment_claim_requires_verification"
    if CustomerSignal.READY_TO_BUY in signals:
        return SalesStage.CLOSING, "order_intent"
    if CustomerSignal.OBJECTION in signals:
        return SalesStage.CLOSING, "objection_intent"
    if CustomerSignal.PRICE_INTEREST in signals:
        if _recommendation_has_basis(current_stage, slots):
            return SalesStage.TRIAL_CLOSE, "price_after_need_or_solution"
        return _no_weak_regression(
            current_stage,
            SalesStage.NEED_DISCOVERY,
            "price_before_need_discovery",
        )
    if CustomerSignal.VALUE_ACKNOWLEDGED in signals:
        if slots.get("selected_product_id") or slots.get("selected_sku_id"):
            return SalesStage.TRIAL_CLOSE, "value_acknowledged"
        return _forward_or_keep(current_stage, SalesStage.VALUE_BUILT, "value_acknowledged")
    if CustomerSignal.RECOMMENDATION_ENGAGED in signals:
        return _forward_or_keep(
            current_stage,
            SalesStage.VALUE_BUILT,
            "recommendation_engaged",
        )
    if _recommendation_ready(slots):
        return _forward_or_keep(
            current_stage,
            SalesStage.SOLUTION_RECOMMENDED,
            "recommendation_evidence_ready",
        )
    if CustomerSignal.PAIN_REVEALED in signals:
        return _forward_or_keep(
            current_stage,
            SalesStage.PAIN_DISCOVERY,
            "pain_revealed",
        )
    if signals & {
        CustomerSignal.SERVICE_NEED,
        CustomerSignal.PRODUCT_NEED,
        CustomerSignal.COMBINED_NEED,
        CustomerSignal.RESPONDED,
    }:
        return _forward_or_keep(
            current_stage,
            SalesStage.NEED_DISCOVERY,
            "need_or_response_signal",
        )
    return current_stage, "keep_current_stage"


def _controlled_loop(
    current_stage: SalesStage,
    opportunity: dict,
    normalized: SalesSignalResult,
) -> tuple[SalesStage, str] | None:
    if STAGE_ORDER[current_stage.value] < STAGE_ORDER[SalesStage.VALUE_BUILT.value]:
        return None
    incoming = set(normalized.incoming_slots)
    old_slots = opportunity.get("slots") if isinstance(opportunity.get("slots"), dict) else {}
    changed = {
        key
        for key in incoming
        if key not in old_slots or old_slots.get(key) != normalized.slots.get(key)
    }
    if current_stage is SalesStage.CLOSING and normalized.slots.get("blocker_resolved") is True:
        return SalesStage.TRIAL_CLOSE, "blocker_resolved"
    if "pain_point" in changed:
        return SalesStage.PAIN_DISCOVERY, "new_core_pain"
    if changed & ENVIRONMENT_AND_FIT_SLOTS:
        return SalesStage.SOLUTION_RECOMMENDED, "recommendation_context_changed"
    return None


def _recommendation_ready(slots: dict[str, Any]) -> bool:
    need_track = bool(slots.get("need_track"))
    if not need_track:
        return False
    product_basis = bool(slots.get("region") or slots.get("placement")) and bool(
        slots.get("budget")
        or slots.get("pain_point")
        or slots.get("color_preference")
        or slots.get("fragrance_preference")
        or slots.get("difficulty_preference")
    )
    service_basis = bool(slots.get("pain_point")) and bool(
        slots.get("current_care_context")
        or slots.get("plant_count")
        or slots.get("experience_level")
    )
    return product_basis or service_basis or bool(slots.get("selected_product_id"))


def _recommendation_has_basis(current_stage: SalesStage, slots: dict[str, Any]) -> bool:
    return STAGE_ORDER[current_stage.value] >= STAGE_ORDER[SalesStage.SOLUTION_RECOMMENDED.value] or bool(
        slots.get("selected_product_id") or slots.get("selected_sku_id")
    )


def _forward_or_keep(
    current: SalesStage,
    target: SalesStage,
    reason: str,
) -> tuple[SalesStage, str]:
    if STAGE_ORDER[target.value] < STAGE_ORDER[current.value]:
        return current, "keep_current_stage"
    return target, reason


def _no_weak_regression(
    current: SalesStage,
    target: SalesStage,
    reason: str,
) -> tuple[SalesStage, str]:
    return _forward_or_keep(current, target, reason)


def _transition_type(current: SalesStage, target: SalesStage) -> SalesTransitionType:
    difference = STAGE_ORDER[target.value] - STAGE_ORDER[current.value]
    if difference == 0:
        return "keep"
    if difference == 1:
        return "advance"
    if difference > 1:
        return "jump"
    return "loop"


def _decision(
    stage: str | SalesStage,
    reason: str,
    *,
    previous_stage: str | SalesStage | None,
    transition_type: SalesTransitionType,
    normalized: SalesSignalResult,
    status: SalesOpportunityStatus = SalesOpportunityStatus.ACTIVE,
    interruption: SalesInterruption | None = None,
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
        transition_type=transition_type,
        opportunity_status=status,
        evidence=normalized.evidence,
        signals=normalized.signals,
        interruption=interruption,
    )


def _current_stage(user_state: UserState, opportunity: dict) -> SalesStage:
    for value in (
        opportunity.get("current_stage"),
        opportunity.get("sales_stage"),
        user_state.sales_stage,
    ):
        normalized = normalize_sales_stage(value)
        if normalized != "unknown":
            return SalesStage(normalized)
    return SalesStage.RAPPORT


def _opportunity_status(opportunity: dict) -> SalesOpportunityStatus:
    try:
        return SalesOpportunityStatus(str(opportunity.get("status") or "active"))
    except ValueError:
        return SalesOpportunityStatus.ACTIVE


def _active_opportunity(user_state: UserState) -> dict:
    direct = user_state.metadata.get("active_opportunity")
    if isinstance(direct, dict):
        return direct
    profile = user_state.metadata.get("profile")
    if isinstance(profile, dict) and isinstance(profile.get("active_opportunity"), dict):
        return profile["active_opportunity"]
    return {}


def _requested_interruption(
    intent: IntentResult,
    tag_result: TagResult,
) -> SalesInterruptionType | None:
    if intent.need_human or tag_result.risk_level == "high" or intent.route == "human":
        return SalesInterruptionType.HUMAN_PENDING
    if intent.primary_intent in AFTER_SALE_INTENTS:
        return SalesInterruptionType.AFTER_SALE
    return None


def _parse_interruption(value: Any) -> SalesInterruption | None:
    if not isinstance(value, dict):
        return None
    try:
        return SalesInterruption.model_validate(value)
    except ValueError:
        return None


def _interruption_resolved(slots: dict[str, Any]) -> bool:
    return slots.get("interruption_resolved") is True


def _starts_new_opportunity(signals: set[CustomerSignal]) -> bool:
    return bool(
        signals
        & {
            CustomerSignal.SERVICE_NEED,
            CustomerSignal.PRODUCT_NEED,
            CustomerSignal.COMBINED_NEED,
            CustomerSignal.PAIN_REVEALED,
            CustomerSignal.PREFERENCE_REVEALED,
            CustomerSignal.PRICE_INTEREST,
            CustomerSignal.READY_TO_BUY,
        }
    )
