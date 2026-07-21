from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.schemas.intent import IntentResult
from app.schemas.persona import PersonaContext, ReplySpec
from app.schemas.reply import FinalReply, OutboundMessage
from app.schemas.reply_plan import BusinessFacts, ReplyPlan
from app.schemas.state import UserState
from app.services.persona_service import build_persona_context, build_reply_spec
from app.services.reply_guard_service import finalize_reply_spec, guard_reply_spec


def _context(**updates) -> PersonaContext:
    values = {
        "persona_id": "orchid_sales",
        "persona_version": "v1",
        "soul": "温和、有判断。",
        "style": "微信短句。",
        "policy": "不编造业务事实。",
        "mode": "recommendation",
        "mode_instructions": ["说明适配理由。"],
        "anti_patterns": ["亲亲"],
    }
    values.update(updates)
    return PersonaContext(**values)


def test_persona_context_selects_mode_and_gates_memory():
    state = UserState(
        user_id="u1",
        session_id="s1",
        metadata={
            "sales_action": {"sales_action": "recommend_solution"},
            "profile": {
                "preference_summary": "偏爱有香味、易养的建兰",
                "ai_summary": "广西阳台养兰，新手",
                "pain_points": ["担心夏天烂根"],
            },
            "recent_turns": [
                {"session_id": "old", "role": "user", "content": "旧会话不应进入"},
                {"session_id": "s1", "role": "user", "content": "阳台上午有光"},
                {"session_id": "s1", "role": "assistant", "content": "可以先看通风"},
            ],
        },
    )
    context = build_persona_context(
        message=SimpleNamespace(message="想挑一盆好养有香味的"),
        user_state=state,
        intent=IntentResult(
            route="template_reply",
            primary_intent="product_recommendation",
            confidence=0.9,
        ),
    )

    assert context.mode == "recommendation"
    assert len(context.examples) <= 2
    assert len(context.relevant_memories) <= 4
    assert any(item.get("content") == "阳台上午有光" for item in context.relevant_memories)
    assert all(item.get("content") != "旧会话不应进入" for item in context.relevant_memories)
    assert all("user_id" not in item for item in context.relevant_memories)


def test_reply_spec_locks_verified_business_facts_and_preserves_delivery_fields():
    reply = FinalReply(
        answer="系统显示订单仍待付款。",
        answer_segments=["系统显示订单仍待付款。"],
        outbound_messages=[OutboundMessage(type="text", content="系统显示订单仍待付款。")],
        reply_type="template",
        route="template_reply",
    )
    plan = ReplyPlan(
        action="template_reply",
        reason="verified_order",
        business_facts=BusinessFacts(tool_state={"payment_status": "pending"}),
    )
    state = UserState(user_id="u1", metadata={"sales_action": {"reply_goal": "回答订单状态"}})

    spec = build_reply_spec(reply=reply, plan=plan, user_state=state)
    final = finalize_reply_spec(spec)

    assert spec.render_mode == "locked"
    assert spec.verified_facts["tool_state"] == {"payment_status": "pending"}
    assert final.answer_segments == reply.answer_segments
    assert final.outbound_messages == reply.outbound_messages


@pytest.mark.asyncio
async def test_persona_renderer_uses_system_role_and_renders_question_once(monkeypatch):
    from app.services import persona_renderer

    captured = {}

    async def generate_messages(messages, *, purpose, temperature):
        captured["messages"] = messages
        captured["purpose"] = purpose
        captured["temperature"] = temperature
        return {
            "answer": "这盆更适合明亮散射光。您家阳台上午有直射光吗？",
            "usage": {"completion_tokens": 12},
        }

    monkeypatch.setattr(persona_renderer, "generate_messages", generate_messages)
    monkeypatch.setattr(
        persona_renderer,
        "get_model_config",
        lambda purpose: SimpleNamespace(provider="deepseek", model="deepseek-chat"),
    )
    monkeypatch.setattr(
        persona_renderer,
        "get_settings",
        lambda: SimpleNamespace(persona_reply_enabled=True, persona_reply_temperature=0.3),
    )
    spec = ReplySpec(
        route="template_reply",
        reply_type="template",
        reply_goal="推荐适合阳台的兰花",
        suggested_copy="可以先看一盆好养的建兰。",
        answer_segments=["可以先看一盆好养的建兰。"],
        question_slot="阳台光照",
        metadata={"persona_original_copy": "可以先看一盆好养的建兰。"},
    )

    rendered = await persona_renderer.render_persona_reply(
        spec=spec,
        context=_context(relevant_memories=[{"kind": "preference", "content": "偏爱香花"}]),
        current_message="想挑一盆好养的",
    )

    assert captured["messages"][0]["role"] == "system"
    assert "温和、有判断" in captured["messages"][0]["content"]
    assert captured["messages"][1]["role"] == "user"
    assert captured["purpose"] == "persona"
    assert rendered.suggested_copy.count("？") == 1
    assert rendered.answer_segments == []
    assert rendered.metadata["persona"]["sales_action_rendered"] is True


def test_guard_rejects_customer_service_tone_and_removes_internal_fallback_copy():
    spec = ReplySpec(
        route="template_reply",
        reply_type="template",
        reply_goal="回应客户",
        suggested_copy="亲亲，您的问题非常好？",
        metadata={
            "persona_original_copy": "可以，先说说您更在意花香还是好养。",
            "persona": {"rendered": True, "sales_action_rendered": True},
        },
    )

    guarded = guard_reply_spec(spec=spec, context=_context())
    final = finalize_reply_spec(guarded)

    assert final.answer == "可以，先说说您更在意花香还是好养。"
    assert final.metadata["persona_guard"]["status"] == "fallback"
    assert final.metadata["persona"]["sales_action_rendered"] is False
    assert "persona_original_copy" not in final.metadata


@pytest.mark.asyncio
async def test_persona_model_config_preserves_system_and_user_messages(monkeypatch):
    from app.services import llm_service

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "自然回复"}}],
                "usage": {"completion_tokens": 2},
            }

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def post(self, url, headers, json):
            captured.update({"url": url, "headers": headers, "body": json})
            return FakeResponse()

    monkeypatch.setenv("PERSONA_LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("PERSONA_LLM_MODEL", "persona-model")
    monkeypatch.setenv("VOLCENGINE_API_KEY", "persona_test_key")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_service.httpx, "AsyncClient", FakeClient)
    messages = [
        {"role": "system", "content": "人格规则"},
        {"role": "user", "content": "客户消息"},
    ]

    try:
        result = await llm_service.generate_messages(
            messages,
            purpose="persona",
            temperature=0.3,
        )
    finally:
        get_settings.cache_clear()

    assert captured["body"]["model"] == "persona-model"
    assert captured["body"]["messages"] == messages
    assert captured["body"]["temperature"] == 0.3
    assert captured["headers"]["Authorization"] == "Bearer persona_test_key"
    assert result["answer"] == "自然回复"
