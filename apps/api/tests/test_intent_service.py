import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.services.intent_service import (
    classify_by_fast_rule,
    classify_by_hard_rules,
    classify_intent,
    match_membership_product_request,
)


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
        ("你是真人还是机器人？", "chitchat", "greeting"),
        ("你到底是不是AI？", "chitchat", "greeting"),
        ("你是谁？", "chitchat", "greeting"),
        ("这个多少钱？", "template_reply", "ask_price"),
        ("有点贵", "template_reply", "price_objection"),
        ("这个有点贵，而且我怕养不活", "template_then_rag", "price_objection"),
        ("兰花怎么养护？", "rag_answer", "care_question"),
        ("报销流程是什么？", "rag_answer", "process_question"),
        ("这个产品有什么注意事项？", "rag_answer", "knowledge_question"),
        (
            "那你推荐一款好养的，最好有视频教学。",
            "template_reply",
            "product_query",
        ),
        ("家里盆和植料不够。", "rag_answer", "care_question"),
        ("怎么加入会员？", "template_reply", "product_query"),
        ("怎么申请售后？", "rag_answer", "process_question"),
        ("什么时候发货？", "template_reply", "ask_logistics"),
        ("已下单成功，请晚一天发货", "template_reply", "order_query"),
        ("我买过你家的兰花，有新手教程吗", "template_reply", "order_query"),
        ("我要退款", "human", "refund_request"),
        ("我要投诉", "human", "complaint"),
        ("我要转人工", "human", "human_request"),
        ("我要找真人客服", "human", "human_request"),
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
async def test_explicit_product_recommendation_keeps_catalog_capability():
    from app.domains.decisioning.services.policy_service import decide_route

    message = _message("那你推荐一款好养的，最好有视频教学。")
    state = UserState(user_id="user_intent")

    intent = await classify_intent(message, state)
    decision = await decide_route(intent, state, message)

    assert intent.classifier_source == "hard_rule"
    assert intent.primary_goal == "seek_help"
    assert intent.issues == ["product_selection"]
    assert intent.slots["conversation_topic"] == "product_recommendation"
    assert intent.route == "template_reply"
    assert intent.primary_intent == "product_query"
    assert decision.route == "template_reply"
    assert decision.reason == "local_product_catalog_query"
    assert decision.retrieval_policy == {}


@pytest.mark.asyncio
async def test_supply_shortage_stays_in_care_and_does_not_request_products():
    intent = await classify_intent(
        _message("家里盆和植料不够。"),
        UserState(user_id="user_intent"),
    )

    assert intent.classifier_source == "hard_rule"
    assert intent.primary_domain == "care"
    assert intent.issues == ["medium_repotting"]
    assert "product_keywords" not in intent.slots
    assert "product_request_kind" not in intent.slots
    assert intent.reason == "rule_orchid_supply_care_need"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "怎么加入会员？",
        "店里的会员多少钱？",
        "39.9元可以开通会员吗？",
    ],
)
async def test_membership_qualification_routes_to_local_product_catalog(text):
    intent = await classify_intent(
        _message(text),
        UserState(user_id="user_intent"),
    )

    assert intent.primary_domain == "product"
    assert intent.primary_goal == "seek_help"
    assert intent.issues == ["product_selection"]
    assert intent.slots["product_keywords"] == ["首单参与陪伴养兰客户"]
    assert intent.slots["product_request_kind"] == "membership"
    assert intent.reason == "fallback_membership_product_request"


def test_generic_service_complaint_is_not_treated_as_membership_purchase():
    assert match_membership_product_request("你们的服务怎么这么差？") is False


@pytest.mark.asyncio
async def test_low_confidence_llm_membership_context_is_executed_without_clarifying(
    monkeypatch,
):
    from app.core.config import get_settings
    from app.services import intent_service

    async def classify_membership(message, user_state, candidates=None):
        del message, user_state, candidates
        return IntentResult(
            route="template_reply",
            primary_intent="order_intent",
            primary_domain="commerce",
            primary_goal="transact",
            issues=["order_process", "price_value"],
            confidence=0.35,
            slots={
                "product_request_kind": "membership",
                "membership_question_kind": "combined",
            },
            reason="llm_contextual_membership_purchase",
        )

    state = UserState(
        user_id="user_intent",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "我们的陪伴养兰服务有视频课程和一对一指导。",
                }
            ]
        },
    )
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", classify_membership)

    try:
        intent = await classify_intent(
            _message("那你们的服务怎么进？多少钱？"),
            state,
        )
    finally:
        get_settings.cache_clear()

    assert intent.confidence == 0.35
    assert intent.route == "template_reply"
    assert intent.slots["product_request_kind"] == "membership"
    assert intent.slots["membership_question_kind"] == "combined"
    assert intent.reason == "llm_contextual_membership_purchase"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "你们有什么福利吗？买这个",
        "这个服务有什么好处？这个我就买了",
        "都包含什么？把购买链接发我",
    ],
)
async def test_explicit_membership_action_overrides_inferred_price_objection(
    monkeypatch,
    text,
):
    from app.core.config import get_settings
    from app.services import intent_service

    async def misclassified_objection(message, user_state, candidates=None):
        del message, user_state, candidates
        return IntentResult(
            route="template_reply",
            primary_intent="price_objection",
            primary_domain="commerce",
            primary_goal="express_objection",
            issues=["price_value"],
            confidence=0.9,
            slots={
                "product_request_kind": "membership",
                "membership_question_kind": "price",
                "decision_blocker": {
                    "type": "price",
                    "detail": "客户认为价格偏高",
                },
            },
            reason="llm_inferred_price_objection",
        )

    state = UserState(
        user_id="user_intent",
        metadata={
            "commerce_last_product_id": "membership-39",
            "commerce_last_product_kind": "membership",
            "recent_turns": [
                {"role": "assistant", "content": "这是陪伴养兰会员的商品卡片。"}
            ],
        },
    )
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", misclassified_objection)

    try:
        intent = await classify_intent(_message(text), state)
    finally:
        get_settings.cache_clear()

    assert intent.primary_intent == "order_intent"
    assert intent.primary_goal == "transact"
    assert intent.slots["product_request_kind"] == "membership"
    assert intent.slots["membership_question_kind"] == "combined"
    assert "decision_blocker" not in intent.slots
    assert intent.reason == "contextual_explicit_action_reconciled"


@pytest.mark.asyncio
async def test_opening_failure_history_reaches_llm_instead_of_product_profile_rule(
    monkeypatch,
):
    from app.core.config import get_settings
    from app.services import intent_service

    async def classify_care_failure(message, user_state, candidates=None):
        del message, user_state, candidates
        return IntentResult(
            route="rag_answer",
            primary_intent="care_question",
            primary_domain="care",
            primary_goal="ask_information",
            issues=["pain_outcome"],
            classifier_source="llm",
            confidence=0.9,
            need_rag=True,
        )

    state = UserState(
        user_id="user_intent",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "家里目前养了多少盆兰花？具体养了哪些品种？",
                }
            ]
        },
    )
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", classify_care_failure)

    try:
        intent = await classify_intent(
            _message("安徽省淮南市，目前还有四盆，死了买，买了死"),
            state,
        )
    finally:
        get_settings.cache_clear()

    assert intent.classifier_source == "llm"
    assert intent.primary_domain == "care"
    assert intent.primary_intent == "care_question"
    assert intent.reason != "opening_profile_answer"


@pytest.mark.asyncio
async def test_price_only_followup_reuses_selected_membership_product():
    state = UserState(
        user_id="user_intent",
        metadata={
            "commerce_last_product_keyword": "首单参与陪伴养兰客户 专享特惠链接",
            "commerce_last_product_kind": "membership",
        },
    )

    intent = await classify_intent(_message("39.9？"), state)

    assert intent.primary_intent == "product_query"
    assert intent.slots["product_keywords"] == [
        "首单参与陪伴养兰客户 专享特惠链接"
    ]
    assert intent.slots["product_request_kind"] == "membership"
    assert intent.reason == "contextual_selected_product_price"


@pytest.mark.asyncio
async def test_price_objection_has_no_empty_default_template():
    from app.domains.decisioning.services.template_service import select_template

    message = _message("河南郑州，我不想买太贵的。")
    intent = IntentResult(
        route="template_reply",
        primary_intent="price_objection",
        sales_stage="need_discovery",
        confidence=0.99,
        need_template=True,
    )

    template = await select_template(
        message,
        intent,
        UserState(user_id="user_intent"),
    )

    assert template is None


@pytest.mark.parametrize(
    ("text", "goal", "kind"),
    [
        ("您好🌹", "social", "greeting"),
        ("好的，谢谢您！", "social", "thanks"),
        ("再见", "end_conversation", "end"),
    ],
)
def test_pure_chitchat_bypasses_ai_with_high_confidence(text, goal, kind):
    intent = classify_by_hard_rules(text)

    assert intent is not None
    assert intent.primary_domain == "conversation"
    assert intent.primary_goal == goal
    assert intent.slots["chitchat_kind"] == kind
    assert intent.confidence == pytest.approx(0.99)


def test_greeting_with_business_question_is_not_pure_chitchat():
    assert classify_by_hard_rules("你好，这个多少钱？") is None


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
async def test_identity_question_is_annotated_for_persona_reply():
    intent = await classify_intent(
        _message("你是真人还是机器人？"),
        UserState(user_id="user_intent"),
    )

    assert intent.reason == "rule_identity_question"
    assert intent.slots["chitchat_kind"] == "identity_question"


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
async def test_explicit_product_image_request_uses_product_query_rule():
    intent = await classify_intent(
        _message("能给我发一下芽黄素的图片吗"),
        UserState(user_id="user_intent"),
    )

    assert intent.primary_intent == "product_query"
    assert intent.reason == "rule_product_image_request"


@pytest.mark.asyncio
async def test_product_gallery_request_uses_product_query_rule():
    intent = await classify_intent(
        _message("我想看刚才那款的图册"),
        UserState(user_id="user_intent"),
    )

    assert intent.primary_intent == "product_query"
    assert intent.reason == "rule_product_image_request"


@pytest.mark.asyncio
async def test_referential_purchase_link_request_uses_product_query_rule():
    intent = await classify_intent(
        _message("那我想买这个，链接在哪里？"),
        UserState(user_id="user_intent"),
    )

    assert intent.route == "template_reply"
    assert intent.primary_intent == "order_intent"
    assert intent.slots["purchase_entry_requested"] is True
    assert intent.need_human is False
    assert intent.reason == "rule_product_purchase_query"


@pytest.mark.asyncio
async def test_live_product_link_purchase_phrase_uses_product_query_rule():
    intent = await classify_intent(
        _message("给我发产品链接，我要买"),
        UserState(user_id="user_intent"),
    )

    assert intent.route == "template_reply"
    assert intent.primary_intent == "order_intent"
    assert intent.slots["purchase_entry_requested"] is True
    assert intent.need_human is False
    assert intent.reason == "rule_product_purchase_query"


def test_orchestrator_defers_purchase_language_to_contextual_llm():
    intent = classify_by_fast_rule("我想买忆香荷，怎么下单？")

    assert intent is None


@pytest.mark.asyncio
async def test_service_purchase_language_is_not_intercepted_by_product_rule(monkeypatch):
    from app.core.config import get_settings
    from app.services import intent_service

    async def classify_membership(message, user_state, candidates=None):
        del message, user_state, candidates
        return IntentResult(
            route="template_reply",
            primary_intent="order_intent",
            primary_domain="commerce",
            primary_goal="transact",
            issues=["order_process"],
            classifier_source="llm",
            confidence=0.82,
            slots={
                "product_request_kind": "membership",
                "membership_question_kind": "purchase",
            },
            reason="llm_contextual_membership_purchase",
        )

    state = UserState(
        user_id="user_intent",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "我们的陪伴养兰服务有视频课程和一对一指导。",
                }
            ]
        },
    )
    monkeypatch.setenv("INTENT_LLM_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(intent_service, "classify_by_llm", classify_membership)

    try:
        intent = await classify_intent(
            _message("那我要怎么买你们的服务？"),
            state,
        )
    finally:
        get_settings.cache_clear()

    assert intent.classifier_source == "llm"
    assert intent.slots["product_request_kind"] == "membership"
    assert intent.slots["membership_question_kind"] == "purchase"


@pytest.mark.asyncio
async def test_explicit_purchase_phrase_uses_order_intent_rule():
    intent = await classify_intent(
        _message("我想买红君荷"),
        UserState(user_id="user_intent"),
    )

    assert intent.route == "template_reply"
    assert intent.primary_intent == "order_intent"
    assert intent.need_human is False
    assert intent.reason == "rule_explicit_order_intent"


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
async def test_enabled_llm_precedes_soft_keyword_rules(monkeypatch):
    from app.core.config import get_settings
    from app.services import intent_service

    async def fake_classify_by_llm(message, user_state, candidates=None):
        del message, user_state, candidates
        return IntentResult(
            route="rag_answer",
            primary_intent="care_question",
            primary_domain="care_service",
            primary_goal="ask_information",
            issues=["watering_fertilizing"],
            confidence=0.91,
            need_rag=True,
            reason="llm_dgi",
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

    assert intent.route == "rag_answer"
    assert intent.primary_intent == "care_question"
    assert intent.reason == "llm_dgi"
    assert intent.primary_domain == "care_service"
    assert intent.issues == ["watering_fertilizing"]


@pytest.mark.asyncio
async def test_product_preference_followup_keeps_recommendation_context():
    from app.domains.decisioning.services.policy_service import decide_route

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

    assert intent.route == "template_reply"
    assert intent.primary_intent == "product_query"
    assert intent.slots["conversation_topic"] == "product_recommendation"
    assert intent.reason == "rule_product_recommendation_request"
    assert decision.route == "template_reply"
    assert decision.reason == "local_product_catalog_query"
    assert decision.retrieval_policy == {}


@pytest.mark.asyncio
async def test_opening_profile_does_not_treat_beginner_as_a_region():
    state = UserState(
        user_id="user_intent",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "家里目前养了多少盆兰花？具体养了哪些品种？",
                }
            ]
        },
    )

    intent = await classify_intent(_message("家里有十来盆建兰，我是新手"), state)

    assert intent.primary_intent == "profile_answer"
    assert intent.slots["plant_count"] == 10
    assert intent.slots["owned_varieties"] == ["建兰"]
    assert "region" not in intent.slots


@pytest.mark.asyncio
async def test_opening_profile_question_does_not_swallow_a_care_incident():
    state = UserState(
        user_id="user_intent",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "家里目前养了多少盆兰花？具体养了哪些品种？",
                }
            ]
        },
    )

    intent = await classify_intent(
        _message("狗碰烂了一盆，发现黑根空根"),
        state,
    )

    assert intent.primary_intent == "care_question"
    assert intent.reason != "opening_profile_answer"


@pytest.mark.asyncio
async def test_deferred_photo_followup_ends_without_sales_discovery():
    intent = await classify_intent(
        _message("我下班后拍给你看"),
        UserState(user_id="user_intent"),
    )

    assert intent.primary_domain == "conversation"
    assert intent.primary_goal == "end_conversation"
    assert intent.slots["chitchat_kind"] == "defer"


@pytest.mark.asyncio
async def test_first_turn_recommendation_bypasses_rag_for_local_catalog():
    from app.domains.decisioning.schemas.intent import IntentResult
    from app.domains.decisioning.services.policy_service import decide_route

    state = UserState(user_id="user_intent")
    message = _message("推荐几款香味浓、性价比高的兰花")
    decision = await decide_route(
        IntentResult(
            route="rag_answer",
            primary_intent="knowledge_question",
            confidence=0.9,
            need_rag=True,
        ),
        state,
        message,
    )

    assert decision.route == "template_reply"
    assert decision.reason == "local_product_catalog_query"
    assert decision.retrieval_policy == {}


@pytest.mark.asyncio
async def test_hard_rules_bypass_llm_when_llm_intent_is_enabled(monkeypatch):
    from app.core.config import get_settings
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
async def test_price_scope_does_not_become_purchase_rejection():
    intent = await classify_intent(
        _message("河南郑州，我不想买太贵的"),
        UserState(user_id="user_intent"),
    )

    assert intent.primary_intent == "price_objection"
    assert intent.reason == "rule_explicit_price_objection"


@pytest.mark.asyncio
async def test_membership_rejection_wins_before_membership_product_routing():
    intent = await classify_intent(
        _message("会员我暂时不买了，也不用发链接。"),
        UserState(user_id="user_intent"),
    )

    assert intent.primary_intent == "purchase_rejection"
    assert intent.primary_domain == "commerce"
    assert "product_request_kind" not in intent.slots
    assert intent.reason == "rule_purchase_rejection"


@pytest.mark.asyncio
async def test_shipping_change_request_enters_order_tool_chain():
    intent = await classify_intent(
        _message("已下单成功，不过周末回老家，请晚一天发货"),
        UserState(user_id="user_intent"),
    )

    assert intent.primary_intent == "order_query"
    assert intent.slots["order_action"] == "shipping_date_change"
    assert intent.reason == "rule_order_service_action"


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


def test_llm_prompt_uses_compact_classification_schema():
    from app.domains.decisioning.services.intent_service import _build_prompt

    prompt = _build_prompt("老师，下一次浇水需要多少天？")

    assert '"primary_domain"' in prompt
    assert '"primary_goal"' in prompt
    assert '"issues"' in prompt
    assert '"scope"' in prompt
    assert '"confidence"' in prompt
    assert '"slots"' in prompt
    assert '"evidence":' not in prompt
    assert '"sales_signals":' not in prompt
    assert '"route":' not in prompt
    assert "Output JSON only" in prompt
    assert "after_sale" in prompt
    assert "material_resource" in prompt
    assert len(prompt) < 3200


def test_llm_intent_cannot_self_confirm_a_purchase():
    from app.domains.decisioning.services.intent_service import _validated_intent

    intent = _validated_intent(
        {
            "route": "template_reply",
            "primary_intent": "payment_success",
            "confidence": 0.9,
            "sales_signals": ["purchased", "payment_claimed", "not_a_signal"],
        }
    )

    assert intent.sales_signals == ["payment_claimed"]
