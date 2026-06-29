from fastapi.testclient import TestClient

from app.main import app


def test_chat_api_returns_unified_response(monkeypatch):
    from app.routers import chat

    async def fake_handle_chat(request):
        assert request.channel == "api"
        return {
            "answer": "答案",
            "session_id": "sess_001",
            "sources": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "reply_type": "rag",
            "route": "rag_answer",
            "intent": {"primary_intent": "knowledge_question"},
            "template": {},
            "need_human": False,
            "next_action": None,
            "trace_id": "req_001",
        }

    monkeypatch.setattr(chat, "handle_chat", fake_handle_chat)
    client = TestClient(app)
    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "message": "问题",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {
            "answer": "答案",
            "session_id": "sess_001",
            "sources": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "reply_type": "rag",
            "route": "rag_answer",
            "intent": {"primary_intent": "knowledge_question"},
            "template": {},
            "need_human": False,
            "next_action": None,
            "trace_id": "req_001",
        },
    }


def test_validation_error_uses_unified_response():
    client = TestClient(app)
    response = client.post("/api/v1/chat", json={"message": "missing fields"})

    assert response.status_code == 422
    assert response.json()["code"] == 40000
    assert response.json()["data"] is None


def test_unhandled_error_uses_unified_response(monkeypatch):
    from app.routers import chat

    async def fail_handle_chat(request):
        del request
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(chat, "handle_chat", fail_handle_chat)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "message": "问题",
            "kb_id": "kb_default",
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "code": 50000,
        "message": "服务内部错误",
        "data": None,
    }
