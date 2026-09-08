from app.domains.handoff.services.handoff_notification_service import get_sop_settings
from app.domains.sales.services.tag_catalog import get_tag_categories


def preference_tag_groups(tags: list[str] | set[str]) -> tuple[set[str], set[str]]:
    values = {str(tag).strip().rpartition(":")[2] for tag in tags}
    categories = get_tag_categories()
    return tuple(
        values & {value.name for value in categories[category].values}
        for category in ("favorite_orchid_type", "product_demand")
    )


def has_product_preferences(tags: list[str] | set[str]) -> bool:
    return all(preference_tag_groups(tags))


def eligible_sop_scopes(tags: list[str], settings: dict[str, bool] | None = None) -> list[str]:
    settings = get_sop_settings() if settings is None else settings
    values = {str(tag).strip().rpartition(":")[2] for tag in tags}
    scopes = []
    if "服务中" in values and settings.get("service"):
        scopes.append("service")
    if has_product_preferences(values) and settings.get("seeding"):
        scopes.append("seeding")
    if not scopes and "服务中" not in values and not values.intersection({"抖音已购", "微信已购"}) and settings.get("first_order"):
        scopes.append("first_order")
    return scopes
