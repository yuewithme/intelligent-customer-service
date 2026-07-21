from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from app.config import get_settings
from app.schemas.memory import MemoryEventCreate
from app.services.memory_event_service import append_memory_event, get_memory_event
from app.services.memory_identity_service import resolve_or_create_subject
from app.services.memory_job_service import enqueue_memory_job


_ROLE_MAP = {
    "user": ("customer_message", "customer"),
    "customer": ("customer_message", "customer"),
    "assistant": ("assistant_message", "assistant"),
    "human": ("human_message", "human_agent"),
}


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def dual_write_conversation_event(
    *,
    tenant_id: str,
    channel: str,
    external_user_id: str,
    owner_external_id: str,
    session_id: str | None,
    role: str,
    content: str,
    source_id: str,
    trace_id: str | None,
    occurred_at: datetime,
) -> int | None:
    """Append one idempotent raw event and enqueue its durable extraction job."""
    if not get_settings().memory_v2_write_enabled:
        return None
    role = role.strip().lower()
    if role not in _ROLE_MAP:
        raise ValueError(f"unsupported conversation role: {role}")
    tenant_id = _required(tenant_id, "tenant_id")
    channel = _required(channel, "channel")
    external_user_id = _required(external_user_id, "external_user_id")
    source_id = _required(source_id, "source_id")
    event_type, actor_type = _ROLE_MAP[role]
    subject = resolve_or_create_subject(
        tenant_id=tenant_id,
        channel=channel,
        owner_external_id=owner_external_id,
        external_user_id=external_user_id,
        identity_source="conversation_channel",
        verified=False,
    )
    uid_material = f"{tenant_id}\0{channel}\0{source_id}\0{role}"
    event_uid = "conversation:" + hashlib.sha256(
        uid_material.encode("utf-8")
    ).hexdigest()
    existing = get_memory_event(
        tenant_id=tenant_id,
        subject_id=subject.id,
        event_uid=event_uid,
    )
    effective_occurred_at = existing.occurred_at if existing else occurred_at
    effective_trace_id = existing.trace_id if existing else trace_id
    event = append_memory_event(
        MemoryEventCreate(
            event_uid=event_uid,
            tenant_id=tenant_id,
            subject_id=subject.id,
            session_id=session_id,
            event_type=event_type,
            actor_type=actor_type,
            content={"text": content},
            source_type="conversation_message",
            source_id=source_id,
            trace_id=effective_trace_id,
            occurred_at=(
                effective_occurred_at.replace(tzinfo=timezone.utc)
                if effective_occurred_at.tzinfo is None
                else effective_occurred_at.astimezone(timezone.utc)
            ),
            sensitivity="internal",
        )
    )
    enqueue_memory_job(
        tenant_id=tenant_id,
        subject_id=subject.id,
        trigger_event_id=event.event.id,
    )
    return event.event.id
