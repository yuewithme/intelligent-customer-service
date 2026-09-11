import httpx
import pytest

from app.core.config import get_settings
from app.integrations.feishu.services import webhook_alert_service as service


@pytest.mark.asyncio
@pytest.mark.parametrize("webhook", ["https://example.com/new-bot", ""])
async def test_all_alerts_use_unified_webhook_even_when_legacy_value_exists(monkeypatch, webhook):
    monkeypatch.setenv("FEISHU_HANDOFF_WEBHOOK_URL", webhook)
    monkeypatch.setenv("FEISHU_ALERT_WEBHOOK_URL", "https://example.com/old-bot")
    get_settings.cache_clear()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0})

    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        service.httpx,
        "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    try:
        assert await service.send_feishu_webhook_alert("通知测试") is bool(webhook)
        assert [str(request.url) for request in requests] == ([webhook] if webhook else [])
    finally:
        get_settings.cache_clear()
