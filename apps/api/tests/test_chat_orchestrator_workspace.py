from datetime import datetime, timezone

import pytest

from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.services import chat_orchestrator
from app.domains.conversations.services.chat_orchestrator import _customer_workspace
from app.domains.customers.schemas.memory import MemoryContext
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.reply import FinalReply


class _Message:
    metadata = {}
    user_id = "customer-1"


def test_customer_workspace_does_not_expose_profile_names_to_agent():
    workspace = _customer_workspace(
        message=_Message(),
        user_state=UserState(user_id="customer-1"),
        profile_bundle={
            "profile": {
                "basic_info": {
                    "nickname": "贵杰",
                    "remark_name": "黄先生",
                    "display_name": "奇怪微信名",
                    "region": "杭州",
                }
            },
            "recent_memories": [],
        },
    )

    assert workspace["profile"]["basic_info"] == {"region": "杭州"}


@pytest.mark.asyncio
async def test_handle_chat_injects_gated_memory_context_into_agent_workspace(
    monkeypatch,
):
    state = UserState(
        user_id="customer-1",
        session_id="session-1",
        metadata={
            "memory_v2_context": {"stale": True},
            "memory_v2_trace": {"status": "stale"},
        },
    )
    memory_context = MemoryContext(
        tenant_id="tenant-1",
        subject_id="subject-1",
        as_of=datetime.now(timezone.utc),
        current_facts=[
            {
                "fact_id": 1,
                "fact_key": "service.preference",
                "fact_value": {"topic": "flower_color", "value": "白色"},
                "source_type": "customer_explicit",
                "confidence": 0.99,
                "valid_from": datetime.now(timezone.utc),
                "evidence_event_ids": [10],
            }
        ],
    )
    captured = {}

    async def fake_recover_automatic_handoff(**kwargs):
        del kwargs

    async def fake_get_user_state(user_id, session_id):
        assert (user_id, session_id) == ("customer-1", "session-1")
        return state

    async def fake_prepare_memory(message):
        assert message.tenant_id == "tenant-1"
        return memory_context, {
            "status": "canary",
            "injected": True,
            "latency_ms": 7,
        }

    async def fake_get_profile_bundle(*args, **kwargs):
        del args, kwargs
        return {"profile": {}, "recent_memories": []}

    async def fake_run_sales_agent(*, message, user_state, workspace):
        del message, user_state
        captured["workspace"] = workspace
        return FinalReply(answer="已记住", reply_type="agent", route="agent")

    async def fake_update_user_state(*args, **kwargs):
        del args, kwargs

    async def fake_record_chat_log(payload):
        captured["log"] = payload

    monkeypatch.setattr(
        chat_orchestrator,
        "recover_automatic_handoff",
        fake_recover_automatic_handoff,
    )
    monkeypatch.setattr(chat_orchestrator, "conversation_blocks_ai", lambda **_: False)
    monkeypatch.setattr(chat_orchestrator, "get_user_state", fake_get_user_state)
    monkeypatch.setattr(
        chat_orchestrator,
        "prepare_memory_context_for_request",
        fake_prepare_memory,
    )
    monkeypatch.setattr(
        chat_orchestrator,
        "get_profile_bundle",
        fake_get_profile_bundle,
    )
    monkeypatch.setattr(chat_orchestrator, "run_sales_agent", fake_run_sales_agent)
    monkeypatch.setattr(chat_orchestrator, "update_user_state", fake_update_user_state)
    monkeypatch.setattr(
        chat_orchestrator,
        "record_agent_relationship_state",
        lambda **_: None,
    )
    monkeypatch.setattr(chat_orchestrator, "record_chat_log", fake_record_chat_log)

    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="wechat",
            user_id="customer-1",
            session_id="session-1",
            message="我喜欢白色兰花",
            kb_id="kb_default",
            metadata={
                "tenant_id": "tenant-1",
                "provider": "eyun",
                "skip_conversation_memory": True,
            },
        )
    )

    expected = memory_context.model_dump(mode="json")
    assert captured["workspace"]["evidence_memory"] == expected
    assert state.metadata["memory_v2_context"] == expected
    assert state.metadata["memory_v2_trace"] == {
        "status": "canary",
        "injected": True,
        "latency_ms": 7,
    }
    assert captured["log"]["stage_latencies"]["memory_v2_ms"] == 7
    assert result["answer"] == "已记住"
