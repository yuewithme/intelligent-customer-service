import logging
import re
from collections.abc import Collection
from datetime import datetime, timezone

from app.core.config import get_settings
from app.domains.decisioning.schemas.reply_plan import BusinessFacts
from app.integrations.youzan.services.youzan_identity_store import YouzanIdentityStore
from app.integrations.youzan.services.youzan_order_service import (
    YouzanOrderService,
    YouzanOrderSummary,
)
from app.integrations.youzan.services.youzan_token_service import (
    create_managed_youzan_client,
    youzan_credentials_available,
)
from app.domains.catalog.services.product_knowledge_service import (
    get_catalog_product,
    search_catalog_products,
)
from app.domains.decisioning.services.business_action_service import (
    CATALOG_SEARCH,
    ORDER_VERIFY,
    SELECTED_PRODUCT_DETAIL,
)


MOBILE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
PRICE_YUAN_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d{1,2})?)\s*元")
logger = logging.getLogger("wechat_rag_bot.commerce_query")
PRODUCT_IMAGE_REQUEST_WORDS = (
    "图片",
    "照片",
    "商品图",
    "实拍图",
    "发张图",
    "发图",
    "看图",
    "图册",
    "图集",
    "相册",
    "长什么样",
)
PRODUCT_REFERENCE_WORDS = {
    "这款",
    "这个",
    "这个花",
    "这盆花",
    "这株花",
    "这款花",
    "刚才那款",
    "刚刚那款",
    "上面那款",
}
PRODUCT_REFERENCE_ORDER_PATTERN = re.compile(
    r"^(?:那|那么)?(?:我)?"
    r"(?:就按|按|就选|选|就要|要|想买|买)?"
    r"(?:这个|这款|这盆|这株|这个花|这盆花|这株花|这款花|刚才那款|刚刚那款|上面那款)"
    r"(?:就)?(?:下单|购买|买|要)?(?:吧|了)?$"
)
CATALOG_KNOWLEDGE_FIELDS = {
    "product_name",
    "aliases",
    "category",
    "flower_color",
    "fragrance",
    "flowering_status",
    "care_scenes",
    "bloom_period",
    "audience_tag",
}
VALUE_KNOWLEDGE_FIELDS = {"highlighted_features", "sales_copy"}
SKU_KNOWLEDGE_FIELDS = {"price_budget", "market_price"}
MEMBERSHIP_SERVICE_CAPABILITIES = (
    "系统的视频课程",
    "结合具体养护问题的一对一指导",
)
MEMBERSHIP_SERVICE_VALUE_POINTS = (
    {
        "capability": "系统的视频课程",
        "customer_problem": "养兰基础零散，上盆、浇水、施肥等环节容易反复出错",
        "customer_benefit": "把基础养护系统理顺，少走弯路",
    },
    {
        "capability": "结合具体养护问题的一对一指导",
        "customer_problem": "地区、品种和养护习惯不同，通用资料难以覆盖具体问题",
        "customer_benefit": "结合实际情况针对性调整，避开反复试错",
    },
)
MEMBERSHIP_BRAND_POSITIONING = (
    "我们萧岚苑不只是卖兰花，更希望陪兰友把养兰基础理顺、把兰花养好"
)
MEMBERSHIP_CARE_PAIN_SALES_SCRIPT = (
    "我们服务过很多兰友，也有人有过{service_need}，只靠自己试很容易顾了一头又忽略另一头。\n\n"
    "我们萧岚苑的陪伴养兰服务，会先给您单品养护资料和视频，里面会讲收苗后的处理和上盆方法，"
    "以及浇水、施肥、防病害、花期管理和分株。看完后遇到不明白的地方，都可以随时问老师；"
    "针对您现在的{service_need}，我们也会结合您家里的环境和实际操作，一步步带着您调整。"
)
MEMBERSHIP_CARE_QUESTION_FOLLOWUP_SCRIPT = (
    "像您刚问的这个问题，陪伴养兰不是只发一份资料就结束。"
    "基础内容可以跟着单品养护资料和视频学，真正操作时，老师再结合您家里的环境和兰花状态一对一带着调整。"
)
MEMBERSHIP_SOFT_DECLINE_VALUE_SCRIPT = (
    "市面上不少商家只负责把兰花卖出去，卖完后养护基本靠兰友自己摸索。"
    "我们萧岚苑更看重您买回去以后真正养好，因为养稳了，您才会真正信任我们。\n\n"
    "我们服务过很多兰友，很多人一开始也是因为{service_need}自己反复试。"
    "后来用‘资料和视频理顺基础＋老师结合实际情况指导’的方式，才知道每一步的调整方法。\n\n"
    "陪伴养兰服务里，单品养护资料会讲收苗处理上盆、浇水、施肥、防病害、花期管理和分株；"
    "看完有不懂的可以随时问，老师会围绕您现在的{service_need}和实际操作手把手带着调整。"
    "我把陪伴养兰服务的卡片也发给您，您直接点开就能看详情和开通。"
)
MEMBERSHIP_PRODUCT_QUERY = "首单参与陪伴养兰客户"
MEMBERSHIP_PRICE_LABEL = "首单体验价"
MEMBERSHIP_ADDITIONAL_DISCOUNT_STATUS = "unavailable"
ORCHID_PRODUCT_MARKERS = (
    "兰花",
    "国兰",
    "建兰",
    "春兰",
    "蕙兰",
    "墨兰",
    "寒兰",
    "春剑",
    "莲瓣兰",
    "四季兰",
    "蝴蝶兰",
    "石斛兰",
    "兜兰",
)
EXPLICIT_ORCHID_PRODUCT_MARKERS = tuple(
    marker for marker in ORCHID_PRODUCT_MARKERS if marker not in {"兰花", "国兰"}
)
LIVE_ORCHID_PRODUCT_MARKERS = (
    "兰苗",
    "种苗",
    "苗子",
    "兰株",
    "盆栽",
    "裸根",
    "带花",
    "花苞",
)
NON_ORCHID_PRODUCT_MARKERS = (
    "花盆",
    "紫砂盆",
    "植料",
    "基质",
    "营养土",
    "肥料",
    "工具",
    "会员",
    "服务",
    "课程",
    "挂画",
    "字画",
    "国画",
    "装饰画",
    "画框",
    "摆件",
    "仿真",
    "假花",
    "香薰",
    "精油",
    "茶叶",
    "食品",
)
MEMBERSHIP_CAPABILITY_MARKERS = (
    "服务",
    "权益",
    "包含",
    "老师",
    "一对一",
    "指导",
    "具体情况",
    "看我这盆",
    "帮我看",
)
MEMBERSHIP_PRICE_MARKERS = ("多少钱", "价格", "费用", "收费", "几元", "多少元")
MEMBERSHIP_PURCHASE_MARKERS = (
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
    "加入会员吗",
    "开通会员吗",
)


def verified_membership_brand_facts() -> list[dict]:
    """Expose stable brand/service facts without depending on catalog availability."""

    product_id = ""
    try:
        products = search_catalog_products("会员", limit=3)
        if not products:
            products = search_catalog_products("陪伴养兰", limit=3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Membership value lookup failed: %s", type(exc).__name__)
        products = []
    if products:
        product_id = str(products[0].get("item_id") or "")
    return [
        {
            "brand": "萧岚苑",
            "product_type": "陪伴养兰会员",
            "product_id": product_id,
            "service_capabilities": list(MEMBERSHIP_SERVICE_CAPABILITIES),
            "service_value_points": [
                dict(item) for item in MEMBERSHIP_SERVICE_VALUE_POINTS
            ],
            "brand_positioning": MEMBERSHIP_BRAND_POSITIONING,
            "approved_sales_scripts": {
                "care_pain": MEMBERSHIP_CARE_PAIN_SALES_SCRIPT,
                "care_question_followup": MEMBERSHIP_CARE_QUESTION_FOLLOWUP_SCRIPT,
                "soft_decline_value": MEMBERSHIP_SOFT_DECLINE_VALUE_SCRIPT,
            },
        }
    ]


async def build_commerce_context(
    message,
    user_state,
    intent,
    *,
    product_service=None,
    order_service=None,
    mini_program_base: dict | None = None,
    order_card: dict | None = None,
    identity_store=None,
    business_action: str | None = None,
    allowed_source_groups: Collection[str] | None = None,
) -> BusinessFacts:
    commerce_type = _commerce_type(
        intent,
        user_state,
        business_action=business_action,
    )
    if not commerce_type:
        return BusinessFacts()
    if allowed_source_groups is not None:
        required_source = "product_catalog" if commerce_type == "product" else "order_facts"
        if required_source not in allowed_source_groups:
            return BusinessFacts()

    settings = get_settings()
    evaluation_order_fixture = (
        _evaluation_order_fixture(message) if commerce_type == "order" else None
    )
    if (
        commerce_type != "product"
        and order_service is None
        and evaluation_order_fixture is None
    ):
        if not settings.youzan_enabled or not youzan_credentials_available():
            return BusinessFacts()
        client = create_managed_youzan_client()
        order_service = YouzanOrderService(
            client,
            method=settings.youzan_order_search_method,
            version=settings.youzan_order_search_version,
            customer_method=settings.youzan_customer_get_method,
            customer_version=settings.youzan_customer_get_version,
            follower_method=settings.youzan_follower_get_method,
            follower_version=settings.youzan_follower_get_version,
            detail_enabled=settings.youzan_order_detail_enabled,
            detail_method=settings.youzan_order_detail_method,
            detail_version=settings.youzan_order_detail_version,
            logistics_enabled=settings.youzan_logistics_enabled,
            logistics_method=settings.youzan_logistics_method,
            logistics_version=settings.youzan_logistics_version,
        )
        identity_store = identity_store or YouzanIdentityStore()

    base_card = mini_program_base or _mini_program_base(settings)
    if commerce_type == "product":
        if business_action == SELECTED_PRODUCT_DETAIL:
            return await _selected_product_facts(
                message=message,
                user_state=user_state,
                product_service=product_service,
                base_card=base_card,
                allowed_source_groups=allowed_source_groups,
            )
        product_keywords = [
            str(value).strip()
            for value in (
                intent.slots.get("product_keywords", [])
                if isinstance(intent.slots, dict)
                else []
            )
            if str(value).strip()
        ]
        product_request_kind = str(
            intent.slots.get("product_request_kind") or ""
        ).strip()
        last_product_kind = str(
            user_state.metadata.get("commerce_last_product_kind") or ""
        ).strip()
        last_product_keyword = str(
            user_state.metadata.get("commerce_last_product_keyword") or ""
        ).strip()
        continues_selected_product = bool(
            not product_request_kind
            and user_state.metadata.get("commerce_last_product_id")
            and last_product_kind in {"membership", "matched_orchid"}
            and (
                intent.primary_intent in {"payment_intent", "order_intent"}
                or intent.primary_intent
                in {"price_objection", "discount_request", "hesitation"}
                or _explicitly_requests_purchase_card(message.message)
                or (
                    product_keywords
                    and last_product_keyword
                    and all(
                        keyword == last_product_keyword
                        for keyword in product_keywords
                    )
                )
            )
        )
        if continues_selected_product:
            product_request_kind = last_product_kind
        if product_request_kind and product_request_kind not in {
            "membership",
            "matched_orchid",
        }:
            return BusinessFacts()
        if (
            not product_keywords
            and product_request_kind == last_product_kind
            and last_product_keyword
        ):
            product_keywords = [last_product_keyword]
        membership_request = product_request_kind == "membership"
        if not product_request_kind:
            product_request_kind = "matched_orchid"
        keyword = (
            MEMBERSHIP_PRODUCT_QUERY
            if membership_request
            else (
                product_keywords[0]
                if product_keywords
                else _orchid_catalog_query(
                    message.message,
                    intent.slots,
                    user_state.metadata,
                )
            )
        )
        if business_action == CATALOG_SEARCH and not membership_request:
            keyword = _orchid_catalog_query(
                message.message,
                intent.slots,
                user_state.metadata,
            )
        if not keyword:
            if intent.primary_intent == "order_intent":
                return BusinessFacts()
            return BusinessFacts(
                tool_state={
                    "commerce_type": "product",
                    "status": "missing_product",
                }
            )
        try:
            product_data = []
            for search_keyword in product_keywords or [keyword]:
                search_limit = (
                    3
                    if membership_request
                    else (1 if product_keywords else 3)
                )
                if product_service is not None:
                    products = await product_service.search(
                        search_keyword,
                        limit=search_limit,
                    )
                    matches = [product.model_dump() for product in products]
                else:
                    matches = search_catalog_products(
                        search_keyword,
                        limit=search_limit,
                    )
                for product in matches:
                    item_id = str(product.get("item_id") or "")
                    if item_id and any(
                        str(existing.get("item_id") or "") == item_id
                        for existing in product_data
                    ):
                        continue
                    product_data.append(product)
            product_data = [
                product
                for product in product_data
                if _is_allowed_ai_product(
                    product,
                    product_request_kind=product_request_kind,
                )
            ]
            product_data = _prefer_requested_price(
                product_data,
                message.message,
            )
            if allowed_source_groups is not None:
                product_data = _restrict_product_data(
                    product_data,
                    set(allowed_source_groups),
                    include_sku_facts=membership_request,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Local product catalog query failed: %s", type(exc).__name__)
            return BusinessFacts(
                tool_state={"commerce_type": "product", "status": "unavailable"}
            )
        if membership_request:
            if intent.slots.get("service_offer_followup") == "value_card":
                membership_question_kind = "purchase"
            elif intent.primary_intent in {
                "price_objection",
                "discount_request",
                "hesitation",
            }:
                membership_question_kind = "objection"
            else:
                membership_question_kind = (
                    str(
                        intent.slots.get("membership_question_kind") or ""
                    ).strip()
                    or _membership_question_kind(message.message)
                )
        else:
            membership_question_kind = None
        send_purchase_card = bool(product_data)
        if send_purchase_card and not any(
            str(product.get("page_path") or product.get("h5_url") or "").strip()
            for product in product_data
            if isinstance(product, dict)
        ):
            send_purchase_card = False
        first_product_id = (
            str(product_data[0].get("item_id") or "").strip()
            if product_data
            else ""
        )
        previous_purchase_card_available = bool(
            first_product_id
            and _card_already_sent(user_state.metadata, first_product_id)
        )
        explicitly_requested_card = (
            _explicitly_requests_purchase_card(message.message)
            or (
                membership_request
                and membership_question_kind in {"purchase", "combined"}
            )
        )
        if (
            send_purchase_card
            and first_product_id
            and previous_purchase_card_available
            and not explicitly_requested_card
        ):
            send_purchase_card = False
        tool_state = {
            "commerce_type": "product",
            "status": "found" if product_data else "not_found",
            "query_performed": True,
            "products": product_data,
            "send_product_image": _wants_product_image(message.message),
            "product_request_kind": product_request_kind or None,
            "requested_product_keywords": product_keywords,
            "send_all_product_cards": (
                len(product_keywords) > 1
                or (
                    business_action == CATALOG_SEARCH
                    and _wants_multiple_products(keyword)
                )
            ),
            "requested_capabilities": _requested_capabilities(message.message),
            "membership_question_kind": membership_question_kind,
            "send_purchase_card": send_purchase_card,
            "previous_purchase_card_available": previous_purchase_card_available,
            "business_action": business_action,
        }
        if membership_request and product_data:
            tool_state["brand"] = "萧岚苑"
            tool_state["service_capabilities"] = list(
                MEMBERSHIP_SERVICE_CAPABILITIES
            )
            tool_state["service_value_points"] = [
                dict(item) for item in MEMBERSHIP_SERVICE_VALUE_POINTS
            ]
            tool_state["brand_positioning"] = MEMBERSHIP_BRAND_POSITIONING
            tool_state["approved_sales_scripts"] = {
                "care_pain": MEMBERSHIP_CARE_PAIN_SALES_SCRIPT,
                "care_question_followup": MEMBERSHIP_CARE_QUESTION_FOLLOWUP_SCRIPT,
                "soft_decline_value": MEMBERSHIP_SOFT_DECLINE_VALUE_SCRIPT,
            }
            tool_state["price_label"] = MEMBERSHIP_PRICE_LABEL
            tool_state["additional_discount_status"] = (
                MEMBERSHIP_ADDITIONAL_DISCOUNT_STATUS
            )
            tool_state["negotiation_allowed"] = False
            if membership_question_kind == "objection":
                tool_state["membership_objection_round"] = (
                    "followup"
                    if _has_prior_membership_price_objection(
                        user_state.metadata.get("recent_turns")
                    )
                    else "initial"
                )
        if product_data and product_data[0].get("page_path"):
            tool_state["mini_program"] = {
                **base_card,
                "page_path": product_data[0]["page_path"],
                "thumb_url": product_data[0].get("image_url", ""),
                "title": product_data[0].get("title", ""),
            }
        if send_purchase_card and first_product_id:
            sent_ids = (
                list(user_state.metadata.get("commerce_sent_card_ids"))
                if isinstance(
                    user_state.metadata.get("commerce_sent_card_ids"),
                    list,
                )
                else []
            )
            if first_product_id not in sent_ids:
                sent_ids.append(first_product_id)
            user_state.metadata["commerce_sent_card_ids"] = sent_ids[-20:]
        if (
            product_data
            and product_request_kind in {"membership", "matched_orchid"}
        ):
            first_product = product_data[0]
            user_state.metadata["commerce_last_product_keyword"] = str(
                first_product.get("title") or keyword
            ).strip()
            user_state.metadata["commerce_last_product_id"] = str(
                first_product.get("item_id") or ""
            ).strip()
            user_state.metadata["commerce_last_product_kind"] = str(
                product_request_kind
            ).strip()
            if business_action == CATALOG_SEARCH:
                user_state.metadata["commerce_last_catalog_query"] = keyword
        return BusinessFacts(tool_state=tool_state)

    if order_service is None and evaluation_order_fixture is None:
        return BusinessFacts()

    active_task = user_state.metadata.get("active_task")
    active_task = active_task if isinstance(active_task, dict) else {}
    requested_action = str(
        intent.slots.get("order_action")
        or (
            active_task.get("action")
            if active_task.get("domain") == "order"
            else ""
        )
        or ""
    ).strip()
    user_state.metadata["active_task"] = {
        **active_task,
        "domain": "order",
        "task_type": "order_query",
        "status": str(active_task.get("status") or "querying"),
        **({"action": requested_action} if requested_action else {}),
    }
    tenant_id = str(getattr(message, "tenant_id", "tenant_default") or "tenant_default")
    external_user_id = str(getattr(message, "user_id", "") or "")
    binding = None
    if identity_store is not None and external_user_id:
        binding = identity_store.get(
            tenant_id=tenant_id,
            channel=str(getattr(message, "channel", "wechat") or "wechat"),
            external_user_id=external_user_id,
            kdt_id=settings.youzan_kdt_id,
        )
    explicit_mobile = _mobile_from(message.message)
    mobile = explicit_mobile or (
        ""
        if binding is not None
        else (
            str(user_state.metadata.get("commerce_mobile") or "")
            or _mobile_from_profile(user_state.metadata.get("profile"))
            or _mobile_from_recent_turns(user_state.metadata.get("recent_turns"))
        )
    )
    if not mobile and binding is None:
        user_state.metadata["commerce_pending"] = "order_mobile"
        user_state.metadata["active_task"]["status"] = "awaiting_identity"
        tool_state = {"commerce_type": "order", "status": "missing_mobile"}
        if requested_action:
            tool_state["requested_action"] = requested_action
            tool_state["requested_action_executed"] = False
        return BusinessFacts(tool_state=tool_state)

    if evaluation_order_fixture is not None:
        return _evaluation_order_facts(
            fixture=evaluation_order_fixture,
            mobile=mobile,
            user_state=user_state,
            order_card=order_card or _order_card(settings),
            requested_action=requested_action,
        )

    lookup_identity = binding
    identity_source = ""
    try:
        if mobile:
            user_state.metadata["commerce_mobile"] = mobile
            if hasattr(order_service, "lookup_by_mobile"):
                lookup = await order_service.lookup_by_mobile(mobile, limit=3)
                lookup_identity = lookup.identity
                orders = lookup.orders
                identity_source = "mobile_verified"
            else:
                orders = await order_service.search_by_mobile(mobile, limit=3)
        elif binding is not None and hasattr(order_service, "search_by_identity"):
            orders = await order_service.search_by_identity(binding, limit=3)
        elif _is_official_wechat_message(message) and hasattr(
            order_service, "lookup_by_weixin_openid"
        ):
            lookup = await order_service.lookup_by_weixin_openid(
                external_user_id,
                limit=3,
            )
            lookup_identity = lookup.identity
            orders = lookup.orders
            identity_source = "official_wechat_openid"
        else:
            user_state.metadata["active_task"]["status"] = "query_failed"
            return BusinessFacts(
                tool_state={"commerce_type": "order", "status": "unavailable"}
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Youzan order query failed: %s", type(exc).__name__)
        user_state.metadata["active_task"] = {
            **user_state.metadata.get("active_task", {}),
            "domain": "order",
            "task_type": "order_query",
            "status": "query_failed",
            "last_result_status": "unavailable",
            "last_queried_at": datetime.now(timezone.utc).isoformat(),
        }
        return BusinessFacts(
            tool_state={"commerce_type": "order", "status": "unavailable"}
        )
    user_state.metadata.pop("commerce_pending", None)
    if (
        identity_store is not None
        and lookup_identity is not None
        and identity_source
        and external_user_id
        and any(
            getattr(lookup_identity, field, "")
            for field in ("buyer_id", "yz_uid", "yz_open_id", "fans_id", "weixin_openid")
        )
    ):
        identity_store.upsert(
            tenant_id=tenant_id,
            channel=str(getattr(message, "channel", "wechat") or "wechat"),
            external_user_id=external_user_id,
            kdt_id=settings.youzan_kdt_id,
            identity=lookup_identity,
            source=identity_source,
        )
    tool_state = {
        "commerce_type": "order",
        "status": "found" if orders else "not_found",
        "lookup_performed": True,
        "mobile_masked": (
            str(getattr(lookup_identity, "mobile_masked", "") or "")
            or _mask_mobile(mobile)
        ),
        "orders": [order.model_dump() for order in orders],
    }
    user_state.metadata["active_task"] = {
        **user_state.metadata.get("active_task", {}),
        "domain": "order",
        "task_type": "order_query",
        "status": "completed" if orders else "awaiting_order_evidence",
        "last_result_status": tool_state["status"],
        "last_queried_at": datetime.now(timezone.utc).isoformat(),
    }
    if requested_action:
        tool_state["requested_action"] = requested_action
        tool_state["requested_action_executed"] = False
        user_state.metadata["active_task"] = {
            **user_state.metadata.get("active_task", {}),
            "domain": "order",
            "action": requested_action,
            "status": "verified_requires_human",
            "order_nos": [
                str(order.get("order_no") or "")
                for order in tool_state["orders"]
                if isinstance(order, dict) and order.get("order_no")
            ],
        }
    configured_order_card = order_card or _order_card(settings)
    if configured_order_card.get("page_path"):
        tool_state["mini_program"] = configured_order_card
    return BusinessFacts(tool_state=tool_state)


def _commerce_type(
    intent,
    user_state=None,
    *,
    business_action: str | None = None,
) -> str:
    if business_action == ORDER_VERIFY:
        return "order"
    if business_action in {CATALOG_SEARCH, SELECTED_PRODUCT_DETAIL}:
        return "product"
    if business_action:
        return ""
    primary_intent = str(getattr(intent, "primary_intent", "") or "")
    if primary_intent in {
        "product_query",
        "product_recommendation",
        "recommend_product",
        "order_intent",
    }:
        return "product"
    if primary_intent == "order_query":
        return "order"
    if (
        primary_intent == "payment_intent"
        and user_state is not None
        and getattr(user_state, "metadata", {}).get("commerce_last_product_keyword")
    ):
        return "product"
    slots = getattr(intent, "slots", {})
    if isinstance(slots, dict) and slots.get(
        "conversation_topic"
    ) == "product_recommendation":
        return "product"
    if (
        getattr(intent, "primary_domain", None) == "product"
        and getattr(intent, "primary_goal", None) == "seek_help"
        and "product_selection" in (getattr(intent, "issues", []) or [])
    ):
        return "product"
    return ""


async def _selected_product_facts(
    *,
    message,
    user_state,
    product_service,
    base_card: dict,
    allowed_source_groups: Collection[str] | None,
) -> BusinessFacts:
    item_id = str(user_state.metadata.get("commerce_last_product_id") or "").strip()
    if not item_id:
        return BusinessFacts(
            tool_state={
                "commerce_type": "product",
                "status": "missing_product",
                "business_action": SELECTED_PRODUCT_DETAIL,
            }
        )
    try:
        if product_service is not None and hasattr(product_service, "get"):
            product = await product_service.get(item_id)
            product_data = (
                product.model_dump()
                if hasattr(product, "model_dump")
                else dict(product)
            )
        else:
            product_data = get_catalog_product(item_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Selected product detail query failed: %s", type(exc).__name__)
        return BusinessFacts(
            tool_state={
                "commerce_type": "product",
                "status": "unavailable",
                "business_action": SELECTED_PRODUCT_DETAIL,
            }
        )
    if not isinstance(product_data, dict):
        return BusinessFacts(
            tool_state={
                "commerce_type": "product",
                "status": "not_found",
                "business_action": SELECTED_PRODUCT_DETAIL,
                "query_performed": True,
            }
        )
    selected_kind = str(
        user_state.metadata.get("commerce_last_product_kind") or "matched_orchid"
    ).strip()
    if not _is_allowed_ai_product(
        product_data,
        product_request_kind=selected_kind,
    ):
        return BusinessFacts()
    if allowed_source_groups is not None:
        products = _restrict_product_data(
            [product_data],
            set(allowed_source_groups),
            include_sku_facts=True,
        )
        product_data = products[0] if products else {}
    tool_state = {
        "commerce_type": "product",
        "status": "found" if product_data else "not_found",
        "query_performed": True,
        "products": [product_data] if product_data else [],
        "product_request_kind": "selected_product_detail",
        "business_action": SELECTED_PRODUCT_DETAIL,
        "detail_question": str(message.message or ""),
        "send_all_product_cards": False,
        "send_purchase_card": _explicitly_requests_purchase_card(message.message),
    }
    if product_data.get("page_path"):
        tool_state["mini_program"] = {
            **base_card,
            "page_path": product_data["page_path"],
            "thumb_url": product_data.get("image_url", ""),
            "title": product_data.get("title", ""),
        }
    return BusinessFacts(tool_state=tool_state)


def _evaluation_order_fixture(message) -> dict | None:
    metadata = getattr(message, "metadata", {})
    if not isinstance(metadata, dict) or not metadata.get("evaluation_id"):
        return None
    fixture = metadata.get("tool_state")
    if not isinstance(fixture, dict) or fixture.get("fixture_type") != "order":
        return None
    return fixture


def _evaluation_order_facts(
    *,
    fixture: dict,
    mobile: str,
    user_state,
    order_card: dict,
    requested_action: str,
) -> BusinessFacts:
    expected_mobile = str(fixture.get("mobile") or "").strip()
    raw_orders = fixture.get("orders")
    raw_orders = raw_orders if isinstance(raw_orders, list) else []
    orders = []
    if not expected_mobile or expected_mobile == mobile:
        for raw_order in raw_orders:
            if not isinstance(raw_order, dict):
                continue
            try:
                orders.append(YouzanOrderSummary.model_validate(raw_order))
            except ValueError:
                logger.warning("Invalid evaluation order fixture ignored")

    user_state.metadata["commerce_mobile"] = mobile
    user_state.metadata.pop("commerce_pending", None)
    tool_state = {
        "commerce_type": "order",
        "status": "found" if orders else "not_found",
        "lookup_performed": True,
        "mobile_masked": _mask_mobile(mobile),
        "orders": [order.model_dump() for order in orders],
        "fixture_used": True,
    }
    if requested_action:
        tool_state["requested_action"] = requested_action
        tool_state["requested_action_executed"] = False
        user_state.metadata["active_task"] = {
            **(
                user_state.metadata.get("active_task")
                if isinstance(user_state.metadata.get("active_task"), dict)
                else {}
            ),
            "domain": "order",
            "action": requested_action,
            "status": "verified_requires_human",
            "order_nos": [
                str(order.order_no)
                for order in orders
                if getattr(order, "order_no", None)
            ],
        }
    if order_card.get("page_path"):
        tool_state["mini_program"] = order_card
    return BusinessFacts(tool_state=tool_state)


def _catalog_query(text: str, keyword: str, metadata: dict) -> str:
    current = str(text or "").strip()
    previous = str(metadata.get("commerce_last_catalog_query") or "").strip()
    if previous and _looks_like_budget(current):
        return f"{previous}；{current}"
    return keyword or current


def _orchid_catalog_query(text: str, slots: dict, metadata: dict) -> str:
    """Build a catalog query without sending the raw customer message."""

    current = _product_keyword(
        text,
        slots,
        metadata.get("recent_turns"),
    )
    previous = str(metadata.get("commerce_last_catalog_query") or "").strip()
    if previous and _looks_like_budget(current):
        return f"{previous}；{current}"
    if current:
        return current
    return str(metadata.get("commerce_last_product_keyword") or "").strip()


def _is_allowed_ai_product(
    product: dict,
    *,
    product_request_kind: str,
) -> bool:
    title = str(product.get("title") or "").strip()
    knowledge = product.get("knowledge")
    knowledge = knowledge if isinstance(knowledge, dict) else {}
    product_name = str(knowledge.get("product_name") or "").strip()
    category = str(knowledge.get("category") or "").strip()
    searchable = " ".join(
        (
            title,
            product_name,
            category,
            str(knowledge.get("aliases") or ""),
        )
    )
    if product_request_kind == "membership":
        return MEMBERSHIP_PRODUCT_QUERY in searchable
    if product_request_kind != "matched_orchid":
        return False
    if any(marker in searchable for marker in NON_ORCHID_PRODUCT_MARKERS):
        return False
    if category and any(marker in category for marker in ORCHID_PRODUCT_MARKERS):
        return True
    named_product_text = " ".join((title, product_name))
    if any(marker in named_product_text for marker in EXPLICIT_ORCHID_PRODUCT_MARKERS):
        return True
    return bool(
        any(marker in named_product_text for marker in ("兰花", "国兰"))
        and any(marker in named_product_text for marker in LIVE_ORCHID_PRODUCT_MARKERS)
    )


def _membership_question_kind(text: str) -> str:
    value = str(text or "")
    asks_capability = any(marker in value for marker in MEMBERSHIP_CAPABILITY_MARKERS)
    asks_price = any(marker in value for marker in MEMBERSHIP_PRICE_MARKERS) or bool(
        PRICE_YUAN_PATTERN.search(value)
    )
    asks_purchase = any(marker in value for marker in MEMBERSHIP_PURCHASE_MARKERS)
    if asks_purchase and (asks_capability or asks_price):
        return "combined"
    if asks_purchase:
        return "purchase"
    if asks_capability:
        return "capability"
    if asks_price:
        return "price"
    return "capability"


def _explicitly_requests_purchase_card(text: str) -> bool:
    value = str(text or "")
    return any(
        marker in value
        for marker in (
            "链接",
            "入口",
            "发我",
            "购买",
            "下单",
            "付款",
            "支付",
            "怎么进",
            "如何进",
            "怎么加入",
            "如何加入",
            "怎么开通",
            "如何开通",
        )
    )


def _card_already_sent(metadata: dict, item_id: str) -> bool:
    sent_ids = metadata.get("commerce_sent_card_ids")
    return isinstance(sent_ids, list) and item_id in {
        str(value) for value in sent_ids
    }


_MEMBERSHIP_PRICE_OBJECTION_MARKERS = (
    "贵",
    "便宜",
    "优惠",
    "打折",
    "少一点",
    "少点",
    "降一点",
    "降点",
    "再少",
    "再低",
)


def _has_prior_membership_price_objection(value) -> bool:
    if not isinstance(value, list):
        return False
    for turn in value[-6:]:
        if not isinstance(turn, dict):
            continue
        if str(turn.get("role") or "") not in {"user", "customer"}:
            continue
        content = str(turn.get("content") or turn.get("text") or "")
        if any(marker in content for marker in _MEMBERSHIP_PRICE_OBJECTION_MARKERS):
            return True
    return False


def _looks_like_budget(text: str) -> bool:
    return bool(
        re.search(
            r"预算|性价比|便宜|实惠|划算|"
            r"\d+(?:\.\d+)?\s*元?\s*(?:以内|以下|之内|不超过|最多|左右|上下)|"
            r"[一二三四五六七八九十百两]{1,8}\s*(?:元|块)?"
            r"(?:以内|以下|之内|不超过|最多|左右|上下)",
            str(text or ""),
        )
    )


def _wants_multiple_products(text: str) -> bool:
    return bool(re.search(r"几款|几种|多款|多个|推荐.{0,6}(?:款|种)", str(text or "")))


def _prefer_requested_price(products: list[dict], text: str) -> list[dict]:
    match = PRICE_YUAN_PATTERN.search(str(text or ""))
    if match is None:
        match = re.fullmatch(
            r"\s*(\d+(?:\.\d{1,2})?)\s*(?:元|块)?(?:是吗|对吗|吗)?[？?]?\s*",
            str(text or ""),
        )
    if not match or len(products) < 2:
        return products
    target_price_cent = round(float(match.group(1)) * 100)
    return sorted(
        products,
        key=lambda product: (
            product.get("price_cent") != target_price_cent,
        ),
    )


def _restrict_product_data(
    products: list[dict],
    allowed_source_groups: set[str],
    *,
    include_sku_facts: bool = False,
) -> list[dict]:
    allowed_knowledge_fields = set(CATALOG_KNOWLEDGE_FIELDS)
    if "product_value" in allowed_source_groups:
        allowed_knowledge_fields.update(VALUE_KNOWLEDGE_FIELDS)
    if "sku_facts" in allowed_source_groups:
        allowed_knowledge_fields.update(SKU_KNOWLEDGE_FIELDS)

    result = []
    for product in products:
        if not isinstance(product, dict):
            continue
        restricted = {
            key: value
            for key, value in product.items()
            if key
            in {
                "item_id",
                "title",
                "alias",
                "image_url",
                "image_urls",
                "knowledge",
                # Purchase navigation is a delivery capability, not a SKU fact.
                # It must survive early-stage price/stock restrictions so a matched
                # product can always be sent as its corresponding message card.
                "page_path",
                "h5_url",
            }
        }
        knowledge = product.get("knowledge")
        if isinstance(knowledge, dict):
            restricted["knowledge"] = {
                key: value
                for key, value in knowledge.items()
                if key in allowed_knowledge_fields
            }
        if include_sku_facts or "sku_facts" in allowed_source_groups:
            for key in ("price_cent", "stock", "skus"):
                if key in product:
                    restricted[key] = product[key]
        result.append(restricted)
    return result


def _mobile_from(text: str) -> str:
    match = MOBILE_PATTERN.search(text or "")
    return match.group(0) if match else ""


def _requested_capabilities(text: str) -> list[str]:
    capabilities = []
    if any(marker in str(text or "") for marker in ("视频", "教程", "课程")):
        capabilities.append("video_tutorial")
    if any(
        marker in str(text or "")
        for marker in ("老师", "一对一", "指导", "具体情况", "看我这盆", "帮我看")
    ):
        capabilities.append("one_to_one_guidance")
    return capabilities


def _mask_mobile(mobile: str) -> str:
    return f"{mobile[:3]}****{mobile[-4:]}" if len(mobile) == 11 else mobile


def _mobile_from_recent_turns(value) -> str:
    if not isinstance(value, list):
        return ""
    for turn in reversed(value):
        if not isinstance(turn, dict) or turn.get("role") not in {"user", "customer"}:
            continue
        mobile = _mobile_from(str(turn.get("content") or ""))
        if mobile:
            return mobile
    return ""


def _mobile_from_profile(value) -> str:
    if not isinstance(value, dict):
        return ""
    basic_info = value.get("basic_info")
    if not isinstance(basic_info, dict):
        return ""
    return _mobile_from(str(basic_info.get("mobile") or ""))


def _product_keyword(text: str, slots: dict, recent_turns=None) -> str:
    for key in ("product_keyword", "product", "name"):
        value = slots.get(key) if isinstance(slots, dict) else None
        if isinstance(value, str) and value.strip():
            keyword = _clean_product_keyword(value)
            if keyword:
                return keyword
    keyword = _clean_product_keyword(text)
    if keyword:
        return keyword
    if isinstance(recent_turns, list):
        for turn in reversed(recent_turns):
            if not isinstance(turn, dict):
                continue
            content = str(turn.get("content") or "")
            if turn.get("role") in {"assistant", "ai"}:
                keyword = _assistant_product_keyword(content)
            elif turn.get("role") in {"user", "customer"}:
                keyword = _clean_product_keyword(content)
            else:
                continue
            if keyword:
                return keyword
    return ""


def _clean_product_keyword(text: str) -> str:
    keyword = str(text or "").strip()
    normalized = re.sub(r"[\s，,。！？!?]", "", keyword)
    if PRODUCT_REFERENCE_ORDER_PATTERN.fullmatch(normalized):
        return ""
    keyword = re.sub(
        r"^(?:给我|帮我)?(?:发)?(?:产品|商品|购买|下单)?链接"
        r"(?:在哪里|在哪儿|在哪)?[，,。！？!?\s]*",
        "",
        keyword,
    )
    keyword = re.sub(
        r"^(?:那|那么)?(?:我)?(?:想|要|准备|决定|就|直接)*(?:买|购买|下单)(?:一下)?",
        "",
        keyword,
    )
    keyword = re.sub(
        r"^(?:请问|麻烦|帮我|我想买|我想要|我想看看|我想看|我对|有没有)+",
        "",
        keyword,
    )
    keyword = re.sub(r"(?:比较)?感兴趣[。！？!?]*$", "", keyword)
    keyword = re.sub(
        r"[，,。！？!?\s]*(?:发我|发一下|给我|帮我发)?(?:一下)?(?:商品|购买|下单)?链接[。！？!?]*$",
        "",
        keyword,
    )
    keyword = re.sub(
        r"[，,。！？!?\s]*(?:商品|购买|下单)?链接"
        r"(?:在哪里|在哪儿|在哪|呢|吗)?[。！？!?]*$",
        "",
        keyword,
    )
    keyword = re.sub(
        r"[，,。！？!?\s]*(?:怎么|如何|在哪|哪里)?"
        r"(?:下单|购买|买|拍下)(?:呢|吗)?[。！？!?]*$",
        "",
        keyword,
    )
    keyword = re.sub(
        r"[，,。！？!?\s]*(?:的)?(?:有)?(?:商品)?"
        r"(?:图片|照片|实拍图|商品图|图册|图集|相册)"
        r"(?:可以吗|有吗|吗|呢)?[。！？!?]*$",
        "",
        keyword,
    )
    keyword = re.sub(r"[，,。！？!?\s]*(?:长什么样|有图吗)[。！？!?]*$", "", keyword)
    keyword = re.sub(
        r"^(?:(?:能不能|可以|能)?(?:给我)?(?:发|看看?|看)(?:一下|一张|张)?)",
        "",
        keyword,
    )
    keyword = re.sub(r"[？?]+$", "", keyword)
    keyword = keyword.strip()
    return "" if keyword in PRODUCT_REFERENCE_WORDS else keyword


def _assistant_product_keyword(text: str) -> str:
    value = str(text or "").strip()
    for pattern in (
        r"这是(.{1,40}?)的商品图片",
        r"推荐您看看(.{1,40}?)(?:，当前售价|，|。)",
    ):
        match = re.search(pattern, value)
        if match:
            return match.group(1).strip()
    return ""


def _wants_product_image(text: str) -> bool:
    value = str(text or "")
    return any(word in value for word in PRODUCT_IMAGE_REQUEST_WORDS)


def _mini_program_base(settings) -> dict[str, str]:
    return {
        "display_name": settings.youzan_mini_program_display_name,
        "app_id": settings.youzan_mini_program_app_id,
        "user_name": settings.youzan_mini_program_user_name,
        "icon_url": settings.youzan_mini_program_icon_url,
    }


def _order_card(settings) -> dict[str, str]:
    return {
        **_mini_program_base(settings),
        "page_path": settings.youzan_order_page_path,
        "thumb_url": settings.youzan_order_card_thumb_url,
        "title": settings.youzan_order_card_title,
    }


def _is_official_wechat_message(message) -> bool:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    return bool(metadata.get("wechat_to_user")) and metadata.get("provider") != "eyun"
