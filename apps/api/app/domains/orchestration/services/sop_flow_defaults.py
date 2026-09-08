from typing import Any
from app.core.config import get_settings
from app.domains.handoff.schemas.handoff_notification import SOP_NODE_GROUPS


def build_default_sop_packages(
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
            "description": "打上服务中标签后进入，每日三个时段触达与客户问题处理独立运行。",
            "entry_event": "打上服务中标签",
            "outcome_name": "服务关系持续",
        },
        "seeding": {
            "package_id": "orchid.seeding",
            "name": "种草 SOP",
            "description": "喜欢的兰花品类与产品需求分类均有标签时进入；每日 15:00 分享偏好产品视频，客户表达想了解产品信息时继续推品。",
            "entry_event": "品类偏好且产品需求标签齐备",
            "outcome_name": "持续种草与产品咨询",
        },
    }
    switches = {"first_order": True, "service": True, "seeding": False}
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
                "sop_scope": scope,
                "enabled": switches[scope],
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
        if scope in {"service", "seeding"}:
            _build_parallel_sop_graph(packages[-1])
    return packages


def _build_parallel_sop_graph(package: dict[str, Any]) -> None:
    scope = package["sop_scope"]
    steps = package["steps"]
    layout = package["layout"]
    transitions = []

    def node(step_id: str, name: str, description: str, kind: str, x: int, y: int) -> None:
        steps.append({"step_id": step_id, "name": name, "description": description,
                      "type": kind, "goal": description, "directions": [],
                      "collect": [], "capabilities": []})
        layout[step_id] = {"x": x, "y": y}

    def edge(source: str, target: str, label: str) -> None:
        transitions.append({"transition_id": f"{source}_to_{target}", "from_step": source,
                            "to_step": target, "outcome": None, "label": label,
                            "priority": 100, "condition": None})

    entry_name = "服务中标签" if scope == "service" else "两类偏好标签均具备"
    node("entry", entry_name, package["description"], "decision", 50, 220)
    node("daily_touch", "每日触达", "总开关开启且标签满足时按日调度；发送前再次校验。", "wait", 330, 80)
    edge("entry", "daily_touch", "定时分支")
    package["entry"]["start_step_id"] = "entry"
    package["outcomes"] = []
    package["global_rules"] += ["关闭 SOP 后停止该流程对话推进和自动触达，普通问答继续。", "服务与种草可同时命中；两个分支独立触发。"]
    if scope == "service":
        settings = get_settings()
        for index, (slot, at, title) in enumerate((
            ("story", settings.service_material_story_time, "名品故事"),
            ("knowledge", settings.service_material_knowledge_time, "养护科普"),
            ("topic", settings.service_material_topic_time, "话题种草"),
        )):
            node(slot, f"{at} {title}", f"每天 {at}（{settings.service_material_touch_timezone}）发送{title}文案与素材。", "action", 620, 20 + index * 125)
            edge("daily_touch", slot, at)
        layout["need_discovery"] = {"x": 330, "y": 500}
        edge("entry", "need_discovery", "客户发来问题")
        for index, (target, label) in enumerate((
            ("member_benefit", "需要权益交付"), ("post_service_close", "问题已解决"),
            ("repurchase_discovery", "出现复购需求"), ("relationship_maintenance", "需要后续服务"),
        )):
            layout[target] = {"x": 620, "y": 405 + index * 125}
            edge("need_discovery", target, label)
    else:
        node("video_touch", "15:00 文字＋产品视频", "北京时间每天 15:00；视频同时匹配品类与产品需求，缺少匹配视频则跳过并记录原因。", "action", 620, 80)
        edge("daily_touch", "video_touch", "每天 15:00")
        layout["product_interest"] = {"x": 330, "y": 370}
        layout["recommendation"] = {"x": 620, "y": 370}
        edge("entry", "product_interest", "回复表达想了解产品")
        edge("product_interest", "recommendation", "意向成立，继续推品")
    package["transitions"] = transitions
