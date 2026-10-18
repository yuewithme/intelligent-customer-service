import json

from sqlalchemy import select

from app.domains.conversations.services.conversation_service import _get_session
from app.infrastructure.database.models import ConversationMessageModel


def message_context_text(message: dict) -> str:
    metadata = message.get("metadata") or {}
    media = metadata.get("media")
    for key, label in (("link_card", "链接卡片"), ("mini_program", "小程序")):
        card = metadata.get(key)
        if isinstance(card, dict):
            details = [str(card.get(field) or "").strip() for field in ("title", "description")]
            if any(details):
                return f"[{label}] " + "；".join(part for part in details if part)
    labels = {"image": "图片", "audio": "语音", "video": "视频"}
    if not isinstance(media, dict) or media.get("type") not in labels:
        return str(message.get("content") or "").strip()
    label = labels[media["type"]]
    recognition = media.get("recognition") or {}
    if recognition.get("status") != "succeeded":
        return f"[{label}内容尚未可靠识别]"
    parts = [str(recognition.get(key) or "").strip() for key in ("text", "summary", "transcript")]
    for key, title in (("visible_content", "画面内容"), ("orchid_observations", "兰花观察")):
        values = recognition.get(key)
        if isinstance(values, list) and values:
            parts.append(f"{title}：" + "；".join(str(value) for value in values))
    parts = list(dict.fromkeys(part for part in parts if part))
    if not parts:
        return f"[{label}内容尚未可靠识别]"
    if recognition.get("needs_clarification"):
        parts.append("内容仍有不确定之处，需结合客户原话理解。")
    return f"[{label}识别内容] " + "\n".join(parts)


def get_recent_customer_media(conversation_id: str) -> list[dict]:
    with _get_session() as session:
        rows = session.scalars(select(ConversationMessageModel).where(
            ConversationMessageModel.conversation_id == conversation_id,
            ConversationMessageModel.sender_type == "customer",
        ).order_by(ConversationMessageModel.created_at.desc(), ConversationMessageModel.id.desc()).limit(50)).all()
        result = []
        remaining = 12_000
        for row in rows:
            try:
                metadata = json.loads(row.metadata_json or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(metadata, dict) or metadata.get("revoked_at") or row.delivery_status == "revoked":
                continue
            media = metadata.get("media") or {}
            if not isinstance(media, dict) or media.get("type") not in {"image", "audio", "video"}:
                continue
            content = message_context_text({"content": row.content, "metadata": metadata})
            if len(content) > remaining:
                content = content[:remaining] + "…[内容已截断]"
            result.append({
                "message_id": row.id,
                "created_at": row.created_at.isoformat(),
                "type": media["type"],
                "content": content,
            })
            remaining -= len(content)
            if len(result) >= 10 or remaining <= 0:
                break
    return list(reversed(result))
