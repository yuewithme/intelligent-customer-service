from typing import Any
from app.domains.decisioning.services.service_sop import SERVICE_COLLECTION_DIRECTIONS
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
            "description": "新好友订单教程接待、专属服务、柔性建档及周四会员日；每日两次触达与每周长效服务独立运行。",
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

    entry_name = "服务客户接待" if scope == "service" else "两类偏好标签均具备"
    node("entry", entry_name, package["description"], "decision", 50, 220)
    node("daily_touch", "每日触达", "总开关开启且标签满足时按日调度；发送前再次校验。", "wait", 330, 80)
    edge("entry", "daily_touch", "定时分支")
    package["entry"]["start_step_id"] = "entry"
    package["outcomes"] = []
    package["global_rules"] += ["关闭 SOP 后停止该流程对话推进和自动触达，普通问答继续。", "服务与种草可同时命中；两个分支独立触发。"]
    if scope == "service":
        for index, (slot, at, title) in enumerate((
            ("knowledge", "10:00", "养护科普"), ("topic", "14:00", "品种鉴赏"),
            ("care_followup", "周六11:00", "季节关怀回访（每隔7天）"),
            ("personalized_knowledge", "周日14:00", "个性化知识（每隔7天）"),
        )):
            node(slot, f"{at} {title}", f"北京时间{at}发送{title}；周日14:00合并为个性化推送。", "action", 620, 20 + index * 125)
            edge("daily_touch", slot, at)
        collection = next(step for step in steps if step["step_id"] == "basic_info_collection")
        collection["directions"] = list(SERVICE_COLLECTION_DIRECTIONS)
        for index, step_id in enumerate(("order_intake", "member_benefit", "basic_info_collection", "post_service_close", "relationship_maintenance")):
            layout[step_id] = {"x": 330 + index * 260, "y": 650}
        edge("entry", "order_intake", "首次接待：订单截图与教程")
        edge("order_intake", "member_benefit", "已识别购买品种，交付教程并介绍服务")
        edge("member_benefit", "basic_info_collection", "教程和介绍已交付，逐步补齐档案")
        edge("basic_info_collection", "post_service_close", "准备自然收尾，介绍周四会员日")
        edge("post_service_close", "relationship_maintenance", "持续服务，已知偏好不再挖需")
        layout["need_discovery"] = {"x": 330, "y": 880}
        layout["repurchase_discovery"] = {"x": 650, "y": 1050}
        edge("entry", "need_discovery", "客户当前问题优先处理")
        edge("entry", "repurchase_discovery", "明确选购需求转人工")
        for target, label in (("member_benefit", "需要交付资料"), ("basic_info_collection", "自然补齐未知信息"),
                              ("post_service_close", "问题已解决，准备收尾"), ("relationship_maintenance", "需要后续服务")):
            edge("need_discovery", target, label)
        for step in steps:
            if step["step_id"] == "repurchase_discovery":
                step["handoff_enabled"] = True
    else:
        node("video_touch", "15:00 文字＋产品视频", "北京时间每天 15:00；视频同时匹配品类与产品需求，缺少匹配视频则跳过并记录原因。", "action", 620, 80)
        edge("daily_touch", "video_touch", "每天 15:00")
        layout["product_interest"] = {"x": 330, "y": 370}
        layout["recommendation"] = {"x": 620, "y": 370}
        edge("entry", "product_interest", "回复表达想了解产品")
        edge("product_interest", "recommendation", "意向成立，继续推品")
    package["transitions"] = transitions
