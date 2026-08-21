import json

import pytest

from app.core.config import get_settings


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
    monkeypatch.setenv("PURCHASE_TAGS_ENABLED", "true")
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
    assert "兰花病虫害或健康问题" in prompt
    assert "正常兰花、开花状态或品类询问" in prompt
    assert "软证据" in prompt


def test_purchase_tag_is_disabled_by_default():
    from app.services import vision_service

    analysis = vision_service.VisionAnalysis.model_validate(_order_response())

    assert vision_service.purchase_tag_for_analysis(analysis) is None
    assert "已将客户标记为抖音已购" not in vision_service.format_analysis_for_chat(analysis)


@pytest.mark.parametrize(
    ("platform", "status"),
    (
        ("淘宝/天猫", "交易完成"),
        ("抖音电商", "待付款"),
        ("抖音电商", "交易关闭"),
        ("抖音电商", "退款成功"),
    ),
)
def test_purchase_tag_requires_douyin_and_paid_status(monkeypatch, platform, status):
    from app.services import vision_service

    monkeypatch.setenv("PURCHASE_TAGS_ENABLED", "true")
    payload = _order_response()
    payload["order"]["platform"] = platform
    payload["order"]["status"] = status
    payload["order"]["page_type"] = status
    analysis = vision_service.VisionAnalysis.model_validate(payload)

    assert vision_service.purchase_tag_for_analysis(analysis) is None


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
async def test_healthy_orchid_identification_is_soft_evidence(monkeypatch):
    from app.services import vision_service

    monkeypatch.setenv("VISION_ENABLED", "true")
    monkeypatch.setenv("VISION_API_KEY", "test-key")
    responses = [
        {
            "category": "orchid_general",
            "summary": "一盆正在开花的兰花",
            "order": None,
            "orchid_health": None,
            "orchid_general": {
                "visible_features": ["叶片细长", "花梗从株基抽出"],
                "likely_type": "看着更像建兰",
                "identification_basis": ["叶姿和开花形态接近建兰"],
                "uncertainties": ["远景无法确认具体品种"],
                "flowering_state": "当前正在开花",
                "care_observations": ["整株长势尚可"],
            },
            "needs_ocr": False,
            "needs_clarification": True,
            "confidence": 0.48,
        }
    ]
    monkeypatch.setattr(
        vision_service.httpx,
        "AsyncClient",
        lambda **kwargs: _FakeClient(responses, []),
    )

    result = await vision_service.analyze_image("https://cdn.example.com/bloom.jpg")
    formatted = vision_service.format_analysis_for_chat(result)

    assert result.category == "orchid_general"
    assert "品类判断：看着更像建兰" in formatted
    assert "以上是软证据" in formatted
    assert "不向客户提及图片识别" in formatted


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

    with pytest.raises(vision_service.UnsupportedStoreOrderError) as exc_info:
        await vision_service.analyze_image("https://cdn.example.com/other-order.jpg")
    assert exc_info.value.store_name == "其他兰花店"


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
