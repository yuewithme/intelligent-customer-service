from typing import Any

from pydantic import BaseModel


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


class YouzanOrderService:
    def __init__(
        self,
        client,
        *,
        method: str = "youzan.trades.sold.get",
        version: str = "4.0.0",
        customer_method: str = "youzan.scrm.customer.get",
        customer_version: str = "3.0.0",
    ) -> None:
        self.client = client
        self.method = method
        self.version = version
        self.customer_method = customer_method
        self.customer_version = customer_version

    async def search_by_mobile(
        self,
        mobile: str,
        *,
        limit: int = 3,
    ) -> list[YouzanOrderSummary]:
        customer = await self.client.call(
            self.customer_method,
            self.customer_version,
            {"mobile": mobile},
        )
        buyer_id = customer.get("yz_uid") or customer.get("buyer_id")
        if not buyer_id:
            return []
        data = await self.client.call(
            self.method,
            self.version,
            {"buyer_id": buyer_id, "page_no": 1, "page_size": limit},
        )
        trades = _trade_list(data)
        return [self._normalize(trade) for trade in trades[:limit] if isinstance(trade, dict)]

    def _normalize(self, trade: dict[str, Any]) -> YouzanOrderSummary:
        nested = trade.get("full_order_info")
        if isinstance(nested, dict):
            trade = nested
        order_info = trade.get("order_info")
        if not isinstance(order_info, dict):
            order_info = trade
        status = str(order_info.get("status") or trade.get("status") or "")
        express = trade.get("express_info")
        if not isinstance(express, dict):
            if isinstance(trade.get("delivery_order"), dict):
                express = trade["delivery_order"]
            else:
                express = trade.get("logistics") if isinstance(trade.get("logistics"), dict) else {}
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
