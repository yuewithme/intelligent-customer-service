import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.services.intent_example_service import retrieve_intent_examples
from app.domains.decisioning.services.intent_service import _validated_intent, classify_intent
from app.domains.decisioning.services.reply_builder import build_chitchat_reply
from app.domains.decisioning.services.intent_taxonomy_service import load_intent_taxonomy
from app.domains.decisioning.services.rule_guard_service import check_rules
from app.domains.decisioning.schemas.intent import IntentResult


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="taxonomy_v1",
        channel="api",
        user_id="customer",
        session_id="session",
        message=text,
        kb_id="kb_default",
    )


def test_compact_taxonomy_is_available_as_machine_readable_catalog():
    catalog = load_intent_taxonomy()

    assert catalog["version"] == "2.0"
    assert catalog["counts"] == {"domain": 5, "goal": 16, "issue": 18}
    material = next(
        card for card in catalog["labels"] if card["id"] == "material_resource"
    )
    assert material["name"] == "资料与资源"


@pytest.mark.asyncio
async def test_material_request_has_explicit_goal_and_keeps_runtime_compatibility():
    intent = await classify_intent(
        _message("麻烦把全套新手养兰图文资料发我一份"),
        UserState(user_id="customer"),
    )

    assert intent.primary_domain == "care"
    assert intent.primary_goal == "ask_information"
    assert intent.issues == ["material_resource"]
    assert intent.primary_intent == "care_question"
    assert intent.route == "rag_answer"
    assert intent.slots["resource_type"] == "orchid_material"


@pytest.mark.asyncio
async def test_refund_guard_has_priority_over_material_delivery():
    intent = await classify_intent(
        _message("我要退款，养兰资料也发我一份"),
        UserState(user_id="customer"),
    )

    assert intent.primary_goal == "request_refund_return"
    assert intent.route == "human"


def test_dgi_result_projects_to_existing_route_and_intent_contract():
    intent = _validated_intent(
        {
            "primary_domain": "commerce",
            "secondary_domains": ["care"],
            "primary_goal": "express_objection",
            "issues": ["price_value", "care_confidence"],
            "scope": "in_scope",
            "confidence": 0.92,
            "evidence": [
                {
                    "text": "有点贵，而且怕养不好",
                    "dimension": "goal",
                    "label": "express_objection",
                }
            ],
        }
    )

    assert intent.route == "template_then_rag"
    assert intent.primary_intent == "price_objection"
    assert intent.secondary_intents == ["care_question"]
    assert intent.need_template is True
    assert intent.need_rag is True


def test_chitchat_replies_are_short_and_match_the_social_act():
    greeting = build_chitchat_reply(
        IntentResult(
            route="chitchat",
            primary_intent="greeting",
            confidence=0.95,
            slots={"chitchat_kind": "greeting"},
        )
    )
    thanks = build_chitchat_reply(
        IntentResult(
            route="chitchat",
            primary_intent="greeting",
            confidence=0.95,
            slots={"chitchat_kind": "thanks"},
        )
    )

    assert greeting.answer == "您好，我在的。"
    assert thanks.answer == "不客气。"
    assert len(greeting.answer) <= 15
    assert len(thanks.answer) <= 15


@pytest.mark.asyncio
async def test_semantic_candidates_cover_each_dimension():
    candidates = await retrieve_intent_examples("我怕养不活，想要一份新手教程", top_k=2)

    assert {candidate["kind"] for candidate in candidates} >= {"domain", "goal", "issue"}
    assert sum(candidate["kind"] == "domain" for candidate in candidates) == 2
    assert sum(candidate["kind"] == "goal" for candidate in candidates) == 2
    assert sum(candidate["kind"] == "issue" for candidate in candidates) == 2


@pytest.mark.asyncio
async def test_mentioning_customer_service_guidance_does_not_force_handoff():
    intent = await check_rules(
        _message("有没有客服指导养护？"),
        UserState(user_id="customer"),
    )

    assert intent is None
