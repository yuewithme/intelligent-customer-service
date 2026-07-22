import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import or_, select

from app.infrastructure.database.models import (
    MemoryFactEvidenceModel,
    MemoryFactModel,
    MemoryIdentityModel,
    MemorySubjectModel,
)
from app.domains.customers.schemas.memory import LegacyProfileProjection
from app.domains.customers.services.memory_repository import get_memory_session


def build_legacy_profile_projection(
    *,
    tenant_id: str,
    subject_id: str,
    channel: str | None = None,
    as_of: datetime | None = None,
) -> LegacyProfileProjection:
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    with get_memory_session() as session:
        subject = session.scalar(
            select(MemorySubjectModel).where(
                MemorySubjectModel.id == subject_id,
                MemorySubjectModel.tenant_id == tenant_id,
                MemorySubjectModel.deleted_at.is_(None),
            )
        )
        if subject is None:
            raise ValueError("memory subject not found in tenant")

        identity_query = select(MemoryIdentityModel).where(
            MemoryIdentityModel.tenant_id == tenant_id,
            MemoryIdentityModel.subject_id == subject_id,
        )
        if channel:
            identity_query = identity_query.where(
                MemoryIdentityModel.channel == channel.strip().lower()
            )
        identity = session.scalar(identity_query.order_by(MemoryIdentityModel.id))
        if identity is None:
            raise ValueError("memory subject has no compatible external identity")

        facts = list(
            session.scalars(
                select(MemoryFactModel)
                .where(
                    MemoryFactModel.tenant_id == tenant_id,
                    MemoryFactModel.subject_id == subject_id,
                    MemoryFactModel.status == "active",
                    MemoryFactModel.valid_from <= as_of,
                    MemoryFactModel.id.in_(
                        select(MemoryFactEvidenceModel.fact_id)
                    ),
                    or_(
                        MemoryFactModel.valid_to.is_(None),
                        MemoryFactModel.valid_to > as_of,
                    ),
                )
                .order_by(
                    MemoryFactModel.fact_key,
                    MemoryFactModel.version.desc(),
                    MemoryFactModel.recorded_at.desc(),
                )
            )
        )

    current_by_key: dict[str, list[Any]] = {}
    for fact in facts:
        value = json.loads(fact.fact_value_json)
        values = current_by_key.setdefault(fact.fact_key, [])
        if value not in values:
            values.append(value)

    basic_info: dict[str, Any] = {}
    display_name = _first(current_by_key, "identity.display_name")
    if isinstance(display_name, str):
        basic_info["nickname"] = display_name
    region = _first(current_by_key, "location.region")
    if isinstance(region, dict) and region.get("city"):
        basic_info["shipping_city"] = region["city"]

    product_interests = [
        _product_interest_label(value)
        for value in current_by_key.get("purchase.product_interest", [])
    ]
    product_interests = [value for value in product_interests if value]
    pain_points = [
        _pain_point_label(value)
        for value in current_by_key.get("service.pain_point", [])
    ]
    pain_points = [value for value in pain_points if value]
    preference_summary = _preference_summary(current_by_key)

    active_opportunity: dict[str, Any] = {}
    budget = _first(current_by_key, "purchase.budget")
    if budget is not None:
        active_opportunity["budget"] = budget
    purchase_status = _first(current_by_key, "purchase.status")
    if purchase_status is not None:
        active_opportunity["purchase_status"] = purchase_status

    return LegacyProfileProjection(
        user_id=identity.external_user_id,
        tenant_id=tenant_id,
        channel=identity.channel,
        subject_id=subject_id,
        basic_info=basic_info,
        product_interests=product_interests,
        pain_points=pain_points,
        preference_summary=preference_summary,
        active_opportunity=active_opportunity,
    )


def _first(values_by_key: dict[str, list[Any]], key: str) -> Any:
    values = values_by_key.get(key) or []
    return values[0] if values else None


def _product_interest_label(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    return value.get("name") or value.get("category") or value.get("product_id")


def _pain_point_label(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return None
    return value.get("detail") or value.get("topic")


def _preference_summary(values_by_key: dict[str, list[Any]]) -> str | None:
    parts: list[str] = []
    detail = _first(values_by_key, "communication.preferred_detail")
    if isinstance(detail, str):
        parts.append(
            {
                "concise": "偏好简洁说明",
                "balanced": "偏好适中说明",
                "detailed": "偏好详细说明",
            }.get(detail, detail)
        )
    channel = _first(values_by_key, "communication.preferred_channel")
    if isinstance(channel, str):
        parts.append(f"偏好通过 {channel} 沟通")
    for value in values_by_key.get("service.preference", []):
        if isinstance(value, dict) and value.get("topic") and value.get("value"):
            parts.append(f"{value['topic']}: {value['value']}")
    return "；".join(parts) or None
