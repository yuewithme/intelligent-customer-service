import json

import pytest

from app.config import get_settings


class _FakeResponse:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": json.dumps(self._content)}}]}


class _FakeClient:
    def __init__(self, responses, requests):
        self._responses = responses
        self._requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def post(self, url, **kwargs):
        self._requests.append({"url": url, **kwargs})
        return _FakeResponse(self._responses.pop(0))


@pytest.fixture(autouse=True)
def reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_analyze_image_calls_dashscope_compatible_api(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    monkeypatch.setenv("VISION_OCR_ENABLED", "false")
    responses = [
        {
            "image_type": "product",
            "summary": "一盆开花的兰花",
            "visible_text": [],
            "visible_facts": ["花朵为黄色"],
            "possible_need": "咨询品种",
            "needs_ocr": False,
            "needs_clarification": False,
            "risk_flags": [],
            "confidence": 0.92,
        }
    ]
    requests = []
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, requests),
    )

    result = await vision_service.analyze_image("https://cdn.example.com/orchid.jpg")

    assert result.summary == "一盆开花的兰花"
    assert requests[0]["url"].endswith("/chat/completions")
    assert requests[0]["headers"]["Authorization"] == "Bearer test-key"
    assert requests[0]["json"]["model"] == "qwen3.7-plus"
    assert requests[0]["json"]["messages"][0]["content"][0] == {
        "type": "image_url",
        "image_url": {"url": "https://cdn.example.com/orchid.jpg"},
    }


@pytest.mark.asyncio
async def test_analyze_image_runs_ocr_when_requested(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    responses = [
        {
            "image_type": "order_screenshot",
            "summary": "订单截图",
            "visible_text": ["已发货"],
            "visible_facts": [],
            "possible_need": "",
            "needs_ocr": True,
            "needs_clarification": False,
            "risk_flags": [],
            "confidence": 0.7,
        },
        {
            "image_type": "order_screenshot",
            "summary": "订单截图",
            "visible_text": ["已发货", "中通快递"],
            "visible_facts": [],
            "possible_need": "",
            "needs_ocr": False,
            "needs_clarification": False,
            "risk_flags": [],
            "confidence": 0.95,
        },
    ]
    requests = []
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, requests),
    )

    result = await vision_service.analyze_image("data:image/jpeg;base64,abc")

    assert result.visible_text == ["已发货", "中通快递"]
    assert result.confidence == 0.95
    assert [request["json"]["model"] for request in requests] == [
        "qwen3.7-plus",
        "qwen3.5-ocr",
    ]


@pytest.mark.asyncio
async def test_analyze_image_rejects_low_confidence(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    responses = [
        {
            "image_type": "other",
            "summary": "图片模糊",
            "visible_text": [],
            "visible_facts": [],
            "possible_need": "",
            "needs_ocr": False,
            "needs_clarification": True,
            "risk_flags": [],
            "confidence": 0.2,
        }
    ]
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, []),
    )

    with pytest.raises(vision_service.VisionRecognitionError):
        await vision_service.analyze_image("https://cdn.example.com/blurry.jpg")
