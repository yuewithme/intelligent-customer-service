import re

from pydantic import ValidationError

from app.core.config import get_settings
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.sales.schemas.sales_flow import CustomerSignal
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.services.intent_taxonomy_service import (
    format_candidate_cards,
    format_candidate_cards_compact,
    prepare_intent_payload,
    taxonomy_values,
)
from app.domains.catalog.services.orchid_material_service import (
    is_orchid_material_followup,
    is_orchid_material_request,
)
from app.domains.sales.services.shipping_contact_service import extract_shipping_contact
from app.domains.sales.services.tag_catalog import normalize_system_value, system_tag_values
from app.domains.decisioning.services.intent_shadow_service import (
    record_intent_shadow,
    schedule_intent_shadow,
    shadow_selected,
)


HUMAN_WORDS = ("人工", "转人工", "真人", "人工客服")
IDENTITY_QUESTION_PATTERNS = (
    re.compile(
        r"(?:你|您|你们)(?:到底)?(?:是|是不是)"
        r"(?:真人|ai|人工智能|机器人|智能客服|人工客服)"
    ),
    re.compile(r"(?:你|您|你们)(?:到底)?是(?:真人还是机器人|机器人还是真人)"),
    re.compile(r"(?:你|您)(?:到底)?是谁"),
)
REFUND_WORDS = ("退款", "退货", "退钱", "退单")
COMPLAINT_WORDS = ("投诉", "举报", "骗子", "骗我", "不满意", "差评", "强烈不满")
PRICE_ASK_WORDS = ("价格", "多少钱", "报价", "优惠", "便宜")
PRICE_OBJECTION_WORDS = ("太贵", "有点贵", "好贵", "贵了", "价格贵", "不便宜")
HESITATION_WORDS = ("再考虑一下", "考虑一下", "考虑考虑", "再想想", "再看看")
CUSTOMER_SERVICE_REQUEST_WORDS = ("转客服", "找客服", "接客服", "人工客服", "客服介入", "客服处理")
LOGISTICS_WORDS = ("物流", "发货", "快递", "多久到", "什么时候到", "运费")
ORDER_WORDS = ("怎么买", "下单", "付款", "支付", "购买", "拍下")
AFTER_SALE_WORDS = ("售后", "坏了", "破损", "质量问题")
HARD_PURCHASE_REJECTION_WORDS = (
    "不要再推荐",
    "不要再给我推荐",
    "别再推荐",
    "别再给我推荐",
    "不用推荐",
    "别发链接",
    "不用发链接",
)
SOFT_PURCHASE_DEFERRAL_WORDS = (
    "不想买",
    "先不买",
    "暂时不买",
    "暂时不考虑",
    "先不考虑",
    "不考虑了",
    "不买了",
    "算了不买",
    "不用了",
)
PURCHASE_REJECTION_WORDS = (
    *HARD_PURCHASE_REJECTION_WORDS,
    *SOFT_PURCHASE_DEFERRAL_WORDS,
)
SHIPPING_DAMAGE_WORDS = ("花盆碎", "盆碎", "苗歪", "运输破损", "收到后破损")
ORDER_INFO_WORDS = ("身份证号", "详细地址", "收货地址", "电话号码", "电话")
KNOWLEDGE_PATTERNS = (
    "是什么",
    "怎么",
    "如何",
    "为什么",
    "有什么",
    "有哪些",
    "流程",
    "步骤",
    "方法",
    "注意事项",
    "区别",
    "适合",
    "能不能",
    "可以吗",
    "需要什么",
    "怎么使用",
    "怎么养",
    "怎么浇水",
    "怎么申请",
    "怎么处理",
    "注意什么",
    "说明",
    "资料",
    "材料",
)
CARE_INCIDENT_WORDS = (
    "黑根",
    "空根",
    "烂根",
    "腐根",
    "黄叶",
    "黑斑",
    "焦尖",
    "腐苗",
    "死苗",
    "修根",
    "修剪",
    "重新栽",
    "病虫害",
)
CARE_FAILURE_HISTORY_WORDS = (
    "养死",
    "死了",
    "总死",
    "反复死",
    "没养活",
    "养不活",
    "买了死",
    "死了买",
)
CARE_WORDS = (
    "养护",
    "养不活",
    "不会养",
    "新手",
    "浇水",
    "施肥",
    "护理",
    "怕养死",
    "怕养不好",
    *CARE_INCIDENT_WORDS,
)
GREETING_WORDS = ("你好", "您好", "在吗", "hello", "hi", "谢谢", "感谢")
PURE_GREETING_TEXTS = {
    "你好",
    "您好",
    "在吗",
    "有人吗",
    "hello",
    "hi",
    "早上好",
    "中午好",
    "下午好",
    "晚上好",
    "早安",
    "晚安",
}
PURE_THANKS_TEXTS = {
    "谢谢",
    "谢谢你",
    "谢谢您",
    "感谢",
    "感谢你",
    "感谢您",
    "辛苦了",
    "麻烦了",
    "不客气",
    "好的谢谢",
    "好谢谢",
    "好嘞谢谢",
    "嗯谢谢",
    "嗯嗯谢谢",
    "好的感谢",
    "收到谢谢",
}
PURE_END_TEXTS = {"再见", "拜拜", "回头聊", "先这样", "没问题了", "没有问题了"}
DEFER_FOLLOWUP_PATTERNS = (
    re.compile(
        r"(?:下班后|晚点|晚些时候|过会儿|回头|有空|方便时|到家后)"
        r".{0,12}(?:拍|发|看|联系|找你|回复)"
    ),
    re.compile(
        r"(?:拍|发|看|联系|找你|回复).{0,12}"
        r"(?:下班后|晚点|晚些时候|过会儿|回头|有空|方便时|到家后)"
    ),
)
MEMBERSHIP_PRODUCT_QUERY = "首单参与陪伴养兰客户"
MEMBERSHIP_EXCLUSION_WORDS = ("会员专属", "会员商品", "会员价商品")
MEMBERSHIP_ACTION_WORDS = (
    "加入",
    "开通",
    "办理",
    "购买",
    "买",
    "成为",
    "怎么",
    "如何",
    "多少钱",
    "价格",
    "费用",
    "收费",
    "付款",
    "支付",
    "有会员",
    "服务",
    "权益",
    "包含",
    "老师",
    "一对一",
    "指导",
)
SEMANTIC_COMMERCE_FALLBACK_REASONS = frozenset(
    {
        "rule_explicit_order_intent",
        "rule_product_purchase_query",
    }
)
UNSUPPORTED_WORDS = ("写代码", "彩票", "股票推荐", "医疗诊断", "法律意见", "无关业务")
ORDER_QUERY_WORDS = (
    "查订单",
    "查询订单",
    "我的订单",
    "订单状态",
    "订单发货",
    "发货了吗",
    "快递到哪",
    "物流到哪",
)
ORDER_SERVICE_ACTION_PATTERNS = {
    "shipping_date_change": (
        re.compile(r"(?:晚|迟|延迟|推迟|晚点|改天).{0,8}发货"),
        re.compile(r"发货.{0,8}(?:晚|迟|延迟|推迟|晚点|改天)"),
        re.compile(r"(?:先别|暂缓|暂停|不要马上)发货"),
        re.compile(r"(?:改|调整|修改).{0,8}发货(?:时间|日期|安排)?"),
    ),
}
PURCHASED_ENTITLEMENT_PATTERN = re.compile(
    r"(?:我|已经|之前).{0,8}(?:买|购买|下单|拍).{0,12}"
    r"(?:教程|课程|视频|资料|群|指导)"
)
PRODUCT_IMAGE_QUERY_WORDS = (
    "商品图片",
    "商品图",
    "实拍图",
    "发张图",
    "发图",
    "看图",
    "图册",
    "图集",
    "相册",
    "发图片",
    "看看图片",
    "看看照片",
    "有图片吗",
    "图片吗",
    "照片吗",
    "长什么样",
)
PRODUCT_LINK_QUERY_WORDS = (
    "商品链接",
    "产品链接",
    "购买链接",
    "下单链接",
    "购买入口",
    "下单入口",
    "发我链接",
    "发一下链接",
    "商品卡片",
    "产品卡片",
    "链接在哪",
    "链接在哪里",
    "哪里下单",
    "怎么下单",
    "怎么买",
)
PRODUCT_REFERENCE_PURCHASE_PATTERNS = (
    re.compile(
        r"(?:想|要|准备|决定|就|直接)?买"
        r"(?:这个|这款|这盆|这株|刚才那款|刚刚那款|上面那款)"
    ),
    re.compile(
        r"(?:这个|这款|这盆|这株|刚才那款|刚刚那款|上面那款)"
        r".*(?:购买|下单|链接)"
    ),
)
EXPLICIT_ORDER_PATTERNS = (
    re.compile(
        r"(?:^|[，,。！？!?])(?:那|那么)?(?:我)?"
        r"(?:想|要|准备|决定|就|直接)(?:买|购买|下单|拍下)"
    ),
    re.compile(r"(?:给我|帮我)(?:下单|拍下)"),
)
PRODUCT_QUERY_WORDS = (
    "有没有",
) + PRODUCT_LINK_QUERY_WORDS + PRODUCT_IMAGE_QUERY_WORDS
MOBILE_ONLY_PATTERN = re.compile(r"^1[3-9]\d{9}$")
PRICE_ONLY_FOLLOWUP_PATTERN = re.compile(
    r"^\d+(?:\.\d{1,2})?\s*(?:元|块)?(?:是吗|对吗|吗)?[？?]?$"
)
PLANT_COUNT_PATTERN = re.compile(
    r"(?:养了|養了|养|養|有)?[^\d零一二两三四五六七八九十百]{0,6}"
    r"(?:\d{1,5}|[零一二两三四五六七八九十百]{1,5})(?:来|多|左右)?\s*(?:盆|棵|株)"
)
ORCHID_VARIETY_WORDS = (
    "建兰",
    "春兰",
    "蕙兰",
    "墨兰",
    "寒兰",
    "春剑",
    "莲瓣兰",
    "蝴蝶兰",
    "石斛兰",
    "兜兰",
    "文心兰",
    "卡特兰",
    "大花蕙兰",
)
OPENING_REGION_WORDS = (
    "北京",
    "上海",
    "天津",
    "重庆",
    "河北",
    "山西",
    "辽宁",
    "吉林",
    "黑龙江",
    "江苏",
    "浙江",
    "安徽",
    "福建",
    "江西",
    "山东",
    "河南",
    "湖北",
    "湖南",
    "广东",
    "海南",
    "四川",
    "贵州",
    "云南",
    "陕西",
    "甘肃",
    "青海",
    "台湾",
    "内蒙古",
    "广西",
    "西藏",
    "宁夏",
    "新疆",
    "香港",
    "澳门",
    "杭州",
    "广州",
    "深圳",
    "成都",
    "南京",
    "苏州",
    "宁波",
)
PRODUCT_RECOMMENDATION_CONTEXT_WORDS = (
    "推荐",
    "品种",
    "哪款",
    "哪种",
    "性价比",
    "好养",
    "易活",
    "花香",
    "香味",
)
PRODUCT_PREFERENCE_WORDS = (
    "想找",
    "想要",
    "更看重",
    "好养",
    "养活",
    "易活",
    "新手",
    "性价比",
    "花香",
    "香味",
    "便宜",
)
PRODUCT_RECOMMENDATION_TARGET_WORDS = (
    "一款",
    "几款",
    "哪款",
    "哪种",
    "兰花",
    *ORCHID_VARIETY_WORDS,
    *PRODUCT_PREFERENCE_WORDS,
)
SUPPLY_SHORTAGE_WORDS = ("不够", "缺", "没有", "没准备", "需要补", "想补")


def normalize_intent_text(text: str) -> str:
    return "".join((text or "").strip().lower().split())


def hit_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def match_human_request(text: str) -> bool:
    return hit_any(text, HUMAN_WORDS) or hit_any(text, CUSTOMER_SERVICE_REQUEST_WORDS)


def match_identity_question(text: str) -> bool:
    return any(pattern.search(text) for pattern in IDENTITY_QUESTION_PATTERNS)


def match_pure_chitchat(text: str) -> tuple[str, str] | None:
    compact = re.sub(r"[^\w\u4e00-\u9fff]+", "", str(text or "").lower())
    if compact in PURE_GREETING_TEXTS:
        return "social", "greeting"
    if compact in PURE_THANKS_TEXTS or re.fullmatch(
        r"(?:好的?|好嘞|嗯嗯?|收到)?(?:谢谢|感谢)(?:你|您)?",
        compact,
    ):
        return "social", "thanks"
    if compact in PURE_END_TEXTS or re.fullmatch(
        r"(?:好的?)?(?:先这样)?(?:再见|拜拜)",
        compact,
    ):
        return "end_conversation", "end"
    return None


def match_deferred_followup(text: str) -> bool:
    return any(pattern.search(text) for pattern in DEFER_FOLLOWUP_PATTERNS)


def match_membership_product_request(text: str) -> bool:
    if "会员" not in text or hit_any(text, MEMBERSHIP_EXCLUSION_WORDS):
        return False
    return hit_any(text, MEMBERSHIP_ACTION_WORDS) or bool(
        re.search(r"\d+(?:\.\d+)?元?.{0,6}会员|会员.{0,6}\d+(?:\.\d+)?元?", text)
    )


def membership_question_kind(text: str) -> str:
    asks_capability = hit_any(
        text,
        (
            "权益",
            "福利",
            "好处",
            "包含",
            "老师",
            "一对一",
            "指导",
            "具体情况",
            "帮我看",
        ),
    )
    asks_price = hit_any(text, ("多少钱", "价格", "费用", "收费")) or bool(
        re.search(r"\d+(?:\.\d+)?元", text)
    )
    asks_purchase = hit_any(
        text,
        (
            "怎么加入",
            "如何加入",
            "怎么进",
            "如何进",
            "怎么开通",
            "如何开通",
            "购买",
            "下单",
            "付款",
            "支付",
            "链接",
            "入口",
            "发我",
            "怎么买",
            "买这个",
            "买这款",
            "就买",
            "我要买",
            "加入会员吗",
            "开通会员吗",
        ),
    ) and not hit_any(text, PURCHASE_REJECTION_WORDS)
    if asks_purchase and (asks_capability or asks_price):
        return "combined"
    if asks_purchase:
        return "purchase"
    if asks_capability:
        return "capability"
    if asks_price:
        return "price"
    return "capability"


def _reconcile_contextual_product_intent(
    intent: IntentResult,
    text: str,
    metadata: dict | None,
) -> IntentResult:
    """Keep explicit customer actions from being overwritten by inferred motives.

    The model remains responsible for semantic classification. This layer only
    resolves a current verified product reference and rejects contradictions such
    as classifying "buy this" as a price objection without objection evidence.
    """

    metadata = metadata if isinstance(metadata, dict) else {}
    normalized = normalize_intent_text(text)
    slots = dict(intent.slots)
    product_kind = str(
        slots.get("product_request_kind")
        or metadata.get("commerce_last_product_kind")
        or ""
    ).strip()
    if product_kind != "membership":
        return intent

    has_capability_question = hit_any(
        normalized,
        ("福利", "权益", "好处", "包含什么", "有什么", "提供什么", "怎么服务"),
    )
    has_price_question = hit_any(normalized, ("多少钱", "价格", "费用", "收费"))
    has_purchase_action = (
        match_product_purchase_query(normalized)
        or match_explicit_order_intent(normalized)
        or hit_any(normalized, ("买这个", "买这款", "就买", "我要买"))
    ) and not hit_any(normalized, PURCHASE_REJECTION_WORDS)
    if not (has_capability_question or has_price_question or has_purchase_action):
        return intent

    kind = membership_question_kind(normalized)
    slots.update(
        {
            "conversation_topic": "product_recommendation",
            "product_keywords": [MEMBERSHIP_PRODUCT_QUERY],
            "product_request_kind": "membership",
            "membership_question_kind": kind,
        }
    )
    if match_price_intent(normalized) != "price_objection":
        slots.pop("decision_blocker", None)

    if kind in {"purchase", "combined"}:
        if (
            intent.primary_goal == "transact"
            and intent.slots.get("membership_question_kind") == kind
            and "decision_blocker" not in intent.slots
        ):
            return intent
        issues = ["order_process"]
        if has_price_question:
            issues.append("price_value")
        return intent.model_copy(
            update={
                "route": "template_reply",
                "primary_intent": "order_intent",
                "primary_domain": "commerce",
                "primary_goal": "transact",
                "issues": issues,
                "sales_stage": "closing",
                "need_template": True,
                "need_rag": False,
                "need_human": False,
                "slots": slots,
                "reason": "contextual_explicit_action_reconciled",
            }
        )

    if (
        intent.primary_goal == "seek_help"
        and intent.slots.get("membership_question_kind") == kind
        and "decision_blocker" not in intent.slots
    ):
        return intent
    issues = ["product_selection"]
    if has_price_question:
        issues.append("price_value")
    return intent.model_copy(
        update={
            "route": "template_reply",
            "primary_intent": "product_query",
            "primary_domain": "product",
            "primary_goal": "seek_help",
            "issues": issues,
            "need_template": True,
            "need_rag": False,
            "need_human": False,
            "slots": slots,
            "reason": "contextual_product_question_reconciled",
        }
    )


def match_price_intent(text: str) -> str | None:
    if hit_any(text, PRICE_OBJECTION_WORDS) or hit_any(text, HESITATION_WORDS):
        return "price_objection"
    if hit_any(text, PRICE_ASK_WORDS):
        return "ask_price"
    return None


def match_product_purchase_query(text: str) -> bool:
    return hit_any(text, PRODUCT_LINK_QUERY_WORDS) or any(
        pattern.search(text) for pattern in PRODUCT_REFERENCE_PURCHASE_PATTERNS
    )


def match_product_recommendation_request(text: str) -> bool:
    if hit_any(text, PURCHASE_REJECTION_WORDS) or hit_any(text, UNSUPPORTED_WORDS):
        return False
    return hit_any(text, ("推荐", "想找", "想要", "哪款", "哪种")) and hit_any(
        text,
        PRODUCT_RECOMMENDATION_TARGET_WORDS,
    )


def match_orchid_supply_shortage(text: str) -> list[str]:
    if not hit_any(text, SUPPLY_SHORTAGE_WORDS):
        return []
    keywords = []
    if hit_any(text, ("花盆", "盆子", "盆")):
        keywords.append("兰花专用紫砂盆")
    if hit_any(text, ("植料", "基质")):
        keywords.append("兰花专用植料")
    return keywords


def match_explicit_order_intent(text: str) -> bool:
    return any(pattern.search(text) for pattern in EXPLICIT_ORDER_PATTERNS)


def match_order_service_action(text: str) -> str | None:
    for action, patterns in ORDER_SERVICE_ACTION_PATTERNS.items():
        if any(pattern.search(text) for pattern in patterns):
            return action
    if PURCHASED_ENTITLEMENT_PATTERN.search(text):
        return "verify_material_entitlement"
    return None


def classify_by_hard_rules(text: str) -> IntentResult | None:
    text = normalize_intent_text(text)
    if not text:
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    if match_identity_question(text):
        return _validated_intent(
            {
                "route": "chitchat",
                "primary_intent": "greeting",
                "sales_stage": "rapport",
                "confidence": 0.99,
                "slots": {"chitchat_kind": "identity_question"},
                "reason": "rule_identity_question",
            }
        )

    if hit_any(text, REFUND_WORDS):
        return _validated_intent(
            {
                "route": "human",
                "primary_intent": "refund_request",
                "sales_stage": "unknown",
                "confidence": 0.98,
                "need_human": True,
                "reason": "rule_refund",
            }
        )
    if hit_any(text, COMPLAINT_WORDS):
        return _validated_intent(
            {
                "route": "human",
                "primary_intent": "complaint",
                "sales_stage": "unknown",
                "confidence": 0.98,
                "need_human": True,
                "reason": "rule_complaint",
            }
        )
    if match_human_request(text):
        return _validated_intent(
            {
                "route": "human",
                "primary_intent": "human_request",
                "sales_stage": "unknown",
                "confidence": 0.98,
                "need_human": True,
                "reason": "rule_human_request",
            }
        )

    if match_price_intent(text) == "price_objection" and not hit_any(text, CARE_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "price_objection",
                "sales_stage": "closing",
                "confidence": 0.95,
                "need_template": True,
                "reason": "rule_explicit_price_objection",
            }
        )

    if hit_any(text, HARD_PURCHASE_REJECTION_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "purchase_rejection",
                "sales_stage": "closing",
                "confidence": 0.99,
                "need_template": True,
                "reason": "rule_purchase_rejection",
            }
        )

    if hit_any(text, SOFT_PURCHASE_DEFERRAL_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_domain": "commerce",
                "primary_goal": "defer_decision",
                "primary_intent": "hesitation",
                "sales_stage": "unknown",
                "confidence": 0.99,
                "need_template": True,
                "slots": {"rejection_kind": "polite_decline"},
                "reason": "rule_polite_purchase_deferral",
            }
        )

    if match_deferred_followup(text):
        return _validated_intent(
            {
                "primary_domain": "conversation",
                "primary_goal": "end_conversation",
                "confidence": 0.99,
                "slots": {"chitchat_kind": "defer"},
                "reason": "rule_deferred_followup",
            }
        )

    if match_product_recommendation_request(text):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_domain": "product",
                "primary_goal": "seek_help",
                "issues": ["product_selection"],
                "sales_stage": "need_discovery",
                "confidence": 0.99,
                "need_template": True,
                "slots": {"conversation_topic": "product_recommendation"},
                "reason": "rule_product_recommendation_request",
            }
        )

    if match_orchid_supply_shortage(text):
        return _validated_intent(
            {
                "route": "rag_answer",
                "primary_domain": "care",
                "primary_goal": "seek_help",
                "issues": ["medium_repotting"],
                "sales_stage": "pain_discovery",
                "confidence": 0.99,
                "need_rag": True,
                "reason": "rule_orchid_supply_care_need",
            }
        )

    order_action = match_order_service_action(text)
    if order_action:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_query",
                "sales_stage": "unknown",
                "confidence": 0.99,
                "need_template": True,
                "slots": {"order_action": order_action},
                "reason": "rule_order_service_action",
            }
        )

    if is_orchid_material_request(text):
        return _validated_intent(
            {
                "primary_domain": "care",
                "primary_goal": "request_material",
                "issues": ["material_resource"],
                "confidence": 0.99,
                "slots": {
                    "material_type": "orchid_care",
                    "resource_type": "orchid_material",
                },
                "reason": "rule_material_request",
            }
        )

    if hit_any(text, PRODUCT_IMAGE_QUERY_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "product_query",
                "sales_stage": "closing",
                "confidence": 0.99,
                "need_template": True,
                "reason": "rule_product_image_request",
            }
        )

    chitchat = match_pure_chitchat(text)
    if chitchat is not None:
        goal, kind = chitchat
        return _validated_intent(
            {
                "primary_domain": "conversation",
                "primary_goal": goal,
                "confidence": 0.99,
                "slots": {"chitchat_kind": kind},
                "reason": "rule_pure_chitchat",
            }
        )

    if match_product_purchase_query(text):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_intent",
                "sales_stage": "closing",
                "confidence": 0.99,
                "need_template": True,
                "slots": {"purchase_entry_requested": True},
                "reason": "rule_product_purchase_query",
            }
        )

    if match_explicit_order_intent(text):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_intent",
                "sales_stage": "closing",
                "confidence": 0.99,
                "need_template": True,
                "reason": "rule_explicit_order_intent",
            }
        )

    if hit_any(text, UNSUPPORTED_WORDS):
        return _validated_intent(
            {
                "route": "unsupported",
                "primary_intent": "unsupported",
                "confidence": 0.88,
                "reason": "rule_unsupported",
            }
        )
    return None


def classify_by_soft_rules(text: str) -> IntentResult | None:
    text = normalize_intent_text(text)
    if not text:
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    price_intent = match_price_intent(text)
    has_price = price_intent is not None
    has_care = hit_any(text, CARE_WORDS)
    has_knowledge = hit_any(text, KNOWLEDGE_PATTERNS) or "知识" in text or "资料" in text
    if hit_any(text, ORDER_QUERY_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_query",
                "sales_stage": "unknown",
                "confidence": 0.9,
                "need_template": True,
                "reason": "soft_rule_order_query",
            }
        )
    if hit_any(text, PRODUCT_QUERY_WORDS) and not has_care:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "product_query",
                "sales_stage": "closing",
                "confidence": 0.86,
                "need_template": True,
                "reason": "soft_rule_product_query",
            }
        )
    if hit_any(text, HARD_PURCHASE_REJECTION_WORDS) and not has_price:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "purchase_rejection",
                "sales_stage": "closing",
                "confidence": 0.92,
                "need_template": True,
                "reason": "soft_rule_purchase_rejection",
            }
        )
    if hit_any(text, SOFT_PURCHASE_DEFERRAL_WORDS) and not has_price:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_domain": "commerce",
                "primary_goal": "defer_decision",
                "primary_intent": "hesitation",
                "sales_stage": "unknown",
                "confidence": 0.92,
                "need_template": True,
                "slots": {"rejection_kind": "polite_decline"},
                "reason": "soft_rule_polite_purchase_deferral",
            }
        )
    if hit_any(text, SHIPPING_DAMAGE_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_after_sale",
                "sales_stage": "unknown",
                "confidence": 0.92,
                "need_template": True,
                "reason": "soft_rule_shipping_damage",
            }
        )
    if hit_any(text, ORDER_INFO_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_intent",
                "sales_stage": "closing",
                "confidence": 0.9,
                "need_template": True,
                "slots": {"conversation_topic": "order_information"},
                "reason": "soft_rule_order_information",
            }
        )
    if has_price and has_care:
        return _validated_intent(
            {
                "route": "template_then_rag",
                "primary_intent": "price_objection",
                "secondary_intents": ["care_question"],
                "sales_stage": "closing",
                "confidence": 0.78,
                "need_template": True,
                "need_rag": True,
                "reason": "soft_rule_mixed_price_care",
            }
        )
    if has_price:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": price_intent,
                "sales_stage": "closing" if price_intent == "price_objection" else "need_discovery",
                "confidence": 0.76,
                "need_template": True,
                "reason": "soft_rule_price",
            }
        )
    if hit_any(text, LOGISTICS_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_logistics",
                "sales_stage": "need_discovery",
                "confidence": 0.74,
                "need_template": True,
                "reason": "soft_rule_logistics",
            }
        )
    if hit_any(text, ORDER_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "payment_intent" if hit_any(text, ("付款", "支付")) else "order_intent",
                "sales_stage": "closing",
                "confidence": 0.74,
                "need_template": True,
                "reason": "soft_rule_order",
            }
        )
    if has_care:
        return _validated_intent(
            {
                "route": "rag_answer",
                "primary_intent": "care_question",
                "sales_stage": "pain_discovery",
                "confidence": 0.75,
                "need_rag": True,
                "reason": "soft_rule_care",
            }
        )
    if has_knowledge:
        return _validated_intent(
            {
                "route": "rag_answer",
                "primary_intent": _knowledge_primary_intent(text),
                "sales_stage": "pain_discovery",
                "confidence": 0.72,
                "need_rag": True,
                "reason": "soft_rule_knowledge",
            }
        )
    if hit_any(text, AFTER_SALE_WORDS):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "ask_after_sale",
                "sales_stage": "unknown",
                "confidence": 0.72,
                "need_template": True,
                "reason": "soft_rule_after_sale",
            }
        )
    if hit_any(text, GREETING_WORDS):
        chitchat_kind = (
            "thanks" if hit_any(text, ("谢谢", "感谢")) else "greeting"
        )
        return _validated_intent(
            {
                "route": "chitchat",
                "primary_intent": "greeting",
                "sales_stage": "rapport",
                "confidence": 0.76,
                "slots": {"chitchat_kind": chitchat_kind},
                "reason": "soft_rule_greeting",
            }
        )
    return _validated_intent(
        {
            "route": "clarify",
            "primary_intent": "unknown",
            "confidence": 0.45,
            "reason": "soft_rule_no_match",
        }
    )


def classify_by_rules(text: str) -> IntentResult | None:
    return classify_by_hard_rules(text) or classify_by_soft_rules(text)


def classify_by_fast_rule(text: str) -> IntentResult | None:
    settings = get_settings()
    if not settings.intent_fast_rules_enabled:
        return None
    hard_intent = classify_by_hard_rules(text)
    if (
        hard_intent is not None
        and hard_intent.reason in SEMANTIC_COMMERCE_FALLBACK_REASONS
    ):
        return None
    if hard_intent is not None and hard_intent.reason in {
        "rule_material_request",
        "rule_product_image_request",
        "rule_product_recommendation_request",
        "rule_orchid_supply_shortage",
        "rule_order_service_action",
        "rule_explicit_price_objection",
    }:
        return _with_decision_blocker(hard_intent, text)
    intent = classify_by_soft_rules(text)
    if intent.confidence < settings.intent_fast_rule_threshold:
        return None
    return _with_decision_blocker(intent, text)


def classify_material_followup(
    text: str,
    recent_turns: list[dict] | None,
) -> IntentResult | None:
    if not is_orchid_material_followup(text, recent_turns):
        return None
    return _validated_intent(
        {
            "primary_domain": "care",
            "primary_goal": "request_material",
            "issues": ["material_resource"],
            "confidence": 0.99,
            "slots": {
                "material_type": "orchid_care",
                "resource_type": "orchid_material",
            },
            "reason": "contextual_material_followup",
        }
    )


async def classify_by_llm(
    message: NormalizedMessage,
    user_state: UserState,
    candidates: list[dict] | None = None,
    *,
    model_override: str | None = None,
    provider_override: str | None = None,
    shadow: bool = False,
) -> IntentResult:
    from app.integrations.ai.services import llm_service

    settings = get_settings()
    raw = await llm_service.classify_intent(
        _build_prompt(
            message.message,
            recent_turns=user_state.metadata.get("recent_turns", []),
            candidates=candidates,
        ),
        model_override=model_override,
        provider_override=provider_override,
        shadow=shadow,
        prompt_version=settings.intent_prompt_version,
    )
    model_config = llm_service.get_model_config("intent")
    provider = provider_override or model_config.provider
    model = model_override or model_config.model
    enriched = {
        **raw,
        "classifier_source": "llm_shadow" if shadow else "llm",
        "classifier_provider": provider,
        "classifier_model": model,
    }
    return _validated_intent(enriched, raw_prediction=raw)


async def classify_intent(
    message: NormalizedMessage,
    user_state: UserState,
    candidates: list[dict] | None = None,
) -> IntentResult:
    hard_intent = classify_by_hard_rules(message.message)
    semantic_commerce_fallback = None
    if (
        hard_intent is not None
        and hard_intent.reason in SEMANTIC_COMMERCE_FALLBACK_REASONS
    ):
        semantic_commerce_fallback = hard_intent
    elif hard_intent is not None:
        return _with_decision_blocker(hard_intent, message.message)

    normalized = normalize_intent_text(message.message)
    if (
        user_state.metadata.get("commerce_pending") == "order_mobile"
        and re.search(r"(?<!\d)1[3-9]\d{9}(?!\d)", normalized)
    ):
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_query",
                "sales_stage": "unknown",
                "confidence": 0.99,
                "need_template": True,
                "reason": "pending_order_mobile",
            }
        )

    shipping_contact = extract_shipping_contact(message.message)
    if shipping_contact:
        return _validated_intent(
            {
                "route": "template_reply",
                "primary_intent": "order_intent",
                "sales_stage": "closing",
                "confidence": 0.98,
                "need_template": True,
                "slots": {"shipping_contact": shipping_contact},
                "reason": "structured_shipping_contact",
            }
        )

    selected_product_followup = classify_selected_product_followup(
        message.message,
        user_state.metadata,
    )
    if selected_product_followup is not None:
        return selected_product_followup

    opening_followup = classify_opening_followup(
        message.message,
        user_state.metadata.get("recent_turns", []),
    )
    if opening_followup is not None:
        return opening_followup

    recommendation_followup = classify_product_recommendation_followup(
        message.message,
        user_state.metadata.get("recent_turns", []),
    )
    if recommendation_followup is not None:
        return recommendation_followup

    settings = get_settings()
    llm_enabled = bool(getattr(settings, "intent_llm_enabled", False))
    rule_intent = classify_by_soft_rules(message.message)
    if (
        semantic_commerce_fallback is None
        and settings.intent_fast_rules_enabled
        and rule_intent.confidence >= settings.intent_fast_rule_threshold
    ):
        return _with_decision_blocker(rule_intent, message.message)
    if llm_enabled:
        try:
            llm_intent = await classify_by_llm(message, user_state, candidates)
            llm_intent = _reconcile_contextual_product_intent(
                llm_intent,
                message.message,
                user_state.metadata,
            )
            if (
                llm_intent.primary_goal != "unclear"
                or llm_intent.slots.get("product_request_kind") == "membership"
            ):
                return _with_decision_blocker(llm_intent, message.message)
        except AppError:
            pass

    if match_membership_product_request(normalized):
        return _validated_intent(
            {
                "primary_domain": "product",
                "primary_goal": "seek_help",
                "issues": ["product_selection"],
                "sales_stage": "closing",
                "confidence": 0.8,
                "slots": {
                    "conversation_topic": "product_recommendation",
                    "product_keywords": [MEMBERSHIP_PRODUCT_QUERY],
                    "product_request_kind": "membership",
                    "membership_question_kind": membership_question_kind(normalized),
                },
                "reason": "fallback_membership_product_request",
            }
        )

    if semantic_commerce_fallback is not None:
        return _with_decision_blocker(semantic_commerce_fallback, message.message)

    if candidates and rule_intent.route == candidates[0].get("route"):
        rule_intent = rule_intent.model_copy(
            update={"confidence": min(rule_intent.confidence + 0.05, 1.0)}
        )
    return _with_decision_blocker(rule_intent, message.message)


def classify_selected_product_followup(
    text: str,
    metadata: dict | None,
) -> IntentResult | None:
    metadata = metadata if isinstance(metadata, dict) else {}
    product_keyword = str(
        metadata.get("commerce_last_product_keyword") or ""
    ).strip()
    normalized = normalize_intent_text(text)
    if not product_keyword or not PRICE_ONLY_FOLLOWUP_PATTERN.fullmatch(normalized):
        return None
    product_kind = str(metadata.get("commerce_last_product_kind") or "").strip()
    slots = {
        "conversation_topic": "product_recommendation",
        "product_keywords": [product_keyword],
    }
    if product_kind:
        slots["product_request_kind"] = product_kind
    return _validated_intent(
        {
            "primary_domain": "product",
            "primary_goal": "seek_help",
            "issues": ["product_selection", "price_value"],
            "sales_stage": "closing",
            "confidence": 0.98,
            "slots": slots,
            "reason": "contextual_selected_product_price",
        }
    )


def schedule_intent_shadow_evaluation(
    *,
    message: NormalizedMessage,
    user_state: UserState,
    primary: IntentResult,
    candidates: list[dict] | None = None,
) -> None:
    settings = get_settings()
    if not shadow_selected(message.trace_id):
        return
    recent_turns = list(user_state.metadata.get("recent_turns", []))

    async def run() -> None:
        shadow_result = None
        error_class = None
        try:
            shadow_candidates = candidates
            if not shadow_candidates:
                from app.domains.decisioning.services.intent_example_service import (
                    retrieve_intent_examples,
                )

                shadow_candidates = await retrieve_intent_examples(
                    message.message,
                    top_k=settings.intent_example_top_k,
                )
            shadow_state = user_state.model_copy(
                update={"metadata": {**user_state.metadata, "recent_turns": recent_turns}}
            )
            shadow_result = await classify_by_llm(
                message,
                shadow_state,
                shadow_candidates,
                model_override=settings.intent_shadow_llm_model,
                provider_override=settings.intent_shadow_llm_provider,
                shadow=True,
            )
        except Exception as exc:  # noqa: BLE001
            error_class = type(exc).__name__
        record_intent_shadow(
            trace_id=message.trace_id,
            primary=primary,
            shadow_provider=settings.intent_shadow_llm_provider,
            shadow_model=settings.intent_shadow_llm_model,
            shadow=shadow_result,
            error_class=error_class,
        )

    schedule_intent_shadow(run())


def _with_decision_blocker(intent: IntentResult, text: str) -> IntentResult:
    normalized = normalize_intent_text(text)
    blocker = None
    explicit_price_objection = match_price_intent(normalized) == "price_objection"
    if explicit_price_objection:
        blocker = {"type": "price", "detail": "客户认为价格偏高"}
    elif hit_any(normalized, ("一直问", "反复问", "别再问", "直接看商品", "直接告诉")):
        blocker = {
            "type": "other",
            "detail": "客户不愿继续回答重复问题，希望直接查看商品",
        }
    else:
        candidate = intent.slots.get("decision_blocker")
        if (
            isinstance(candidate, dict)
            and candidate.get("type")
            in {
                "price",
                "trust",
                "care_risk",
                "product_fit",
                "choice",
                "timing",
                "other",
            }
            and _decision_blocker_is_grounded(str(candidate.get("type")), normalized)
        ):
            blocker = {
                "type": candidate["type"],
                "detail": str(candidate.get("detail") or "").strip(),
            }
    if blocker is None:
        if isinstance(intent.slots.get("decision_blocker"), dict):
            slots = dict(intent.slots)
            slots.pop("decision_blocker", None)
            return intent.model_copy(update={"slots": slots})
        return intent
    return intent.model_copy(
        update={"slots": {**intent.slots, "decision_blocker": blocker}}
    )


def _knowledge_primary_intent(text: str) -> str:
    if hit_any(text, ("养护", "养不活", "不会养", "浇水", "施肥", "护理", "怕养死", "怕养不好")):
        return "care_question"
    if hit_any(text, ("流程", "步骤", "怎么申请", "怎么处理")):
        return "process_question"
    if hit_any(text, ("怎么使用", "如何使用", "使用方法")):
        return "usage_question"
    return "knowledge_question"


def _validated_intent(
    raw: dict,
    *,
    raw_prediction: dict | None = None,
) -> IntentResult:
    raw = dict(raw)
    raw.setdefault("classifier_source", _classifier_source(raw.get("reason")))
    if raw_prediction is not None:
        raw["raw_prediction"] = raw_prediction
    raw = prepare_intent_payload(raw)
    try:
        intent = IntentResult.model_validate(raw)
    except ValidationError as exc:
        raise AppError(ErrorCode.INTENT_SCHEMA_INVALID) from exc
    primary_intent = normalize_system_value(
        "intent", intent.primary_intent, fallback="unknown"
    )
    secondary_intents = []
    for value in intent.secondary_intents:
        normalized = normalize_system_value("intent", value, fallback="")
        if normalized and normalized != primary_intent and normalized not in secondary_intents:
            secondary_intents.append(normalized)
    sentiment = (
        normalize_system_value(
            "customer_sentiment", intent.customer_sentiment, fallback="neutral"
        )
        if intent.customer_sentiment
        else None
    )
    sales_signals = []
    for value in intent.sales_signals:
        try:
            signal = CustomerSignal(value)
        except ValueError:
            continue
        if signal is not CustomerSignal.PURCHASED and signal.value not in sales_signals:
            sales_signals.append(signal.value)
    return intent.model_copy(
        update={
            "primary_intent": primary_intent,
            "secondary_intents": secondary_intents,
            "sales_stage": normalize_system_value(
                "sales_stage", intent.sales_stage, fallback="unknown"
            ),
            "sales_signals": sales_signals,
            "customer_sentiment": sentiment,
        }
    )


def classify_opening_followup(
    text: str, recent_turns: list[dict] | None
) -> IntentResult | None:
    recent_turns = recent_turns if isinstance(recent_turns, list) else []
    asked_for_profile = any(
        isinstance(turn, dict)
        and turn.get("role") == "assistant"
        and "多少盆" in str(turn.get("content") or "")
        and "品种" in str(turn.get("content") or "")
        for turn in recent_turns[-6:]
    )
    if not asked_for_profile:
        return None
    normalized = normalize_intent_text(text)
    if hit_any(normalized, (*CARE_INCIDENT_WORDS, *CARE_FAILURE_HISTORY_WORDS)):
        return None
    if not (
        PLANT_COUNT_PATTERN.search(normalized)
        or _opening_varieties(normalized)
    ):
        return None
    slots = _opening_profile_slots(normalized)
    return _validated_intent(
        {
            "route": "chitchat",
            "primary_intent": "profile_answer",
            "sales_stage": "need_discovery",
            "confidence": 0.98,
            "slots": slots,
            "reason": "opening_profile_answer",
        }
    )


def _decision_blocker_is_grounded(blocker_type: str, text: str) -> bool:
    markers = {
        "price": (*PRICE_OBJECTION_WORDS, *HESITATION_WORDS),
        "trust": ("真假", "靠谱吗", "可靠", "被骗", "保障", "对版", "信不过"),
        "care_risk": ("养不活", "养死", "不会养", "怕养不好", "总是死"),
        "product_fit": ("适合我", "不适合", "合不合适", "不匹配"),
        "choice": ("哪个好", "怎么选", "选不出", "纠结"),
        "timing": ("晚点", "以后再说", "暂时", "再考虑", "再想想"),
        "other": ("一直问", "反复问", "别再问", "直接告诉"),
    }
    return hit_any(text, tuple(markers.get(blocker_type, ())))


def _opening_profile_slots(text: str) -> dict:
    slots: dict[str, object] = {}
    varieties = _opening_varieties(text)
    if varieties:
        slots["owned_varieties"] = varieties
    count_match = re.search(
        r"([零一二两三四五六七八九十百\d]{1,5})(?:来|多|左右)?\s*(?:盆|棵|株)",
        text,
    )
    if count_match:
        plant_count = _parse_plant_count(count_match.group(1))
        if plant_count is not None:
            slots["plant_count"] = plant_count
    known_regions = [region for region in OPENING_REGION_WORDS if region in text]
    known_region = max(
        known_regions,
        key=lambda region: (text.rfind(region), len(region)),
        default="",
    )
    informal_region_match = re.search(
        r"^(?:我在|人在|坐标)?([\u4e00-\u9fff]{2,6}?)(?:这边|这儿)"
        r"(?:的|，|,|。|\s|$)",
        text,
    )
    informal_region = (
        informal_region_match.group(1) if informal_region_match else ""
    )
    if informal_region in {"阳台", "家里", "室内", "户外", "窗边", "楼顶", "院子"}:
        informal_region = ""
    region_match = re.search(
        r"(?:我在|来自|地区(?:是|在))"
        r"([\u4e00-\u9fff]{2,10}?)(?:的|，|,|。|\s|$)",
        text,
    ) or re.search(
        r"我是((?:北京|天津|上海|重庆|河北|河南|云南|辽宁|黑龙江|湖南|"
        r"安徽|山东|新疆|江苏|浙江|江西|湖北|广西|甘肃|山西|内蒙古|"
        r"陕西|吉林|福建|贵州|广东|青海|西藏|四川|宁夏|海南|台湾|"
        r"香港|澳门)[\u4e00-\u9fff]{0,6}?)(?:的|人)(?:，|,|。|\s|$)",
        text,
    ) or re.search(
        r"我是([\u4e00-\u9fff]{2,8}?人)(?:，|,|。|\s|$)",
        text,
    )
    if region_match:
        slots["region"] = re.sub(r"(?:这边|这儿)$", "", region_match.group(1))
    elif informal_region:
        slots["region"] = informal_region
    elif known_region:
        slots["region"] = known_region
    return slots


def _opening_varieties(text: str) -> list[str]:
    varieties = [variety for variety in ORCHID_VARIETY_WORDS if variety in text]
    direct_match = re.fullmatch(
        r"(?:我)?(?:养的)?(?:全是|主要是|是)?([\u4e00-\u9fff]{2,8}?兰)",
        text,
    )
    if direct_match:
        candidate = direct_match.group(1)
        if (
            candidate not in {"养兰", "兰花", "国兰"}
            and not candidate.endswith("养兰")
            and not any(variety in candidate for variety in varieties)
        ):
            varieties.append(candidate)
    for match in re.finditer(
        r"[，,、]\s*(?:我)?(?:养的)?(?:全是|主要是|是)?"
        r"([\u4e00-\u9fff]{2,8}?兰)(?=$|[，,。.!！\s])",
        text,
    ):
        candidate = match.group(1)
        if any(variety in candidate for variety in varieties):
            continue
        if candidate not in {"养兰", "兰花", "国兰"} and candidate not in varieties:
            varieties.append(candidate)
    return varieties


def _parse_plant_count(value: str) -> int | None:
    if value.isdigit():
        return int(value)
    digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if value == "十":
        return 10
    if "百" in value:
        hundreds, remainder = value.split("百", 1)
        base = digits.get(hundreds, 1) * 100
        tail = _parse_plant_count(remainder) if remainder else 0
        return base + tail if tail is not None else None
    if "十" in value:
        tens, ones = value.split("十", 1)
        return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    if all(char in digits for char in value):
        return int("".join(str(digits[char]) for char in value))
    return None


def classify_product_recommendation_followup(
    text: str, recent_turns: list[dict] | None
) -> IntentResult | None:
    recent_turns = recent_turns if isinstance(recent_turns, list) else []
    normalized = normalize_intent_text(text)
    if not hit_any(normalized, PRODUCT_PREFERENCE_WORDS):
        return None
    has_recommendation_context = any(
        isinstance(turn, dict)
        and turn.get("role") == "assistant"
        and hit_any(
            normalize_intent_text(str(turn.get("content") or "")),
            PRODUCT_RECOMMENDATION_CONTEXT_WORDS,
        )
        for turn in recent_turns[-4:]
    )
    if not has_recommendation_context:
        return None
    return _validated_intent(
        {
            "primary_domain": "product",
            "primary_goal": "seek_help",
            "issues": ["product_selection"],
            "sales_stage": "need_discovery",
            "confidence": 0.9,
            "slots": {"conversation_topic": "product_recommendation"},
            "reason": "contextual_product_preference",
        }
    )


def _build_legacy_prompt(message: str, recent_turns: list[dict] | None = None) -> str:
    intent_values = " | ".join(system_tag_values("intent")) or "unknown"
    stage_values = " | ".join(system_tag_values("sales_stage")) or "unknown"
    signal_values = " | ".join(
        signal.value for signal in CustomerSignal if signal is not CustomerSignal.PURCHASED
    )
    sentiment_values = (
        " | ".join(system_tag_values("customer_sentiment")) or "neutral"
    )
    recent_lines = []
    for turn in (recent_turns or [])[-6:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            recent_lines.append(f"{role}: {content[:500]}")
    recent_context = (
        "\n## 最近对话\n\n" + "\n".join(recent_lines) + "\n"
        if recent_lines
        else ""
    )
    return f"""# 角色

你只负责做兰花私域客服消息的意图识别。

# 任务

读取【用户消息】，判断用户的真实意图，并只输出一个 JSON 对象。

不要生成客服回复。  
不要输出 Markdown。  
不要输出解释文字。  
不要输出代码块。  
不要在 JSON 前后添加任何内容。

## 用户消息

{message}
{recent_context}

# 必须输出的 JSON 字段

{{
  "route": "template_reply | rag_answer | template_then_rag | clarify | human | chitchat | unsupported",
  "primary_intent": "{intent_values}",
  "secondary_intents": [],
  "sales_signals": [],
  "slots": {{}},
  "sales_stage": "{stage_values}",
  "customer_sentiment": "{sentiment_values}",
  "confidence": 0.0,
  "need_template": false,
  "need_rag": false,
  "need_human": false,
  "reason": "简短说明"
}}

# 字段说明

1. `route`：后续处理路径。
2. `primary_intent`：用户最主要的意图，只能选择一个。
3. `secondary_intents`：用户同时表达的次要意图，没有则输出空数组。
4. `sales_signals`：只输出明确观察到的候选信号，可选值为 `{signal_values}`。客户口述付款只能输出 `payment_claimed`，禁止输出 `purchased`。
5. `sales_stage`：兼容字段，只输出候选阶段，最终阶段由后续状态机决定。
6. `customer_sentiment`：只能从标签管理中的客户情绪分类选择。
7. `confidence`：判断置信度，范围为 `0.00` 到 `1.00`。
8. `need_template`：是否需要调用固定话术模板。
9. `need_rag`：是否需要调用兰花知识资料回答。
10. `need_human`：是否需要转人工。
11. `reason`：用一句简短中文说明分类原因，不超过 20 个字。

`slots.decision_blocker` 格式为 {{"type": "price | trust | care_risk | product_fit | choice | timing | other", "detail": ""}}。
只记录客户明确表达的成交阻碍；没有明确阻碍时不要输出该槽位。
detail 使用中性中文概括，不复述辱骂或攻击性原话；售后问题本身不算成交阻碍。

# route 判定规则

## 1. template_reply

适用于明确的销售、交易、订单、物流、售后政策类问题。

包括：询价、优惠、议价、下单、付款、物流、发货、售后政策等。

字段要求：

- `need_template = true`
- `need_rag = false`
- `need_human = false`

## 2. rag_answer

适用于兰花知识和养护咨询。

包括：浇水、施肥、光照、通风、植料、换盆、修根、服盆、催花、不开花、黄叶、烂根、病虫害、地区养护差异等。

字段要求：

- `need_template = false`
- `need_rag = true`
- `need_human = false`

## 3. template_then_rag

适用于用户同时表达成交犹豫和养护顾虑。

例如：怕养不好、先看看、再考虑、担心不会养，同时又涉及购买决策。

字段要求：

- `need_template = true`
- `need_rag = true`
- `need_human = false`

## 4. clarify

适用于用户表达不完整、指代不明、无法判断真实意图。

字段要求：

- `primary_intent = unknown`
- `confidence < 0.60`

## 5. human

适用于必须人工处理的高风险或强诉求场景。

包括：明确要求人工、退款、投诉、赔付、补发、订单异常、严重售后纠纷、人身攻击、高风险售后异常等。

字段要求：

- `need_human = true`

## 6. chitchat

适用于问候、感谢、简单寒暄。

例如：你好、在吗、谢谢、好的、辛苦了。

## 7. unsupported

适用于与兰花、订单、客服服务无关，且无法通过普通寒暄处理的内容。

# primary_intent 判定规则

## greeting

问候、寒暄、感谢、确认收到。  
例如：你好、在吗、谢谢、好的。

## ask_price

明确询问价格、多少钱、报价、怎么卖。  
例如：这个多少钱、价格多少、怎么卖。

## price_objection

明确表达价格贵、预算犹豫或价格异议。  
例如：太贵了、有点贵、我再考虑一下。

注意：“名贵兰花”里的“贵”不是价格异议。

## discount_request

明确要求优惠、便宜点、打折、包邮、少一点。  
例如：能优惠吗、便宜点、可以包邮吗。

## ask_logistics

询问发货、快递、物流、到货时间、运费。  
例如：什么时候发货、发什么快递、几天到。

## ask_after_sale

询问售后政策、保障、养死是否处理、售后怎么负责。  
例如：养死包赔吗、有售后吗、收到坏了怎么办。

## order_intent

明确表达想买、下单、要一盆、怎么拍。  
例如：我要了、怎么下单、给我留一盆。

## payment_intent

询问付款方式、付款链接、转账、支付问题。  
例如：怎么付款、发我付款码、可以微信支付吗。

## care_question

具体兰花养护操作问题。

包括：浇水、施肥、换盆、修根、植料、光照、通风、温湿度、服盆、催花等。

## knowledge_question

兰花知识类问题，但不一定是具体操作。

包括：品种、花期、习性、香味、名贵程度、真假鉴别等。

## process_question

询问操作流程或处理步骤。  
例如：收到后怎么处理、上盆流程是什么。

## usage_question

询问某个养护用品、工具、药剂、植料的使用方式。  
例如：这个植料怎么用、杀菌剂怎么用。

涉及具体药剂搭配或剂量不确定时，可转 `human`。

## refund_request

明确要求退款、退货、退钱。

必须：

- `route = human`
- `need_human = true`

## complaint

明确投诉、强烈不满、责怪商家、要求赔付。

必须：

- `route = human`
- `need_human = true`

## human_request

明确要求人工、客服、老板、售后人员介入。

必须：

- `route = human`
- `need_human = true`

## unsupported

与兰花、销售、订单、售后无关的问题。

## unknown

信息不足，无法判断意图。

# sales_stage 判定规则

`sales_stage` 只描述首单七阶段。售后、退款、投诉和人工介入不是销售阶段，
此类消息输出 `sales_stage = unknown`，由后续服务记录中断并保留当前销售阶段。

## rapport

用户处于问候或寒暄阶段。

## need_discovery

正在围绕养兰困难挖掘客户的具体需求和痛点。首单默认优先理解养护服务需求，
只有客户明确表达购兰、选品或品种偏好时才判断为产品需求。

## pain_discovery

客户已经表达黑斑、黄叶、腐苗、烂根、不开花等具体养兰困难，或明确了其他核心痛点。

## solution_recommended

已有足够依据，可以推荐产品或服务方案。

## value_built

客户正在了解或认可苗质、服务和适配价值。

## trial_close

客户开始确认价格、规格、数量或购买方案。

## closing

客户有明确下单意向或提出需要解决的成交阻碍。

## unknown

无法判断阶段。

# 分类优先级

按以下优先级从高到低判断：

1. 明确退款、投诉、赔付、补发、强烈售后纠纷、明确转人工、人身攻击、高风险异常  
   → `route = human`

2. 明确价格、优惠、下单、付款、物流、售后政策  
   → `route = template_reply`

3. 兰花养护知识、病虫害、浇水施肥、换盆修根、植料、光照通风、地区环境  
   → `route = rag_answer`

4. 同时包含成交犹豫和养护顾虑  
   → `route = template_then_rag`

5. 问候、感谢、简单寒暄  
   → `route = chitchat`

6. 与兰花及服务无关  
   → `route = unsupported`

7. 仍不确定  
   → `route = clarify`

# 重要边界

1. “浇水需要多少天”“多久浇水”“浇多少水”“多少天浇一次”属于养护问题，不是价格问题。
2. 只有明确问价格、多少钱、报价、怎么卖，才是 `ask_price`。
3. 只有明确说太贵、有点贵、再考虑、预算不够，才是 `price_objection`。
6. “客服指导养护”不是转人工，属于养护咨询。
7. “售后怎么养护”如果只是问养护方法。
8. 用户说“怕养不好”“不会养”，如果没有购买犹豫语境，优先归为 `care_question`；如果同时出现“再考虑”“不敢买”“先不买”，归为 `template_then_rag`。
9. 病虫害、烂根、严重黄叶等如果只是咨询养护，`route = rag_answer`；如果要求赔付、退换、投诉，`route = human`。
10. 用户消息同时包含多个意图时，`primary_intent` 选择最需要优先处理的意图，其余放入 `secondary_intents`。

# confidence 规则

## 0.90 - 1.00

用户表达直接命中单一意图。  
例如：“多少钱”“我要退款”“帮我转人工”。

## 0.75 - 0.89

语义明确，但可能需要少量上下文。  
例如：“多久浇水”“收到后怎么养”。

## 0.60 - 0.74

可能包含两个意图，但主意图基本可判断。  
例如：“这个贵吗，我怕养不好”。

## 0.00 - 0.59

表达不完整、指代不明、缺少关键信息，需要追问。

字段要求：

- `route = clarify`
- `primary_intent = unknown`

# 输出格式要求

1. 只能输出一个合法 JSON 对象。
2. JSON 必须包含所有字段。
3. 字段名必须与要求完全一致。
4. 字段值必须使用规定枚举值。
5. `secondary_intents` 必须是数组。
6. `confidence` 必须是数字，不要写成字符串。
7. `need_template`、`need_rag`、`need_human` 必须是布尔值。
8. `reason` 必须简短，不超过 20 个字。
9. 不要输出 Markdown、代码块、注释或额外解释。

# 示例

## 示例 1

用户消息：老师，下一次浇水需要多少天？

输出：

{{
  "route": "rag_answer",
  "primary_intent": "care_question",
  "secondary_intents": [],
  "sales_stage": "pain_discovery",
  "confidence": 0.86,
  "need_template": false,
  "need_rag": true,
  "need_human": false,
  "reason": "询问浇水频率"
}}

## 示例 2

用户消息：这个多少钱？

输出：

{{
  "route": "template_reply",
  "primary_intent": "ask_price",
  "secondary_intents": [],
  "sales_stage": "need_discovery",
  "confidence": 0.92,
  "need_template": true,
  "need_rag": false,
  "need_human": false,
  "reason": "明确询价"
}}

## 示例 3

用户消息：我再考虑一下，怕养不好

输出：

{{
  "route": "template_then_rag",
  "primary_intent": "price_objection",
  "secondary_intents": ["care_question"],
  "sales_stage": "closing",
  "confidence": 0.82,
  "need_template": true,
  "need_rag": true,
  "need_human": false,
  "reason": "犹豫且担心养护"
}}

## 示例 4

用户消息：我要退款

输出：

{{
  "route": "human",
  "primary_intent": "refund_request",
  "secondary_intents": [],
  "sales_stage": "unknown",
  "confidence": 0.95,
  "need_template": false,
  "need_rag": false,
  "need_human": true,
  "reason": "明确要求退款"
}}"""


def _build_prompt_legacy(
    message: str,
    recent_turns: list[dict] | None = None,
    candidates: list[dict] | None = None,
) -> str:
    domain_values = " | ".join(taxonomy_values("domain"))
    goal_values = " | ".join(taxonomy_values("goal"))
    issue_values = " | ".join(taxonomy_values("issue"))
    legacy_intent_values = " | ".join(system_tag_values("intent")) or "unknown"
    signal_values = " | ".join(
        signal.value
        for signal in CustomerSignal
        if signal is not CustomerSignal.PURCHASED
    )
    recent_lines = []
    for turn in (recent_turns or [])[-6:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            recent_lines.append(f"{role}: {content[:500]}")
    recent_context = "\n最近对话：\n" + "\n".join(recent_lines) if recent_lines else ""
    candidate_text = format_candidate_cards(candidates)
    return f"""你是兰花私域销售客服的意图结构化分类器，只分类，不回复客户。

当前消息：{message}
{recent_context}

分类采用三个彼此独立的维度：
- Domain：客户在谈哪一类业务对象。
- Goal：客户这一轮想完成什么动作。
- Issue：涉及哪些具体议题；没有明确议题可以为空，禁止猜测。

只输出一个 JSON 对象，字段必须完整：
{{
  "primary_domain": "{domain_values}",
  "secondary_domains": [],
  "primary_goal": "{goal_values}",
  "secondary_goals": [],
  "issues": [],
  "scope": "in_scope | ambiguous | out_of_scope",
  "evidence": [{{"text": "消息中的原文片段", "dimension": "domain | goal | issue", "label": "对应标签"}}],
  "sales_signals": [],
  "slots": {{}},
  "customer_sentiment": "neutral",
  "confidence": 0.0,
  "reason": "不超过20字"
}}

约束：
1. 每个维度只能使用候选卡中的标签；主标签一个，确有并列意图才给 secondary。
   Issue 允许值：{issue_values}。
2. 优先识别明确动作，再结合最近对话消解“这个、那款、发我”等指代。
3. “要资料/发教程/怎么领视频”识别为独立 Goal=request_material、Issue=material_resource；资料类型放入 slots.material_type。普通事实询问才使用 ask_information。明确不要资料时使用 reject，资料打不开或发送失败时使用 request_service。
4. 售前品质、对版和保障用 Issue=trust_guarantee；已经发生的收货、售后、退款退货主题统一用 Issue=after_sale；明确退款退货时 Goal=request_refund_return。
5. 只有明确请求人工、退款退货或强烈投诉才使用对应人工 Goal。出现“客服指导、客服怎么说”不等于请求人工。
6. 不能仅因出现“贵”判断价格异议，例如“名贵兰花”；必须结合完整语义和反例。
7. evidence 必须引用当前消息或最近对话中的短原文；不能编造。confidence 低于 0.60 或指代无法消解时 scope=ambiguous、Goal=unclear。
8. sales_signals 仅允许 `{signal_values}`；客户自述付款只能为 payment_claimed，禁止输出 purchased。
9. slots.decision_blocker 仅记录明确成交阻碍，type 只能是 price | trust | care_risk | product_fit | choice | timing | other。

候选标签卡（含定义、正例和反例）：
{candidate_text}

旧兼容意图目录（只用于理解历史标注样例，不得作为新输出字段）：{legacy_intent_values}

不要输出 route、primary_intent、need_template、need_rag、need_human 或 sales_stage；这些由确定性策略根据 D/G/I 计算。
"""


def _build_prompt(
    message: str,
    recent_turns: list[dict] | None = None,
    candidates: list[dict] | None = None,
) -> str:
    recent_lines = []
    for turn in (recent_turns or [])[-6:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "").strip()
        content = " ".join(str(turn.get("content") or "").split())[:200]
        if role in {"user", "assistant"} and content:
            recent_lines.append(f"{role}: {content}")
    recent_context = "\n".join(recent_lines) or "none"
    candidate_text = format_candidate_cards_compact(candidates)
    issue_values = ",".join(taxonomy_values("issue"))
    configured_intents = ",".join(system_tag_values("intent")) or "unknown"
    return f"""You classify Chinese customer-service messages for an orchid seller.
Return exactly one compact JSON object. Do not answer the customer.

Message: {message[:500]}
Recent context (最近对话):
{recent_context}

Allowed candidate labels:
{candidate_text}
Configured business intent tags (context only; do not output): {configured_intents}

Required JSON:
{{
  "primary_domain": "candidate domain id",
  "primary_goal": "candidate goal id",
  "issues": ["zero or more allowed issue ids"],
  "scope": "in_scope|ambiguous|out_of_scope",
  "confidence": 0.0,
  "slots": {{}}
}}

Rules:
- Use only candidate domain and goal ids.
- issues may only contain: {issue_values}
- Confidence is telemetry; never use unclear only because confidence is low.
- Objection, rejection, complaint and human request require explicit wording; neutral
  questions about price, benefits or enrollment are not objections.
- Explicit buy/join/pay/link actions override inferred motives; retain other needs in slots.
- Use request_material for an explicit request to send or receive fixed learning
  materials; use ask_information when the customer only asks about information.
- Labeled examples are trusted references, but copy their labels only when the current
  message has the same meaning after considering recent context.
- Resolve pronouns such as "这个服务", "你们说的那个", "怎么进" from recent context.
- If recent context is about 陪伴养兰服务 and the customer asks about its
  content, benefits/福利, price, enrollment, purchase, payment, or link, set
  slots.product_request_kind="membership". Also set
  slots.membership_question_kind to capability, price, purchase, or combined.
- For membership service intent, use product/seek_help/product_selection for service
  information, and commerce/transact/order_process for joining or purchasing.
- slots may include explicit facts and product/service references resolved from recent context.
- Output JSON only. No evidence, explanation, route, sales stage, sentiment, or legacy intent fields.
"""


def _classifier_source(reason: object) -> str:
    value = str(reason or "")
    if value.startswith("soft_rule_"):
        return "fallback_rule"
    if value.startswith("rule_"):
        return "hard_rule"
    if value.startswith(("contextual_", "opening_", "pending_", "structured_")):
        return "context_rule"
    return "unknown"
