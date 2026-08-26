from __future__ import annotations

from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.domains.orchestration.schemas import (
    CapabilityCatalog,
    ExperiencePackageDefinition,
)
from app.domains.orchestration.schemas.definitions import (
    ActionStep,
    AgentStageStep,
)


_DATA_ROOT = Path(__file__).resolve().parents[1] / "data"
_CAPABILITY_CATALOG = (
    _DATA_ROOT / "capabilities" / "current_agent_capabilities.v1.json"
)
_EXPERIENCE_PACKAGE_ROOT = _DATA_ROOT / "experience_packages"


@lru_cache(maxsize=1)
def _load_capability_catalog() -> CapabilityCatalog:
    return CapabilityCatalog.model_validate_json(
        _CAPABILITY_CATALOG.read_text(encoding="utf-8")
    )


@lru_cache(maxsize=1)
def _load_experience_packages() -> tuple[ExperiencePackageDefinition, ...]:
    return tuple(
        ExperiencePackageDefinition.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(_EXPERIENCE_PACKAGE_ROOT.glob("*.json"))
    )


def get_capability_workbench() -> dict[str, Any]:
    catalog = _load_capability_catalog()
    packages = _load_experience_packages()
    catalog_ids = {item.capability_id for item in catalog.capabilities}
    usage: dict[str, list[dict[str, str]]] = defaultdict(list)

    for package in packages:
        dependency_ids = {
            dependency.capability_id
            for dependency in package.capability_dependencies
        }
        missing = dependency_ids - catalog_ids
        if missing:
            raise ValueError(
                f"experience package {package.package_id} has missing capabilities: "
                f"{sorted(missing)}"
            )
        for step in package.steps:
            bindings = []
            if isinstance(step, AgentStageStep):
                bindings = [
                    (binding.capability_id, binding.usage)
                    for binding in step.capabilities
                ]
            elif isinstance(step, ActionStep):
                bindings = [(step.capability_id, "action")]
            for capability_id, usage_type in bindings:
                usage[capability_id].append(
                    {
                        "package_id": package.package_id,
                        "package_name": package.name,
                        "step_id": step.step_id,
                        "step_name": step.name,
                        "usage": usage_type,
                    }
                )

    visibility_counts = Counter(
        item.ui.visibility for item in catalog.capabilities
    )
    capabilities = []
    for capability in catalog.capabilities:
        item = capability.model_dump(mode="json")
        item["used_by"] = usage.get(capability.capability_id, [])
        capabilities.append(item)

    return {
        "read_only": True,
        "source": "static_contracts",
        "stats": {
            "capabilities_total": len(capabilities),
            "hidden_total": visibility_counts["hidden"],
            "configurable_total": visibility_counts["configurable"],
            "draggable_total": visibility_counts["draggable"],
            "experience_packages_total": len(packages),
            "draft_packages_total": sum(
                package.status == "draft" for package in packages
            ),
        },
        "capabilities": capabilities,
        "experience_packages": [
            package.model_dump(mode="json") for package in packages
        ],
    }
