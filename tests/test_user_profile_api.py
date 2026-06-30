from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def clear_settings_cache_after_test():
    yield
    get_settings.cache_clear()


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat_logs.db').as_posix()}")
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()


def test_get_profile_creates_empty_profile_and_legacy_state_still_works(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/users/user_001/profile")
    legacy_response = client.get("/api/v1/users/user_001/state")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"code", "message", "data"}
    assert body["code"] == 0
    profile = body["data"]["profile"]
    assert profile["user_id"] == "user_001"
    assert profile["tenant_id"] == "tenant_default"
    assert profile["current_stage"] == "unknown"
    assert profile["risk_level"] == "normal"
    assert profile["customer_tags"] == []
    assert body["data"]["recent_memories"] == []
    assert body["data"]["events"] == []

    assert legacy_response.status_code == 200
    assert legacy_response.json()["data"]["user_id"] == "user_001"


def test_patch_profile_updates_allowed_fields_and_writes_event(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.patch(
        "/api/v1/users/user_001/profile",
        json={
            "customer_tags": ["vip", "price_sensitive"],
            "tenant_id": "evil",
            "metadata": {"reason": "operator_update"},
        },
    )
    events_response = client.get("/api/v1/users/user_001/profile/events")

    assert response.status_code == 200
    profile = response.json()["data"]["profile"]
    assert profile["tenant_id"] == "tenant_default"
    assert profile["customer_tags"] == ["vip", "price_sensitive"]

    assert events_response.status_code == 200
    event = events_response.json()["data"]["items"][0]
    assert event["event_type"] == "profile_patched"
    assert event["before"]["customer_tags"] == []
    assert event["after"]["customer_tags"] == ["vip", "price_sensitive"]
    assert event["reason"] == "operator_update"


def test_memories_returns_recent_chat_messages(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    chat_response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {"tenant_id": "tenant_default"},
        },
    )
    memories_response = client.get("/api/v1/users/user_001/memories")

    assert chat_response.status_code == 200
    assert memories_response.status_code == 200
    body = memories_response.json()
    assert set(body) == {"code", "message", "data"}
    assert body["data"]["limit"] == 10
    assert [item["role"] for item in body["data"]["items"]] == ["user", "assistant"]
    assert body["data"]["items"][0]["content"] == "hello"


def test_new_profile_apis_require_bearer_authentication(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    missing = client.get("/api/v1/users/user_001/profile")
    authorized = client.get(
        "/api/v1/users/user_001/profile",
        headers={"Authorization": "Bearer test-key"},
    )

    assert missing.status_code == 401
    assert missing.json()["data"] is None
    assert authorized.status_code == 200
