from collections.abc import Collection

from app.domains.catalog.orchid_products.repository import list_orchid_skus
from app.domains.decisioning.schemas.reply_plan import BusinessFacts


async def build_business_context(
    message,
    *,
    allowed_source_groups: Collection[str] | None = None,
) -> BusinessFacts:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    names = metadata.get("requested_varieties")
    requested = [str(name) for name in names] if isinstance(names, list) else []
    allow_skus = allowed_source_groups is None or "sku_facts" in allowed_source_groups
    tool_state = (
        dict(metadata.get("tool_state"))
        if isinstance(metadata.get("tool_state"), dict)
        else {}
    )
    commerce_type = str(tool_state.get("commerce_type") or "")
    if (
        allowed_source_groups is not None
        and (
            (
                commerce_type == "product"
                and "product_catalog" not in allowed_source_groups
            )
            or (commerce_type == "order" and "order_facts" not in allowed_source_groups)
        )
    ):
        tool_state = {}
    return BusinessFacts(
        snapshot=str(metadata.get("business_snapshot") or "").strip(),
        tool_state=tool_state,
        skus=(
            list_orchid_skus(variety_names=requested, limit=10)
            if requested and allow_skus
            else []
        ),
    )
