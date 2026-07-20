"""Repeatable first-order sales script seed.

The copy deliberately avoids unverifiable operating claims. Templates that mention
price, stock, gifts, promotions, or service entitlements are protected by fact keys.
"""

from app.talk_script.repository import list_sales_templates, upsert_sales_templates


OPERATING_CLAIM_FACT_KEYS = {
    "service_headcount": "service_headcount",
    "product_quality": "product_quality_record",
    "hormone_free": "hormone_free_certificate",
    "year_round_service": "service_entitlements",
    "inventory": "inventory",
    "price": "current_price",
    "gift": "current_gift",
    "promotion": "current_promotion",
}


def _script(
    code: str,
    stage: str,
    action: str,
    answer: str,
    *,
    conditions: list[str] | None = None,
    excludes: list[str] | None = None,
    facts: list[str] | None = None,
    variables: list[str] | None = None,
    priority: int = 60,
) -> dict:
    return {
        "template_id": f"sales_{code}",
        "question_id": f"sales_question_{code}",
        "template_name": code.replace("_", " "),
        "answer_default": answer,
        "answer_goal": action,
        "sales_stage": stage,
        "sales_action": action,
        "branch_code": code,
        "required_conditions": conditions or [],
        "exclude_conditions": excludes or ["after_sale", "human_pending"],
        "required_fact_keys": facts or [],
        "variables": variables or [],
        "priority": priority,
        "status": "active",
        "version": "v2",
    }


FIRST_ORDER_SALES_TEMPLATES: tuple[dict, ...] = (
    _script("rapport_welcome", "rapport", "build_rapport", "您好，我在的。您这次是想了解兰花养护，还是想选一盆合适的兰花？"),
    _script("rapport_basic_context", "rapport", "build_rapport", "可以，我先了解一下您的实际情况，再给您更贴合的建议。", conditions=["responded"]),
    _script("need_service", "need_discovery", "discover_need_track", "明白，先把当前养护问题理清楚。您最希望优先改善哪个情况？", conditions=["service_need"]),
    _script("need_product", "need_discovery", "discover_need_track", "可以，我会按您的环境和偏好缩小选择范围。", conditions=["product_need"]),
    _script("need_combined", "need_discovery", "discover_need_track", "可以同时考虑选品和后续养护，我先按您最关心的问题来安排。", conditions=["combined_need"]),
    _script("pain_beginner", "pain_discovery", "discover_pain", "新手先不用追求复杂品种，重点是环境匹配和养护节奏。您目前最担心哪一步？", conditions=["experience_level:beginner"]),
    _script("pain_leaf", "pain_discovery", "discover_pain", "黄叶焦尖可能有多种原因，先不要急着下确定结论。请描述最近的光照、浇水和通风变化。", conditions=["pain_point:leaf"], excludes=["dangerous_chemical_request"]),
    _script("pain_root", "pain_discovery", "discover_pain", "烂根需要结合根系状态和养护环境判断；先隔离观察，不建议在信息不足时直接配药。", conditions=["pain_point:root_rot"], excludes=["dangerous_chemical_request"]),
    _script("pain_no_bloom", "pain_discovery", "discover_pain", "不开花通常要结合苗龄、光照和温差判断，我先帮您确认最关键的环境条件。", conditions=["pain_point:no_bloom"]),
    _script("pain_no_clear", "pain_discovery", "discover_pain", "如果目前没有明显问题，也可以从好养程度、花色和摆放环境来选。", conditions=["no_clear_pain"]),
    _script("solution_service", "solution_recommended", "recommend_solution", "按您描述的情况，更适合先给出分步骤养护方案，再根据反馈调整。", conditions=["service_need"]),
    _script("solution_preference", "solution_recommended", "recommend_solution", "我会只推荐少量符合您环境和偏好的方案，并说明每个方案的适配依据。", conditions=["preference_revealed"], facts=["product_catalog"]),
    _script("solution_budget", "solution_recommended", "recommend_solution", "可以按预算控制范围，先比较适配度，再核对实时规格和价格。", conditions=["budget"], facts=["product_catalog"]),
    _script("solution_product_teaching", "solution_recommended", "recommend_solution", "可以把产品选择和后续养护一起规划，具体权益以当前可核实的服务信息为准。", conditions=["combined_need"], facts=["product_catalog", "service_entitlements"]),
    _script("value_service", "value_built", "build_value", "这个方案的价值在于步骤清楚、能结合您的反馈调整；具体服务范围以当前权益记录为准。", facts=["service_entitlements"]),
    _script("value_quality", "value_built", "build_value", "苗情和规格只按商品实拍、检测或商品档案说明，不使用无法核实的绝对承诺。", facts=["product_quality_record"]),
    _script("value_care_risk", "value_built", "build_value", "养护结果受环境和操作影响，我能做的是把风险点和后续步骤讲清楚，不承诺一定养活。", conditions=["decision_blocker:care_risk"]),
    _script("value_compare", "value_built", "build_value", "比较时建议把规格、苗情、服务和实际权益放在一起看，价格只引用当前商品事实。", conditions=["decision_blocker:price"], facts=["current_price"]),
    _script("trial_direct", "trial_close", "trial_close", "如果这个方案符合预期，我可以按您确认的规格和数量继续核对订单信息。", conditions=["ready_to_buy"]),
    _script("trial_choice", "trial_close", "trial_close", "目前可以在这两个已核实方案中选择，我可以再按您最看重的一点帮您做取舍。", conditions=["decision_blocker:choice"], facts=["product_catalog"]),
    _script("trial_order_confirm", "trial_close", "trial_close", "请您确认商品、规格和数量；价格、库存及权益以订单工具返回结果为准。", facts=["current_price", "inventory", "order_preview"]),
    _script("closing_price", "closing", "resolve_blocker", "理解您关注价格。我先按当前可核实的规格、价格和权益说明，不做额外承诺。", conditions=["decision_blocker:price"], facts=["current_price"]),
    _script("closing_trust", "closing", "resolve_blocker", "您的顾虑合理，我只提供可核实的商品、订单和服务信息；不确定的部分会明确说明。", conditions=["decision_blocker:trust"]),
    _script("closing_timing", "closing", "resolve_blocker", "可以按您的节奏考虑，不制造稀缺或倒计时。需要时我再基于实时信息继续核对。", conditions=["decision_blocker:timing"]),
    _script("closing_payment", "closing", "close_order", "我可以继续核对真实订单和付款状态；只有支付回调或订单工具确认后才会标记成交。", conditions=["payment_claimed"], facts=["order_status"], priority=100),
)


def ensure_first_order_sales_templates() -> int:
    if list_sales_templates(status=None):
        return 0
    return upsert_sales_templates(FIRST_ORDER_SALES_TEMPLATES)
