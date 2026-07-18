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


def _order_response(*, store_name="萧岚苑", needs_ocr=False):
    return {
        "category": "order",
        "summary": "订单截图",
        "order": {
            "is_order_screenshot": True,
            "store_name": store_name,
            "platform": "抖音电商",
            "page_type": "交易完成",
            "product": "寒兰裸苗",
            "amount": "¥39.90",
            "order_number": "6950923243201500463",
            "status": "交易完成",
            "evidence": [store_name],
        },
        "orchid_health": None,
        "needs_ocr": needs_ocr,
        "needs_clarification": False,
        "confidence": 0.95,
    }


@pytest.mark.asyncio
async def test_verified_store_order_uses_dashscope_and_returns_purchase_tag(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    monkeypatch.setenv("VISION_OCR_ENABLED", "false")
    responses = [_order_response(store_name="萧兰苑")]
    requests = []
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, requests),
    )

    result = await vision_service.analyze_image("https://cdn.example.com/order.jpg")

    assert result.order.store_name == "萧兰苑"
    assert vision_service.purchase_tag_for_analysis(result) == "抖音已购"
    assert requests[0]["url"].endswith("/chat/completions")
    assert requests[0]["headers"]["Authorization"] == "Bearer test-key"
    prompt = requests[0]["json"]["messages"][0]["content"][1]["text"]
    assert "只允许处理以下两个场景" in prompt
    assert "兰花病虫害或健康问题" in prompt


@pytest.mark.asyncio
async def test_order_ocr_rechecks_store_name(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    responses = [
        _order_response(store_name="", needs_ocr=True),
        _order_response(store_name="萧岚苑"),
    ]
    requests = []
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, requests),
    )

    result = await vision_service.analyze_image("data:image/jpeg;base64,abc")

    assert result.order.store_name == "萧岚苑"
    assert [request["json"]["model"] for request in requests] == [
        "qwen3.7-plus",
        "qwen3.5-ocr",
    ]


@pytest.mark.asyncio
async def test_orchid_health_result_can_request_clarification(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    responses = [
        {
            "category": "orchid_health",
            "summary": "兰花芦头发黑",
            "order": None,
            "orchid_health": {
                "visible_symptoms": ["外层叶鞘发黑"],
                "primary_diagnosis": "疑似茎腐或老叶鞘干枯",
                "alternative_diagnosis": "正常老化",
                "evidence": ["外层组织颜色深"],
                "uncertainties": ["无法从照片判断软硬和气味"],
                "severity": "待确认",
                "isolation_needed": True,
                "safe_actions": ["先隔离观察，不要立即切除芦头"],
                "clarifying_questions": ["发黑处是干硬还是软烂？"],
            },
            "needs_ocr": False,
            "needs_clarification": True,
            "confidence": 0.82,
        }
    ]
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, []),
    )

    result = await vision_service.analyze_image("https://cdn.example.com/orchid.jpg")

    assert result.category == "orchid_health"
    assert result.needs_clarification is True
    assert "干硬还是软烂" in vision_service.format_analysis_for_chat(result)


@pytest.mark.asyncio
async def test_order_from_other_store_is_not_supported(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    responses = [_order_response(store_name="其他兰花店")]
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, []),
    )

    with pytest.raises(vision_service.VisionRecognitionError):
        await vision_service.analyze_image("https://cdn.example.com/other-order.jpg")


@pytest.mark.asyncio
async def test_unrelated_image_is_not_described(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    responses = [
        {
            "category": "unsupported",
            "summary": "",
            "order": None,
            "orchid_health": None,
            "needs_ocr": False,
            "needs_clarification": False,
            "confidence": 0.99,
        }
    ]
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, []),
    )

    with pytest.raises(vision_service.VisionRecognitionError):
        await vision_service.analyze_image("https://cdn.example.com/cat.jpg")
