import httpx
import pytest

from app.core.config import get_settings
from app.integrations.youzan.services import youzan_token_service as token_service


@pytest.mark.asyncio
async def test_silent_token_exchange_uses_credentials_and_tracks_expiry(monkeypatch):
    monkeypatch.setenv("YOUZAN_ACCESS_TOKEN", "")
    monkeypatch.setenv("YOUZAN_CLIENT_ID", "client-id")
    monkeypatch.setenv("YOUZAN_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("YOUZAN_KDT_ID", "9001")
    get_settings.cache_clear()
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "access_token": "fresh-token",
                    "expires": 604800,
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        manager = token_service.YouzanTokenManager(http_client=client)
        token = await manager.get_access_token()

    assert token == "fresh-token"
    assert requests[0].url.path == "/auth/token"
    payload = __import__("json").loads(requests[0].content)
    assert payload == {
        "authorize_type": "silent",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "grant_id": "9001",
    }
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_token_outage_alert_is_deduplicated_and_recovery_is_sent(monkeypatch):
    alerts = []

    async def fake_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(token_service, "send_feishu_webhook_alert", fake_alert)
    token_service.reset_youzan_token_state_for_tests()

    error = RuntimeError("Token 不存在")
    await token_service.notify_youzan_failure("订单同步", error)
    await token_service.notify_youzan_failure("订单同步", error)
    await token_service.notify_youzan_recovery("订单同步恢复")

    assert len(alerts) == 2
    assert "订单能力不可用" in alerts[0]
    assert "已恢复" in alerts[1]
