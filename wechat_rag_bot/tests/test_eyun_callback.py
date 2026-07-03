from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_eyun_callback_accepts_json_payload():
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


def test_wechat_callback_handles_eyun_text_payload(monkeypatch):
    from app.services import eyun_callback_service

    monkeypatch.setenv("WECHAT_DEFAULT_KB_ID", "kb_default")
    get_settings.cache_clear()
    sent_messages = []

    async def fake_handle_chat(request):
        assert request.channel == "wechat"
        assert request.user_id == "wxid_customer"
        assert request.message == "hello"
        assert request.metadata["provider"] == "eyun"
        assert request.metadata["new_msg_id"] == 123
        return {"answer": "AI reply"}

    async def fake_send_text(*, w_id: str, wc_id: str, content: str):
        sent_messages.append({"w_id": w_id, "wc_id": wc_id, "content": content})

    monkeypatch.setattr(eyun_callback_service, "handle_chat", fake_handle_chat)
    monkeypatch.setattr(eyun_callback_service, "send_eyun_text", fake_send_text)
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
    assert sent_messages == [
        {"w_id": "wid_test", "wc_id": "wxid_customer", "content": "AI reply"}
    ]
