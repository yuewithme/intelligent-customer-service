from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from app.core.config import get_settings
from app.integrations.youzan.client import YouzanError
from app.integrations.youzan.services.youzan_identity_store import YouzanIdentityStore
from app.integrations.youzan.services.youzan_order_service import (
    YouzanCustomerIdentity,
    YouzanOrderLookup,
    YouzanOrderService,
)
from app.integrations.youzan.services.youzan_product_service import YouzanProductService
from app.integrations.youzan.services.youzan_token_service import (
    create_managed_youzan_client,
    youzan_credentials_available,
)
from app.domains.catalog.services.product_knowledge_service import (
    get_catalog_product,
    list_catalog_products,
    search_catalog_products,
)


logger = logging.getLogger("wechat_rag_bot.youzan_ai_tools")
MOBILE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


class YouzanAIToolService:
    """Privacy-bounded read-only tools for MCP-capable AI clients."""

    def __init__(
        self,
        *,
        product_service: YouzanProductService,
        order_service: YouzanOrderService,
        identity_store: YouzanIdentityStore,
        kdt_id: str,
        caller: str = "mcp",
    ) -> None:
        self.product_service = product_service
        self.order_service = order_service
        self.identity_store = identity_store
        self.kdt_id = kdt_id
        self.caller = caller

    @classmethod
    def from_settings(cls) -> "YouzanAIToolService":
        settings = get_settings()
        if not settings.youzan_enabled or not youzan_credentials_available():
            raise RuntimeError("Youzan read-only integration is not configured")
        client = create_managed_youzan_client()
        return cls(
            product_service=YouzanProductService(
                client,
                method=settings.youzan_product_search_method,
                version=settings.youzan_product_search_version,
                page_path_template=settings.youzan_product_page_path_template,
                h5_url_template=settings.youzan_product_h5_url_template,
                kdt_id=settings.youzan_kdt_id,
                detail_enabled=settings.youzan_product_detail_enabled,
                detail_method=settings.youzan_product_detail_method,
                detail_version=settings.youzan_product_detail_version,
                inventory_method=settings.youzan_inventory_method,
                inventory_version=settings.youzan_inventory_version,
            ),
            order_service=YouzanOrderService(
                client,
                method=settings.youzan_order_search_method,
                version=settings.youzan_order_search_version,
                customer_method=settings.youzan_customer_get_method,
                customer_version=settings.youzan_customer_get_version,
                follower_method=settings.youzan_follower_get_method,
                follower_version=settings.youzan_follower_get_version,
                detail_enabled=False,
                detail_method=settings.youzan_order_detail_method,
                detail_version=settings.youzan_order_detail_version,
                logistics_enabled=settings.youzan_logistics_enabled,
                logistics_method=settings.youzan_logistics_method,
                logistics_version=settings.youzan_logistics_version,
            ),
            identity_store=YouzanIdentityStore(),
            kdt_id=settings.youzan_kdt_id,
        )

    async def search_products(self, *, keyword: str, limit: int = 3) -> dict[str, Any]:
        keyword = keyword.strip()
        if not keyword or len(keyword) > 100:
            return await self._invalid(
                "youzan_search_products",
                parameters={"keyword": keyword[:100], "limit": limit},
                message="keyword must contain 1 to 100 characters",
            )
        if not 1 <= limit <= 10:
            return await self._invalid(
                "youzan_search_products",
                parameters={"keyword": keyword, "limit": limit},
                message="limit must be between 1 and 10",
            )

        async def operation() -> tuple[dict[str, Any], int]:
            products = search_catalog_products(keyword, limit=limit)
            return {"products": products}, len(products)

        return await self._execute(
            "youzan_search_products",
            parameters={"keyword": keyword, "limit": limit},
            operation=operation,
        )

    async def get_product(self, *, item_id: str) -> dict[str, Any]:
        item_id = item_id.strip()
        if not item_id or len(item_id) > 128:
            return await self._invalid(
                "youzan_get_product",
                parameters={"item_id": item_id[:128]},
                message="item_id is required",
            )

        async def operation() -> tuple[dict[str, Any], int]:
            product = get_catalog_product(item_id)
            return {"product": product}, int(product is not None)

        return await self._execute(
            "youzan_get_product",
            parameters={"item_id": item_id},
            operation=operation,
        )

    async def list_inventory(self, *, limit: int = 20) -> dict[str, Any]:
        if not 1 <= limit <= 50:
            return await self._invalid(
                "youzan_list_inventory",
                parameters={"limit": limit},
                message="limit must be between 1 and 50",
            )

        async def operation() -> tuple[dict[str, Any], int]:
            products = list_catalog_products(limit=limit)
            return {"products": products}, len(products)

        return await self._execute(
            "youzan_list_inventory",
            parameters={"limit": limit},
            operation=operation,
        )

    async def resolve_customer(
        self,
        *,
        customer_id: str,
        mobile: str,
        tenant_id: str = "tenant_default",
        channel: str = "wechat",
    ) -> dict[str, Any]:
        invalid = self._validate_customer_input(customer_id, mobile)
        if invalid:
            return await self._invalid(
                "youzan_resolve_customer",
                customer_id=customer_id,
                parameters={"mobile_masked": _mask_mobile(mobile)},
                message=invalid,
            )

        async def operation() -> tuple[dict[str, Any], int]:
            lookup = await self.order_service.lookup_by_mobile(mobile, limit=3)
            self._persist_identity(
                customer_id=customer_id,
                tenant_id=tenant_id,
                channel=channel,
                identity=lookup.identity,
            )
            return _public_identity(lookup.identity), int(lookup.identity.order_queryable)

        return await self._execute(
            "youzan_resolve_customer",
            customer_id=customer_id,
            parameters={
                "mobile_masked": _mask_mobile(mobile),
                "tenant_id": tenant_id,
                "channel": channel,
            },
            operation=operation,
        )

    async def search_customer_orders(
        self,
        *,
        customer_id: str,
        mobile: str | None = None,
        limit: int = 3,
        tenant_id: str = "tenant_default",
        channel: str = "wechat",
    ) -> dict[str, Any]:
        mobile = (mobile or "").strip()
        invalid = self._validate_customer_input(customer_id, mobile, mobile_optional=True)
        if invalid or not 1 <= limit <= 10:
            return await self._invalid(
                "youzan_search_customer_orders",
                customer_id=customer_id,
                parameters={"mobile_masked": _mask_mobile(mobile), "limit": limit},
                message=invalid or "limit must be between 1 and 10",
            )

        async def operation() -> tuple[dict[str, Any], int]:
            identity, initial_orders = await self._resolve_identity(
                customer_id=customer_id,
                mobile=mobile,
                limit=limit,
                tenant_id=tenant_id,
                channel=channel,
            )
            if identity is None:
                return {"status": "missing_mobile", "orders": []}, 0
            orders = (
                initial_orders
                if initial_orders is not None
                else await self.order_service.search_by_identity(identity, limit=limit)
            )
            return {
                "status": "found" if orders else "not_found",
                "mobile_masked": identity.mobile_masked or _mask_mobile(identity.mobile),
                "orders": [item.model_dump() for item in orders],
            }, len(orders)

        return await self._execute(
            "youzan_search_customer_orders",
            customer_id=customer_id,
            parameters={
                "mobile_masked": _mask_mobile(mobile),
                "limit": limit,
                "tenant_id": tenant_id,
                "channel": channel,
            },
            operation=operation,
        )

    async def get_customer_order(
        self,
        *,
        customer_id: str,
        order_no: str,
        mobile: str | None = None,
        tenant_id: str = "tenant_default",
        channel: str = "wechat",
    ) -> dict[str, Any]:
        mobile = (mobile or "").strip()
        order_no = order_no.strip()
        invalid = self._validate_customer_input(customer_id, mobile, mobile_optional=True)
        if invalid or not order_no or len(order_no) > 128:
            return await self._invalid(
                "youzan_get_customer_order",
                customer_id=customer_id,
                parameters={
                    "order_no": order_no[:128],
                    "mobile_masked": _mask_mobile(mobile),
                },
                message=invalid or "order_no is required",
            )

        async def operation() -> tuple[dict[str, Any], int]:
            identity, initial_orders = await self._resolve_identity(
                customer_id=customer_id,
                mobile=mobile,
                limit=50,
                tenant_id=tenant_id,
                channel=channel,
            )
            if identity is None:
                return {"status": "missing_mobile", "order": None}, 0
            orders = (
                initial_orders
                if initial_orders is not None
                else await self.order_service.search_by_identity(identity, limit=50)
            )
            if not any(item.order_no == order_no for item in orders):
                return {"status": "not_found", "order": None}, 0
            order = await self.order_service.get(order_no)
            return {"status": "found", "order": order.model_dump()}, 1

        return await self._execute(
            "youzan_get_customer_order",
            customer_id=customer_id,
            parameters={
                "order_no": order_no,
                "mobile_masked": _mask_mobile(mobile),
                "tenant_id": tenant_id,
                "channel": channel,
            },
            operation=operation,
        )

    async def _resolve_identity(
        self,
        *,
        customer_id: str,
        mobile: str,
        limit: int,
        tenant_id: str,
        channel: str,
    ) -> tuple[YouzanCustomerIdentity | None, list | None]:
        if mobile:
            lookup: YouzanOrderLookup = await self.order_service.lookup_by_mobile(
                mobile,
                limit=limit,
            )
            self._persist_identity(
                customer_id=customer_id,
                tenant_id=tenant_id,
                channel=channel,
                identity=lookup.identity,
            )
            return lookup.identity, lookup.orders
        identity = self.identity_store.get(
            tenant_id=tenant_id,
            channel=channel,
            external_user_id=customer_id,
            kdt_id=self.kdt_id,
        )
        return identity, None

    def _persist_identity(
        self,
        *,
        customer_id: str,
        tenant_id: str,
        channel: str,
        identity: YouzanCustomerIdentity,
    ) -> None:
        if not identity.order_queryable:
            return
        self.identity_store.upsert(
            tenant_id=tenant_id,
            channel=channel,
            external_user_id=customer_id,
            kdt_id=self.kdt_id,
            identity=identity,
            source="ai_tool_mobile_verified",
        )

    @staticmethod
    def _validate_customer_input(
        customer_id: str,
        mobile: str,
        *,
        mobile_optional: bool = False,
    ) -> str:
        if not customer_id.strip() or len(customer_id) > 256:
            return "customer_id is required"
        if not mobile and mobile_optional:
            return ""
        if not MOBILE_PATTERN.fullmatch(mobile):
            return "mobile must be a valid mainland China mobile number"
        return ""

    async def _invalid(
        self,
        tool_name: str,
        *,
        parameters: dict[str, Any],
        message: str,
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = uuid4().hex
        self._record_audit(
            trace_id=trace_id,
            tool_name=tool_name,
            customer_id=customer_id,
            parameters=parameters,
            result_count=0,
            status="rejected",
            error_code="invalid_arguments",
            latency_ms=0,
        )
        return {
            "ok": False,
            "read_only": True,
            "tool": tool_name,
            "trace_id": trace_id,
            "error": {"code": "invalid_arguments", "message": message},
        }

    async def _execute(
        self,
        tool_name: str,
        *,
        parameters: dict[str, Any],
        operation: Callable[[], Awaitable[tuple[dict[str, Any], int]]],
        customer_id: str | None = None,
    ) -> dict[str, Any]:
        trace_id = uuid4().hex
        started = time.perf_counter()
        status = "success"
        error_code: str | None = None
        result_count = 0
        try:
            data, result_count = await operation()
            return {
                "ok": True,
                "read_only": True,
                "tool": tool_name,
                "trace_id": trace_id,
                "data": data,
            }
        except YouzanError as exc:
            status = "upstream_error"
            error_code = exc.code or "youzan_error"
            return {
                "ok": False,
                "read_only": True,
                "tool": tool_name,
                "trace_id": trace_id,
                "error": {
                    "code": error_code,
                    "upstream_trace_id": exc.trace_id or None,
                },
            }
        except Exception as exc:  # noqa: BLE001
            status = "error"
            error_code = "internal_error"
            logger.warning("Youzan AI tool failed: %s", type(exc).__name__)
            return {
                "ok": False,
                "read_only": True,
                "tool": tool_name,
                "trace_id": trace_id,
                "error": {"code": error_code},
            }
        finally:
            latency_ms = round((time.perf_counter() - started) * 1000)
            self._record_audit(
                trace_id=trace_id,
                tool_name=tool_name,
                customer_id=customer_id,
                parameters=parameters,
                result_count=result_count,
                status=status,
                error_code=error_code,
                latency_ms=latency_ms,
            )

    def _record_audit(self, **payload: Any) -> None:
        try:
            self.identity_store.record_tool_call(caller=self.caller, **payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Youzan tool audit write failed: %s", type(exc).__name__)


def _mask_mobile(mobile: str) -> str:
    return f"{mobile[:3]}****{mobile[-4:]}" if len(mobile) == 11 else ""


def _public_identity(identity: YouzanCustomerIdentity) -> dict[str, Any]:
    return {
        "found": identity.order_queryable,
        "mobile_masked": identity.mobile_masked or _mask_mobile(identity.mobile),
        "links": {
            "youzan_customer": bool(identity.yz_uid or identity.buyer_id),
            "youzan_open_id": bool(identity.yz_open_id),
            "wechat_open_id": bool(identity.weixin_openid),
            "wechat_union_id": bool(identity.union_id),
        },
    }
