import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.unpurchased_sop import (
    UnpurchasedSopStepRequest,
    UnpurchasedSopUpdateRequest,
)
from app.services.unpurchased_sop_service import (
    SERVICE_SOP_ID,
    create_unpurchased_sop_step,
    get_unpurchased_sop,
    list_unpurchased_sop_contacts,
    process_due_unpurchased_sop_deliveries,
    sync_eyun_contacts,
    sync_service_sop_enrollments,
    update_unpurchased_sop,
    validate_sop_delivery_before_send,
)
from app.services import unpurchased_sop_service
from app.services.user_profile_service import patch_user_profile


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'sop.db').as_posix()}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}")
    monkeypatch.setenv("EYUN_WID", "wid-1")
    monkeypatch.setenv("EYUN_BASE_URL", "")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_only_purchased_tags_enter_service_sop(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    await sync_eyun_contacts(friend_ids=["baseline"])
    await sync_eyun_contacts(
        friend_ids=["baseline", "wechat-buyer", "douyin-buyer", "not-buyer"]
    )
    await patch_user_profile("wechat-buyer", {"customer_tags": ["微信已购"]})
    await patch_user_profile("douyin-buyer", {"customer_tags": ["抖音已购"]})

    result = sync_service_sop_enrollments()
    contacts = list_unpurchased_sop_contacts(
        page_size=10, sop_id=SERVICE_SOP_ID
    )["items"]
    by_id = {item["wc_id"]: item for item in contacts}

    assert result == {"enrolled": 2, "exited": 0}
    assert by_id["wechat-buyer"]["enrollment_status"] == "active"
    assert by_id["douyin-buyer"]["enrollment_status"] == "active"
    assert by_id["not-buyer"]["enrollment_status"] is None
    assert by_id["baseline"]["enrollment_status"] is None


@pytest.mark.asyncio
async def test_service_sop_executes_independently_and_exits_when_tag_removed(
    monkeypatch, tmp_path
):
    _settings(monkeypatch, tmp_path)
    await sync_eyun_contacts(friend_ids=["baseline"])
    await sync_eyun_contacts(friend_ids=["baseline", "buyer", "not-buyer"])
    await patch_user_profile("buyer", {"customer_tags": ["微信已购"]})
    sync_service_sop_enrollments()
    update_unpurchased_sop(
        UnpurchasedSopUpdateRequest(
            name="服务SOP",
            enabled=True,
            dry_run=False,
            send_window_start="00:00",
            send_window_end="23:59",
        ),
        sop_id=SERVICE_SOP_ID,
    )
    captured = {}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"id": 501, "status": "queued"}

    monkeypatch.setattr(
        unpurchased_sop_service, "enqueue_wechat_outbound", fake_enqueue
    )
    create_unpurchased_sop_step(
        UnpurchasedSopStepRequest(
            day_offset=0,
            send_time="00:00",
            message_type="text",
            content="已购服务提醒",
        ),
        sop_id=SERVICE_SOP_ID,
    )

    assert await process_due_unpurchased_sop_deliveries(
        sop_id=SERVICE_SOP_ID
    ) == 1
    assert captured["source_batch_key"].startswith("service_sop:")
    assert validate_sop_delivery_before_send(captured["source_batch_key"])
    assert get_unpurchased_sop(sop_id=SERVICE_SOP_ID)["stats"]["active_enrollments"] == 1

    await patch_user_profile("buyer", {"customer_tags": []})
    assert sync_service_sop_enrollments() == {"enrolled": 0, "exited": 1}
    buyer = next(
        item
        for item in list_unpurchased_sop_contacts(
            page_size=10, sop_id=SERVICE_SOP_ID
        )["items"]
        if item["wc_id"] == "buyer"
    )
    assert buyer["enrollment_status"] == "exited"
    assert buyer["exit_reason"] == "purchase_tag_removed"


def test_service_sop_admin_api_uses_separate_default_config(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    from app.main import app

    response = TestClient(app).get("/api/v1/admin/service-sop")

    assert response.status_code == 200
    assert response.json()["data"]["sop"]["id"] == SERVICE_SOP_ID
    assert response.json()["data"]["sop"]["name"] == "服务SOP"
