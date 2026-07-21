from __future__ import annotations

import re

from app.schemas.memory import MemoryEpisodeType, MemoryQueryPlan


_DEFAULT_FACT_KEYS = [
    "communication.preferred_detail",
    "communication.preferred_channel",
    "purchase.product_interest",
    "service.pain_point",
    "service.preference",
]

_RULES: tuple[
    tuple[tuple[str, ...], tuple[str, ...], tuple[MemoryEpisodeType, ...], bool], ...
] = (
    (
        ("订单", "付款", "支付", "发货", "物流", "收货", "购买"),
        ("purchase.status", "purchase.product_interest"),
        ("purchase", "after_sales"),
        True,
    ),
    (
        ("退款", "退货", "售后"),
        ("purchase.status", "service.pain_point"),
        ("refund", "after_sales", "complaint"),
        True,
    ),
    (
        ("预算", "价格", "价位", "多少钱"),
        ("purchase.budget",),
        ("product_consultation", "sales_objection"),
        False,
    ),
    (
        ("喜欢", "偏好", "习惯", "要求", "风格"),
        (
            "service.preference",
            "communication.preferred_detail",
            "communication.preferred_channel",
            "purchase.product_interest",
        ),
        ("preference_expression", "product_consultation"),
        False,
    ),
    (
        ("问题", "痛点", "投诉", "不满", "之前", "历史"),
        ("service.pain_point",),
        ("complaint", "after_sales", "sales_objection"),
        False,
    ),
    (
        ("承诺", "答应", "跟进", "回访"),
        ("service.commitment",),
        ("commitment", "after_sales"),
        False,
    ),
    (
        ("名字", "姓名", "称呼"),
        ("identity.display_name",),
        (),
        False,
    ),
    (
        ("地区", "地址", "城市", "哪里人"),
        ("location.region",),
        (),
        False,
    ),
)


def plan_memory_query(query: str) -> MemoryQueryPlan:
    normalized = query.strip().lower()
    fact_keys: list[str] = []
    episode_types: list[MemoryEpisodeType] = []
    matched_terms: list[str] = []
    require_verified_business = False

    for keywords, keys, types, verified in _RULES:
        hits = [keyword for keyword in keywords if keyword in normalized]
        if not hits:
            continue
        matched_terms.extend(hits)
        fact_keys.extend(keys)
        episode_types.extend(types)
        require_verified_business = require_verified_business or verified

    if not fact_keys:
        fact_keys.extend(_DEFAULT_FACT_KEYS)
    query_terms = matched_terms + re.findall(r"[a-z0-9_-]{2,}", normalized)
    return MemoryQueryPlan(
        requested_fact_keys=list(dict.fromkeys(fact_keys)),
        include_episodes=True,
        episode_types=list(dict.fromkeys(episode_types)),
        require_verified_business=require_verified_business,
        query_terms=list(dict.fromkeys(query_terms)),
    )
