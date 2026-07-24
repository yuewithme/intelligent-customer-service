from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "intent_taxonomy_v1.json"
DIMENSIONS = ("domain", "goal", "issue")

LEGACY_TO_DGI: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "greeting": ("conversation", "social", ()),
    "profile_answer": ("customer_need", "provide_information", ()),
    "ask_price": ("commercial_decision", "ask_information", ("price",)),
    "price_query": ("commercial_decision", "ask_information", ("price",)),
    "price_objection": ("commercial_decision", "express_objection", ("price",)),
    "discount_request": ("commercial_decision", "negotiate", ("discount",)),
    "ask_logistics": ("purchase_transaction", "ask_information", ("delivery",)),
    "ask_after_sale": ("customer_service", "ask_information", ("after_sale_policy",)),
    "order_intent": ("purchase_transaction", "purchase", ("order_process",)),
    "payment_intent": ("purchase_transaction", "pay", ("payment",)),
    "order_query": ("purchase_transaction", "query_status", ("delivery",)),
    "product_query": ("product_solution", "ask_information", ("product_information",)),
    "product_recommendation": ("product_solution", "seek_recommendation", ("recommendation_fit",)),
    "recommend_product": ("product_solution", "seek_recommendation", ("recommendation_fit",)),
    "knowledge_question": ("product_solution", "ask_information", ("product_information",)),
    "care_question": ("care_service", "seek_solution", ("care_general",)),
    "process_question": ("care_service", "ask_information", ("care_general",)),
    "usage_question": ("care_service", "ask_information", ("care_general",)),
    "refund_request": ("customer_service", "request_refund_return", ("refund_return",)),
    "complaint": ("customer_service", "complain", ()),
    "human_request": ("customer_service", "request_human", ()),
    "purchase_rejection": ("commercial_decision", "decline_purchase", ()),
    "unsupported": ("out_of_scope", "unclear", ()),
    "unknown": ("conversation", "unclear", ()),
}

CARE_ISSUES = {
    "care_general",
    "watering_fertilizing",
    "environment_care",
    "medium_repotting",
    "symptom_disease_recovery",
    "care_confidence",
}
COMMERCIAL_ISSUES = {
    "price",
    "discount",
    "product_value",
    "quality_trust",
    "service_guarantee",
    "sku_quantity",
    "order_process",
    "payment",
    "delivery",
    "after_sale_policy",
    "received_problem",
    "refund_return",
}
HUMAN_GOALS = {"request_refund_return", "complain", "request_human"}
CONVERSATION_GOALS = {"affirm", "deny", "social", "end_conversation"}
TRANSACTION_GOALS = {
    "express_objection",
    "negotiate",
    "confirm_choice",
    "defer_decision",
    "decline_purchase",
    "purchase",
    "pay",
    "query_status",
    "request_service",
}


@lru_cache(maxsize=1)
def load_intent_taxonomy() -> dict:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=3)
def taxonomy_cards(kind: str) -> tuple[dict, ...]:
    return tuple(
        card for card in load_intent_taxonomy()["labels"] if card["kind"] == kind
    )


@lru_cache(maxsize=3)
def taxonomy_values(kind: str) -> tuple[str, ...]:
    return tuple(card["id"] for card in taxonomy_cards(kind))


def normalize_taxonomy_value(kind: str, value: Any, fallback: str | None = None) -> str | None:
    normalized = str(value or "").strip()
    return normalized if normalized in taxonomy_values(kind) else fallback


def prepare_intent_payload(raw: dict) -> dict:
    payload = dict(raw)
    supplied_dgi = bool(payload.get("primary_domain") or payload.get("primary_goal"))
    if not supplied_dgi:
        domain, goal, issues = LEGACY_TO_DGI.get(
            str(payload.get("primary_intent") or "unknown"),
            LEGACY_TO_DGI["unknown"],
        )
        payload.setdefault("primary_domain", domain)
        payload.setdefault("primary_goal", goal)
        payload.setdefault("issues", list(issues))

    payload["primary_domain"] = normalize_taxonomy_value(
        "domain", payload.get("primary_domain"), "conversation"
    )
    payload["primary_goal"] = normalize_taxonomy_value(
        "goal", payload.get("primary_goal"), "unclear"
    )
    payload["secondary_domains"] = _normalize_values(
        "domain", payload.get("secondary_domains"), exclude=payload["primary_domain"]
    )
    payload["secondary_goals"] = _normalize_values(
        "goal", payload.get("secondary_goals"), exclude=payload["primary_goal"]
    )
    payload["issues"] = _normalize_values("issue", payload.get("issues"))
    payload["scope"] = _normalize_scope(payload)
    payload["evidence"] = _normalize_evidence(payload.get("evidence"))

    if supplied_dgi:
        payload.update(project_dgi_to_runtime(payload))
    return payload


def project_dgi_to_runtime(payload: dict) -> dict:
    domain = str(payload.get("primary_domain") or "conversation")
    domains = {domain, *_as_list(payload.get("secondary_domains"))}
    goal = str(payload.get("primary_goal") or "unclear")
    issues = set(_as_list(payload.get("issues")))
    scope = str(payload.get("scope") or "in_scope")

    primary_intent = _legacy_intent(domain, goal, issues, scope)
    route = _route(domain, domains, goal, issues, scope)
    return {
        "route": route,
        "primary_intent": primary_intent,
        "secondary_intents": _legacy_secondary_intents(domains, issues, primary_intent),
        "need_template": route in {"template_reply", "template_then_rag"},
        "need_rag": route in {"rag_answer", "template_then_rag"},
        "need_human": route == "human",
    }


def format_candidate_cards(candidates: list[dict] | None) -> str:
    grouped: dict[str, list[dict]] = {kind: [] for kind in DIMENSIONS}
    examples: list[dict] = []
    for candidate in candidates or []:
        kind = candidate.get("kind")
        if kind in grouped:
            grouped[kind].append(candidate)
        elif kind == "example" or candidate.get("text"):
            examples.append(candidate)
    if not any(grouped.values()):
        grouped = {kind: list(taxonomy_cards(kind)) for kind in DIMENSIONS}

    blocks: list[str] = []
    for kind in DIMENSIONS:
        blocks.append(f"### {kind.upper()} 候选")
        for card in grouped[kind]:
            positives = "；".join(card.get("positive_examples", [])[:3]) or "无"
            negatives = "；".join(card.get("negative_examples", [])[:2]) or "无"
            blocks.append(
                f"- {card['id']}｜{card.get('name', '')}：{card.get('definition', '')} "
                f"正例：{positives} 反例：{negatives}"
            )
    if examples:
        blocks.append("### 历史标注样例（仅辅助理解，最终仍输出 D/G/I）")
        for example in examples:
            blocks.append(
                f"- 文本：{example.get('text', '')}；旧意图："
                f"{example.get('primary_intent', 'unknown')}"
            )
    return "\n".join(blocks)


def format_candidate_cards_compact(candidates: list[dict] | None) -> str:
    grouped: dict[str, list[dict]] = {kind: [] for kind in DIMENSIONS}
    for candidate in candidates or []:
        kind = candidate.get("kind")
        if kind in grouped:
            grouped[kind].append(candidate)
    if not any(grouped.values()):
        grouped = {
            kind: list(taxonomy_cards(kind))[:3]
            for kind in DIMENSIONS
        }

    lines: list[str] = []
    for kind in DIMENSIONS:
        values = []
        for card in grouped[kind][:3]:
            definition = " ".join(str(card.get("definition") or "").split())[:120]
            values.append(f"{card['id']}={definition}")
        lines.append(f"{kind}: " + " | ".join(values))
    return "\n".join(lines)


def _normalize_values(kind: str, values: Any, *, exclude: str | None = None) -> list[str]:
    result: list[str] = []
    for value in _as_list(values):
        normalized = normalize_taxonomy_value(kind, value)
        if normalized and normalized != exclude and normalized not in result:
            result.append(normalized)
    return result


def _normalize_scope(payload: dict) -> str:
    if payload.get("primary_domain") == "out_of_scope":
        return "out_of_scope"
    value = str(payload.get("scope") or "in_scope")
    return value if value in {"in_scope", "ambiguous", "out_of_scope"} else "ambiguous"


def _normalize_evidence(values: Any) -> list[dict]:
    result: list[dict] = []
    for value in _as_list(values):
        if not isinstance(value, dict):
            continue
        dimension = str(value.get("dimension") or "")
        label = normalize_taxonomy_value(dimension, value.get("label")) if dimension in DIMENSIONS else None
        text = str(value.get("text") or "").strip()[:200]
        if text and label:
            result.append({"text": text, "dimension": dimension, "label": label})
    return result


def _legacy_intent(domain: str, goal: str, issues: set[str], scope: str) -> str:
    if scope == "out_of_scope" or domain == "out_of_scope":
        return "unsupported"
    if goal == "request_refund_return":
        return "refund_request"
    if goal == "complain":
        return "complaint"
    if goal == "request_human":
        return "human_request"
    if goal in CONVERSATION_GOALS:
        return "greeting"
    if goal == "unclear":
        return "unknown"
    if goal == "request_material":
        return "knowledge_question"
    if goal == "negotiate":
        return "discount_request"
    if goal == "express_objection":
        return "price_objection"
    if goal == "decline_purchase":
        return "purchase_rejection"
    if goal in {"purchase", "confirm_choice"}:
        return "order_intent"
    if goal == "pay":
        return "payment_intent"
    if goal == "query_status":
        return "ask_logistics" if "delivery" in issues else "order_query"
    if goal == "request_service":
        return "ask_after_sale"
    if goal == "ask_information" and "price" in issues:
        return "ask_price"
    if goal == "ask_information" and "delivery" in issues:
        return "ask_logistics"
    if domain == "care_service":
        return "care_question"
    if domain == "product_solution":
        return "knowledge_question"
    return "knowledge_question"


def _route(domain: str, domains: set[str], goal: str, issues: set[str], scope: str) -> str:
    if goal in HUMAN_GOALS:
        return "human"
    if scope == "out_of_scope" or domain == "out_of_scope":
        return "unsupported"
    if scope == "ambiguous" or goal == "unclear":
        return "clarify"
    if goal in CONVERSATION_GOALS or domain == "conversation":
        return "chitchat"
    has_care = bool(domains & {"care_service"} or issues & CARE_ISSUES)
    has_commercial = bool(
        domains & {"commercial_decision", "purchase_transaction", "customer_service"}
        or issues & COMMERCIAL_ISSUES
        or goal in TRANSACTION_GOALS
    )
    if has_care and has_commercial:
        return "template_then_rag"
    if has_commercial:
        return "template_reply"
    return "rag_answer"


def _legacy_secondary_intents(
    domains: set[str], issues: set[str], primary_intent: str
) -> list[str]:
    result: list[str] = []
    if ("care_service" in domains or issues & CARE_ISSUES) and primary_intent != "care_question":
        result.append("care_question")
    return result


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]
