from __future__ import annotations

from typing import Any


DOMAIN_MAP = {
    "customer_need": "product",
    "product_solution": "product",
    "care_service": "care",
    "commercial_decision": "commerce",
    "purchase_transaction": "commerce",
    "customer_service": "customer_service",
    "conversation": "conversation",
    "out_of_scope": "conversation",
}

GOAL_MAP = {
    "express_need": "express_interest",
    "express_interest": "express_interest",
    "provide_information": "provide_information",
    "ask_information": "ask_information",
    "compare_options": "ask_information",
    "request_material": "ask_information",
    "seek_recommendation": "seek_help",
    "seek_solution": "seek_help",
    "express_objection": "express_objection",
    "negotiate": "express_objection",
    "affirm": "confirm",
    "confirm_choice": "confirm",
    "defer_decision": "defer_decision",
    "deny": "reject",
    "decline_purchase": "reject",
    "purchase": "transact",
    "pay": "transact",
    "query_status": "request_service",
    "request_service": "request_service",
    "request_refund_return": "request_refund_return",
    "complain": "complain",
    "request_human": "request_human",
    "social": "social",
    "end_conversation": "end_conversation",
    "unclear": "unclear",
}

ISSUE_MAP = {
    "purchase_purpose": "customer_fit",
    "experience_level": "customer_fit",
    "preference": "customer_fit",
    "budget": "customer_fit",
    "growing_environment": "growing_environment",
    "environment_care": "growing_environment",
    "pain_and_outcome": "pain_outcome",
    "product_information": "product_information",
    "recommendation_fit": "product_selection",
    "product_comparison": "product_selection",
    "stock_availability": "stock_availability",
    "care_general": "routine_care",
    "watering_fertilizing": "routine_care",
    "medium_repotting": "medium_repotting",
    "symptom_disease_recovery": "plant_problem",
    "price": "price_value",
    "discount": "price_value",
    "product_value": "price_value",
    "quality_trust": "trust_guarantee",
    "service_guarantee": "trust_guarantee",
    "care_confidence": "care_confidence",
    "sku_quantity": "purchase_selection",
    "order_process": "order_process",
    "payment": "payment",
    "delivery": "delivery",
    "after_sale_policy": "after_sale",
    "received_problem": "after_sale",
    "refund_return": "after_sale",
}

SPLIT_GOALS = {"request_material"}
SPLIT_ISSUES = {"sku_quantity"}


def requires_v2_reclassification(payload: dict[str, Any]) -> bool:
    goals = {
        str(payload.get("primary_goal") or ""),
        *_strings(payload.get("secondary_goals")),
    }
    issues = set(_strings(payload.get("issues")))
    return bool(goals & SPLIT_GOALS or issues & SPLIT_ISSUES)


def migrate_dgi_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    primary_domain = DOMAIN_MAP.get(
        str(payload.get("primary_domain") or ""), "conversation"
    )
    primary_goal = GOAL_MAP.get(
        str(payload.get("primary_goal") or ""), "unclear"
    )
    secondary_domains = _mapped_values(
        payload.get("secondary_domains"), DOMAIN_MAP, exclude=primary_domain
    )
    secondary_goals = _mapped_values(
        payload.get("secondary_goals"), GOAL_MAP, exclude=primary_goal
    )
    issues = _mapped_values(payload.get("issues"), ISSUE_MAP)
    if str(payload.get("primary_goal") or "") == "request_material":
        _append_unique(issues, "material_resource")
    scope = str(payload.get("scope") or "in_scope")
    if str(payload.get("primary_domain") or "") == "out_of_scope":
        scope = "out_of_scope"
    return {
        "primary_domain": primary_domain,
        "secondary_domains": secondary_domains,
        "primary_goal": primary_goal,
        "secondary_goals": secondary_goals,
        "issues": issues,
        "scope": scope,
        "taxonomy_version": "2.0",
    }


def _mapped_values(
    values: Any,
    mapping: dict[str, str],
    *,
    exclude: str | None = None,
) -> list[str]:
    result: list[str] = []
    for value in _strings(values):
        mapped = mapping.get(value)
        if mapped and mapped != exclude:
            _append_unique(result, mapped)
    return result


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item) for item in values if str(item or "").strip()]


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
