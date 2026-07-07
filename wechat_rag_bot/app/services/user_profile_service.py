import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ConversationMemoryModel,
    ProfileEventModel,
    UserProfileModel,
)


_sessionmakers: dict[str, sessionmaker] = {}
_profile_tables = [
    UserProfileModel.__table__,
    ConversationMemoryModel.__table__,
    ProfileEventModel.__table__,
]
_allowed_patch_fields = {
    "current_stage",
    "risk_level",
    "customer_tags",
    "product_interests",
    "ai_summary",
    "preference_summary",
    "pain_points",
    "is_human_handoff",
    "human_ticket_id",
    "human_handoff_status",
    "human_handoff_reason",
}


async def get_profile_bundle(user_id: str) -> dict:
    with _get_session() as session:
        profile = _get_or_create_profile(session, user_id)
        session.commit()
        return {
            "profile": _profile_to_dict(profile),
            "recent_memories": _list_memories(session, user_id, 10),
            "events": _list_events(session, user_id, 20),
        }


async def patch_user_profile(user_id: str, updates: dict) -> dict:
    metadata = updates.get("metadata") if isinstance(updates.get("metadata"), dict) else {}
    reason = metadata.get("reason") or "manual_patch"
    with _get_session() as session:
        profile = _get_or_create_profile(session, user_id)
        before = _profile_to_dict(profile)
        for field, value in updates.items():
            if field not in _allowed_patch_fields:
                continue
            _set_profile_field(profile, field, value)
        profile.updated_at = _now()
        after = _profile_to_dict(profile)
        changed_before, changed_after = _changed_fields(before, after)
        if changed_after:
            session.add(
                ProfileEventModel(
                    user_id=profile.user_id,
                    tenant_id=profile.tenant_id,
                    event_type="profile_patched",
                    before_json=_json_dumps(changed_before),
                    after_json=_json_dumps(changed_after),
                    reason=str(reason),
                    trace_id=metadata.get("trace_id"),
                    created_at=_now(),
                )
            )
        session.commit()
        return _profile_to_dict(profile)


async def get_recent_memories(user_id: str, limit: int = 10) -> dict:
    limit = _clamp_limit(limit, default=10, maximum=50)
    with _get_session() as session:
        _get_or_create_profile(session, user_id)
        session.commit()
        return {"items": _list_memories(session, user_id, limit), "limit": limit}


async def get_profile_events(user_id: str, limit: int = 20) -> dict:
    limit = _clamp_limit(limit, default=20, maximum=100)
    with _get_session() as session:
        _get_or_create_profile(session, user_id)
        session.commit()
        return {"items": _list_events(session, user_id, limit), "limit": limit}


async def append_conversation_memory(
    *,
    user_id: str,
    tenant_id: str = "tenant_default",
    session_id: str | None,
    role: str,
    content: str,
    intent: str | None = None,
    route: str | None = None,
    template_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    if not content:
        return
    with _get_session() as session:
        _get_or_create_profile(session, user_id, tenant_id=tenant_id)
        session.add(
            ConversationMemoryModel(
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                role=role,
                content=content,
                intent=intent,
                route=route,
                template_id=template_id,
                trace_id=trace_id,
                created_at=_now(),
            )
        )
        session.commit()


async def update_profile_after_chat(message, intent, reply) -> None:
    with _get_session() as session:
        profile = _get_or_create_profile(
            session,
            message.user_id,
            tenant_id=message.tenant_id,
            channel=message.channel,
        )
        before = _profile_to_dict(profile)
        if getattr(intent, "sales_stage", None) and intent.sales_stage != "unknown":
            profile.current_stage = intent.sales_stage
        profile.last_intent = intent.primary_intent
        profile.last_route = reply.route
        profile.last_template_id = reply.template_id
        profile.last_active_at = _now()
        _apply_tag_result(profile, reply.metadata.get("tag_result"))
        profile.ai_summary = _build_overall_memory(profile, message, intent)
        profile.updated_at = _now()
        if reply.route == "human" or reply.need_human:
            handoff = reply.metadata.get("handoff", {})
            profile.is_human_handoff = True
            profile.human_ticket_id = handoff.get("ticket_id")
            profile.human_handoff_status = "pending"
            profile.human_handoff_reason = (
                handoff.get("reason")
                or getattr(intent, "reason", None)
                or reply.metadata.get("reason")
                or "human_route"
            )
            _add_event(
                session,
                profile,
                "handoff_created",
                before,
                _profile_to_dict(profile),
                profile.human_handoff_reason,
                message.trace_id,
            )
        session.commit()


def _apply_tag_result(profile: UserProfileModel, tag_result: Any) -> None:
    if not isinstance(tag_result, dict):
        return
    labels = _string_list(tag_result.get("labels"))
    if not labels:
        return
    customer_tags = _json_loads(profile.customer_tags_json, [])
    product_interests = _json_loads(profile.product_interests_json, [])
    pain_points = _json_loads(profile.pain_points_json, [])
    for label in labels:
        if label.startswith("product_interest:"):
            product_interests = _append_unique(product_interests, label.split(":", 1)[1])
        elif label.startswith("pain_point:"):
            pain_points = _append_unique(pain_points, label.split(":", 1)[1])
            customer_tags = _append_unique(customer_tags, label)
        elif label.startswith("customer_tag:"):
            customer_tags = _append_unique(customer_tags, label.split(":", 1)[1])
        else:
            customer_tags = _append_unique(customer_tags, label)
    profile.customer_tags_json = _json_dumps(customer_tags)
    profile.product_interests_json = _json_dumps(product_interests)
    profile.pain_points_json = _json_dumps(pain_points)
    risk_level = tag_result.get("risk_level")
    if isinstance(risk_level, str) and risk_level.strip():
        profile.risk_level = risk_level.strip()


def _build_overall_memory(profile: UserProfileModel, message, intent) -> str:
    tags = _json_loads(profile.customer_tags_json, [])
    interests = _json_loads(profile.product_interests_json, [])
    pain_points = _json_loads(profile.pain_points_json, [])
    region = _label_value(tags, "region")
    budget = _label_value(tags, "budget")
    issue = pain_points[0] if pain_points else _label_value(tags, "pain_point")
    interest = interests[0] if interests else ""
    facts: list[str] = []
    if region:
        facts.append(f"客户在{region}")
    if budget:
        facts.append(f"预算约{budget}元")
    if issue:
        facts.append(f"正在咨询{issue}处理")
    if facts:
        first = "，".join(facts)
    else:
        first = f"客户最近在咨询{getattr(intent, 'primary_intent', '') or '问题'}"
    if interest:
        second = f"整体看更关注{interest}问题，适合给出分步骤、可执行的养护建议。"
    else:
        second = "整体看需要保留最近诉求，并在后续回复中延续上下文。"
    return f"{first}；{second}"


def _label_value(labels: list[str], prefix: str) -> str:
    marker = f"{prefix}:"
    for label in labels:
        if isinstance(label, str) and label.startswith(marker):
            return label.split(":", 1)[1]
    return ""


def _append_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        return [*values, value]
    return values


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_profile_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def _get_or_create_profile(
    session: Session,
    user_id: str,
    *,
    tenant_id: str = "tenant_default",
    channel: str = "api",
) -> UserProfileModel:
    profile = session.scalar(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    if profile is None:
        now = _now()
        profile = UserProfileModel(
            user_id=user_id,
            tenant_id=tenant_id or "tenant_default",
            channel=channel or "api",
            current_stage="unknown",
            risk_level="normal",
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
    return profile


def _list_memories(session: Session, user_id: str, limit: int) -> list[dict]:
    rows = session.scalars(
        select(ConversationMemoryModel)
        .where(ConversationMemoryModel.user_id == user_id)
        .order_by(ConversationMemoryModel.created_at.desc(), ConversationMemoryModel.id.desc())
        .limit(limit)
    ).all()
    return [_memory_to_dict(row) for row in reversed(rows)]


def _list_events(session: Session, user_id: str, limit: int) -> list[dict]:
    rows = session.scalars(
        select(ProfileEventModel)
        .where(ProfileEventModel.user_id == user_id)
        .order_by(ProfileEventModel.created_at.desc(), ProfileEventModel.id.desc())
        .limit(limit)
    ).all()
    return [_event_to_dict(row) for row in rows]


def _set_profile_field(profile: UserProfileModel, field: str, value: Any) -> None:
    if field == "customer_tags":
        profile.customer_tags_json = _json_dumps(_string_list(value))
    elif field == "product_interests":
        profile.product_interests_json = _json_dumps(_string_list(value))
    elif field == "pain_points":
        profile.pain_points_json = _json_dumps(_string_list(value))
    elif hasattr(profile, field):
        setattr(profile, field, value)


def _profile_to_dict(profile: UserProfileModel) -> dict:
    return {
        "user_id": profile.user_id,
        "tenant_id": profile.tenant_id,
        "channel": profile.channel,
        "current_stage": profile.current_stage,
        "risk_level": profile.risk_level,
        "is_human_handoff": profile.is_human_handoff,
        "human_ticket_id": profile.human_ticket_id,
        "human_handoff_status": profile.human_handoff_status,
        "human_handoff_reason": profile.human_handoff_reason,
        "customer_tags": _json_loads(profile.customer_tags_json, []),
        "product_interests": _json_loads(profile.product_interests_json, []),
        "ai_summary": profile.ai_summary,
        "preference_summary": profile.preference_summary,
        "pain_points": _json_loads(profile.pain_points_json, []),
        "last_intent": profile.last_intent,
        "last_route": profile.last_route,
        "last_template_id": profile.last_template_id,
        "last_active_at": _datetime_to_iso(profile.last_active_at),
        "created_at": _datetime_to_iso(profile.created_at),
        "updated_at": _datetime_to_iso(profile.updated_at),
    }


def _memory_to_dict(row: ConversationMemoryModel) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "tenant_id": row.tenant_id,
        "session_id": row.session_id,
        "role": row.role,
        "content": row.content,
        "intent": row.intent,
        "route": row.route,
        "template_id": row.template_id,
        "trace_id": row.trace_id,
        "created_at": _datetime_to_iso(row.created_at),
    }


def _event_to_dict(row: ProfileEventModel) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "tenant_id": row.tenant_id,
        "event_type": row.event_type,
        "before": _json_loads(row.before_json, {}),
        "after": _json_loads(row.after_json, {}),
        "reason": row.reason,
        "trace_id": row.trace_id,
        "created_at": _datetime_to_iso(row.created_at),
    }


def _add_event(
    session: Session,
    profile: UserProfileModel,
    event_type: str,
    before: dict,
    after: dict,
    reason: str | None,
    trace_id: str | None,
) -> None:
    changed_before, changed_after = _changed_fields(before, after)
    if not changed_after:
        return
    session.add(
        ProfileEventModel(
            user_id=profile.user_id,
            tenant_id=profile.tenant_id,
            event_type=event_type,
            before_json=_json_dumps(changed_before),
            after_json=_json_dumps(changed_after),
            reason=reason,
            trace_id=trace_id,
            created_at=_now(),
        )
    )


def _changed_fields(before: dict, after: dict) -> tuple[dict, dict]:
    changed_before = {}
    changed_after = {}
    for key, value in after.items():
        if before.get(key) != value:
            changed_before[key] = before.get(key)
            changed_after[key] = value
    return changed_before, changed_after


def _clamp_limit(value: int, *, default: int, maximum: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximum))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)
