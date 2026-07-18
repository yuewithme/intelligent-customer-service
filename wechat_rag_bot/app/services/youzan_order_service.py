import logging
from typing import Any

from pydantic import BaseModel

from app.integrations.youzan.client import YouzanError


logger = logging.getLogger("wechat_rag_bot.youzan_order")


ORDER_STATUS_TEXT = {
    "WAIT_BUYER_PAY": "待付款",
    "WAIT_SELLER_SEND_GOODS": "待发货",
    "WAIT_BUYER_CONFIRM_GOODS": "已发货",
    "TRADE_BUYER_SIGNED": "已完成",
    "TRADE_CLOSED": "已关闭",
}


class YouzanOrderSummary(BaseModel):
    order_no: str
    created_at: str = ""
    status: str = ""
    status_text: str = "状态待确认"
    item_summary: str = ""
    express_company: str = ""
    tracking_no_masked: str = ""


class YouzanCustomerIdentity(BaseModel):
    yz_uid: str = ""
    buyer_id: str = ""
    yz_open_id: str = ""
    fans_id: str = ""
    weixin_openid: str = ""
    union_id: str = ""
    mobile_masked: str = ""

    @property
    def order_queryable(self) -> bool:
        return bool(self.buyer_id or self.yz_uid or self.fans_id)


class YouzanOrderLookup(BaseModel):
    identity: YouzanCustomerIdentity
    orders: list[YouzanOrderSummary]


class YouzanOrderService:
    def __init__(
        self,
        client,
        *,
        method: str = "youzan.trades.sold.get",
        version: str = "4.0.0",
        customer_method: str = "youzan.scrm.customer.get",
        customer_version: str = "3.0.0",
        follower_method: str = "youzan.users.weixin.follower.get",
        follower_version: str = "3.0.0",
        detail_enabled: bool = False,
        detail_method: str = "youzan.trade.get",
        detail_version: str = "4.0.0",
        logistics_enabled: bool = False,
        logistics_method: str = "youzan.logistics.expressbyorderno.search",
        logistics_version: str = "3.0.0",
    ) -> None:
        self.client = client
        self.method = method
        self.version = version
        self.customer_method = customer_method
        self.customer_version = customer_version
        self.follower_method = follower_method
        self.follower_version = follower_version
        self.detail_enabled = detail_enabled
        self.detail_method = detail_method
        self.detail_version = detail_version
        self.logistics_enabled = logistics_enabled
        self.logistics_method = logistics_method
        self.logistics_version = logistics_version

    async def search_by_mobile(
        self,
        mobile: str,
        *,
        limit: int = 3,
    ) -> list[YouzanOrderSummary]:
        return (await self.lookup_by_mobile(mobile, limit=limit)).orders

    async def lookup_by_mobile(
        self,
        mobile: str,
        *,
        limit: int = 3,
    ) -> YouzanOrderLookup:
        try:
            customer = await self.client.call(
                self.customer_method,
                self.customer_version,
                {"mobile": mobile},
            )
        except YouzanError as exc:
            if exc.code == "141502108":
                return YouzanOrderLookup(identity=YouzanCustomerIdentity(), orders=[])
            raise
        identity = _normalize_identity(customer, mobile=mobile)
        orders = await self.search_by_identity(identity, limit=limit)
        return YouzanOrderLookup(identity=identity, orders=orders)

    async def lookup_by_weixin_openid(
        self,
        weixin_openid: str,
        *,
        limit: int = 3,
    ) -> YouzanOrderLookup:
        try:
            follower = await self.client.call(
                self.follower_method,
                self.follower_version,
                {"weixin_openid": weixin_openid},
            )
        except YouzanError as exc:
            if exc.code == "50000":
                return YouzanOrderLookup(
                    identity=YouzanCustomerIdentity(weixin_openid=weixin_openid),
                    orders=[],
                )
            raise
        identity = _normalize_identity(follower, weixin_openid=weixin_openid)
        if identity.fans_id:
            try:
                customer = await self.client.call(
                    self.customer_method,
                    self.customer_version,
                    {"fans_id": identity.fans_id, "fans_type": 1},
                )
            except YouzanError as exc:
                if exc.code != "141502108":
                    raise
            else:
                identity = _merge_identity(identity, _normalize_identity(customer))
        orders = await self.search_by_identity(identity, limit=limit)
        return YouzanOrderLookup(identity=identity, orders=orders)

    async def search_by_identity(
        self,
        identity: YouzanCustomerIdentity | dict[str, Any],
        *,
        limit: int = 3,
    ) -> list[YouzanOrderSummary]:
        if not isinstance(identity, YouzanCustomerIdentity):
            identity = YouzanCustomerIdentity.model_validate(identity)
        buyer_id = identity.buyer_id or identity.yz_uid
        if buyer_id:
            params = {
                "buyer_id": _api_identifier(buyer_id),
                "page_no": 1,
                "page_size": limit,
            }
        elif identity.fans_id:
            params = {
                "fans_id": _api_identifier(identity.fans_id),
                "page_no": 1,
                "page_size": limit,
            }
        else:
            return []
        data = await self.client.call(
            self.method,
            self.version,
            params,
        )
        trades = _trade_list(data)
        orders = [
            self._normalize(trade) for trade in trades[:limit] if isinstance(trade, dict)
        ]
        if self.detail_enabled and orders and orders[0].order_no:
            try:
                orders[0] = await self.get(orders[0].order_no)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Youzan order detail enrichment failed: %s",
                    type(exc).__name__,
                )
        if (
            self.logistics_enabled
            and orders
            and orders[0].order_no
            and not orders[0].tracking_no_masked
        ):
            try:
                logistics = await self.get_logistics(orders[0].order_no)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Youzan logistics enrichment failed: %s",
                    type(exc).__name__,
                )
            else:
                orders[0] = orders[0].model_copy(update=logistics)
        return orders

    async def get(self, order_no: str) -> YouzanOrderSummary:
        data = await self.client.call(
            self.detail_method,
            self.detail_version,
            {"tid": order_no},
        )
        return self._normalize(data)

    async def get_logistics(self, order_no: str) -> dict[str, str]:
        data = await self.client.call(
            self.logistics_method,
            self.logistics_version,
            {"tid": order_no},
        )
        express = _find_express(data)
        return {
            "express_company": str(
                express.get("express_name")
                or express.get("company")
                or express.get("company_name")
                or ""
            ),
            "tracking_no_masked": _mask_tracking_no(
                str(
                    express.get("express_no")
                    or express.get("tracking_no")
                    or express.get("nu")
                    or ""
                )
            ),
        }

    def _normalize(self, trade: dict[str, Any]) -> YouzanOrderSummary:
        nested = trade.get("full_order_info")
        if isinstance(nested, dict):
            trade = {**trade, **nested}
        order_info = trade.get("order_info")
        if not isinstance(order_info, dict):
            order_info = trade
        status = str(order_info.get("status") or trade.get("status") or "")
        express = _find_express(trade)
        return YouzanOrderSummary(
            order_no=str(order_info.get("tid") or order_info.get("order_no") or ""),
            created_at=str(order_info.get("created") or order_info.get("created_at") or ""),
            status=status,
            status_text=ORDER_STATUS_TEXT.get(status, str(trade.get("status_text") or "状态待确认")),
            item_summary=_item_summary(trade),
            express_company=str(express.get("express_name") or express.get("company") or ""),
            tracking_no_masked=_mask_tracking_no(
                str(express.get("express_no") or express.get("tracking_no") or "")
            ),
        )


def _trade_list(data: dict[str, Any]) -> list:
    for key in ("full_order_info_list", "trades", "items", "orders"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _item_summary(trade: dict[str, Any]) -> str:
    items = trade.get("orders") or trade.get("items") or []
    if not isinstance(items, list):
        return ""
    parts = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "商品")
        quantity = item.get("num") or item.get("quantity") or 1
        parts.append(f"{title} × {quantity}")
    return "；".join(parts)


def _mask_tracking_no(value: str) -> str:
    if len(value) <= 6:
        return value
    return f"{value[:4]}{'*' * (len(value) - 6)}{value[-2:]}"


def _normalize_identity(
    data: dict[str, Any],
    *,
    mobile: str = "",
    weixin_openid: str = "",
) -> YouzanCustomerIdentity:
    yz_uid = _nested_text(data, "yz_uid")
    buyer_id = _nested_text(data, "buyer_id") or yz_uid
    fans_id = _nested_text(data, "fans_id") or _nested_text(data, "user_id")
    resolved_mobile = _nested_text(data, "mobile") or mobile
    return YouzanCustomerIdentity(
        yz_uid=yz_uid,
        buyer_id=buyer_id,
        yz_open_id=_nested_text(data, "yz_open_id", "yz_openid"),
        fans_id=fans_id,
        weixin_openid=(
            _nested_text(data, "weixin_openid", "openid", "open_id")
            or weixin_openid
        ),
        union_id=_nested_text(data, "union_id", "unionid"),
        mobile_masked=_mask_mobile(resolved_mobile),
    )


def _merge_identity(
    first: YouzanCustomerIdentity,
    second: YouzanCustomerIdentity,
) -> YouzanCustomerIdentity:
    return YouzanCustomerIdentity(
        **{
            field: getattr(second, field) or getattr(first, field)
            for field in YouzanCustomerIdentity.model_fields
        }
    )


def _nested_text(data: dict[str, Any], *keys: str) -> str:
    queue: list[tuple[dict[str, Any], int]] = [(data, 0)]
    while queue:
        current, depth = queue.pop(0)
        for key in keys:
            value = current.get(key)
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return str(value)
        if depth >= 3:
            continue
        for value in current.values():
            if isinstance(value, dict):
                queue.append((value, depth + 1))
    return ""


def _find_express(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("express_info", "delivery_order", "logistics", "packages"):
        value = data.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            first = next((item for item in value if isinstance(item, dict)), None)
            if first is not None:
                nested = _find_express(first)
                return nested or first
    for value in data.values():
        if isinstance(value, dict):
            found = _find_express(value)
            if found:
                return found
    return {}


def _mask_mobile(value: str) -> str:
    return f"{value[:3]}****{value[-4:]}" if len(value) == 11 else ""


def _api_identifier(value: str) -> str | int:
    return int(value) if value.isdigit() else value
