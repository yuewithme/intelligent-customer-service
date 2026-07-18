import logging
from typing import Any

from pydantic import BaseModel


logger = logging.getLogger("wechat_rag_bot.youzan_product")


class YouzanProduct(BaseModel):
    item_id: str
    title: str
    alias: str = ""
    price_cent: int | None = None
    stock: int | None = None
    image_url: str = ""
    page_path: str = ""
    h5_url: str | None = None


class YouzanProductService:
    def __init__(
        self,
        client,
        *,
        method: str = "youzan.items.onsale.get",
        version: str = "3.0.0",
        page_path_template: str = "",
        h5_url_template: str = "",
        kdt_id: str = "",
        detail_enabled: bool = False,
        detail_method: str = "youzan.item.get",
        detail_version: str = "3.0.0",
        inventory_method: str = "youzan.items.inventory.get",
        inventory_version: str = "3.0.0",
    ) -> None:
        self.client = client
        self.method = method
        self.version = version
        self.page_path_template = page_path_template
        self.h5_url_template = h5_url_template
        self.kdt_id = kdt_id
        self.detail_enabled = detail_enabled
        self.detail_method = detail_method
        self.detail_version = detail_version
        self.inventory_method = inventory_method
        self.inventory_version = inventory_version

    async def search(self, keyword: str, *, limit: int = 3) -> list[YouzanProduct]:
        data = await self.client.call(
            self.method,
            self.version,
            {"q": keyword.strip(), "page_no": 1, "page_size": limit},
        )
        raw_items = _list_value(data, "items", "products", "goods_list")
        products = [
            self._normalize(item) for item in raw_items[:limit] if isinstance(item, dict)
        ]
        if self.detail_enabled and products and products[0].item_id:
            try:
                products[0] = await self.get(products[0].item_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Youzan product detail enrichment failed: %s",
                    type(exc).__name__,
                )
        return products

    async def get(self, item_id: str) -> YouzanProduct:
        data = await self.client.call(
            self.detail_method,
            self.detail_version,
            {"item_id": item_id},
        )
        item = data.get("item") if isinstance(data.get("item"), dict) else data
        return self._normalize(item)

    async def list_inventory(self, *, limit: int = 50) -> list[YouzanProduct]:
        data = await self.client.call(
            self.inventory_method,
            self.inventory_version,
            {"page_no": 1, "page_size": limit},
        )
        raw_items = _list_value(data, "items", "products", "goods_list")
        return [
            self._normalize(item) for item in raw_items[:limit] if isinstance(item, dict)
        ]

    def _normalize(self, item: dict[str, Any]) -> YouzanProduct:
        item_id = _text(item, "item_id", "num_iid", "goods_id", "id")
        alias = _text(item, "alias", "handle")
        values = {"item_id": item_id, "alias": alias, "kdt_id": self.kdt_id}
        return YouzanProduct(
            item_id=item_id,
            title=_text(item, "title", "name"),
            alias=alias,
            price_cent=_integer(item, "price", "price_cent"),
            stock=_integer(item, "quantity", "stock_num", "stock"),
            image_url=_text(item, "pic_url", "image_url", "image"),
            page_path=_format_template(self.page_path_template, values),
            h5_url=(
                _format_template(self.h5_url_template, values)
                or _text(item, "detail_url", "h5_url")
                or None
            ),
        )


def _list_value(data: dict[str, Any], *keys: str) -> list:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _text(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _integer(data: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _format_template(template: str, values: dict[str, str]) -> str:
    if not template:
        return ""
    try:
        return template.format(**values)
    except KeyError:
        return ""
