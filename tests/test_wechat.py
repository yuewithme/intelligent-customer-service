import hashlib

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.wechat_service import build_text_reply, parse_message, verify_signature


def test_wechat_signature_verification():
    token, timestamp, nonce = "token", "1710000000", "nonce"
    signature = hashlib.sha1(
        "".join(sorted([token, timestamp, nonce])).encode()
    ).hexdigest()

    assert verify_signature(token, signature, timestamp, nonce)
    assert not verify_signature(token, "invalid", timestamp, nonce)


def test_wechat_xml_parse_and_reply_escape():
    message = parse_message(
        b"<xml><ToUserName>gh_1</ToUserName><FromUserName>openid</FromUserName>"
        b"<CreateTime>1</CreateTime><MsgType>text</MsgType>"
        b"<Content>1 &lt; 2</Content><MsgId>99</MsgId></xml>"
    )
    reply = build_text_reply("openid", "gh_1", "A < B & C")

    assert message["Content"] == "1 < 2"
    assert "<![CDATA[A < B & C]]>" in reply


@pytest.mark.asyncio
async def test_wechat_post_delegates_to_handle_chat(monkeypatch):
    from app.routers import wechat

    async def fake_handle_chat(request):
        assert request.channel == "wechat"
        assert request.metadata["wechat_msg_id"] == "10001"
        return {
            "answer": "统一主流程回答",
            "session_id": "sess_001",
            "sources": [],
            "usage": {},
            "reply_type": "rag",
            "route": "rag_answer",
            "intent": {},
            "template": {},
            "need_human": False,
            "next_action": None,
            "trace_id": "req_001",
        }

    monkeypatch.setattr(wechat, "handle_chat", fake_handle_chat)
    client = TestClient(app)
    timestamp, nonce = "1710000000", "nonce"
    signature = hashlib.sha1(
        "".join(sorted(["change_me", timestamp, nonce])).encode()
    ).hexdigest()
    xml = (
        "<xml><ToUserName>gh_1</ToUserName><FromUserName>openid</FromUserName>"
        "<CreateTime>1</CreateTime><MsgType>text</MsgType>"
        "<Content>问题</Content><MsgId>10001</MsgId></xml>"
    )

    response = client.post(
        f"/wechat/callback?signature={signature}&timestamp={timestamp}&nonce={nonce}",
        content=xml,
    )

    assert response.status_code == 200
    assert "统一主流程回答" in response.text
    assert response.headers["content-type"].startswith("application/xml")


def test_wechat_post_rejects_invalid_signature():
    client = TestClient(app)
    response = client.post(
        "/wechat/callback?signature=invalid&timestamp=1&nonce=2",
        content="<xml />",
    )

    assert response.status_code == 403
