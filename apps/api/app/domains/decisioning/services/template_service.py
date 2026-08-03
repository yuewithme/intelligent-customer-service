from app.core.config import get_settings
from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.customers.schemas.state import UserState
from app.domains.decisioning.schemas.template import TemplateItem, TemplateReply
from app.domains.sales.services.shipping_contact_service import extract_shipping_contact, mask_mobile


_templates: dict[str, TemplateItem] = {
    "tpl_logistics_default": TemplateItem(
        template_id="tpl_logistics_default",
        name="物流默认话术",
        intent="ask_logistics",
        trigger_examples=["什么时候发货", "物流"],
        content="发货时效需要结合收货地区和当前库存确认。",
        priority=50,
    ),
    "tpl_order_recommend_default": TemplateItem(
        template_id="tpl_order_recommend_default",
        name="产品推荐需求确认",
        intent="order_intent",
        stage="need_discovery",
        trigger_examples=["推荐一款", "帮我选", "买哪个"],
        content="可以，我先结合您的情况帮您缩小范围。",
        next_action="discover_need_track",
        priority=90,
    ),
    "tpl_purchase_rejection_default": TemplateItem(
        template_id="tpl_purchase_rejection_default",
        name="客户暂不考虑产品",
        intent="purchase_rejection",
        stage="unknown",
        trigger_examples=["不要再给我推荐产品了", "先不买", "不用推荐"],
        content="明白，那我们先不聊产品，也不继续给您推荐。",
        priority=100,
    ),
    "tpl_shipping_damage_intake": TemplateItem(
        template_id="tpl_shipping_damage_intake",
        name="物流破损售后取证",
        intent="ask_after_sale",
        stage="unknown",
        trigger_examples=["花盆碎了", "苗也歪了", "运输破损"],
        content=(
            "收到，这种情况先把外包装、破损花盆和苗体状态照片拍清楚，"
            "再把订单号一起发我，我按售后流程提交人工审核。"
        ),
        next_action="collect_after_sales_evidence",
        priority=100,
    ),
    "tpl_order_information_received": TemplateItem(
        template_id="tpl_order_information_received",
        name="订单信息确认",
        intent="order_intent",
        stage="closing",
        trigger_examples=["详细地址", "电话", "身份证号"],
        content="好的，已收到您的订单和收货信息。",
        next_action="continue_order",
        priority=100,
    ),
}


async def create_template(template: TemplateItem) -> dict:
    _templates[template.template_id] = template
    return {"template_id": template.template_id, "status": "indexed"}


async def search_templates(
    message: NormalizedMessage,
    intent: IntentResult,
    user_state: UserState,
    top_k: int = 5,
) -> list[dict]:
    settings = get_settings()
    scored = []
    text = message.message.strip()
    candidates = [
        template
        for template in _templates.values()
        if (
            template.stage in {"unknown", intent.sales_stage}
            or template.intent == intent.primary_intent
        )
    ]
    for template in candidates:
        if template.status != "active":
            continue
        if (
            template.template_id == "tpl_order_information_received"
            and intent.slots.get("conversation_topic") != "order_information"
            and not extract_shipping_contact(text)
        ):
            continue
        if any(word and word in text for word in template.not_use_when):
            continue
        intent_match = 1.0 if template.intent == intent.primary_intent or template.branch_code else 0.0
        stage_match = 1.0 if template.stage == intent.sales_stage else 0.5
        vector_score = _text_score(text, template)
        tag_match = _tag_score(user_state.customer_tags, template.customer_tags)
        priority_score = min(max(template.priority, 0), 100) / 100
        final_score = (
            0.40 * intent_match
            + 0.25 * stage_match
            + 0.20 * vector_score
            + 0.10 * tag_match
            + 0.05 * priority_score
        )
        if final_score >= settings.template_min_score:
            scored.append((final_score, template))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            **template.model_dump(),
            "score": round(score, 4),
        }
        for score, template in scored[:top_k]
    ]


async def select_template(
    message: NormalizedMessage,
    intent: IntentResult,
    user_state: UserState,
) -> TemplateItem | None:
    results = await search_templates(
        message,
        intent,
        user_state,
        top_k=get_settings().template_top_k,
    )
    if not results:
        return None
    return TemplateItem.model_validate(results[0])


async def render_template(
    template: TemplateItem,
    message: NormalizedMessage,
    user_state: UserState,
) -> TemplateReply:
    content = template.content
    basic_info = _profile_basic_info(user_state)
    if template.template_id == "tpl_order_information_received":
        contact = {**basic_info, **extract_shipping_contact(message.message)}
        content = _shipping_contact_confirmation(contact)
    elif template.template_id == "tpl_logistics_default" and basic_info.get(
        "shipping_city"
    ):
        content = (
            "已记录您的收货地区，具体发货时效还需要结合当前库存和实际物流确认，"
            "我会继续为您跟进。"
        )
    return TemplateReply(
        answer=content,
        template_id=template.template_id,
        next_action=template.next_action,
    )


def _profile_basic_info(user_state: UserState) -> dict:
    profile = user_state.metadata.get("profile")
    if not isinstance(profile, dict):
        return {}
    basic_info = profile.get("basic_info")
    return basic_info if isinstance(basic_info, dict) else {}


def _shipping_contact_confirmation(contact: dict) -> str:
    details: list[str] = []
    if contact.get("shipping_address"):
        details.append(str(contact["shipping_address"]))
    if contact.get("recipient_name"):
        details.append(f"收件人{contact['recipient_name']}")
    if contact.get("mobile"):
        details.append(f"电话{mask_mobile(str(contact['mobile']))}")
    if not details:
        return "好的，已收到您的订单和收货信息。"
    return f"好的，已记录收货信息：{'，'.join(details)}。"


def _text_score(text: str, template: TemplateItem) -> float:
    if not template.trigger_examples:
        return 0.3
    best = 0.0
    chars = set(text)
    for example in template.trigger_examples:
        if example in text:
            return 1.0
        if example:
            best = max(best, len(chars & set(example)) / max(len(set(example)), 1))
    return best


def _tag_score(user_tags: list[str], template_tags: list[str]) -> float:
    if not template_tags:
        return 0.5
    return len(set(user_tags) & set(template_tags)) / len(set(template_tags))
