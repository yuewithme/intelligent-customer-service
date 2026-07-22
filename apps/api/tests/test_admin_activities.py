import asyncio
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.database.models import ActivityModel, Base
from app.main import app
from app.domains.conversations.services.conversation_service import record_customer_message


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "activities.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()


def _record_source_messages():
    async def record():
        await record_customer_message(
            channel="wechat",
            user_id="wxid_marketer",
            session_id="default",
            content="本周兰花活动开始了",
            metadata={"provider": "eyun", "message_type": "60001"},
        )
        await record_customer_message(
            channel="wechat",
            user_id="wxid_marketer",
            session_id="default",
            content="[图片]",
            metadata={
                "provider": "eyun",
                "message_type": "60002",
                "raw_content": '<msg><img aeskey="secret" /></msg>',
                "media": {
                    "type": "image",
                    "url": "https://cdn.example.com/activity.jpg",
                },
            },
        )

    asyncio.run(record())


def test_create_activity_from_messages_keeps_order_and_hides_media_xml(
    monkeypatch, tmp_path
):
    _reset_settings(monkeypatch, tmp_path)
    _record_source_messages()
    client = TestClient(app)
    detail = client.get(
        "/api/v1/admin/conversations/wechat:wxid_marketer:default"
    ).json()["data"]
    text_id, image_id = [message["id"] for message in detail["messages"]]

    response = client.post(
        "/api/v1/admin/activities/from-messages",
        json={
            "conversation_id": "wechat:wxid_marketer:default",
            "message_ids": [image_id, text_id],
            "title": "七月兰花活动",
            "summary": "活动素材包",
            "operator_id": "admin",
        },
    )

    assert response.status_code == 200
    activity = response.json()["data"]
    assert activity["status"] == "published"
    assert activity["effective_status"] == "active"
    assert activity["enabled"] is True
    assert activity["ai_enabled"] is False
    assert activity["valid_until"] is None
    assert [item["type"] for item in activity["items"]] == [
        "text",
        "received_image",
    ]
    assert [item["source_message_id"] for item in activity["items"]] == [
        text_id,
        image_id,
    ]
    assert activity["items"][1]["preview_url"] == (
        "https://cdn.example.com/activity.jpg"
    )
    assert "aeskey" not in response.text


def test_activity_switch_archive_and_restart_lifecycle(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    _record_source_messages()
    client = TestClient(app)
    messages = client.get(
        "/api/v1/admin/conversations/wechat:wxid_marketer:default"
    ).json()["data"]["messages"]
    created = client.post(
        "/api/v1/admin/activities/from-messages",
        json={
            "conversation_id": "wechat:wxid_marketer:default",
            "message_ids": [messages[0]["id"]],
            "title": "长期活动",
            "operator_id": "admin",
        },
    ).json()["data"]

    disabled = client.patch(
        f"/api/v1/admin/activities/{created['id']}/switches",
        json={"operator_id": "admin", "enabled": False},
    )
    archived = client.post(
        f"/api/v1/admin/activities/{created['id']}/archive",
        json={"operator_id": "admin"},
    )
    restarted = client.post(
        f"/api/v1/admin/activities/{created['id']}/publish",
        json={"operator_id": "admin"},
    )

    assert created["status"] == "published"
    assert created["enabled"] is True
    assert created["published_at"] is not None
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == "archived"
    assert restarted.status_code == 200
    assert restarted.json()["data"]["status"] == "published"
    assert restarted.json()["data"]["enabled"] is True
    assert restarted.json()["data"]["effective_status"] == "active"


def test_legacy_draft_activity_is_activated_when_activity_store_opens(
    monkeypatch, tmp_path
):
    _reset_settings(monkeypatch, tmp_path)
    engine = create_engine(get_settings().chat_log_db_url)
    Base.metadata.create_all(engine, tables=[ActivityModel.__table__])
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            ActivityModel(
                title="历史草稿活动",
                status="draft",
                enabled=False,
                ai_enabled=False,
                ai_rules_json="{}",
                items_json='[{"position": 1, "type": "text", "content": "活动"}]',
                created_by="admin",
                updated_by="admin",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    activity = TestClient(app).get("/api/v1/admin/activities").json()["data"]["items"][0]

    assert activity["status"] == "published"
    assert activity["enabled"] is True
    assert activity["published_at"] is not None


def test_activity_apis_require_authorization(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    response = client.get("/api/v1/admin/activities")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
