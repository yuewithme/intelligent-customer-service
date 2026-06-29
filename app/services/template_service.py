from app.config import get_settings
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.schemas.template import TemplateItem, TemplateReply


_templates: dict[str, TemplateItem] = {
    "tpl_price_objection_default": TemplateItem(
        template_id="tpl_price_objection_default",
        name="价格异议默认话术",
        intent="price_objection",
        stage="objection_handling",
        trigger_examples=["有点贵", "太贵了", "再考虑一下"],
        content="我理解你会关注价格。这个价格主要包含了品质筛选、养护支持和售后保障，适合想省心入手的情况。",
        next_action="offer_value_explanation",
        priority=80,
    ),
    "tpl_logistics_default": TemplateItem(
        template_id="tpl_logistics_default",
        name="物流默认话术",
        intent="ask_logistics",
        trigger_examples=["什么时候发货", "物流"],
        content="正常会尽快安排发货，具体时效会结合地区和库存确认。你也可以把收货城市发我，我帮你进一步确认。",
        priority=50,
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
    for template in _templates.values():
        if template.status != "active":
            continue
        if any(word and word in text for word in template.not_use_when):
            continue
        intent_match = 1.0 if template.intent == intent.primary_intent else 0.0
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
    del message, user_state
    return TemplateReply(
        answer=template.content,
        template_id=template.template_id,
        next_action=template.next_action,
    )


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
