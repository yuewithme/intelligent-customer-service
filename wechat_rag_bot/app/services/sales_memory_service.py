import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    SalesMemoryEpisodeModel,
    UserMemoryFactModel,
    UserProfileModel,
)
from app.services.tag_catalog import TAG_CATEGORIES


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [
    UserProfileModel.__table__,
    UserMemoryFactModel.__table__,
    SalesMemoryEpisodeModel.__table__,
]
_source_rank = {
    "llm_inference": 1,
    "deterministic_signal": 2,
    "business_event": 3,
    "customer_message": 4,
}
_fact_keys_by_category = {
    "province": "customer.region",
    "orchid_quantity": "customer.plant_count",
    "favorite_orchid_type": "customer.preferred_variety",
    "customer_level": "customer.level",
}
_episode_type_by_intent = {
    "complaint": "complaint",
    "ask_after_sale": "after_sale",
    "refund_request": "after_sale",
    "order_intent": "order_intent",
    "payment_intent": "order_intent",
    "purchase_rejection": "purchase_rejection",
    "not_interested": "purchase_rejection",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def _normalized_value(value: Any) -> tuple[str, str]:
    value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
    normalized = " ".join(value.strip().lower().split()) if isinstance(value, str) else value_json
    return value_json, normalized


async def upsert_memory_fact(
    *,
    profile_user_id: str,
    tenant_id: str,
    fact_key: str,
    value: Any,
    source_kind: str,
    source_trace_id: str | None,
    confidence: float,
) -> dict:
    if source_kind not in _source_rank:
        raise ValueError(f"unsupported fact source: {source_kind}")
    now = _now()
    value_json, normalized = _normalized_value(value)
    with _get_session() as session:
        current = session.scalar(
            select(UserMemoryFactModel)
            .where(
                UserMemoryFactModel.profile_user_id == profile_user_id,
                UserMemoryFactModel.tenant_id == tenant_id,
                UserMemoryFactModel.fact_key == fact_key,
                UserMemoryFactModel.valid_to.is_(None),
            )
            .order_by(UserMemoryFactModel.valid_from.desc())
        )
        if current and current.normalized_value == normalized:
            current.confidence = max(current.confidence, confidence)
            current.last_observed_at = now
            current.updated_at = now
            session.commit()
            return _fact_to_dict(current)
        if current and _source_rank[source_kind] < _source_rank[current.source_kind]:
            return _fact_to_dict(current)
        previous_id = current.id if current else None
        if current:
            current.valid_to = now
            current.updated_at = now
        row = UserMemoryFactModel(
            profile_user_id=profile_user_id,
            tenant_id=tenant_id,
            fact_key=fact_key,
            value_json=value_json,
            normalized_value=normalized,
            source_kind=source_kind,
            source_trace_id=source_trace_id,
            confidence=max(0, min(float(confidence), 1)),
            valid_from=now,
            valid_to=None,
            last_observed_at=now,
            supersedes_fact_id=previous_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _fact_to_dict(row)


async def list_memory_facts(
    profile_user_id: str, *, current_only: bool = True
) -> list[dict]:
    with _get_session() as session:
        query = select(UserMemoryFactModel).where(
            UserMemoryFactModel.profile_user_id == profile_user_id
        )
        if current_only:
            query = query.where(UserMemoryFactModel.valid_to.is_(None))
        rows = session.scalars(query.order_by(UserMemoryFactModel.id.asc())).all()
        return [_fact_to_dict(row) for row in rows]


async def record_sales_episode(
    *,
    profile_user_id: str,
    tenant_id: str,
    episode_type: str,
    summary: str,
    source_trace_id: str,
    resolved: bool = False,
    importance: float = 0.8,
) -> dict:
    now = _now()
    with _get_session() as session:
        row = session.scalar(
            select(SalesMemoryEpisodeModel).where(
                SalesMemoryEpisodeModel.profile_user_id == profile_user_id,
                SalesMemoryEpisodeModel.episode_type == episode_type,
                SalesMemoryEpisodeModel.source_trace_id == source_trace_id,
            )
        )
        if row is None:
            row = SalesMemoryEpisodeModel(
                profile_user_id=profile_user_id,
                tenant_id=tenant_id,
                episode_type=episode_type,
                summary=_sanitize_summary(summary),
                source_trace_id=source_trace_id,
                importance=max(0, min(float(importance), 1)),
                confidence=1.0,
                resolved=resolved,
                occurred_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
        return _episode_to_dict(row)


async def list_unresolved_sales_episodes(profile_user_id: str) -> list[dict]:
    with _get_session() as session:
        rows = session.scalars(
            select(SalesMemoryEpisodeModel)
            .where(
                SalesMemoryEpisodeModel.profile_user_id == profile_user_id,
                SalesMemoryEpisodeModel.resolved.is_(False),
            )
            .order_by(
                SalesMemoryEpisodeModel.importance.desc(),
                SalesMemoryEpisodeModel.occurred_at.desc(),
            )
        ).all()
        return [_episode_to_dict(row) for row in rows]


async def apply_deterministic_sales_memory(message, intent, reply) -> None:
    labels = (reply.metadata.get("tag_result") or {}).get("labels") or []
    for label in labels:
        fact = _fact_from_label(str(label))
        if fact:
            await upsert_memory_fact(
                profile_user_id=message.user_id,
                tenant_id=message.tenant_id,
                fact_key=fact[0],
                value=fact[1],
                source_kind="deterministic_signal",
                source_trace_id=message.trace_id,
                confidence=0.95,
            )
    episode_type = _episode_type_by_intent.get(intent.primary_intent)
    if episode_type:
        await record_sales_episode(
            profile_user_id=message.user_id,
            tenant_id=message.tenant_id,
            episode_type=episode_type,
            summary=message.message,
            source_trace_id=message.trace_id,
            resolved=episode_type == "purchase_rejection",
            importance=1.0 if episode_type in {"complaint", "after_sale"} else 0.8,
        )


def _fact_from_label(label: str) -> tuple[str, str] | None:
    value = label.split(":", 1)[1] if ":" in label else label
    for category_id, category in TAG_CATEGORIES.items():
        if any(item.name == value for item in category.values):
            fact_key = _fact_keys_by_category.get(category_id)
            return (fact_key, value) if fact_key else None
    return None


def _sanitize_summary(value: str) -> str:
    return " ".join(str(value).strip().split())[:500]


def _fact_to_dict(row: UserMemoryFactModel) -> dict:
    return {
        "id": row.id,
        "profile_user_id": row.profile_user_id,
        "fact_key": row.fact_key,
        "value": json.loads(row.value_json),
        "source_kind": row.source_kind,
        "source_trace_id": row.source_trace_id,
        "confidence": row.confidence,
        "valid_from": row.valid_from.isoformat(),
        "valid_to": row.valid_to.isoformat() if row.valid_to else None,
        "supersedes_fact_id": row.supersedes_fact_id,
    }


def _episode_to_dict(row: SalesMemoryEpisodeModel) -> dict:
    return {
        "id": row.id,
        "profile_user_id": row.profile_user_id,
        "episode_type": row.episode_type,
        "summary": row.summary,
        "source_trace_id": row.source_trace_id,
        "importance": row.importance,
        "confidence": row.confidence,
        "resolved": row.resolved,
        "occurred_at": row.occurred_at.isoformat(),
    }
