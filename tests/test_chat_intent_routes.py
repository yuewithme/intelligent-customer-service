from fastapi.testclient import TestClient

from app.main import app


def _chat(message: str, user_id: str = "intent_route_user") -> dict:
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": user_id,
            "message": message,
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 0
    return payload["data"]


def test_rag_no_answer_routes_to_human_with_metadata():
    data = _chat("报销流程是什么？", "intent_route_rag_empty")

    assert data["answer"] == ""
    assert data["route"] == "human"
    assert data["need_human"] is True
    assert data["next_action"] == "human_handoff"
    assert data["metadata"]["handoff"]["ticket_id"].startswith("handoff_")
    assert data["metadata"]["handoff"]["reason"] == "rag_no_answer_to_handoff"
    assert data["metadata"]["original_route"] == "rag_answer"
    assert {"answer", "session_id", "sources", "usage"} <= set(data)


def test_unclear_input_routes_to_human_with_original_route():
    data = _chat("乱七八糟不明确输入", "intent_route_clarify")

    assert data["answer"] == ""
    assert data["route"] == "human"
    assert data["metadata"]["original_route"] == "clarify"
    assert data["metadata"]["handoff"]["reason"] == "clarify_to_handoff"


def test_refund_routes_to_human_without_normal_reply():
    data = _chat("我要退款", "intent_route_refund")

    assert data["answer"] == ""
    assert data["route"] == "human"
    assert data["need_human"] is True
    assert data["metadata"]["handoff"]["status"] == "pending"


def test_price_objection_uses_template_when_template_matches():
    data = _chat("这个有点贵", "intent_route_price")

    assert data["route"] == "template_reply"
    assert data["answer"]
    assert data["template"]["template_id"] == "tpl_price_objection_default"
