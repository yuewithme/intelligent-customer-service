from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_admin_gate_uses_api_key_when_password_is_not_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_GATE_PASSWORD", raising=False)
    monkeypatch.setenv("API_KEY", "local-secret")
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    get_settings.cache_clear()

    client = TestClient(app)
    denied = client.post("/api/gate", json={"password": "wrong"})
    allowed = client.post("/api/gate", json={"password": "local-secret"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert allowed.json() == {
        "code": 0,
        "data": {"unlocked": True, "role": "admin"},
    }
    assert allowed.cookies.get("admin_gate")

    protected = client.get(
        "/api/v1/admin/conversations?page=1&page_size=1",
        headers={"Authorization": "Bearer vercel-proxy"},
    )
    assert protected.status_code == 200


def test_admin_gate_separates_test_and_admin_permissions(monkeypatch):
    monkeypatch.setenv("ADMIN_GATE_PASSWORD", "admin-password")
    monkeypatch.setenv("ADMIN_GATE_TEST_PASSWORD", "test-password")
    monkeypatch.setenv("ADMIN_GATE_SECRET", "cookie-signing-secret")
    monkeypatch.setenv("API_KEY", "api-key")
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    get_settings.cache_clear()

    test_client = TestClient(app)
    unlocked = test_client.post("/api/gate", json={"password": "test-password"})
    gate_status = test_client.get("/api/gate")
    demo_conversations = test_client.get("/api/v1/demo-admin/conversations")
    formal_conversations = test_client.get("/api/v1/admin/conversations")
    readonly_get = test_client.get("/api/v1/admin/activities")
    readonly_write = test_client.post(
        "/api/v1/admin/activities/1/archive",
        json={"operator_id": "tester"},
    )

    assert unlocked.json()["data"]["role"] == "test"
    assert gate_status.json()["data"] == {"unlocked": True, "role": "test"}
    assert demo_conversations.status_code == 200
    assert formal_conversations.status_code == 403
    assert readonly_get.status_code == 200
    assert readonly_write.status_code == 403

    admin_client = TestClient(app)
    admin_client.post("/api/gate", json={"password": "admin-password"})
    assert admin_client.get("/api/v1/admin/conversations").status_code == 200
