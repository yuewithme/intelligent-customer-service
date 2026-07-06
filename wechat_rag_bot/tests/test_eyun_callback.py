from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def _reset_settings(monkeypatch, tmp_path):
    db_path = tmp_path / "chat_logs.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "mock")
    monkeypatch.setenv("STATE_PROVIDER", "memory")
    get_settings.cache_clear()


def test_eyun_callback_accepts_json_payload(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_enqueue(payload):
        return {"batch_key": "wid_test:wxid_customer"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    client = TestClient(app)

    response = client.post(
        "/eyun/callback",
        json={
            "account": "test_account",
            "messageType": "60001",
            "wcId": "wxid_test",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_test",
                "content": "hello",
                "newMsgId": 123,
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": "1000", "message": "success", "data": None}


def test_wechat_callback_accepts_eyun_test_payload():
    client = TestClient(app)

    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "00000",
            "wcId": "wxid_test",
            "data": {},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": "1000", "message": "success", "data": None}


def test_wechat_callback_enqueues_eyun_text_payload(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    enqueued = []

    async def fake_enqueue(payload):
        enqueued.append(payload)
        return {"batch_key": "wid_test:wxid_customer"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)
    client = TestClient(app)

    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "hello",
                "msgId": 456,
                "newMsgId": 123,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert response.json() == {"code": "1000", "message": "success", "data": None}
    assert len(enqueued) == 1
    assert enqueued[0]["data"]["content"] == "hello"


def test_wechat_callback_records_private_messages_under_same_wcid(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    enqueued = []

    async def fake_enqueue(payload):
        enqueued.append(payload)
        return {"batch_key": "wid_test:wxid_customer"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)

    async def fake_fetch_image_url(**kwargs):
        assert kwargs["w_id"] == "wid_test"
        assert kwargs["msg_id"] == "456"
        assert kwargs["image_type"] == 1
        return "https://cdn.example.com/original.jpg"

    monkeypatch.setattr(eyun_callback_service, "fetch_eyun_image_url", fake_fetch_image_url)
    client = TestClient(app)

    text_response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "hello",
                "msgId": 455,
                "newMsgId": 122,
                "self": False,
            },
        },
    )
    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60002",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "toUser": "wxid_bot",
                "content": "<?xml version=\"1.0\"?><msg><img md5=\"abc\" /></msg>",
                "img": "/9j/4AAQSkZJRgABAQAASABIAAD/",
                "msgId": 456,
                "newMsgId": 123,
                "self": False,
            },
        },
    )

    assert text_response.status_code == 200
    assert response.status_code == 200
    assert len(enqueued) == 1
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 1
    item = conversations["items"][0]
    assert item["conversation_id"] == "wechat:wxid_customer:default"
    assert item["status"] == "handoff_pending"
    assert item["last_message"] == "[图片]"

    detail = client.get("/api/v1/admin/conversations/wechat:wxid_customer:default")
    messages = detail.json()["data"]["messages"]
    assert [message["content"] for message in messages] == ["hello", "[图片]"]
    assert all(message["sender_type"] == "customer" for message in messages)
    assert messages[1]["metadata"]["message_type"] == "60002"
    assert messages[1]["metadata"]["image_thumb_base64"]
    assert messages[1]["metadata"]["media"] == {
        "type": "image",
        "url": "https://cdn.example.com/original.jpg",
        "thumb_base64": "/9j/4AAQSkZJRgABAQAASABIAAD/",
        "fallback": False,
    }


def test_wechat_callback_ignores_group_messages(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fail_enqueue(payload):
        raise AssertionError("group messages should not enter the AI queue")

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail_enqueue)

    async def fail_contact_snapshot(**kwargs):
        raise AssertionError("group messages should not query contact details")

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fail_contact_snapshot
    )
    client = TestClient(app)

    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "80001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "fromGroup": "12345@chatroom",
                "toUser": "wxid_bot",
                "content": "group hello",
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 0


def test_eyun_non_image_messages_expose_media_links(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_contact_snapshot(**kwargs):
        return {}

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    client = TestClient(app)
    payloads = [
        (
            "60003",
            '<msg><videomsg cdnvideourl="https://cdn.example.com/video.mp4" /></msg>',
            {},
            "video",
            "https://cdn.example.com/video.mp4",
        ),
        (
            "60004",
            "<msg><voicemsg /></msg>",
            {"url": "https://cdn.example.com/voice.mp3"},
            "audio",
            "https://cdn.example.com/voice.mp3",
        ),
        (
            "60006",
            "<msg><emoji /></msg>",
            {"url": "https://cdn.example.com/emoji.gif"},
            "image",
            "https://cdn.example.com/emoji.gif",
        ),
        (
            "60007",
            "<msg><appmsg><title>Example</title>"
            "<url>https://example.com/article</url></appmsg></msg>",
            {},
            "link",
            "https://example.com/article",
        ),
    ]

    for index, (message_type, content, extra, expected_type, expected_url) in enumerate(
        payloads
    ):
        response = client.post(
            "/wechat/callback",
            json={
                "account": "test_account",
                "messageType": message_type,
                "wcId": "wxid_bot",
                "data": {
                    "wId": "wid_test",
                    "fromUser": f"wxid_customer_{index}",
                    "toUser": "wxid_bot",
                    "content": content,
                    "msgId": 500 + index,
                    "newMsgId": 600 + index,
                    "self": False,
                    **extra,
                },
            },
        )
        assert response.status_code == 200

        detail = client.get(
            f"/api/v1/admin/conversations/wechat:wxid_customer_{index}:default"
        ).json()["data"]
        media = detail["messages"][0]["metadata"]["media"]
        assert media["type"] == expected_type
        assert media["url"] == expected_url
