from app.schemas.policy import PolicyDecision
from app.schemas.reply_plan import BusinessFacts, DecisionStep, ReplyPlan


def _execution_action(route: str) -> str:
    if route == "template_then_rag":
        return "rag_answer"
    return route


def resolve_reply_plan(
    *,
    base: PolicyDecision,
    tagged: PolicyDecision,
    facts: BusinessFacts,
) -> ReplyPlan:
    trace = [
        DecisionStep(
            source="base_policy",
            proposed_action=base.route,
            reason=base.reason or "base_policy",
        ),
        DecisionStep(
            source="tag_policy",
            proposed_action=tagged.route,
            reason=tagged.reason or "tag_policy",
            accepted=tagged.reason != "default_tag_policy",
        ),
    ]

    if base.route == "human" or tagged.route == "human":
        selected = tagged if tagged.route == "human" else base
        action = "human"
    elif tagged.reason != "default_tag_policy":
        selected = tagged
        action = _execution_action(tagged.route)
    else:
        selected = base
        action = _execution_action(base.route)

    trace.append(
        DecisionStep(
            source="planner",
            proposed_action=action,
            reason=selected.reason or "selected_policy",
        )
    )
    return ReplyPlan(
        action=action,
        original_route=selected.original_route or base.original_route or base.route,
        reason=selected.reason or "selected_policy",
        need_human=action == "human",
        next_action="human_handoff" if action == "human" else selected.next_action,
        knowledge_base_ids=list(selected.knowledge_base_ids),
        template_ids=list(selected.template_ids),
        prompt_block_ids=list(selected.prompt_block_ids),
        context_policy=dict(selected.context_policy),
        retrieval_policy=dict(selected.retrieval_policy),
        business_facts=facts,
        decision_trace=trace,
    )
