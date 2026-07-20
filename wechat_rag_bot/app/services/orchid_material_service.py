from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


ORCHID_MATERIAL_TEXT = "亲，这是我们萧岚苑陪伴养兰服务的资料，您可以查收一下"
ORCHID_MATERIAL_CARD = {
    "note_id": "24482256",
    "note_alias": "0Ja8r3cajo",
    "title": "萧岚苑陪伴养兰资料",
    "url": "https://h5.youzan.com/wscshop/shopnote/detail?noteAlias=0Ja8r3cajo",
    "description": (
        "《萧岚苑陪伴养兰资料》系统性收录我家兰圃秘传技艺和养兰心得，"
        "让你5分钟轻松学会养兰基础，新手上道更轻松。"
    ),
    "thumb_url": (
        "https://img01.yzcdn.cn/upload_files/2025/04/19/"
        "FhLcsP1lvFtffSQOTpWQbksCivad.jpg"
    ),
}

_DIRECT_TRIGGERS = (
    "发资料",
    "要资料",
    "养兰资料",
    "养兰的资料",
    "兰花资料",
    "兰花的资料",
    "养护资料",
    "陪伴养兰资料",
)
_NEGATIVE_TRIGGERS = (
    "不要资料",
    "别发资料",
    "不用发资料",
    "不需要资料",
    "无需资料",
    "不要养兰资料",
)


def is_orchid_material_request(content: str) -> bool:
    normalized = _normalize(content)
    negative_request = re.search(
        r"(?:不要|不用发|不需要|无需|别发).{0,8}资料",
        normalized,
    )
    if (
        not normalized
        or negative_request is not None
        or any(value in normalized for value in _NEGATIVE_TRIGGERS)
    ):
        return False
    if any(value in normalized for value in _DIRECT_TRIGGERS):
        return True
    has_resource = "资料" in normalized
    has_request = any(
        value in normalized
        for value in ("发我", "发一下", "给我", "想要", "需要", "领取", "看看")
    )
    has_orchid_context = any(
        value in normalized for value in ("养兰", "兰花", "养护", "萧岚苑")
    )
    return has_resource and has_request and has_orchid_context


def orchid_material_chat_result(content: str) -> dict[str, Any] | None:
    if not is_orchid_material_request(content):
        return None
    card_payload = {
        key: ORCHID_MATERIAL_CARD[key]
        for key in ("title", "url", "description", "thumb_url")
    }
    return {
        "answer": ORCHID_MATERIAL_TEXT,
        "answer_segments": [ORCHID_MATERIAL_TEXT],
        "outbound_messages": [
            {"type": "text", "content": ORCHID_MATERIAL_TEXT},
            {
                "type": "link_card",
                "content": json.dumps(card_payload, ensure_ascii=False),
            },
        ],
        "reply_type": "fixed_resource",
        "route": "orchid_material_delivery",
        "metadata": {
            "resource_type": "orchid_material",
            "youzan_note_id": ORCHID_MATERIAL_CARD["note_id"],
        },
    }


def _normalize(content: str) -> str:
    value = unicodedata.normalize("NFKC", str(content or "")).casefold()
    return re.sub(r"[\s,，。.!！?？:：;；、~～]", "", value)
