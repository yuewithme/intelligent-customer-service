import asyncio
import json
from datetime import datetime, timezone

import pytest
import httpx
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domains.conversations.services import chat_review_service as service
from app.domains.conversations.services.conversation_service import _get_session
from app.infrastructure.database.models import ConversationMessageModel, ConversationModel
from app.main import app
from app.shared.schemas.common import AppError, ErrorCode
from test_admin_gate import context as access_context


def test_five_round_window_targets_latest_sent_reply_and_keeps_customer_followup():
    messages = []
    for turn in range(8):
        messages.extend([
            {"id": turn * 3, "sender_type": "customer", "content": f"问题{turn}"},
            {"id": turn * 3 + 1, "sender_type": "customer", "content": "同轮补充"},
            {"id": turn * 3 + 2, "sender_type": "ai", "content": f"回复{turn}"},
        ])
    messages.extend([
        {"id": 24, "sender_type": "human", "content": "人工补充"},
        {"id": 25, "sender_type": "system", "content": "切换接待"},
        {"id": 26, "sender_type": "customer", "content": "客户最新追问"},
        {"id": 27, "sender_type": "ai", "content": "失败消息", "delivery_status": "failed"},
        {"id": 28, "sender_type": "ai", "content": "待发送", "delivery_status": "queued"},
    ])
    rounds = service.recent_rounds(messages)
    assert len(rounds) == 5
    assert rounds[0][0]["content"] == "问题4"
    assert rounds[-1][-1]["content"] == "客户最新追问"
    assert [message["id"] for message in service.current_reply(rounds)] == [23, 24]
    assert "问题3" not in json.dumps(rounds, ensure_ascii=False)


@pytest.fixture
def review_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profiles.db'}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'messages.db'}")
    get_settings.cache_clear()
    service._cache.clear()
    now = datetime.now(timezone.utc)
    with _get_session() as db:
        db.add(ConversationModel(conversation_id="review:customer", channel="wechat", user_id="review-customer",
                                 status="human_active", created_at=now, updated_at=now))
        db.add_all([
            ConversationMessageModel(conversation_id="review:customer", sender_type="customer", content="兰花叶子发黄", created_at=now),
            ConversationMessageModel(conversation_id="review:customer", sender_type="human", content="先别急着施肥", created_at=now),
        ])
        db.commit()
    profile = {"customer_tags": ["新手"], "pain_points": ["叶片发黄"]}
    calls = []

    async def bundle(user_id):
        return {"profile": profile, "recent_memories": [{"content": "不应提供的旧聊天"}]}

    async def generate(messages, **kwargs):
        calls.append(json.loads(messages[1]["content"]))
        await asyncio.sleep(0)
        return {"data": {"analysis": "已提醒暂缓施肥，可以进一步结合叶片情况沟通。",
                         "suggestions": ["发黄主要在老叶还是新叶上？", "您先观察一下叶片有没有发软。", "最近浇水频率怎么样？"]}}

    monkeypatch.setattr(service, "get_profile_bundle", bundle)
    monkeypatch.setattr(service, "generate_messages_json", generate)
    yield profile, calls
    service._cache.clear()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_shared_generation_cache_and_message_profile_invalidation(review_context):
    profile, calls = review_context
    first, second = await asyncio.gather(service.get_chat_review("review:customer"), service.get_chat_review("review:customer"))
    assert first == second
    assert first["status"] == "ready" and len(first["suggestions"]) == 3
    assert first["reviewed_reply"] == "先别急着施肥"
    assert len(calls) == 1
    assert "不应提供的旧聊天" not in json.dumps(calls, ensure_ascii=False)
    await service.get_chat_review("review:customer")
    assert len(calls) == 1
    profile["customer_tags"] = ["经验丰富"]
    await service.get_chat_review("review:customer")
    assert len(calls) == 2 and calls[-1]["customer_profile"]["customer_tags"] == ["经验丰富"]
    with _get_session() as db:
        db.add(ConversationMessageModel(conversation_id="review:customer", sender_type="human", content="看看叶片有没有发软", created_at=datetime.now(timezone.utc)))
        db.commit()
    third = await service.get_chat_review("review:customer")
    assert len(calls) == 3 and third["reviewed_reply"].endswith("看看叶片有没有发软")


@pytest.mark.asyncio
@pytest.mark.parametrize("suggestions", [["一", "二"], ["一", "一", "三"], ["一", "二", " "], ["一", "二", "三", "四"]])
async def test_invalid_suggestions_are_not_shown_or_cached(review_context, monkeypatch, suggestions):
    async def invalid(*args, **kwargs):
        return {"data": {"analysis": "分析", "suggestions": suggestions}}
    monkeypatch.setattr(service, "generate_messages_json", invalid)
    result = await service.get_chat_review("review:customer")
    assert result["status"] == "unavailable" and result["suggestions"] == []
    assert not service._cache


@pytest.mark.asyncio
async def test_provider_failure_is_diagnosable_without_leaking_and_can_retry(review_context, monkeypatch):
    generate = service.generate_messages_json

    async def fail(*args, **kwargs):
        response = httpx.Response(403, json={"error": {"code": "AccessDenied", "message": "private-provider-detail"}},
                                  request=httpx.Request("POST", "https://model.example/chat", headers={"Authorization": "private-key"}))
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AppError(ErrorCode.LLM_FAILED) from exc

    monkeypatch.setattr(service, "generate_messages_json", fail)
    result = await service.get_chat_review("review:customer")
    assert result["status"] == "unavailable"
    assert result["error"] == {"kind": "AppError", "upstream_status": 403, "upstream_code": "AccessDenied"}
    assert "private" not in json.dumps(result)
    assert not service._cache and not service._inflight
    monkeypatch.setattr(service, "generate_messages_json", generate)
    assert (await service.get_chat_review("review:customer"))["status"] == "ready"


def test_review_endpoint_empty_missing_and_demo_scope(review_context):
    client = TestClient(app)
    assert client.post("/api/v1/admin/conversations/review:customer/chat-review").json()["data"]["status"] == "ready"
    assert client.post("/api/v1/demo-admin/conversations/review:customer/chat-review").status_code == 403
    assert client.post("/api/v1/admin/conversations/missing/chat-review").status_code == 404
    with _get_session() as db:
        db.query(ConversationMessageModel).delete()
        db.commit()
    assert client.post("/api/v1/admin/conversations/review:customer/chat-review").json()["data"]["status"] == "empty"


def test_media_review_uses_only_recognized_content():
    message = {"content": "下载错误及敏感地址", "metadata": {"media": {"type": "audio", "recognition": {"status": "failed", "error": "内部错误"}}}}
    assert service.message_text(message) == "[语音内容尚未可靠识别]"
    message["metadata"]["media"]["recognition"] = {"status": "succeeded", "text": "老叶有点发黄"}
    assert service.message_text(message) == "[语音识别内容] 老叶有点发黄"


def test_chat_review_enforces_shared_customer_profile_scope(access_context):
    client, _ = access_context
    allowed = "wechat:customer_wx_allowed:wx_allowed"
    denied = "wechat:customer_wx_denied:wx_denied"
    assert client.post(f"/api/v1/admin/conversations/{allowed}/chat-review").status_code == 200
    assert client.post(f"/api/v1/admin/conversations/{denied}/chat-review").status_code == 403
    now = datetime.now(timezone.utc)
    with _get_session() as db:
        db.add(ConversationModel(conversation_id="wechat:shared:wx_denied", channel="wechat", user_id="customer_wx_allowed",
                                 tenant_id="wx_denied", owner_wc_id="wx_denied", status="human_active", created_at=now, updated_at=now))
        db.commit()
    assert client.post(f"/api/v1/admin/conversations/{allowed}/chat-review").status_code == 403
