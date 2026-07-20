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
        ("不要再给我推荐产品了", "template_reply", "purchase_rejection"),
        ("收到后花盆碎了，苗也歪了", "template_reply", "ask_after_sale"),
        ("我把身份证号、详细地址和电话都发群里了", "template_reply", "order_intent"),
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
async def test_clear_soft_rule_bypasses_llm(monkeypatch):
    from app.config import get_settings
    from app.services import intent_service

    async def fail_classify_by_llm(message, user_state, candidates=None):
        del message, user_state, candidates
        raise AssertionError("clear existing soft rules should bypass the LLM")

    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", fail_classify_by_llm)

    try:
        intent = await classify_intent(
            _message("名贵兰花怎么养护？"),
            UserState(user_id="user_intent"),
        )
    finally:
        get_settings.cache_clear()

    assert intent.route == "rag_answer"
    assert intent.primary_intent == "care_question"
    assert intent.reason == "soft_rule_care"


@pytest.mark.asyncio
async def test_product_preference_followup_keeps_recommendation_context():
    from app.services.policy_service import decide_route

    state = UserState(
        user_id="user_intent",
        metadata={
            "recent_turns": [
                {"role": "user", "content": "国兰有什么推荐的"},
                {
                    "role": "assistant",
                    "content": "推荐小国魂和蕙兰。您更看重好养，还是花香？",
                },
            ]
        },
    )

    message = _message("我想找好养活的，我不是很懂这个怎么养")
    intent = await classify_intent(message, state)
    decision = await decide_route(intent, state, message)

    assert intent.route == "rag_answer"
    assert intent.primary_intent == "knowledge_question"
    assert intent.slots["conversation_topic"] == "product_recommendation"
    assert intent.reason == "contextual_product_preference"
    assert decision.retrieval_policy == {"mode": "product_recommendation"}


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


@pytest.mark.asyncio
async def test_price_objection_adds_structured_decision_blocker():
    intent = await classify_intent(
        _message("这个价格太贵了"),
        UserState(user_id="user_intent"),
    )

    assert intent.slots["decision_blocker"] == {
        "type": "price",
        "detail": "客户认为价格偏高",
    }


@pytest.mark.asyncio
async def test_repeated_question_complaint_adds_other_blocker():
    intent = await classify_intent(
        _message("你怎么一直问，我想直接看商品"),
        UserState(user_id="user_intent"),
    )

    assert intent.slots["decision_blocker"] == {
        "type": "other",
        "detail": "客户不愿继续回答重复问题，希望直接查看商品",
    }


def test_llm_prompt_requires_complete_intent_schema():
    from app.services.intent_service import _build_prompt

    prompt = _build_prompt("老师，下一次浇水需要多少天？")

    assert '"route"' in prompt
    assert '"primary_intent"' in prompt
    assert '"confidence"' in prompt
    assert '"need_rag"' in prompt
    assert "slots.decision_blocker" in prompt
    assert '"sales_signals"' in prompt
    assert "payment_claimed" in prompt
    assert "禁止输出 `purchased`" in prompt
    assert "price | trust | care_risk | product_fit | choice | timing | other" in prompt
    assert "浇水需要多少天" in prompt
    assert "分类优先级" in prompt
    assert "重要边界" in prompt
    assert "confidence 规则" in prompt
    assert "名贵兰花" in prompt
    assert "换盆修根" in prompt


def test_llm_intent_cannot_self_confirm_a_purchase():
    from app.services.intent_service import _validated_intent

    intent = _validated_intent(
        {
            "route": "template_reply",
            "primary_intent": "payment_success",
            "confidence": 0.9,
            "sales_signals": ["purchased", "payment_claimed", "not_a_signal"],
        }
    )

    assert intent.sales_signals == ["payment_claimed"]
