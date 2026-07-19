from fastapi.testclient import TestClient

from app.main import app
from app.schemas.chat import APIResponse, ChatRequest
from app.schemas.common import ErrorCode
from app.utils.ids import generate_id


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


def test_chat_response_preserves_old_fields_and_adds_router_fields():
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
    assert data["route"] == "template_reply"
    assert data["reply_type"] == "template"
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


def test_rule_guard_routes_human_refund_and_complaint_to_human():
    client = TestClient(app)
    messages = [
        ("我要转人工", "human_request"),
        ("我要退款", "refund_request"),
        ("我要投诉，你们骗我", "complaint"),
    ]

    for text, primary_intent in messages:
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
        assert data["intent"]["primary_intent"] == primary_intent


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


def test_mock_intent_routes_knowledge_price_and_mixed_messages():
    client = TestClient(app)
    cases = [
        ("兰花怎么养护？", "rag_answer"),
        ("这个有点贵", "template_reply"),
        ("这个有点贵，而且我怕养不活", "rag_answer"),
    ]

    for text, route in cases:
        response = client.post(
            "/api/v1/chat",
            json={
                "channel": "api",
                "user_id": "user_001",
                "message": text,
                "kb_id": "kb_default",
            },
        )
        assert response.status_code == 200
        assert response.json()["data"]["route"] == route


def test_template_routes_create_and_search():
    from app.services.template_service import _templates

    template_id = "tpl_price_objection_test"
    try:
        client = TestClient(app)
        create_response = client.post(
            "/api/v1/templates",
            json={
                "template_id": template_id,
                "intent": "price_objection",
                "stage": "objection_handling",
                "trigger_examples": ["有点贵"],
                "content": "价格确实需要综合品质和售后来看。",
                "priority": 90,
            },
        )
        assert create_response.status_code == 200
        assert create_response.json()["data"] == {
            "template_id": template_id,
            "status": "indexed",
        }

        search_response = client.post(
            "/api/v1/templates/search",
            json={
                "message": "有点贵，我再考虑一下",
                "intent": "price_objection",
                "stage": "objection_handling",
                "customer_tags": ["price_sensitive"],
                "top_k": 5,
            },
        )
        assert search_response.status_code == 200
        assert search_response.json()["data"]["templates"]
    finally:
        _templates.pop(template_id, None)


def test_intent_examples_debug_and_state_routes():
    client = TestClient(app)
    example_response = client.post(
        "/api/v1/intent-examples",
        json={
            "example_id": "ex_price_test",
            "text": "有点贵，我再考虑一下",
            "route": "template_reply",
            "primary_intent": "price_objection",
            "secondary_intents": ["hesitation"],
            "sales_stage": "objection_handling",
        },
    )
    assert example_response.status_code == 200
    assert example_response.json()["data"]["example_id"] == "ex_price_test"

    debug_response = client.post(
        "/api/v1/debug/intent",
        json={
            "user_id": "user_001",
            "message": "这个有点贵，而且我怕养不活",
            "session_id": "sess_xxx",
            "kb_id": "kb_default",
        },
    )
    assert debug_response.status_code == 200
    debug_data = debug_response.json()["data"]
    assert debug_data["intent"]["route"] == "template_then_rag"
    assert "candidates" in debug_data
    assert "user_state" in debug_data

    patch_response = client.patch(
        "/api/v1/users/user_001/state",
        json={"sales_stage": "objection_handling", "customer_tags": ["price_sensitive"]},
    )
    assert patch_response.status_code == 200
    assert patch_response.json()["data"]["sales_stage"] == "objection_handling"

    get_response = client.get("/api/v1/users/user_001/state")
    assert get_response.status_code == 200
    assert get_response.json()["data"]["customer_tags"] == []
