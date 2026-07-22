from types import SimpleNamespace

import pytest

from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.decisioning.schemas.reply import FinalReply
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

    monkeypatch.setattr(chat_orchestrator, "execute_reply_plan", execute_reply_plan)
    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="api",
            user_id="user_001",
            message="hello",
            kb_id="kb_default",
        )
    )

    assert result["answer"].startswith("planned answer")
    assert result["answer"].count("？") == 1
    assert captured["plan"].decision_trace
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
    assert captured["reply_sales_action"]["sales_action"] == "discover_need_track"
    assert captured["reply_sales_action"]["question_slot"] == "need_track"
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
