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
    now = [0.0]

    async def fake_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(token_service, "send_feishu_webhook_alert", fake_alert)
    monkeypatch.setattr(token_service, "monotonic", lambda: now[0])
    token_service.reset_youzan_token_state_for_tests()

    error = RuntimeError("access_token=sensitive-token")
    await token_service.notify_youzan_failure("订单同步", error)
    now[0] = 30
    await token_service.notify_youzan_failure("订单同步", error)
    assert alerts == []
    now[0] = 60
    await token_service.notify_youzan_failure("订单同步", error)
    await token_service.notify_youzan_failure("订单同步", error)
    assert len(alerts) == 1
    await token_service.notify_youzan_recovery("订单同步")
    await token_service.notify_youzan_recovery("订单同步")

    assert len(alerts) == 2
    assert "【小兰异常通知】" in alerts[0]
    assert "订单同步连续失败3次" in alerts[0]
    assert "最新订单可能尚未更新" in alerts[0]
    assert "sensitive-token" not in alerts[0]
    assert "订单能力不可用" not in alerts[0]
    assert "已恢复" in alerts[1]


@pytest.mark.asyncio
async def test_transient_youzan_failure_and_success_remain_silent(monkeypatch):
    alerts = []
    now = [0.0]

    async def fake_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(token_service, "send_feishu_webhook_alert", fake_alert)
    monkeypatch.setattr(token_service, "monotonic", lambda: now[0])
    token_service.reset_youzan_token_state_for_tests()

    for _ in range(2):
        await token_service.notify_youzan_failure("订单同步", RuntimeError("temporary"))
        now[0] += 60
        await token_service.notify_youzan_failure("订单同步", RuntimeError("temporary"))
        await token_service.notify_youzan_recovery("订单同步")

    assert alerts == []
    assert token_service._failures_by_component == {}


@pytest.mark.asyncio
async def test_short_burst_of_youzan_failures_requires_duration_confirmation(monkeypatch):
    alerts = []
    now = [0.0]

    async def fake_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(token_service, "send_feishu_webhook_alert", fake_alert)
    monkeypatch.setattr(token_service, "monotonic", lambda: now[0])
    token_service.reset_youzan_token_state_for_tests()

    for _ in range(4):
        await token_service.notify_youzan_failure("Token自动换取", RuntimeError("temporary"))
    assert alerts == []
    now[0] = 60
    await token_service.notify_youzan_failure("Token自动换取", RuntimeError("persistent"))
    assert len(alerts) == 1


@pytest.mark.asyncio
async def test_youzan_failures_merge_without_partial_or_unrelated_recovery(monkeypatch):
    alerts = []
    now = [0.0]

    async def fake_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(token_service, "send_feishu_webhook_alert", fake_alert)
    monkeypatch.setattr(token_service, "monotonic", lambda: now[0])
    token_service.reset_youzan_token_state_for_tests()

    for timestamp in (0, 30, 60):
        now[0] = timestamp
        await token_service.notify_youzan_failure("Token自动换取", RuntimeError("token failed"))
        await token_service.notify_youzan_failure("订单同步", RuntimeError("sync failed"))

    assert len(alerts) == 1
    assert "有赞授权获取" in alerts[0] and "订单同步" in alerts[0]
    await token_service.notify_youzan_recovery("Token自动换取")
    assert len(alerts) == 1
    await token_service.notify_youzan_failure("订单同步", RuntimeError("still failed"))
    assert len(alerts) == 1
    await token_service.notify_youzan_recovery("订单同步")
    assert len(alerts) == 2


@pytest.mark.asyncio
async def test_youzan_components_do_not_share_failure_counts(monkeypatch):
    alerts = []
    now = [0.0]

    async def fake_alert(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(token_service, "send_feishu_webhook_alert", fake_alert)
    monkeypatch.setattr(token_service, "monotonic", lambda: now[0])
    token_service.reset_youzan_token_state_for_tests()

    await token_service.notify_youzan_failure("Token自动换取", RuntimeError("failed"))
    now[0] = 60
    await token_service.notify_youzan_failure("订单同步", RuntimeError("failed"))
    await token_service.notify_youzan_failure("订单同步", RuntimeError("failed"))
    await token_service.notify_youzan_recovery("Token自动换取")
    assert alerts == []


@pytest.mark.asyncio
async def test_youzan_alert_and_recovery_retry_when_webhook_fails(monkeypatch):
    attempts = []
    now = [0.0]
    results = iter((False, True, False, True))

    async def fake_alert(content):
        attempts.append(content)
        return next(results)

    monkeypatch.setattr(token_service, "send_feishu_webhook_alert", fake_alert)
    monkeypatch.setattr(token_service, "monotonic", lambda: now[0])
    token_service.reset_youzan_token_state_for_tests()

    await token_service.notify_youzan_failure("订单同步", RuntimeError("failed"))
    now[0] = 60
    await token_service.notify_youzan_failure("订单同步", RuntimeError("failed"))
    await token_service.notify_youzan_failure("订单同步", RuntimeError("failed"))
    assert not token_service._outage_alerted
    await token_service.notify_youzan_failure("订单同步", RuntimeError("failed"))
    assert token_service._outage_alerted
    await token_service.notify_youzan_recovery("订单同步")
    assert token_service._outage_alerted
    await token_service.notify_youzan_recovery("订单同步")
    assert not token_service._outage_alerted
    assert len(attempts) == 4
