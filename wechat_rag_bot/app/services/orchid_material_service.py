from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


ORCHID_MATERIAL_TEXT = (
    "直播间展示的是图文版资料， 我们以电子档形式给您发放了，请及时参考，"
    "48小时链接会失效哦~\n"
    "我们在浙江，品类比较丰富，春蕙兰建兰墨兰等传统品种都在种植，"
    "如果以后您有喜欢的品种可以考虑下我们家，结缘任意一株兰草都可享受"
    "陪伴养兰手把手指导服务，资料-百节视频-一对一指导都是长期给会员开放权限的"
)
ORCHID_MATERIAL_IMAGE_URL = (
    "http://150.158.52.233/static/orchid-material/"
    "companion-service-video-links.png"
)
ORCHID_MATERIAL_VIDEO_ISSUE_REPLY = (
    "我们的视频是购买过我们产品的客户才能观看的。请问您是在抖音上购买的吗？"
    "麻烦您发送一下订单截图，我先帮您核实购买记录。"
)
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
        "http://150.158.52.233/static/orchid-material/"
        "companion-material-card-thumb.jpg"
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

_VIDEO_ISSUE_PATTERNS = (
    r"(?:资料里|资料内|资料中的|你们的|发的|里面的)?.{0,6}"
    r"(?:视频|课程).{0,8}(?:打不开|打不了|无法打开|不能打开|看不了|无法播放|不能播放|播放不了|点不开|失效)",
    r"(?:打不开|打不了|无法打开|不能打开|看不了|无法播放|不能播放|播放不了|点不开|失效).{0,8}"
    r"(?:资料里|资料内|资料中的|你们的|发的|里面的)?.{0,6}(?:视频|课程)",
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
    has_resource = any(value in normalized for value in ("资料", "教程"))
    has_request = any(
        value in normalized
        for value in (
            "发我",
            "发一下",
            "给我",
            "想要",
            "需要",
            "领取",
            "怎么领",
            "获取",
            "看看",
        )
    )
    has_orchid_context = any(
        value in normalized
        for value in ("养兰", "兰花", "养护", "萧岚苑", "直播间", "视频")
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
            {
                "type": "link_card",
                "content": json.dumps(card_payload, ensure_ascii=False),
            },
            {
                "type": "text",
                "content": ORCHID_MATERIAL_TEXT,
                "split": False,
            },
            {"type": "image", "content": ORCHID_MATERIAL_IMAGE_URL},
        ],
        "reply_type": "fixed_resource",
        "route": "orchid_material_delivery",
        "metadata": {
            "resource_type": "orchid_material",
            "youzan_note_id": ORCHID_MATERIAL_CARD["note_id"],
        },
    }


def is_orchid_material_video_issue(content: str) -> bool:
    normalized = _normalize(content)
    return bool(normalized) and any(
        re.search(pattern, normalized) is not None
        for pattern in _VIDEO_ISSUE_PATTERNS
    )


def orchid_material_video_issue_chat_result(content: str) -> dict[str, Any] | None:
    if not is_orchid_material_video_issue(content):
        return None
    return {
        "answer": ORCHID_MATERIAL_VIDEO_ISSUE_REPLY,
        "answer_segments": [ORCHID_MATERIAL_VIDEO_ISSUE_REPLY],
        "outbound_messages": [
            {
                "type": "text",
                "content": ORCHID_MATERIAL_VIDEO_ISSUE_REPLY,
                "split": False,
            }
        ],
        "reply_type": "fixed_text",
        "route": "orchid_material_video_issue",
        "metadata": {"resource_type": "orchid_material"},
    }


def _normalize(content: str) -> str:
    value = unicodedata.normalize("NFKC", str(content or "")).casefold()
    return re.sub(r"[\s,，。.!！?？:：;；、~～]", "", value)
