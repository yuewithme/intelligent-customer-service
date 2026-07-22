from app.domains.conversations.schemas.context import ContextPackage, ContextSelectionInput


async def select_context(request: ContextSelectionInput) -> ContextPackage:
    recent_turns_count = int(request.context_policy.get("recent_turns", 4))
    include_profile = bool(request.context_policy.get("include_profile_summary", True))
    include_long_summary = bool(request.context_policy.get("include_long_memory_summary", False))

    profile_summary = _profile_summary(request.profile) if include_profile else {}
    session_state = {
        "sales_stage": request.state.get("sales_stage", "unknown"),
        "risk_level": request.state.get("risk_level", "normal"),
        "last_intent": request.state.get("last_intent"),
        "last_route": request.state.get("last_route"),
        "sales_action": request.state.get("metadata", {}).get("sales_action"),
        "known_contact_fields": _known_contact_fields(request.profile),
    }
    recent_turns = request.memories[-recent_turns_count:] if recent_turns_count > 0 else []
    long_memory_summary = _long_memory_summary(request.profile) if include_long_summary else ""
    return ContextPackage(
        profile_summary=profile_summary,
        session_state=session_state,
        recent_turns=recent_turns,
        long_memory_summary=long_memory_summary,
        memory_facts=list(request.memory_context.get("current_facts") or []),
        verified_business_facts=list(
            request.memory_context.get("verified_business_facts") or []
        ),
        relevant_episodes=list(
            request.memory_context.get("relevant_episodes") or []
        ),
        unresolved_memory_conflicts=list(
            request.memory_context.get("unresolved_conflicts") or []
        ),
        memory_unknowns=list(request.memory_context.get("unknowns") or []),
    )


def _profile_summary(profile: dict) -> dict:
    basic_info = profile.get("basic_info") if isinstance(profile.get("basic_info"), dict) else {}
    safe_basic_info = {
        key: str(basic_info.get(key))[:120]
        for key in ("nickname", "remark_name")
        if basic_info.get(key) not in (None, "", [])
    }
    values = {
        "basic_info": safe_basic_info,
        "ai_summary": profile.get("ai_summary"),
        "preference_summary": profile.get("preference_summary"),
        "pain_points": profile.get("pain_points", []),
        "customer_tags": profile.get("customer_tags", []),
        "product_interests": profile.get("product_interests", []),
        "active_opportunity": profile.get("active_opportunity", {}),
    }
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def _known_contact_fields(profile: dict) -> list[str]:
    basic_info = profile.get("basic_info") if isinstance(profile, dict) else None
    if not isinstance(basic_info, dict):
        return []
    return [
        key
        for key in ("recipient_name", "mobile", "shipping_address", "shipping_city")
        if basic_info.get(key) not in (None, "", [])
    ]


def _long_memory_summary(profile: dict) -> str:
    parts = []
    if profile.get("ai_summary"):
        parts.append(profile["ai_summary"])
    if profile.get("preference_summary"):
        parts.append(profile["preference_summary"])
    pain_points = profile.get("pain_points") or []
    if pain_points:
        parts.append("Pain points: " + "; ".join(pain_points))
    return "\n".join(parts)
