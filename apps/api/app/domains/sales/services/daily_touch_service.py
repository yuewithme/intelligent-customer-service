from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domains.catalog.services.agent_media_library_service import (
    select_scheduled_agent_media,
)
from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.services.conversation_service import (
    AI_BLOCKED_STATUSES,
    HUMAN_ACTIVE,
)
from app.domains.sales.services.tag_catalog import get_tag_categories
from app.infrastructure.database.models import (
    AgentCustomerStateModel,
    AgentWakeupModel,
    Base,
    ConversationMessageModel,
    ConversationModel,
    EyunContactModel,
    EyunOutboundMessageModel,
    UserProfileModel,
)


logger = logging.getLogger("wechat_rag_bot.daily_touch")
_WAKEUP_MAX_ATTEMPTS = 3
_WAKEUP_RETRY_DELAY = timedelta(minutes=15)
_PROCESSING_LEASE = timedelta(minutes=10)
_SERVICE_MATERIAL_KIND = "service_material_touch"
_SERVICE_TAG_CATEGORY_IDS = {"service_status", "service_tag"}
_INACTIVE_SERVICE_TAG_MARKERS = ("暂停", "停止", "结束", "退订", "禁用")
_database_factories: dict[str, sessionmaker] = {}
_chat_factories: dict[str, sessionmaker] = {}
_last_contact_sync_at: datetime | None = None


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


def get_daily_touch_snapshot(
    customer_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    zone = _timezone(settings.daily_touch_timezone)
    current = (now or _utcnow()).astimezone(zone)
    local_start = datetime.combine(current.date(), time.min, tzinfo=zone)
    local_end = local_start + timedelta(days=1)
    start_utc = local_start.astimezone(timezone.utc)
    end_utc = local_end.astimezone(timezone.utc)
    with _chat_session() as session:
        conversation = session.scalar(
            select(ConversationModel)
            .where(
                ConversationModel.channel == "wechat",
                ConversationModel.user_id == customer_id,
            )
            .order_by(ConversationModel.updated_at.desc())
            .limit(1)
        )
        rows = []
        if conversation is not None:
            rows = list(
                session.scalars(
                    select(ConversationMessageModel)
                    .where(
                        ConversationMessageModel.conversation_id
                        == conversation.conversation_id,
                        ConversationMessageModel.sender_type.in_(("ai", "human")),
                        ConversationMessageModel.delivery_status == "sent",
                        ConversationMessageModel.created_at >= start_utc,
                        ConversationMessageModel.created_at < end_utc,
                    )
                    .order_by(
                        ConversationMessageModel.created_at.asc(),
                        ConversationMessageModel.id.asc(),
                    )
                )
            )
        last = rows[-1] if rows else None
        touch_keys = {_touch_key(row) for row in rows}
        status = conversation.status if conversation is not None else "ai_active"
        owner_id = conversation.owner_id if conversation is not None else None
    relationship = get_agent_relationship_state(customer_id)
    return {
        "local_date": current.date().isoformat(),
        "timezone": str(zone),
        "sent_message_count": len(touch_keys),
        "sent_unit_count": len(rows),
        "completed_today": bool(rows),
        "last_sender": last.sender_type if last is not None else None,
        "last_sent_at": _iso(last.created_at) if last is not None else None,
        "last_topic": str(last.content or "")[:200] if last is not None else None,
        "conversation_status": status,
        "human_active": status in AI_BLOCKED_STATUSES,
        "human_owner_id": owner_id,
        "customer_signal": relationship.get("customer_signal", "none"),
        "explicit_refusal": bool(relationship.get("explicit_refusal")),
        "daily_minimum": 1,
        "daily_maximum": 1 if relationship.get("explicit_refusal") else 2,
    }


def schedule_agent_wakeup(
    *,
    customer_id: str,
    tenant_id: str,
    due_in_hours: float,
    reason: str,
    checklist: list[str],
    source_trace_id: str,
) -> dict[str, Any]:
    now = _utcnow()
    due_at = now + timedelta(hours=due_in_hours)
    raw_key = f"{tenant_id}\0{customer_id}\0{source_trace_id}\0{reason}\0{due_at.isoformat()}"
    dedup_key = "follow_up:" + hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    with _database_session() as session:
        existing = session.scalar(
            select(AgentWakeupModel).where(AgentWakeupModel.dedup_key == dedup_key)
        )
        if existing is None:
            existing = AgentWakeupModel(
                dedup_key=dedup_key,
                tenant_id=tenant_id,
                customer_id=customer_id,
                kind="follow_up",
                reason=reason,
                checklist_json=json.dumps(checklist, ensure_ascii=False),
                status="pending",
                due_at=due_at,
                source_trace_id=source_trace_id,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            session.add(existing)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(AgentWakeupModel).where(
                        AgentWakeupModel.dedup_key == dedup_key
                    )
                )
        if existing is None:
            raise RuntimeError("failed_to_create_agent_wakeup")
        session.refresh(existing)
        return _wakeup_dict(existing)


def ensure_daily_touch_wakeups(*, now: datetime | None = None) -> int:
    settings = get_settings()
    if not settings.daily_touch_enabled:
        return 0
    zone = _timezone(settings.daily_touch_timezone)
    current = (now or _utcnow()).astimezone(zone)
    if not _within_send_window(current):
        return 0
    local_date = current.date().isoformat()
    created = 0
    utc_now = (now or _utcnow()).astimezone(timezone.utc)
    service_tag_values = _active_service_tag_values()
    with _database_session() as session:
        service_customer_ids = _service_customer_ids(session, service_tag_values)
        contacts = list(
            session.scalars(
                select(EyunContactModel)
                .where(EyunContactModel.status == "active")
                .order_by(EyunContactModel.id.asc())
            )
        )
        for contact in contacts:
            if contact.wc_id in service_customer_ids:
                continue
            snapshot = get_daily_touch_snapshot(contact.wc_id, now=utc_now)
            if snapshot["completed_today"]:
                continue
            dedup_key = f"daily:{contact.tenant_id}:{contact.id}:{local_date}"
            if session.scalar(
                select(AgentWakeupModel.id).where(
                    AgentWakeupModel.dedup_key == dedup_key
                )
            ):
                continue
            technical_status = "pending" if contact.current_w_id else "technical_skip"
            session.add(
                AgentWakeupModel(
                    dedup_key=dedup_key,
                    tenant_id=contact.tenant_id,
                    customer_id=contact.wc_id,
                    contact_id=contact.id,
                    kind="daily_touch",
                    local_date=local_date,
                    reason="完成今日客户关系触达，由 Agent 重新判断内容和工具",
                    checklist_json=json.dumps(
                        [
                            "今日真实 sent 次数",
                            "客户购买与服务状态",
                            "最近问题、顾虑、资料和商品主题",
                            "选择一个新的商业和关系目的",
                        ],
                        ensure_ascii=False,
                    ),
                    status=technical_status,
                    due_at=utc_now,
                    attempts=0,
                    last_error=("联系人缺少可用微信账号" if not contact.current_w_id else None),
                    created_at=utc_now,
                    updated_at=utc_now,
                )
            )
            created += 1
        session.commit()
    return created


def ensure_service_material_touch_wakeups(*, now: datetime | None = None) -> int:
    settings = get_settings()
    if not settings.service_material_touch_enabled:
        return 0
    zone = _timezone(settings.daily_touch_timezone)
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


async def process_due_agent_wakeups(
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> int:
    settings = get_settings()
    limit = limit or settings.daily_touch_batch_size
    utc_now = (now or _utcnow()).astimezone(timezone.utc)
    candidates: list[int] = []
    with _database_session() as session:
        stale_rows = list(
            session.scalars(
                select(AgentWakeupModel).where(
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
                    AgentWakeupModel.status.in_(("pending", "waiting_human")),
                    AgentWakeupModel.due_at <= utc_now,
                )
                .order_by(AgentWakeupModel.due_at.asc(), AgentWakeupModel.id.asc())
                .limit(limit)
            )
        )
        for row in rows:
            if row.status == "waiting_human":
                snapshot = get_daily_touch_snapshot(row.customer_id, now=utc_now)
                if snapshot["completed_today"]:
                    row.status = "completed"
                    row.completed_at = utc_now
                    row.updated_at = utc_now
                continue
            row.status = "processing"
            row.attempts += 1
            row.updated_at = utc_now
            candidates.append(row.id)
        session.commit()

    queued = 0
    for wakeup_id in candidates:
        if await _process_wakeup(wakeup_id, now=utc_now):
            queued += 1
    return queued


async def _process_wakeup(wakeup_id: int, *, now: datetime) -> bool:
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is not None and row.kind == _SERVICE_MATERIAL_KIND:
            return await _process_service_material_wakeup(wakeup_id, now=now)

    local_now = now.astimezone(_timezone(get_settings().daily_touch_timezone))
    if not _within_send_window(local_now):
        with _database_session() as session:
            deferred = session.get(AgentWakeupModel, wakeup_id)
            if deferred is not None and deferred.status == "processing":
                deferred.status = "pending"
                deferred.due_at = _inside_or_next_send_window(now)
                deferred.last_error = "当前不在每日触达发送时段，已顺延"
                deferred.updated_at = now
                session.commit()
        return False
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is None or row.status != "processing":
            return False
        contact = session.get(EyunContactModel, row.contact_id) if row.contact_id else session.scalar(
            select(EyunContactModel)
            .where(
                EyunContactModel.wc_id == row.customer_id,
                EyunContactModel.status == "active",
            )
            .order_by(EyunContactModel.updated_at.desc())
            .limit(1)
        )
        if contact is None or contact.status != "active" or not contact.current_w_id:
            row.status = "technical_skip"
            row.last_error = "联系人或发送账号当前不可用"
            row.updated_at = now
            session.commit()
            return False
        snapshot = get_daily_touch_snapshot(row.customer_id, now=now)
        if row.kind == "daily_touch" and snapshot["completed_today"]:
            row.status = "completed"
            row.completed_at = now
            row.updated_at = now
            session.commit()
            return False
        if row.kind != "daily_touch" and snapshot["sent_message_count"] >= snapshot["daily_maximum"]:
            row.status = "cancelled"
            row.last_error = "今日触达上限已满足"
            row.updated_at = now
            session.commit()
            return False
        if snapshot["human_active"]:
            row.status = "waiting_human"
            row.last_error = "人工会话中，已提醒负责人完成今日触达"
            row.updated_at = now
            session.commit()
            await _notify_human_daily_touch(row.customer_id, row.id)
            return False
        checklist = _json_list(row.checklist_json)
        batch_key = f"agent_wakeup:{row.id}"
        w_id = contact.current_w_id
        customer_id = contact.wc_id
        tenant_id = contact.tenant_id
        kind = row.kind
        reason = row.reason

    from app.domains.conversations.services.chat_orchestrator import handle_chat

    chat_result = await handle_chat(
        ChatRequest(
            channel="wechat",
            user_id=customer_id,
            session_id="default",
            message=(
                "[系统每日触达唤醒]"
                if kind == "daily_touch"
                else "[系统专项跟进唤醒]"
            ),
            kb_id=get_settings().wechat_default_kb_id,
            metadata={
                "provider": "eyun",
                "provider_delivery_mode": "scheduled",
                "system_event": "daily_touch" if kind == "daily_touch" else "follow_up_wakeup",
                "daily_touch": snapshot,
                "wakeup_reason": reason,
                "wakeup_checklist": checklist,
                "w_id": w_id,
                "wc_id": customer_id,
                "from_user": customer_id,
                "from_group": "default",
                "tenant_id": tenant_id,
                "skip_customer_record": True,
                "skip_conversation_memory": True,
            },
        )
    )
    messages = _outbound_messages(chat_result)
    if not messages:
        _mark_wakeup(wakeup_id, "failed", "Agent 未生成可发送消息")
        return False

    from app.integrations.eyun.services.message_risk_control_service import enqueue_wechat_outbound

    dependency_id: int | None = None
    due_at = now
    for item in messages[:5]:
        queued = await enqueue_wechat_outbound(
            w_id=w_id,
            wc_id=customer_id,
            content=item["content"],
            source_batch_key=batch_key,
            message_type=item["type"],
            material_id=item.get("material_id"),
            depends_on_outbound_id=dependency_id,
            due_at=due_at,
            channel="wechat",
            user_id=customer_id,
            session_id="default",
            tenant_id=tenant_id,
            source_type="agent_wakeup",
            source_id=str(wakeup_id),
        )
        dependency_id = int(queued["id"])
        due_at += timedelta(seconds=3)
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is not None:
            row.status = "queued"
            row.outbound_batch_key = batch_key
            row.updated_at = now
            session.commit()
    return True


async def _process_service_material_wakeup(
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
        snapshot = get_daily_touch_snapshot(row.customer_id, now=now)
        if snapshot["explicit_refusal"] or snapshot["human_active"]:
            row.status = "cancelled"
            row.last_error = (
                "客户已明确拒绝自动触达"
                if snapshot["explicit_refusal"]
                else "客户当前由人工接待"
            )
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
                _timezone(get_settings().daily_touch_timezone)
            ).date()
        material = select_scheduled_agent_media(
            local_date=scheduled_date,
            category=slot[2],
            copy_type=slot[3],
        )
        if material is None:
            row.status = "failed"
            row.last_error = f"素材库无可用的{slot[3]}内容"
            row.updated_at = now
            session.commit()
            return False
        batch_key = f"agent_wakeup:{row.id}"
        w_id = contact.current_w_id
        customer_id = contact.wc_id
        tenant_id = contact.tenant_id

    media_type = str(material.get("format") or "")
    media_url = str(material.get("url") or "").strip()
    if media_type == "video":
        thumb_url = str(material.get("thumb_url") or "").strip()
        if not media_url or not thumb_url:
            _mark_wakeup(wakeup_id, "failed", "定时视频缺少地址或封面")
            return False
        media_content = json.dumps(
            {"path": media_url, "thumb_path": thumb_url},
            ensure_ascii=False,
        )
    elif media_type == "image" and media_url:
        media_content = media_url
    else:
        _mark_wakeup(wakeup_id, "failed", "定时素材类型或地址无效")
        return False
    messages = (
        ("text", str(material.get("copy_text") or "").strip()),
        (media_type, media_content),
    )
    if not messages[0][1]:
        _mark_wakeup(wakeup_id, "failed", "定时素材缺少已审核文案")
        return False

    from app.integrations.eyun.services.message_risk_control_service import (
        enqueue_wechat_outbound,
    )

    dependency_id: int | None = None
    due_at = now
    try:
        for message_type, content in messages:
            queued = await enqueue_wechat_outbound(
                w_id=w_id,
                wc_id=customer_id,
                content=content,
                source_batch_key=batch_key,
                message_type=message_type,
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
        _mark_wakeup(
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


def validate_agent_wakeup_before_send(source_batch_key: str | None) -> bool:
    wakeup_id = _wakeup_id(source_batch_key)
    if wakeup_id is None:
        return True
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is None or row.status not in {"queued", "completed"}:
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
        if row.kind == _SERVICE_MATERIAL_KIND:
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
            special_touch = True
        else:
            special_touch = False
        created_at = row.created_at
        customer_id = row.customer_id
    if special_touch:
        snapshot = get_daily_touch_snapshot(customer_id)
        return not snapshot["explicit_refusal"] and not snapshot["human_active"]
    with _chat_session() as session:
        changed = session.scalar(
            select(ConversationMessageModel.id)
            .join(
                ConversationModel,
                ConversationModel.conversation_id == ConversationMessageModel.conversation_id,
            )
            .where(
                ConversationModel.channel == "wechat",
                ConversationModel.user_id == customer_id,
                ConversationMessageModel.sender_type == "customer",
                ConversationMessageModel.created_at > created_at,
            )
            .limit(1)
        )
    return changed is None


def sync_agent_wakeup_from_outbound(
    source_batch_key: str | None,
    status: str,
    error: str | None = None,
) -> None:
    wakeup_id = _wakeup_id(source_batch_key)
    if wakeup_id is None:
        return
    now = _utcnow()
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is None:
            return
        if status == "sent":
            if row.kind == _SERVICE_MATERIAL_KIND:
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
            if row.kind == "daily_touch" and row.attempts < _WAKEUP_MAX_ATTEMPTS:
                retry_at = now + _WAKEUP_RETRY_DELAY
                row.status = "pending"
                row.due_at = _inside_or_next_send_window(retry_at)
                row.last_error = f"本次发送{status}，已安排重试：{error or status}"[:2000]
            else:
                row.status = status
                row.last_error = (error or status)[:2000]
        row.updated_at = now
        session.commit()


async def daily_touch_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            settings = get_settings()
            daily_enabled = settings.daily_touch_enabled
            service_enabled = settings.service_material_touch_enabled
            if daily_enabled or service_enabled:
                await _refresh_contacts_if_due()
            if daily_enabled:
                ensure_daily_touch_wakeups()
            if service_enabled:
                ensure_service_material_touch_wakeups()
            if daily_enabled or service_enabled:
                await process_due_agent_wakeups()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Daily touch worker tick failed: %s", type(exc).__name__)
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=get_settings().daily_touch_poll_seconds,
            )
        except asyncio.TimeoutError:
            pass


async def _refresh_contacts_if_due() -> None:
    global _last_contact_sync_at
    settings = get_settings()
    now = _utcnow()
    if not (settings.eyun_base_url and settings.eyun_authorization and settings.eyun_wid):
        return
    if _last_contact_sync_at and now - _last_contact_sync_at < timedelta(hours=2):
        return
    from app.domains.sales.services.contact_sync_service import sync_eyun_contacts

    await sync_eyun_contacts()
    _last_contact_sync_at = now


async def _notify_human_daily_touch(customer_id: str, wakeup_id: int) -> None:
    try:
        from app.domains.handoff.services.handoff_notification_service import enqueue_handoff_notification

        await enqueue_handoff_notification(
            customer_wc_id=customer_id,
            handoff_reason="daily_touch_human_reminder",
            trigger_message="该客户今日尚无真实 sent 消息，请当前负责人完成一次有价值的触达。",
            source_reference=f"daily-touch-{wakeup_id}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Daily touch human reminder failed: %s", type(exc).__name__)


def _mark_wakeup(wakeup_id: int, status: str, error: str | None = None) -> None:
    with _database_session() as session:
        row = session.get(AgentWakeupModel, wakeup_id)
        if row is None:
            return
        row.status = status
        row.last_error = error
        row.updated_at = _utcnow()
        session.commit()


def _outbound_messages(chat_result: dict[str, Any]) -> list[dict[str, Any]]:
    values = chat_result.get("outbound_messages")
    if not isinstance(values, list):
        values = [{"type": "text", "content": chat_result.get("answer")}]
    messages = []
    for value in values:
        if not isinstance(value, dict):
            continue
        message_type = str(value.get("type") or "")
        content = str(value.get("content") or "").strip()
        if message_type not in {"text", "image", "link_card", "mini_program", "material"} or not content:
            continue
        item = {"type": message_type, "content": content}
        if value.get("material_id"):
            item["material_id"] = int(value["material_id"])
        messages.append(item)
    return messages


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


def _chat_session() -> Session:
    url = get_settings().chat_log_db_url
    factory = _chat_factories.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(
            engine,
            tables=[ConversationModel.__table__, ConversationMessageModel.__table__],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _chat_factories[url] = factory
    return factory()


def _within_send_window(current: datetime) -> bool:
    settings = get_settings()
    start = _parse_time(settings.daily_touch_window_start, time(8, 0))
    end = _parse_time(settings.daily_touch_window_end, time(23, 0))
    return start <= current.timetz().replace(tzinfo=None) < end


def _inside_or_next_send_window(value: datetime) -> datetime:
    settings = get_settings()
    zone = _timezone(settings.daily_touch_timezone)
    local = value.astimezone(zone)
    start = _parse_time(settings.daily_touch_window_start, time(8, 0))
    end = _parse_time(settings.daily_touch_window_end, time(23, 0))
    local_time = local.timetz().replace(tzinfo=None)
    if start <= local_time < end:
        return value.astimezone(timezone.utc)
    target_date = local.date() if local_time < start else local.date() + timedelta(days=1)
    return datetime.combine(target_date, start, tzinfo=zone).astimezone(timezone.utc)


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


def _wakeup_id(source_batch_key: str | None) -> int | None:
    value = str(source_batch_key or "")
    if not value.startswith("agent_wakeup:"):
        return None
    try:
        return int(value.partition(":")[2])
    except ValueError:
        return None


def _touch_key(row: ConversationMessageModel) -> str:
    try:
        metadata = json.loads(row.metadata_json or "{}")
    except (TypeError, json.JSONDecodeError):
        metadata = {}
    if isinstance(metadata, dict):
        batch_key = str(metadata.get("source_batch_key") or "").strip()
        if batch_key:
            return f"batch:{batch_key}"
        turn_id = str(metadata.get("sales_turn_id") or "").strip()
        if turn_id:
            return f"turn:{turn_id}"
    return f"message:{row.id}"


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def _wakeup_dict(row: AgentWakeupModel) -> dict[str, Any]:
    return {
        "wakeup_ref": f"wakeup:{row.id}",
        "status": row.status,
        "due_at": _iso(row.due_at),
        "reason": row.reason,
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
