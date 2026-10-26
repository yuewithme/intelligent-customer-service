import hashlib
import json
from typing import Any

from sqlalchemy import create_engine, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.domains.orchestration.schemas.sop_flow import SopFlowDefinition
from app.domains.orchestration.services.sop_flow_defaults import build_default_sop_packages
from app.domains.sales.services.tag_catalog import get_tag_categories
from app.infrastructure.database.models import Base, SopFlowModel, SopFlowCursorModel
from app.shared.schemas.common import AppError, ErrorCode

_factories = {}


def _session():
    url = get_settings().database_url
    if url not in _factories:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[SopFlowModel.__table__, SopFlowCursorModel.__table__])
        _factories[url] = sessionmaker(bind=engine, expire_on_commit=False)
    return _factories[url]()


def get_saved_flow(scope: str) -> SopFlowDefinition | None:
    with _session() as session:
        row = session.get(SopFlowModel, scope)
        if row is None:
            return None
        flow = SopFlowDefinition.model_validate_json(row.definition_json)
        if scope == "service" and flow.service_sop_version < 2:
            flow = _upgrade_service_flow(flow)
            changed = session.execute(update(SopFlowModel).where(SopFlowModel.scope == scope, SopFlowModel.revision == row.revision)
                .values(revision=flow.revision, definition_json=flow.model_dump_json()))
            session.commit()
            if not changed.rowcount:
                return get_saved_flow(scope)
        return flow


def default_flow(package: dict[str, Any]) -> SopFlowDefinition:
    scope = package["sop_scope"]
    entries = {
        "first_order": {"excluded_tags": ["服务中", "抖音已购", "微信已购"], "fallback_only": True},
        "service": {"any_tags": ["服务中", "抖音已购", "微信已购"]},
        "seeding": {"tag_categories": ["favorite_orchid_type", "product_demand"]},
    }
    settings = get_settings()
    schedules = {
        "story": (settings.service_material_story_time, "名品故事"),
        "knowledge": (settings.service_material_knowledge_time, "养护科普"),
        "topic": (settings.service_material_topic_time, "话题种草"),
        "video_touch": ("15:00", "话题种草"),
        "care_followup": ("11:00", "养护科普"),
        "personalized_knowledge": ("14:00", "养护科普"),
    }
    steps = []
    for source in package["steps"]:
        node = {key: source[key] for key in ("step_id", "name", "type", "description", "goal", "directions", "handoff_enabled") if key in source}
        node["require_product_interest"] = scope == "seeding" and source["type"] == "agent_stage"
        if source["type"] == "action":
            at, copy_type = schedules[source["step_id"]]
            node["schedule"] = {"time": at, "copy_type": copy_type, "match_preferences": scope == "seeding"}
            if source["step_id"] in {"care_followup", "personalized_knowledge"}:
                node["schedule"].update(weekday=5 if source["step_id"] == "care_followup" else 6, purpose=source["step_id"])
        steps.append(node)
    return SopFlowDefinition.model_validate({
        "service_sop_version": 2 if scope == "service" else 0,
        "start_step_id": package["entry"]["start_step_id"], "entry_rule": entries[scope],
        "steps": steps, "transitions": [
            {key: value for key, value in edge.items() if key != "condition"}
            for edge in package["transitions"]
        ], "layout": package["layout"],
    })


def save_flow(scope: str, definition: SopFlowDefinition) -> SopFlowDefinition:
    categories = get_tag_categories()
    if any(key not in categories for key in definition.entry_rule.tag_categories):
        raise AppError(ErrorCode.REQUEST_INVALID, message="入口引用了不存在的标签类别", status_code=422)
    if scope not in {"first_order", "service", "seeding"}:
        raise AppError(ErrorCode.REQUEST_INVALID, message="未知 SOP", status_code=422)
    next_flow = definition.model_copy(update={"revision": definition.revision + 1})
    try:
        with _session() as session:
            if definition.revision == 0:
                session.add(SopFlowModel(scope=scope, revision=1, definition_json=next_flow.model_dump_json()))
            else:
                result = session.execute(update(SopFlowModel).where(
                    SopFlowModel.scope == scope, SopFlowModel.revision == definition.revision,
                ).values(revision=next_flow.revision, definition_json=next_flow.model_dump_json()))
                if result.rowcount != 1:
                    raise IntegrityError("revision conflict", {}, None)
            session.commit()
    except IntegrityError as exc:
        raise AppError(ErrorCode.REQUEST_INVALID, message="流程已被其他人修改，请刷新后再保存", status_code=409) from exc
    return next_flow


def build_workbench_packages(policies: dict[str, bool], switches: dict[str, bool]) -> list[dict[str, Any]]:
    packages = build_default_sop_packages(policies)
    for package in packages:
        scope = package["sop_scope"]
        flow = get_saved_flow(scope) or default_flow(package)
        package.update(enabled=switches[scope], flow_revision=flow.revision,
                       service_sop_version=flow.service_sop_version, entry_rule=flow.entry_rule.model_dump(), layout={key: pos.model_dump() for key, pos in flow.layout.items()})
        package["entry"]["start_step_id"] = flow.start_step_id
        package["steps"] = [
            {**node.model_dump(), **({"node_id": f"{scope}.{node.step_id}"} if node.type == "agent_stage" else {})}
            for node in flow.steps
        ]
        package["transitions"] = [edge.model_dump() for edge in flow.transitions]
        if flow.revision:
            package["version"] = f"1.0.{flow.revision}"
            package["description"] = "按已保存的入口条件、连线和节点配置执行。"
            package["entry"]["events"] = ["按工作台入口条件触发"]
        package["outcomes"] = [{"outcome_id": "complete", "name": "本分支结束", "terminal": True,
                                "next_package_id": None, "result_tags": []}] if any(edge.outcome for edge in flow.transitions) else []
    return packages


def entry_matches(flow: SopFlowDefinition, tags: list[str] | set[str]) -> bool:
    values = {str(tag).strip().rpartition(":")[2] for tag in tags}
    rule = flow.entry_rule
    if (rule.any_tags and not set(rule.any_tags) & values) or not set(rule.required_tags).issubset(values) or set(rule.excluded_tags) & values:
        return False
    categories = get_tag_categories()
    return all(key in categories and values.intersection(value.name for value in categories[key].values)
               for key in rule.tag_categories)


def scheduled_nodes(scope: str) -> list | None:
    flow = get_saved_flow(scope)
    return [node for node in flow.steps if node.schedule] if flow else None


def dialogue_choices(flow: SopFlowDefinition, previous: str | None) -> set[str]:
    nodes = {node.step_id: node for node in flow.steps}
    choices, visited = set(), set()
    def visit(key):
        if key in visited:
            return
        visited.add(key)
        if nodes[key].type == "agent_stage":
            choices.add(key)
        elif nodes[key].type != "action":
            for edge in flow.transitions:
                if edge.from_step == key and edge.to_step:
                    visit(edge.to_step)
    # New questions may restart an entry branch; progression within a branch follows its edges.
    visit(flow.start_step_id)
    if previous in nodes and nodes[previous].type == "agent_stage":
        choices.add(previous)
        for edge in flow.transitions:
            if edge.from_step == previous and edge.to_step:
                visit(edge.to_step)
    return choices


def _cursor_key(message, scope: str) -> str:
    identity = [getattr(message, "tenant_id", ""), message.user_id, message.session_id, scope]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def execution_version(flow: SopFlowDefinition) -> int:
    payload = flow.model_dump(exclude={"revision", "layout"})
    return int(hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:7], 16)


def load_cursor(message, scope: str, flow: SopFlowDefinition) -> str | None:
    with _session() as session:
        row = session.get(SopFlowCursorModel, _cursor_key(message, scope))
        return row.step_id if row and row.revision == execution_version(flow) else None


def save_cursor(message, scope: str, flow: SopFlowDefinition, step_id: str) -> None:
    with _session() as session:
        session.merge(SopFlowCursorModel(cursor_key=_cursor_key(message, scope), scope=scope,
                                        revision=execution_version(flow), step_id=step_id))
        session.commit()


def execution_prompt(scope: str, flow: SopFlowDefinition, previous: str | None) -> str:
    available = dialogue_choices(flow, previous)
    return "\n工作台生效流程（替代旧提示词中的节点清单、步骤顺序与推进目标；只能使用列出的本轮节点）：\n" + json.dumps({
        "scope": scope, "revision": flow.revision, "entry": flow.start_step_id,
        "current_step": previous, "allowed_sop_nodes": ["general.reply", *[f"{scope}.{key}" for key in sorted(available)]],
        "nodes": [{"node_id": f"{scope}.{node.step_id}", "name": node.name,
                   "goal": node.goal, "directions": node.directions} for node in flow.steps if node.type == "agent_stage"],
        "transitions": [edge.model_dump() for edge in flow.transitions],
        "rules": "客户当前需要优先于节点目标。普通答疑、分享、道谢或收尾可用 general.reply，不要求推进或补齐旧目标。需要业务动作时按连线 label 判断，条件未满足留在当前节点，新问题可重入入口分支。定时动作由后台负责，不在对话中执行或补发。",
    }, ensure_ascii=False)


def _upgrade_service_flow(flow: SopFlowDefinition) -> SopFlowDefinition:
    package = next(p for p in build_default_sop_packages({}) if p["sop_scope"] == "service")
    updated = default_flow(package)
    standard_ids = {node.step_id for node in updated.steps} | {"story"}
    previous = {node.step_id: node for node in flow.steps}
    for node in updated.steps:
        old = previous.get(node.step_id)
        if old and node.type == "agent_stage" and node.step_id != "repurchase_discovery":
            node.handoff_enabled = old.handoff_enabled
        if node.step_id in flow.layout:
            updated.layout[node.step_id] = flow.layout[node.step_id]
    for node in flow.steps:
        if node.step_id not in standard_ids:
            updated.steps.append(node)
            updated.layout[node.step_id] = flow.layout[node.step_id]
    for edge in flow.transitions:
        if edge.from_step != "story" and edge.to_step != "story" and (edge.from_step not in standard_ids or (edge.to_step and edge.to_step not in standard_ids)):
            updated.transitions.append(edge)
    updated.start_step_id = flow.start_step_id
    updated.entry_rule = flow.entry_rule.model_copy(deep=True)
    if updated.entry_rule.required_tags == ["服务中"] and not updated.entry_rule.any_tags:
        updated.entry_rule.required_tags = []
        updated.entry_rule.any_tags = ["服务中", "抖音已购", "微信已购"]
    updated.revision = flow.revision + 1
    return SopFlowDefinition.model_validate(updated.model_dump())
