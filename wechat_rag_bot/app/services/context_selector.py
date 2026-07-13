from app.schemas.context import ContextPackage, ContextSelectionInput


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
    }
    recent_turns = request.memories[-recent_turns_count:] if recent_turns_count > 0 else []
    long_memory_summary = _long_memory_summary(request.profile) if include_long_summary else ""
    memory_facts = [
        {"fact_key": item.get("fact_key"), "value": item.get("value")}
        for item in request.sales_memory.get("facts", [])
        if isinstance(item, dict) and item.get("fact_key") and item.get("value") is not None
    ]
    unresolved_sales_events = [
        {"episode_type": item.get("episode_type"), "summary": item.get("summary")}
        for item in request.sales_memory.get("unresolved_episodes", [])
        if isinstance(item, dict) and item.get("episode_type") and item.get("summary")
    ]

    return ContextPackage(
        profile_summary=profile_summary,
        session_state=session_state,
        recent_turns=recent_turns,
        long_memory_summary=long_memory_summary,
        memory_facts=memory_facts,
        unresolved_sales_events=unresolved_sales_events,
    )


def _profile_summary(profile: dict) -> dict:
    return {
        "ai_summary": profile.get("ai_summary"),
        "preference_summary": profile.get("preference_summary"),
        "pain_points": profile.get("pain_points", []),
        "customer_tags": profile.get("customer_tags", []),
    }


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
