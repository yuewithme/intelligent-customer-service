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


def test_rag_no_answer_detection_keeps_llm_fallback_without_sources():
    from app.services.chat_orchestrator import _is_rag_no_answer

    assert _is_rag_no_answer({"answer": "可以先放在通风散光处观察。", "sources": []}) is False
    assert _is_rag_no_answer({"answer": "", "sources": []}) is True
    assert _is_rag_no_answer({"answer": "知识库中没有找到明确答案。", "sources": []}) is True


def test_unclear_input_stays_in_clarify_route():
    data = _chat("乱七八糟不明确输入", "intent_route_clarify")

    assert data["answer"]
    assert data["route"] == "clarify"
    assert data["need_human"] is False
    assert data["intent"]["slots"]["original_route"] == "clarify"
    assert data["intent"]["reason"] == "clarify_missing_information"


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
