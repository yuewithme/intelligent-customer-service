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


def test_generated_ids_use_required_prefix():
    assert generate_id("request").startswith("req_")
    assert generate_id("session").startswith("sess_")
    assert generate_id("document").startswith("doc_")
    assert generate_id("chunk").startswith("chunk_")

