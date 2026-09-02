from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree


@dataclass(frozen=True)
class EyunInboundClassification:
    category: str
    subtype: str
    disposition: str
    display_content: str
    semantic_text: str = ""

    def to_metadata(self) -> dict[str, str]:
        return asdict(self)


_MESSAGE_TYPES = {
    "001": ("text", "text", "agent", ""),
    "002": ("image", "image", "agent", "[图片]"),
    "003": ("video", "video", "media", "[视频]"),
    "004": ("audio", "audio", "media", "[语音]"),
    "005": ("contact", "contact", "agent", "[名片]"),
    "006": ("emoji", "emoji", "reaction", "[表情]"),
    "007": ("link", "link", "agent", "[链接]"),
    "008": ("file", "file", "agent", "[文件]"),
    "009": ("file", "file", "agent", "[文件]"),
    "010": ("mini_program", "mini_program", "agent", "[小程序]"),
    "011": ("chat_history", "chat_history", "agent", "[聊天记录]"),
    "020": ("location", "location", "agent", "[位置]"),
}

_APP_MESSAGE_TYPES = {
    "5": ("link", "link", "agent", "[链接]"),
    "6": ("file", "file", "agent", "[文件]"),
    "8": ("emoji", "emoji", "reaction", "[表情]"),
    "19": ("chat_history", "chat_history", "agent", "[聊天记录]"),
    "33": ("mini_program", "mini_program", "agent", "[小程序]"),
    "36": ("mini_program", "mini_program", "agent", "[小程序]"),
    "109": ("app_card", "app_message_109", "agent", "[应用卡片]"),
    "2000": ("payment", "transfer", "agent", "[收付款消息]"),
    "2001": ("payment", "red_packet", "agent", "[红包消息]"),
}


def classify_eyun_inbound(payload: dict[str, Any]) -> EyunInboundClassification:
    message_type = str(payload.get("messageType") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    content = str(data.get("content") or "").strip()

    if _is_opening_event(content):
        return EyunInboundClassification(
            category="opening_event",
            subtype="new_friend",
            disposition="opening",
            display_content=content or "[新好友]",
            semantic_text=content,
        )
    if message_type == "60999":
        return _classify_private_other(content)

    definition = _MESSAGE_TYPES.get(message_type[-3:])
    if definition is None:
        return EyunInboundClassification(
            category="unknown",
            subtype=message_type or "missing_message_type",
            disposition="unknown",
            display_content="[未知消息]",
        )

    category, subtype, disposition, display_content = definition
    if category == "text":
        return EyunInboundClassification(
            category=category,
            subtype=subtype,
            disposition=disposition,
            display_content=content or "[空消息]",
            semantic_text=content,
        )
    return EyunInboundClassification(
        category=category,
        subtype=subtype,
        disposition=disposition,
        display_content=display_content,
        semantic_text=_semantic_text(category, display_content, content),
    )


def payload_for_agent(
    payload: dict[str, Any], classification: EyunInboundClassification
) -> dict[str, Any]:
    if classification.category in {"text", "image"}:
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    return {
        **payload,
        "messageType": "60001",
        "_eyun_original_message_type": str(payload.get("messageType") or ""),
        "_eyun_classification": classification.to_metadata(),
        "data": {
            **data,
            "content": classification.semantic_text or classification.display_content,
        },
    }


def _classify_private_other(content: str) -> EyunInboundClassification:
    root = _parse_xml(content)
    if root is not None:
        root_name = _local_name(root.tag)
        system_type = str(root.attrib.get("type") or "").strip()
        if root_name == "sysmsg":
            return EyunInboundClassification(
                category="system_event",
                subtype=system_type or "sysmsg",
                disposition="ignore",
                display_content="[系统事件]",
            )

        app_message = next(
            (node for node in root.iter() if _local_name(node.tag) == "appmsg"),
            None,
        )
        if app_message is not None:
            app_type = _first_text(app_message, ("type",))
            definition = _APP_MESSAGE_TYPES.get(
                app_type,
                (
                    "app_card",
                    f"app_message_{app_type or 'unknown'}",
                    "agent",
                    "[应用卡片]",
                ),
            )
            category, subtype, disposition, display_content = definition
            return EyunInboundClassification(
                category=category,
                subtype=subtype,
                disposition=disposition,
                display_content=display_content,
                semantic_text=_semantic_text(category, display_content, content),
            )

        if any(_local_name(node.tag) == "emoji" for node in root.iter()):
            return EyunInboundClassification(
                category="emoji",
                subtype="emoji_xml",
                disposition="reaction",
                display_content="[表情]",
                semantic_text="[客户发送了一个表情]",
            )
        if any(_local_name(node.tag) == "img" for node in root.iter()):
            return EyunInboundClassification(
                category="image",
                subtype="image_xml",
                disposition="agent",
                display_content="[图片]",
                semantic_text="[客户发送了一张图片]",
            )

    return EyunInboundClassification(
        category="unknown",
        subtype="private_other",
        disposition="unknown",
        display_content="[未知消息]",
        semantic_text=content if content and not content.startswith("<") else "",
    )


def _is_opening_event(content: str) -> bool:
    return (
        ("你已添加了" in content and "以上是打招呼的消息" in content)
        or content.strip("。 ") == "以上是打招呼的消息"
        or "NewXmlOpenIMFriReqAcceptedInWxWork" in content
    )


def _semantic_text(category: str, label: str, content: str) -> str:
    root = _parse_xml(content)
    if root is None:
        return f"{label} {content}".strip()
    fields: list[str] = []
    for field_label, tags in (
        ("标题", ("title", "filename")),
        ("描述", ("des", "description")),
        ("链接", ("url", "pagepath")),
        ("位置", ("label", "poiname", "address")),
    ):
        value = _first_text(root, tags)
        if value:
            fields.append(f"{field_label}：{value}")
    if fields:
        return f"{label} " + "；".join(fields)
    readable_category = {
        "contact": "名片",
        "file": "文件",
        "chat_history": "聊天记录",
        "location": "位置",
        "payment": "收付款信息",
        "emoji": "表情",
    }.get(category, label.strip("[]"))
    return f"[客户发送了{readable_category}]"


def _parse_xml(content: str) -> ElementTree.Element | None:
    if not content.lstrip().startswith("<"):
        return None
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError:
        return None


def _first_text(root: ElementTree.Element, tags: tuple[str, ...]) -> str:
    wanted = {tag.lower() for tag in tags}
    for node in root.iter():
        if _local_name(node.tag) not in wanted:
            continue
        value = unquote(str(node.text or "").strip())
        if value:
            return value
    return ""


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1].lower()
