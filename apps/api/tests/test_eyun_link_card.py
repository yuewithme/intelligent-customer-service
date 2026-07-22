import json

import pytest

from app.core.config import get_settings
from app.services import eyun_callback_service
from app.services import link_card_thumbnail_service
from app.integrations.eyun.services.eyun_callback_service import send_eyun_link_card
from app.integrations.eyun.services.message_risk_control_service import (
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


@pytest.mark.asyncio
async def test_send_eyun_link_card_fills_required_fields_for_url_only_card(monkeypatch):
    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "test-token")
    monkeypatch.setenv(
        "EYUN_LINK_CARD_DEFAULT_THUMB_URL",
        "https://bot.example.com/static/default-link-card.jpg",
    )
    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "1000", "data": {"newMsgId": 456}}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return FakeResponse()

    monkeypatch.setattr(eyun_callback_service.httpx, "AsyncClient", FakeClient)

    await send_eyun_link_card(
        w_id="wid-1",
        wc_id="customer-1",
        card={"url": "https://j.youzan.com/yddHbe"},
    )

    assert captured["json"] == {
        "wId": "wid-1",
        "wcId": "customer-1",
        "title": "查看详情",
        "url": "https://j.youzan.com/yddHbe",
        "description": "点击查看详情",
        "thumbUrl": "https://bot.example.com/static/default-link-card.jpg",
    }


@pytest.mark.asyncio
async def test_send_eyun_link_card_compresses_oversized_thumbnail_and_retries(
    monkeypatch,
):
    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "test-token")
    get_settings.cache_clear()
    payloads = []

    class FakeResponse:
        def __init__(self, result):
            self._result = result

        def raise_for_status(self):
            return None

        def json(self):
            return self._result

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            payloads.append(kwargs["json"].copy())
            if len(payloads) == 1:
                return FakeResponse(
                    {
                        "code": "1001",
                        "message": "图片大小超出：51200byte,请压缩图片后再试",
                    }
                )
            return FakeResponse({"code": "1000", "data": {"newMsgId": 789}})

    async def fake_compress(source_url):
        assert source_url == "https://img.example.com/large.jpg"
        return "https://bot.example.com/static/link-card-thumbs/small.jpg"

    monkeypatch.setattr(eyun_callback_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        link_card_thumbnail_service,
        "compress_link_card_thumbnail",
        fake_compress,
    )

    result = await send_eyun_link_card(
        w_id="wid-1",
        wc_id="customer-1",
        card={
            "title": "养兰资料",
            "url": "https://j.youzan.com/yddHbe",
            "description": "点击查看",
            "thumb_url": "https://img.example.com/large.jpg",
        },
    )

    assert result["code"] == "1000"
    assert len(payloads) == 2
    assert payloads[0]["thumbUrl"] == "https://img.example.com/large.jpg"
    assert payloads[1]["thumbUrl"] == (
        "https://bot.example.com/static/link-card-thumbs/small.jpg"
    )


@pytest.mark.asyncio
async def test_send_eyun_link_card_rejects_url_only_card_without_default_thumb(
    monkeypatch,
):
    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "test-token")
    monkeypatch.delenv("EYUN_LINK_CARD_DEFAULT_THUMB_URL", raising=False)
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="thumbnail is required"):
        await send_eyun_link_card(
            w_id="wid-1",
            wc_id="customer-1",
            card={"url": "https://j.youzan.com/yddHbe"},
        )


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
