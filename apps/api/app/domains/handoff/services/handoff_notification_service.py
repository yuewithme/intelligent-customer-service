import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import Base, EyunContactModel, HandoffNotificationSettingModel
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.handoff.schemas.handoff_notification import (
    HandoffNotificationSettingsUpdateRequest,
)


logger = logging.getLogger("wechat_rag_bot.handoff_notification")
SETTING_ID = 1
DEFAULT_MESSAGE_TEXT = "有客户需要转人工处理，请及时跟进。"
HANDOFF_REASON_TEXTS = {
    "manual_force_handoff": "人工主动转接",
    "human_required": "当前问题需要人工处理",
    "human_request": "客户明确要求人工客服",
    "rule_guard_human_request": "客户明确要求人工客服",
    "clarify": "多轮澄清后仍无法确定客户需求",
    "clarify_to_handoff": "多轮澄清后仍无法确定客户需求",
    "rag_no_answer": "知识库未找到可靠答案",
    "rag_no_answer_to_handoff": "知识库未找到可靠答案",
    "template_not_found_to_handoff": "未找到适用的回复模板",
    "business_facts_unanswerable_to_handoff": "业务信息不足，无法可靠回答",
    "unsupported": "超出自动客服处理范围",
    "unsupported_to_handoff": "超出自动客服处理范围",
    "unsupported_message_type": "非文本消息需人工处理",
    "image_recognition_failure": "图片识别失败，需要人工查看",
    "advanced_customer_level": "高阶客户需要人工接待",
    "tag_high_risk_to_human": "高风险客户问题需要人工处理",
    "invalid_route_to_handoff": "自动处理路线异常",
    "need_human": "当前问题需要人工处理",
    "need_human_non_critical": "建议人工介入处理",
}

_sessionmakers: dict[str, sessionmaker] = {}


def get_handoff_notification_settings() -> dict[str, Any]:
    with _get_session() as session:
        setting = _get_or_create_setting(session)
        session.commit()
        return _setting_to_dict(session, setting)


def update_handoff_notification_settings(
    request: HandoffNotificationSettingsUpdateRequest,
) -> dict[str, Any]:
    with _get_session() as session:
        contacts = session.scalars(
            select(EyunContactModel).where(
                EyunContactModel.id.in_(request.recipient_contact_ids),
                EyunContactModel.status == "active",
            )
        ).all()
        found_ids = {contact.id for contact in contacts}
        missing_ids = [
            contact_id
            for contact_id in request.recipient_contact_ids
            if contact_id not in found_ids
        ]
        if missing_ids:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message=f"联系人不存在或已失效：{missing_ids}",
                status_code=422,
            )

        setting = _get_or_create_setting(session)
        setting.recipient_contact_ids_json = json.dumps(
            request.recipient_contact_ids, ensure_ascii=False
        )
        setting.message_text = request.message_text
        setting.updated_at = _utcnow()
        session.commit()
        session.refresh(setting)
        return _setting_to_dict(session, setting)


def list_handoff_notification_contacts(
    *, page: int = 1, page_size: int = 50, keyword: str = ""
) -> dict[str, Any]:
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    with _get_session() as session:
        query = select(EyunContactModel).where(EyunContactModel.status == "active")
        keyword = keyword.strip()
        if keyword:
            pattern = f"%{keyword}%"
            query = query.where(
                or_(
                    EyunContactModel.remark_name.ilike(pattern),
                    EyunContactModel.display_name.ilike(pattern),
                    EyunContactModel.wechat_id.ilike(pattern),
                    EyunContactModel.wc_id.ilike(pattern),
                )
            )
        total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
        contacts = session.scalars(
            query.order_by(
                EyunContactModel.last_seen_at.desc(), EyunContactModel.id.desc()
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "items": [_contact_to_dict(contact) for contact in contacts],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


async def enqueue_handoff_notification(
    *,
    customer_wc_id: str,
    nickname: str | None = None,
    wechat_id: str | None = None,
    handoff_reason: str | None = None,
    trigger_message: str | None = None,
    source_reference: str | None = None,
) -> dict[str, Any]:
    with _get_session() as session:
        setting = _get_or_create_setting(session)
        recipient_ids = _recipient_ids(setting)
        if not recipient_ids:
            session.commit()
            return {"queued": 0, "reason": "no_recipients"}
        recipients = session.scalars(
            select(EyunContactModel).where(
                EyunContactModel.id.in_(recipient_ids),
                EyunContactModel.status == "active",
            )
        ).all()
        recipients_by_id = {contact.id: contact for contact in recipients}
        recipients = [
            recipients_by_id[contact_id]
            for contact_id in recipient_ids
            if contact_id in recipients_by_id
        ]
        customer = session.scalar(
            select(EyunContactModel)
            .where(EyunContactModel.wc_id == customer_wc_id)
            .order_by(EyunContactModel.updated_at.desc())
            .limit(1)
        )
        message_text = setting.message_text.strip()
        recipient_targets = [
            {
                "id": recipient.id,
                "wc_id": recipient.wc_id,
                "w_id": recipient.current_w_id,
            }
            for recipient in recipients
        ]
        customer_display_name = customer.display_name if customer else None
        customer_saved_wechat_id = customer.wechat_id if customer else None
        session.commit()

    customer_nickname = (
        (nickname or "").strip()
        or customer_display_name
        or customer_wc_id
    )
    customer_wechat_id = (
        (wechat_id or "").strip()
        or customer_saved_wechat_id
        or customer_wc_id
    )
    content = _render_notification_message(
        message_text=message_text,
        nickname=customer_nickname,
        wechat_id=customer_wechat_id,
        handoff_reason=_handoff_reason_text(handoff_reason),
        trigger_message=(trigger_message or "").strip() or "未记录",
    )

    from app.integrations.eyun.services.message_risk_control_service import enqueue_wechat_outbound

    queued_ids: list[int] = []
    for recipient in recipient_targets:
        w_id = (recipient["w_id"] or get_settings().eyun_wid).strip()
        if not w_id:
            logger.warning(
                "Skip handoff notification recipient %s because wId is missing",
                recipient["id"],
            )
            continue
        try:
            outbound = await enqueue_wechat_outbound(
                w_id=w_id,
                wc_id=str(recipient["wc_id"]),
                content=content,
                source_batch_key=(
                    f"handoff_notification:{source_reference or customer_wc_id}:{recipient['id']}"
                ),
                due_at=_utcnow(),
            )
            queued_ids.append(int(outbound["id"]))
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Failed to queue handoff notification for recipient %s: %s",
                recipient["id"],
                exc,
            )
    return {"queued": len(queued_ids), "outbound_message_ids": queued_ids}


def _render_notification_message(
    *,
    message_text: str,
    nickname: str,
    wechat_id: str,
    handoff_reason: str,
    trigger_message: str,
) -> str:
    return (
        f"{message_text.strip()}\n\n"
        f"转人工用户昵称：{nickname}\n"
        f"转人工用户微信号：{wechat_id}\n"
        f"转人工原因：{handoff_reason}\n"
        f"触发客户消息：{trigger_message}"
    )


def _handoff_reason_text(reason: str | None) -> str:
    value = str(reason or "").strip()
    if not value:
        return HANDOFF_REASON_TEXTS["human_required"]
    return HANDOFF_REASON_TEXTS.get(value, value)


def _get_session() -> Session:
    database_url = get_settings().database_url
    factory = _sessionmakers.get(database_url)
    if factory is None:
        engine = create_engine(database_url)
        Base.metadata.create_all(
            engine,
            tables=[
                EyunContactModel.__table__,
                HandoffNotificationSettingModel.__table__,
            ],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[database_url] = factory
    return factory()


def _get_or_create_setting(session: Session) -> HandoffNotificationSettingModel:
    setting = session.get(HandoffNotificationSettingModel, SETTING_ID)
    if setting is None:
        now = _utcnow()
        setting = HandoffNotificationSettingModel(
            id=SETTING_ID,
            recipient_contact_ids_json="[]",
            message_text=DEFAULT_MESSAGE_TEXT,
            created_at=now,
            updated_at=now,
        )
        session.add(setting)
        session.flush()
    return setting


def _setting_to_dict(
    session: Session, setting: HandoffNotificationSettingModel
) -> dict[str, Any]:
    recipient_ids = _recipient_ids(setting)
    contacts = session.scalars(
        select(EyunContactModel).where(EyunContactModel.id.in_(recipient_ids))
    ).all() if recipient_ids else []
    contacts_by_id = {contact.id: contact for contact in contacts}
    return {
        "recipient_contact_ids": recipient_ids,
        "recipients": [
            _contact_to_dict(contacts_by_id[contact_id])
            for contact_id in recipient_ids
            if contact_id in contacts_by_id
        ],
        "message_text": setting.message_text,
        "updated_at": setting.updated_at.isoformat(),
    }


def _recipient_ids(setting: HandoffNotificationSettingModel) -> list[int]:
    try:
        values = json.loads(setting.recipient_contact_ids_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(values, list):
        return []
    return list(
        dict.fromkeys(
            int(value)
            for value in values
            if isinstance(value, int) and value > 0
        )
    )


def _contact_to_dict(contact: EyunContactModel) -> dict[str, Any]:
    return {
        "id": contact.id,
        "wc_id": contact.wc_id,
        "display_name": contact.display_name,
        "remark_name": contact.remark_name,
        "wechat_id": contact.wechat_id,
        "avatar_url": contact.avatar_url,
        "status": contact.status,
    }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
