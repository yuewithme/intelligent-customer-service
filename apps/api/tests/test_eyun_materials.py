import asyncio
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def material_db(monkeypatch, tmp_path):
    from app.services import eyun_material_service, message_risk_control_service

    db_path = tmp_path / "materials.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("EYUN_REPLY_JITTER_MIN_SECONDS", "0")
    monkeypatch.setenv("EYUN_REPLY_JITTER_MAX_SECONDS", "0")
    get_settings.cache_clear()
    eyun_material_service._sessionmakers.clear()
    eyun_material_service._material_locks.clear()
    eyun_material_service._source_locks.clear()
    eyun_material_service._source_cache.clear()
    message_risk_control_service._sessionmakers.clear()
    message_risk_control_service._initialized_urls.clear()
    yield
    get_settings.cache_clear()
    eyun_material_service._sessionmakers.clear()
    eyun_material_service._material_locks.clear()
    eyun_material_service._source_locks.clear()
    eyun_material_service._source_cache.clear()
    message_risk_control_service._sessionmakers.clear()
    message_risk_control_service._initialized_urls.clear()


def test_material_capture_deduplicates_xml_and_exposes_admin_api():
    from app.integrations.eyun.services.eyun_material_service import capture_eyun_material

    xml = '<msg><img cdnmidimgurl="same-material" /></msg>'
    first = capture_eyun_material(
        media_type="image", raw_xml=xml, preview_url="https://cdn.example/a.jpg"
    )
    second = capture_eyun_material(
        media_type="image", raw_xml=xml, name="七月活动图"
    )

    assert first["id"] == second["id"]
    response = TestClient(app).get("/api/v1/admin/wechat-materials")
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1
    assert response.json()["data"]["items"][0]["name"] == "七月活动图"


def test_material_group_callback_auto_captures_raw_xml(monkeypatch):
    from app.integrations.eyun.services.eyun_material_service import (
        capture_material_group_message,
        list_materials,
    )

    monkeypatch.setenv("EYUN_MATERIAL_GROUP_WC_ID", "material-group@chatroom")
    get_settings.cache_clear()
    result = capture_material_group_message(
        {
            "messageType": "60002",
            "data": {
                "wId": "wid-material",
                "fromGroup": "material-group@chatroom",
                "newMsgId": 1001,
                "content": '<msg><img cdnmidimgurl="asset" /></msg>',
            },
        },
        {
            "raw_content": '<msg><img cdnmidimgurl="asset" /></msg>',
            "media": {"type": "image", "url": "https://cdn.example/asset.jpg"},
        },
    )

    assert result is not None
    assert list_materials()["total"] == 1


@pytest.mark.asyncio
async def test_image_url_is_materialized_once_for_concurrent_sends(monkeypatch):
    from app.services import eyun_material_service as materials

    monkeypatch.setenv("EYUN_BASE_URL", "https://eyun.example")
    monkeypatch.setenv("EYUN_AUTHORIZATION", "token")
    get_settings.cache_clear()
    upload_count = 0
    fetch_count = 0

    async def fake_fetch(_url):
        nonlocal fetch_count
        fetch_count += 1
        return b"same-image-content"

    async def fake_upload(**_kwargs):
        nonlocal upload_count
        upload_count += 1
        await asyncio.sleep(0.01)
        return {"cdnUrl": "image-cdn", "aesKey": "image-key", "hdLength": 18}

    monkeypatch.setattr(materials, "_fetch_source_bytes", fake_fetch)
    monkeypatch.setattr(materials, "_upload_eyun_cdn_image", fake_upload)
    results = await asyncio.gather(
        *[
            materials.materialize_eyun_outbound_media(
                w_id="wid", message_type="image", content="https://cdn.example/a.jpg"
            )
            for _ in range(10)
        ]
    )
    same_bytes_at_new_url = await materials.materialize_eyun_outbound_media(
        w_id="wid", message_type="image", content="https://cdn.example/b.jpg"
    )

    assert upload_count == 1
    assert fetch_count == 2
    assert len({item["id"] for item in results}) == 1
    assert same_bytes_at_new_url["id"] == results[0]["id"]
    image = ElementTree.fromstring(results[0]["raw_xml"]).find("img")
    assert image is not None
    assert image.attrib["cdnmidimgurl"] == "image-cdn"


@pytest.mark.asyncio
async def test_video_url_builds_reusable_video_and_thumbnail_xml(monkeypatch):
    from app.services import eyun_material_service as materials

    async def fake_fetch(url):
        return b"video-content" if url.endswith(".mp4") else b"thumb-content"

    async def fake_video(**_kwargs):
        return {"cdnUrl": "video-cdn", "aesKey": "video-key", "length": 13}

    async def fake_image(**_kwargs):
        return {"cdnUrl": "thumb-cdn", "aesKey": "thumb-key", "hdLength": 13}

    monkeypatch.setattr(materials, "_fetch_source_bytes", fake_fetch)
    monkeypatch.setattr(materials, "_upload_eyun_cdn_video", fake_video)
    monkeypatch.setattr(materials, "_upload_eyun_cdn_image", fake_image)
    result = await materials.materialize_eyun_outbound_media(
        w_id="wid",
        message_type="video",
        content='{"path":"https://cdn.example/a.mp4","thumb_path":"https://cdn.example/a.jpg"}',
    )

    video = ElementTree.fromstring(result["raw_xml"]).find("videomsg")
    assert video is not None
    assert video.attrib["cdnvideourl"] == "video-cdn"
    assert video.attrib["cdnthumburl"] == "thumb-cdn"
    assert video.attrib["aeskey"] == "video-key"
    assert video.attrib["cdnthumbaeskey"] == "thumb-key"


@pytest.mark.asyncio
async def test_legacy_image_enqueue_uses_material_forwarding(monkeypatch):
    from app.infrastructure.database.models import EyunOutboundMessageModel
    from app.services import eyun_callback_service
    from app.services import eyun_material_service as materials
    from app.services import message_risk_control_service as risk

    now = datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(risk, "utcnow", lambda: now)

    async def fake_fetch(_url):
        return b"legacy-image"

    async def fake_upload(**_kwargs):
        return {"cdnUrl": "legacy-cdn", "aesKey": "legacy-key", "hdLength": 12}

    forwarded = []

    async def fake_forward(**kwargs):
        forwarded.append(kwargs)
        return {"code": "1000"}

    async def fail_direct(**_kwargs):
        raise AssertionError("newly enqueued media must not use sendImage2")

    monkeypatch.setattr(materials, "_fetch_source_bytes", fake_fetch)
    monkeypatch.setattr(materials, "_upload_eyun_cdn_image", fake_upload)
    monkeypatch.setattr(eyun_callback_service, "send_eyun_received_media", fake_forward)
    monkeypatch.setattr(eyun_callback_service, "send_eyun_image", fail_direct)
    outbound = await risk.enqueue_wechat_outbound(
        w_id="wid",
        wc_id="customer",
        content="https://cdn.example/legacy.jpg",
        source_batch_key="legacy-sop:image",
        message_type="image",
        due_at=now,
    )

    with risk._get_session() as session:
        row = session.get(EyunOutboundMessageModel, outbound["id"])
        assert row.material_id is not None
    assert await risk.process_due_eyun_outbound_messages() == 1
    assert forwarded[0]["message_type"] == "received_image"


@pytest.mark.asyncio
async def test_generated_material_refresh_resumes_waiting_queue(monkeypatch):
    from app.infrastructure.database.models import EyunOutboundMessageModel
    from app.services import eyun_material_service as materials
    from app.services import message_risk_control_service as risk

    now = datetime(2026, 7, 17, 7, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(risk, "utcnow", lambda: now)
    monkeypatch.setattr(materials, "utcnow", lambda: now)

    async def fake_fetch(_url):
        return b"refreshable-image"

    upload_count = 0

    async def fake_upload(**_kwargs):
        nonlocal upload_count
        upload_count += 1
        return {
            "cdnUrl": f"cdn-{upload_count}",
            "aesKey": f"key-{upload_count}",
            "hdLength": 17,
        }

    monkeypatch.setattr(materials, "_fetch_source_bytes", fake_fetch)
    monkeypatch.setattr(materials, "_upload_eyun_cdn_image", fake_upload)
    material = await materials.materialize_eyun_outbound_media(
        w_id="wid", message_type="image", content="https://cdn.example/refresh.jpg"
    )
    outbound = await risk.enqueue_wechat_outbound(
        w_id="wid",
        wc_id="customer",
        content="",
        source_batch_key="refresh",
        message_type="received_image",
        material_id=material["id"],
        due_at=now,
    )
    materials.mark_material_expired(material["id"], "expired")

    assert await risk.process_due_eyun_outbound_messages() == 0
    with risk._get_session() as session:
        assert session.get(EyunOutboundMessageModel, outbound["id"]).status == "waiting_material"

    refreshed = await materials.materialize_eyun_outbound_media(
        w_id="wid", message_type="image", content="https://cdn.example/refresh.jpg"
    )
    assert refreshed["id"] == material["id"]
    assert upload_count == 2
    with risk._get_session() as session:
        assert session.get(EyunOutboundMessageModel, outbound["id"]).status == "queued"


@pytest.mark.asyncio
async def test_bulk_material_send_stores_one_xml_and_queues_per_recipient(monkeypatch):
    from app.infrastructure.database.models import EyunMediaMaterialModel, EyunOutboundMessageModel
    from app.services import eyun_callback_service
    from app.services import eyun_material_service as materials
    from app.services import message_risk_control_service as risk

    xml = '<msg><img cdnmidimgurl="bulk-asset" /></msg>'
    material = materials.capture_eyun_material(media_type="image", raw_xml=xml)
    now = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(risk, "utcnow", lambda: now)
    sent = []

    async def fake_received_media(**kwargs):
        sent.append(kwargs)
        return {"code": "1000"}

    monkeypatch.setattr(
        eyun_callback_service, "send_eyun_received_media", fake_received_media
    )
    job = await risk.enqueue_wechat_bulk_send(
        recipients=[
            {"w_id": "wid", "wc_id": "customer-a"},
            {"w_id": "wid", "wc_id": "customer-b"},
        ],
        items=[{"type": "material", "material_id": material["id"]}],
        source_type="test",
    )

    with risk._get_session() as session:
        assert session.query(EyunMediaMaterialModel).count() == 1
        rows = session.query(EyunOutboundMessageModel).order_by(EyunOutboundMessageModel.id).all()
        assert [row.material_id for row in rows] == [material["id"], material["id"]]
        assert all(row.bulk_job_id == job["id"] for row in rows)

    assert sent == []
    assert await risk.process_due_eyun_outbound_messages(limit=10) == 1
    assert sent[0]["message_type"] == "received_image"
    assert sent[0]["content"] == xml

    monkeypatch.setattr(risk, "utcnow", lambda: now + timedelta(seconds=4))
    assert await risk.process_due_eyun_outbound_messages(limit=10) == 1
    assert {item["wc_id"] for item in sent} == {"customer-a", "customer-b"}


@pytest.mark.asyncio
async def test_expired_material_pauses_and_recapture_resumes_queue(monkeypatch):
    from app.infrastructure.database.models import EyunOutboundMessageModel
    from app.services import eyun_callback_service
    from app.services import eyun_material_service as materials
    from app.services import message_risk_control_service as risk

    xml = '<msg><videomsg cdnvideourl="temporary-cdn" /></msg>'
    material = materials.capture_eyun_material(media_type="video", raw_xml=xml)
    now = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(risk, "utcnow", lambda: now)
    monkeypatch.setattr(materials, "utcnow", lambda: now)

    async def expired_send(**_kwargs):
        raise RuntimeError("CDN material expired")

    monkeypatch.setattr(eyun_callback_service, "send_eyun_received_media", expired_send)
    outbound = await risk.enqueue_wechat_outbound(
        w_id="wid",
        wc_id="customer",
        content="",
        source_batch_key="material-expiry",
        message_type="received_video",
        material_id=material["id"],
        due_at=now,
    )

    assert await risk.process_due_eyun_outbound_messages() == 1
    with risk._get_session() as session:
        assert session.get(EyunOutboundMessageModel, outbound["id"]).status == "waiting_material"
    assert materials.list_materials()["items"][0]["status"] == "expired"

    materials.capture_eyun_material(media_type="video", raw_xml=xml)

    async def successful_send(**_kwargs):
        return {"code": "1000"}

    monkeypatch.setattr(eyun_callback_service, "send_eyun_received_media", successful_send)
    assert await risk.process_due_eyun_outbound_messages() == 1
    with risk._get_session() as session:
        assert session.get(EyunOutboundMessageModel, outbound["id"]).status == "sent"
