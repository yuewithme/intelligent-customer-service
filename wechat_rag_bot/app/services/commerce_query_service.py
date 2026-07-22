import logging
import re
from collections.abc import Collection

from app.config import get_settings
from app.integrations.youzan.client import YouzanClient
from app.schemas.reply_plan import BusinessFacts
from app.services.youzan_identity_store import YouzanIdentityStore
from app.services.youzan_order_service import YouzanOrderService
from app.services.product_knowledge_service import search_catalog_products


MOBILE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
logger = logging.getLogger("wechat_rag_bot.commerce_query")
PRODUCT_IMAGE_REQUEST_WORDS = (
    "图片",
    "照片",
    "商品图",
    "实拍图",
    "发张图",
    "长什么样",
)
PRODUCT_REFERENCE_WORDS = {"这款", "这个", "刚才那款", "刚刚那款", "上面那款"}
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
    allowed_source_groups: Collection[str] | None = None,
) -> BusinessFacts:
    commerce_type = _commerce_type(intent.primary_intent)
    if not commerce_type:
        return BusinessFacts()
    if allowed_source_groups is not None:
        required_source = "product_catalog" if commerce_type == "product" else "order_facts"
        if required_source not in allowed_source_groups:
            return BusinessFacts()

    settings = get_settings()
    if commerce_type != "product" and order_service is None:
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
        keyword = _product_keyword(
            message.message,
            intent.slots,
            user_state.metadata.get("recent_turns"),
        )
        if not keyword:
            return BusinessFacts(
                tool_state={
                    "commerce_type": "product",
                    "status": "missing_product",
                }
            )
        try:
            if product_service is not None:
                products = await product_service.search(keyword, limit=3)
                product_data = [product.model_dump() for product in products]
            else:
                product_data = search_catalog_products(keyword, limit=3)
            if allowed_source_groups is not None:
                product_data = _restrict_product_data(
                    product_data,
                    set(allowed_source_groups),
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Local product catalog query failed: %s", type(exc).__name__)
            return BusinessFacts(
                tool_state={"commerce_type": "product", "status": "unavailable"}
            )
        tool_state = {
            "commerce_type": "product",
            "status": "found" if product_data else "not_found",
            "products": product_data,
            "send_product_image": _wants_product_image(message.message),
        }
        if product_data and product_data[0].get("page_path"):
            tool_state["mini_program"] = {
                **base_card,
                "page_path": product_data[0]["page_path"],
                "thumb_url": product_data[0].get("image_url", ""),
                "title": product_data[0].get("title", ""),
            }
        return BusinessFacts(tool_state=tool_state)

    if order_service is None:
        return BusinessFacts()

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
            user_state.metadata["commerce_pending"] = "order_mobile"
            return BusinessFacts(
                tool_state={"commerce_type": "order", "status": "missing_mobile"}
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
        "mobile_masked": (
            str(getattr(lookup_identity, "mobile_masked", "") or "")
            or _mask_mobile(mobile)
        ),
        "orders": [order.model_dump() for order in orders],
    }
    configured_order_card = order_card or _order_card(settings)
    if configured_order_card.get("page_path"):
        tool_state["mini_program"] = configured_order_card
    return BusinessFacts(tool_state=tool_state)


def _commerce_type(primary_intent: str) -> str:
    if primary_intent == "product_query":
        return "product"
    if primary_intent == "order_query":
        return "order"
    return ""


def _restrict_product_data(
    products: list[dict],
    allowed_source_groups: set[str],
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
            if key in {"item_id", "title", "alias", "image_url", "knowledge"}
        }
        knowledge = product.get("knowledge")
        if isinstance(knowledge, dict):
            restricted["knowledge"] = {
                key: value
                for key, value in knowledge.items()
                if key in allowed_knowledge_fields
            }
        if "sku_facts" in allowed_source_groups:
            for key in ("price_cent", "stock", "page_path", "h5_url", "skus"):
                if key in product:
                    restricted[key] = product[key]
        result.append(restricted)
    return result


def _mobile_from(text: str) -> str:
    match = MOBILE_PATTERN.search(text or "")
    return match.group(0) if match else ""


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
            if not isinstance(turn, dict) or turn.get("role") not in {"user", "customer"}:
                continue
            keyword = _clean_product_keyword(str(turn.get("content") or ""))
            if keyword:
                return keyword
    return ""


def _clean_product_keyword(text: str) -> str:
    keyword = str(text or "").strip()
    keyword = re.sub(
        r"^(?:请问|麻烦|帮我|我想买|我想要|我想看看|我想看|有没有)+",
        "",
        keyword,
    )
    keyword = re.sub(
        r"[，,。！？!?\s]*(?:发我|发一下|给我|帮我发)?(?:一下)?(?:商品|购买|下单)?链接[。！？!?]*$",
        "",
        keyword,
    )
    keyword = re.sub(
        r"[，,。！？!?\s]*(?:的)?(?:有)?(?:商品)?(?:图片|照片|实拍图|商品图)"
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
