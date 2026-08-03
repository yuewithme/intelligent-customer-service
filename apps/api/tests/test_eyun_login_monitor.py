import pytest

from app.core.config import get_settings
from app.integrations.eyun.services import eyun_login_monitor_service as monitor


def _configure(monkeypatch):
    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "Bearer test")
    monkeypatch.setenv("EYUN_WID", "stale-wid")
    monkeypatch.setenv("EYUN_WC_ID", "wxid_bot")
    monkeypatch.setenv(
        "FEISHU_HANDOFF_WEBHOOK_URL",
        "https://open.feishu.cn/open-apis/bot/v2/hook/test-webhook",
    )
    get_settings.cache_clear()
    monitor._reset_monitor_state()


@pytest.mark.asyncio
async def test_monitor_alerts_once_for_offline_and_once_for_recovery(monkeypatch):
    _configure(monkeypatch)
    online_rows = [{"wcId": "wxid_bot", "wId": "current-wid"}]
    alerts: list[str] = []

    async def fake_post(url, *, authorization, payload):
        assert authorization == "Bearer test"
        if url.endswith("/queryLoginWx"):
            return {"code": "1000", "data": list(online_rows)}
        if url.endswith("/offlineReason"):
            assert payload == {"wcId": "wxid_bot"}
            return {
                "code": "1000",
                "data": [{"wcId": "wxid_bot", "reason": "session expired"}],
            }
        raise AssertionError(f"unexpected URL: {url}")

    async def fake_send(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(monitor, "_post_eyun", fake_post)
    monkeypatch.setattr(monitor, "_send_feishu_alert", fake_send)

    assert await monitor.poll_eyun_login_status() is True
    assert alerts == []
    assert get_settings().eyun_wid == "current-wid"

    online_rows.clear()
    assert await monitor.poll_eyun_login_status() is False
    assert alerts == []
    assert await monitor.poll_eyun_login_status() is False
    assert len(alerts) == 1
    assert "已离线" in alerts[0]
    assert "session expired" in alerts[0]

    online_rows.append({"wcId": "wxid_bot", "wId": "recovered-wid"})
    assert await monitor.poll_eyun_login_status() is True
    assert len(alerts) == 2
    assert "已恢复在线" in alerts[1]


@pytest.mark.asyncio
async def test_monitor_retries_alert_after_webhook_failure(monkeypatch):
    _configure(monkeypatch)
    attempts = 0

    async def fake_post(url, *, authorization, payload):
        del authorization, payload
        if url.endswith("/queryLoginWx"):
            return {"code": "1000", "data": []}
        return {
            "code": "1000",
            "data": [{"wcId": "wxid_bot", "reason": "session expired"}],
        }

    async def fake_send(content):
        nonlocal attempts
        assert "已离线" in content
        attempts += 1
        return attempts > 1

    monkeypatch.setattr(monitor, "_post_eyun", fake_post)
    monkeypatch.setattr(monitor, "_send_feishu_alert", fake_send)

    assert await monitor.poll_eyun_login_status() is False
    assert await monitor.poll_eyun_login_status() is False
    assert attempts == 1
    assert await monitor.poll_eyun_login_status() is False
    assert attempts == 2


@pytest.mark.asyncio
async def test_duplicate_offline_callbacks_require_periodic_confirmation(monkeypatch):
    _configure(monkeypatch)
    alerts: list[str] = []

    async def fake_post(url, *, authorization, payload):
        del authorization, payload
        if url.endswith("/queryLoginWx"):
            return {"code": "1000", "data": []}
        if url.endswith("/offlineReason"):
            return {
                "code": "1000",
                "data": [{"wcId": "wxid_bot", "reason": "network disconnected"}],
            }
        raise AssertionError(f"unexpected URL: {url}")

    async def fake_send(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(monitor, "_post_eyun", fake_post)
    monkeypatch.setattr(monitor, "_send_feishu_alert", fake_send)

    payload = {
        "messageType": "30000",
        "data": {"wId": "wid-callback", "reason": "network disconnected"},
    }
    await monitor.handle_eyun_offline_notification(payload)
    await monitor.handle_eyun_offline_notification(payload)

    assert alerts == []
    assert await monitor.poll_eyun_login_status() is False
    assert alerts == []
    assert await monitor.poll_eyun_login_status() is False
    assert len(alerts) == 1
    assert "network disconnected" in alerts[0]


@pytest.mark.asyncio
async def test_offline_reason_null_keeps_account_online(monkeypatch):
    _configure(monkeypatch)
    alerts: list[str] = []

    async def fake_post(url, *, authorization, payload):
        del authorization, payload
        if url.endswith("/queryLoginWx"):
            return {"code": "1000", "data": []}
        if url.endswith("/offlineReason"):
            return {
                "code": "1000",
                "data": [{"wcId": "wxid_bot", "reason": None}],
            }
        raise AssertionError(f"unexpected URL: {url}")

    async def fake_send(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(monitor, "_post_eyun", fake_post)
    monkeypatch.setattr(monitor, "_send_feishu_alert", fake_send)

    assert await monitor.poll_eyun_login_status() is True
    assert await monitor.poll_eyun_login_status() is True
    assert alerts == []
