from fastapi.testclient import TestClient

from app.main import app


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


def test_explicit_human_request_triggers_handoff_without_reply():
    data = _chat("我要转人工")

    assert data["answer"] == ""
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
