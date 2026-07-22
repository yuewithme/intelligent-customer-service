import json

import pytest


@pytest.mark.asyncio
async def test_send_eyun_mini_program_posts_structured_card(monkeypatch):
    from app.core.config import get_settings
    from app.services import eyun_callback_service

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
