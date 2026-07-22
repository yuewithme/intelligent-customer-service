import asyncio
import json
import logging
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ConversationMessageModel,
    ConversationModel,
    EyunBulkSendJobModel,
    EyunInboundBatchModel,
    EyunInboundMessageModel,
    EyunImagePromptRateModel,
    EyunMediaMaterialModel,
    EyunOutboundMessageModel,
    EyunSendRateModel,
)
from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat
from app.services.conversation_service import (
    HANDOFF_PENDING,
    HUMAN_ACTIVE,
    RESOLVED,
    ensure_outbound_conversation_message,
    force_handoff,
    make_conversation_id,
    record_customer_message,
    update_outbound_message_delivery,
)
from app.services.customer_reply_formatter import split_customer_messages
from app.services.eyun_contact_service import get_eyun_contact_snapshot
from app.services.intent_observation_service import record_bypassed_intent_observation
from app.services.orchid_material_service import (
    orchid_material_chat_result,
    orchid_material_video_issue_chat_result,
)
from app.services.user_profile_service import (
    add_verified_customer_tag,
    append_conversation_memory,
)


logger = logging.getLogger("wechat_rag_bot.eyun_risk_control")

_URL_PATTERN = re.compile(r"https?://[^\s<>，。！？；：、（）【】“”‘’《》]+")
_URL_TRAILING_PUNCTUATION = "，。！？；：、,.!?;:)]}》〉”’\"'"

WRONG_STORE_ORDER_REPLY = "亲这不是我们萧岚苑的订单截图哦"

_sessionmakers: dict[str, sessionmaker] = {}
_initialized_urls: set[str] = set()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_eyun_batch_key(*, w_id: str, target_wc_id: str, from_user: str) -> str:
    return f"{w_id}:{target_wc_id or from_user}"


async def enqueue_eyun_inbound(payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    now = utcnow()
    is_image = str(payload.get("messageType") or "") == "60002"
    content = "[图片]" if is_image else str(data.get("content") or "").strip()
    w_id = str(data.get("wId") or payload.get("wId") or settings.eyun_wid or "")
    from_user = str(data.get("fromUser") or "")
    from_group = str(data.get("fromGroup") or "")
    target_wc_id = from_group or from_user
    wc_id = str(payload.get("wcId") or data.get("toUser") or "")
    batch_key = build_eyun_batch_key(
        w_id=w_id, target_wc_id=target_wc_id, from_user=from_user
    )
    due_at = now + timedelta(seconds=settings.eyun_inbound_debounce_seconds)
    provider_message_id = _provider_message_id(payload, batch_key, now)

    with _get_session() as session:
        existing_message = session.scalar(
            select(EyunInboundMessageModel).where(
                EyunInboundMessageModel.provider_message_id == provider_message_id
            )
        )
        if existing_message is not None:
            batch = _get_batch(session, existing_message.batch_key)
            return _inbound_batch_to_dict(batch)

        batch = _get_batch(session, batch_key)
        if batch is None:
            batch = EyunInboundBatchModel(
                batch_key=batch_key,
                w_id=w_id or None,
                wc_id=wc_id,
                target_wc_id=target_wc_id,
                from_user=from_user or None,
                from_group=from_group or None,
                account=str(payload.get("account") or "") or None,
                message_type=str(payload.get("messageType") or ""),
                content=content,
                message_count=1,
                status="pending",
                due_at=due_at if is_image else now,
                created_at=now,
                updated_at=now,
            )
            session.add(batch)
        elif batch.status != "pending":
            batch.w_id = w_id or None
            batch.wc_id = wc_id
            batch.target_wc_id = target_wc_id
            batch.from_user = from_user or None
            batch.from_group = from_group or None
            batch.account = str(payload.get("account") or "") or None
            batch.message_type = str(payload.get("messageType") or "")
            batch.content = content
            batch.message_count = 1
            batch.status = "pending"
            batch.due_at = due_at
            batch.created_at = now
            batch.updated_at = now
        else:
            batch.content = f"{batch.content}\n{content}" if batch.content else content
            batch.message_count = (batch.message_count or 0) + 1
            batch.due_at = due_at
            batch.updated_at = now

        session.add(
            EyunInboundMessageModel(
                provider_message_id=provider_message_id,
                batch_key=batch_key,
                content=content,
                payload_json=json.dumps(payload, ensure_ascii=False),
                created_at=now,
            )
        )
        session.commit()
        session.refresh(batch)
        result = _inbound_batch_to_dict(batch)
    if from_user and not from_group:
        from app.services.unpurchased_sop_service import pause_unpurchased_sop_for_customer

        pause_unpurchased_sop_for_customer(from_user)
    return result


async def process_due_eyun_inbound_batches(limit: int = 10) -> int:
    now = utcnow()
    with _get_session() as session:
        rows = session.scalars(
            select(EyunInboundBatchModel)
            .where(
                EyunInboundBatchModel.status == "pending",
                EyunInboundBatchModel.due_at <= now,
            )
            .order_by(EyunInboundBatchModel.due_at.asc(), EyunInboundBatchModel.id.asc())
            .limit(limit)
        ).all()
        for row in rows:
            row.status = "processing"
            row.updated_at = now
        session.commit()
        batch_ids = [row.id for row in rows]

    attempted = 0
    for batch_id in batch_ids:
        attempted += 1
        await _process_inbound_batch(batch_id)
    return attempted


def random_reply_delay_seconds() -> int:
    settings = get_settings()
    return random.randint(
        settings.eyun_reply_jitter_min_seconds,
        settings.eyun_reply_jitter_max_seconds,
    )


def random_outbound_spacing_seconds() -> float:
    settings = get_settings()
    minimum = _minimum_outbound_interval_seconds()
    maximum = max(minimum + 0.01, settings.eyun_send_max_interval_seconds)
    return random.uniform(minimum, maximum)


async def enqueue_wechat_outbound(
    *,
    w_id: str,
    wc_id: str,
    content: str,
    source_batch_key: str | None,
    message_type: str = "text",
    conversation_message_id: int | None = None,
    depends_on_outbound_id: int | None = None,
    material_id: int | None = None,
    bulk_job_id: int | None = None,
    due_at: datetime | None = None,
    channel: str = "wechat",
    user_id: str | None = None,
    session_id: str | None = "default",
    tenant_id: str = "tenant_default",
    sender_type: str = "ai",
    sender_id: str | None = "ai",
    source_type: str | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    original_message_type = message_type
    original_content = content
    if conversation_message_id is None:
        origin = source_type or _source_type_from_batch_key(source_batch_key)
        conversation_message = await ensure_outbound_conversation_message(
            channel=channel,
            user_id=(user_id or wc_id).strip(),
            session_id=session_id,
            tenant_id=tenant_id,
            content=original_content,
            message_type=original_message_type,
            sender_type=sender_type,
            sender_id=sender_id,
            trace_id=source_batch_key,
            delivery_status="queued",
            route=origin,
            reconcile_pending=False,
            metadata={
                "origin": origin,
                "source_type": origin,
                "source_id": source_id,
                "source_batch_key": source_batch_key,
                "w_id": w_id,
                "wc_id": wc_id,
            },
        )
        conversation_message_id = int(conversation_message["id"])
    try:
        if material_id is None and message_type in {"image", "video"}:
            from app.services.eyun_material_service import materialize_eyun_outbound_media

            material = await materialize_eyun_outbound_media(
                w_id=w_id, message_type=message_type, content=content
            )
            material_id = int(material["id"])
            message_type = f"received_{material['media_type']}"
            content = ""
        if material_id is not None:
            from app.services.eyun_material_service import get_ready_material

            get_ready_material(material_id)
        now = utcnow()
        row = EyunOutboundMessageModel(
            w_id=w_id,
            wc_id=wc_id,
            content=_encode_outbound_content(message_type, content),
            source_batch_key=source_batch_key,
            conversation_message_id=conversation_message_id,
            depends_on_outbound_id=depends_on_outbound_id,
            material_id=material_id,
            bulk_job_id=bulk_job_id,
            status="queued",
            due_at=due_at or now + timedelta(seconds=random_reply_delay_seconds()),
            attempts=0,
            created_at=now,
            updated_at=now,
        )
        with _get_session() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return _outbound_to_dict(row)
    except Exception:
        update_outbound_message_delivery(conversation_message_id, status="failed")
        raise


async def enqueue_eyun_outbound(
    *,
    w_id: str,
    wc_id: str,
    content: str,
    source_batch_key: str | None,
    message_type: str = "text",
    conversation_message_id: int | None = None,
    depends_on_outbound_id: int | None = None,
    material_id: int | None = None,
    bulk_job_id: int | None = None,
    due_at: datetime | None = None,
) -> dict[str, Any]:
    """Backward-compatible alias; all Eyun sends still enter the shared queue."""
    return await enqueue_wechat_outbound(
        w_id=w_id,
        wc_id=wc_id,
        content=content,
        source_batch_key=source_batch_key,
        message_type=message_type,
        conversation_message_id=conversation_message_id,
        depends_on_outbound_id=depends_on_outbound_id,
        material_id=material_id,
        bulk_job_id=bulk_job_id,
        due_at=due_at,
    )


async def enqueue_wechat_bulk_send(
    *,
    recipients: list[dict[str, str]],
    items: list[dict[str, Any]],
    source_type: str,
    source_id: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    if not recipients or not items:
        raise ValueError("bulk send requires recipients and items")
    w_ids = {str(recipient.get("w_id") or "").strip() for recipient in recipients}
    if "" in w_ids or len(w_ids) != 1:
        raise ValueError("one bulk job must use exactly one w_id")
    from app.services.eyun_material_service import get_ready_material

    normalized_items: list[dict[str, Any]] = []
    for item in items:
        item_type = str(item.get("type") or "")
        if item_type == "material":
            material_id = int(item.get("material_id") or 0)
            material = get_ready_material(material_id)
            normalized_items.append(
                {
                    "message_type": f"received_{material['media_type']}",
                    "content": "",
                    "material_id": material_id,
                }
            )
        elif item_type == "text" and str(item.get("content") or "").strip():
            normalized_items.append(
                {"message_type": "text", "content": str(item["content"]).strip()}
            )
        else:
            raise ValueError("bulk media must reference a ready material_id")
    now = utcnow()
    outbound_ids: list[int] = []
    with _get_session() as session:
        job = EyunBulkSendJobModel(
            source_type=source_type,
            source_id=source_id,
            w_id=next(iter(w_ids)),
            status="queued",
            total_count=len(recipients) * len(normalized_items),
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        session.add(job)
        session.flush()
        job_id = job.id
        session.commit()
    try:
        for recipient_index, recipient in enumerate(recipients):
            wc_id = str(recipient.get("wc_id") or "").strip()
            if not wc_id:
                raise ValueError("bulk recipient wc_id is required")
            dependency_id: int | None = None
            due_at = now + timedelta(seconds=random_reply_delay_seconds())
            for item_index, item in enumerate(normalized_items):
                outbound = await enqueue_wechat_outbound(
                    w_id=str(recipient["w_id"]).strip(),
                    wc_id=wc_id,
                    content=str(item.get("content") or ""),
                    source_batch_key=f"bulk:{job_id}:{recipient_index}:{item_index}",
                    message_type=str(item["message_type"]),
                    depends_on_outbound_id=dependency_id,
                    material_id=item.get("material_id"),
                    bulk_job_id=job_id,
                    due_at=due_at,
                    user_id=wc_id,
                    source_type=source_type,
                    source_id=source_id,
                    sender_type="human" if created_by else "system",
                    sender_id=created_by or "system",
                )
                dependency_id = int(outbound["id"])
                outbound_ids.append(dependency_id)
                due_at += timedelta(seconds=random_outbound_spacing_seconds())
    except Exception:
        with _get_session() as session:
            failed_job = session.get(EyunBulkSendJobModel, job_id)
            if failed_job is not None:
                failed_job.status = "failed"
                failed_job.updated_at = utcnow()
                session.commit()
        raise
    return {
        "id": job_id,
        "status": "queued",
        "recipient_count": len(recipients),
        "message_count": len(outbound_ids),
        "outbound_message_ids": outbound_ids,
    }


async def process_due_eyun_outbound_messages(limit: int = 5) -> int:
    now = utcnow()
    attempted = 0
    with _get_session() as session:
        rows = session.scalars(
            select(EyunOutboundMessageModel)
            .where(
                EyunOutboundMessageModel.status == "queued",
                EyunOutboundMessageModel.due_at <= now,
            )
            .order_by(EyunOutboundMessageModel.due_at.asc(), EyunOutboundMessageModel.id.asc())
            .limit(limit)
        ).all()
        for row in rows:
            if row.depends_on_outbound_id:
                dependency = session.get(
                    EyunOutboundMessageModel, row.depends_on_outbound_id
                )
                if dependency is None or dependency.status in {
                    "failed",
                    "cancelled",
                }:
                    row.status = "cancelled"
                    row.last_error = "前一条组合消息发送失败"
                    row.updated_at = now
                    session.commit()
                    _sync_sop_outbound(
                        row.source_batch_key, row.status, row.last_error
                    )
                    if row.conversation_message_id:
                        update_outbound_message_delivery(
                            row.conversation_message_id, status="cancelled"
                        )
                    continue
                if dependency.status != "sent":
                    row.due_at = now + timedelta(seconds=5)
                    row.updated_at = now
                    session.commit()
                    continue
            if not _validate_sop_outbound(row.source_batch_key):
                row.status = "cancelled"
                row.last_error = "SOP发送前条件已不满足"
                row.updated_at = now
                session.commit()
                if row.conversation_message_id:
                    update_outbound_message_delivery(
                        row.conversation_message_id, status="cancelled"
                    )
                continue
            if row.material_id is None:
                legacy_type, legacy_content = _decode_outbound_content(row.content)
                if legacy_type in {"image", "video"}:
                    from app.services.eyun_material_service import (
                        materialize_eyun_outbound_media,
                    )

                    try:
                        material = await materialize_eyun_outbound_media(
                            w_id=row.w_id,
                            message_type=legacy_type,
                            content=legacy_content,
                        )
                    except Exception as exc:  # noqa: BLE001
                        row.attempts = (row.attempts or 0) + 1
                        row.status = "failed" if row.attempts >= 2 else "queued"
                        row.last_error = str(exc)
                        if row.status == "queued":
                            row.due_at = utcnow() + timedelta(seconds=30)
                        row.updated_at = utcnow()
                        session.commit()
                        _sync_sop_outbound(
                            row.source_batch_key, row.status, row.last_error
                        )
                        if row.conversation_message_id:
                            update_outbound_message_delivery(
                                row.conversation_message_id, status=row.status
                            )
                        continue
                    row.material_id = int(material["id"])
                    row.content = _encode_outbound_content(
                        f"received_{material['media_type']}", ""
                    )
                    row.updated_at = utcnow()
                    session.commit()
            rate = session.get(EyunSendRateModel, row.w_id)
            allowed_at = _next_allowed_send_at(rate.last_sent_at if rate else None)
            if allowed_at > now:
                row.due_at = allowed_at + timedelta(
                    seconds=_outbound_rate_jitter_seconds()
                )
                row.updated_at = now
                session.commit()
                continue

            row.status = "sending"
            row.updated_at = now
            session.commit()
            if row.conversation_message_id:
                update_outbound_message_delivery(
                    row.conversation_message_id, status="sending"
                )
            send_result = None
            material_message: tuple[str, str] | None = None
            if row.material_id is not None:
                from app.services.eyun_material_service import get_ready_material

                try:
                    material = get_ready_material(row.material_id)
                except Exception as exc:  # noqa: BLE001
                    row.status = "waiting_material"
                    row.last_error = str(exc)
                    row.updated_at = utcnow()
                    session.commit()
                    continue
                material_message = (
                    f"received_{material['media_type']}",
                    str(material["raw_xml"]),
                )
            attempted += 1
            try:
                from app.services.eyun_callback_service import (
                    send_eyun_image,
                    send_eyun_link_card,
                    send_eyun_mini_program,
                    send_eyun_received_media,
                    send_eyun_text,
                    send_eyun_video,
                )

                if material_message is not None:
                    message_type, content = material_message
                else:
                    message_type, content = _decode_outbound_content(row.content)
                if message_type == "image":
                    send_result = await send_eyun_image(
                        w_id=row.w_id, wc_id=row.wc_id, content=content
                    )
                elif message_type == "mini_program":
                    send_result = await send_eyun_mini_program(
                        w_id=row.w_id,
                        wc_id=row.wc_id,
                        card=json.loads(content),
                    )
                elif message_type == "link_card":
                    send_result = await send_eyun_link_card(
                        w_id=row.w_id,
                        wc_id=row.wc_id,
                        card=json.loads(content),
                    )
                elif message_type in {"received_image", "received_video"}:
                    send_result = await send_eyun_received_media(
                        w_id=row.w_id,
                        wc_id=row.wc_id,
                        content=content,
                        message_type=message_type,
                    )
                elif message_type == "video":
                    video = json.loads(content)
                    send_result = await send_eyun_video(
                        w_id=row.w_id,
                        wc_id=row.wc_id,
                        path=str(video.get("path") or ""),
                        thumb_path=str(video.get("thumb_path") or ""),
                    )
                else:
                    send_result = await send_eyun_text(
                        w_id=row.w_id, wc_id=row.wc_id, content=content
                    )
            except Exception as exc:  # noqa: BLE001
                if row.material_id is not None and _is_material_expiry_error(exc):
                    from app.services.eyun_material_service import mark_material_expired

                    mark_material_expired(row.material_id, str(exc))
                    row.status = "waiting_material"
                    row.last_error = str(exc)
                    row.updated_at = utcnow()
                    session.commit()
                    continue
                row.attempts = (row.attempts or 0) + 1
                row.status = "failed" if row.attempts >= 2 else "queued"
                row.last_error = str(exc)
                if row.status == "queued":
                    row.due_at = utcnow() + timedelta(seconds=30)
                row.updated_at = utcnow()
                session.commit()
                if row.conversation_message_id:
                    update_outbound_message_delivery(
                        row.conversation_message_id,
                        status=row.status,
                    )
                logger.warning("Eyun outbound send failed: %s", exc)
                _sync_sop_outbound(row.source_batch_key, row.status, row.last_error)
                continue

            sent_at = _eyun_sent_at(send_result) or utcnow()
            row.status = "sent"
            row.updated_at = sent_at
            if not row.conversation_message_id:
                _mark_conversation_ai_message_sent(
                    session,
                    source_batch_key=row.source_batch_key,
                    content=content,
                    sent_at=sent_at,
                )
            if rate is None:
                rate = EyunSendRateModel(
                    w_id=row.w_id, last_sent_at=sent_at, updated_at=sent_at
                )
                session.add(rate)
            else:
                rate.last_sent_at = sent_at
                rate.updated_at = sent_at
            session.commit()
            _sync_sop_outbound(row.source_batch_key, "sent")
            if row.conversation_message_id:
                update_outbound_message_delivery(
                    row.conversation_message_id,
                    status="sent",
                    provider_message_id=_eyun_provider_message_id_from_result(send_result),
                    sent_at=sent_at,
                )
            break
    return attempted


async def eyun_worker_tick() -> None:
    await process_due_eyun_inbound_batches(limit=10)
    await process_due_eyun_outbound_messages(limit=5)


async def eyun_risk_control_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            await eyun_worker_tick()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Eyun risk-control worker tick failed: %s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.eyun_worker_poll_seconds)
        except asyncio.TimeoutError:
            pass


async def _process_inbound_batch(batch_id: int) -> None:
    with _get_session() as session:
        batch = session.get(EyunInboundBatchModel, batch_id)
        if batch is None:
            return
        batch_data = _inbound_batch_to_dict(batch)
        inbound_payloads = _batch_inbound_payloads(
            session,
            batch_data["batch_key"],
            created_after=batch_data["created_at"],
        )
        customer_snapshot = _latest_customer_snapshot(session, batch_data["batch_key"])
        is_first_inbound = is_first_eyun_inbound_message(session, batch_data["batch_key"])
        has_sent_orchid_material = _has_sent_orchid_material(
            session,
            user_id=batch_data["from_user"] or batch_data["target_wc_id"],
            session_id=batch_data["from_group"],
            before=batch_data["created_at"],
        )

    try:
        if batch_data["from_group"]:
            _mark_batch(batch_id, "skipped")
            return

        (
            prepared_content,
            image_count,
            recognized_image_count,
            verified_order_count,
            wrong_store_order_count,
        ) = await _prepare_inbound_content(batch_data["content"], inbound_payloads)
        batch_data["content"] = prepared_content
        all_images_failed = image_count > 0 and recognized_image_count == 0
        user_id = batch_data["from_user"] or batch_data["target_wc_id"]

        if verified_order_count:
            profile = await add_verified_customer_tag(
                user_id,
                "抖音已购",
                reason="vision_verified_store_order",
                trace_id=batch_data["batch_key"],
            )
            customer_snapshot["customer_tags"] = profile.get("customer_tags", [])

        if _conversation_blocks_ai(batch_data):
            await record_bypassed_intent_observation(
                trace_id=batch_data["batch_key"],
                channel="wechat",
                user_id=user_id,
                session_id=batch_data["from_group"],
                user_message=batch_data["content"],
                final_route="human",
                reason="human_conversation_locked",
            )
            _mark_batch(batch_id, "skipped")
            return

        contact_snapshot = await get_eyun_contact_snapshot(
            w_id=batch_data["w_id"],
            wc_id=user_id,
        )
        customer_snapshot.update(contact_snapshot)

        video_issue_result = orchid_material_video_issue_chat_result(
            batch_data["content"]
        )
        material_result = orchid_material_chat_result(batch_data["content"])
        if has_sent_orchid_material and video_issue_result is not None:
            chat_result = video_issue_result
            await record_bypassed_intent_observation(
                trace_id=batch_data["batch_key"],
                channel="wechat",
                user_id=user_id,
                session_id=batch_data["from_group"],
                user_message=batch_data["content"],
                final_route="orchid_material_video_issue",
                primary_domain="customer_service",
                primary_goal="request_service",
                reason="fixed_material_video_issue",
            )
        elif material_result is not None:
            chat_result = material_result
            await record_bypassed_intent_observation(
                trace_id=batch_data["batch_key"],
                channel="wechat",
                user_id=user_id,
                session_id=batch_data["from_group"],
                user_message=batch_data["content"],
                final_route="orchid_material_delivery",
                primary_domain="care_service",
                primary_goal="request_material",
                issues=["care_general"],
                scope="in_scope",
                reason="fixed_material_delivery",
            )
        elif all_images_failed:
            if wrong_store_order_count == image_count:
                chat_result = {
                    "answer": WRONG_STORE_ORDER_REPLY,
                    "route": "unsupported_store_order",
                }
                await record_bypassed_intent_observation(
                    trace_id=batch_data["batch_key"],
                    channel="wechat",
                    user_id=user_id,
                    session_id=batch_data["from_group"],
                    user_message=batch_data["content"],
                    final_route="unsupported_store_order",
                    primary_domain="out_of_scope",
                    primary_goal="unclear",
                    scope="out_of_scope",
                    reason="unsupported_store_order",
                )
            else:
                await record_bypassed_intent_observation(
                    trace_id=batch_data["batch_key"],
                    channel="wechat",
                    user_id=user_id,
                    session_id=batch_data["from_group"],
                    user_message=batch_data["content"],
                    final_route="human",
                    reason="image_recognition_failed",
                )
                await _handoff_image_failure(
                    batch_id=batch_id,
                    batch=batch_data,
                    customer_snapshot=customer_snapshot,
                )
                _mark_batch(batch_id, "processed")
                return
        elif is_first_inbound and image_count == 0:
            chat_result = _opening_chat_result()
            await record_bypassed_intent_observation(
                trace_id=batch_data["batch_key"],
                channel="wechat",
                user_id=user_id,
                session_id=batch_data["from_group"],
                user_message=batch_data["content"],
                final_route=str(chat_result.get("route") or "opening"),
                reason="first_inbound_opening",
            )
            await _record_opening_memories(batch_data, chat_result.get("answer", ""))
        else:
            chat_result = await handle_chat(
                ChatRequest(
                    channel="wechat",
                    user_id=batch_data["from_user"] or batch_data["target_wc_id"],
                    session_id=batch_data["from_group"],
                    message=batch_data["content"],
                    kb_id=get_settings().wechat_default_kb_id,
                    metadata={
                        "provider": "eyun",
                        "account": batch_data["account"],
                        "message_type": batch_data["message_type"],
                        "wc_id": batch_data["wc_id"],
                        "w_id": batch_data["w_id"],
                        "from_user": batch_data["from_user"],
                        "from_group": batch_data["from_group"],
                        "batch_key": batch_data["batch_key"],
                        "message_count": batch_data["message_count"],
                        "image_count": image_count,
                        "recognized_image_count": recognized_image_count,
                        "verified_order_count": verified_order_count,
                        **customer_snapshot,
                    },
                )
            )
        if batch_data["w_id"] and batch_data["target_wc_id"]:
            outbound_messages = _outbound_messages(chat_result)
            due_at = utcnow() + timedelta(seconds=random_reply_delay_seconds())
            for index, message in enumerate(outbound_messages):
                conversation_message = await ensure_outbound_conversation_message(
                    channel="wechat",
                    user_id=batch_data["from_user"] or batch_data["target_wc_id"],
                    session_id=batch_data["from_group"],
                    content=message["content"],
                    message_type=message["type"],
                    sender_type="ai",
                    sender_id="ai",
                    route=str(
                        chat_result.get("route")
                        or ("opening" if is_first_inbound and image_count == 0 else "")
                    ),
                    created_after=batch_data["created_at"],
                )
                kwargs = {
                    "w_id": batch_data["w_id"],
                    "wc_id": batch_data["target_wc_id"],
                    "content": message["content"],
                    "source_batch_key": batch_data["batch_key"],
                    "conversation_message_id": conversation_message["id"],
                    "due_at": due_at,
                }
                if message.get("material_id"):
                    kwargs["material_id"] = int(message["material_id"])
                if message["type"] not in {"text", "material"}:
                    kwargs["message_type"] = message["type"]
                await enqueue_eyun_outbound(**kwargs)
                if index < len(outbound_messages) - 1:
                    due_at += timedelta(seconds=random_outbound_spacing_seconds())
        _mark_batch(batch_id, "processed")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Eyun inbound batch processing failed: %s", exc)
        _mark_batch(batch_id, "failed")


def _batch_inbound_payloads(
    session: Session, batch_key: str, *, created_after: datetime
) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(EyunInboundMessageModel)
        .where(
            EyunInboundMessageModel.batch_key == batch_key,
            EyunInboundMessageModel.created_at >= created_after,
        )
        .order_by(EyunInboundMessageModel.created_at.asc(), EyunInboundMessageModel.id.asc())
    ).all()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _has_sent_orchid_material(
    session: Session,
    *,
    user_id: str,
    session_id: str | None,
    before: datetime,
) -> bool:
    conversation_id = make_conversation_id("wechat", user_id, session_id)
    return session.scalar(
        select(ConversationMessageModel.id)
        .where(
            ConversationMessageModel.conversation_id == conversation_id,
            ConversationMessageModel.sender_type.in_(("ai", "human")),
            ConversationMessageModel.route == "orchid_material_delivery",
            ConversationMessageModel.delivery_status == "sent",
            ConversationMessageModel.created_at < before,
        )
        .limit(1)
    ) is not None


async def _prepare_inbound_content(
    stored_content: str, payloads: list[dict[str, Any]]
) -> tuple[str, int, int, int, int]:
    from app.services.eyun_callback_service import fetch_eyun_image_url
    from app.services.vision_service import (
        UnsupportedStoreOrderError,
        VisionError,
        analyze_image,
        format_analysis_for_chat,
        purchase_tag_for_analysis,
    )

    parts: list[str] = []
    image_count = 0
    recognized_image_count = 0
    verified_order_count = 0
    wrong_store_order_count = 0
    for payload in payloads:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        message_type = str(payload.get("messageType") or "")
        if message_type == "60001":
            text_content = str(data.get("content") or "").strip()
            if text_content:
                parts.append(text_content)
            continue
        if message_type != "60002":
            continue

        image_count += 1
        image_source = str(
            data.get("_image_url")
            or data.get("imageUrl")
            or data.get("imgUrl")
            or data.get("url")
            or ""
        ).strip()
        if not image_source:
            image_request = {
                "w_id": str(data.get("wId") or payload.get("wId") or ""),
                "msg_id": str(data.get("msgId") or data.get("newMsgId") or ""),
                "content": str(data.get("content") or ""),
            }
            image_source = str(
                await fetch_eyun_image_url(**image_request, image_type=1)
                or await fetch_eyun_image_url(**image_request, image_type=0)
                or ""
            )
        if not image_source:
            thumbnail = str(data.get("img") or "").strip()
            if thumbnail:
                image_source = (
                    thumbnail
                    if thumbnail.startswith("data:")
                    else f"data:image/jpeg;base64,{thumbnail}"
                )

        try:
            analysis = await analyze_image(image_source)
        except UnsupportedStoreOrderError as exc:
            wrong_store_order_count += 1
            logger.info(
                "Eyun order screenshot uses an unsupported store: %s",
                exc.store_name,
            )
            continue
        except VisionError as exc:
            logger.warning("Eyun image recognition failed: %s", exc)
            continue
        recognized_image_count += 1
        if purchase_tag_for_analysis(analysis):
            verified_order_count += 1
        parts.append(format_analysis_for_chat(analysis, index=image_count))

    if not parts and image_count == 0:
        parts.append(stored_content)
    separator = "\n\n" if image_count else "\n"
    return (
        separator.join(part for part in parts if part),
        image_count,
        recognized_image_count,
        verified_order_count,
        wrong_store_order_count,
    )


async def _record_opening_memories(batch: dict[str, Any], answer: str) -> None:
    user_id = batch["from_user"] or batch["target_wc_id"]
    session_id = batch["from_group"] or "default"
    if batch.get("content"):
        await append_conversation_memory(
            user_id=user_id,
            tenant_id="tenant_default",
            session_id=session_id,
            role="user",
            content=batch["content"],
            intent="opening_trigger",
            route="chitchat",
            channel="wechat",
            owner_external_id=str(batch.get("w_id") or ""),
            source_id=f"{batch['batch_key']}:customer",
        )
    if answer:
        await append_conversation_memory(
            user_id=user_id,
            tenant_id="tenant_default",
            session_id=session_id,
            role="assistant",
            content=answer,
            intent="opening",
            route="chitchat",
            channel="wechat",
            owner_external_id=str(batch.get("w_id") or ""),
            source_id=f"{batch['batch_key']}:assistant",
        )


async def _handoff_image_failure(
    *,
    batch_id: int,
    batch: dict[str, Any],
    customer_snapshot: dict[str, Any],
) -> None:
    user_id = batch["from_user"] or batch["target_wc_id"]
    conversation_id = make_conversation_id(
        "wechat",
        user_id,
        batch["from_group"],
    )
    with _get_session() as session:
        conversation_exists = session.scalar(
            select(ConversationModel.id).where(
                ConversationModel.conversation_id == conversation_id
            )
        ) is not None

    if conversation_exists:
        await force_handoff(
            conversation_id,
            operator_id="system",
            reason="image_recognition_failure",
        )
        return

    await record_customer_message(
        channel="wechat",
        user_id=user_id,
        session_id=batch["from_group"],
        content=batch["content"] or "[图片]",
        message_id=f"image-handoff:{batch['batch_key']}:{batch_id}",
        route="image_recognition_handoff",
        metadata={
            "provider": "eyun",
            "w_id": batch["w_id"],
            "wc_id": batch["wc_id"],
            "batch_key": batch["batch_key"],
            **customer_snapshot,
        },
        handoff_reason="image_recognition_failure",
    )


def _conversation_blocks_ai(batch: dict[str, Any]) -> bool:
    conversation_id = make_conversation_id(
        "wechat",
        batch["from_user"] or batch["target_wc_id"],
        batch["from_group"],
    )
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(
                ConversationModel.conversation_id == conversation_id
            )
        )
        return bool(
            conversation
            and conversation.status in {HANDOFF_PENDING, HUMAN_ACTIVE, RESOLVED}
        )


def _answer_segments(chat_result: dict[str, Any]) -> list[str]:
    segments = chat_result.get("answer_segments")
    if isinstance(segments, list):
        return [str(segment).strip() for segment in segments if str(segment).strip()]
    answer = str(chat_result.get("answer") or "").strip()
    return [answer] if answer else []


def _outbound_messages(chat_result: dict[str, Any]) -> list[dict[str, Any]]:
    messages = chat_result.get("outbound_messages")
    if isinstance(messages, list):
        valid = [
            {
                "type": str(message["type"]),
                "content": str(message["content"]).strip(),
                **(
                    {"material_id": int(message["material_id"])}
                    if message.get("material_id")
                    else {}
                ),
                **(
                    {"split": False}
                    if message.get("split") is False
                    else {}
                ),
            }
            for message in messages
            if isinstance(message, dict)
            and message.get("type")
            in {"text", "image", "link_card", "mini_program", "material"}
            and str(message.get("content") or "").strip()
        ]
        if valid:
            formatted: list[dict[str, str]] = []
            for message in valid:
                if message["type"] != "text" or message.get("split") is False:
                    message.pop("split", None)
                    formatted.append(message)
                    continue
                formatted.extend(
                    {"type": "text", "content": content}
                    for content in split_customer_messages(message["content"])
                )
            return _convert_text_links_to_cards(formatted)
    fallback = [
        {"type": "text", "content": answer}
        for answer in _answer_segments(chat_result)
    ]
    return _convert_text_links_to_cards(fallback)


def _convert_text_links_to_cards(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Never send a bare URL to customers; render every URL as a link card."""
    result: list[dict[str, Any]] = []
    existing_urls: set[str] = set()
    for message in messages:
        if message.get("type") != "link_card":
            continue
        try:
            card = json.loads(str(message.get("content") or ""))
        except json.JSONDecodeError:
            continue
        if isinstance(card, dict) and card.get("url"):
            existing_urls.add(str(card["url"]).strip())

    for message in messages:
        if message.get("type") != "text":
            result.append(message)
            continue
        content = str(message.get("content") or "")
        urls: list[str] = []
        for match in _URL_PATTERN.finditer(content):
            url = match.group(0).rstrip(_URL_TRAILING_PUNCTUATION)
            if url and url not in urls:
                urls.append(url)
        if not urls:
            result.append(message)
            continue

        text_content = content
        for url in urls:
            text_content = text_content.replace(url, "")
        text_content = re.sub(
            r"(?:(?:购买)?链接|详情)\s*[：:]\s*(?=[，。；,.;]|$)",
            "",
            text_content,
        ).strip()
        text_content = text_content.strip("，。；：,.;: ")
        if text_content:
            result.append({**message, "content": text_content})
        for url in urls:
            if url in existing_urls:
                continue
            result.append(
                {
                    "type": "link_card",
                    "content": json.dumps(
                        {
                            "title": "查看详情",
                            "url": url,
                            "description": "点击卡片查看详情",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            existing_urls.add(url)
    return result


def _source_type_from_batch_key(source_batch_key: str | None) -> str:
    prefix = str(source_batch_key or "").partition(":")[0].strip()
    return {
        "activity": "activity",
        "bulk": "bulk",
        "unpurchased_sop": "unpurchased_sop",
        "unpurchased_sop_test": "unpurchased_sop_test",
        "service_sop": "service_sop",
        "service_sop_test": "service_sop_test",
        "workbench": "admin_workbench",
        "image_description_prompt": "image_description_prompt",
    }.get(prefix, prefix or "system")


def is_first_eyun_inbound_message(session: Session, batch_key: str) -> bool:
    messages = session.scalars(
        select(EyunInboundMessageModel.id)
        .where(EyunInboundMessageModel.batch_key == batch_key)
        .limit(2)
    ).all()
    return len(messages) == 1


def _opening_chat_result() -> dict[str, Any]:
    from app.services.reply_builder import build_opening_reply

    reply = build_opening_reply()
    return {
        "answer": reply.answer,
        "outbound_messages": [message.model_dump() for message in reply.outbound_messages],
    }


def _encode_outbound_content(message_type: str, content: str) -> str:
    if message_type in {
        "image",
        "video",
        "link_card",
        "mini_program",
        "received_image",
        "received_video",
    }:
        return json.dumps({"type": message_type, "content": content}, ensure_ascii=False)
    return content


def _decode_outbound_content(content: str) -> tuple[str, str]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return "text", content
    if (
        isinstance(payload, dict)
        and payload.get("type")
        in {
            "image",
            "video",
            "link_card",
            "mini_program",
            "received_image",
            "received_video",
        }
        and payload.get("content")
    ):
        return str(payload["type"]), str(payload["content"])
    return "text", content


def _is_material_expiry_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("cdn", "xml", "material", "expired", "素材", "过期", "失效")
    )


def _mark_batch(batch_id: int, status: str) -> None:
    with _get_session() as session:
        batch = session.get(EyunInboundBatchModel, batch_id)
        if batch is None:
            return
        batch.status = status
        batch.updated_at = utcnow()
        session.commit()


def _next_allowed_send_at(last_sent_at: datetime | None) -> datetime:
    if last_sent_at is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _ensure_aware(last_sent_at) + timedelta(
        seconds=_minimum_outbound_interval_seconds()
    )


def _minimum_outbound_interval_seconds() -> float:
    settings = get_settings()
    # A small safety margin keeps a rolling 60-second window below the configured cap.
    rate_interval = 60 / max(1, settings.eyun_send_max_per_minute)
    return max(settings.eyun_send_min_interval_seconds, rate_interval + 0.05)


def _outbound_rate_jitter_seconds() -> float:
    settings = get_settings()
    minimum = _minimum_outbound_interval_seconds()
    maximum = max(minimum, settings.eyun_send_max_interval_seconds)
    return random.uniform(0, maximum - minimum)


def _eyun_sent_at(result: Any) -> datetime | None:
    if not isinstance(result, dict):
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    try:
        timestamp = int(data.get("createTime"))
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)


def _eyun_provider_message_id_from_result(result: Any) -> str | None:
    if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
        return None
    data = result["data"]
    return str(data.get("newMsgId") or data.get("msgId") or "") or None


def _mark_conversation_ai_message_sent(
    session: Session,
    *,
    source_batch_key: str | None,
    content: str,
    sent_at: datetime,
) -> None:
    if not source_batch_key:
        return
    batch = _get_batch(session, source_batch_key)
    if batch is None:
        return
    conversation_id = make_conversation_id(
        "wechat",
        batch.from_user or batch.target_wc_id,
        batch.from_group,
    )
    message = session.scalar(
        select(ConversationMessageModel)
        .where(
            ConversationMessageModel.conversation_id == conversation_id,
            ConversationMessageModel.sender_type == "ai",
            ConversationMessageModel.content == content,
            ConversationMessageModel.created_at >= batch.created_at,
        )
        .order_by(
            ConversationMessageModel.created_at.asc(),
            ConversationMessageModel.id.asc(),
        )
        .limit(1)
    )
    if message is not None:
        message.created_at = sent_at


def _provider_message_id(
    payload: dict[str, Any], batch_key: str, now: datetime
) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    raw_id = data.get("newMsgId") or data.get("msgId")
    if raw_id:
        return f"{batch_key}:{raw_id}"
    content = str(data.get("content") or "")
    return f"{batch_key}:{now.timestamp()}:{hash(content)}"


def _latest_customer_snapshot(session: Session, batch_key: str) -> dict[str, str]:
    row = session.scalar(
        select(EyunInboundMessageModel)
        .where(EyunInboundMessageModel.batch_key == batch_key)
        .order_by(EyunInboundMessageModel.created_at.desc(), EyunInboundMessageModel.id.desc())
        .limit(1)
    )
    if row is None:
        return {}
    try:
        payload = json.loads(row.payload_json or "{}")
    except json.JSONDecodeError:
        return {}

    display_name = _payload_text(
        payload,
        (
            "remark_name",
            "remark",
            "display_name",
            "nickname",
            "nick_name",
            "from_user_name",
            "fromUserName",
            "sender_name",
            "alias",
        ),
    )
    avatar_url = _payload_text(
        payload,
        ("avatar_url", "avatar", "headimgurl", "head_img_url", "head_url"),
    )
    snapshot = {}
    if display_name:
        snapshot["remark_name"] = display_name
    if avatar_url:
        snapshot["avatar_url"] = avatar_url
    return snapshot


def _payload_text(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    sources = [payload]
    data = payload.get("data")
    if isinstance(data, dict):
        sources.append(data)
        for nested_key in ("user", "customer", "contact", "profile"):
            nested = data.get(nested_key)
            if isinstance(nested, dict):
                sources.append(nested)
    for nested_key in ("user", "customer", "contact", "profile"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
    for source in sources:
        for key in keys:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _get_session() -> Session:
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                ConversationModel.__table__,
                ConversationMessageModel.__table__,
                EyunInboundBatchModel.__table__,
                EyunInboundMessageModel.__table__,
                EyunImagePromptRateModel.__table__,
                EyunMediaMaterialModel.__table__,
                EyunBulkSendJobModel.__table__,
                EyunOutboundMessageModel.__table__,
                EyunSendRateModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    if settings.chat_log_db_url not in _initialized_urls:
        _ensure_risk_control_columns(factory)
        _initialized_urls.add(settings.chat_log_db_url)
    return factory()


def _ensure_risk_control_columns(factory: sessionmaker) -> None:
    with factory() as session:
        bind = session.get_bind()
        outbound_columns = {
            column["name"]
            for column in inspect(bind).get_columns("eyun_outbound_messages")
        }
        if "conversation_message_id" not in outbound_columns:
            session.execute(
                text(
                    "ALTER TABLE eyun_outbound_messages "
                    "ADD COLUMN conversation_message_id INTEGER"
                )
            )
        if "depends_on_outbound_id" not in outbound_columns:
            session.execute(
                text(
                    "ALTER TABLE eyun_outbound_messages "
                    "ADD COLUMN depends_on_outbound_id INTEGER"
                )
            )
        if "material_id" not in outbound_columns:
            session.execute(
                text("ALTER TABLE eyun_outbound_messages ADD COLUMN material_id INTEGER")
            )
        if "bulk_job_id" not in outbound_columns:
            session.execute(
                text("ALTER TABLE eyun_outbound_messages ADD COLUMN bulk_job_id INTEGER")
            )
        message_columns = {
            column["name"]
            for column in inspect(bind).get_columns("conversation_messages")
        }
        if "delivery_status" not in message_columns:
            session.execute(
                text(
                    "ALTER TABLE conversation_messages "
                    "ADD COLUMN delivery_status VARCHAR(32)"
                )
            )
        session.commit()


def _get_batch(session: Session, batch_key: str) -> EyunInboundBatchModel | None:
    return session.scalar(
        select(EyunInboundBatchModel).where(
            EyunInboundBatchModel.batch_key == batch_key
        )
    )


def _inbound_batch_to_dict(row: EyunInboundBatchModel | None) -> dict[str, Any]:
    if row is None:
        return {}
    return {
        "id": row.id,
        "batch_key": row.batch_key,
        "w_id": row.w_id,
        "wc_id": row.wc_id,
        "target_wc_id": row.target_wc_id,
        "from_user": row.from_user,
        "from_group": row.from_group,
        "account": row.account,
        "message_type": row.message_type,
        "content": row.content,
        "message_count": row.message_count,
        "status": row.status,
        "due_at": _ensure_aware(row.due_at),
        "created_at": _ensure_aware(row.created_at),
        "updated_at": _ensure_aware(row.updated_at),
    }


def _outbound_to_dict(row: EyunOutboundMessageModel) -> dict[str, Any]:
    return {
        "id": row.id,
        "w_id": row.w_id,
        "wc_id": row.wc_id,
        "content": row.content,
        "source_batch_key": row.source_batch_key,
        "conversation_message_id": row.conversation_message_id,
        "depends_on_outbound_id": row.depends_on_outbound_id,
        "material_id": row.material_id,
        "bulk_job_id": row.bulk_job_id,
        "status": row.status,
        "due_at": _ensure_aware(row.due_at),
        "attempts": row.attempts,
        "last_error": row.last_error,
    }


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _validate_sop_outbound(source_batch_key: str | None) -> bool:
    if not source_batch_key or not source_batch_key.startswith(
        ("unpurchased_sop:", "service_sop:")
    ):
        return True
    from app.services.unpurchased_sop_service import validate_sop_delivery_before_send

    return validate_sop_delivery_before_send(source_batch_key)


def _sync_sop_outbound(
    source_batch_key: str | None, status: str, error: str | None = None
) -> None:
    if not source_batch_key or not source_batch_key.startswith(
        ("unpurchased_sop:", "service_sop:")
    ):
        return
    from app.services.unpurchased_sop_service import sync_sop_delivery_from_outbound

    sync_sop_delivery_from_outbound(source_batch_key, status, error)
