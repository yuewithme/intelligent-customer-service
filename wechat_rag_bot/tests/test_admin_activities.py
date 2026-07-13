import asyncio

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.conversation_service import record_customer_message


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
    assert activity["status"] == "draft"
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


def test_activity_publish_switch_and_archive_lifecycle(monkeypatch, tmp_path):
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

    published = client.post(
        f"/api/v1/admin/activities/{created['id']}/publish",
        json={"operator_id": "admin"},
    )
    disabled = client.patch(
        f"/api/v1/admin/activities/{created['id']}/switches",
        json={"operator_id": "admin", "enabled": False},
    )
    archived = client.post(
        f"/api/v1/admin/activities/{created['id']}/archive",
        json={"operator_id": "admin"},
    )

    assert published.status_code == 200
    assert published.json()["data"]["status"] == "published"
    assert published.json()["data"]["valid_from"] is not None
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == "archived"


def test_activity_apis_require_authorization(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    response = client.get("/api/v1/admin/activities")

    assert response.status_code == 401
    assert response.json()["code"] == 40100
