from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.orchestration.schemas.definitions import Identifier


class CanvasModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodePosition(CanvasModel):
    x: float = Field(ge=-10000, le=10000, allow_inf_nan=False)
    y: float = Field(ge=-10000, le=10000, allow_inf_nan=False)


class CanvasNode(CanvasModel):
    id: Identifier
    type: Literal["start", "general", "scheduled_send", "condition", "handoff", "end"]
    name: str = Field(max_length=80)
    position: NodePosition
    trigger_condition: str = Field(default="", max_length=4000)
    action: str = Field(default="", max_length=6000)
    exit_condition: str = Field(default="", max_length=4000)
    schedule_rule: str = Field(default="", max_length=2000)
    send_content_type: Literal["text", "image", "video", "link", "product_card", "mixed"] = "text"
    branch_mode: Literal["first_match", "all_matches"] = "first_match"
    handoff_target: str = Field(default="", max_length=500)


class CanvasEdge(CanvasModel):
    id: Identifier
    source: Identifier
    target: Identifier
    label: str = Field(default="", max_length=200)


class CanvasDefinition(CanvasModel):
    name: str = Field(max_length=80)
    nodes: list[CanvasNode] = Field(default_factory=list, max_length=60)
    edges: list[CanvasEdge] = Field(default_factory=list, max_length=150)

    @model_validator(mode="after")
    def references(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("节点标识不能重复")
        if len({edge.id for edge in self.edges}) != len(self.edges):
            raise ValueError("连线标识不能重复")
        if len({(edge.source, edge.target) for edge in self.edges}) != len(self.edges):
            raise ValueError("同一条连线不能重复")
        if any(edge.source not in ids or edge.target not in ids for edge in self.edges):
            raise ValueError("连线引用了不存在的节点")
        return self


class CreateCanvas(CanvasModel):
    name: str = Field(default="未命名流程", min_length=1, max_length=80)


class SaveCanvas(CanvasModel):
    revision: int = Field(ge=1)
    definition: CanvasDefinition


class PublishCanvas(CanvasModel):
    revision: int = Field(ge=1)


class CanvasIssue(CanvasModel):
    node_id: Identifier | None = None
    field: Literal["name", "trigger_condition", "action", "exit_condition", "schedule_rule", "send_content_type", "branch_mode", "handoff_target", "graph"]
    message: str = Field(min_length=1, max_length=1000)
    suggestion: str = Field(min_length=1, max_length=2000)


class NodeUnderstanding(CanvasModel):
    node_id: Identifier
    understanding: str = Field(min_length=1, max_length=5000)


class CanvasReview(CanvasModel):
    summary: str = Field(max_length=8000)
    nodes: list[NodeUnderstanding] = Field(max_length=60)
    issues: list[CanvasIssue] = Field(max_length=500)


def publication_issues(flow: CanvasDefinition) -> list[CanvasIssue]:
    issues: list[CanvasIssue] = []

    def issue(node_id, field, message, suggestion):
        issues.append(CanvasIssue(node_id=node_id, field=field, message=message, suggestion=suggestion))

    if not flow.name.strip():
        issue(None, "name", "请填写流程名称", "为流程填写能说明业务目的的名称。")
    starts = [node for node in flow.nodes if node.type == "start"]
    ends = [node for node in flow.nodes if node.type == "end"]
    if len(starts) != 1:
        issue(None, "graph", "流程需要且只能有一个开始节点", "在开始节点的触发条件中描述标签、加好友或时间触发。")
    if not ends:
        issue(None, "graph", "流程缺少结束节点", "添加结束节点，并连接业务完成后的路径。")
    children = {node.id: set() for node in flow.nodes}
    parents = {node.id: set() for node in flow.nodes}
    for edge in flow.edges:
        children[edge.source].add(edge.target)
        parents[edge.target].add(edge.source)
        if edge.source == edge.target:
            issue(edge.source, "graph", "节点不能连接自己", "未满足退出条件时可留在当前节点，无需连接自己。")
    for node in flow.nodes:
        if not node.name.strip():
            issue(node.id, "name", "节点名称不能为空", "填写便于识别的业务名称。")
        fields = ("trigger_condition", "action", "exit_condition") if node.type not in {"start", "end"} else ("trigger_condition",) if node.type == "start" else ()
        labels = {"trigger_condition": "触发条件", "action": "具体行动", "exit_condition": "退出条件"}
        if node.type == "scheduled_send":
            fields += ("schedule_rule",)
            labels.update(trigger_condition="发送前条件", action="发送内容说明", exit_condition="停止条件", schedule_rule="发送时间与周期")
        elif node.type == "condition":
            labels.update(trigger_condition="判断依据", action="分支规则", exit_condition="未命中时的处理")
            branches = [edge for edge in flow.edges if edge.source == node.id]
            if len(branches) < 2:
                issue(node.id, "graph", "条件分支至少需要两个出口", "连接不同的后续节点，并填写各自的分支条件。")
            if any(not edge.label.strip() for edge in branches):
                issue(node.id, "graph", "条件分支的连线缺少判断条件", "为每条向外连线填写条件；兜底路径可填写“其他情况”。")
        elif node.type == "handoff":
            fields += ("handoff_target",)
            labels.update(action="交接说明", exit_condition="交接后的处理", handoff_target="转交对象")
        for field in fields:
            if not getattr(node, field).strip():
                issue(node.id, field, f"请填写{labels[field]}", f"用文字说明此节点的{labels[field]}，无需编写代码。")
        if node.type == "start" and parents[node.id]:
            issue(node.id, "graph", "开始节点不能接收连线", "将返回路径连接到一般节点。")
        if node.type == "end" and children[node.id]:
            issue(node.id, "graph", "结束节点不能继续连接后续节点", "删除结束节点的向外连线。")
        if node.type != "end" and not children[node.id]:
            issue(node.id, "graph", "节点缺少后续路径", "将节点连接到下一步或结束节点。")

    def reachable(seeds, adjacency):
        seen, pending = set(), list(seeds)
        while pending:
            key = pending.pop()
            if key not in seen:
                seen.add(key)
                pending.extend(adjacency[key] - seen)
        return seen

    if len(starts) == 1:
        connected = reachable([starts[0].id], children)
        for node in flow.nodes:
            if node.id not in connected:
                issue(node.id, "graph", "节点无法从开始节点到达", "连接此节点，或删除不参与流程的节点。")
    if ends:
        finishing = reachable([node.id for node in ends], parents)
        for node in flow.nodes:
            if node.id not in finishing:
                issue(node.id, "graph", "此路径无法到达结束节点", "为这条路径补充可以结束的出口。")
    return issues
