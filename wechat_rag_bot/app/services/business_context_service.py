from typing import Any

from pydantic import BaseModel, Field

from app.orchid_products.repository import list_orchid_skus
from app.schemas.reply import FinalReply


class BusinessContext(BaseModel):
    snapshot: str = ""
    tool_state: dict[str, Any] = Field(default_factory=dict)
    skus: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def has_facts(self) -> bool:
        return bool(self.snapshot or self.tool_state or self.skus)

    def to_reply(self) -> FinalReply | None:
        if not self.has_facts:
            return None
        if self.snapshot:
            answer = f"根据当前可确认的信息，{self.snapshot.strip()}"
        elif self.skus:
            answer = "当前可确认的商品信息：" + "；".join(
                _render_sku(sku) for sku in self.skus
            )
        else:
            states = "；".join(
                f"{key}：{value}" for key, value in self.tool_state.items()
            )
            answer = (
                f"当前可确认的业务状态是：{states}。"
                "未执行或结果未知的事项，需要查询或操作完成后再确认。"
            )
        return FinalReply(
            answer=answer,
            reply_type="template",
            route="template_reply",
            metadata={"business_context": self.model_dump()},
        )


async def build_business_context(message) -> BusinessContext:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    names = metadata.get("requested_varieties")
    requested = [str(name) for name in names] if isinstance(names, list) else []
    return BusinessContext(
        snapshot=str(metadata.get("business_snapshot") or "").strip(),
        tool_state=metadata.get("tool_state")
        if isinstance(metadata.get("tool_state"), dict)
        else {},
        skus=list_orchid_skus(variety_names=requested, limit=10) if requested else [],
    )


def has_business_context(message) -> bool:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    return bool(
        metadata.get("business_snapshot")
        or metadata.get("tool_state")
        or metadata.get("requested_varieties")
    )


def _render_sku(sku: dict[str, Any]) -> str:
    details = [str(sku.get("variety_name") or "").strip()]
    for key in ("seedling_count", "package_spec", "flower_bud_status", "price_text"):
        value = sku.get(key)
        if value:
            details.append(str(value).strip())
    if not sku.get("price_text") and sku.get("price") is not None:
        details.append(f"{sku['price']}元")
    return " ".join(value for value in details if value)
