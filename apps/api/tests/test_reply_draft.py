from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import get_settings
from app.domains.conversations.services import conversation_service as conversations
from app.domains.conversations.services import reply_draft_service as drafts
from app.infrastructure.database.models import (
    ConversationModel, ConversationMessageModel, ConversationReplyDraftModel,
    EyunInboundBatchModel, EyunOutboundMessageModel,
)
from app.integrations.eyun.services import message_risk_control_service as queue
from app.main import app
from app.shared.schemas.common import AppError
from test_admin_gate import context as access_context


CID = "wechat:customer:bot"


@pytest.fixture
def draft_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profiles.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("EYUN_WC_ID", "")
    monkeypatch.setenv("EYUN_WID", "")
    get_settings.cache_clear()
    clock = [datetime.now(timezone.utc)]
    monkeypatch.setattr(drafts, "_now", lambda: clock[0])
    monkeypatch.setattr(queue, "utcnow", lambda: clock[0])
    with conversations._get_session() as db:
        db.add(ConversationModel(conversation_id=CID, channel="wechat", user_id="customer", session_id="bot",
                                 status="ai_active", created_at=clock[0], updated_at=clock[0]))
        db.commit()
    yield clock
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_typing_blocks_ai_and_clear_restores_it_without_handoff_ticket(draft_context):
    result = await drafts.update_reply_draft(CID, "operator", "editor", active=True)
    assert result["status"] == "human_active" and result["handoff_ticket_id"] is None
    assert conversations.conversation_blocks_ai(channel="wechat", user_id="customer", session_id="bot")
    result = await drafts.update_reply_draft(CID, "operator", "editor", active=False)
    assert result["status"] == "ai_active" and result["owner_id"] is None
    assert not conversations.conversation_blocks_ai(channel="wechat", user_id="customer", session_id="bot")


@pytest.mark.asyncio
async def test_server_expires_at_last_edit_deadline_without_browser(draft_context):
    clock = draft_context
    await drafts.update_reply_draft(CID, "operator", "editor", active=True)
    clock[0] += timedelta(seconds=50)
    assert await drafts.expire_reply_drafts() == 0
    await drafts.update_reply_draft(CID, "operator", "editor", active=True, remaining_seconds=59.5)
    clock[0] += timedelta(seconds=59)
    assert await drafts.expire_reply_drafts() == 0
    clock[0] += timedelta(seconds=0.5)
    assert await drafts.expire_reply_drafts() == 1
    assert (await conversations.get_conversation_detail(CID))["conversation"]["status"] == "ai_active"
    assert drafts.reply_draft_deadline(CID) is None


@pytest.mark.asyncio
async def test_other_tab_cannot_clear_or_take_over_live_draft(draft_context):
    await drafts.update_reply_draft(CID, "operator", "editor", active=True)
    result = await drafts.update_reply_draft(CID, "operator", "other-tab", active=False)
    assert result["status"] == "human_active"
    with pytest.raises(AppError, match="其他客服"):
        await drafts.update_reply_draft(CID, "other", "other-tab", active=True)
    with pytest.raises(AppError, match="其他客服"):
        await drafts.update_reply_draft(CID, "other", "editor", active=True)


@pytest.mark.asyncio
async def test_expiry_does_not_reopen_resolved_conversation(draft_context):
    await drafts.update_reply_draft(CID, "operator", "editor", active=True)
    await conversations.resolve_conversation(CID, "operator", "resolved")
    draft_context[0] += timedelta(seconds=61)
    assert await drafts.expire_reply_drafts() == 0
    assert (await conversations.get_conversation_detail(CID))["conversation"]["status"] == "resolved"
    with pytest.raises(AppError):
        await drafts.update_reply_draft(CID, "operator", "editor", active=True)


@pytest.mark.asyncio
async def test_human_can_send_without_force_handoff_and_empty_returns_ai(draft_context):
    await drafts.update_reply_draft(CID, "operator", "editor", active=True)
    await conversations.reply_conversation(CID, "operator", "我帮您看看")
    result = await drafts.update_reply_draft(CID, "operator", "editor", active=False)
    assert result["status"] == "ai_active"
    detail = await conversations.get_conversation_detail(CID)
    assert [(message["sender_type"], message["content"]) for message in detail["messages"]] == [("human", "我帮您看看")]


@pytest.mark.asyncio
async def test_inbound_waits_for_typing_and_skips_if_human_already_replied(draft_context, monkeypatch):
    clock = draft_context
    await queue.enqueue_eyun_inbound({"messageType": "60001", "wcId": "bot", "data": {
        "wId": "wid", "fromUser": "customer", "content": "这是什么品种", "newMsgId": "draft-inbound",
    }})
    await drafts.update_reply_draft(CID, "operator", "editor", active=True)

    async def unexpected(*args, **kwargs):
        raise AssertionError("AI must not generate while typing or after a human reply")
    monkeypatch.setattr(queue, "handle_chat", unexpected)
    await queue.process_due_eyun_inbound_batches()
    with queue._get_session() as db:
        assert db.scalar(select(EyunInboundBatchModel.status)) == "pending"
    await conversations.reply_conversation(CID, "operator", "这是建兰")
    await drafts.update_reply_draft(CID, "operator", "editor", active=False)
    clock[0] += timedelta(seconds=2)
    await queue.process_due_eyun_inbound_batches()
    with queue._get_session() as db:
        assert db.scalar(select(EyunInboundBatchModel.status)) == "skipped"


@pytest.mark.asyncio
async def test_queued_ai_pauses_then_is_cancelled_after_human_reply(draft_context, monkeypatch):
    clock = draft_context
    with queue._get_session() as db:
        message = ConversationMessageModel(conversation_id=CID, sender_type="ai", content="旧AI回复", created_at=clock[0], delivery_status="queued")
        db.add(message)
        db.flush()
        outbound = EyunOutboundMessageModel(w_id="wid", wc_id="customer", content="旧AI回复",
                                           conversation_message_id=message.id, status="queued", due_at=clock[0], created_at=clock[0], updated_at=clock[0])
        db.add(outbound)
        db.commit()
        outbound_id = outbound.id
    await drafts.update_reply_draft(CID, "operator", "editor", active=True)
    assert await queue.process_due_eyun_outbound_messages() == 0
    with queue._get_session() as db:
        assert db.get(EyunOutboundMessageModel, outbound_id).status == "queued"
    await conversations.reply_conversation(CID, "operator", "人工新回复")
    await drafts.update_reply_draft(CID, "operator", "editor", active=False)
    clock[0] += timedelta(seconds=2)
    assert await queue.process_due_eyun_outbound_messages() == 0
    with queue._get_session() as db:
        assert db.get(EyunOutboundMessageModel, outbound_id).status == "cancelled"


def test_draft_endpoint_validates_lifetime_and_conversation_scope(draft_context):
    client = TestClient(app)
    url = f"/api/v1/admin/conversations/{CID}/reply-draft"
    data = {"operator_id": "operator", "editor_id": "editor", "active": True}
    assert client.post(url, json=data).json()["data"]["status"] == "human_active"
    assert client.post(url, json={**data, "remaining_seconds": 61}).status_code == 422
    assert client.post(f"/api/v1/demo-admin/conversations/{CID}/reply-draft", json=data).status_code == 403


def test_draft_endpoint_uses_authorized_identity(access_context):
    client, state = access_context
    data = {"operator_id": "spoofed", "editor_id": "editor", "active": True}
    response = client.post("/api/v1/admin/conversations/wechat:customer_wx_allowed:wx_allowed/reply-draft", json=data)
    assert response.status_code == 200
    assert response.json()["data"]["owner_id"] == state["account"]["username"]
    assert client.post("/api/v1/admin/conversations/wechat:customer_wx_denied:wx_denied/reply-draft", json=data).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("human_replies_during_generation", [False, True])
async def test_waiting_customer_resumes_after_expiry_without_duplicate_ai(draft_context, monkeypatch, human_replies_during_generation):
    calls = []
    await queue.enqueue_eyun_inbound({"messageType": "60001", "wcId": "bot", "data": {
        "wId": "wid", "fromUser": "customer", "content": "这是什么品种", "newMsgId": "resume-inbound",
    }})

    async def contact(**kwargs):
        return {}

    async def prepare(content, payloads):
        return content, 0, 0, 0

    async def generate(message):
        calls.append(message.message)
        if human_replies_during_generation:
            await drafts.update_reply_draft(CID, "operator", "editor", active=True)
            await conversations.reply_conversation(CID, "operator", "人工已经回答")
            await drafts.update_reply_draft(CID, "operator", "editor", active=False)
        return {"answer": "AI恢复回复", "trace_id": "resume-trace"}

    async def finalize(**kwargs):
        return kwargs["chat_result"]

    monkeypatch.setattr(queue, "get_eyun_contact_snapshot", contact)
    monkeypatch.setattr(queue, "_prepare_inbound_content", prepare)
    monkeypatch.setattr(queue, "handle_chat", generate)
    monkeypatch.setattr(queue, "_finalize_eyun_handoff", finalize)
    if not human_replies_during_generation:
        await drafts.update_reply_draft(CID, "operator", "editor", active=True)
        await queue.process_due_eyun_inbound_batches()
        assert calls == []
        draft_context[0] += timedelta(seconds=60)
        await drafts.expire_reply_drafts()
    await queue.process_due_eyun_inbound_batches()
    assert calls == ["这是什么品种"]
    with queue._get_session() as db:
        assert db.scalar(select(EyunInboundBatchModel.status)) == ("skipped" if human_replies_during_generation else "processed")
        assert len(db.scalars(select(EyunOutboundMessageModel)).all()) == (0 if human_replies_during_generation else 1)
