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


def test_clarify_route_asks_followup_without_rag_or_handoff():
    data = _chat("这个那个")

    assert data["answer"]
    assert data["route"] == "clarify"
    assert data["reply_type"] == "clarify"
    assert data["need_human"] is False
    assert data["next_action"] is None
    assert data["handoff"] is None


def test_rag_answer_without_sources_is_not_handoff_by_itself():
    from app.services.chat_orchestrator import _is_rag_no_answer

    assert _is_rag_no_answer({"answer": "可以先放在通风散光处观察。", "sources": []}) is False
