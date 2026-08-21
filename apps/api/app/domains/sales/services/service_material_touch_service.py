from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domains.catalog.services.agent_media_library_service import (
    select_scheduled_agent_media,
)
from app.domains.sales.services.tag_catalog import get_tag_categories
from app.infrastructure.database.models import (
    AgentCustomerStateModel,
    AgentWakeupModel,
    Base,
    EyunContactModel,
    EyunOutboundMessageModel,
    UserProfileModel,
)


logger = logging.getLogger("wechat_rag_bot.service_material_touch")
_MAX_ATTEMPTS = 3
_RETRY_DELAY = timedelta(minutes=15)
_PROCESSING_LEASE = timedelta(minutes=10)
_CONTACT_SYNC_RETRY_DELAY = timedelta(minutes=10)
_SERVICE_MATERIAL_KIND = "service_material_touch"
_SERVICE_TAG_CATEGORY_IDS = {"service_status", "service_tag"}
_INACTIVE_SERVICE_TAG_MARKERS = ("暂停", "停止", "结束", "退订", "禁用")
_database_factories: dict[str, sessionmaker] = {}
_last_contact_sync_at: datetime | None = None
_last_contact_sync_attempt_at: datetime | None = None


def record_agent_relationship_state(
    *,
    customer_id: str,
    tenant_id: str,
    customer_signal: str,
    commercial_judgment: str,
    relationship_purpose: str,
    source_trace_id: str,
) -> None:
    now = _utcnow()
    signal = customer_signal if customer_signal in {
        "none",
        "soft_refusal",
        "explicit_refusal",
    } else "none"
    with _database_session() as session:
        row = session.get(AgentCustomerStateModel, customer_id)
        if row is None:
            row = AgentCustomerStateModel(
                customer_id=customer_id,
                tenant_id=tenant_id,
                customer_signal=signal,
                explicit_refusal=signal == "explicit_refusal",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        row.tenant_id = tenant_id
        row.customer_signal = signal
        if signal == "explicit_refusal":
            row.explicit_refusal = True
        elif signal == "none" and row.explicit_refusal:
            # A later ordinary turn does not erase a written refusal. It can be
            # cleared only by a new positive purchase/relationship signal.
            pass
        row.commercial_judgment = commercial_judgment[:4000]
        row.relationship_purpose = relationship_purpose[:2000]
        row.source_trace_id = source_trace_id
        row.updated_at = now
        session.commit()


def get_agent_relationship_state(customer_id: str) -> dict[str, Any]:
    with _database_session() as session:
        row = session.get(AgentCustomerStateModel, customer_id)
        if row is None:
            return {
                "customer_signal": "none",
                "explicit_refusal": False,
            }
        return {
            "customer_signal": row.customer_signal,
            "explicit_refusal": row.explicit_refusal,
            "commercial_judgment": row.commercial_judgment,
            "relationship_purpose": row.relationship_purpose,
            "updated_at": _iso(row.updated_at),
        }


def ensure_service_material_touch_tasks(*, now: datetime | None = None) -> int:
    settings = get_settings()
    if not settings.service_material_touch_enabled:
        return 0
    zone = _timezone(settings.service_material_touch_timezone)
    utc_now = (now or _utcnow()).astimezone(timezone.utc)
    current = utc_now.astimezone(zone)
    local_date = current.date().isoformat()
    grace = timedelta(minutes=settings.service_material_touch_grace_minutes)
    service_tag_values = _active_service_tag_values()
    if not service_tag_values:
        return 0
    created = 0
    with _database_session() as session:
        service_customer_ids = _service_customer_ids(session, service_tag_values)
        if not service_customer_ids:
            return 0
        contacts = list(
            session.scalars(
                select(EyunContactModel)
                .where(
                    EyunContactModel.status == "active",
                    EyunContactModel.wc_id.in_(service_customer_ids),
                )
                .order_by(EyunContactModel.id.asc())
            )
        )
        for contact in contacts:
            for slot_id, slot_time, _, _ in _service_material_slots():
                due_local = datetime.combine(current.date(), slot_time, tzinfo=zone)
                if due_local < current - grace:
                    continue
                due_at = max(due_local.astimezone(timezone.utc), utc_now)
                dedup_key = (
                    f"service_material:{contact.tenant_id}:{contact.id}:"
                    f"{local_date}:{slot_id}"
                )
                if session.scalar(
                    select(AgentWakeupModel.id).where(
                        AgentWakeupModel.dedup_key == dedup_key
                    )
                ):
                    continue
                technical_status = (
                    "pending" if contact.current_w_id else "technical_skip"
                )
                session.add(
                    AgentWakeupModel(
                        dedup_key=dedup_key,
                        tenant_id=contact.tenant_id,
                        customer_id=contact.wc_id,
                        contact_id=contact.id,
                        kind=_SERVICE_MATERIAL_KIND,
                        local_date=local_date,
                        reason=f"service_material:{slot_id}",
                        checklist_json="[]",
                        status=technical_status,
                        due_at=due_at,
                        attempts=0,
                        last_error=(
                            "联系人缺少可用微信账号"
                            if not contact.current_w_id
                            else None
                        ),
                        created_at=utc_now,
                        updated_at=utc_now,
                    )
                )
                created += 1
        session.commit()
    return created


async def process_due_service_material_touches(
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> int:
    settings = get_settings()
    if not settings.service_material_touch_enabled:
        return 0
    limit = limit or settings.service_material_touch_batch_size
    utc_now = (now or _utcnow()).astimezone(timezone.utc)
    candidates: list[int] = []
    with _database_session() as session:
        stale_rows = list(
            session.scalars(
                select(AgentWakeupModel).where(
                    AgentWakeupModel.kind == _SERVICE_MATERIAL_KIND,
                    AgentWakeupModel.status == "processing",
                    AgentWakeupModel.updated_at < utc_now - _PROCESSING_LEASE,
                )
            )
        )
        for row in stale_rows:
            row.status = "pending"
            row.last_error = "上一次处理租约超时，已自动恢复"
            row.due_at = utc_now
            row.updated_at = utc_now
        if stale_rows:
            session.flush()
        rows = list(
            session.scalars(
                select(AgentWakeupModel)
                .where(
                    AgentWakeupModel.kind == _SERVICE_MATERIAL_KIND,
                    AgentWakeupModel.status == "pending",
                    AgentWakeupModel.due_at <= utc_now,
                )
                .order_by(AgentWakeupModel.due_at.asc(), AgentWakeupModel.id.asc())
                .limit(limit)
            )
        )
        for row in rows:
            row.status = "processing"
            row.attempts += 1
            row.updated_at = utc_now
            candidates.append(row.id)
        session.commit()

    queued = 0
    for wakeup_id in candidates:
        if await _process_service_material_touch(wakeup_id, now=utc_now):
            queued += 1
    return queued


async def _process_service_material_touch(
    wakeup_id: int, *, now: datetime
) -> bool:
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is None or row.status != "processing":
            return False
        contact = (
            session.get(EyunContactModel, row.contact_id)
            if row.contact_id
            else session.scalar(
                select(EyunContactModel)
                .where(
                    EyunContactModel.wc_id == row.customer_id,
                    EyunContactModel.status == "active",
                )
                .order_by(EyunContactModel.updated_at.desc())
                .limit(1)
            )
        )
        if contact is None or contact.status != "active" or not contact.current_w_id:
            row.status = "technical_skip"
            row.last_error = "联系人或发送账号当前不可用"
            row.updated_at = now
            session.commit()
            return False
        if not _profile_has_active_service_tag(
            session, row.customer_id, _active_service_tag_values()
        ):
            row.status = "cancelled"
            row.last_error = "客户已无有效服务标签"
            row.updated_at = now
            session.commit()
            return False
        relationship = get_agent_relationship_state(row.customer_id)
        if relationship["explicit_refusal"]:
            row.status = "cancelled"
            row.last_error = "客户已明确拒绝自动触达"
            row.updated_at = now
            session.commit()
            return False
        slot_id = row.reason.removeprefix("service_material:")
        slot = next(
            (item for item in _service_material_slots() if item[0] == slot_id),
            None,
        )
        if slot is None:
            row.status = "failed"
            row.last_error = "定时素材时段配置无效"
            row.updated_at = now
            session.commit()
            return False
        try:
            scheduled_date = datetime.strptime(
                row.local_date or "", "%Y-%m-%d"
            ).date()
        except ValueError:
            scheduled_date = now.astimezone(
                _timezone(get_settings().service_material_touch_timezone)
            ).date()
        material = select_scheduled_agent_media(
            local_date=scheduled_date,
            category=slot[2],
            copy_type=slot[3],
            max_video_bytes=get_settings().service_material_max_video_bytes,
        )
        if material is None:
            row.status = "failed"
            row.last_error = f"素材库无可用的{slot[3]}内容"
            row.updated_at = now
            session.commit()
            return False
        batch_key = f"service_material_touch:{row.id}"
        w_id = contact.current_w_id
        customer_id = contact.wc_id
        tenant_id = contact.tenant_id

    media_type = str(material.get("format") or "")
    media_url = str(material.get("url") or "").strip()
    if media_type == "video":
        thumb_url = str(material.get("thumb_url") or "").strip()
        if not media_url or not thumb_url:
            _mark_service_material_touch(wakeup_id, "failed", "定时视频缺少地址或封面")
            return False
        media_content = json.dumps(
            {"path": media_url, "thumb_path": thumb_url},
            ensure_ascii=False,
        )
    elif media_type == "image" and media_url:
        media_content = media_url
    else:
        _mark_service_material_touch(wakeup_id, "failed", "定时素材类型或地址无效")
        return False
    copy_text = str(material.get("copy_text") or "").strip()
    messages = (("text", copy_text, None),)
    if not copy_text:
        _mark_service_material_touch(wakeup_id, "failed", "定时素材缺少已审核文案")
        return False

    from app.integrations.eyun.services.eyun_material_service import (
        materialize_eyun_outbound_media,
    )
    from app.integrations.eyun.services.message_risk_control_service import (
        enqueue_wechat_outbound,
    )

    try:
        prepared_material = await materialize_eyun_outbound_media(
            w_id=w_id,
            message_type=media_type,
            content=media_content,
        )
    except Exception as exc:  # noqa: BLE001
        _retry_service_material_touch(
            wakeup_id,
            now=now,
            error=f"定时素材转换失败：{type(exc).__name__}",
        )
        return False
    messages += ((media_type, media_content, int(prepared_material["id"])),)

    dependency_id: int | None = None
    due_at = now
    try:
        for message_type, content, material_id in messages:
            queued = await enqueue_wechat_outbound(
                w_id=w_id,
                wc_id=customer_id,
                content=content,
                source_batch_key=batch_key,
                message_type=message_type,
                material_id=material_id,
                depends_on_outbound_id=dependency_id,
                due_at=due_at,
                channel="wechat",
                user_id=customer_id,
                session_id="default",
                tenant_id=tenant_id,
                source_type=_SERVICE_MATERIAL_KIND,
                source_id=str(wakeup_id),
            )
            dependency_id = int(queued["id"])
            due_at += timedelta(seconds=3)
    except Exception as exc:  # noqa: BLE001
        _mark_service_material_touch(
            wakeup_id,
            "failed",
            f"定时素材入队失败：{type(exc).__name__}",
        )
        return False
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is not None:
            row.status = "queued"
            row.outbound_batch_key = batch_key
            row.updated_at = now
            session.commit()
    return True


def _retry_service_material_touch(
    wakeup_id: int, *, now: datetime, error: str
) -> None:
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is None:
            return
        if row.attempts < _MAX_ATTEMPTS:
            row.status = "pending"
            row.due_at = now + _RETRY_DELAY
            row.last_error = f"{error}，已安排重试"
        else:
            row.status = "failed"
            row.last_error = error
        row.updated_at = now
        session.commit()


def validate_service_material_touch_before_send(source_batch_key: str | None) -> bool:
    task_id = _service_material_touch_id(source_batch_key)
    if task_id is None:
        return True
    with _database_session() as session:
        row = session.get(AgentWakeupModel, task_id)
        if row is None or row.status not in {"queued", "completed"}:
            return False
        if row.kind != _SERVICE_MATERIAL_KIND:
            row.status = "cancelled"
            row.last_error = "非服务中触达任务已停用"
            row.updated_at = _utcnow()
            session.commit()
            return False
        contact = session.get(EyunContactModel, row.contact_id) if row.contact_id else session.scalar(
            select(EyunContactModel).where(
                EyunContactModel.wc_id == row.customer_id,
                EyunContactModel.status == "active",
            ).limit(1)
        )
        if contact is None or contact.status != "active" or not contact.current_w_id:
            row.status = "technical_skip"
            row.last_error = "发送前通道不可用"
            row.updated_at = _utcnow()
            session.commit()
            return False
        service_tagged = _profile_has_active_service_tag(
            session, row.customer_id, _active_service_tag_values()
        )
        customer_id = row.customer_id
        if not service_tagged:
            row.status = "cancelled"
            row.last_error = "发送前检查发现服务标签已移除"
            row.updated_at = _utcnow()
            session.commit()
            return False
    return not get_agent_relationship_state(customer_id)["explicit_refusal"]


def sync_service_material_touch_from_outbound(
    source_batch_key: str | None,
    status: str,
    error: str | None = None,
) -> None:
    task_id = _service_material_touch_id(source_batch_key)
    if task_id is None:
        return
    now = _utcnow()
    with _database_session() as session:
        row = session.get(AgentWakeupModel, task_id)
        if row is None or row.kind != _SERVICE_MATERIAL_KIND:
            return
        if status == "sent":
            outbound_statuses = list(
                session.scalars(
                    select(EyunOutboundMessageModel.status).where(
                        EyunOutboundMessageModel.source_batch_key
                        == source_batch_key
                    )
                )
            )
            if not outbound_statuses or any(
                value != "sent" for value in outbound_statuses
            ):
                return
            row.status = "completed"
            row.completed_at = now
            row.last_error = None
        elif status in {"failed", "cancelled"} and row.status != "completed":
            row.status = status
            row.last_error = (error or status)[:2000]
        row.updated_at = now
        session.commit()


async def service_material_touch_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await _service_material_touch_worker_tick()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Service material touch worker tick failed: %s", exc)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=get_settings().service_material_touch_poll_seconds,
            )
        except asyncio.TimeoutError:
            pass


async def _service_material_touch_worker_tick() -> None:
    settings = get_settings()
    if settings.service_material_touch_enabled:
        try:
            ensure_service_material_touch_tasks()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Service material touch creation failed: %s", exc)
        try:
            await process_due_service_material_touches()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Service material touch processing failed: %s", exc)
        try:
            await _refresh_contacts_if_due()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Service material contact refresh failed: %s", exc)


async def _refresh_contacts_if_due() -> None:
    global _last_contact_sync_at, _last_contact_sync_attempt_at
    settings = get_settings()
    now = _utcnow()
    if not (settings.eyun_base_url and settings.eyun_authorization and settings.eyun_wid):
        return
    if _last_contact_sync_at and now - _last_contact_sync_at < timedelta(hours=2):
        return
    if (
        _last_contact_sync_attempt_at
        and now - _last_contact_sync_attempt_at < _CONTACT_SYNC_RETRY_DELAY
    ):
        return
    from app.domains.sales.services.contact_sync_service import sync_eyun_contacts

    _last_contact_sync_attempt_at = now
    await sync_eyun_contacts()
    _last_contact_sync_at = now


def _mark_service_material_touch(
    wakeup_id: int, status: str, error: str | None = None
) -> None:
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is None:
            return
        row.status = status
        row.last_error = error
        row.updated_at = _utcnow()
        session.commit()


def _database_session() -> Session:
    url = get_settings().database_url
    factory = _database_factories.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(
            engine,
            tables=[
                EyunContactModel.__table__,
                AgentCustomerStateModel.__table__,
                AgentWakeupModel.__table__,
                EyunOutboundMessageModel.__table__,
                UserProfileModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _database_factories[url] = factory
    return factory()


def _parse_time(value: str, fallback: time) -> time:
    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        return fallback


def _service_material_slots() -> tuple[tuple[str, time, str, str], ...]:
    settings = get_settings()
    return (
        (
            "story",
            _parse_time(settings.service_material_story_time, time(7, 0)),
            "",
            "名品故事",
        ),
        (
            "knowledge",
            _parse_time(settings.service_material_knowledge_time, time(10, 0)),
            "",
            "养护科普",
        ),
        (
            "topic",
            _parse_time(settings.service_material_topic_time, time(14, 0)),
            "",
            "话题种草",
        ),
    )


def _active_service_tag_values() -> set[str]:
    values: set[str] = set()
    for category_id, category in get_tag_categories().items():
        if (
            category_id not in _SERVICE_TAG_CATEGORY_IDS
            and category.name != "服务标签"
        ):
            continue
        for value in category.values:
            name = str(value.name or "").strip()
            if name and not any(
                marker in name for marker in _INACTIVE_SERVICE_TAG_MARKERS
            ):
                values.add(name)
    return values


def _service_customer_ids(session: Session, tag_values: set[str]) -> set[str]:
    if not tag_values:
        return set()
    refused_ids = set(
        session.scalars(
            select(AgentCustomerStateModel.customer_id).where(
                AgentCustomerStateModel.explicit_refusal.is_(True)
            )
        )
    )
    return {
        profile.user_id
        for profile in session.scalars(select(UserProfileModel))
        if profile.user_id not in refused_ids
        and bool(_profile_tag_values(profile) & tag_values)
    }


def _profile_has_active_service_tag(
    session: Session, customer_id: str, tag_values: set[str]
) -> bool:
    if not tag_values:
        return False
    profile = session.get(UserProfileModel, customer_id)
    return profile is not None and bool(_profile_tag_values(profile) & tag_values)


def _profile_tag_values(profile: UserProfileModel) -> set[str]:
    try:
        parsed = json.loads(profile.customer_tags_json or "[]")
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(parsed, list):
        return set()
    values: set[str] = set()
    for item in parsed:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if value:
            values.add(value.rpartition(":")[2] or value)
    return values


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Shanghai")


def _service_material_touch_id(source_batch_key: str | None) -> int | None:
    value = str(source_batch_key or "")
    prefixes = ("service_material_touch:", "agent_wakeup:")
    if not value.startswith(prefixes):
        return None
    try:
        return int(value.partition(":")[2])
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
