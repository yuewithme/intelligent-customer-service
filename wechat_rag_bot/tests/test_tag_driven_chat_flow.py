import pytest

from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat


@pytest.mark.asyncio
async def test_chat_response_contains_tag_and_policy_metadata():
    request = ChatRequest(
        channel="api",
        user_id="tag_user_1",
        session_id="tag_sess_1",
        message="first time growing orchids, root rot, what should I do?",
        kb_id="kb_default",
        metadata={"segment": "beginner"},
    )

    result = await handle_chat(request)

    assert "tag_result" in result["metadata"]
    assert "policy_decision" in result["metadata"]
    assert result["metadata"]["tag_result"]["segment"] == "beginner"
    assert result["metadata"]["policy_decision"]["action"] in {
        "rag_answer",
        "template_reply",
        "template_then_rag",
        "human",
        "clarify",
        "unsupported",
        "chitchat",
    }
