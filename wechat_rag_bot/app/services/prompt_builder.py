from app.schemas.prompt import PromptBuildInput


PROMPT_BLOCKS = {
    "base.customer_service": (
        "You are an intelligent customer service assistant. Answer only from the provided information, "
        "and do not fabricate facts."
    ),
    "scenario.orchid_care": (
        "The current scenario is orchid care consultation. Focus on care diagnosis, treatment steps, "
        "and risk boundaries."
    ),
    "intent.orchid_problem": (
        "The user is describing an orchid problem. Respond with care first, then provide actionable advice."
    ),
    "segment.beginner": (
        "The user is a beginner. Explain basic causes first, avoid complex terms, and provide clear steps."
    ),
    "segment.advanced": (
        "The user is experienced. Give key judgments, constraints, and advanced suggestions directly."
    ),
    "emotion.anxious": "The user feels anxious. Reassure them first, then explain the treatment direction.",
    "emotion.neutral": "The user is neutral. Keep the answer natural, clear, and concise.",
    "tone.patient_step_by_step": "Use a patient tone suitable for step-by-step instructions.",
    "tone.concise_professional": "Use a concise and professional tone without repeating basic concepts.",
    "output.customer_reply": (
        "Only output customer-facing service copy. Do not output internal tags, rules, or reasoning."
    ),
}


async def build_prompt(request: PromptBuildInput) -> str:
    sections: list[str] = []
    sections.extend(_render_prompt_blocks(request.prompt_block_ids))
    sections.append(_render_templates(request.templates))
    sections.append(_render_context(request.context))
    sections.append(_render_knowledge(request.knowledge_snippets))
    sections.append("User question:\n" + request.user_message.strip())
    return "\n\n".join(section for section in sections if section.strip())


def _render_prompt_blocks(block_ids: list[str]) -> list[str]:
    return [PROMPT_BLOCKS[block_id] for block_id in block_ids if block_id in PROMPT_BLOCKS]


def _render_templates(templates: list[str]) -> str:
    if not templates:
        return ""
    return "Available fixed scripts:\n" + "\n".join(f"- {template}" for template in templates)


def _render_context(context) -> str:
    parts = []
    if context.profile_summary:
        parts.append(f"User profile summary:\n{context.profile_summary}")
    if context.session_state:
        parts.append(f"Session state:\n{context.session_state}")
    if context.recent_turns:
        turns = "\n".join(f"{turn.get('role')}: {turn.get('content')}" for turn in context.recent_turns)
        parts.append(f"Recent conversation:\n{turns}")
    if context.long_memory_summary:
        parts.append(f"Long-memory summary:\n{context.long_memory_summary}")
    return "\n\n".join(parts)


def _render_knowledge(snippets: list[dict]) -> str:
    if not snippets:
        return ""
    blocks = []
    for index, snippet in enumerate(snippets, start=1):
        source = snippet.get("source") or snippet.get("file_name") or "unknown"
        text = snippet.get("text", "")
        blocks.append(f"[{index}] {source}\n{text}")
    return "Knowledge snippets:\n" + "\n\n".join(blocks)
