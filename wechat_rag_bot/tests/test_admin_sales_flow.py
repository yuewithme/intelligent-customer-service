import json

from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.models import UserProfileModel
from app.main import app
from app.services.user_profile_service import _get_session, ensure_user_profile


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'sales-flow.db').as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("EVALUATION_MODE", "true")
    get_settings.cache_clear()


def _seed_opportunity(user_id: str, *, interruption: dict | None = None):
    import asyncio

    asyncio.run(ensure_user_profile(user_id))
    with _get_session() as session:
        profile = session.get(UserProfileModel, user_id)
        profile.current_stage = "need_discovery"
        profile.active_opportunity_json = json.dumps(
            {
                "opportunity_id": "opp_test",
                "status": "active",
                "current_stage": "need_discovery",
                "slots": {"need_track": "product"},
                "asked_slots": [],
                "interruption": interruption,
            }
        )
        session.commit()


def test_stage_catalog_exposes_seven_stages_and_script_coverage(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    response = TestClient(app).get("/api/admin/sales-flow/stages")

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 7
    assert all(item["script_coverage"] > 0 for item in items)


def test_manual_stage_adjustment_requires_reason_and_is_audited(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _seed_opportunity("sales-user")
    client = TestClient(app)

    invalid = client.patch(
        "/api/admin/sales-flow/opportunities/sales-user/stage",
        json={"stage": "pain_discovery", "reason": "", "operator_id": "admin"},
    )
    assert invalid.status_code == 422

    response = client.patch(
        "/api/admin/sales-flow/opportunities/sales-user/stage",
        json={"stage": "pain_discovery", "reason": "客户补充痛点", "operator_id": "admin"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["current_stage"] == "pain_discovery"
    assert response.json()["data"]["opportunity"]["manual_adjustment"]["operator_id"] == "admin"


def test_manual_close_cannot_forge_won_or_override_interruption(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    _seed_opportunity("paused-user", interruption={"type": "after_sale"})
    client = TestClient(app)

    stage_response = client.patch(
        "/api/admin/sales-flow/opportunities/paused-user/stage",
        json={"stage": "closing", "reason": "test", "operator_id": "admin"},
    )
    assert stage_response.status_code == 409

    close_response = client.post(
        "/api/admin/sales-flow/opportunities/paused-user/close",
        json={"status": "won", "reason": "manual", "operator_id": "admin"},
    )
    assert close_response.status_code == 409
