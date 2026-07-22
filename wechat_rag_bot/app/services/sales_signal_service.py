from collections.abc import Iterable
from typing import Any

from app.schemas.intent import IntentResult
from app.schemas.reply_plan import BusinessFacts
from app.schemas.sales_flow import (
    CustomerSignal,
    SalesSignalResult,
    SalesStageEvidence,
)
from app.schemas.state import UserState
from app.schemas.tag import TagResult


AFTER_SALE_INTENTS = {"ask_after_sale", "refund_request", "complaint"}
SERVICE_INTENTS = {"care_question", "usage_question", "process_question"}
PRODUCT_INTENTS = {
    "product_query",
    "product_recommendation",
    "recommend_product",
    "order_intent",
    "payment_intent",
}
PRICE_INTENTS = {"ask_price", "price_query"}
OBJECTION_INTENTS = {
    "price_objection",
    "discount_request",
    "hesitation",
    "trust_issue",
}
READY_TO_BUY_INTENTS = {"order_intent", "payment_intent"}
PAYMENT_CLAIM_INTENTS = {"order_completed", "payment_success"}
REJECTION_INTENTS = {"purchase_rejection", "not_interested"}

SLOT_ALIASES = {
    "need_type": "need_track",
    "demand_type": "need_track",
    "goal": "desired_outcome",
    "desired_result": "desired_outcome",
    "problem": "pain_point",
    "issue": "pain_point",
    "location": "region",
    "province": "region",
    "city": "region",
    "growing_place": "placement",
    "sunlight": "light",
    "airflow": "ventilation",
    "preferred_color": "color_preference",
    "preferred_fragrance": "fragrance_preference",
    "price_range": "budget",
    "product_id": "selected_product_id",
    "sku_id": "selected_sku_id",
    "count": "quantity",
}

BLOCKER_ALIASES = {
    "communication": "other",
    "unknown": "other",
}
BLOCKER_TYPES = {
    "price",
    "trust",
    "care_risk",
    "product_fit",
    "choice",
    "timing",
    "other",
}

PAID_ORDER_STATUSES = {
    "paid",
    "payment_success",
    "order_completed",
    "completed",
    "wait_seller_send_goods",
    "wait_buyer_confirm_goods",
    "trade_buyer_signed",
}


def normalize_sales_signals(
    *,
    user_state: UserState,
    intent: IntentResult,
    tag_result: TagResult,
    message: Any | None = None,
    business_facts: BusinessFacts | None = None,
) -> SalesSignalResult:
    """Normalize model output and trusted facts into deterministic sales inputs."""

    opportunity = _active_opportunity(user_state)
    existing_slots = _normalize_slots(opportunity.get("slots"))
    profile_slots = _normalize_slots(user_state.metadata.get("known_slots"))
    tag_slots = _normalize_slots(tag_result.entities)
    intent_slots = _normalize_slots(intent.slots)
    incoming_slots = {**tag_slots, **intent_slots}
    slots = {**profile_slots, **existing_slots, **incoming_slots}

    evidence: list[SalesStageEvidence] = []
    signals: list[CustomerSignal] = []

    for key, value in profile_slots.items():
        _add_evidence(evidence, f"slot:{key}", "profile", value)
    for key, value in existing_slots.items():
        _add_evidence(evidence, f"slot:{key}", "opportunity", value)
    for key, value in incoming_slots.items():
        _add_evidence(evidence, f"slot:{key}", "message", value)

    for value in intent.sales_signals:
        signal = _customer_signal(value)
        if signal is None or signal is CustomerSignal.PURCHASED:
            continue
        _add_signal(signals, evidence, signal, "message")

    intents = {intent.primary_intent, *intent.secondary_intents}
    domains = {
        value
        for value in {intent.primary_domain, *intent.secondary_domains}
        if value
    }
    goals = {
        value for value in {intent.primary_goal, *intent.secondary_goals} if value
    }
    issues = set(intent.issues)
    if intents & SERVICE_INTENTS or "care_service" in domains or "request_material" in goals:
        _add_signal(signals, evidence, CustomerSignal.SERVICE_NEED, "message")
    if intents & PRODUCT_INTENTS or domains & {
        "customer_need",
        "product_solution",
        "commercial_decision",
        "purchase_transaction",
    }:
        _add_signal(signals, evidence, CustomerSignal.PRODUCT_NEED, "message")
    if intents & PRICE_INTENTS or issues & {"price", "discount"}:
        _add_signal(signals, evidence, CustomerSignal.PRICE_INTEREST, "message")
    if intents & OBJECTION_INTENTS or goals & {
        "express_objection",
        "negotiate",
        "defer_decision",
    }:
        _add_signal(signals, evidence, CustomerSignal.OBJECTION, "message")
    if intents & READY_TO_BUY_INTENTS or goals & {"confirm_choice", "purchase", "pay"}:
        _add_signal(signals, evidence, CustomerSignal.READY_TO_BUY, "message")
    if intents & PAYMENT_CLAIM_INTENTS:
        _add_signal(signals, evidence, CustomerSignal.PAYMENT_CLAIMED, "message")
    if intents & REJECTION_INTENTS or "decline_purchase" in goals:
        _add_signal(signals, evidence, CustomerSignal.PURCHASE_REJECTED, "message")

    text = str(getattr(message, "message", "") or "").strip()
    if text and intent.primary_intent not in {"greeting", *AFTER_SALE_INTENTS}:
        _add_signal(signals, evidence, CustomerSignal.RESPONDED, "message")
    if _contains_any(text, ("已经付款", "已经付了", "我付了", "支付成功", "已经买了")):
        _add_signal(signals, evidence, CustomerSignal.PAYMENT_CLAIMED, "message")
    if _contains_any(text, ("这个不错", "挺合适", "可以接受", "这个可以")):
        _add_signal(signals, evidence, CustomerSignal.VALUE_ACKNOWLEDGED, "message")
    if _contains_any(text, ("有什么优势", "具体介绍", "对比一下", "这个怎么样")):
        _add_signal(signals, evidence, CustomerSignal.RECOMMENDATION_ENGAGED, "message")

    for label in tag_result.labels:
        if label.startswith("pain_point:"):
            _add_signal(signals, evidence, CustomerSignal.PAIN_REVEALED, "message")
        elif label.startswith("product_interest:") and not (
            intent.primary_intent in SERVICE_INTENTS and not intents & PRODUCT_INTENTS
        ):
            _add_signal(signals, evidence, CustomerSignal.PRODUCT_NEED, "message")

    if slots.get("pain_point") or slots.get("failed_history"):
        _add_signal(signals, evidence, CustomerSignal.PAIN_REVEALED, "message")
    if any(
        slots.get(key)
        for key in (
            "color_preference",
            "fragrance_preference",
            "difficulty_preference",
            "collection_preference",
        )
    ):
        _add_signal(signals, evidence, CustomerSignal.PREFERENCE_REVEALED, "message")
    if slots.get("selected_product_id") or slots.get("selected_sku_id"):
        _add_signal(signals, evidence, CustomerSignal.RECOMMENDATION_ENGAGED, "message")

    if CustomerSignal.SERVICE_NEED in signals and CustomerSignal.PRODUCT_NEED in signals:
        _add_signal(signals, evidence, CustomerSignal.COMBINED_NEED, "message")
    if not slots.get("need_track"):
        if CustomerSignal.COMBINED_NEED in signals:
            slots["need_track"] = "combined"
        elif CustomerSignal.SERVICE_NEED in signals:
            slots["need_track"] = "service"
        elif CustomerSignal.PRODUCT_NEED in signals:
            slots["need_track"] = "product"

    trusted_purchase = _trusted_purchase_evidence(business_facts)
    if trusted_purchase is not None:
        _add_signal(
            signals,
            evidence,
            CustomerSignal.PURCHASED,
            "commerce",
            value=trusted_purchase,
            trusted=True,
        )

    return SalesSignalResult(
        signals=tuple(signals),
        slots=slots,
        incoming_slots=tuple(incoming_slots),
        evidence=tuple(_dedupe_evidence(evidence)),
    )


def _active_opportunity(user_state: UserState) -> dict:
    direct = user_state.metadata.get("active_opportunity")
    if isinstance(direct, dict):
        return direct
    profile = user_state.metadata.get("profile")
    if isinstance(profile, dict) and isinstance(profile.get("active_opportunity"), dict):
        return profile["active_opportunity"]
    return {}


def _normalize_slots(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, Any] = {}
    for raw_key, raw_value in value.items():
        if raw_value in (None, "", []):
            continue
        key = SLOT_ALIASES.get(str(raw_key), str(raw_key))
        if key in {"original_route", "sales_stage_reason"}:
            continue
        if key == "decision_blocker":
            blocker = _normalize_blocker(raw_value)
            if blocker:
                result[key] = blocker
            continue
        if key == "need_track":
            result[key] = _normalize_need_track(raw_value)
            continue
        result[key] = raw_value
    return result


def _normalize_blocker(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    blocker_type = BLOCKER_ALIASES.get(str(value.get("type") or ""), str(value.get("type") or ""))
    if blocker_type not in BLOCKER_TYPES:
        blocker_type = "other"
    detail = str(value.get("detail") or "").strip()
    if not detail and blocker_type == "other":
        return None
    return {"type": blocker_type, "detail": detail}


def _normalize_need_track(value: Any) -> str:
    normalized = str(value).strip().lower()
    aliases = {
        "care": "service",
        "service_need": "service",
        "purchase": "product",
        "product_need": "product",
        "both": "combined",
        "mixed": "combined",
    }
    return aliases.get(normalized, normalized)


def _trusted_purchase_evidence(facts: BusinessFacts | None) -> str | None:
    if facts is None or not isinstance(facts.tool_state, dict):
        return None
    state = facts.tool_state
    for key in ("payment_status", "order_status"):
        value = str(state.get(key) or "").strip().lower()
        if value in PAID_ORDER_STATUSES:
            return f"{key}:{value}"
    if state.get("payment_success") is True or state.get("order_completed") is True:
        return "verified_completion_flag"
    orders = state.get("orders")
    if isinstance(orders, list):
        for order in orders:
            if not isinstance(order, dict):
                continue
            status = str(order.get("status") or "").strip().lower()
            if status in PAID_ORDER_STATUSES:
                order_no = str(order.get("order_no") or "verified")
                return f"order:{order_no}:{status}"
    return None


def _customer_signal(value: str) -> CustomerSignal | None:
    try:
        return CustomerSignal(str(value))
    except ValueError:
        return None


def _add_signal(
    signals: list[CustomerSignal],
    evidence: list[SalesStageEvidence],
    signal: CustomerSignal,
    source: str,
    *,
    value: Any = True,
    trusted: bool = False,
) -> None:
    if signal not in signals:
        signals.append(signal)
    _add_evidence(evidence, f"signal:{signal.value}", source, value, trusted=trusted)


def _add_evidence(
    evidence: list[SalesStageEvidence],
    code: str,
    source: str,
    value: Any,
    *,
    trusted: bool = False,
) -> None:
    evidence.append(
        SalesStageEvidence(
            code=code,
            source=source,
            value=value,
            trusted=trusted,
        )
    )


def _dedupe_evidence(values: Iterable[SalesStageEvidence]) -> list[SalesStageEvidence]:
    seen: set[tuple[str, str, str]] = set()
    result: list[SalesStageEvidence] = []
    for value in values:
        key = (value.code, value.source, repr(value.value))
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _contains_any(text: str, values: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(value.lower() in lowered for value in values)
