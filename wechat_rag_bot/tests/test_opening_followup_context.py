from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.db.models import EyunInboundBatchModel, EyunInboundMessageModel
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState


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

    async def capture_memory(**kwargs):
        memories.append(kwargs)

    async def empty_contact(**kwargs):
        return {}

    async def capture_outbound(**kwargs):
        return kwargs

    monkeypatch.setattr(service, "utcnow", lambda: now)
    monkeypatch.setattr(
        service, "append_conversation_memory", capture_memory, raising=False
    )
    monkeypatch.setattr(service, "get_eyun_contact_snapshot", empty_contact)
    monkeypatch.setattr(service, "enqueue_wechat_outbound", capture_outbound)

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
    ]


@pytest.mark.asyncio
async def test_opening_followup_uses_recent_assistant_question():
    from app.services.intent_service import classify_intent

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


def test_profile_answer_chitchat_acknowledges_collected_information():
    from app.services.reply_builder import build_chitchat_reply

    reply = build_chitchat_reply(
        IntentResult(
            route="chitchat",
            primary_intent="profile_answer",
            sales_stage="need_discovery",
            confidence=0.98,
        )
    )

    assert "养兰规模和主要品种" in reply.answer
    assert "产品、价格、养护、发货或售后" not in reply.answer


def test_intent_prompt_includes_recent_conversation():
    from app.services.intent_service import _build_prompt

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
async def test_fixed_opening_then_orchid_profile_answer_uses_persisted_context(
    monkeypatch,
):
    from app.services import message_risk_control_service as risk_control
    from app.services.intent_service import classify_intent
    from app.services.user_profile_service import get_profile_bundle

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
    assert intent.route == "chitchat"
    assert intent.primary_intent == "profile_answer"
