from typing import Any


SERVICE_NEED_SLOT_ORDER = (
    "pain_point",
    "desired_outcome",
    "failed_history",
)

_GENERIC_SERVICE_NEEDS = {
    "",
    "unknown",
    "不清楚",
    "新手",
    "养不好",
    "不会养",
    "不懂养护",
    "想学习",
    "想学养兰",
    "想学习养兰",
    "怕养不好",
    "想把兰花养好",
}


def resolved_service_need(slots: dict[str, Any] | None) -> tuple[str, str] | None:
    """Return an explicit, actionable service need from normalized sales slots."""

    if not isinstance(slots, dict):
        return None
    for key in SERVICE_NEED_SLOT_ORDER:
        value = _need_text(slots.get(key))
        if value and value.lower() not in _GENERIC_SERVICE_NEEDS:
            return key, value
    return None


def has_resolved_service_need(slots: dict[str, Any] | None) -> bool:
    return resolved_service_need(slots) is not None


def _need_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip(" ，,;；。.!！?？")
    if isinstance(value, (list, tuple, set)):
        parts = [
            item.strip(" ，,;；。.!！?？")
            for item in map(str, value)
            if str(item).strip()
        ]
        return "、".join(parts)
    if isinstance(value, dict):
        for key in ("detail", "description", "summary"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
    return ""
