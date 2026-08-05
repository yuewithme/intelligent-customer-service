from types import SimpleNamespace

import pytest

from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.decisioning.schemas.reply_plan import BusinessFacts
from app.domains.customers.schemas.state import UserState


def _intent() -> IntentResult:
    return IntentResult(
        route="rag_answer",
        primary_intent="care_question",
        confidence=0.9,
        need_rag=True,
    )


def _message() -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_001",
        channel="api",
        user_id="user_001",
        session_id="session_001",
        message="hello",
        kb_id="kb_default",
    )


def _reply(answer: str) -> FinalReply:
    return FinalReply(
        answer=answer,
        reply_type="rag",
        route="rag_answer",
        sources=[{"doc_id": "doc_1", "file_name": "doc.md"}],
        usage={"tokens": 1},
        metadata={"path": answer},
    )


def _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator):
    async def normalize_chat_request(request):
        del request
        return _message()

    async def get_user_state(user_id, session_id):
        return UserState(user_id=user_id, session_id=session_id)

    async def get_profile_bundle(user_id):
        del user_id
        return {"profile": {}, "recent_memories": []}

    async def check_rules(message, user_state):
        del message, user_state
        return None

    async def retrieve_intent_examples(message, top_k):
        del message, top_k
        return []

    async def classify_intent(message, user_state, candidates):
        del message, user_state, candidates
        return _intent()

    async def decide_route(intent, user_state, message):
        del intent, user_state, message
        return PolicyDecision(route="rag_answer", reason="test_policy")

    async def build_business_context(message, **kwargs):
        del kwargs
        assert message.trace_id == "trace_001"
        return BusinessFacts()

    async def execute_reply_plan(**kwargs):
        del kwargs
        return _reply("planned reply")

    async def noop(*args, **kwargs):
        del args, kwargs

    def no_shadow(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(chat_orchestrator, "normalize_chat_request", normalize_chat_request)
    monkeypatch.setattr(chat_orchestrator, "get_user_state", get_user_state)
    monkeypatch.setattr(chat_orchestrator, "get_profile_bundle", get_profile_bundle)
    monkeypatch.setattr(chat_orchestrator, "check_rules", check_rules)
    monkeypatch.setattr(
        chat_orchestrator,
        "retrieve_intent_examples",
        retrieve_intent_examples,
    )
    monkeypatch.setattr(chat_orchestrator, "classify_intent", classify_intent)
    monkeypatch.setattr(chat_orchestrator, "decide_route", decide_route)
    monkeypatch.setattr(
        chat_orchestrator,
        "build_business_context",
        build_business_context,
    )
    monkeypatch.setattr(chat_orchestrator, "execute_reply_plan", execute_reply_plan)
    monkeypatch.setattr(chat_orchestrator, "update_user_state", noop)
    monkeypatch.setattr(chat_orchestrator, "append_conversation_memory", noop)
    monkeypatch.setattr(
        chat_orchestrator, "apply_deterministic_profile_update", noop
    )
    monkeypatch.setattr(chat_orchestrator, "record_chat_log", noop)
    monkeypatch.setattr(chat_orchestrator, "record_ai_turn", noop)
    monkeypatch.setattr(
        chat_orchestrator,
        "schedule_reply_shadow_evaluation",
        no_shadow,
    )


@pytest.mark.asyncio
async def test_soft_decline_attaches_membership_card_in_the_same_turn(monkeypatch):
    from app.services import chat_orchestrator

    async def build_card_context(*args, **kwargs):
        del args
        assert kwargs["business_action"] == "catalog_search"
        return BusinessFacts(tool_state={"commerce_type": "product", "status": "found"})

    async def render_card(message, facts):
        del message, facts
        return FinalReply(
            answer="卡片文案",
            reply_type="template",
            route="template_reply",
            outbound_messages=[
                OutboundMessage(type="text", content="卡片文案"),
                OutboundMessage(type="link_card", content='{"item_id":"membership-39"}'),
            ],
            metadata={"commerce_action": {"card_sent": True}},
        )

    monkeypatch.setattr(chat_orchestrator, "build_commerce_context", build_card_context)
    monkeypatch.setattr(chat_orchestrator, "render_business_reply", render_card)
    reply = await chat_orchestrator._attach_membership_card_for_service_offer(
        reply=_reply("先回答需求，再介绍服务"),
        message=_message(),
        user_state=UserState(user_id="user_001"),
        intent=_intent(),
        sales_action=SimpleNamespace(reason="service_offer_soft_decline_value_card"),
    )

    assert [item.type for item in reply.outbound_messages] == ["link_card"]
    assert reply.metadata["commerce_action"]["card_sent"] is True


@pytest.mark.asyncio
async def test_care_question_does_not_attach_membership_card_in_the_same_turn(
    monkeypatch,
):
    from app.services import chat_orchestrator

    async def unexpected(*args, **kwargs):
        del args, kwargs
        raise AssertionError("客户问题还在当前轮时不应立即查询商品卡")

    monkeypatch.setattr(chat_orchestrator, "build_commerce_context", unexpected)
    original = _reply("浇水看植料干湿，施肥要薄肥勤施。")
    reply = await chat_orchestrator._attach_membership_card_for_service_offer(
        reply=original,
        message=_message(),
        user_state=UserState(user_id="user_001"),
        intent=_intent(),
        sales_action=SimpleNamespace(reason="service_question_answer_only"),
    )

    assert reply is original
    assert reply.outbound_messages == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason", ["service_solution_first_offer", "service_question_answer_only"]
)
async def test_care_question_prepares_card_for_five_minutes_of_silence(
    monkeypatch, reason
):
    from app.services import chat_orchestrator

    async def build_card_context(*args, **kwargs):
        del args
        assert kwargs["business_action"] == "catalog_search"
        return BusinessFacts(
            tool_state={
                "commerce_type": "product",
                "status": "found",
                "products": [{"item_id": "membership-39"}],
            }
        )

    async def render_card(message, facts):
        del message, facts
        return FinalReply(
            answer="卡片文案",
            reply_type="template",
            route="template_reply",
            outbound_messages=[
                OutboundMessage(type="text", content="卡片文案"),
                OutboundMessage(type="link_card", content='{"item_id":"membership-39"}'),
            ],
        )

    monkeypatch.setattr(chat_orchestrator, "build_commerce_context", build_card_context)
    monkeypatch.setattr(chat_orchestrator, "render_business_reply", render_card)
    reply = await chat_orchestrator._prepare_membership_card_silence_follow_up(
        reply=_reply("浇水看植料干湿，施肥要薄肥勤施。"),
        message=_message(),
        user_state=UserState(user_id="user_001"),
        intent=_intent(),
        sales_action=SimpleNamespace(reason=reason),
    )

    assert reply.outbound_messages == []
    follow_up = reply.metadata["scheduled_follow_up"]
    assert follow_up["delay_seconds"] == 300
    assert follow_up["cancel_on_customer_message"] is True
    assert follow_up["product_id"] == "membership-39"
    assert [item["type"] for item in follow_up["messages"]] == ["text", "link_card"]


@pytest.mark.asyncio
async def test_first_service_brand_bridge_does_not_attach_card(monkeypatch):
    from app.services import chat_orchestrator

    async def unexpected(*args, **kwargs):
        del args, kwargs
        raise AssertionError("the first brand bridge must not query a product card")

    monkeypatch.setattr(chat_orchestrator, "build_commerce_context", unexpected)
    original = _reply("先回答痛点，再简单介绍我们萧岚苑")
    reply = await chat_orchestrator._attach_membership_card_for_service_offer(
        reply=original,
        message=_message(),
        user_state=UserState(user_id="user_001"),
        intent=_intent(),
        sales_action=SimpleNamespace(reason="service_solution_first_offer"),
    )

    assert reply is original
    assert reply.outbound_messages == []


@pytest.mark.asyncio
async def test_discovery_action_does_not_attach_membership_card(monkeypatch):
    from app.services import chat_orchestrator

    async def unexpected(*args, **kwargs):
        del args, kwargs
        raise AssertionError("discovery must not query the membership product")

    monkeypatch.setattr(chat_orchestrator, "build_commerce_context", unexpected)
    original = _reply("继续挖掘具体需求")
    reply = await chat_orchestrator._attach_membership_card_for_service_offer(
        reply=original,
        message=_message(),
        user_state=UserState(user_id="user_001"),
        intent=_intent(),
        sales_action=SimpleNamespace(reason="stage_default"),
    )

    assert reply is original


@pytest.mark.asyncio
async def test_service_offer_does_not_duplicate_an_existing_card(monkeypatch):
    from app.services import chat_orchestrator

    async def unexpected(*args, **kwargs):
        del args, kwargs
        raise AssertionError("an existing card must not be queried again")

    monkeypatch.setattr(chat_orchestrator, "build_commerce_context", unexpected)
    original = _reply("塑造服务价值")
    original.outbound_messages.append(
        OutboundMessage(type="link_card", content='{"item_id":"membership-39"}')
    )
    reply = await chat_orchestrator._attach_membership_card_for_service_offer(
        reply=original,
        message=_message(),
        user_state=UserState(user_id="user_001"),
        intent=_intent(),
        sales_action=SimpleNamespace(reason="service_offer_soft_decline_value_card"),
    )

    assert reply is original
    assert [item.type for item in reply.outbound_messages] == ["link_card"]


def test_explicit_service_interest_selects_membership_card_immediately():
    from app.services import chat_orchestrator

    intent = _intent()
    state = UserState(
        user_id="user_001",
        metadata={
            "active_opportunity": {
                "service_offer_phase": "introduced",
                "last_sales_action": "recommend_solution",
            }
        },
    )
    message = _message().model_copy(
        update={"message": "我感兴趣，把详细介绍发我看看"}
    )

    result = chat_orchestrator._service_offer_followup_business_intent(
        intent=intent,
        user_state=state,
        message=message,
    )

    assert result.slots["product_request_kind"] == "membership"
    assert result.slots["membership_question_kind"] == "purchase"
    assert result.slots["service_offer_followup"] == "value_card"
    assert result.slots["service_offer_trigger"] == "explicit_interest"


@pytest.mark.asyncio
async def test_orchestrator_executes_the_single_planned_reply(monkeypatch):
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    captured = {}

    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(intent_example_top_k=5),
    )

    async def execute_reply_plan(**kwargs):
        captured["plan"] = kwargs["plan"]
        assert kwargs["intent"].primary_intent == "care_question"
        assert kwargs["message"].trace_id == "trace_001"
        assert kwargs["user_state"].user_id == "user_001"
        return FinalReply(
            answer="planned answer",
            reply_type="template",
            route="template_reply",
        )

    def capture_shadow(**kwargs):
        captured["shadow"] = kwargs

    monkeypatch.setattr(chat_orchestrator, "execute_reply_plan", execute_reply_plan)
    monkeypatch.setattr(
        chat_orchestrator,
        "schedule_reply_shadow_evaluation",
        capture_shadow,
    )
    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="api",
            user_id="user_001",
            message="hello",
            kb_id="kb_default",
        )
    )

    assert result["answer"] == "planned answer"
    assert captured["plan"].decision_trace
    assert captured["shadow"]["message"].trace_id == "trace_001"
    assert captured["shadow"]["reply"].answer.startswith("planned answer")
    assert set(result) == {
        "answer",
        "answer_segments",
        "outbound_messages",
        "session_id",
        "sources",
        "usage",
        "reply_type",
        "route",
        "intent",
        "template",
        "need_human",
        "next_action",
        "trace_id",
        "metadata",
        "handoff",
    }


@pytest.mark.asyncio
async def test_orchestrator_does_not_append_question_after_persona_render(monkeypatch):
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(intent_example_top_k=5),
    )

    async def execute_reply_plan(**kwargs):
        del kwargs
        return FinalReply(
            answer="先看看您想解决养护问题，还是正在挑品种？",
            reply_type="template",
            route="template_reply",
            metadata={"persona": {"sales_action_rendered": True}},
        )

    monkeypatch.setattr(chat_orchestrator, "execute_reply_plan", execute_reply_plan)
    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="api",
            user_id="user_001",
            message="想了解一下",
            kb_id="kb_default",
        )
    )

    assert result["answer"] == "先看看您想解决养护问题，还是正在挑品种？"
    assert result["answer"].count("？") == 1


@pytest.mark.asyncio
async def test_orchestrator_uses_sales_stage_decision_for_state_updates(monkeypatch):
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    captured = {}

    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(intent_example_top_k=5),
    )

    async def classify_intent(message, user_state, candidates):
        del message, user_state, candidates
        return IntentResult(
            route="template_reply",
            primary_intent="ask_price",
            sales_stage="price_discussed",
            confidence=0.9,
            need_template=True,
        )

    async def decide_route(intent, user_state, message):
        del intent, user_state, message
        return PolicyDecision(route="template_reply", reason="test_policy")

    async def get_profile_bundle(user_id):
        del user_id
        return {"profile": {}, "recent_memories": []}

    async def execute_reply_plan(**kwargs):
        captured["reply_sales_action"] = kwargs["user_state"].metadata.get(
            "sales_action"
        )
        return FinalReply(
            answer="price reply",
            reply_type="template",
            route="template_reply",
            metadata={},
        )

    async def update_user_state(user_id, session_id, intent, reply):
        del reply
        captured["state_update"] = {
            "user_id": user_id,
            "session_id": session_id,
            "sales_stage": intent.sales_stage,
        }

    async def apply_deterministic_profile_update(message, intent, reply):
        del message
        captured["profile_stage"] = intent.sales_stage
        captured["profile_sales_action"] = reply.metadata.get("sales_action")

    monkeypatch.setattr(chat_orchestrator, "classify_intent", classify_intent)
    monkeypatch.setattr(chat_orchestrator, "decide_route", decide_route)
    monkeypatch.setattr(chat_orchestrator, "get_profile_bundle", get_profile_bundle)
    monkeypatch.setattr(chat_orchestrator, "execute_reply_plan", execute_reply_plan)
    monkeypatch.setattr(chat_orchestrator, "update_user_state", update_user_state)
    monkeypatch.setattr(
        chat_orchestrator,
        "apply_deterministic_profile_update",
        apply_deterministic_profile_update,
    )

    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="api",
            user_id="user_001",
            message="多少钱",
            kb_id="kb_default",
        )
    )
    assert result["intent"]["sales_stage"] == "need_discovery"
    assert captured["state_update"]["sales_stage"] == "need_discovery"
    assert captured["profile_stage"] == "need_discovery"
    assert captured["reply_sales_action"]["sales_action"] == "discover_pain"
    assert captured["reply_sales_action"]["question_slot"] == "pain_point"
    assert captured["profile_sales_action"] == captured["reply_sales_action"]


@pytest.mark.asyncio
async def test_hydrate_user_state_restores_persisted_sales_stage(monkeypatch):
    from app.services import chat_orchestrator

    state = UserState(user_id="user_001", sales_stage="unknown")

    async def get_profile_bundle(user_id):
        assert user_id == "user_001"
        return {
            "profile": {"current_stage": "price_discussed", "customer_tags": []},
            "recent_memories": [],
        }

    monkeypatch.setattr(chat_orchestrator, "get_profile_bundle", get_profile_bundle)

    await chat_orchestrator._hydrate_user_state_from_profile("user_001", state)

    assert state.sales_stage == "trial_close"


@pytest.mark.asyncio
async def test_orchestrator_uses_trusted_payment_facts_before_stage_decision(monkeypatch):
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    captured = {}
    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(intent_example_top_k=5),
    )

    async def classify_intent(message, user_state, candidates):
        del message, user_state, candidates
        return IntentResult(
            route="template_reply",
            primary_intent="payment_success",
            confidence=0.9,
        )

    async def build_business_context(message, **kwargs):
        del message, kwargs
        return BusinessFacts(tool_state={"payment_status": "paid"})

    async def update_user_state(user_id, session_id, intent, reply):
        del user_id, session_id
        captured["signals"] = intent.sales_signals
        captured["stage_decision"] = reply.metadata["sales_stage_decision"]

    monkeypatch.setattr(chat_orchestrator, "classify_intent", classify_intent)
    monkeypatch.setattr(
        chat_orchestrator,
        "build_business_context",
        build_business_context,
    )
    monkeypatch.setattr(chat_orchestrator, "update_user_state", update_user_state)

    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="api",
            user_id="user_001",
            message="我已经付了",
            kb_id="kb_default",
        )
    )

    assert "purchased" in captured["signals"]
    assert captured["stage_decision"]["stage"] == "closing"
    assert captured["stage_decision"]["opportunity_status"] == "won"
    assert "sales_stage_decision" not in result["metadata"]
