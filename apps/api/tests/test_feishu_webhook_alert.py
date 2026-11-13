import httpx
import pytest

from app.core.config import get_settings
from app.integrations.feishu.services import webhook_alert_service as service


@pytest.mark.parametrize("recovered", [False, True])
def test_system_alert_uses_one_concise_format(recovered):
    content = service.render_system_alert(
        "微信账号状态变更",
        impact="该账号消息收发受影响。",
        status="已确认。",
        action="检查登录状态。",
        details=("微信账号：wxid_bot",),
        recovered=recovered,
    )

    assert content.startswith("【小兰恢复通知】" if recovered else "【小兰异常通知】")
    assert "影响：该账号消息收发受影响。" in content
    assert "当前状态：已确认。" in content
    assert "微信账号：wxid_bot" in content
    assert "处理建议：检查登录状态。" in content
    assert "时间：" in content


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
