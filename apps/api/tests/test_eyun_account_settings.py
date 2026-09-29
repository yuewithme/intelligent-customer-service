import pytest

from app.core.config import get_settings
from app.integrations.eyun.services.eyun_account_settings_service import (
    get_eyun_account_settings_revision,
    get_eyun_account_settings,
    load_eyun_account_settings,
    save_eyun_account_settings,
)
from app.integrations.eyun.services import eyun_login_monitor_service as monitor


@pytest.fixture
def settings_context(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'settings.db'}")
    monkeypatch.setenv("EYUN_WID", "env-wid")
    monkeypatch.setenv("EYUN_WC_ID", "wxid_env")
    get_settings.cache_clear()
    monitor._reset_monitor_state()
    yield
    get_settings.cache_clear()
    monitor._reset_monitor_state()


def test_save_updates_runtime_and_survives_restart(settings_context):
    load_eyun_account_settings()
    assert get_eyun_account_settings() == {
        "w_id": "env-wid", "wc_id": "wxid_env"
    }
    saved = save_eyun_account_settings(w_id="new-wid", wc_id="wxid_new")
    expected = {"w_id": "new-wid", "wc_id": "wxid_new"}
    assert saved == expected
    assert get_eyun_account_settings() == expected

    get_settings.cache_clear()
    load_eyun_account_settings()
    assert get_eyun_account_settings() == expected


@pytest.mark.asyncio
async def test_monitor_refresh_is_persisted(settings_context, monkeypatch):
    save_eyun_account_settings(w_id="old-wid", wc_id="wxid_account")
    settings = get_settings()
    settings.eyun_base_url = "https://eyun.example.com"
    settings.eyun_authorization = "test"

    async def fake_post(*args, **kwargs):
        return {"code": "1000", "data": [{"wcId": "wxid_account", "wId": "refreshed-wid"}]}

    monkeypatch.setattr(monitor, "_post_eyun", fake_post)
    assert await monitor.poll_eyun_login_status() is True
    assert get_eyun_account_settings()["w_id"] == "refreshed-wid"
    get_settings.cache_clear()
    load_eyun_account_settings()
    assert get_settings().eyun_wid == "refreshed-wid"


@pytest.mark.asyncio
async def test_inflight_monitor_cannot_overwrite_manual_save(settings_context, monkeypatch):
    get_settings().eyun_base_url = "https://eyun.example.com"
    get_settings().eyun_authorization = "test"

    async def fake_post(*args, **kwargs):
        save_eyun_account_settings(w_id="manual-wid", wc_id="wxid_manual")
        return {"code": "1000", "data": [{"wcId": "wxid_env", "wId": "stale-result"}]}

    monkeypatch.setattr(monitor, "_post_eyun", fake_post)
    assert await monitor.poll_eyun_login_status() is None
    assert get_eyun_account_settings() == {"w_id": "manual-wid", "wc_id": "wxid_manual"}


@pytest.mark.asyncio
async def test_account_switch_resets_state_and_rejects_pending_alert(settings_context, monkeypatch):
    monitor._status_by_wc_id["wxid_env"] = True
    monitor._offline_observations_by_wc_id["wxid_env"] = 1
    old_revision = get_eyun_account_settings_revision()
    alerts: list[str] = []

    async def fake_send(content):
        alerts.append(content)
        return True

    monkeypatch.setattr(monitor, "_send_feishu_alert", fake_send)
    save_eyun_account_settings(w_id="new-wid", wc_id="wxid_new")

    assert monitor._status_by_wc_id == {}
    assert monitor._offline_observations_by_wc_id == {}
    await monitor._apply_status(
        wc_id="wxid_env",
        w_id="env-wid",
        online=False,
        reason="old account offline",
        require_offline_confirmation=True,
        configuration_revision=old_revision,
    )
    assert alerts == []
