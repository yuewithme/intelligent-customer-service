import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.unpurchased_sop import (
    UnpurchasedSopStepRequest,
    UnpurchasedSopUpdateRequest,
)
from app.services import unpurchased_sop_service
from app.services.unpurchased_sop_service import (
    create_unpurchased_sop_step,
    get_unpurchased_sop,
    list_unpurchased_sop_deliveries,
    list_unpurchased_sop_contacts,
    process_due_unpurchased_sop_deliveries,
    sync_eyun_contacts,
    sync_sop_delivery_status,
    update_unpurchased_sop,
)
from app.services.user_profile_service import get_profile_bundle, patch_user_profile


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


def _settings(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'sop.db').as_posix()}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}")
    monkeypatch.setenv("EYUN_WID", "wid-1")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_contact_sync_builds_baseline_then_enrolls_only_new_contacts(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)

    first = await sync_eyun_contacts(friend_ids=["old-1", "old-2"])
    second = await sync_eyun_contacts(friend_ids=["old-1", "old-2", "new-1"])
    contacts = list_unpurchased_sop_contacts(page_size=10)["items"]
    by_id = {item["wc_id"]: item for item in contacts}

    assert first["baseline"] == 2
    assert second["new"] == 1
    assert by_id["old-1"]["friend_added_on"] is None
    assert by_id["old-1"]["enrollment_status"] is None
    assert by_id["new-1"]["friend_added_on"] is not None
    assert by_id["new-1"]["enrollment_status"] == "active"
    assert not (tmp_path / "chat.db").exists()


def test_sop_steps_support_text_image_and_video(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)

    text = create_unpurchased_sop_step(
        UnpurchasedSopStepRequest(
            day_offset=0,
            send_time="10:00",
            message_type="text",
            content="欢迎添加",
        )
    )
    image = create_unpurchased_sop_step(
        UnpurchasedSopStepRequest(
            day_offset=1,
            send_time="10:00",
            message_type="image",
            content="https://example.com/image.jpg",
        )
    )
    video = create_unpurchased_sop_step(
        UnpurchasedSopStepRequest(
            day_offset=2,
            send_time="10:00",
            message_type="video",
            content="https://example.com/video.mp4",
            preview_url="https://example.com/video-cover.jpg",
        )
    )

    assert [text["message_type"], image["message_type"], video["message_type"]] == [
        "text",
        "image",
        "video",
    ]
    assert get_unpurchased_sop()["steps"][2]["preview_url"].endswith("cover.jpg")


def test_contact_polling_controls_are_saved_in_sop(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)

    updated = update_unpurchased_sop(
        UnpurchasedSopUpdateRequest(
            name="未购SOP",
            enabled=False,
            dry_run=True,
            send_window_start="09:00",
            send_window_end="20:00",
            contact_poll_interval_minutes=60,
            contact_missing_threshold=5,
        )
    )

    assert updated["contact_poll_interval_minutes"] == 60
    assert updated["contact_missing_threshold"] == 5
    assert get_unpurchased_sop()["sop"]["contact_poll_interval_minutes"] == 60


def test_existing_sop_table_gets_polling_columns_automatically(monkeypatch, tmp_path):
    database_path = tmp_path / "sop.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE unpurchased_sops (
                id INTEGER PRIMARY KEY,
                name VARCHAR(256) NOT NULL,
                enabled BOOLEAN NOT NULL,
                dry_run BOOLEAN NOT NULL,
                send_window_start VARCHAR(5) NOT NULL,
                send_window_end VARCHAR(5) NOT NULL,
                timezone VARCHAR(64) NOT NULL,
                baseline_initialized_at DATETIME,
                last_contact_sync_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO unpurchased_sops (
                id, name, enabled, dry_run, send_window_start, send_window_end,
                timezone, created_at, updated_at
            ) VALUES (1, '未购SOP', 0, 1, '09:00', '20:00',
                      'Asia/Shanghai', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """
        )

    _settings(monkeypatch, tmp_path)
    sop = get_unpurchased_sop()["sop"]

    assert sop["contact_poll_interval_minutes"] == 120
    assert sop["contact_missing_threshold"] == 3


def test_sop_media_upload_uses_persistent_upload_directory_and_public_url(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    from app.main import app

    response = TestClient(app).post(
        "/api/v1/admin/unpurchased-sop/media/upload",
        files={"file": ("poster.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
        headers={"Origin": "https://admin.example.com"},
    )

    assert response.status_code == 200
    media = response.json()["data"]
    assert media["type"] == "image"
    assert media["url"].startswith("https://admin.example.com/static/sop-media/")
    assert any((tmp_path / "uploads" / "sop-media").iterdir())


@pytest.mark.asyncio
async def test_purchase_tag_exits_before_sop_message_is_queued(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    await sync_eyun_contacts(friend_ids=["baseline"])
    await sync_eyun_contacts(friend_ids=["baseline", "new-contact"])
    update_unpurchased_sop(
        UnpurchasedSopUpdateRequest(
            name="未购SOP",
            enabled=True,
            dry_run=False,
            send_window_start="00:00",
            send_window_end="23:59",
        )
    )
    create_unpurchased_sop_step(
        UnpurchasedSopStepRequest(
            day_offset=0,
            send_time="00:00",
            message_type="text",
            content="未购提醒",
        )
    )
    await patch_user_profile("new-contact", {"customer_tags": ["微信已购"]})

    queued = await process_due_unpurchased_sop_deliveries()
    contact = next(
        item
        for item in list_unpurchased_sop_contacts(page_size=10)["items"]
        if item["wc_id"] == "new-contact"
    )

    assert queued == 0
    assert contact["enrollment_status"] == "exited"
    assert contact["exit_reason"] == "purchase_tag_added"


@pytest.mark.asyncio
async def test_due_video_uses_existing_outbound_queue_without_conversation(monkeypatch, tmp_path):
    _settings(monkeypatch, tmp_path)
    await sync_eyun_contacts(friend_ids=["baseline"])
    await sync_eyun_contacts(friend_ids=["baseline", "new-contact"])
    update_unpurchased_sop(
        UnpurchasedSopUpdateRequest(
            name="未购SOP",
            enabled=True,
            dry_run=False,
            send_window_start="00:00",
            send_window_end="23:59",
        )
    )
    create_unpurchased_sop_step(
        UnpurchasedSopStepRequest(
            day_offset=0,
            send_time="00:00",
            message_type="video",
            content="https://example.com/video.mp4",
            preview_url="https://example.com/cover.jpg",
        )
    )
    captured = {}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        return {"id": 99, "status": "queued"}

    monkeypatch.setattr(unpurchased_sop_service, "enqueue_eyun_outbound", fake_enqueue)

    queued = await process_due_unpurchased_sop_deliveries()

    assert queued == 1
    assert captured["wc_id"] == "new-contact"
    assert captured["message_type"] == "video"
    assert '"thumb_path": "https://example.com/cover.jpg"' in captured["content"]
    assert captured["source_batch_key"].startswith("unpurchased_sop:")

    delivery = list_unpurchased_sop_deliveries()["items"][0]
    sync_sop_delivery_status(delivery["id"], "sent")
    memories = (await get_profile_bundle("new-contact"))["recent_memories"]
    assert memories[-1]["content"].startswith("[未购SOP发送视频]")


@pytest.mark.asyncio
async def test_eyun_video_posts_public_video_and_cover_urls(monkeypatch):
    from app.services import eyun_callback_service

    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example.com")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "test-token")
    get_settings.cache_clear()
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": "1000", "data": {"newMsgId": 123}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return FakeResponse()

    monkeypatch.setattr(eyun_callback_service.httpx, "AsyncClient", FakeClient)

    await eyun_callback_service.send_eyun_video(
        w_id="wid-1",
        wc_id="customer-1",
        path="https://admin.example.com/static/sop-media/video.mp4",
        thumb_path="https://admin.example.com/static/sop-media/cover.jpg",
    )

    assert captured["url"] == "https://eyun.example.com/sendVideo"
    assert captured["json"] == {
        "wId": "wid-1",
        "wcId": "customer-1",
        "path": "https://admin.example.com/static/sop-media/video.mp4",
        "thumbPath": "https://admin.example.com/static/sop-media/cover.jpg",
    }


def test_video_round_trips_through_outbound_queue_encoding():
    from app.services.message_risk_control_service import (
        _decode_outbound_content,
        _encode_outbound_content,
    )

    payload = '{"path":"https://example.com/video.mp4","thumb_path":"https://example.com/cover.jpg"}'
    stored = _encode_outbound_content("video", payload)

    message_type, content = _decode_outbound_content(stored)
    assert message_type == "video"
    assert json.loads(content)["thumb_path"].endswith("cover.jpg")
