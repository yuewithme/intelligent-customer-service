import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ConversationMemoryModel,
    EyunContactModel,
    UnpurchasedSopDeliveryModel,
    UnpurchasedSopEnrollmentModel,
    UnpurchasedSopModel,
    UnpurchasedSopStepModel,
    UserProfileModel,
)
from app.schemas.common import AppError, ErrorCode
from app.schemas.unpurchased_sop import (
    UnpurchasedSopStepRequest,
    UnpurchasedSopUpdateRequest,
)
from app.services.message_risk_control_service import enqueue_eyun_outbound


logger = logging.getLogger("wechat_rag_bot.unpurchased_sop")
PURCHASE_TAGS = {"抖音已购", "微信已购"}
SOP_ID = 1
OWNER_ACCOUNT_ID = "default"
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_sessionmakers: dict[str, sessionmaker] = {}


def get_unpurchased_sop() -> dict[str, Any]:
    with _get_session() as session:
        sop = _get_or_create_sop(session)
        session.commit()
        steps = session.scalars(
            select(UnpurchasedSopStepModel)
            .where(UnpurchasedSopStepModel.sop_id == sop.id)
            .order_by(
                UnpurchasedSopStepModel.day_offset.asc(),
                UnpurchasedSopStepModel.send_time.asc(),
                UnpurchasedSopStepModel.position.asc(),
                UnpurchasedSopStepModel.id.asc(),
            )
        ).all()
        return {
            "sop": _sop_to_dict(sop),
            "steps": [_step_to_dict(step) for step in steps],
            "stats": _stats(session, sop.id),
        }


def update_unpurchased_sop(request: UnpurchasedSopUpdateRequest) -> dict[str, Any]:
    with _get_session() as session:
        sop = _get_or_create_sop(session)
        steps = session.scalars(
            select(UnpurchasedSopStepModel).where(
                UnpurchasedSopStepModel.sop_id == sop.id,
                UnpurchasedSopStepModel.enabled.is_(True),
            )
        ).all()
        invalid = [step.id for step in steps if not request.send_window_start <= step.send_time <= request.send_window_end]
        if invalid:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message=f"节点 {invalid} 的发送时间不在新的发送窗口内",
                status_code=422,
            )
        sop.name = request.name.strip()
        sop.enabled = request.enabled
        sop.dry_run = request.dry_run
        sop.send_window_start = request.send_window_start
        sop.send_window_end = request.send_window_end
        sop.contact_poll_interval_minutes = request.contact_poll_interval_minutes
        sop.contact_missing_threshold = request.contact_missing_threshold
        sop.updated_at = _utcnow()
        session.commit()
        session.refresh(sop)
        return _sop_to_dict(sop)


def create_unpurchased_sop_step(request: UnpurchasedSopStepRequest) -> dict[str, Any]:
    with _get_session() as session:
        sop = _get_or_create_sop(session)
        _validate_step_window(sop, request.send_time)
        now = _utcnow()
        messages = _request_messages(request)
        first = messages[0]
        row = UnpurchasedSopStepModel(
            sop_id=sop.id,
            day_offset=request.day_offset,
            send_time=request.send_time,
            message_type=first["message_type"],
            content=first["content"],
            preview_url=first.get("preview_url"),
            messages_json=json.dumps(messages, ensure_ascii=False),
            position=request.position,
            enabled=request.enabled,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _step_to_dict(row)


def update_unpurchased_sop_step(
    step_id: int, request: UnpurchasedSopStepRequest
) -> dict[str, Any]:
    with _get_session() as session:
        sop = _get_or_create_sop(session)
        row = _get_step(session, step_id)
        _validate_step_window(sop, request.send_time)
        row.day_offset = request.day_offset
        row.send_time = request.send_time
        messages = _request_messages(request)
        first = messages[0]
        row.message_type = first["message_type"]
        row.content = first["content"]
        row.preview_url = first.get("preview_url")
        row.messages_json = json.dumps(messages, ensure_ascii=False)
        row.position = request.position
        row.enabled = request.enabled
        row.updated_at = _utcnow()
        session.commit()
        session.refresh(row)
        return _step_to_dict(row)


def delete_unpurchased_sop_step(step_id: int) -> dict[str, Any]:
    with _get_session() as session:
        row = _get_step(session, step_id)
        session.delete(row)
        session.commit()
        return {"id": step_id, "deleted": True}


async def query_eyun_friend_ids(w_id: str) -> list[str]:
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        raise AppError(
            ErrorCode.STATE_FAILED,
            message="Eyun 联系人同步配置不完整",
            status_code=503,
        )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/getAddressList",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json={"wId": w_id},
        )
    response.raise_for_status()
    body = response.json()
    if str(body.get("code")) != "1000":
        raise AppError(
            ErrorCode.STATE_FAILED,
            message=f"Eyun 获取通讯录失败：{body.get('message') or body.get('code')}",
            status_code=502,
        )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    values = data.get("friends") if isinstance(data.get("friends"), list) else []
    return sorted({str(value).strip() for value in values if str(value).strip()})


async def sync_eyun_contacts(*, friend_ids: list[str] | None = None) -> dict[str, Any]:
    settings = get_settings()
    w_id = settings.eyun_wid.strip()
    if friend_ids is None:
        with _get_session() as session:
            baseline_pending = _get_or_create_sop(session).baseline_initialized_at is None
            session.commit()
        if baseline_pending:
            from app.services.eyun_contact_service import initialize_eyun_contacts

            await initialize_eyun_contacts(w_id=w_id)
        friend_ids = await query_eyun_friend_ids(w_id)
    current_ids = set(friend_ids)
    now = _utcnow()
    local_today = now.astimezone(SHANGHAI_TZ).date()
    new_count = 0
    removed_count = 0
    baseline_count = 0
    readded_count = 0
    detail_ids: list[str] = []
    with _get_session() as session:
        sop = _get_or_create_sop(session)
        first_sync = sop.baseline_initialized_at is None
        if first_sync and not current_ids:
            sop.last_contact_sync_at = now
            sop.updated_at = now
            session.commit()
            return {
                "total": 0,
                "baseline": 0,
                "new": 0,
                "readded": 0,
                "removed": 0,
                "baseline_pending": True,
                "synced_at": now.isoformat(),
            }
        rows = session.scalars(
            select(EyunContactModel).where(
                EyunContactModel.tenant_id == "tenant_default",
                EyunContactModel.owner_account_id == OWNER_ACCOUNT_ID,
            )
        ).all()
        by_wc_id = {row.wc_id: row for row in rows}
        for wc_id in current_ids:
            row = by_wc_id.get(wc_id)
            if row is None:
                source = "baseline" if first_sync else "polling"
                added_on = None if first_sync else local_today
                row = EyunContactModel(
                    tenant_id="tenant_default",
                    owner_account_id=OWNER_ACCOUNT_ID,
                    wc_id=wc_id,
                    current_w_id=w_id,
                    friend_added_on=added_on,
                    first_seen_at=now,
                    last_seen_at=now,
                    status="active",
                    missing_count=0,
                    discovery_source=source,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                by_wc_id[wc_id] = row
                if first_sync:
                    baseline_count += 1
                else:
                    new_count += 1
                    detail_ids.append(wc_id)
                    _set_profile_friend_added_at(session, wc_id, local_today)
                    _ensure_enrollment(session, sop.id, row, now)
                continue
            if row.status == "removed":
                row.friend_added_on = local_today
                row.discovery_source = "readded"
                readded_count += 1
                detail_ids.append(wc_id)
                _set_profile_friend_added_at(session, wc_id, local_today)
                _ensure_enrollment(session, sop.id, row, now)
            row.current_w_id = w_id
            row.status = "active"
            row.missing_count = 0
            row.last_seen_at = now
            row.updated_at = now

        for row in rows:
            if row.wc_id in current_ids or row.status == "removed":
                continue
            row.missing_count += 1
            row.updated_at = now
            if row.missing_count >= sop.contact_missing_threshold:
                row.status = "removed"
                removed_count += 1
                _exit_contact_enrollments(session, row.id, "contact_removed", now)

        if first_sync:
            sop.baseline_initialized_at = now
        sop.last_contact_sync_at = now
        sop.updated_at = now
        session.commit()
        result = {
            "total": len(current_ids),
            "baseline": baseline_count,
            "new": new_count,
            "readded": readded_count,
            "removed": removed_count,
            "synced_at": now.isoformat(),
        }
    await _refresh_new_contact_details(detail_ids, w_id)
    return result


async def run_unpurchased_sop_tick(*, force_contact_sync: bool = False) -> dict[str, int]:
    synced = 0
    with _get_session() as session:
        sop = _get_or_create_sop(session)
        last_sync = _aware(sop.last_contact_sync_at) if sop.last_contact_sync_at else None
        due_sync = last_sync is None or _utcnow() - last_sync >= timedelta(
            minutes=sop.contact_poll_interval_minutes
        )
        session.commit()
    if force_contact_sync or due_sync:
        await sync_eyun_contacts()
        synced = 1
    queued = await process_due_unpurchased_sop_deliveries()
    return {"contact_syncs": synced, "queued": queued}


async def unpurchased_sop_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            settings = get_settings()
            if settings.eyun_base_url and settings.eyun_authorization and settings.eyun_wid:
                await run_unpurchased_sop_tick()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unpurchased SOP worker tick failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=10)
        except asyncio.TimeoutError:
            pass


async def process_due_unpurchased_sop_deliveries(limit: int = 100) -> int:
    now = _utcnow()
    candidates: list[tuple[int, int, int, datetime]] = []
    with _get_session() as session:
        sop = _get_or_create_sop(session)
        if not sop.enabled:
            session.commit()
            return 0
        enrollments = session.scalars(
            select(UnpurchasedSopEnrollmentModel)
            .where(UnpurchasedSopEnrollmentModel.status == "active")
            .order_by(UnpurchasedSopEnrollmentModel.id.asc())
            .limit(limit)
        ).all()
        steps = session.scalars(
            select(UnpurchasedSopStepModel)
            .where(
                UnpurchasedSopStepModel.sop_id == sop.id,
                UnpurchasedSopStepModel.enabled.is_(True),
            )
            .order_by(UnpurchasedSopStepModel.day_offset.asc(), UnpurchasedSopStepModel.position.asc())
        ).all()
        for enrollment in enrollments:
            contact = session.get(EyunContactModel, enrollment.contact_id)
            if contact is None or contact.status != "active":
                _exit_enrollment(enrollment, "contact_removed", now)
                continue
            profile = session.get(UserProfileModel, contact.wc_id)
            if _has_purchase_tag(profile):
                _exit_enrollment(enrollment, "purchase_tag_added", now)
                _cancel_pending_deliveries(session, enrollment.id, now)
                continue
            if profile is not None and profile.is_human_handoff:
                continue
            if enrollment.paused_until and _aware(enrollment.paused_until) > now:
                continue
            for step in steps:
                if session.scalar(
                    select(UnpurchasedSopDeliveryModel.id).where(
                        UnpurchasedSopDeliveryModel.enrollment_id == enrollment.id,
                        UnpurchasedSopDeliveryModel.step_id == step.id,
                    )
                ):
                    continue
                due_at = _step_due_at(enrollment.friend_added_on, step, sop.timezone)
                if due_at <= now:
                    candidates.append((enrollment.id, contact.id, step.id, due_at))
        session.commit()

    queued = 0
    for enrollment_id, contact_id, step_id, due_at in candidates[:limit]:
        if await _create_and_queue_delivery(enrollment_id, contact_id, step_id, due_at):
            queued += 1
    return queued


async def _create_and_queue_delivery(
    enrollment_id: int, contact_id: int, step_id: int, due_at: datetime
) -> bool:
    now = _utcnow()
    with _get_session() as session:
        enrollment = session.get(UnpurchasedSopEnrollmentModel, enrollment_id)
        contact = session.get(EyunContactModel, contact_id)
        step = session.get(UnpurchasedSopStepModel, step_id)
        sop = session.get(UnpurchasedSopModel, SOP_ID)
        if not enrollment or enrollment.status != "active" or not contact or contact.status != "active":
            return False
        if not step or not step.enabled or not sop or not sop.enabled:
            return False
        profile = session.get(UserProfileModel, contact.wc_id)
        if _has_purchase_tag(profile):
            _exit_enrollment(enrollment, "purchase_tag_added", now)
            session.commit()
            return False
        messages = _step_messages(step)
        first = messages[0]
        delivery = UnpurchasedSopDeliveryModel(
            enrollment_id=enrollment.id,
            step_id=step.id,
            due_at=due_at,
            status="dry_run" if sop.dry_run else "creating",
            message_type=first["message_type"],
            content_snapshot=first["content"],
            preview_url_snapshot=first.get("preview_url"),
            messages_snapshot_json=json.dumps(messages, ensure_ascii=False),
            outbound_message_ids_json="[]",
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        session.add(delivery)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            return False
        session.refresh(delivery)
        if sop.dry_run:
            return True
        delivery_id = delivery.id
        w_id = contact.current_w_id or get_settings().eyun_wid
        wc_id = contact.wc_id
    try:
        outbounds = await _enqueue_sop_message_sequence(
            w_id=w_id,
            wc_id=wc_id,
            messages=messages,
            source_key=f"unpurchased_sop:{delivery_id}",
        )
    except Exception as exc:
        sync_sop_delivery_status(delivery_id, "failed", str(exc))
        return False
    with _get_session() as session:
        delivery = session.get(UnpurchasedSopDeliveryModel, delivery_id)
        if delivery:
            delivery.status = "queued"
            outbound_ids = [int(outbound["id"]) for outbound in outbounds]
            delivery.outbound_message_id = outbound_ids[0]
            delivery.outbound_message_ids_json = json.dumps(outbound_ids)
            delivery.updated_at = _utcnow()
            session.commit()
    return True


async def test_send_unpurchased_sop_step(step_id: int, contact_id: int) -> dict[str, Any]:
    with _get_session() as session:
        step = _get_step(session, step_id)
        contact = session.get(EyunContactModel, contact_id)
        if contact is None or contact.status != "active":
            raise AppError(ErrorCode.REQUEST_INVALID, message="联系人不存在或已不是好友", status_code=404)
        messages = _step_messages(step)
        w_id = contact.current_w_id or get_settings().eyun_wid
        wc_id = contact.wc_id
    outbounds = await _enqueue_sop_message_sequence(
        w_id=w_id,
        wc_id=wc_id,
        messages=messages,
        source_key=f"unpurchased_sop_test:{step_id}:{contact_id}",
    )
    outbound_ids = [int(outbound["id"]) for outbound in outbounds]
    return {
        "outbound_message_id": outbound_ids[0],
        "outbound_message_ids": outbound_ids,
        "status": outbounds[0]["status"],
    }


async def _enqueue_sop_message_sequence(
    *, w_id: str, wc_id: str, messages: list[dict[str, Any]], source_key: str
) -> list[dict[str, Any]]:
    outbounds: list[dict[str, Any]] = []
    dependency_id: int | None = None
    total = len(messages)
    for index, message in enumerate(messages):
        outbound = await enqueue_eyun_outbound(
            w_id=w_id,
            wc_id=wc_id,
            content=_message_outbound_content(message),
            source_batch_key=f"{source_key}:{index}:{total}",
            message_type=message["message_type"],
            depends_on_outbound_id=dependency_id,
            due_at=_utcnow(),
        )
        outbounds.append(outbound)
        dependency_id = int(outbound["id"])
    return outbounds


def list_unpurchased_sop_contacts(page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    with _get_session() as session:
        total = session.scalar(select(func.count(EyunContactModel.id))) or 0
        rows = session.scalars(
            select(EyunContactModel)
            .order_by(EyunContactModel.last_seen_at.desc(), EyunContactModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = []
        for row in rows:
            profile = session.get(UserProfileModel, row.wc_id)
            enrollment = session.scalar(
                select(UnpurchasedSopEnrollmentModel)
                .where(UnpurchasedSopEnrollmentModel.contact_id == row.id)
                .order_by(UnpurchasedSopEnrollmentModel.id.desc())
                .limit(1)
            )
            items.append(_contact_to_dict(row, profile, enrollment))
        return {"items": items, "total": total, "page": page, "page_size": page_size}


def list_unpurchased_sop_deliveries(page: int = 1, page_size: int = 50) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(200, max(1, page_size))
    with _get_session() as session:
        total = session.scalar(select(func.count(UnpurchasedSopDeliveryModel.id))) or 0
        rows = session.scalars(
            select(UnpurchasedSopDeliveryModel)
            .order_by(UnpurchasedSopDeliveryModel.created_at.desc(), UnpurchasedSopDeliveryModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        items = []
        for row in rows:
            enrollment = session.get(UnpurchasedSopEnrollmentModel, row.enrollment_id)
            contact = session.get(EyunContactModel, enrollment.contact_id) if enrollment else None
            items.append(_delivery_to_dict(row, contact))
        return {"items": items, "total": total, "page": page, "page_size": page_size}


def validate_sop_delivery_before_send(source_batch_key: str | None) -> bool:
    delivery_id = _delivery_id_from_source(source_batch_key)
    if delivery_id is None:
        return True
    now = _utcnow()
    with _get_session() as session:
        delivery = session.get(UnpurchasedSopDeliveryModel, delivery_id)
        if delivery is None or delivery.status in {"cancelled", "sent", "failed"}:
            return False
        enrollment = session.get(UnpurchasedSopEnrollmentModel, delivery.enrollment_id)
        step = session.get(UnpurchasedSopStepModel, delivery.step_id)
        sop = session.get(UnpurchasedSopModel, enrollment.sop_id) if enrollment else None
        contact = session.get(EyunContactModel, enrollment.contact_id) if enrollment else None
        profile = session.get(UserProfileModel, contact.wc_id) if contact else None
        paused = bool(enrollment and enrollment.paused_until and _aware(enrollment.paused_until) > now)
        flow_disabled = not sop or not sop.enabled or not step or not step.enabled
        if not enrollment or enrollment.status != "active" or not contact or contact.status != "active" or _has_purchase_tag(profile) or paused or flow_disabled:
            delivery.status = "cancelled"
            delivery.last_error = "客户回复暂停中" if paused else "发送前条件已不满足"
            delivery.updated_at = now
            if enrollment and _has_purchase_tag(profile):
                _exit_enrollment(enrollment, "purchase_tag_added", now)
            session.commit()
            return False
        return True


def pause_unpurchased_sop_for_customer(user_id: str, hours: int = 24) -> None:
    now = _utcnow()
    paused_until = now + timedelta(hours=hours)
    with _get_session() as session:
        contact = session.scalar(
            select(EyunContactModel).where(
                EyunContactModel.wc_id == user_id,
                EyunContactModel.status == "active",
            )
        )
        if contact is None:
            return
        enrollments = session.scalars(
            select(UnpurchasedSopEnrollmentModel).where(
                UnpurchasedSopEnrollmentModel.contact_id == contact.id,
                UnpurchasedSopEnrollmentModel.status == "active",
            )
        ).all()
        for enrollment in enrollments:
            enrollment.paused_until = paused_until
            enrollment.updated_at = now
            sop = session.get(UnpurchasedSopModel, enrollment.sop_id)
            if sop is None:
                continue
            steps = session.scalars(
                select(UnpurchasedSopStepModel).where(
                    UnpurchasedSopStepModel.sop_id == sop.id,
                    UnpurchasedSopStepModel.enabled.is_(True),
                )
            ).all()
            for step in steps:
                due_at = _step_due_at(enrollment.friend_added_on, step, sop.timezone)
                if due_at > paused_until:
                    continue
                exists = session.scalar(
                    select(UnpurchasedSopDeliveryModel.id).where(
                        UnpurchasedSopDeliveryModel.enrollment_id == enrollment.id,
                        UnpurchasedSopDeliveryModel.step_id == step.id,
                    )
                )
                if exists:
                    continue
                messages = _step_messages(step)
                first = messages[0]
                session.add(
                    UnpurchasedSopDeliveryModel(
                        enrollment_id=enrollment.id,
                        step_id=step.id,
                        due_at=due_at,
                        status="skipped_reply",
                        message_type=first["message_type"],
                        content_snapshot=first["content"],
                        preview_url_snapshot=first.get("preview_url"),
                        messages_snapshot_json=json.dumps(
                            messages, ensure_ascii=False
                        ),
                        outbound_message_ids_json="[]",
                        attempts=0,
                        last_error="客户回复后24小时内暂停自动触达",
                        created_at=now,
                        updated_at=now,
                    )
                )
        session.commit()


def sync_sop_delivery_from_outbound(
    source_batch_key: str | None, status: str, error: str | None = None
) -> None:
    delivery_id, message_index, message_total = _delivery_source_parts(
        source_batch_key
    )
    if delivery_id is not None:
        sync_sop_delivery_status(
            delivery_id,
            status,
            error,
            message_index=message_index,
            message_total=message_total,
        )


def sync_sop_delivery_status(
    delivery_id: int,
    status: str,
    error: str | None = None,
    *,
    message_index: int = 0,
    message_total: int = 1,
) -> None:
    with _get_session() as session:
        row = session.get(UnpurchasedSopDeliveryModel, delivery_id)
        if row is None:
            return
        if row.status == "failed" and status == "cancelled":
            return
        if status == "sent" and message_index < message_total - 1:
            row.status = "queued"
            row.last_error = None
            row.updated_at = _utcnow()
            session.commit()
            return
        first_sent_transition = status == "sent" and row.status != "sent"
        row.status = status
        row.last_error = error
        row.updated_at = _utcnow()
        if status == "sent":
            row.sent_at = row.updated_at
        if first_sent_transition:
            enrollment = session.get(UnpurchasedSopEnrollmentModel, row.enrollment_id)
            contact = session.get(EyunContactModel, enrollment.contact_id) if enrollment else None
            trace_id = f"unpurchased_sop_delivery:{row.id}"
            if contact and not session.scalar(
                select(ConversationMemoryModel.id).where(
                    ConversationMemoryModel.trace_id == trace_id
                )
            ):
                memory_content = _delivery_memory_content(row)
                session.add(
                    ConversationMemoryModel(
                        user_id=contact.wc_id,
                        tenant_id=contact.tenant_id,
                        session_id="default",
                        role="assistant",
                        content=memory_content,
                        intent="unpurchased_sop",
                        route="unpurchased_sop",
                        trace_id=trace_id,
                        created_at=row.sent_at,
                    )
                )
        if status in {"failed", "queued"}:
            row.attempts += 1 if status == "failed" else 0
        session.commit()


def _delivery_id_from_source(value: str | None) -> int | None:
    return _delivery_source_parts(value)[0]


def _delivery_source_parts(value: str | None) -> tuple[int | None, int, int]:
    if not value or not value.startswith("unpurchased_sop:"):
        return None, 0, 1
    parts = value.split(":")
    try:
        delivery_id = int(parts[1])
        message_index = int(parts[2]) if len(parts) > 2 else 0
        message_total = int(parts[3]) if len(parts) > 3 else 1
        return delivery_id, message_index, max(1, message_total)
    except (ValueError, IndexError):
        return None, 0, 1


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(
            engine,
            tables=[
                UserProfileModel.__table__,
                ConversationMemoryModel.__table__,
                EyunContactModel.__table__,
                UnpurchasedSopModel.__table__,
                UnpurchasedSopStepModel.__table__,
                UnpurchasedSopEnrollmentModel.__table__,
                UnpurchasedSopDeliveryModel.__table__,
            ],
        )
        columns = {
            column["name"] for column in inspect(engine).get_columns("user_profiles")
        }
        if "friend_added_at" not in columns:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "ALTER TABLE user_profiles "
                        "ADD COLUMN friend_added_at DATETIME"
                    )
                )
        sop_columns = {
            column["name"] for column in inspect(engine).get_columns("unpurchased_sops")
        }
        sop_column_migrations = {
            "contact_poll_interval_minutes": (
                "ALTER TABLE unpurchased_sops "
                "ADD COLUMN contact_poll_interval_minutes INTEGER DEFAULT 120"
            ),
            "contact_missing_threshold": (
                "ALTER TABLE unpurchased_sops "
                "ADD COLUMN contact_missing_threshold INTEGER DEFAULT 3"
            ),
        }
        with engine.begin() as connection:
            for column_name, statement in sop_column_migrations.items():
                if column_name not in sop_columns:
                    connection.execute(text(statement))
        table_column_migrations = {
            "unpurchased_sop_steps": {
                "messages_json": (
                    "ALTER TABLE unpurchased_sop_steps "
                    "ADD COLUMN messages_json TEXT DEFAULT '[]'"
                )
            },
            "unpurchased_sop_deliveries": {
                "messages_snapshot_json": (
                    "ALTER TABLE unpurchased_sop_deliveries "
                    "ADD COLUMN messages_snapshot_json TEXT DEFAULT '[]'"
                ),
                "outbound_message_ids_json": (
                    "ALTER TABLE unpurchased_sop_deliveries "
                    "ADD COLUMN outbound_message_ids_json TEXT DEFAULT '[]'"
                ),
            },
        }
        with engine.begin() as connection:
            for table_name, migrations in table_column_migrations.items():
                existing = {
                    column["name"]
                    for column in inspect(engine).get_columns(table_name)
                }
                for column_name, statement in migrations.items():
                    if column_name not in existing:
                        connection.execute(text(statement))
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


async def _refresh_new_contact_details(wc_ids: list[str], w_id: str) -> None:
    if not wc_ids or not w_id:
        return
    from app.services.eyun_contact_service import get_eyun_contact_snapshot
    from app.services.user_profile_service import ensure_user_profile

    for index, wc_id in enumerate(wc_ids):
        snapshot = await get_eyun_contact_snapshot(w_id=w_id, wc_id=wc_id)
        if snapshot:
            await ensure_user_profile(
                wc_id,
                tenant_id="tenant_default",
                channel="wechat",
                basic_info=snapshot,
            )
            display_name = str(
                snapshot.get("remark_name")
                or snapshot.get("nickname")
                or snapshot.get("display_name")
                or ""
            ).strip()
            with _get_session() as session:
                row = session.scalar(
                    select(EyunContactModel).where(EyunContactModel.wc_id == wc_id)
                )
                if row:
                    row.display_name = display_name or row.display_name
                    row.avatar_url = str(snapshot.get("avatar_url") or "") or row.avatar_url
                    row.updated_at = _utcnow()
                    session.commit()
        if index < len(wc_ids) - 1:
            await asyncio.sleep(0.3)


def _get_or_create_sop(session: Session) -> UnpurchasedSopModel:
    row = session.get(UnpurchasedSopModel, SOP_ID)
    if row is None:
        now = _utcnow()
        row = UnpurchasedSopModel(
            id=SOP_ID,
            name="未购SOP",
            enabled=False,
            dry_run=True,
            send_window_start="09:00",
            send_window_end="20:00",
            timezone="Asia/Shanghai",
            contact_poll_interval_minutes=120,
            contact_missing_threshold=3,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
    return row


def _get_step(session: Session, step_id: int) -> UnpurchasedSopStepModel:
    row = session.get(UnpurchasedSopStepModel, step_id)
    if row is None or row.sop_id != SOP_ID:
        raise AppError(ErrorCode.REQUEST_INVALID, message="SOP节点不存在", status_code=404)
    return row


def _ensure_enrollment(
    session: Session, sop_id: int, contact: EyunContactModel, now: datetime
) -> None:
    if contact.friend_added_on is None:
        return
    existing = session.scalar(
        select(UnpurchasedSopEnrollmentModel).where(
            UnpurchasedSopEnrollmentModel.sop_id == sop_id,
            UnpurchasedSopEnrollmentModel.contact_id == contact.id,
            UnpurchasedSopEnrollmentModel.friend_added_on == contact.friend_added_on,
        )
    )
    if existing is None:
        session.add(
            UnpurchasedSopEnrollmentModel(
                sop_id=sop_id,
                contact_id=contact.id,
                friend_added_on=contact.friend_added_on,
                status="active",
                enrolled_at=now,
                updated_at=now,
            )
        )


def _set_profile_friend_added_at(session: Session, wc_id: str, added_on: date) -> None:
    profile = session.get(UserProfileModel, wc_id)
    now = _utcnow()
    value = datetime.combine(added_on, time.min, tzinfo=SHANGHAI_TZ).astimezone(timezone.utc)
    if profile is None:
        profile = UserProfileModel(
            user_id=wc_id,
            tenant_id="tenant_default",
            channel="wechat",
            current_stage="unknown",
            risk_level="normal",
            friend_added_at=value,
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
    else:
        profile.friend_added_at = value
        profile.updated_at = now


def _exit_contact_enrollments(session: Session, contact_id: int, reason: str, now: datetime) -> None:
    rows = session.scalars(
        select(UnpurchasedSopEnrollmentModel).where(
            UnpurchasedSopEnrollmentModel.contact_id == contact_id,
            UnpurchasedSopEnrollmentModel.status == "active",
        )
    ).all()
    for row in rows:
        _exit_enrollment(row, reason, now)
        _cancel_pending_deliveries(session, row.id, now)


def _exit_enrollment(row: UnpurchasedSopEnrollmentModel, reason: str, now: datetime) -> None:
    row.status = "exited"
    row.exit_reason = reason
    row.updated_at = now


def _cancel_pending_deliveries(session: Session, enrollment_id: int, now: datetime) -> None:
    rows = session.scalars(
        select(UnpurchasedSopDeliveryModel).where(
            UnpurchasedSopDeliveryModel.enrollment_id == enrollment_id,
            UnpurchasedSopDeliveryModel.status.in_(("scheduled", "creating", "queued")),
        )
    ).all()
    for row in rows:
        row.status = "cancelled"
        row.updated_at = now


def _has_purchase_tag(profile: UserProfileModel | None) -> bool:
    if profile is None:
        return False
    try:
        tags = json.loads(profile.customer_tags_json or "[]")
    except json.JSONDecodeError:
        return False
    return bool(PURCHASE_TAGS.intersection(str(tag) for tag in tags))


def _step_due_at(friend_added_on: date, step: UnpurchasedSopStepModel, timezone_name: str) -> datetime:
    local_tz = SHANGHAI_TZ
    local_date = friend_added_on + timedelta(days=step.day_offset)
    hour, minute = (int(value) for value in step.send_time.split(":"))
    return datetime.combine(local_date, time(hour, minute), tzinfo=local_tz).astimezone(timezone.utc)


def _validate_step_window(sop: UnpurchasedSopModel, send_time: str) -> None:
    if not sop.send_window_start <= send_time <= sop.send_window_end:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="节点发送时间必须位于SOP发送窗口内",
            status_code=422,
        )


def _stats(session: Session, sop_id: int) -> dict[str, int]:
    return {
        "contacts": session.scalar(select(func.count(EyunContactModel.id))) or 0,
        "active_contacts": session.scalar(
            select(func.count(EyunContactModel.id)).where(EyunContactModel.status == "active")
        ) or 0,
        "active_enrollments": session.scalar(
            select(func.count(UnpurchasedSopEnrollmentModel.id)).where(
                UnpurchasedSopEnrollmentModel.sop_id == sop_id,
                UnpurchasedSopEnrollmentModel.status == "active",
            )
        ) or 0,
        "sent": session.scalar(
            select(func.count(UnpurchasedSopDeliveryModel.id)).where(
                UnpurchasedSopDeliveryModel.status == "sent"
            )
        ) or 0,
        "failed": session.scalar(
            select(func.count(UnpurchasedSopDeliveryModel.id)).where(
                UnpurchasedSopDeliveryModel.status == "failed"
            )
        ) or 0,
    }


def _sop_to_dict(row: UnpurchasedSopModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "enabled": row.enabled,
        "dry_run": row.dry_run,
        "send_window_start": row.send_window_start,
        "send_window_end": row.send_window_end,
        "timezone": row.timezone,
        "contact_poll_interval_minutes": row.contact_poll_interval_minutes,
        "contact_missing_threshold": row.contact_missing_threshold,
        "baseline_initialized_at": _iso(row.baseline_initialized_at),
        "last_contact_sync_at": _iso(row.last_contact_sync_at),
        "updated_at": _iso(row.updated_at),
    }


def _step_to_dict(row: UnpurchasedSopStepModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "sop_id": row.sop_id,
        "day_offset": row.day_offset,
        "send_time": row.send_time,
        "message_type": row.message_type,
        "content": row.content,
        "preview_url": row.preview_url,
        "messages": _step_messages(row),
        "position": row.position,
        "enabled": row.enabled,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _contact_to_dict(
    row: EyunContactModel,
    profile: UserProfileModel | None,
    enrollment: UnpurchasedSopEnrollmentModel | None,
) -> dict[str, Any]:
    try:
        tags = json.loads(profile.customer_tags_json or "[]") if profile else []
    except json.JSONDecodeError:
        tags = []
    return {
        "id": row.id,
        "wc_id": row.wc_id,
        "display_name": row.display_name,
        "avatar_url": row.avatar_url,
        "friend_added_on": row.friend_added_on.isoformat() if row.friend_added_on else None,
        "first_seen_at": _iso(row.first_seen_at),
        "last_seen_at": _iso(row.last_seen_at),
        "status": row.status,
        "discovery_source": row.discovery_source,
        "customer_tags": tags,
        "enrollment_status": enrollment.status if enrollment else None,
        "exit_reason": enrollment.exit_reason if enrollment else None,
    }


def _delivery_to_dict(
    row: UnpurchasedSopDeliveryModel, contact: EyunContactModel | None
) -> dict[str, Any]:
    return {
        "id": row.id,
        "contact_id": contact.id if contact else None,
        "wc_id": contact.wc_id if contact else None,
        "display_name": contact.display_name if contact else None,
        "step_id": row.step_id,
        "due_at": _iso(row.due_at),
        "status": row.status,
        "message_type": row.message_type,
        "content": row.content_snapshot,
        "preview_url": row.preview_url_snapshot,
        "messages": _delivery_messages(row),
        "outbound_message_id": row.outbound_message_id,
        "outbound_message_ids": _json_int_list(row.outbound_message_ids_json),
        "attempts": row.attempts,
        "last_error": row.last_error,
        "sent_at": _iso(row.sent_at),
        "created_at": _iso(row.created_at),
    }


def _request_messages(request: UnpurchasedSopStepRequest) -> list[dict[str, Any]]:
    return [
        {
            "message_type": message.message_type,
            "content": message.content,
            "preview_url": (message.preview_url or "").strip() or None,
        }
        for message in request.messages
    ]


def _step_messages(row: UnpurchasedSopStepModel) -> list[dict[str, Any]]:
    return _stored_messages(
        row.messages_json,
        row.message_type,
        row.content,
        row.preview_url,
    )


def _delivery_messages(row: UnpurchasedSopDeliveryModel) -> list[dict[str, Any]]:
    return _stored_messages(
        row.messages_snapshot_json,
        row.message_type,
        row.content_snapshot,
        row.preview_url_snapshot,
    )


def _stored_messages(
    raw: str | None,
    fallback_type: str,
    fallback_content: str,
    fallback_preview_url: str | None,
) -> list[dict[str, Any]]:
    try:
        values = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        values = []
    messages = []
    if isinstance(values, list):
        for value in values:
            if not isinstance(value, dict):
                continue
            message_type = str(value.get("message_type") or "")
            content = str(value.get("content") or "").strip()
            if message_type in {"text", "image", "video"} and content:
                messages.append(
                    {
                        "message_type": message_type,
                        "content": content,
                        "preview_url": str(value.get("preview_url") or "") or None,
                    }
                )
    if messages:
        return messages
    return [
        {
            "message_type": fallback_type,
            "content": fallback_content,
            "preview_url": fallback_preview_url,
        }
    ]


def _message_outbound_content(message: dict[str, Any]) -> str:
    if message["message_type"] == "video":
        return json.dumps(
            {
                "path": message["content"],
                "thumb_path": message.get("preview_url") or "",
            },
            ensure_ascii=False,
        )
    return str(message["content"])


def _delivery_memory_content(row: UnpurchasedSopDeliveryModel) -> str:
    parts = []
    for message in _delivery_messages(row):
        if message["message_type"] == "image":
            parts.append(f"[未购SOP发送图片] {message['content']}")
        elif message["message_type"] == "video":
            parts.append(f"[未购SOP发送视频] {message['content']}")
        else:
            parts.append(str(message["content"]))
    return "\n".join(parts)


def _json_int_list(raw: str | None) -> list[int]:
    try:
        values = json.loads(raw or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return [int(value) for value in values if isinstance(value, int)]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return _aware(value).isoformat() if value else None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
