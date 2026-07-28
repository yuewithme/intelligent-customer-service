from __future__ import annotations

from typing import Any

from app.domains.decisioning.services.intent_taxonomy_migration_service import (
    migrate_dgi_v1_to_v2,
)


TARGET_TAXONOMY_VERSION = "2.1"


def migrate_dgi_to_v21(
    payload: dict[str, Any],
    *,
    source_version: str | None = None,
) -> dict[str, Any]:
    source = str(source_version or payload.get("taxonomy_version") or "")
    migrated = (
        migrate_dgi_v1_to_v2(payload)
        if source not in {"2.0", TARGET_TAXONOMY_VERSION}
        else dict(payload)
    )
    issues = _strings(migrated.get("issues"))
    primary_goal = str(migrated.get("primary_goal") or "unclear")
    if primary_goal == "ask_information" and "material_resource" in issues:
        primary_goal = "request_material"
    if primary_goal == "request_material" and "material_resource" not in issues:
        issues.append("material_resource")
    return {
        "primary_domain": str(migrated.get("primary_domain") or "conversation"),
        "secondary_domains": _strings(migrated.get("secondary_domains")),
        "primary_goal": primary_goal,
        "secondary_goals": _strings(migrated.get("secondary_goals")),
        "issues": issues,
        "scope": str(migrated.get("scope") or "in_scope"),
        "taxonomy_version": TARGET_TAXONOMY_VERSION,
    }


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return list(
        dict.fromkeys(
            str(item).strip()
            for item in values
            if str(item or "").strip()
        )
    )
