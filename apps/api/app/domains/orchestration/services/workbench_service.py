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
from app.domains.handoff.services.handoff_notification_service import (
    SOP_NODE_GROUPS,
    get_sop_node_handoff_settings,
    update_sop_node_handoff,
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
    contract_packages = _load_experience_packages()
    catalog_ids = {item.capability_id for item in catalog.capabilities}
    usage: dict[str, list[dict[str, str]]] = defaultdict(list)

    for package in contract_packages:
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

    handoff_settings = get_sop_node_handoff_settings()
    packages = _build_sop_handoff_packages(
        handoff_settings["sop_node_handoff"]
    )

    return {
        "read_only": False,
        "source": "sop_handoff_settings",
        "stats": {
            "capabilities_total": len(capabilities),
            "hidden_total": visibility_counts["hidden"],
            "configurable_total": visibility_counts["configurable"],
            "draggable_total": visibility_counts["draggable"],
            "experience_packages_total": len(packages),
            "draft_packages_total": 0,
        },
        "capabilities": capabilities,
        "experience_packages": packages,
    }


def update_capability_workbench_sop_node(
    *, node_id: str, handoff_enabled: bool
) -> dict[str, Any]:
    return update_sop_node_handoff(node_id, handoff_enabled)


def _build_sop_handoff_packages(
    policies: dict[str, bool],
) -> list[dict[str, Any]]:
    package_metadata = {
        "first_order": {
            "package_id": "orchid.first_order",
            "name": "首单 SOP",
            "description": "面向未完成首单的新客户，从破冰、需求判断到成交推进。",
            "entry_event": "first_contact",
            "outcome_name": "首单流程完成",
        },
        "service": {
            "package_id": "orchid.service",
            "name": "服务 SOP",
            "description": "面向已购客户，完成养护服务、权益交付、复购挖需和长期关系维护。",
            "entry_event": "verified_purchase",
            "outcome_name": "服务关系持续",
        },
    }
    packages: list[dict[str, Any]] = []
    for group in SOP_NODE_GROUPS:
        scope = str(group["sop_scope"])
        metadata = package_metadata[scope]
        nodes = list(group["nodes"])
        steps = []
        transitions = []
        layout = {}
        for index, (node_id, name, description) in enumerate(nodes):
            step_id = node_id.split(".", 1)[1]
            steps.append(
                {
                    "step_id": step_id,
                    "node_id": node_id,
                    "name": name,
                    "type": "agent_stage",
                    "description": description,
                    "goal": description,
                    "directions": [],
                    "collect": [],
                    "capabilities": [],
                    "handoff_enabled": bool(policies.get(node_id, False)),
                }
            )
            layout[step_id] = {"x": 80 + index * 250, "y": 210}
            if index < len(nodes) - 1:
                next_step_id = nodes[index + 1][0].split(".", 1)[1]
                transitions.append(
                    {
                        "transition_id": f"{step_id}_to_{next_step_id}",
                        "from_step": step_id,
                        "to_step": next_step_id,
                        "outcome": None,
                        "label": "继续推进",
                        "priority": 100,
                        "condition": None,
                    }
                )
            else:
                transitions.append(
                    {
                        "transition_id": f"{step_id}_to_complete",
                        "from_step": step_id,
                        "to_step": None,
                        "outcome": "complete",
                        "label": "本阶段完成",
                        "priority": 100,
                        "condition": None,
                    }
                )
        packages.append(
            {
                "schema_version": "experience_package.v1",
                "package_id": metadata["package_id"],
                "version": "1.0.0",
                "name": metadata["name"],
                "description": metadata["description"],
                "status": "published",
                "tags": ["兰花", scope, "SOP"],
                "goals": [metadata["description"]],
                "entry": {
                    "events": [metadata["entry_event"]],
                    "start_step_id": nodes[0][0].split(".", 1)[1],
                    "conditions": None,
                    "priority": 100,
                },
                "parameters_schema": {"type": "object", "properties": {}},
                "context_schema": {"type": "object", "properties": {}},
                "global_rules": ["节点默认由 AI 回复，开启后命中节点即转人工。"],
                "capability_dependencies": [],
                "steps": steps,
                "transitions": transitions,
                "outcomes": [
                    {
                        "outcome_id": "complete",
                        "name": metadata["outcome_name"],
                        "terminal": True,
                        "next_package_id": None,
                        "result_tags": [],
                    }
                ],
                "success_metrics": [],
                "layout": layout,
            }
        )
    return packages
