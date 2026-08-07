import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from app.domains.conversations.services.conversation_service import record_customer_message
from app.domains.conversations.services.conversation_event_service import conversation_event_broker


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "chat_logs.db"
    profile_db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{profile_db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("STATE_PROVIDER", "memory")
    get_settings.cache_clear()


def test_conversation_list_starts_empty(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/admin/conversations")

    assert response.status_code == 200
    assert response.json()["data"] == {"items": [], "total": 0, "page": 1, "page_size": 50}


def test_admin_conversation_list_filters_by_channel(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    for channel, user_id in (("api", "test_user"), ("wechat", "real_customer")):
        asyncio.run(
            record_customer_message(
                channel=channel,
                user_id=user_id,
                session_id="default",
                content="hello",
            )
        )

    data = TestClient(app).get(
        "/api/v1/admin/conversations", params={"channel": "wechat"}
    ).json()["data"]

    assert data["total"] == 1
    assert data["items"][0]["channel"] == "wechat"
    assert data["items"][0]["user_id"] == "real_customer"


def test_evaluation_turn_is_recorded_as_a_labeled_workbench_conversation(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.schemas.event import NormalizedMessage
    from app.domains.conversations.services.chat_orchestrator import (
        _record_workbench_turn,
    )

    _reset_settings(monkeypatch, tmp_path)
    async def record_turn(message_text: str, answer_text: str, trace_id: str) -> None:
        await _record_workbench_turn(
            message=NormalizedMessage(
                trace_id=trace_id,
                channel="api",
                user_id="eval_classic_case_12",
                session_id="session_eval_12",
                message=message_text,
                kb_id="kb_default",
                metadata={
                    "evaluation_id": "classic-case-12",
                    "skip_customer_record": True,
                },
            ),
            result={
                "answer": answer_text,
                "answer_segments": [answer_text],
                "route": "rag_answer",
                "intent": {
                    "primary_intent": "care_question",
                    "sales_stage": (
                        "need_discovery"
                        if trace_id == "trace_eval_001"
                        else "pain_discovery"
                    ),
                },
                "sources": [],
                "template": {},
                "need_human": trace_id == "trace_eval_002",
                "handoff": (
                    {"reason": "should_not_notify"}
                    if trace_id == "trace_eval_002"
                    else None
                ),
            },
            is_evaluation=True,
        )

    asyncio.run(
        record_turn(
            "我的兰花烂根了，应该怎么办？",
            "先把烂根清理掉，再换成透气、排水好的植料。",
            "trace_eval_001",
        )
    )
    asyncio.run(
        record_turn(
            "我现在用的是水苔。",
            "那先把旧水苔清理干净，修掉发黑发软的根。",
            "trace_eval_002",
        )
    )

    client = TestClient(app)
    regular_listing = client.get(
        "/api/v1/admin/conversations", params={"channel": "wechat"}
    ).json()["data"]
    test_listing = client.get(
        "/api/v1/admin/conversations",
        params={"test_only": True},
    ).json()["data"]
    assert regular_listing["items"] == []
    assert regular_listing["total"] == 0
    assert test_listing["total"] == 1
    assert test_listing["items"][0]["user_display_name"] == "测试案例｜classic-case-12"
    assert test_listing["items"][0]["status"] == "handoff_pending"

    detail = client.get(
        "/api/v1/admin/conversations/"
        "wechat:eval_classic_case_12:session_eval_12"
    ).json()["data"]
    assert [item["sender_type"] for item in detail["messages"]] == [
        "customer",
        "ai",
        "customer",
        "ai",
    ]
    assert [item["content"] for item in detail["messages"]] == [
        "我的兰花烂根了，应该怎么办？",
        "先把烂根清理掉，再换成透气、排水好的植料。",
        "我现在用的是水苔。",
        "那先把旧水苔清理干净，修掉发黑发软的根。",
    ]
    assert all(
        item["metadata"]["evaluation_id"] == "classic-case-12"
        and item["metadata"]["is_evaluation"] is True
        for item in detail["messages"]
    )
    assert all("sales_stage" not in item["metadata"] for item in detail["messages"])


def test_evaluation_card_is_recorded_as_real_workbench_message(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.schemas.event import NormalizedMessage
    from app.domains.conversations.services.chat_orchestrator import (
        _record_workbench_turn,
    )

    _reset_settings(monkeypatch, tmp_path)
    card = {
        "title": "陪伴养兰会员",
        "url": "https://h5.youzan.com/goods/member-39",
        "description": "点击查看详情和下单",
        "thumb_url": "https://cdn.example.com/member.jpg",
    }
    asyncio.run(
        _record_workbench_turn(
            message=NormalizedMessage(
                trace_id="trace_eval_card",
                channel="api",
                user_id="eval_card_case",
                session_id="session_eval_card",
                message="把会员购买链接发我",
                kb_id="kb_default",
                metadata={"evaluation_id": "card-case"},
            ),
            result={
                "answer": "可以的，我把购买链接发您。",
                "answer_segments": ["可以的，我把购买链接发您。"],
                "outbound_messages": [
                    {"type": "text", "content": "可以的，我把购买链接发您。"},
                    {"type": "link_card", "content": json.dumps(card, ensure_ascii=False)},
                ],
                "route": "template_reply",
                "intent": {"primary_intent": "product_query"},
                "sources": [],
                "template": {},
                "need_human": False,
            },
            is_evaluation=True,
        )
    )

    detail = TestClient(app).get(
        "/api/v1/admin/conversations/"
        "wechat:eval_card_case:session_eval_card"
    ).json()["data"]
    ai_messages = [
        item for item in detail["messages"] if item["sender_type"] == "ai"
    ]
    assert [item["metadata"]["message_type"] for item in ai_messages] == [
        "text",
        "link_card",
    ]
    assert ai_messages[1]["metadata"]["link_card"] == card
    assert ai_messages[1]["metadata"]["simulated_delivery"] is True


def test_demo_card_counts_as_sent_for_material_deduplication(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.schemas.event import NormalizedMessage
    from app.domains.conversations.services.conversation_service import (
        record_ai_turn,
        was_outbound_content_sent,
    )

    _reset_settings(monkeypatch, tmp_path)
    title = "萧岚苑陪伴养兰资料"
    card = {
        "title": title,
        "url": "https://example.com/orchid-guide",
        "description": "养兰基础资料",
        "thumb_url": "",
    }
    asyncio.run(
        record_ai_turn(
            message=NormalizedMessage(
                trace_id="trace_demo_material",
                channel="wechat",
                user_id="demo_material_customer",
                session_id="default",
                message="把资料发我",
                kb_id="kb_default",
                metadata={
                    "provider": "eyun",
                    "provider_delivery_mode": "simulated",
                },
            ),
            result={
                "answer": "资料发您了。",
                "outbound_messages": [
                    {"type": "text", "content": "资料发您了。"},
                    {
                        "type": "link_card",
                        "content": json.dumps(card, ensure_ascii=False),
                    },
                ],
                "route": "agent",
                "intent": {"primary_intent": "autonomous_sales_turn"},
                "need_human": False,
            },
        )
    )

    detail = TestClient(app).get(
        "/api/v1/admin/conversations/"
        "wechat:demo_material_customer:default"
    ).json()["data"]
    ai_messages = [
        item for item in detail["messages"] if item["sender_type"] == "ai"
    ]
    assert all(item["delivery_status"] == "sent" for item in ai_messages)
    assert was_outbound_content_sent(
        channel="wechat",
        user_id="demo_material_customer",
        content_marker=title,
        within_days=30,
    )


def test_hidden_conversation_reappears_after_new_customer_message(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="real_customer",
            session_id="default",
            content="first message",
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:real_customer:default"

    hidden = client.post(f"/api/v1/admin/conversations/{conversation_id}/hide")

    assert hidden.status_code == 200
    assert client.get("/api/v1/admin/conversations").json()["data"]["total"] == 0
    assert client.get(
        f"/api/v1/admin/conversations/{conversation_id}"
    ).status_code == 200

    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="real_customer",
            session_id="default",
            content="new message",
        )
    )

    data = client.get("/api/v1/admin/conversations").json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["last_message"] == "new message"


def test_hidden_conversation_can_be_restored(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="real_customer",
            session_id="default",
            content="hello",
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:real_customer:default"

    client.post(f"/api/v1/admin/conversations/{conversation_id}/hide")
    restored = client.post(f"/api/v1/admin/conversations/{conversation_id}/unhide")

    assert restored.status_code == 200
    assert client.get("/api/v1/admin/conversations").json()["data"]["total"] == 1


def test_handoff_pending_suppresses_ai_and_preserves_conversation_state(
    monkeypatch, tmp_path
):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="api",
            user_id="locked_customer",
            session_id="locked_session",
            content="initial handoff message",
            route="human",
            primary_intent="human_request",
            handoff_reason="human_required",
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "locked_customer",
            "session_id": "locked_session",
            "message": "客户继续发消息",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    data = response.json()["data"]
    detail = client.get(
        "/api/v1/admin/conversations/api:locked_customer:locked_session"
    ).json()["data"]
    assert data["answer"] == ""
    assert data.get("outbound_messages", []) == []
    assert data["metadata"]["handoff_locked"] is True
    assert detail["conversation"]["status"] == "handoff_pending"
    assert detail["conversation"]["last_route"] == "human"
    assert [item["sender_type"] for item in detail["messages"]] == [
        "customer",
        "customer",
    ]


def test_unclaimed_legacy_fallback_handoff_recovers_before_next_turn(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        conversation_blocks_ai,
        recover_automatic_handoff,
    )

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="api",
            user_id="legacy_fallback_customer",
            session_id="legacy_fallback_session",
            content="那你们的服务怎么进？多少钱？",
            route="human",
            primary_intent="ask_price",
            handoff_reason="template_not_found_to_handoff",
        )
    )
    assert conversation_blocks_ai(
        channel="api",
        user_id="legacy_fallback_customer",
        session_id="legacy_fallback_session",
    )

    recovered = asyncio.run(
        recover_automatic_handoff(
            channel="api",
            user_id="legacy_fallback_customer",
            session_id="legacy_fallback_session",
        )
    )

    assert recovered is True
    assert not conversation_blocks_ai(
        channel="api",
        user_id="legacy_fallback_customer",
        session_id="legacy_fallback_session",
    )


def test_explicit_human_handoff_does_not_auto_recover(monkeypatch, tmp_path):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        conversation_blocks_ai,
        recover_automatic_handoff,
    )

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="api",
            user_id="explicit_human_customer",
            session_id="explicit_human_session",
            content="我要找人工",
            route="human",
            primary_intent="human_request",
            handoff_reason="human_required",
        )
    )

    recovered = asyncio.run(
        recover_automatic_handoff(
            channel="api",
            user_id="explicit_human_customer",
            session_id="explicit_human_session",
        )
    )

    assert recovered is False
    assert conversation_blocks_ai(
        channel="api",
        user_id="explicit_human_customer",
        session_id="explicit_human_session",
    )


def test_human_active_suppresses_ai_until_admin_releases_it(monkeypatch, tmp_path):
    import asyncio

    from app.domains.customers.services.user_profile_service import get_profile_bundle

    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    conversation_id = "api:release_customer:release_session"
    handoff = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "release_customer",
            "session_id": "release_session",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    assert handoff.json()["data"]["need_human"] is True

    claimed = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/claim",
        json={"operator_id": "op_release"},
    )
    assert claimed.json()["data"]["status"] == "human_active"
    blocked = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "release_customer",
            "session_id": "release_session",
            "message": "人工接管期间的新消息",
            "kb_id": "kb_default",
            "metadata": {},
        },
    ).json()["data"]
    assert blocked["answer"] == ""
    claimed_detail = client.get(
        f"/api/v1/admin/conversations/{conversation_id}"
    ).json()["data"]["conversation"]
    assert claimed_detail["status"] == "human_active"
    assert claimed_detail["owner_id"] == "op_release"

    released = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/release-to-ai",
        json={"operator_id": "op_release"},
    )
    assert released.json()["data"]["status"] == "ai_active"
    profile = asyncio.run(get_profile_bundle("release_customer"))["profile"]
    assert profile["is_human_handoff"] is False
    assert profile["human_handoff_status"] is None

    resumed = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "release_customer",
            "session_id": "release_session",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    ).json()["data"]
    assert resumed["answer"]
    assert resumed["route"] == "agent"


def test_admin_only_displays_configured_wechat_material_group(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("EYUN_MATERIAL_GROUP_WC_ID", "material@chatroom")
    get_settings.cache_clear()
    for group_id in ("material@chatroom", "other@chatroom"):
        asyncio.run(
            record_customer_message(
                channel="wechat",
                user_id=group_id,
                session_id="default",
                content="[图片]",
            )
        )

    data = TestClient(app).get("/api/v1/admin/conversations").json()["data"]

    assert data["total"] == 1
    assert data["items"][0]["user_id"] == "material@chatroom"


def test_chat_creates_ai_owned_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    event_queue = conversation_event_broker.subscribe()

    chat = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    event = event_queue.get_nowait()
    conversation_event_broker.unsubscribe(event_queue)

    assert chat.status_code == 200
    assert event["conversation_id"] == "api:user_001:sess_001"
    assert event["reason"] == "message"
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 1
    item = conversations["items"][0]
    assert item["conversation_id"] == "api:user_001:sess_001"
    assert item["status"] in {"ai_active", "ai_waiting"}
    assert item["owner_id"] is None

    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001")
    assert detail.status_code == 200
    messages = detail.json()["data"]["messages"]
    assert [message["sender_type"] for message in messages] == ["customer", "ai"]


def test_conversation_exposes_customer_display_snapshot(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    client.post(
        "/api/v1/chat",
        json={
            "channel": "wechat",
            "user_id": "wxid_customer",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {
                "remark_name": "Alice Remark",
                "avatar_url": "https://example.com/avatar.jpg",
            },
        },
    )

    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    item = conversations["items"][0]
    assert item["user_display_name"] == "Alice Remark"
    assert item["user_avatar_url"] == "https://example.com/avatar.jpg"

    detail = client.get("/api/v1/admin/conversations/wechat:wxid_customer:sess_001")
    conversation = detail.json()["data"]["conversation"]
    assert conversation["user_display_name"] == "Alice Remark"
    assert conversation["user_avatar_url"] == "https://example.com/avatar.jpg"


def test_conversation_timestamps_are_serialized_as_utc(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    item = client.get("/api/v1/admin/conversations").json()["data"]["items"][0]
    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001").json()[
        "data"
    ]

    assert item["created_at"].endswith("+00:00")
    assert item["updated_at"].endswith("+00:00")
    assert detail["messages"][0]["created_at"].endswith("+00:00")


def test_existing_eyun_messages_recover_media_from_raw_content(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_customer",
            session_id="default",
            content="[视频]",
            metadata={
                "provider": "eyun",
                "message_type": "60003",
                "raw_content": (
                    '<msg><videomsg cdnvideourl="https://cdn.example.com/old.mp4" />'
                    "</msg>"
                ),
                "media": {
                    "type": "video",
                    "url": "https://cdn.example.com/old.mp4",
                    "fallback": False,
                },
            },
        )
    )

    client = TestClient(app)
    detail = client.get(
        "/api/v1/admin/conversations/wechat:wxid_customer:default"
    ).json()["data"]

    assert detail["messages"][0]["metadata"]["media"] == {
        "type": "video",
        "original_url": "https://cdn.example.com/old.mp4",
        "fallback": True,
        "resolve_status": "pending",
    }


def test_human_cannot_reply_until_conversation_is_claimed(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    response = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/reply",
        json={"operator_id": "op_001", "content": "人工回复"},
    )

    assert response.status_code == 409
    assert "不能人工回复" in response.json()["message"]


def test_claimed_handoff_conversation_accepts_human_reply(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    claim = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )
    reply = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/reply",
        json={"operator_id": "op_001", "content": "您好，我来处理。"},
    )

    assert claim.status_code == 200
    assert claim.json()["data"]["status"] == "human_active"
    assert reply.status_code == 200
    assert reply.json()["data"]["status"] == "human_active"

    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001")
    messages = detail.json()["data"]["messages"]
    assert messages[-1]["sender_type"] == "human"
    assert messages[-1]["content"] == "您好，我来处理。"

    memories = client.get("/api/v1/users/user_001/memories").json()["data"]["items"]
    assert memories[-1]["role"] == "human"
    assert memories[-1]["content"] == "您好，我来处理。"


def test_human_reply_to_eyun_conversation_sends_via_provider(monkeypatch, tmp_path):
    from app.services import eyun_callback_service, message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    sent = []

    async def fail_enqueue(payload):
        raise AssertionError("non-text callbacks should not enter the AI queue")

    async def fake_enqueue_wechat_outbound(**kwargs):
        sent.append(kwargs)
        return {"id": 1, "status": "queued"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail_enqueue)
    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue_wechat_outbound,
    )
    client = TestClient(app)

    callback = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60003",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "toUser": "wxid_bot",
                "content": "<msg><videomsg /></msg>",
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )
    claim = client.post(
        "/api/v1/admin/conversations/wechat:wxid_sender:default/claim",
        json={"operator_id": "op_001"},
    )
    reply = client.post(
        "/api/v1/admin/conversations/wechat:wxid_sender:default/reply",
        json={"operator_id": "op_001", "content": "human reply"},
    )

    assert callback.status_code == 200
    assert claim.status_code == 200
    assert reply.status_code == 200
    assert len(sent) == 1
    assert sent[0]["w_id"] == "wid_test"
    assert sent[0]["wc_id"] == "wxid_sender"
    assert sent[0]["content"] == "human reply"
    assert sent[0]["source_batch_key"].startswith("workbench:")
    assert sent[0]["conversation_message_id"] > 0

    detail = client.get("/api/v1/admin/conversations/wechat:wxid_sender:default")
    messages = detail.json()["data"]["messages"]
    assert messages[-1]["sender_type"] == "human"
    assert messages[-1]["content"] == "human reply"
    assert messages[-1]["delivery_status"] == "queued"


def test_claimed_eyun_conversation_accepts_image_and_received_emoji(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        record_customer_message,
    )
    from app.integrations.eyun.services import message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    queued = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued), "status": "queued"}

    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue,
    )
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_customer",
            session_id="default",
            content="[表情]",
            message_id="emoji-provider-message",
            metadata={
                "provider": "eyun",
                "w_id": "wid-1",
                "from_user": "wxid_customer",
                "message_type": "60006",
                "raw_content": "<msg><emoji md5=\"abc123\" len=\"456\" /></msg>",
                "media": {
                    "type": "emoji",
                    "url": "https://cdn.example.com/emoji.gif",
                    "md5": "abc123",
                    "size": "456",
                    "fallback": False,
                },
            },
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:wxid_customer:default"
    assert client.post(
        f"/api/v1/admin/conversations/{conversation_id}/claim",
        json={"operator_id": "op-media"},
    ).status_code == 200

    emoji_items = client.get(
        f"/api/v1/admin/conversations/{conversation_id}/emojis"
    ).json()["data"]["items"]
    assert emoji_items[0]["md5"] == "abc123"

    image_response = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/reply-image",
        data={"operator_id": "op-media"},
        files={"file": ("reply.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
    )
    emoji_response = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/reply-emoji",
        json={
            "operator_id": "op-media",
            "source_message_id": emoji_items[0]["message_id"],
        },
    )

    assert image_response.status_code == 200
    assert emoji_response.status_code == 200
    assert queued[0]["message_type"] == "image"
    assert "/static/workbench-media/" in queued[0]["content"]
    assert queued[1]["message_type"] == "emoji"
    assert '"md5": "abc123"' in queued[1]["content"]


def test_claimed_eyun_conversation_sends_care_manual_link_card(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        record_customer_message,
    )
    from app.domains.sales.services import care_manual_service
    from app.integrations.eyun.services import message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    queued = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": 1, "status": "queued"}

    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue,
    )
    monkeypatch.setattr(
        care_manual_service,
        "get_care_manual",
        lambda card_id: {
            "id": card_id,
            "title": "【建兰玉白丹红】养护注意事项",
            "note_url": "https://h5.example.com/note/11",
            "cover_url": "https://img.example.com/11.jpg",
            "card_description": "玉白丹红养护手册",
            "available": True,
        },
    )
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_manual_customer",
            session_id="default",
            content="发我养护手册",
            metadata={
                "provider": "eyun",
                "w_id": "wid-1",
                "from_user": "wxid_manual_customer",
                "message_type": "60001",
            },
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:wxid_manual_customer:default"
    assert client.post(
        f"/api/v1/admin/conversations/{conversation_id}/claim",
        json={"operator_id": "op-manual"},
    ).status_code == 200

    response = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/reply-care-manual",
        json={"operator_id": "op-manual", "care_manual_id": 11},
    )

    assert response.status_code == 200
    assert queued[0]["message_type"] == "link_card"
    assert json.loads(queued[0]["content"]) == {
        "title": "【建兰玉白丹红】养护注意事项",
        "url": "https://h5.example.com/note/11",
        "description": "玉白丹红养护手册",
        "thumb_url": "https://img.example.com/11.jpg",
    }
    detail = client.get(
        f"/api/v1/admin/conversations/{conversation_id}"
    ).json()["data"]
    message = detail["messages"][-1]
    assert message["sender_type"] == "human"
    assert message["delivery_status"] == "queued"
    assert message["metadata"]["link_card"]["url"].endswith("/note/11")


def test_resolve_eyun_video_replaces_expired_media_url(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_contact_snapshot(**kwargs):
        return {}

    async def fake_download_eyun_video(**kwargs):
        assert kwargs["msg_id"] == "789"
        return "/static/media/playable.mp4"

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    monkeypatch.setattr(
        eyun_callback_service, "download_eyun_video", fake_download_eyun_video
    )
    client = TestClient(app)
    client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60003",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "toUser": "wxid_bot",
                "content": '<msg><videomsg cdnvideourl="https://expired.example/video" /></msg>',
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )
    detail = client.get(
        "/api/v1/admin/conversations/wechat:wxid_sender:default"
    ).json()["data"]
    message_id = detail["messages"][0]["id"]

    response = client.post(
        f"/api/v1/admin/conversations/messages/{message_id}/resolve-media"
    )

    assert response.status_code == 200
    assert response.json()["data"]["metadata"]["media"]["url"] == (
        "/static/media/playable.mp4"
    )
    assert response.json()["data"]["metadata"]["media"]["resolve_status"] == (
        "succeeded"
    )


def test_failed_eyun_video_resolution_is_persisted(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_contact_snapshot(**kwargs):
        return {}

    async def fail_download(**kwargs):
        raise RuntimeError("provider download failed")

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    monkeypatch.setattr(eyun_callback_service, "download_eyun_video", fail_download)
    client = TestClient(app)
    client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60003",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "toUser": "wxid_bot",
                "content": "<msg><videomsg /></msg>",
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )
    detail_url = "/api/v1/admin/conversations/wechat:wxid_sender:default"
    message_id = client.get(detail_url).json()["data"]["messages"][0]["id"]

    response = client.post(
        f"/api/v1/admin/conversations/messages/{message_id}/resolve-media"
    )
    media = client.get(detail_url).json()["data"]["messages"][0]["metadata"][
        "media"
    ]

    assert response.status_code == 502
    assert media["resolve_status"] == "failed"
    assert media["resolve_error"] == "provider_download_failed"

    retry = client.post(
        f"/api/v1/admin/conversations/messages/{message_id}/resolve-media"
    )
    assert retry.status_code == 200
    assert retry.json()["data"]["metadata"]["media"]["resolve_status"] == "failed"


def test_non_resolvable_eyun_video_is_not_marked_pending(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_customer",
            session_id="default",
            content="[video]",
            metadata={
                "provider": "eyun",
                "message_type": "other",
                "media": {
                    "type": "video",
                    "url": "https://cdn.example.com/video.mp4",
                },
            },
        )
    )

    client = TestClient(app)
    message = client.get(
        "/api/v1/admin/conversations/wechat:wxid_customer:default"
    ).json()["data"]["messages"][0]

    assert message["metadata"]["media"]["url"] == (
        "https://cdn.example.com/video.mp4"
    )
    assert "resolve_status" not in message["metadata"]["media"]


def test_message_panel_does_not_resolve_video_on_playback_error():
    panel = (
        Path(__file__).parents[2]
        / "admin"
        / "src"
        / "views"
        / "workbench"
        / "components"
        / "MessagePanel.vue"
    ).read_text(encoding="utf-8")

    assert '@error="resolveVideo(message)"' not in panel
    assert '@error="markVideoFailed(message)"' in panel
    assert "autoResolvePendingVideos" in panel
    assert "canResolveVideo(message) && media?.resolve_status === 'pending'" in panel
    assert "void resolveVideo(message, { silent: true })" in panel


def test_mark_conversation_read_clears_unread_count(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "浣犲ソ",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    before = client.get("/api/v1/admin/conversations").json()["data"]["items"][0]
    response = client.post("/api/v1/admin/conversations/api:user_001:sess_001/read")
    after = client.get("/api/v1/admin/conversations").json()["data"]["items"][0]

    assert before["unread_count"] > 0
    assert response.status_code == 200
    assert response.json()["data"]["unread_count"] == 0
    assert after["unread_count"] == 0


def test_marking_an_already_read_conversation_does_not_publish_again(
    monkeypatch, tmp_path
):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    event_queue = conversation_event_broker.subscribe()

    client.post("/api/v1/admin/conversations/api:user_001:sess_001/read")
    first_event = event_queue.get_nowait()
    client.post("/api/v1/admin/conversations/api:user_001:sess_001/read")

    assert first_event["reason"] == "read"
    assert event_queue.empty()
    conversation_event_broker.unsubscribe(event_queue)


def test_force_handoff_allows_operator_to_claim_ai_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    force = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/force-handoff",
        json={"operator_id": "lead_001", "reason": "AI 可能误答"},
    )
    claim = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )

    assert force.status_code == 200
    assert force.json()["data"]["status"] == "handoff_pending"
    assert force.json()["data"]["handoff_reason"] == "AI 可能误答"
    assert claim.status_code == 200


def test_release_to_ai_and_resolve_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )

    release = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/release-to-ai",
        json={"operator_id": "op_001"},
    )
    resolve = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/resolve",
        json={"operator_id": "op_001", "reason": "已处理"},
    )

    assert release.status_code == 200
    assert release.json()["data"]["status"] == "ai_active"
    assert resolve.status_code == 200
    assert resolve.json()["data"]["status"] == "resolved"


def test_conversation_apis_require_authorization(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    response = client.get("/api/v1/admin/conversations")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
