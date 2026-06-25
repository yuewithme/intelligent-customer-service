import hashlib
import time
import xml.etree.ElementTree as ET

from app.schemas.common import AppError, ErrorCode
from app.utils.time import unix_timestamp


def verify_signature(
    token: str,
    signature: str,
    timestamp: str,
    nonce: str,
) -> bool:
    digest = hashlib.sha1(
        "".join(sorted([token, timestamp, nonce])).encode("utf-8")
    ).hexdigest()
    return digest == signature


def parse_message(xml_body: bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(xml_body)
        return {child.tag: child.text or "" for child in root}
    except ET.ParseError as exc:
        raise AppError(ErrorCode.WECHAT_MESSAGE_PARSE_FAILED) from exc


def _cdata(value: str) -> str:
    return value.replace("]]>", "]]]]><![CDATA[>")


def build_text_reply(to_user: str, from_user: str, content: str) -> str:
    return (
        "<xml>"
        f"<ToUserName><![CDATA[{_cdata(to_user)}]]></ToUserName>"
        f"<FromUserName><![CDATA[{_cdata(from_user)}]]></FromUserName>"
        f"<CreateTime>{unix_timestamp()}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{_cdata(content)}]]></Content>"
        "</xml>"
    )


class MessageDeduplicator:
    def __init__(self, ttl_seconds: int = 300, max_items: int = 10000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._items: dict[str, tuple[float, str]] = {}

    def get(self, message_id: str) -> str | None:
        item = self._items.get(message_id)
        if not item:
            return None
        created_at, reply = item
        if time.monotonic() - created_at > self.ttl_seconds:
            self._items.pop(message_id, None)
            return None
        return reply

    def set(self, message_id: str, reply: str) -> None:
        if len(self._items) >= self.max_items:
            oldest = min(self._items, key=lambda key: self._items[key][0])
            self._items.pop(oldest, None)
        self._items[message_id] = (time.monotonic(), reply)


message_deduplicator = MessageDeduplicator()

