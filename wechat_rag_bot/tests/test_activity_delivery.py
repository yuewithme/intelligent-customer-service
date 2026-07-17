import asyncio

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.conversation_service import record_customer_message
from app.services.message_risk_control_service import process_due_eyun_outbound_messages


def _reset_settings(monkeypatch, tmp_path):
    db_path = tmp_path / "activity-delivery.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("EYUN_REPLY_JITTER_MIN_SECONDS", "0")
    monkeypatch.setenv("EYUN_REPLY_JITTER_MAX_SECONDS", "0")
    monkeypatch.setenv("EYUN_SEND_MIN_INTERVAL_SECONDS", "0")
    get_settings.cache_clear()


def _seed_activity_and_target():
    async def seed():
        for content, message_type, media in (
            ("活动正文", "60001", None),
            (
                "[图片]",
                "60002",
                {"type": "image", "url": "https://cdn.example.com/promo.jpg"},
            ),
            (
                "[视频]",
                "60003",
                {"type": "video", "url": "https://cdn.example.com/promo.mp4"},
            ),
        ):
            metadata = {"provider": "eyun", "message_type": message_type}
            if media:
                metadata.update(
                    {
                        "raw_content": f'<msg media="{media["type"]}" />',
                        "media": media,
                    }
                )
            await record_customer_message(
                channel="wechat",
                user_id="wxid_marketer",
                session_id="default",
                content=content,
                metadata=metadata,
            )
        await record_customer_message(
            channel="wechat",
            user_id="wxid_customer",
            session_id="default",
            content="你好",
            metadata={
                "provider": "eyun",
                "message_type": "60001",
                "w_id": "wid_test",
                "from_user": "wxid_customer",
            },
        )

    asyncio.run(seed())


def _published_activity(client: TestClient) -> int:
    messages = client.get(
        "/api/v1/admin/conversations/wechat:wxid_marketer:default"
    ).json()["data"]["messages"]
    activity = client.post(
        "/api/v1/admin/activities/from-messages",
        json={
            "conversation_id": "wechat:wxid_marketer:default",
            "message_ids": [message["id"] for message in messages],
            "title": "组合活动",
            "operator_id": "admin",
        },
    ).json()["data"]
    client.post(
        f"/api/v1/admin/activities/{activity['id']}/publish",
        json={"operator_id": "admin"},
    )
    return activity["id"]


def test_activity_send_requires_matching_human_owner(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    _seed_activity_and_target()
    client = TestClient(app)
    activity_id = _published_activity(client)

    response = client.post(
        f"/api/v1/admin/activities/{activity_id}/send",
        json={
            "conversation_id": "wechat:wxid_customer:default",
            "operator_id": "admin",
        },
    )

    assert response.status_code == 409
    assert "接管" in response.json()["message"]


def test_activity_send_queues_and_dispatches_text_image_video(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    from app.services import eyun_callback_service, message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    _seed_activity_and_target()
    client = TestClient(app)
    activity_id = _published_activity(client)
    client.post(
        "/api/v1/admin/conversations/wechat:wxid_customer:default/claim",
        json={"operator_id": "admin"},
    )
    sent = []

    async def fake_text(**kwargs):
        sent.append(("text", kwargs))

    async def fake_received(**kwargs):
        sent.append((kwargs["message_type"], kwargs))

    monkeypatch.setattr(eyun_callback_service, "send_eyun_text", fake_text)
    monkeypatch.setattr(
        eyun_callback_service,
        "send_eyun_received_media",
        fake_received,
        raising=False,
    )
    current_time = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)

    def advancing_now():
        nonlocal current_time
        current_time += timedelta(seconds=3)
        return current_time

    monkeypatch.setattr(message_risk_control_service, "utcnow", advancing_now)

    response = client.post(
        f"/api/v1/admin/activities/{activity_id}/send",
        json={
            "conversation_id": "wechat:wxid_customer:default",
            "operator_id": "admin",
        },
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "queued"
    assert len(response.json()["data"]["outbound_message_ids"]) == 3

    attempted = sum(
        asyncio.run(process_due_eyun_outbound_messages(limit=3)) for _ in range(3)
    )
    logs = client.get(
        f"/api/v1/admin/activities/{activity_id}/send-logs"
    ).json()["data"]

    assert attempted == 3
    assert [item[0] for item in sent] == [
        "text",
        "received_image",
        "received_video",
    ]
    assert all(item[1]["w_id"] == "wid_test" for item in sent)
    assert all(item[1]["wc_id"] == "wxid_customer" for item in sent)
    assert logs["items"][0]["status"] == "sent"
    assert logs["items"][0]["trigger_mode"] == "manual"
