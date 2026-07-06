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


def test_wechat_callback_records_group_text_without_ai_queue(monkeypatch, tmp_path):
    from app.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)

    async def fail_enqueue(payload):
        raise AssertionError("group messages should not enter the AI queue")

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail_enqueue)
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
    assert conversations["total"] == 1
    item = conversations["items"][0]
    assert item["conversation_id"] == "wechat:12345@chatroom:default"
    assert item["last_message"] == "group hello"

    detail = client.get("/api/v1/admin/conversations/wechat:12345@chatroom:default")
    messages = detail.json()["data"]["messages"]
    assert messages[0]["content"] == "group hello"
    assert messages[0]["metadata"]["from_user"] == "wxid_sender"
