import json
import re
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.domains.sales.api import video_touch
from app.domains.sales.services import video_touch_tracking_service as tracking
from app.infrastructure.database.models import EyunOutboundMessageModel
from app.integrations.eyun.services import message_risk_control_service as risk


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("VIDEO_TOUCH_PUBLIC_BASE_URL", "https://sales.example.test")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    tracking.reset_video_touch_storage_cache()
    risk._sessionmakers.clear()
    risk._initialized_urls.clear()


def _create_link():
    return tracking.create_video_touch_link(
        delivery_key="video_touch_test:one:card",
        w_id="wid-1",
        wc_id="customer-1",
        material_ref="material:agent-media:video-1",
        target_url="https://media.example.test/video.mp4",
        thumb_url="https://media.example.test/video.jpg",
        title="浇水演示",
        source_type="video_touch_test",
        source_batch_key="video_touch_test:one",
    )


def test_production_tracking_host_is_always_https(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)

    assert (
        tracking._public_base_url("http://sales-agent.hzwohu.com")
        == "https://sales-agent.hzwohu.com"
    )


def test_preview_does_not_count_and_play_sessions_are_deduplicated(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    link = _create_link()
    same_link = _create_link()

    assert same_link["id"] == link["id"]
    assert same_link["token"] == link["token"]
    assert tracking.get_video_touch_landing(link["token"])["title"] == "浇水演示"
    assert tracking.get_video_touch_test(link["id"])["open_count"] == 0

    assert tracking.record_video_touch_open(
        token=link["token"], play_session="session-0000000001"
    ) == "https://media.example.test/video.mp4"
    assert tracking.record_video_touch_open(
        token=link["token"], play_session="session-0000000001"
    ) == "https://media.example.test/video.mp4"
    tracking.record_video_touch_open(
        token=link["token"], play_session="session-0000000002"
    )

    result = tracking.get_video_touch_test(link["id"])
    assert result["opened"] is True
    assert result["open_count"] == 2
    stats = tracking.list_video_touch_tests()
    assert stats["total"] == 1
    assert stats["opened"] == 1
    assert stats["unique_open_rate"] == 1.0
    assert stats["open_count"] == 2


def test_public_landing_streams_video_and_reuses_one_play_session(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    link = _create_link()

    class FakeResponse:
        status_code = 206
        headers = {
            "content-type": "video/mp4",
            "content-length": "5",
            "content-range": "bytes 0-4/5",
            "accept-ranges": "bytes",
        }

        def raise_for_status(self):
            return None

        async def aiter_bytes(self):
            yield b"video"

        async def aclose(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.request = None

        def build_request(self, method, url, headers=None):
            self.request = {"method": method, "url": url, "headers": headers or {}}
            return self.request

        async def send(self, request, stream=False):
            assert request["url"] == "https://media.example.test/video.mp4"
            assert request["headers"]["Range"] == "bytes=0-4"
            assert stream is True
            return FakeResponse()

        async def aclose(self):
            return None

    monkeypatch.setattr(video_touch.httpx, "AsyncClient", FakeClient)
    app = FastAPI()
    app.include_router(video_touch.public_router)
    client = TestClient(app)

    landing = client.get(f"/v/{link['token']}")
    assert landing.status_code == 200
    assert "window.location.replace" in landing.text
    assert tracking.get_video_touch_test(link["id"])["open_count"] == 0
    play_session = re.search(r"session=([A-Za-z0-9_-]+)", landing.text).group(1)

    first = client.get(
        f"/v/{link['token']}/play?session={play_session}",
        headers={"Range": "bytes=0-4"},
    )
    second = client.get(
        f"/v/{link['token']}/play?session={play_session}",
        headers={"Range": "bytes=0-4"},
    )
    assert first.status_code == 206
    assert first.content == b"video"
    assert second.status_code == 206
    assert tracking.get_video_touch_test(link["id"])["open_count"] == 1


@pytest.mark.asyncio
async def test_test_sender_queues_link_card_without_changing_native_video_flow(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(
        tracking,
        "get_agent_media",
        lambda material_ref: {
            "material_ref": f"material:{material_ref}",
            "title": "养兰视频",
            "format": "video",
            "url": "https://media.example.test/video.mp4",
            "thumb_url": "https://media.example.test/video.jpg",
        },
    )
    captured = {}

    async def fake_enqueue(**kwargs):
        captured.update(kwargs)
        now = datetime.now(timezone.utc)
        with tracking._session() as session:
            row = EyunOutboundMessageModel(
                w_id=kwargs["w_id"],
                wc_id=kwargs["wc_id"],
                content=kwargs["content"],
                source_batch_key=kwargs["source_batch_key"],
                delivery_key=kwargs["delivery_key"],
                conversation_message_id=77,
                status="queued",
                priority=100,
                due_at=now,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return {"id": row.id, "conversation_message_id": 77}

    monkeypatch.setattr(risk, "enqueue_wechat_outbound", fake_enqueue)
    result = await tracking.enqueue_video_touch_test(
        material_ref="material:agent-media:video-1",
        w_id="wid-1",
        wc_id="filehelper",
    )

    assert captured["message_type"] == "link_card"
    card = json.loads(captured["content"])
    assert card["url"].startswith("https://sales.example.test/v/")
    assert card["thumb_url"] == "https://media.example.test/video.jpg"
    assert result["delivery_status"] == "queued"
    assert result["open_count"] == 0
