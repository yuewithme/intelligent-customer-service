from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.persona import PersonaContext, ReplySpec
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.decisioning.schemas.reply_plan import BusinessFacts, ReplyPlan
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.services.persona_service import build_persona_context, build_reply_spec
from app.domains.decisioning.services.reply_builder import build_chitchat_reply
from app.domains.decisioning.services.reply_guard_service import finalize_reply_spec, guard_reply_spec


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
    assert context.persona_version == "v1.1"
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


def test_plain_template_uses_anchor_plus_persona_composition():
    reply = FinalReply(
        answer="我理解您会关注价格。",
        reply_type="template",
        route="template_reply",
    )
    plan = ReplyPlan(action="template_reply", reason="template_intent")
    state = UserState(
        user_id="u1",
        metadata={"sales_action": {"reply_goal": "回应价格顾虑"}},
    )

    spec = build_reply_spec(reply=reply, plan=plan, user_state=state)

    assert spec.render_mode == "persona"
    assert spec.composition_mode == "anchor_plus_persona"


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
        composition_mode="anchor_plus_persona",
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
    assert rendered.suggested_copy == "可以先看一盆好养的建兰。"
    assert rendered.persona_copy.count("？") == 1
    assert rendered.metadata["persona"]["sales_action_rendered"] is True
    final = finalize_reply_spec(guard_reply_spec(spec=rendered, context=_context()))
    assert final.answer_segments == [
        "可以先看一盆好养的建兰。",
        "这盆更适合明亮散射光。您家阳台上午有直射光吗？",
    ]
    assert final.metadata["emitted_question_slot"] == "阳台光照"


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


def test_guard_rejects_unsolicited_question_and_information_request():
    context = _context()
    question = ReplySpec(
        route="template_reply",
        reply_type="template",
        reply_goal="确认下一步",
        suggested_copy="我先核对库存。您这边要几盆？",
        metadata={"persona_original_copy": "我先核对当前库存，确认后再往下走。"},
    )
    request = question.model_copy(
        update={"suggested_copy": "你把下单手机号发我，我先核实。"}
    )

    guarded_question = guard_reply_spec(spec=question, context=context)
    guarded_request = guard_reply_spec(spec=request, context=context)

    assert guarded_question.metadata["persona_guard"]["reason"] == "unexpected_question"
    assert (
        guarded_request.metadata["persona_guard"]["reason"]
        == "unsolicited_information_request"
    )


def test_guard_rejects_unverified_product_facts_after_template_anchor():
    spec = ReplySpec(
        route="template_reply",
        reply_type="template",
        reply_goal="回应价格顾虑",
        composition_mode="anchor_plus_persona",
        suggested_copy="我理解您会关注价格。",
        persona_copy=(
            "郑州这边气候干燥，建议选带花苞的秋芝七仙女，"
            "开花后香气很浓。您的预算大概是多少？"
        ),
        question_slot="budget",
        metadata={"persona": {"rendered": True, "sales_action_rendered": True}},
    )

    guarded = guard_reply_spec(spec=spec, context=_context())

    assert guarded.persona_copy == ""
    assert guarded.metadata["persona_guard"]["reason"] == "unverified_fact_claim"
    assert guarded.metadata["persona"]["sales_action_rendered"] is False


def test_identity_question_has_role_first_fallback_copy():
    reply = build_chitchat_reply(
        IntentResult(
            route="chitchat",
            primary_intent="greeting",
            confidence=0.99,
            slots={"chitchat_kind": "identity_question"},
        )
    )

    assert "在线顾问" in reply.answer
    assert "智能客服" not in reply.answer
    assert "我是真人" not in reply.answer


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
    assert captured["body"]["max_tokens"] == 300
    assert captured["headers"]["Authorization"] == "Bearer persona_test_key"
    assert result["answer"] == "自然回复"


@pytest.mark.asyncio
async def test_dashscope_persona_disables_thinking(monkeypatch):
    from app.services import llm_service

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "自然回复"}}], "usage": {}}

    class FakeClient:
        def __init__(self, timeout):
            del timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb

        async def post(self, url, headers, json):
            del url, headers
            captured["body"] = json
            return FakeResponse()

    monkeypatch.setenv("PERSONA_LLM_PROVIDER", "dashscope")
    monkeypatch.setenv("PERSONA_LLM_MODEL", "qwen3.6-flash")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "persona_test_key")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_service.httpx, "AsyncClient", FakeClient)

    try:
        await llm_service.generate_messages(
            [{"role": "user", "content": "生成回复"}],
            purpose="persona",
        )
    finally:
        get_settings.cache_clear()

    assert captured["body"]["enable_thinking"] is False


@pytest.mark.asyncio
async def test_persona_generation_retries_one_transient_timeout(monkeypatch):
    import httpx

    from app.shared.schemas.common import AppError, ErrorCode
    from app.services import llm_service

    attempts = []

    async def fake_completion(**kwargs):
        attempts.append(kwargs["attempt"])
        if len(attempts) == 1:
            try:
                raise httpx.ReadTimeout("temporary")
            except httpx.ReadTimeout as cause:
                raise AppError(ErrorCode.LLM_FAILED, status_code=502) from cause
        return {
            "choices": [{"message": {"content": "第二次成功"}}],
            "usage": {},
        }

    monkeypatch.setattr(llm_service, "_chat_completion", fake_completion)
    monkeypatch.setattr(
        llm_service,
        "_model_config",
        lambda *args, **kwargs: llm_service.ModelConfig(
            provider="dashscope",
            model="persona-model",
        ),
    )

    result = await llm_service.generate_messages(
        [{"role": "user", "content": "生成自然回复"}],
        purpose="persona",
    )

    assert attempts == [1, 2]
    assert result["answer"] == "第二次成功"
