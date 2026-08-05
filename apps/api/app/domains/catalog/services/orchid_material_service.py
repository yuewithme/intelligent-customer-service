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
ORCHID_MATERIAL_VIDEO_ISSUE_SNAPSHOT = (
    "客户反馈此前发送的养兰资料中的视频或课程无法打开。"
    "当前不能判定客户是否具备观看权益，也不能承诺已经恢复。"
    "处理时先确认客户是否通过抖音购买；需要客户提供订单截图后，"
    "才能进一步核实购买记录和资料观看权益。"
)
ORCHID_MATERIAL_PURCHASE_QUESTION = (
    "资料里的视频打不开确实影响使用。请问您是在抖音购买的吗？"
)
ORCHID_MATERIAL_ORDER_SCREENSHOT_REQUEST = (
    "好的，请把抖音购买的订单截图发给我，我先帮您核实购买记录和资料观看权限。"
)
ORCHID_MATERIAL_ORDER_SCREENSHOT_SNAPSHOT = (
    "客户已确认通过抖音购买。当前只需要客户提供抖音购买订单截图，"
    "用于核实购买记录和资料观看权限；在截图核实前不能承诺已经恢复。"
)
ORCHID_MATERIAL_DISCOVERY_TEXT = (
    "可以的。为了给您发更适合的资料，我先了解一下：您现在养兰最想解决哪方面的问题？"
    "比如新手入门、浇水植料、黄叶黑斑、烂根腐苗，还是促花复花？"
    "您也可以直接说说目前遇到的具体情况。"
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
_MATERIAL_VERIFICATION_PATTERNS = (
    r"(?:直播间|主播|你们|咱们).{0,12}(?:说|讲|提到|承诺|有|送).{0,8}(?:资料|教程).{0,8}(?:真的|是真|靠谱吗|属实|有吗|有没有|吗|嘛)",
    r"(?:资料|教程).{0,8}(?:真的会发|会发吗|会给吗|是真的吗|是真的嘛|靠谱吗|有吗|有没有)",
)
_MATERIAL_DELIVERY_PATTERN = re.compile(
    r"(?:给我|帮我|麻烦|请|直接|现在|那就|就是|意思是).{0,8}(?:发|发送|给).{0,8}(?:资料|教程)"
    r"|(?:资料|教程).{0,8}(?:发我|给我|来一份)"
    r"|(?:想要|要一份|需要).{0,8}(?:资料|教程)"
)
_NEGATIVE_TRIGGERS = (
    "不要资料",
    "别发资料",
    "不用发资料",
    "不需要资料",
    "无需资料",
    "不要养兰资料",
)
_CONTEXTUAL_NEGATIVE_TRIGGERS = (
    "不要",
    "不用",
    "不需要",
    "别发",
    "已经领到",
    "领到了",
    "已经收到",
    "收到了",
)
_CONTEXTUAL_BLOCKERS = (
    "订单",
    "快递",
    "物流",
    "包裹",
    "发货",
    "签收",
    "退款",
    "退货",
    "投诉",
    "人工",
    "花盆",
    "破损",
    "苗体",
)
_CONTEXTUAL_SHORT_REPLIES = {
    "1",
    "好",
    "好的",
    "可以",
    "要",
    "需要",
    "想要",
    "发我",
    "给我",
    "资料",
    "养护",
    "视频",
    "课程",
    "领取",
    "怎么领",
    "怎么领取",
    "如何领取",
    "没领到",
    "还没领到",
    "没有领到",
    "没收到",
    "还没收到",
    "没有收到",
    "[强]",
    "[握手]",
}

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
    if is_orchid_material_verification(content):
        return True
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


def is_orchid_material_verification(content: str) -> bool:
    normalized = _normalize(content)
    if not normalized or any(value in normalized for value in _NEGATIVE_TRIGGERS):
        return False
    return any(
        re.search(pattern, normalized) is not None
        for pattern in _MATERIAL_VERIFICATION_PATTERNS
    )


def orchid_material_request_kind(content: str) -> str:
    """Separate direct delivery requests from early material discovery."""

    if is_orchid_material_verification(content):
        return "verification"
    if _MATERIAL_DELIVERY_PATTERN.search(_normalize(content)):
        return "delivery"
    return "discovery"


def is_orchid_material_followup(
    content: str,
    recent_turns: list[dict] | None,
) -> bool:
    recent_turns = recent_turns if isinstance(recent_turns, list) else []
    latest_assistant = next(
        (
            turn
            for turn in reversed(recent_turns)
            if isinstance(turn, dict) and turn.get("role") == "assistant"
        ),
        None,
    )
    if latest_assistant is None:
        return False
    assistant_content = _normalize(str(latest_assistant.get("content") or ""))
    assistant_route = str(latest_assistant.get("route") or "")
    has_opening_offer = assistant_route == "opening" or (
        "养兰资料" in assistant_content
        and any(marker in assistant_content for marker in ("提供", "领取", "回复"))
    )
    if not has_opening_offer:
        return False

    normalized = _normalize(content)
    compact = re.sub(r"[，。！？、,.!?\s]+", "", normalized)
    if (
        not compact
        or any(marker in compact for marker in _CONTEXTUAL_NEGATIVE_TRIGGERS)
        or any(marker in compact for marker in _CONTEXTUAL_BLOCKERS)
        or any(marker in compact for marker in ("打不开", "失效", "看不了"))
    ):
        return False
    return (
        is_orchid_material_request(content)
        or compact in _CONTEXTUAL_SHORT_REPLIES
        or re.fullmatch(r"(?:(?:好的)|好|可以|嗯){1,3}", compact) is not None
        or re.search(r"(?:怎么|如何).{0,3}(?:领|领取|获取)", compact) is not None
        or re.search(r"(?:没|没有|还没).{0,2}(?:领|收到)", compact) is not None
        or "[强]" in compact
    )


def orchid_material_chat_result(
    content: str,
    *,
    confirmed_request: bool = False,
) -> dict[str, Any] | None:
    if not confirmed_request and not is_orchid_material_request(content):
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
        "next_action": "查看养兰资料",
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


def orchid_material_video_issue_context(content: str) -> dict[str, Any] | None:
    if not is_orchid_material_video_issue(content):
        return None
    return {
        "business_snapshot": ORCHID_MATERIAL_VIDEO_ISSUE_SNAPSHOT,
        "tool_state": {
            "resource_access_issue": "video_unavailable",
            "purchase_channel": "unverified",
            "purchase_record": "unverified",
            "viewing_entitlement": "unverified",
            "material_video_access_action": "confirm_douyin_purchase",
        },
    }


def is_douyin_purchase_confirmation(content: str) -> bool:
    normalized = _normalize(content)
    if not normalized or any(
        marker in normalized
        for marker in ("不是", "没有", "没买", "未买", "非抖音")
    ):
        return False
    if "抖音" in normalized and any(
        marker in normalized for marker in ("买", "购买", "下单", "订单", "有")
    ):
        return True
    return normalized in {
        "是",
        "是的",
        "是啊",
        "对",
        "对的",
        "嗯",
        "嗯嗯",
        "有",
        "有的",
        "有买",
        "有买的",
        "有购买",
        "有购买的",
        "有订单",
        "有订单的",
        "买了",
        "买过",
        "购买了",
        "我买了",
    }


def orchid_material_order_screenshot_context(content: str) -> dict[str, Any] | None:
    if not is_douyin_purchase_confirmation(content):
        return None
    return {
        "business_snapshot": ORCHID_MATERIAL_ORDER_SCREENSHOT_SNAPSHOT,
        "tool_state": {
            "resource_access_issue": "video_unavailable",
            "purchase_channel": "douyin_confirmed",
            "purchase_record": "unverified",
            "viewing_entitlement": "unverified",
            "material_video_access_action": "request_order_screenshot",
        },
    }


def orchid_material_video_access_chat_result(action: str) -> dict[str, Any] | None:
    replies = {
        "confirm_douyin_purchase": (
            ORCHID_MATERIAL_PURCHASE_QUESTION,
            "orchid_material_purchase_check",
        ),
        "request_order_screenshot": (
            ORCHID_MATERIAL_ORDER_SCREENSHOT_REQUEST,
            "orchid_material_order_screenshot_request",
        ),
    }
    selected = replies.get(str(action or ""))
    if selected is None:
        return None
    answer, route = selected
    return {
        "answer": answer,
        "answer_segments": [answer],
        "outbound_messages": [{"type": "text", "content": answer}],
        "reply_type": "fixed_workflow",
        "route": route,
        "metadata": {
            "resource_type": "orchid_material",
            "material_video_access_action": action,
        },
    }


def orchid_material_discovery_chat_result() -> dict[str, Any]:
    return {
        "answer": ORCHID_MATERIAL_DISCOVERY_TEXT,
        "answer_segments": [ORCHID_MATERIAL_DISCOVERY_TEXT],
        "outbound_messages": [
            {"type": "text", "content": ORCHID_MATERIAL_DISCOVERY_TEXT},
        ],
        "reply_type": "fixed_text",
        "route": "orchid_material_discovery",
        "next_action": "确认资料需求",
        "metadata": {
            "resource_type": "orchid_material",
            "material_request_phase": "discovery",
        },
    }


def _normalize(content: str) -> str:
    value = unicodedata.normalize("NFKC", str(content or "")).casefold()
    return re.sub(r"[\s,，。.!！?？:：;；、~～]", "", value)
