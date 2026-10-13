import json

import pytest


@pytest.mark.asyncio
async def test_send_eyun_mini_program_posts_structured_card(monkeypatch):
    from app.core.config import get_settings
    from app.integrations.eyun.services import eyun_callback_service

    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "Bearer test")
    get_settings.cache_clear()
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "1000", "message": "success"}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            requests.append((url, headers, json))
            return FakeResponse()

    monkeypatch.setattr(eyun_callback_service.httpx, "AsyncClient", FakeClient)

    await eyun_callback_service.send_eyun_mini_program(
        w_id="wid-1",
        wc_id="wxid-customer",
        card={
            "display_name": "萧岚苑",
            "icon_url": "https://cdn.example.com/icon.jpg",
            "app_id": "wx123",
            "page_path": "pages/goods/detail?alias=abc",
            "thumb_url": "https://cdn.example.com/goods.jpg",
            "title": "白色大花蝴蝶兰",
            "user_name": "gh_123@app",
        },
    )

    assert requests == [
        (
            "https://eyun.example.com/sendApplets",
            {"Authorization": "Bearer test", "Content-Type": "application/json"},
            {
                "wId": "wid-1",
                "wcId": "wxid-customer",
                "displayName": "萧岚苑",
                "iconUrl": "https://cdn.example.com/icon.jpg",
                "appId": "wx123",
                "pagePath": "pages/goods/detail?alias=abc",
                "thumbUrl": "https://cdn.example.com/goods.jpg",
                "title": "白色大花蝴蝶兰",
                "userName": "gh_123@app",
            },
        )
    ]


def test_outbound_mini_program_round_trips_through_queue_encoding():
    from app.integrations.eyun.services.message_risk_control_service import (
        _decode_outbound_content,
        _encode_outbound_content,
    )

    card = {
        "display_name": "萧岚苑",
        "app_id": "wx123",
        "page_path": "pages/order/list",
        "title": "查看我的订单",
    }
    stored = _encode_outbound_content("mini_program", json.dumps(card, ensure_ascii=False))

    message_type, content = _decode_outbound_content(stored)
    assert message_type == "mini_program"
    assert json.loads(content) == card


MINI_PROGRAM_XML = """<msg><appmsg><title>会员日</title><type>33</type>
<url>https://mp.weixin.qq.com/mp/waerrpage?type=upgrade</url>
<appattach><cdnthumburl>test-cdn-id</cdnthumburl><cdnthumbaeskey>test-cdn-key</cdnthumbaeskey>
<cdnthumblength>11675</cdnthumblength></appattach>
<sourcedisplayname>萧岚苑</sourcedisplayname><weappinfo>
<username>gh_example@app</username><appid>wxexample</appid>
<weappiconurl>https://example.com/icon.png</weappiconurl>
<pagepath><![CDATA[pages/common/index.html?path=%2Fpages%2Fhome%3Fa%3D1%26b%3D2]]></pagepath>
</weappinfo></appmsg></msg>"""


@pytest.mark.parametrize("app_type,message_type", [("33", "60010"), ("36", "60999")])
def test_received_card_preserves_page_path_and_does_not_use_upgrade_url(app_type, message_type):
    from app.integrations.eyun.services.eyun_callback_service import extract_eyun_media_metadata

    media = extract_eyun_media_metadata(message_type, {"content": MINI_PROGRAM_XML.replace("<type>33", f"<type>{app_type}")})
    assert media["type"] == "mini_program"
    assert media["app_id"] == "wxexample"
    assert media["display_name"] == "萧岚苑"
    assert media["page_path"] == "pages/common/index.html?path=%2Fpages%2Fhome%3Fa%3D1%26b%3D2"
    assert media["thumbnail_source"] == "wechat_cdn"
    assert "url" not in media


def test_received_product_card_uses_public_cover():
    from app.integrations.eyun.services.eyun_callback_service import extract_eyun_mini_program_card

    xml = MINI_PROGRAM_XML.replace("</weappinfo>", "<weapppagethumbrawurl>https://example.com/goods.png</weapppagethumbrawurl></weappinfo>")
    assert extract_eyun_mini_program_card(xml)["thumb_url"] == "https://example.com/goods.png"
    assert extract_eyun_mini_program_card("not xml") is None
    assert extract_eyun_mini_program_card(MINI_PROGRAM_XML.replace("<type>33", "<type>5")) is None


def test_legacy_received_card_gets_structured_metadata_without_database_rewrite():
    from datetime import datetime, timezone
    from app.infrastructure.database.models import ConversationMessageModel
    from app.domains.conversations.services.conversation_service import _message_to_dict

    stored = json.dumps({"provider": "eyun", "message_type": "60010", "raw_content": MINI_PROGRAM_XML,
        "media": {"type": "mini_program", "file_name": "会员日", "url": "https://mp.weixin.qq.com/mp/waerrpage"}})
    row = ConversationMessageModel(id=7, conversation_id="wechat:user:owner", content="[小程序]",
        sender_type="customer", metadata_json=stored, created_at=datetime.now(timezone.utc))
    metadata = _message_to_dict(row)["metadata"]
    assert metadata["mini_program"]["title"] == "会员日"
    assert metadata["mini_program"]["thumb_url"] == "/api/v1/admin/conversations/messages/7/mini-program-thumbnail"
    assert "url" not in metadata["media"]
    assert row.metadata_json == stored


@pytest.mark.asyncio
async def test_encrypted_card_cover_is_downloaded_once_and_cached(monkeypatch, tmp_path):
    from pathlib import Path
    from app.integrations.eyun.services import eyun_callback_service, eyun_material_service

    image = (Path(__file__).resolve().parents[2] / "admin/public/service-member-day.jpg").read_bytes()
    calls = []
    async def download(**kwargs):
        calls.append(kwargs)
        return image

    monkeypatch.setattr(eyun_callback_service, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(eyun_material_service, "_download_cdn_bytes", download)
    for _ in range(2):
        assert await eyun_callback_service.download_eyun_mini_program_thumbnail(w_id="wid-test", content=MINI_PROGRAM_XML) == image
    assert len(calls) == 1
    assert calls[0]["file_type"] == 3
    assert calls[0]["size"] == len(image)


def test_received_card_cover_endpoint(monkeypatch, tmp_path):
    import asyncio
    from fastapi.testclient import TestClient
    from test_admin_conversations import _reset_settings
    from app.main import app
    from app.domains.conversations.services.conversation_service import record_customer_message
    from app.integrations.eyun.services import eyun_callback_service

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(record_customer_message(channel="wechat", user_id="card-user", session_id="owner", content="[小程序]",
        message_id="card-1", status="ai_active", route="inbound_mini_program", primary_intent="mini_program",
        metadata={"provider": "eyun", "message_type": "60010", "w_id": "wid-test", "raw_content": MINI_PROGRAM_XML}))
    async def download(**kwargs):
        assert kwargs == {"w_id": "wid-test", "content": MINI_PROGRAM_XML}
        return b"\xff\xd8\xfftest-image"
    monkeypatch.setattr(eyun_callback_service, "download_eyun_mini_program_thumbnail", download)
    client = TestClient(app)
    detail = client.get("/api/v1/admin/conversations/wechat:card-user:owner").json()["data"]
    response = client.get(detail["messages"][0]["metadata"]["mini_program"]["thumb_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8\xfftest-image"
    assert client.get("/api/v1/admin/conversations/messages/99999/mini-program-thumbnail").status_code == 404
