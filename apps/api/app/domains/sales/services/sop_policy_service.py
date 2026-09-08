from app.domains.handoff.services.handoff_notification_service import get_sop_settings
from app.domains.sales.services.tag_catalog import get_tag_categories
from app.domains.orchestration.services.sop_flow_service import get_saved_flow, entry_matches


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
    defaults = {"service": "服务中" in values, "seeding": has_product_preferences(values),
                "first_order": not values.intersection({"服务中", "抖音已购", "微信已购"})}
    scopes, fallback = [], []
    for scope in ("service", "seeding", "first_order"):
        if not settings.get(scope):
            continue
        flow = get_saved_flow(scope)
        if entry_matches(flow, values) if flow else defaults[scope]:
            (fallback if (flow.entry_rule.fallback_only if flow else scope == "first_order") else scopes).append(scope)
    if not scopes:
        scopes = fallback
    return scopes
