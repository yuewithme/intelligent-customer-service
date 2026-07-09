import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    ConversationMemoryModel,
    ProfileEventModel,
    UserProfileModel,
)
from app.services.llm_service import generate_json
from app.services.sales_action_service import evolve_opportunity
from app.services.tag_catalog import TAG_CATEGORIES


_sessionmakers: dict[str, sessionmaker] = {}
_profile_tables = [
    UserProfileModel.__table__,
    ConversationMemoryModel.__table__,
    ProfileEventModel.__table__,
]
_allowed_patch_fields = {
    "current_stage",
    "risk_level",
    "customer_tags",
    "product_interests",
    "preference_summary",
    "pain_points",
    "is_human_handoff",
    "human_ticket_id",
    "human_handoff_status",
    "human_handoff_reason",
}

PROFILE_RECORD_FORMAT_INSTRUCTION = (
    "请读取每条记录的 `role` 和 `content` 字段；`content` 会以双大括号包裹。"
    "`customer` 是客户原话，是画像事实来源；`assistant` 和 `human` 是客服回复，"
    "只能用于理解客户追问、指代关系和上下文，不能当作客户事实。"
)
PROFILE_STABILITY_INSTRUCTION = (
    "画像必须优先保留地区、规模、偏好、预算、购买意向、长期痛点等稳定事实。"
    "短期情绪、抱怨、辱骂或催促不能覆盖长期稳定事实，也不能被提取为长期痛点。"
)

DEFAULT_PROFILE_ANALYSIS_PROMPT = """你是兰花私域客服的用户画像分析助手。

你的唯一输入是【用户消息原文记录】。只能依据这些用户亲自发送的内容生成画像。

严禁使用或输出路由、意图、模板编号、AI 回复、系统判断、知识库命中结果等中间字段。

输出要求：
1. 只输出 JSON 对象，不要 Markdown、解释或代码块。
2. 使用中文自然语言总结，不能照抄后台字段。
3. `customer_tags` 必须从【标签库】里选择，严禁自造标签。
4. `pain_points` 是中文数组，每项概括一个明确痛点，要包含对象、问题和用户顾虑。
5. 没有明确内容时输出空数组。

JSON 格式：
{
  "risk_level": "normal | medium | high",
  "customer_tags": [],
  "product_interests": [],
  "pain_points": []
}
"""


async def get_profile_bundle(user_id: str) -> dict:
    with _get_session() as session:
        profile = _get_or_create_profile(session, user_id)
        session.commit()
        return {
            "profile": _profile_to_dict(profile),
            "recent_memories": _list_memories(session, user_id, 10),
            "events": _list_events(session, user_id, 20),
        }


async def patch_user_profile(user_id: str, updates: dict) -> dict:
    metadata = updates.get("metadata") if isinstance(updates.get("metadata"), dict) else {}
    reason = metadata.get("reason") or "manual_patch"
    with _get_session() as session:
        profile = _get_or_create_profile(session, user_id)
        before = _profile_to_dict(profile)
        for field, value in updates.items():
            if field not in _allowed_patch_fields:
                continue
            _set_profile_field(profile, field, value)
        profile.updated_at = _now()
        after = _profile_to_dict(profile)
        changed_before, changed_after = _changed_fields(before, after)
        if changed_after:
            session.add(
                ProfileEventModel(
                    user_id=profile.user_id,
                    tenant_id=profile.tenant_id,
                    event_type="profile_patched",
                    before_json=_json_dumps(changed_before),
                    after_json=_json_dumps(changed_after),
                    reason=str(reason),
                    trace_id=metadata.get("trace_id"),
                    created_at=_now(),
                )
            )
        session.commit()
        return _profile_to_dict(profile)


async def get_recent_memories(user_id: str, limit: int = 10) -> dict:
    limit = _clamp_limit(limit, default=10, maximum=50)
    with _get_session() as session:
        _get_or_create_profile(session, user_id)
        session.commit()
        return {"items": _list_memories(session, user_id, limit), "limit": limit}


async def get_profile_events(user_id: str, limit: int = 20) -> dict:
    limit = _clamp_limit(limit, default=20, maximum=100)
    with _get_session() as session:
        _get_or_create_profile(session, user_id)
        session.commit()
        return {"items": _list_events(session, user_id, limit), "limit": limit}


async def append_conversation_memory(
    *,
    user_id: str,
    tenant_id: str = "tenant_default",
    session_id: str | None,
    role: str,
    content: str,
    intent: str | None = None,
    route: str | None = None,
    template_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    if not content:
        return
    with _get_session() as session:
        _get_or_create_profile(session, user_id, tenant_id=tenant_id)
        session.add(
            ConversationMemoryModel(
                user_id=user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                role=role,
                content=content,
                intent=intent,
                route=route,
                template_id=template_id,
                trace_id=trace_id,
                created_at=_now(),
            )
        )
        session.commit()


async def update_profile_after_chat(message, intent, reply) -> None:
    with _get_session() as session:
        user_records = _list_profile_context_records(
            session,
            message.user_id,
            current_message=message.message,
            limit=18,
        )
    profile_analysis = await _build_profile_analysis(user_records)
    with _get_session() as session:
        profile = _get_or_create_profile(
            session,
            message.user_id,
            tenant_id=message.tenant_id,
            channel=message.channel,
        )
        before = _profile_to_dict(profile)
        if getattr(intent, "sales_stage", None) and intent.sales_stage != "unknown":
            profile.current_stage = intent.sales_stage
        profile.last_intent = intent.primary_intent
        profile.last_route = reply.route
        profile.last_template_id = reply.template_id
        profile.last_active_at = _now()
        _apply_tag_result(profile, reply.metadata.get("tag_result"))
        _apply_sales_action(profile, intent, reply.metadata.get("sales_action"))
        _apply_profile_analysis(profile, profile_analysis)
        profile.updated_at = _now()
        if reply.route == "human" or reply.need_human:
            handoff = reply.metadata.get("handoff", {})
            profile.is_human_handoff = True
            profile.human_ticket_id = handoff.get("ticket_id")
            profile.human_handoff_status = "pending"
            profile.human_handoff_reason = (
                handoff.get("reason")
                or getattr(intent, "reason", None)
                or reply.metadata.get("reason")
                or "human_route"
            )
            _add_event(
                session,
                profile,
                "handoff_created",
                before,
                _profile_to_dict(profile),
                profile.human_handoff_reason,
                message.trace_id,
            )
        session.commit()


def _apply_tag_result(profile: UserProfileModel, tag_result: Any) -> None:
    if not isinstance(tag_result, dict):
        return
    risk_level = tag_result.get("risk_level")
    if isinstance(risk_level, str) and risk_level.strip():
        profile.risk_level = risk_level.strip()
    labels = _string_list(tag_result.get("labels"))
    if not labels:
        return
    customer_tags = _json_loads(profile.customer_tags_json, [])
    incoming_customer_tags: list[str] = []
    product_interests = _json_loads(profile.product_interests_json, [])
    pain_points = _json_loads(profile.pain_points_json, [])
    for label in labels:
        if label.startswith("product_interest:"):
            product_interests = _append_unique(product_interests, label.split(":", 1)[1])
        elif label.startswith("pain_point:"):
            pain_points = _append_unique(pain_points, label.split(":", 1)[1])
            incoming_customer_tags.append(label)
        elif label.startswith("customer_tag:"):
            incoming_customer_tags.append(label)
        else:
            incoming_customer_tags.append(label)
    profile.customer_tags_json = _json_dumps(
        _merge_customer_tags(customer_tags, incoming_customer_tags)
    )
    profile.product_interests_json = _json_dumps(product_interests)
    profile.pain_points_json = _json_dumps(pain_points)


def _apply_sales_action(profile: UserProfileModel, intent, sales_action: Any) -> None:
    if not isinstance(sales_action, dict):
        return
    profile.active_opportunity_json = _json_dumps(
        evolve_opportunity(
            _json_loads(profile.active_opportunity_json, {}),
            sales_stage=intent.sales_stage,
            sales_action=sales_action,
        )
    )


async def _build_profile_analysis(user_records: list[dict]) -> dict:
    prompt = _build_profile_analysis_prompt(user_records)
    try:
        analysis = await generate_json(prompt, purpose="profile")
    except Exception:
        return _fallback_profile_analysis(user_records)
    if not _is_valid_profile_analysis(analysis):
        return _fallback_profile_analysis(user_records)
    return analysis


def _build_profile_analysis_prompt(user_records: list[dict]) -> str:
    settings = get_settings()
    prompt = _profile_prompt_with_record_format(
        settings.profile_analysis_prompt.strip() or DEFAULT_PROFILE_ANALYSIS_PROMPT
    )
    prompt_records = _wrap_prompt_record_content(user_records)
    return (
        f"{prompt}\n\n【标签库】\n{_json_dumps(_profile_tag_catalog_prompt())}"
        f"\n\n【聊天上下文记录】\n{_json_dumps(prompt_records)}"
    )


def _profile_prompt_with_record_format(prompt: str) -> str:
    result = prompt.rstrip()
    if not (
        "读取每条记录的 `role` 和 `content` 字段" in prompt
        and "customer" in prompt
        and "assistant" in prompt
    ):
        result = f"{result}\n{PROFILE_RECORD_FORMAT_INSTRUCTION}"
    if "短期情绪、抱怨、辱骂或催促不能覆盖长期稳定事实" not in result:
        result = f"{result}\n{PROFILE_STABILITY_INSTRUCTION}"
    return result


def _wrap_prompt_record_content(user_records: list[dict]) -> list[dict]:
    wrapped_records = []
    for record in user_records:
        if not isinstance(record, dict):
            continue
        wrapped = dict(record)
        wrapped["role"] = _profile_context_role(wrapped.get("role"))
        content = wrapped.get("content")
        if isinstance(content, str):
            wrapped["content"] = f"{{{{{content}}}}}"
        wrapped_records.append(wrapped)
    return wrapped_records


def _is_valid_profile_analysis(value: Any) -> bool:
    return isinstance(value, dict) and any(
        isinstance(value.get(key), expected)
        for key, expected in {
            "customer_tags": list,
            "product_interests": list,
            "pain_points": list,
        }.items()
    )


def _apply_profile_analysis(
    profile: UserProfileModel,
    analysis: dict,
) -> None:
    risk_level = _risk_value(analysis.get("risk_level"))
    if risk_level and _risk_rank(risk_level) > _risk_rank(profile.risk_level):
        profile.risk_level = risk_level

    profile.customer_tags_json = _json_dumps(
        _merge_customer_tags(
            _json_loads(profile.customer_tags_json, []),
            _string_list(analysis.get("customer_tags")),
        )
    )
    profile.product_interests_json = _json_dumps(
        _dedupe(
            [
                *_json_loads(profile.product_interests_json, []),
                *_string_list(analysis.get("product_interests")),
            ]
        )
    )
    profile.pain_points_json = _json_dumps(
        _merge_descriptive_list(
            _json_loads(profile.pain_points_json, []),
            _string_list(analysis.get("pain_points")),
        )
    )
def _risk_rank(value: str | None) -> int:
    return {"normal": 0, "medium": 1, "high": 2}.get(value or "", 0)


def _merge_descriptive_list(existing: list[str], incoming: list[str]) -> list[str]:
    merged = _dedupe(existing)
    for value in incoming:
        if any(value in item for item in merged):
            continue
        merged = [item for item in merged if item not in value]
        merged.append(value)
    return merged


def _fallback_profile_analysis(user_records: list[dict]) -> dict:
    texts = [
        str(record.get("content") or "")
        for record in user_records
        if isinstance(record, dict)
        and record.get("content")
        and record.get("role", "customer") == "customer"
    ]
    combined = "\n".join(texts)
    customer_tags: list[str] = []
    region = _region_from_text(combined)
    budget = _budget_from_text(combined)
    plant_count = _plant_count_from_text(combined)
    if region:
        customer_tags.append(f"region:{region}")
    if budget:
        customer_tags.append(f"budget:{budget}")
    if plant_count:
        customer_tags.append(f"plant_count:{plant_count}")

    product_interests: list[str] = []
    if _contains_any(combined, ("兰花", "蘭花", "orchid")):
        product_interests.append("兰花养护")

    pain_points: list[str] = []
    for text in texts:
        pain_point = _pain_point_from_text(text)
        if pain_point:
            pain_points = _append_or_replace_specific(pain_points, pain_point)

    return {
        "risk_level": "normal",
        "customer_tags": customer_tags,
        "product_interests": product_interests,
        "pain_points": pain_points,
    }


def _render_profile_summary(profile: UserProfileModel) -> str:
    tags = _merge_customer_tags(_json_loads(profile.customer_tags_json, []), [])
    interests = _json_loads(profile.product_interests_json, [])
    pain_points = _json_loads(profile.pain_points_json, [])
    if not tags and not interests and not pain_points:
        return ""
    advice = {
        "pain_confirmed": "基于明确痛点直接给出合适方案，减少重复追问。",
        "solution_recommended": "说明方案价值并确认客户是否认可。",
        "price_discussed": "先回应价格问题，再确认购买意向。",
        "objection_handling": "针对客户异议给出直接说明，避免重复询问。",
        "order_intent": "确认规格、数量和收货信息，推进下单。",
        "after_sale": "优先解决售后问题，暂停销售推进。",
        "human_pending": "优先由人工接管并解决当前问题，暂停销售推进。",
    }.get(profile.current_stage, "补充一个最关键的缺失信息，再推进下一步。")
    return (
        f"客户情况：{'、'.join(tags) or '信息待补充'}；"
        f"产品兴趣：{'、'.join(interests) or '待确认'}。\n"
        f"当前诉求：{'；'.join(pain_points) or '待确认'}。\n"
        f"跟进建议：{advice}"
    )


def _pain_point_from_text(text: str) -> str:
    if not text:
        return ""

    issues: list[str] = []
    if _contains_any(text, ("烂根", "爛根", "root rot")):
        issues.append("烂根")
    if _contains_any(text, ("黄叶", "黃葉", "叶子发黄", "葉子發黃")):
        issues.append("黄叶")
    if _contains_any(text, ("黑腐", "腐烂", "腐爛")) and "烂根" not in issues:
        issues.append("腐烂")
    if _contains_any(text, ("不开花", "不来花", "没花", "沒有花")):
        issues.append("不开花")
    if _contains_any(text, ("虫", "病虫害", "介壳虫", "蚧壳虫")):
        issues.append("病虫害")
    if _contains_any(text, ("不会养", "新手", "第一次养", "怕养死", "养死", "養死")):
        concern = "担心养死"
    elif _contains_any(text, ("怎么救", "救回来", "急救")):
        concern = "需要救治方案"
    else:
        concern = ""

    if not issues:
        return ""

    subject = "兰花" if _contains_any(text, ("兰花", "蘭花", "orchid")) else "植物"
    issue_text = "、".join(_dedupe(issues))
    suffixes = [concern] if concern else []
    if _contains_any(text, ("怎么救", "救回来", "急救", "咋办", "怎么办")):
        suffixes.append("需要救治方案")
    suffix_text = f"，{'，'.join(_dedupe(suffixes))}" if suffixes else ""
    return f"{subject}{issue_text}{suffix_text}"


def _region_from_text(text: str) -> str:
    known_regions = (
        "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
        "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
        "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
        "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
        "杭州", "广州", "深圳", "成都", "南京", "苏州", "宁波",
    )
    for region in known_regions:
        if region in text:
            return region
    return ""


def _budget_from_text(text: str) -> str:
    import re

    match = re.search(r"(?:预算|預算|budget|价格|價位|价位)[^\d]{0,8}(\d{2,6})", text, re.I)
    return match.group(1) if match else ""


def _plant_count_from_text(text: str) -> str:
    import re

    match = re.search(r"(?:养了|養了|养|養|有)[^\d]{0,6}(\d{1,5})\s*(盆|棵|株)", text)
    return f"{match.group(1)}{match.group(2)}" if match else ""


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _is_more_specific_pain_point(candidate: str, existing: str) -> bool:
    return bool(candidate and existing and existing in candidate and candidate != existing)


def _append_or_replace_specific(values: list[str], value: str) -> list[str]:
    compacted = [
        item
        for item in values
        if item and not _is_more_specific_pain_point(value, item)
    ]
    return _append_unique(compacted, value)


def _risk_value(value: Any) -> str:
    allowed = {"normal", "medium", "high"}
    return value if isinstance(value, str) and value in allowed else ""


def _merge_customer_tags(existing: list[str], incoming: list[str]) -> list[str]:
    candidates = [
        normalized_tag
        for tag in [*existing, *incoming]
        for normalized_tag in [_normalize_customer_tag(tag)]
        if isinstance(tag, str) and tag.strip()
        if normalized_tag
    ]
    latest_by_key: dict[str, tuple[int, str]] = {}
    for index, tag in enumerate(candidates):
        latest_by_key[_tag_replace_key(tag)] = (index, tag)
    selected = [
        (index, tag)
        for key, (index, tag) in latest_by_key.items()
        if key and tag
    ]
    selected.sort(key=lambda item: (_tag_order(item[1]), item[0]))
    return [tag for _, tag in selected]


def _normalize_customer_tag(tag: str) -> str:
    tag = tag.strip()
    if tag.startswith("customer_tag:"):
        tag = tag.split(":", 1)[1].strip()
    if _is_catalog_tag_value(tag):
        return tag
    if tag.startswith("region:"):
        return _catalog_province_value(tag.split(":", 1)[1])
    if tag.startswith("plant_count:"):
        return _catalog_quantity_value(tag.split(":", 1)[1])
    if tag.startswith("preference:"):
        return _catalog_orchid_type_value(tag.split(":", 1)[1])
    return ""


def _tag_replace_key(tag: str) -> str:
    return _catalog_category_for_tag(tag) or tag


def _tag_order(tag: str) -> int:
    order = {
        "province": 10,
        "orchid_quantity": 20,
        "customer_level": 30,
        "favorite_orchid_type": 40,
    }
    return order.get(_catalog_category_for_tag(tag), 100)


def _profile_tag_catalog_prompt() -> dict[str, list[str]]:
    return {
        category.name: [value.name for value in category.values]
        for category in TAG_CATEGORIES.values()
    }


def _catalog_tag_values() -> set[str]:
    return {
        value.name
        for category in TAG_CATEGORIES.values()
        for value in category.values
    }


def _is_catalog_tag_value(tag: str) -> bool:
    return tag in _catalog_tag_values()


def _catalog_category_for_tag(tag: str) -> str:
    for category in TAG_CATEGORIES.values():
        if any(value.name == tag for value in category.values):
            return category.id
    return ""


def _catalog_province_value(raw: str) -> str:
    raw = raw.strip()
    aliases = {
        "北京": "北京市",
        "天津": "天津市",
        "上海": "上海市",
        "重庆": "重庆市",
        "广西": "广西省",
        "西藏": "西藏自治区",
        "杭州": "浙江省",
    }
    if raw in aliases:
        return aliases[raw]
    if _is_catalog_tag_value(raw):
        return raw
    for suffix in ("省", "市", "自治区"):
        candidate = f"{raw}{suffix}"
        if _is_catalog_tag_value(candidate):
            return candidate
    return ""


def _catalog_quantity_value(raw: str) -> str:
    import re

    match = re.search(r"(\d+)", raw)
    if not match:
        return ""
    count = int(match.group(1))
    if count > 10000:
        return ""
    if count <= 10:
        return "1-10盆"
    if count <= 30:
        return "10-30盆"
    if count <= 50:
        return "30-50盆"
    if count < 100:
        return "50-100盆"
    if count <= 200:
        return "100-200盆"
    if count < 1000:
        return "200+盆"
    return "1000+盆"


def _catalog_orchid_type_value(raw: str) -> str:
    raw = raw.strip()
    return raw if _is_catalog_tag_value(raw) else ""


def _append_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        return [*values, value]
    return values


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_profile_tables)
        _ensure_profile_columns(engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def _get_or_create_profile(
    session: Session,
    user_id: str,
    *,
    tenant_id: str = "tenant_default",
    channel: str = "api",
) -> UserProfileModel:
    profile = session.scalar(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    if profile is None:
        now = _now()
        profile = UserProfileModel(
            user_id=user_id,
            tenant_id=tenant_id or "tenant_default",
            channel=channel or "api",
            current_stage="unknown",
            risk_level="normal",
            created_at=now,
            updated_at=now,
        )
        session.add(profile)
        session.flush()
    return profile


def _list_memories(session: Session, user_id: str, limit: int) -> list[dict]:
    rows = session.scalars(
        select(ConversationMemoryModel)
        .where(ConversationMemoryModel.user_id == user_id)
        .order_by(ConversationMemoryModel.created_at.desc(), ConversationMemoryModel.id.desc())
        .limit(limit)
    ).all()
    return [_memory_to_dict(row) for row in reversed(rows)]


def _list_profile_context_records(
    session: Session,
    user_id: str,
    *,
    current_message: str,
    limit: int,
) -> list[dict]:
    rows = session.scalars(
        select(ConversationMemoryModel)
        .where(
            ConversationMemoryModel.user_id == user_id,
            ConversationMemoryModel.role.in_(("user", "assistant", "human")),
        )
        .order_by(ConversationMemoryModel.created_at.desc(), ConversationMemoryModel.id.desc())
        .limit(limit)
    ).all()
    records = [
        {
            "created_at": _datetime_to_iso(row.created_at),
            "role": _profile_context_role(row.role),
            "content": row.content,
        }
        for row in reversed(rows)
        if row.content
    ]
    if current_message and (not records or records[-1].get("content") != current_message):
        records.append({"created_at": None, "role": "customer", "content": current_message})
    return records[-limit:]


def _profile_context_role(role: str | None) -> str:
    if role == "user":
        return "customer"
    if role in {"assistant", "human"}:
        return role
    return "customer"


def _list_events(session: Session, user_id: str, limit: int) -> list[dict]:
    rows = session.scalars(
        select(ProfileEventModel)
        .where(ProfileEventModel.user_id == user_id)
        .order_by(ProfileEventModel.created_at.desc(), ProfileEventModel.id.desc())
        .limit(limit)
    ).all()
    return [_event_to_dict(row) for row in rows]


def _set_profile_field(profile: UserProfileModel, field: str, value: Any) -> None:
    if field == "customer_tags":
        profile.customer_tags_json = _json_dumps(_string_list(value))
    elif field == "product_interests":
        profile.product_interests_json = _json_dumps(_string_list(value))
    elif field == "pain_points":
        profile.pain_points_json = _json_dumps(_string_list(value))
    elif hasattr(profile, field):
        setattr(profile, field, value)


def _profile_to_dict(profile: UserProfileModel) -> dict:
    return {
        "user_id": profile.user_id,
        "tenant_id": profile.tenant_id,
        "channel": profile.channel,
        "current_stage": profile.current_stage,
        "risk_level": profile.risk_level,
        "is_human_handoff": profile.is_human_handoff,
        "human_ticket_id": profile.human_ticket_id,
        "human_handoff_status": profile.human_handoff_status,
        "human_handoff_reason": profile.human_handoff_reason,
        "customer_tags": _merge_customer_tags(
            _json_loads(profile.customer_tags_json, []),
            [],
        ),
        "product_interests": _json_loads(profile.product_interests_json, []),
        "ai_summary": _render_profile_summary(profile),
        "preference_summary": profile.preference_summary,
        "pain_points": _json_loads(profile.pain_points_json, []),
        "active_opportunity": _json_loads(profile.active_opportunity_json, {}),
        "last_intent": profile.last_intent,
        "last_route": profile.last_route,
        "last_template_id": profile.last_template_id,
        "last_active_at": _datetime_to_iso(profile.last_active_at),
        "created_at": _datetime_to_iso(profile.created_at),
        "updated_at": _datetime_to_iso(profile.updated_at),
    }


def _ensure_profile_columns(engine) -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("user_profiles")}
    if "active_opportunity_json" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE user_profiles "
                    "ADD COLUMN active_opportunity_json TEXT DEFAULT '{}'"
                )
            )


def _memory_to_dict(row: ConversationMemoryModel) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "tenant_id": row.tenant_id,
        "session_id": row.session_id,
        "role": row.role,
        "content": row.content,
        "intent": row.intent,
        "route": row.route,
        "template_id": row.template_id,
        "trace_id": row.trace_id,
        "created_at": _datetime_to_iso(row.created_at),
    }


def _event_to_dict(row: ProfileEventModel) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "tenant_id": row.tenant_id,
        "event_type": row.event_type,
        "before": _json_loads(row.before_json, {}),
        "after": _json_loads(row.after_json, {}),
        "reason": row.reason,
        "trace_id": row.trace_id,
        "created_at": _datetime_to_iso(row.created_at),
    }


def _add_event(
    session: Session,
    profile: UserProfileModel,
    event_type: str,
    before: dict,
    after: dict,
    reason: str | None,
    trace_id: str | None,
) -> None:
    changed_before, changed_after = _changed_fields(before, after)
    if not changed_after:
        return
    session.add(
        ProfileEventModel(
            user_id=profile.user_id,
            tenant_id=profile.tenant_id,
            event_type=event_type,
            before_json=_json_dumps(changed_before),
            after_json=_json_dumps(changed_after),
            reason=reason,
            trace_id=trace_id,
            created_at=_now(),
        )
    )


def _changed_fields(before: dict, after: dict) -> tuple[dict, dict]:
    changed_before = {}
    changed_after = {}
    for key, value in after.items():
        if before.get(key) != value:
            changed_before[key] = before.get(key)
            changed_after[key] = value
    return changed_before, changed_after


def _clamp_limit(value: int, *, default: int, maximum: int) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = default
    return max(1, min(limit, maximum))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _datetime_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)
