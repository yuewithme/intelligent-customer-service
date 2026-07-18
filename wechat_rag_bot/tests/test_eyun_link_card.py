import json

import pytest

from app.config import get_settings
from app.services import eyun_callback_service
from app.services.eyun_callback_service import send_eyun_link_card
from app.services.message_risk_control_service import (
    _decode_outbound_content,
    _encode_outbound_content,
)


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_eyun_link_card_uses_send_url(monkeypatch):
    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "test-token")
    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "1000", "data": {"newMsgId": 123}}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(eyun_callback_service.httpx, "AsyncClient", FakeClient)

    result = await send_eyun_link_card(
        w_id="wid-1",
        wc_id="customer-1",
        card={
            "title": "兰花标准上盆示范",
            "url": "https://j.youzan.com/yddHbe",
            "description": "点击查看完整示范视频",
            "thumb_url": "https://img01.yzcdn.cn/card.jpg",
        },
    )

    assert result["code"] == "1000"
    assert captured["url"] == "https://eyun.example.com/sendUrl"
    assert captured["json"] == {
        "wId": "wid-1",
        "wcId": "customer-1",
        "title": "兰花标准上盆示范",
        "url": "https://j.youzan.com/yddHbe",
        "description": "点击查看完整示范视频",
        "thumbUrl": "https://img01.yzcdn.cn/card.jpg",
    }


def test_link_card_survives_outbound_queue_encoding():
    card = json.dumps(
        {
            "title": "上盆示范",
            "url": "https://j.youzan.com/yddHbe",
            "description": "点击查看",
            "thumb_url": "https://img01.yzcdn.cn/card.jpg",
        },
        ensure_ascii=False,
    )

    message_type, decoded = _decode_outbound_content(
        _encode_outbound_content("link_card", card)
    )

    assert message_type == "link_card"
    assert decoded == card
