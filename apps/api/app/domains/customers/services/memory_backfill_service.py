from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.infrastructure.database.models import (
    ConversationMemoryModel,
    EyunContactModel,
    MemoryEventModel,
    MemoryJobModel,
    UserProfileModel,
)
from app.domains.customers.schemas.memory import (
    MemoryEventCreate,
    MemoryOperationCandidate,
)
from app.domains.customers.services.memory_consolidation_service import (
    apply_memory_candidate,
)
from app.domains.customers.services.memory_event_service import append_memory_event
from app.domains.customers.services.memory_identity_service import (
    resolve_or_create_subject,
)
from app.domains.customers.services.memory_job_service import enqueue_memory_job
from app.domains.customers.services.memory_repository import get_memory_session


BACKFILL_VERSION = "legacy_memory_v1"
_ROLE_MAP = {
    "user": ("customer_message", "customer"),
    "customer": ("customer_message", "customer"),
    "assistant": ("assistant_message", "assistant"),
    "human": ("human_message", "human_agent"),
}


def run_legacy_memory_backfill(
    *,
    apply: bool,
    tenant_id: str | None = None,
    enqueue_jobs: bool = True,
) -> dict[str, Any]:
    """Backfill legacy profiles and turns without logging customer content."""
    profiles, conversations = _load_legacy_rows(tenant_id=tenant_id)
    profile_by_user = {row["user_id"]: row for row in profiles}
    stats: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "backfill_version": BACKFILL_VERSION,
        "profiles_seen": len(profiles),
        "profiles_with_owner_identity": sum(
            1
            for row in profiles
            if str(
                row.get("owner_external_id")
                or row.get("basic_info", {}).get("owner_wc_id")
                or ""
            ).strip()
        ),
        "profile_facts_planned": 0,
        "profile_events_created": 0,
        "profile_events_existing": 0,
        "facts_created": 0,
        "facts_existing": 0,
        "conversations_seen": len(conversations),
        "conversation_events_planned": 0,
        "conversation_events_created": 0,
        "conversation_events_existing": 0,
        "jobs_ensured": 0,
        "unsupported_roles": 0,
        "subjects_ensured": 0,
        "errors": 0,
        "error_types": {},
    }
    errors: Counter[str] = Counter()
    subject_ids: set[str] = set()

    for profile in profiles:
        facts = _profile_facts(profile)
        stats["profile_facts_planned"] += len(facts)
        if not apply:
            continue
        try:
            subject = _resolve_subject(profile)
            subject_ids.add(subject.id)
            if not facts:
                continue
            event = append_memory_event(
                _profile_snapshot_event(profile, subject.id, facts)
            )
            stats[
                "profile_events_created"
                if event.created
                else "profile_events_existing"
            ] += 1
            for fact_key, fact_value in facts:
                result = apply_memory_candidate(
                    tenant_id=profile["tenant_id"],
                    subject_id=subject.id,
                    candidate=MemoryOperationCandidate(
                        operation="ADD",
                        memory_kind="semantic_fact",
                        fact_key=fact_key,
                        fact_value=fact_value,
                        evidence_event_ids=[event.event.id],
                        source_type="legacy_profile",
                        valid_from=_as_utc(profile["updated_at"]),
                        confidence=0.6,
                        reason="legacy_profile_backfill",
                    ),
                    min_confidence=0.0,
                )
                stats["facts_created" if result.created else "facts_existing"] += 1
        except Exception as exc:  # noqa: BLE001
            errors[type(exc).__name__] += 1

    for conversation in conversations:
        role = str(conversation["role"] or "").strip().lower()
        if role not in _ROLE_MAP:
            stats["unsupported_roles"] += 1
            continue
        stats["conversation_events_planned"] += 1
        if not apply:
            continue
        try:
            profile = profile_by_user.get(conversation["user_id"])
            identity = _conversation_identity(conversation, profile)
            subject = _resolve_subject(identity)
            subject_ids.add(subject.id)
            event_type, actor_type = _ROLE_MAP[role]
            source_id = f"legacy_conversation_memory:{conversation['id']}"
            event_id = _matching_conversation_event_id(
                tenant_id=conversation["tenant_id"],
                subject_id=subject.id,
                session_id=conversation["session_id"],
                event_type=event_type,
                actor_type=actor_type,
                content=conversation["content"],
                occurred_at=conversation["created_at"],
            )
            if event_id is None:
                event = append_memory_event(
                    MemoryEventCreate(
                        event_uid=_event_uid(
                            "legacy_conversation",
                            conversation["tenant_id"],
                            str(conversation["id"]),
                        ),
                        tenant_id=conversation["tenant_id"],
                        subject_id=subject.id,
                        session_id=conversation["session_id"],
                        event_type=event_type,
                        actor_type=actor_type,
                        content={"text": conversation["content"]},
                        source_type="conversation_message",
                        source_id=source_id,
                        trace_id=conversation["trace_id"],
                        occurred_at=_as_utc(conversation["created_at"]),
                        sensitivity="internal",
                    )
                )
                event_id = event.event.id
                stats[
                    "conversation_events_created"
                    if event.created
                    else "conversation_events_existing"
                ] += 1
            else:
                stats["conversation_events_existing"] += 1
            if enqueue_jobs:
                if not _memory_job_exists(conversation["tenant_id"], event_id):
                    enqueue_memory_job(
                        tenant_id=conversation["tenant_id"],
                        subject_id=subject.id,
                        trigger_event_id=event_id,
                        dedup_key=f"legacy_extract:{event_id}",
                    )
                stats["jobs_ensured"] += 1
        except Exception as exc:  # noqa: BLE001
            errors[type(exc).__name__] += 1

    stats["subjects_ensured"] = len(subject_ids) if apply else len(profiles)
    stats["errors"] = sum(errors.values())
    stats["error_types"] = dict(sorted(errors.items()))
    return stats


def _load_legacy_rows(
    *, tenant_id: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    engine = create_engine(get_settings().database_url)
    with Session(engine) as session:
        profile_query = select(UserProfileModel)
        conversation_query = select(ConversationMemoryModel)
        contact_query = select(EyunContactModel)
        if tenant_id:
            profile_query = profile_query.where(UserProfileModel.tenant_id == tenant_id)
            conversation_query = conversation_query.where(
                ConversationMemoryModel.tenant_id == tenant_id
            )
            contact_query = contact_query.where(EyunContactModel.tenant_id == tenant_id)
        contact_owners = {}
        if inspect(engine).has_table(EyunContactModel.__tablename__):
            contact_owners = {
                (row.tenant_id, row.wc_id): row.current_w_id
                for row in session.scalars(contact_query)
                if str(row.current_w_id or "").strip()
            }
        profiles = [
            {
                "user_id": row.user_id,
                "tenant_id": row.tenant_id,
                "channel": row.channel,
                "basic_info": _json_object(row.basic_info_json),
                "owner_external_id": contact_owners.get(
                    (row.tenant_id, row.user_id), ""
                ),
                "product_interests": _json_list(row.product_interests_json),
                "pain_points": _json_list(row.pain_points_json),
                "preference_summary": row.preference_summary,
                "updated_at": row.updated_at,
            }
            for row in session.scalars(profile_query.order_by(UserProfileModel.user_id))
        ]
        conversations = [
            {
                "id": row.id,
                "user_id": row.user_id,
                "tenant_id": row.tenant_id,
                "session_id": row.session_id,
                "role": row.role,
                "content": row.content,
                "trace_id": row.trace_id,
                "created_at": row.created_at,
            }
            for row in session.scalars(
                conversation_query.order_by(ConversationMemoryModel.id)
            )
        ]
    engine.dispose()
    return profiles, conversations


def _profile_facts(profile: dict[str, Any]) -> list[tuple[str, Any]]:
    basic_info = profile["basic_info"]
    facts: list[tuple[str, Any]] = []
    display_name = _text(basic_info.get("nickname") or basic_info.get("remark_name"), 120)
    if display_name:
        facts.append(("identity.display_name", display_name))
    city = _text(basic_info.get("shipping_city"), 120)
    if city:
        facts.append(("location.region", {"city": city}))
    for value in profile["product_interests"]:
        label = _text(value, 256)
        if label:
            facts.append(("purchase.product_interest", {"name": label}))
    for value in profile["pain_points"]:
        detail = _text(value, 1000)
        if detail:
            facts.append(
                (
                    "service.pain_point",
                    {"topic": "legacy_profile", "detail": detail},
                )
            )
    preference = _text(profile["preference_summary"], 1000)
    if preference:
        facts.append(
            (
                "service.preference",
                {"topic": "legacy_profile", "value": preference},
            )
        )
    deduped: list[tuple[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for fact_key, value in facts:
        identity = (
            fact_key,
            json.dumps(value, ensure_ascii=False, sort_keys=True),
        )
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append((fact_key, value))
    return deduped


def _profile_snapshot_event(
    profile: dict[str, Any],
    subject_id: str,
    facts: list[tuple[str, Any]],
) -> MemoryEventCreate:
    snapshot_facts = [
        {"fact_key": key, "fact_value": value}
        for key, value in facts
    ]
    return MemoryEventCreate(
        event_uid=_event_uid(
            "legacy_profile",
            profile["tenant_id"],
            profile["channel"],
            profile["user_id"],
        ),
        tenant_id=profile["tenant_id"],
        subject_id=subject_id,
        session_id=None,
        event_type="contact_snapshot",
        actor_type="system",
        content={"migration_version": BACKFILL_VERSION, "facts": snapshot_facts},
        source_type="legacy_profile",
        source_id=f"legacy_user_profile:{profile['user_id']}",
        trace_id=None,
        occurred_at=_as_utc(profile["updated_at"]),
        sensitivity="internal",
    )


def _resolve_subject(identity: dict[str, Any]):
    return resolve_or_create_subject(
        tenant_id=str(identity["tenant_id"] or "tenant_default"),
        channel=str(identity.get("channel") or "legacy"),
        owner_external_id=str(
            identity.get("owner_external_id")
            or identity.get("basic_info", {}).get("owner_wc_id")
            or ""
        ),
        external_user_id=str(identity["user_id"]),
        identity_source="legacy_profile_backfill",
        verified=False,
    )


def _conversation_identity(
    conversation: dict[str, Any], profile: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        "user_id": conversation["user_id"],
        "tenant_id": conversation["tenant_id"],
        "channel": (profile or {}).get("channel") or "legacy",
        "basic_info": (profile or {}).get("basic_info") or {},
        "owner_external_id": (profile or {}).get("owner_external_id") or "",
    }


def _json_object(value: str | None) -> dict[str, Any]:
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def _json_list(value: str | None) -> list[Any]:
    try:
        result = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return result if isinstance(result, list) else []


def _matching_conversation_event_id(
    *,
    tenant_id: str,
    subject_id: str,
    session_id: str | None,
    event_type: str,
    actor_type: str,
    content: str,
    occurred_at: datetime | None,
) -> int | None:
    content_json = json.dumps(
        {"text": content},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with get_memory_session() as session:
        return session.scalar(
            select(MemoryEventModel.id).where(
                MemoryEventModel.tenant_id == tenant_id,
                MemoryEventModel.subject_id == subject_id,
                MemoryEventModel.session_id == session_id,
                MemoryEventModel.event_type == event_type,
                MemoryEventModel.actor_type == actor_type,
                MemoryEventModel.content_json == content_json,
                MemoryEventModel.occurred_at == _as_utc(occurred_at),
                MemoryEventModel.deleted_at.is_(None),
            )
        )


def _memory_job_exists(tenant_id: str, event_id: int) -> bool:
    with get_memory_session() as session:
        return (
            session.scalar(
                select(MemoryJobModel.id).where(
                    MemoryJobModel.tenant_id == tenant_id,
                    MemoryJobModel.trigger_event_id == event_id,
                )
            )
            is not None
        )


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _event_uid(prefix: str, *parts: str) -> str:
    material = "\0".join(str(part or "") for part in parts)
    return f"{prefix}:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
