from app.schemas.policy import PolicyDecision
from app.schemas.tag import TagResult
from app.services.business_tag_prompt_service import get_business_tag_prompt_block_ids
from app.services.customer_level_service import (
    advanced_customer_level_from_labels,
    prompt_blocks_for_customer_level_labels,
)


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

    if tag.risk_level == "high" or tag.route == "human":
        return PolicyDecision(
            route="human",
            action="human",
            reason="tag_high_risk_to_human",
            original_route=tag.route,
            next_action="human_handoff",
            template_ids=["handoff_risk_high"],
        )

    if tag.intent == "orchid_care" and tag.segment == "beginner":
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="beginner_orchid_care_policy",
            original_route=tag.route,
            knowledge_base_ids=["kb_orchid_basic", "kb_care_faq"],
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

    if tag.intent == "orchid_care" and tag.segment == "advanced":
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="advanced_orchid_care_policy",
            original_route=tag.route,
            knowledge_base_ids=["kb_orchid_advanced", "kb_best_practices"],
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

    return PolicyDecision(
        route=tag.route,
        action=tag.route,
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
