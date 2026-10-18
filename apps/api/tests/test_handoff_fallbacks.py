from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.domains.conversations.services.service_handoff_service import HANDOFF_CONFIRMATION
from app.main import app


@pytest.fixture(autouse=True)
def isolated_conversation_db(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "chat_log_db_url", f"sqlite:///{tmp_path / 'chat.db'}")


def _chat(message: str) -> dict:
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "handoff_user",
            "message": message,
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    assert response.status_code == 200
    return response.json()["data"]


def test_explicit_human_request_confirms_handoff():
    data = _chat("我要转人工")

    assert data["answer"] == HANDOFF_CONFIRMATION
    assert data["route"] == "human"
    assert data["reply_type"] == "human"
    assert data["need_human"] is True
    assert data["next_action"] == "human_handoff"
    assert data["handoff"]["status"] == "pending"
    assert data["handoff"]["ticket_id"].startswith("handoff_")


def test_low_information_message_stays_with_agent_without_forced_handoff():
    data = _chat("这个那个")

    assert data["answer"]
    assert data["route"] == "agent"
    assert data["reply_type"] == "sales_agent"
    assert data["need_human"] is False
