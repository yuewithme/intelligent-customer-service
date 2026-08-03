from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.sales.schemas.tag import TagResult
from app.domains.sales.services.business_tag_prompt_service import get_business_tag_prompt_block_ids
from app.domains.customers.services.customer_level_service import (
    advanced_customer_level_from_labels,
    prompt_blocks_for_customer_level_labels,
)


CARE_INTENTS = {"orchid_care", "care_question"}
EXPLICIT_HUMAN_INTENTS = {"complaint", "refund_request", "human_request"}


def _is_care_tag(tag: TagResult) -> bool:
    return tag.intent in CARE_INTENTS or any(
        label.startswith("pain_point:")
        or label == "product_interest:兰花养护"
        for label in tag.labels
    )


def _care_knowledge_base_ids(
    tag: TagResult,
    *,
    advanced: bool = False,
) -> list[str]:
    if advanced or tag.segment == "advanced":
        return ["kb_orchid_advanced", "kb_best_practices"]
    if any(label.startswith("pain_point:") for label in tag.labels):
        return ["kb_orchid_basic", "kb_best_practices"]
    return ["kb_orchid_basic"]


async def decide_policy(tag: TagResult) -> PolicyDecision:
    customer_level_prompt_blocks = prompt_blocks_for_customer_level_labels(tag.labels)
    business_tag_prompt_blocks = get_business_tag_prompt_block_ids(tag.labels)
    advanced_level = advanced_customer_level_from_labels(tag.labels)

    if advanced_level is not None and tag.route != "human" and tag.risk_level != "high":
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="advanced_customer_level_professional_rag",
            original_route=tag.route,
            knowledge_base_ids=(
                _care_knowledge_base_ids(tag, advanced=True)
                if _is_care_tag(tag)
                else []
            ),
            prompt_block_ids=[
                "base.customer_service",
                "tone.concise_professional",
                *customer_level_prompt_blocks,
                *business_tag_prompt_blocks,
                "output.customer_reply",
            ],
            context_policy={
                "recent_turns": 6,
                "include_profile_summary": True,
                "include_long_memory_summary": True,
            },
            retrieval_policy={},
        )

    if tag.risk_level == "high" or tag.intent in EXPLICIT_HUMAN_INTENTS:
        return PolicyDecision(
            route="human",
            action="human",
            reason="tag_high_risk_to_human",
            original_route=tag.route,
            next_action="human_handoff",
            template_ids=["handoff_risk_high"],
        )

    if _is_care_tag(tag) and tag.segment == "beginner":
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="beginner_orchid_care_policy",
            original_route=tag.route,
            knowledge_base_ids=_care_knowledge_base_ids(tag),
            template_ids=["opening_beginner_care"],
            prompt_block_ids=[
                "base.customer_service",
                "scenario.orchid_care",
                "intent.orchid_problem",
                "segment.beginner",
                "emotion.anxious" if tag.emotion == "anxious" else "emotion.neutral",
                "tone.patient_step_by_step",
                *customer_level_prompt_blocks,
                *business_tag_prompt_blocks,
                "output.customer_reply",
            ],
            context_policy={
                "recent_turns": 6,
                "include_profile_summary": True,
                "include_long_memory_summary": True,
            },
            retrieval_policy={
                "focus": ["basic_causes", "treatment_steps", "common_mistakes", "after_sales"],
                "exclude": ["advanced_breeding", "complex_chemical_ratios"],
            },
        )

    if _is_care_tag(tag) and tag.segment == "advanced":
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="advanced_orchid_care_policy",
            original_route=tag.route,
            knowledge_base_ids=_care_knowledge_base_ids(tag),
            template_ids=["opening_advanced_care"],
            prompt_block_ids=[
                "base.customer_service",
                "scenario.orchid_care",
                "intent.orchid_problem",
                "segment.advanced",
                "tone.concise_professional",
                *customer_level_prompt_blocks,
                *business_tag_prompt_blocks,
                "output.customer_reply",
            ],
            context_policy={
                "recent_turns": 4,
                "include_profile_summary": True,
                "include_long_memory_summary": False,
            },
            retrieval_policy={
                "focus": ["constraints", "advanced_treatment", "key_parameters", "best_practices"],
                "exclude": ["basic_concepts", "over_explanation"],
            },
        )

    if _is_care_tag(tag):
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="tagged_orchid_care_policy",
            original_route=tag.route,
            knowledge_base_ids=_care_knowledge_base_ids(tag),
            prompt_block_ids=[
                "base.customer_service",
                "scenario.orchid_care",
                "intent.orchid_problem",
                *customer_level_prompt_blocks,
                *business_tag_prompt_blocks,
                "output.customer_reply",
            ],
            context_policy={
                "recent_turns": 4,
                "include_profile_summary": True,
                "include_long_memory_summary": False,
            },
            retrieval_policy={
                "focus": ["symptoms", "safe_checks", "care_constraints"],
                "exclude": ["sales_copy", "unsupported_fixed_parameters"],
            },
        )

    default_route = "chitchat" if tag.route == "human" else tag.route
    return PolicyDecision(
        route=default_route,
        action=default_route,
        reason="default_tag_policy",
        original_route=tag.route,
        prompt_block_ids=[
            "base.customer_service",
            f"intent.{tag.intent}",
            f"segment.{tag.segment}",
            *customer_level_prompt_blocks,
            *business_tag_prompt_blocks,
            "output.customer_reply",
        ],
        context_policy={
            "recent_turns": 4,
            "include_profile_summary": True,
            "include_long_memory_summary": False,
        },
    )
