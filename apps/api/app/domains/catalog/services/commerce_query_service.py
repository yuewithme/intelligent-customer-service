import logging
import re
from collections.abc import Collection

from app.core.config import get_settings
from app.integrations.youzan.client import YouzanClient
from app.domains.decisioning.schemas.reply_plan import BusinessFacts
from app.integrations.youzan.services.youzan_identity_store import YouzanIdentityStore
from app.integrations.youzan.services.youzan_order_service import (
    YouzanOrderService,
    YouzanOrderSummary,
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
        if not settings.youzan_enabled or not settings.youzan_access_token.strip():
            return BusinessFacts()
        client = YouzanClient(
            access_token=settings.youzan_access_token,
            base_url=settings.youzan_base_url,
        )
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
        product_request_kind = str(
            intent.slots.get("product_request_kind")
            or (
                user_state.metadata.get("commerce_last_product_kind")
                if intent.primary_intent == "payment_intent"
                else ""
            )
            or ""
        ).strip()
        product_keywords = [
            str(value).strip()
            for value in (
                intent.slots.get("product_keywords", [])
                if isinstance(intent.slots, dict)
                else []
            )
            if str(value).strip()
        ]
        if (
            not product_keywords
            and intent.primary_intent == "payment_intent"
            and user_state.metadata.get("commerce_last_product_keyword")
        ):
            product_keywords = [
                str(user_state.metadata["commerce_last_product_keyword"]).strip()
            ]
        keyword = (
            product_keywords[0]
            if product_keywords
            else _product_keyword(
                message.message,
                intent.slots,
                user_state.metadata.get("recent_turns"),
            )
        )
        if business_action == CATALOG_SEARCH:
            keyword = _catalog_query(message.message, keyword, user_state.metadata)
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
            membership_request = product_request_kind == "membership"
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
            "business_action": business_action,
        }
        if product_data and product_data[0].get("page_path"):
            tool_state["mini_program"] = {
                **base_card,
                "page_path": product_data[0]["page_path"],
                "thumb_url": product_data[0].get("image_url", ""),
                "title": product_data[0].get("title", ""),
            }
        if (
            product_data
            and product_request_kind != "supply_shortage"
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

    requested_action = str(intent.slots.get("order_action") or "").strip()
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
            return BusinessFacts(
                tool_state={"commerce_type": "order", "status": "unavailable"}
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Youzan order query failed: %s", type(exc).__name__)
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
    if requested_action:
        tool_state["requested_action"] = requested_action
        tool_state["requested_action_executed"] = False
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
    if order_card.get("page_path"):
        tool_state["mini_program"] = order_card
    return BusinessFacts(tool_state=tool_state)


def _catalog_query(text: str, keyword: str, metadata: dict) -> str:
    current = str(text or "").strip()
    previous = str(metadata.get("commerce_last_catalog_query") or "").strip()
    if previous and _looks_like_budget(current):
        return f"{previous}；{current}"
    return keyword or current


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
