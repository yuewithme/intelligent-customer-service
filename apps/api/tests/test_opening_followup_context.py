from datetime import datetime, timezone

import pytest

from app.core.config import get_settings
from app.infrastructure.database.models import EyunInboundBatchModel, EyunInboundMessageModel
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.customers.schemas.state import UserState


@pytest.fixture(autouse=True)
def isolated_databases(monkeypatch, tmp_path):
    from app.services import message_risk_control_service, user_profile_service

    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}"
    )
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}"
    )
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()
    user_profile_service._sessionmakers.clear()
    yield
    get_settings.cache_clear()
    message_risk_control_service._sessionmakers.clear()
    user_profile_service._sessionmakers.clear()


@pytest.mark.asyncio
async def test_first_inbound_persists_user_and_opening_memories(monkeypatch):
    from app.services import message_risk_control_service as service

    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    memories = []
    chat_requests = []
    queued = []
    monkeypatch.setenv("EYUN_OPENING_TEXT", "opening-message")
    monkeypatch.setenv("EYUN_OPENING_IMAGE_URL", "")
    monkeypatch.setenv("EYUN_OPENING_MATERIAL_ID", "0")
    get_settings.cache_clear()

    async def capture_memory(**kwargs):
        memories.append(kwargs)

    async def empty_contact(**kwargs):
        return {}

    async def capture_outbound(**kwargs):
        queued.append(kwargs)
        return kwargs

    async def answer_first_message(request):
        chat_requests.append(request)
        return {
            "answer": "first-message-answer",
            "route": "rag_answer",
            "trace_id": "req_first_message",
            "intent": {"primary_intent": "care_question"},
        }

    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr(
        service, "append_conversation_memory", capture_memory, raising=False
    )
    monkeypatch.setattr(service, "get_eyun_contact_snapshot", empty_contact)
    monkeypatch.setattr(service, "enqueue_wechat_outbound", capture_outbound)
    monkeypatch.setattr(service, "handle_chat", answer_first_message)

    with service._get_session() as session:
        batch = EyunInboundBatchModel(
            batch_key="wid:customer",
            w_id="wid",
            wc_id="owner",
            target_wc_id="customer",
            from_user="customer",
            from_group=None,
            account="sales",
            message_type="60001",
            content="我是兰亭",
            message_count=1,
            status="processing",
            due_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        session.add(
            EyunInboundMessageModel(
                provider_message_id="first",
                batch_key="wid:customer",
                content="我是兰亭",
                payload_json="{}",
                created_at=now,
            )
        )
        session.commit()
        batch_id = batch.id

    await service._process_inbound_batch(batch_id)

    assert [(item["role"], item["content"]) for item in memories] == [
        ("user", "我是兰亭"),
        ("assistant", get_settings().eyun_opening_text),
        ("assistant", "first-message-answer"),
    ]
    assert len(chat_requests) == 1
    assert chat_requests[0].message == "我是兰亭"
    assert chat_requests[0].metadata["skip_conversation_memory"] is True
    assert [item["content"] for item in queued] == [
        "opening-message",
        "first-message-answer",
    ]


@pytest.mark.asyncio
async def test_opening_followup_uses_recent_assistant_question():
    from app.domains.decisioning.services.intent_service import classify_intent

    message = NormalizedMessage(
        trace_id="req_followup",
        channel="wechat",
        user_id="customer",
        session_id="default",
        message="我们家养了100盆，建兰为主吧",
        kb_id="kb_default",
    )
    state = UserState(
        user_id="customer",
        session_id="default",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "家里目前养了多少盆兰花？具体养了哪些品种？",
                }
            ]
        },
    )

    intent = await classify_intent(message, state)

    assert intent.route == "chitchat"
    assert intent.primary_intent == "profile_answer"
    assert intent.reason == "opening_profile_answer"
    assert intent.slots["plant_count"] == 100
    assert intent.slots["owned_varieties"] == ["建兰"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    ["1", "[强]", "怎么领？", "没领到", "好的，好的", "养护"],
)
async def test_opening_material_followup_preempts_ambiguous_short_intents(
    monkeypatch, content
):
    from app.domains.conversations.schemas.chat import ChatRequest
    from app.domains.conversations.services import chat_orchestrator, state_service

    user_id = f"material-followup-{content}"
    state_service._state_store.pop(user_id, None)

    async def hydrate_opening_context(_user_id, user_state):
        user_state.metadata["recent_turns"] = [
            {
                "role": "assistant",
                "route": "opening",
                "content": "我们会给兰友提供养兰资料、视频课程和一对一养护指导。",
            }
        ]

    monkeypatch.setattr(
        chat_orchestrator,
        "_hydrate_user_state_from_profile",
        hydrate_opening_context,
    )

    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="wechat",
            user_id=user_id,
            session_id="default",
            message=content,
            kb_id="kb_default",
            metadata={"provider": "eyun"},
        )
    )

    assert result["route"] == "orchid_material_delivery"
    assert result["intent"]["primary_goal"] == "request_material"
    assert [item["type"] for item in result["outbound_messages"]] == [
        "link_card",
        "text",
        "image",
    ]


@pytest.mark.asyncio
async def test_opening_followup_extracts_region_and_variety_from_classic_case():
    from app.domains.decisioning.services.intent_service import classify_intent

    message = NormalizedMessage(
        trace_id="req_followup_region",
        channel="wechat",
        user_id="customer",
        session_id="default",
        message="我是甘肃天水的，我养的全是建兰。",
        kb_id="kb_default",
    )
    state = UserState(
        user_id="customer",
        session_id="default",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "家里目前养了多少盆兰花？具体养了哪些品种？",
                }
            ]
        },
    )

    intent = await classify_intent(message, state)

    assert intent.slots["region"] == "甘肃天水"
    assert intent.slots["owned_varieties"] == ["建兰"]


def test_profile_answer_chitchat_acknowledges_collected_information():
    from app.domains.decisioning.services.reply_builder import build_chitchat_reply

    reply = build_chitchat_reply(
        IntentResult(
            route="chitchat",
            primary_intent="profile_answer",
            sales_stage="need_discovery",
            confidence=0.98,
        )
    )

    assert reply.answer == "好的，已经记下了。"


def test_intent_prompt_includes_recent_conversation():
    from app.domains.decisioning.services.intent_service import _build_prompt

    prompt = _build_prompt(
        "接着说吧",
        recent_turns=[
            {"role": "assistant", "content": "你家主要养什么品种？"},
            {"role": "user", "content": "主要是建兰"},
        ],
    )

    assert "最近对话" in prompt
    assert "assistant: 你家主要养什么品种？" in prompt
    assert "user: 主要是建兰" in prompt


@pytest.mark.asyncio
async def test_non_question_opening_does_not_force_profile_followup(
    monkeypatch,
):
    from app.services import message_risk_control_service as risk_control
    from app.domains.decisioning.services.intent_service import classify_intent
    from app.domains.customers.services.user_profile_service import get_profile_bundle

    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    async def empty_contact(**kwargs):
        return {}

    async def ignore_outbound(**kwargs):
        return kwargs

    monkeypatch.setattr(risk_control, "utcnow", lambda: now)
    monkeypatch.setattr(risk_control, "get_eyun_contact_snapshot", empty_contact)
    monkeypatch.setattr(risk_control, "enqueue_wechat_outbound", ignore_outbound)

    with risk_control._get_session() as session:
        batch = EyunInboundBatchModel(
            batch_key="wid:customer",
            w_id="wid",
            wc_id="owner",
            target_wc_id="customer",
            from_user="customer",
            from_group=None,
            account="sales",
            message_type="60001",
            content="我是兰亭",
            message_count=1,
            status="processing",
            due_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        session.add(
            EyunInboundMessageModel(
                provider_message_id="first-integrated",
                batch_key="wid:customer",
                content="我是兰亭",
                payload_json="{}",
                created_at=now,
            )
        )
        session.commit()
        batch_id = batch.id

    await risk_control._process_inbound_batch(batch_id)
    bundle = await get_profile_bundle("customer")
    state = UserState(
        user_id="customer",
        session_id="default",
        metadata={"recent_turns": bundle["recent_memories"]},
    )
    followup = NormalizedMessage(
        trace_id="req_integrated_followup",
        channel="wechat",
        user_id="customer",
        session_id="default",
        message="我们家养了100盆，建兰为主吧",
        kb_id="kb_default",
    )

    intent = await classify_intent(followup, state)

    assert [item["role"] for item in bundle["recent_memories"]] == [
        "user",
        "assistant",
    ]
    assert intent.route == "clarify"
    assert intent.primary_intent != "profile_answer"
