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
    assert allowed.json() == {"code": 0, "data": {"unlocked": True}}
    assert allowed.cookies.get("admin_gate")

    protected = client.get(
        "/api/v1/admin/conversations?page=1&page_size=1",
        headers={"Authorization": "Bearer vercel-proxy"},
    )
    assert protected.status_code == 200
