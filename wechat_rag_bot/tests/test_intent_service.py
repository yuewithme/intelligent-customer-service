import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.state import UserState
from app.schemas.intent import IntentResult
from app.services.intent_service import classify_intent


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="req_intent",
        channel="api",
        user_id="user_intent",
        session_id="sess_intent",
        message=text,
        kb_id="kb_default",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "route", "primary_intent"),
    [
        ("你好", "chitchat", "greeting"),
        ("这个多少钱？", "template_reply", "ask_price"),
        ("有点贵", "template_reply", "price_objection"),
        ("这个有点贵，而且我怕养不活", "template_then_rag", "price_objection"),
        ("兰花怎么养护？", "rag_answer", "care_question"),
        ("报销流程是什么？", "rag_answer", "process_question"),
        ("这个产品有什么注意事项？", "rag_answer", "knowledge_question"),
        ("怎么申请售后？", "rag_answer", "process_question"),
        ("什么时候发货？", "template_reply", "ask_logistics"),
        ("我要退款", "human", "refund_request"),
        ("我要投诉", "human", "complaint"),
        ("我要转人工", "human", "human_request"),
        ("乱七八糟不明确输入", "clarify", "unknown"),
        ("帮我写代码", "unsupported", "unsupported"),
    ],
)
async def test_rule_intent_classification_routes(text, route, primary_intent):
    intent = await classify_intent(_message(text), UserState(user_id="user_intent"))

    assert intent.route == route
    assert intent.primary_intent == primary_intent


@pytest.mark.asyncio
async def test_human_priority_beats_knowledge_question():
    intent = await classify_intent(
        _message("我要退款，怎么处理？"),
        UserState(user_id="user_intent"),
    )

    assert intent.route == "human"
    assert intent.primary_intent == "refund_request"
    assert intent.need_human is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "route", "primary_intent"),
    [
        ("老师，下一次浇水需要多少天？有朋友说要15天以上。", "rag_answer", "care_question"),
        ("这个多少钱？", "template_reply", "ask_price"),
    ],
)
async def test_duration_questions_do_not_match_price_intent(
    text, route, primary_intent
):
    intent = await classify_intent(_message(text), UserState(user_id="user_intent"))

    assert intent.route == route
    assert intent.primary_intent == primary_intent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "route", "primary_intent"),
    [
        ("名贵兰花怎么养护？", "rag_answer", "care_question"),
        ("我考虑换盆，需要注意什么？", "rag_answer", "knowledge_question"),
        ("有没有客服指导养护？", "rag_answer", "care_question"),
    ],
)
async def test_broad_sales_and_human_words_do_not_steal_knowledge_questions(
    text, route, primary_intent
):
    intent = await classify_intent(_message(text), UserState(user_id="user_intent"))

    assert intent.route == route
    assert intent.primary_intent == primary_intent


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "route", "primary_intent"),
    [
        ("这个好贵", "template_reply", "price_objection"),
        ("我再考虑一下", "template_reply", "price_objection"),
        ("帮我转客服", "human", "human_request"),
    ],
)
async def test_explicit_sales_and_human_requests_still_route_to_templates_or_human(
    text, route, primary_intent
):
    intent = await classify_intent(_message(text), UserState(user_id="user_intent"))

    assert intent.route == route
    assert intent.primary_intent == primary_intent


@pytest.mark.asyncio
async def test_llm_intent_is_primary_for_non_hard_rule_messages(monkeypatch):
    from app.config import get_settings
    from app.services import intent_service

    captured = {}

    async def fake_classify_by_llm(message, user_state, candidates=None):
        del user_state, candidates
        captured["message"] = message.message
        return IntentResult(
            route="rag_answer",
            primary_intent="care_question",
            sales_stage="pain_confirmed",
            confidence=0.91,
            need_rag=True,
            reason="llm_understood_care_question",
        )

    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", fake_classify_by_llm)

    try:
        intent = await classify_intent(
            _message("名贵兰花怎么养护？"),
            UserState(user_id="user_intent"),
        )
    finally:
        get_settings.cache_clear()

    assert captured["message"] == "名贵兰花怎么养护？"
    assert intent.route == "rag_answer"
    assert intent.primary_intent == "care_question"
    assert intent.reason == "llm_understood_care_question"


@pytest.mark.asyncio
async def test_hard_rules_bypass_llm_when_llm_intent_is_enabled(monkeypatch):
    from app.config import get_settings
    from app.services import intent_service

    async def fail_classify_by_llm(message, user_state, candidates=None):
        del message, user_state, candidates
        raise AssertionError("hard rules should not call LLM")

    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", fail_classify_by_llm)

    try:
        intent = await classify_intent(
            _message("我要退款，怎么处理？"),
            UserState(user_id="user_intent"),
        )
    finally:
        get_settings.cache_clear()

    assert intent.route == "human"
    assert intent.primary_intent == "refund_request"


def test_llm_prompt_requires_complete_intent_schema():
    from app.services.intent_service import _build_prompt

    prompt = _build_prompt("老师，下一次浇水需要多少天？")

    assert '"route"' in prompt
    assert '"primary_intent"' in prompt
    assert '"confidence"' in prompt
    assert '"need_rag"' in prompt
    assert "浇水需要多少天" in prompt
    assert "分类优先级" in prompt
    assert "重要边界" in prompt
    assert "confidence 规则" in prompt
    assert "名贵兰花" in prompt
    assert "换盆修根" in prompt
