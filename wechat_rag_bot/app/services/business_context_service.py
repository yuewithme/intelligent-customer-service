from collections.abc import Collection

from app.orchid_products.repository import list_orchid_skus
from app.schemas.reply_plan import BusinessFacts


async def build_business_context(
    message,
    *,
    allowed_source_groups: Collection[str] | None = None,
) -> BusinessFacts:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    names = metadata.get("requested_varieties")
    requested = [str(name) for name in names] if isinstance(names, list) else []
    allow_skus = allowed_source_groups is None or "sku_facts" in allowed_source_groups
    return BusinessFacts(
        snapshot=str(metadata.get("business_snapshot") or "").strip(),
        tool_state=metadata.get("tool_state")
        if isinstance(metadata.get("tool_state"), dict)
        else {},
        skus=(
            list_orchid_skus(variety_names=requested, limit=10)
            if requested and allow_skus
            else []
        ),
    )
