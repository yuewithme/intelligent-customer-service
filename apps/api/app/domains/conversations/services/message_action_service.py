import json
from xml.etree import ElementTree

from app.shared.schemas.common import AppError, ErrorCode


def _invalid(message: str, status: int = 409) -> AppError:
    return AppError(ErrorCode.REQUEST_INVALID, message=message, status_code=status)


def forward_payload(content: str, metadata: dict) -> tuple[str, str] | None:
    if metadata.get("revoked_at"):
        return None
    kind = str(metadata.get("message_type") or "")
    media = metadata.get("media") or {}
    raw = str(metadata.get("raw_content") or "").strip()
    if raw.startswith("<"):
        try:
            root = ElementTree.fromstring(raw)
        except ElementTree.ParseError:
            root = None
        app = next((node for node in root.iter() if node.tag == "appmsg"), None) if root is not None else None
        app_type = app.findtext("type") if app is not None else None
        if app_type in {"33", "36"}:
            thumb = str(app.findtext("thumburl") or app.findtext("weappinfo/weappiconurl") or "").strip()
            if thumb.startswith(("http://", "https://")):
                return "received_mini_program", json.dumps({"content": raw, "imgUrl": thumb})
        if app_type == "5":
            return "received_link", raw
        if app_type == "6":
            return "received_file", raw
        received_type = {
            "image": "received_image", "video": "received_video",
            "file": "received_file",
        }.get(media.get("type"))
        if received_type:
            return received_type, raw
        if media.get("type") == "link":
            return "received_link", raw
    for card_type in ("link_card", "mini_program"):
        card = metadata.get(card_type)
        if isinstance(card, dict) and card:
            return card_type, json.dumps(card, ensure_ascii=False)
    if kind in {"image", "video", "emoji", "received_image", "received_video", "received_file", "received_link", "received_mini_program"}:
        outbound = str(metadata.get("outbound_content") or "")
        if outbound:
            return kind, outbound
    if media.get("type") == "emoji" and media.get("md5") and media.get("size"):
        return "emoji", json.dumps({"md5": media["md5"], "size": media["size"]})
    if media.get("type") == "mini_program" and raw and media.get("thumb_url"):
        return "received_mini_program", json.dumps({"content": raw, "imgUrl": media["thumb_url"]})
    if kind in {"", "text", "60001", "80001"} and not media and content.strip():
        return "text", content
    return None


def message_actions(row, metadata: dict) -> dict[str, bool]:
    available = row.sender_type != "system" and not metadata.get("revoked_at")
    receipts = metadata.get("revoke_receipts") or []
    return {
        "quote": bool(available),
        "forward": bool(available and forward_payload(row.content, metadata)),
        "revoke": bool(
            available and row.sender_type in {"human", "ai"}
            and metadata.get("provider") == "eyun"
            and metadata.get("revoke_receipts_complete") and receipts
            and row.delivery_status in {"accepted", "confirmed", "sent", "unconfirmed", "delivery_unknown"}
        ),
    }


def quote_message(session, conversation, source_message_id: int) -> dict:
    from app.infrastructure.database.models import ConversationMessageModel
    from app.domains.conversations.services.conversation_service import (
        _get_conversation_or_error, _load_metadata,
    )

    source = session.get(ConversationMessageModel, source_message_id)
    if source is None:
        raise _invalid("引用消息不存在", 404)
    origin = _get_conversation_or_error(session, source.conversation_id)
    if (origin.channel, origin.user_id, origin.owner_wc_id) != (
        conversation.channel, conversation.user_id, conversation.owner_wc_id
    ):
        raise _invalid("只能引用当前客户、当前微信的消息", 403)
    metadata = _load_metadata(source.metadata_json)
    if not message_actions(source, metadata)["quote"]:
        raise _invalid("该消息不能引用")
    label = (origin.user_display_name or "客户") if source.sender_type == "customer" else "客服"
    media = metadata.get("media") or {}
    recognition = media.get("recognition") or {}
    content = source.content
    if media.get("type") == "audio" and recognition.get("status") == "succeeded":
        content = str(recognition.get("text") or "").strip() or content
    return {"id": source.id, "sender": label, "content": content[:500]}


async def forward_conversation_message(conversation_id: str, operator_id: str, source_message_id: int) -> dict:
    from app.infrastructure.database.models import ConversationMessageModel
    from app.domains.conversations.services.conversation_service import (
        _get_session, _get_conversation_or_error, _message_to_dict, _reply_conversation_media,
        reply_conversation,
    )

    with _get_session() as session:
        target = _get_conversation_or_error(session, conversation_id)
        source = session.get(ConversationMessageModel, source_message_id)
        if source is None:
            raise _invalid("转发消息不存在", 404)
        origin = _get_conversation_or_error(session, source.conversation_id)
        metadata = _message_to_dict(source)["metadata"]
        payload = forward_payload(source.content, metadata)
        if not message_actions(source, metadata)["forward"] or not payload:
            raise _invalid("该消息缺少可转发的内容")
        kind, content = payload
        # Received XML contains account-bound CDN credentials.
        if kind.startswith("received_") and (not origin.owner_wc_id or origin.owner_wc_id != target.owner_wc_id):
            raise _invalid("此类微信消息仅可转发到同一微信账号的会话")
        display_content = source.content
        message_metadata = {
            key: metadata[key] for key in ("media", "link_card", "mini_program") if key in metadata
        }
        message_metadata["forwarded_from_message_id"] = source.id
    if kind == "text":
        quote = metadata.get("quoted_message")
        if isinstance(quote, dict):
            content = f"「{quote['sender']}：{quote['content']}」\n{content}"
        return await reply_conversation(conversation_id, operator_id, content)
    return await _reply_conversation_media(
        conversation_id, operator_id, message_type=kind, content=content,
        display_content=display_content, message_metadata=message_metadata,
    )


async def revoke_conversation_message(message_id: int, operator_id: str) -> dict:
    from sqlalchemy import select
    from app.infrastructure.database.models import ConversationMessageModel, ConversationModel
    from app.domains.conversations.services.conversation_service import (
        _get_session, _load_metadata, _message_to_dict, _now, _publish_change,
    )
    from app.integrations.eyun.services.eyun_callback_service import revoke_eyun_message

    with _get_session() as session:
        message = session.get(ConversationMessageModel, message_id)
        if message is None:
            raise _invalid("消息不存在", 404)
        metadata = _load_metadata(message.metadata_json)
        if metadata.get("revoked_at"):
            return _message_to_dict(message)
        if not message_actions(message, metadata)["revoke"]:
            raise _invalid("该消息未发送成功或缺少微信撤回凭据")
        receipts = metadata["revoke_receipts"]
        revoked_ids = set(metadata.get("revoked_receipt_ids") or [])
        result = _message_to_dict(message)
    for receipt in receipts:
        receipt_id = str(receipt["newMsgId"])
        if receipt_id in revoked_ids:
            continue
        try:
            await revoke_eyun_message(receipt)
        except Exception as exc:
            raise AppError(
                ErrorCode.WECHAT_REPLY_FAILED,
                message="微信撤回失败，可能已超过可撤回时间；请在微信中核实",
                status_code=502,
            ) from exc
        revoked_ids.add(receipt_id)
        # Persist each receipt so a partial failure never repeats successful revocations.
        with _get_session() as session:
            message = session.get(ConversationMessageModel, message_id)
            metadata = _load_metadata(message.metadata_json)
            metadata["revoked_receipt_ids"] = sorted(revoked_ids)
            if len(revoked_ids) == len(receipts):
                metadata.update(revoked_at=_now().isoformat(), revoked_by=operator_id)
                message.delivery_status = "revoked"
                latest_id = session.scalar(select(ConversationMessageModel.id).where(
                    ConversationMessageModel.conversation_id == message.conversation_id,
                ).order_by(ConversationMessageModel.created_at.desc(), ConversationMessageModel.id.desc()).limit(1))
                if latest_id == message.id:
                    conversation = session.scalar(select(ConversationModel).where(
                        ConversationModel.conversation_id == message.conversation_id,
                    ))
                    conversation.last_message = "[消息已撤回]"
            message.metadata_json = json.dumps(metadata, ensure_ascii=False)
            session.commit()
            result = _message_to_dict(message)
            conversation_id = message.conversation_id
        _publish_change(conversation_id, "message")
    return result


async def reply_conversation_product(conversation_id: str, operator_id: str, item_id: str) -> dict:
    from app.integrations.youzan.services.youzan_product_sync_service import list_products
    from app.domains.conversations.services.conversation_service import _reply_conversation_media

    product = None
    page = 1
    while True:
        result = list_products(page=page, page_size=200, keyword=item_id, catalog_only=True)
        product = next((item for item in result["items"] if item["item_id"] == item_id), None)
        if product or not result["items"] or page * 200 >= result["total"]:
            break
        page += 1
    if not product:
        raise _invalid("商品不存在、已下架或已售罄", 404)
    url = str(product.get("h5_url") or product.get("page_url") or "").strip()
    if not url.lower().startswith(("https://", "http://")):
        raise _invalid("商品缺少可发送的购买链接")
    price = product.get("price_cent")
    card = {
        "title": product["title"], "url": url,
        "thumb_url": str(product.get("image_url") or ""),
        "description": f"¥{price / 100:.2f}，点击查看商品详情" if price is not None else "点击查看商品详情",
    }
    return await _reply_conversation_media(
        conversation_id, operator_id, message_type="link_card",
        content=json.dumps(card, ensure_ascii=False),
        display_content=f"[商品] {card['title']}",
        message_metadata={"link_card": card, "product_item_id": item_id},
    )
