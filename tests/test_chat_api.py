from fastapi.testclient import TestClient

from app.main import app


def test_chat_api_returns_unified_response(monkeypatch):
    from app.routers import chat

    async def fake_rag_chat(**kwargs):
        return {
            "answer": "答案",
            "session_id": "sess_001",
            "sources": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    monkeypatch.setattr(chat, "rag_chat", fake_rag_chat)
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

    async def fail_rag_chat(**kwargs):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(chat, "rag_chat", fail_rag_chat)
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
