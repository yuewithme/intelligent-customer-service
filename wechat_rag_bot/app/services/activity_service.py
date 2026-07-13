import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    ActivityModel,
    ActivitySendLogModel,
    Base,
    ConversationMessageModel,
    EyunOutboundMessageModel,
)
from app.schemas.activity import (
    ActivityFromMessagesRequest,
    ActivitySwitchesRequest,
    ActivityUpdateRequest,
)
from app.schemas.common import AppError, ErrorCode


_sessionmakers: dict[str, sessionmaker] = {}


def create_activity_from_messages(request: ActivityFromMessagesRequest) -> dict:
    message_ids = list(dict.fromkeys(request.message_ids))
    with _get_session() as session:
        messages = session.scalars(
            select(ConversationMessageModel)
            .where(
                ConversationMessageModel.conversation_id == request.conversation_id,
                ConversationMessageModel.id.in_(message_ids),
            )
            .order_by(
                ConversationMessageModel.created_at.asc(),
                ConversationMessageModel.id.asc(),
            )
        ).all()
        if len(messages) != len(message_ids):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="所选消息不存在或不属于当前会话",
                status_code=422,
            )
        items = [_snapshot_message(row, position) for position, row in enumerate(messages, 1)]
        now = _now()
        row = ActivityModel(
            title=request.title.strip(),
            summary=_optional_text(request.summary),
            status="draft",
            enabled=True,
            ai_enabled=False,
            ai_rules_json="{}",
            items_json=json.dumps(items, ensure_ascii=False),
            valid_from=request.valid_from,
            valid_until=request.valid_until,
            created_by=request.operator_id,
            updated_by=request.operator_id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _activity_to_dict(row)


def list_activities(
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    keyword: str | None = None,
    available_only: bool = False,
) -> dict:
    filters = []
    if status:
        filters.append(ActivityModel.status == status)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        filters.append(
            or_(ActivityModel.title.like(pattern), ActivityModel.summary.like(pattern))
        )
    now = _now()
    if available_only:
        filters.extend(
            [
                ActivityModel.status == "published",
                ActivityModel.enabled.is_(True),
                or_(ActivityModel.valid_from.is_(None), ActivityModel.valid_from <= now),
                or_(ActivityModel.valid_until.is_(None), ActivityModel.valid_until > now),
            ]
        )
    with _get_session() as session:
        total = session.scalar(
            select(func.count()).select_from(ActivityModel).where(*filters)
        )
        rows = session.scalars(
            select(ActivityModel)
            .where(*filters)
            .order_by(ActivityModel.updated_at.desc(), ActivityModel.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [_activity_to_dict(row) for row in rows],
            "total": int(total or 0),
            "page": page,
            "page_size": page_size,
        }


def get_activity(activity_id: int) -> dict:
    with _get_session() as session:
        return _activity_to_dict(_get_activity_or_error(session, activity_id))


def update_activity(activity_id: int, request: ActivityUpdateRequest) -> dict:
    with _get_session() as session:
        row = _get_activity_or_error(session, activity_id)
        if row.status != "draft" and row.enabled:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="请先关闭活动再编辑",
                status_code=409,
            )
        if row.status == "archived":
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="已归档活动不能编辑",
                status_code=409,
            )
        row.title = request.title.strip()
        row.summary = _optional_text(request.summary)
        row.valid_from = request.valid_from
        row.valid_until = request.valid_until
        row.ai_rules_json = json.dumps(request.ai_rules, ensure_ascii=False)
        row.updated_by = request.operator_id
        row.updated_at = _now()
        session.commit()
        return _activity_to_dict(row)


def publish_activity(activity_id: int, operator_id: str) -> dict:
    with _get_session() as session:
        row = _get_activity_or_error(session, activity_id)
        if row.status == "archived":
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="已归档活动不能发布",
                status_code=409,
            )
        if not row.title.strip() or not _decode_items(row.items_json):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="活动标题和消息不能为空",
                status_code=409,
            )
        now = _now()
        if row.valid_until is not None and _as_utc(row.valid_until) <= now:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="活动结束时间已过",
                status_code=409,
            )
        row.status = "published"
        row.valid_from = row.valid_from or now
        row.published_at = now
        row.updated_by = operator_id
        row.updated_at = now
        session.commit()
        return _activity_to_dict(row)


def update_activity_switches(
    activity_id: int, request: ActivitySwitchesRequest
) -> dict:
    with _get_session() as session:
        row = _get_activity_or_error(session, activity_id)
        if row.status == "archived":
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="已归档活动不能更新开关",
                status_code=409,
            )
        if request.enabled is not None:
            row.enabled = request.enabled
        if request.ai_enabled is not None:
            row.ai_enabled = request.ai_enabled
        row.updated_by = request.operator_id
        row.updated_at = _now()
        session.commit()
        return _activity_to_dict(row)


def archive_activity(activity_id: int, operator_id: str) -> dict:
    with _get_session() as session:
        row = _get_activity_or_error(session, activity_id)
        row.status = "archived"
        row.enabled = False
        row.ai_enabled = False
        row.updated_by = operator_id
        row.updated_at = _now()
        session.commit()
        return _activity_to_dict(row)


async def send_activity(
    activity_id: int, *, conversation_id: str, operator_id: str
) -> dict:
    from app.services.conversation_service import get_human_activity_send_target
    from app.services.message_risk_control_service import (
        enqueue_eyun_outbound,
        random_reply_delay_seconds,
    )

    target = get_human_activity_send_target(conversation_id, operator_id)
    with _get_session() as session:
        activity = _get_activity_or_error(session, activity_id)
        if not is_activity_available(activity):
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前活动未发布、已关闭或不在有效时间内",
                status_code=409,
            )
        items = sorted(
            _decode_items(activity.items_json), key=lambda item: int(item["position"])
        )
    base_due_at = _now() + timedelta(seconds=random_reply_delay_seconds())
    interval = max(0, get_settings().eyun_send_min_interval_seconds)
    outbound_ids = []
    for index, item in enumerate(items):
        outbound = await enqueue_eyun_outbound(
            w_id=target["w_id"],
            wc_id=target["wc_id"],
            content=str(item["content"]),
            source_batch_key=f"activity:{activity_id}",
            message_type=str(item["type"]),
            due_at=base_due_at + timedelta(seconds=index * interval),
        )
        outbound_ids.append(int(outbound["id"]))
    with _get_session() as session:
        log = ActivitySendLogModel(
            activity_id=activity_id,
            conversation_id=conversation_id,
            user_id=target["user_id"],
            trigger_mode="manual",
            operator_id=operator_id,
            outbound_message_ids_json=json.dumps(outbound_ids),
            created_at=_now(),
        )
        session.add(log)
        session.commit()
        session.refresh(log)
        return {
            "id": log.id,
            "activity_id": activity_id,
            "status": "queued",
            "outbound_message_ids": outbound_ids,
        }


def list_activity_send_logs(activity_id: int) -> dict:
    with _get_session() as session:
        _get_activity_or_error(session, activity_id)
        logs = session.scalars(
            select(ActivitySendLogModel)
            .where(ActivitySendLogModel.activity_id == activity_id)
            .order_by(ActivitySendLogModel.created_at.desc(), ActivitySendLogModel.id.desc())
        ).all()
        items = [_send_log_to_dict(session, row) for row in logs]
        return {"items": items, "total": len(items)}


def is_activity_available(activity: ActivityModel, now: datetime | None = None) -> bool:
    current = _as_utc(now or _now())
    if activity.status != "published" or not activity.enabled:
        return False
    if activity.valid_from is not None and current < _as_utc(activity.valid_from):
        return False
    if activity.valid_until is not None and current >= _as_utc(activity.valid_until):
        return False
    return True


def _snapshot_message(row: ConversationMessageModel, position: int) -> dict:
    if row.sender_type != "customer":
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="只能把客户侧收到的消息保存为活动",
            status_code=422,
        )
    try:
        metadata = json.loads(row.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
    media_type = str(media.get("type") or "")
    if media_type in {"image", "video"}:
        raw_content = str(metadata.get("raw_content") or "").strip()
        if not raw_content:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message=f"消息 {row.id} 缺少可转发的原始媒体内容",
                status_code=422,
            )
        preview_url = str(media.get("url") or "").strip() or None
        if preview_url is None and media_type == "image" and media.get("thumb_base64"):
            preview_url = f"data:image/jpeg;base64,{media['thumb_base64']}"
        return {
            "position": position,
            "type": f"received_{media_type}",
            "content": raw_content,
            "preview_url": preview_url,
            "source_message_id": row.id,
        }
    if media_type:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message=f"消息 {row.id} 的类型暂不支持保存为活动",
            status_code=422,
        )
    content = row.content.strip()
    if not content:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message=f"消息 {row.id} 内容为空",
            status_code=422,
        )
    return {
        "position": position,
        "type": "text",
        "content": content,
        "source_message_id": row.id,
    }


def _activity_to_dict(row: ActivityModel) -> dict:
    items = _decode_items(row.items_json)
    safe_items = []
    for item in items:
        safe_item = dict(item)
        if safe_item.get("type") != "text":
            safe_item.pop("content", None)
        safe_items.append(safe_item)
    try:
        ai_rules = json.loads(row.ai_rules_json or "{}")
    except json.JSONDecodeError:
        ai_rules = {}
    return {
        "id": row.id,
        "title": row.title,
        "summary": row.summary,
        "status": row.status,
        "effective_status": _effective_status(row),
        "enabled": row.enabled,
        "ai_enabled": row.ai_enabled,
        "ai_rules": ai_rules,
        "items": safe_items,
        "item_count": len(items),
        "valid_from": _isoformat(row.valid_from),
        "valid_until": _isoformat(row.valid_until),
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "created_at": _isoformat(row.created_at),
        "updated_at": _isoformat(row.updated_at),
        "published_at": _isoformat(row.published_at),
    }


def _send_log_to_dict(session: Session, row: ActivitySendLogModel) -> dict:
    try:
        outbound_ids = [int(value) for value in json.loads(row.outbound_message_ids_json)]
    except (TypeError, ValueError, json.JSONDecodeError):
        outbound_ids = []
    outbound_rows = []
    if outbound_ids:
        rows_by_id = {
            outbound.id: outbound
            for outbound in session.scalars(
                select(EyunOutboundMessageModel).where(
                    EyunOutboundMessageModel.id.in_(outbound_ids)
                )
            ).all()
        }
        outbound_rows = [rows_by_id[value] for value in outbound_ids if value in rows_by_id]
    status = _aggregate_outbound_status(outbound_rows, len(outbound_ids))
    errors = [row.last_error for row in outbound_rows if row.last_error]
    return {
        "id": row.id,
        "activity_id": row.activity_id,
        "conversation_id": row.conversation_id,
        "user_id": row.user_id,
        "trigger_mode": row.trigger_mode,
        "operator_id": row.operator_id,
        "outbound_message_ids": outbound_ids,
        "status": status,
        "last_error": errors[-1] if errors else None,
        "created_at": _isoformat(row.created_at),
    }


def _aggregate_outbound_status(rows: list[EyunOutboundMessageModel], expected: int) -> str:
    if expected == 0 or len(rows) != expected:
        return "queued"
    statuses = [row.status for row in rows]
    if all(status == "sent" for status in statuses):
        return "sent"
    if "sent" in statuses:
        return "partial"
    if "sending" in statuses:
        return "sending"
    if any(row.last_error for row in rows):
        return "retrying"
    return "queued"


def _effective_status(row: ActivityModel) -> str:
    if row.status != "published":
        return row.status
    if not row.enabled:
        return "disabled"
    now = _now()
    if row.valid_from is not None and now < _as_utc(row.valid_from):
        return "scheduled"
    if row.valid_until is not None and now >= _as_utc(row.valid_until):
        return "expired"
    return "active"


def _decode_items(value: str) -> list[dict]:
    try:
        items = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return items if isinstance(items, list) else []


def _get_activity_or_error(session: Session, activity_id: int) -> ActivityModel:
    row = session.get(ActivityModel, activity_id)
    if row is None:
        raise AppError(
            ErrorCode.REQUEST_INVALID,
            message="活动不存在",
            status_code=404,
        )
    return row


def _get_session() -> Session:
    settings = get_settings()
    if settings.chat_log_provider != "sqlite":
        raise RuntimeError(f"unsupported chat log provider: {settings.chat_log_provider}")
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                ActivityModel.__table__,
                ActivitySendLogModel.__table__,
                EyunOutboundMessageModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def _optional_text(value: str | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None
