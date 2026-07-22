from app.domains.decisioning.schemas.reply_plan import BusinessFacts, DecisionStep, ReplyPlan
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.decisioning.services.reply_planner import resolve_reply_plan


def test_reply_plan_keeps_facts_constraints_and_trace_separate():
    plan = ReplyPlan(
        action="template_reply",
        original_route="clarify",
        reason="business_facts_available",
        business_facts=BusinessFacts(tool_state={"payment_status": "failed"}),
        decision_trace=[
            DecisionStep(
                source="base_policy",
                proposed_action="clarify",
                reason="low_confidence",
            ),
            DecisionStep(
                source="planner",
                proposed_action="template_reply",
                reason="business_facts_available",
            ),
        ],
    )

    assert plan.business_facts.tool_state == {"payment_status": "failed"}
    assert plan.decision_trace[-1].source == "planner"
    assert plan.model_dump()["action"] == "template_reply"


def _decision(route: str, reason: str, **updates) -> PolicyDecision:
    return PolicyDecision(route=route, reason=reason, **updates)


def test_explicit_human_requirement_has_highest_precedence():
    plan = resolve_reply_plan(
        base=_decision("human", "human_required", next_action="human_handoff"),
        tagged=_decision("rag_answer", "advanced_customer_level_professional_rag"),
        facts=BusinessFacts(snapshot="会员39.9元"),
    )

    assert plan.action == "human"
    assert plan.need_human is True
    assert plan.next_action == "human_handoff"


def test_specific_tag_policy_overrides_base_policy_once():
    plan = resolve_reply_plan(
        base=_decision("template_reply", "template_intent"),
        tagged=_decision(
            "rag_answer",
            "beginner_orchid_care_policy",
            knowledge_base_ids=["kb_orchid_basic"],
        ),
        facts=BusinessFacts(),
    )

    assert plan.action == "rag_answer"
    assert plan.knowledge_base_ids == ["kb_orchid_basic"]


def test_tag_policy_keeps_contextual_retrieval_mode_from_base_policy():
    plan = resolve_reply_plan(
        base=_decision(
            "rag_answer",
            "knowledge_intent",
            retrieval_policy={"mode": "product_recommendation"},
        ),
        tagged=_decision(
            "rag_answer",
            "advanced_customer_level_professional_rag",
            retrieval_policy={"focus": ["constraints"]},
        ),
        facts=BusinessFacts(),
    )

    assert plan.retrieval_policy == {
        "mode": "product_recommendation",
        "focus": ["constraints"],
    }


def test_business_facts_are_attached_without_overriding_a_human_route():
    plan = resolve_reply_plan(
        base=_decision("human", "human_required", next_action="human_handoff"),
        tagged=_decision(
            "human",
            "tag_high_risk_to_human",
            next_action="human_handoff",
        ),
        facts=BusinessFacts(tool_state={"order_status": "paid"}),
    )

    assert plan.action == "human"
    assert plan.business_facts.tool_state == {"order_status": "paid"}


def test_business_facts_replace_a_knowledge_route_with_grounded_execution():
    plan = resolve_reply_plan(
        base=_decision("rag_answer", "knowledge_intent"),
        tagged=_decision("rag_answer", "default_tag_policy"),
        facts=BusinessFacts(snapshot="会员39.9元"),
    )

    assert plan.action == "template_reply"
    assert plan.reason == "business_facts_available"


def test_compound_legacy_route_is_normalized_to_one_execution_action():
    plan = resolve_reply_plan(
        base=_decision("template_then_rag", "mixed_intent"),
        tagged=_decision("template_then_rag", "default_tag_policy"),
        facts=BusinessFacts(),
    )

    assert plan.action == "rag_answer"
    assert plan.original_route == "template_then_rag"
