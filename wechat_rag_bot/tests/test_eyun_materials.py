from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
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
    message_risk_control_service._sessionmakers.clear()
    message_risk_control_service._initialized_urls.clear()
    yield
    get_settings.cache_clear()
    eyun_material_service._sessionmakers.clear()
    message_risk_control_service._sessionmakers.clear()
    message_risk_control_service._initialized_urls.clear()


def test_material_capture_deduplicates_xml_and_exposes_admin_api():
    from app.services.eyun_material_service import capture_eyun_material

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
    from app.services.eyun_material_service import (
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
async def test_bulk_material_send_stores_one_xml_and_queues_per_recipient(monkeypatch):
    from app.db.models import EyunMediaMaterialModel, EyunOutboundMessageModel
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
    from app.db.models import EyunOutboundMessageModel
    from app.services import eyun_callback_service
    from app.services import eyun_material_service as materials
    from app.services import message_risk_control_service as risk

    xml = '<msg><videomsg cdnvideourl="temporary-cdn" /></msg>'
    material = materials.capture_eyun_material(media_type="video", raw_xml=xml)
    now = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(risk, "utcnow", lambda: now)

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
