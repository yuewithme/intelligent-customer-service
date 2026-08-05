from fastapi.testclient import TestClient

from app.main import app
from app.domains.conversations.schemas.chat import APIResponse, ChatRequest
from app.shared.schemas.common import ErrorCode
from app.core.ids import generate_id


def test_chat_request_preserves_fixed_fields():
    request = ChatRequest(
        channel="api",
        user_id="user_001",
        message="报销流程是什么？",
        kb_id="kb_default",
    )

    assert request.model_dump().keys() == {
        "channel",
        "user_id",
        "session_id",
        "message",
        "kb_id",
        "metadata",
    }
    assert request.metadata == {}


def test_api_response_preserves_fixed_envelope():
    response = APIResponse(code=0, message="success", data={"ok": True})
    assert response.model_dump() == {
        "code": 0,
        "message": "success",
        "data": {"ok": True},
    }


def test_required_error_codes_are_stable():
    assert ErrorCode.SUCCESS == 0
    assert ErrorCode.MESSAGE_EMPTY == 40003
    assert ErrorCode.INVALID_API_KEY == 40101
    assert ErrorCode.QDRANT_FAILED == 50001
    assert ErrorCode.WECHAT_SIGNATURE_FAILED == 60001
    assert ErrorCode.INTENT_SCHEMA_INVALID == 41002
    assert ErrorCode.TEMPLATE_NOT_FOUND == 42000
    assert ErrorCode.POLICY_ROUTE_INVALID == 43001
    assert ErrorCode.STATE_FAILED == 44000
    assert ErrorCode.REPLY_BUILD_FAILED == 45000


def test_generated_ids_use_required_prefix():
    assert generate_id("request").startswith("req_")
    assert generate_id("session").startswith("sess_")
    assert generate_id("document").startswith("doc_")
    assert generate_id("chunk").startswith("chunk_")


def test_health_uses_unified_envelope():
    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "code": 0,
        "message": "success",
        "data": {"status": "ok"},
    }


def test_chat_response_preserves_api_envelope_and_uses_single_agent_route():
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": None,
            "message": "有点贵，我再考虑一下",
            "kb_id": "kb_default",
            "metadata": {
                "tenant_id": "tenant_default",
                "permission": "public",
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 0
    assert body["message"] == "success"
    data = body["data"]
    for field in ("answer", "session_id", "sources", "usage"):
        assert field in data
    for field in (
        "reply_type",
        "route",
        "intent",
        "template",
        "need_human",
        "next_action",
        "trace_id",
    ):
        assert field in data
    assert data["route"] == "agent"
    assert data["reply_type"] == "sales_agent"
    assert data["intent"]["primary_intent"] == "autonomous_sales_turn"
    assert data["session_id"].startswith("sess_")
    assert data["trace_id"].startswith("req_")


def test_empty_chat_message_returns_message_empty():
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "message": "  ",
            "kb_id": "kb_default",
        },
    )

    assert response.status_code == 400
    assert response.json() == {
        "code": int(ErrorCode.MESSAGE_EMPTY),
        "message": "消息为空",
        "data": None,
    }


def test_authorization_boundary_routes_human_refund_and_complaint_to_human():
    client = TestClient(app)
    messages = ["我要转人工", "我要退款", "我要投诉，你们骗我"]

    for text in messages:
        response = client.post(
            "/api/v1/chat",
            json={
                "channel": "api",
                "user_id": "user_001",
                "message": text,
                "kb_id": "kb_default",
            },
        )
        data = response.json()["data"]
        assert data["route"] == "human"
        assert data["reply_type"] == "human"
        assert data["need_human"] is True
        assert data["intent"]["primary_intent"] == "autonomous_sales_turn"


def test_refund_contract_keeps_empty_handoff_reply():
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "refund_contract_user",
            "message": "会员我不想要了，怎么退款？",
            "kb_id": "kb_default",
            "metadata": {
                "tool_state": {
                    "order_status": "paid",
                    "refund_policy": "未提供",
                }
            },
        },
    )

    data = response.json()["data"]
    assert data["answer"] == ""
    assert data["need_human"] is True
    assert data["next_action"] == "human_handoff"


def test_mock_agent_keeps_non_authorized_messages_on_single_agent_route():
    client = TestClient(app)
    cases = ["兰花怎么养护？", "这个有点贵", "这个有点贵，而且我怕养不活"]

    for index, text in enumerate(cases):
        response = client.post(
            "/api/v1/chat",
            json={
                "channel": "api",
                "user_id": f"intent_contract_user_{index}",
                "message": text,
                "kb_id": "kb_default",
            },
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["route"] == "agent"
        assert data["intent"]["classifier_source"] == "sales_agent"
