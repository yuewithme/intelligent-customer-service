from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.conversation_service import record_customer_message
from app.services.conversation_event_service import conversation_event_broker


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
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    sent = []

    async def fail_enqueue(payload):
        raise AssertionError("non-text callbacks should not enter the AI queue")

    async def fake_send_eyun_text(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail_enqueue)
    monkeypatch.setattr(eyun_callback_service, "send_eyun_text", fake_send_eyun_text)
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
    assert sent == [
        {"w_id": "wid_test", "wc_id": "wxid_sender", "content": "human reply"}
    ]

    detail = client.get("/api/v1/admin/conversations/wechat:wxid_sender:default")
    messages = detail.json()["data"]["messages"]
    assert messages[-1]["sender_type"] == "human"
    assert messages[-1]["content"] == "human reply"


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


def test_message_panel_does_not_resolve_video_on_playback_error():
    panel = (
        Path(__file__).parents[2]
        / "admin-web"
        / "src"
        / "views"
        / "workbench"
        / "components"
        / "MessagePanel.vue"
    ).read_text(encoding="utf-8")

    assert '@error="resolveVideo(message)"' not in panel
    assert '@error="markVideoFailed(message)"' in panel


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
