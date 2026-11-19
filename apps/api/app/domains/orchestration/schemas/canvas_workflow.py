from typing import Annotated, Literal
from copy import deepcopy
import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.orchestration.schemas.definitions import Identifier


class CanvasModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodePosition(CanvasModel):
    x: float = Field(ge=-10000, le=10000, allow_inf_nan=False)
    y: float = Field(ge=-10000, le=10000, allow_inf_nan=False)


def branch_name(index: int) -> str:
    number = index + 1
    digits = "零一二三四五六七八九"
    if number < 10:
        suffix = digits[number]
    elif number < 100:
        tens, units = divmod(number, 10)
        suffix = (digits[tens] if tens > 1 else "") + "十" + (digits[units] if units else "")
    else:
        suffix = str(number)
    return "分支" + suffix


class CanvasBranch(CanvasModel):
    id: Identifier
    condition: str = Field(default="", max_length=4000)


class CanvasContentSource(CanvasModel):
    library: Literal["care_manual", "cloud_video", "cloud_image", "beginner_care", "care_course", "sales_activity"]
    category: str = Field(default="", max_length=120)
    selection: Literal["auto", "specific"] = "auto"
    item_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def category_matches_library(self):
        self.category = self.category.strip()
        if self.library not in {"cloud_video", "cloud_image"} and self.category:
            raise ValueError("只有云端视频和图文需要内容分类")
        if len(self.item_ids) != len(set(self.item_ids)):
            raise ValueError("选择的内容不能重复")
        if self.selection == "auto" and self.item_ids:
            raise ValueError("自动匹配不能同时指定内容条目")
        return self


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
    branches: list[CanvasBranch] = Field(default_factory=list, max_length=150)
    send_time: str = Field(default="", pattern=r"^(?:|(?:[01]\d|2[0-3]):[0-5]\d)$")
    send_cycle: Literal["daily", "every_three_days", "weekly"] = "daily"
    send_weekday: int | None = Field(default=None, ge=0, le=6)
    content_source: CanvasContentSource | None = None


class CanvasEdge(CanvasModel):
    id: Identifier
    source: Identifier
    target: Identifier
    label: str = Field(default="", max_length=200)
    source_branch: Identifier | None = None


class CanvasDefinition(CanvasModel):
    name: str = Field(max_length=80)
    nodes: list[CanvasNode] = Field(default_factory=list, max_length=60)
    edges: list[CanvasEdge] = Field(default_factory=list, max_length=150)

    @model_validator(mode="before")
    @classmethod
    def legacy_controls(cls, value):
        if not isinstance(value, dict):
            return value
        data = deepcopy(value)
        edges = data.get("edges", [])
        if not isinstance(data.get("nodes", []), list) or not isinstance(edges, list):
            return data
        for node in data.get("nodes", []):
            if not isinstance(node, dict):
                continue
            if node.get("type") == "scheduled_send" and "content_source" not in node:
                # Only recover the explicit material marker written by the SOP importer.
                action = node.get("action", "")
                categories = re.findall(r"使用「([^「」\n]{1,120})」类型的素材。", action) if isinstance(action, str) else []
                if len(set(categories)) == 1:
                    node["content_source"] = {"library": "cloud_video", "category": categories[0]}
                    node["action"] = re.sub(r"使用「[^「」\n]{1,120}」类型的素材。\n?", "", action).strip()
            if node.get("type") == "condition" and "branches" not in node:
                # Old labels held conditions; stable per-branch IDs now connect them to exits.
                branches = []
                for edge in edges:
                    if isinstance(edge, dict) and edge.get("source") == node.get("id"):
                        branch_id = f"branch_{len(branches) + 1}"
                        branches.append({"id": branch_id, "condition": edge.get("label", "")})
                        edge["source_branch"] = branch_id
                        edge["label"] = branch_name(len(branches) - 1)
                node["branches"] = branches
            if node.get("type") == "scheduled_send" and "send_time" not in node:
                rule = node.get("schedule_rule", "")
                if not isinstance(rule, str):
                    continue
                schedule = re.fullmatch(r"(?:北京时间)?(每天|每三天|每3天|每周[一二三四五六日天]?|周[一二三四五六日天])((?:[01]\d|2[0-3]):[0-5]\d)(?:[，,]?北京时间)?[。.]?", re.sub(r"\s+", "", rule))
                if schedule:
                    period = schedule.group(1)
                    weekday = re.search(r"周([一二三四五六日天])", period)
                    node["send_time"] = schedule.group(2)
                    node["send_cycle"] = "weekly" if "周" in period else "every_three_days" if period in {"每三天", "每3天"} else "daily"
                    if weekday:
                        node["send_weekday"] = "一二三四五六日".index(weekday.group(1).replace("天", "日"))
        return data

    @model_validator(mode="after")
    def references(self):
        ids = {node.id for node in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("节点标识不能重复")
        if len({edge.id for edge in self.edges}) != len(self.edges):
            raise ValueError("连线标识不能重复")
        if len({(edge.source, edge.source_branch, edge.target) for edge in self.edges}) != len(self.edges):
            raise ValueError("同一条连线不能重复")
        if any(edge.source not in ids or edge.target not in ids for edge in self.edges):
            raise ValueError("连线引用了不存在的节点")
        by_id = {node.id: node for node in self.nodes}
        for node in self.nodes:
            if len({branch.id for branch in node.branches}) != len(node.branches):
                raise ValueError("节点内的分支标识不能重复")
        connected_branches = set()
        for edge in self.edges:
            source = by_id[edge.source]
            if source.type == "condition":
                branch_ids = [branch.id for branch in source.branches]
                if edge.source_branch not in branch_ids:
                    raise ValueError("条件分支连线必须引用该节点内的有效分支")
                key = (edge.source, edge.source_branch)
                if key in connected_branches:
                    raise ValueError("每个分支只能连接一个后续节点")
                connected_branches.add(key)
                edge.label = branch_name(branch_ids.index(edge.source_branch))
            elif edge.source_branch is not None:
                raise ValueError("只有条件分支节点的连线可以引用分支")
        return self


class CreateCanvas(CanvasModel):
    name: str = Field(default="未命名流程", min_length=1, max_length=80)


class SaveCanvas(CanvasModel):
    revision: int = Field(ge=1)
    definition: CanvasDefinition


class PublishCanvas(CanvasModel):
    revision: int = Field(ge=1)


class CanvasChatMessage(CanvasModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class EditCanvas(SaveCanvas):
    message: str = Field(min_length=1, max_length=4000)
    history: list[CanvasChatMessage] = Field(default_factory=list, max_length=20)
    selected_node_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_context(self):
        self.message = self.message.strip()
        if not self.message:
            raise ValueError("请先输入要调整的内容")
        if self.selected_node_id and not any(node.id == self.selected_node_id for node in self.definition.nodes):
            raise ValueError("选中的节点不属于当前流程")
        return self


class CanvasEdits(CanvasModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    upsert_nodes: list[CanvasNode] = Field(default_factory=list, max_length=60)
    remove_node_ids: list[Identifier] = Field(default_factory=list, max_length=60)
    upsert_edges: list[CanvasEdge] = Field(default_factory=list, max_length=150)
    remove_edge_ids: list[Identifier] = Field(default_factory=list, max_length=150)


class CanvasChatAnswer(CanvasModel):
    reply: str = Field(min_length=1, max_length=4000)
    edits: CanvasEdits | None = None


class CanvasIssue(CanvasModel):
    node_id: Identifier | None = None
    field: Literal["name", "trigger_condition", "action", "exit_condition", "schedule_rule", "send_content_type", "branch_mode", "handoff_target", "branches", "send_time", "send_cycle", "content_source", "graph"]
    message: str = Field(min_length=1, max_length=1000)
    suggestion: str = Field(min_length=1, max_length=2000)


class NodeUnderstanding(CanvasModel):
    node_id: Identifier
    understanding: str = Field(min_length=1, max_length=5000)


class CanvasReview(CanvasModel):
    summary: str = Field(default="", max_length=8000)
    nodes: list[NodeUnderstanding] = Field(default_factory=list, max_length=60)
    issues: list[CanvasIssue] = Field(max_length=500)


class ResolvedCanvasIssue(CanvasModel):
    index: int = Field(ge=0, lt=500)
    reason: str = Field(min_length=1, max_length=1000)


class CanvasReviewDecision(CanvasReview):
    resolved_issues: list[ResolvedCanvasIssue] = Field(default_factory=list, max_length=500)


class FixCanvasIssue(SaveCanvas):
    review_revision: int = Field(ge=1)
    issue: CanvasIssue


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
        fields = ("trigger_condition", "action", "exit_condition") if node.type == "general" else ("trigger_condition",) if node.type == "start" else ()
        labels = {"trigger_condition": "触发条件", "action": "具体行动", "exit_condition": "退出条件"}
        if node.type == "scheduled_send":
            fields = ("send_time",)
            labels.update(send_time="发送时间")
            if node.content_source is None:
                issue(node.id, "content_source", "请选择发送内容库", "选择手册、云端视频、云端图文、课程或活动。")
            else:
                if node.content_source.library in {"cloud_video", "cloud_image"} and not node.content_source.category:
                    issue(node.id, "content_source", "请选择内容分类", "从内容分类中选择养护科普等实际存在的分类。")
                if node.content_source.selection == "specific" and not node.content_source.item_ids:
                    issue(node.id, "content_source", "还没有指定具体内容", "搜索并选择至少一项内容，或改为自动匹配。")
        elif node.type == "condition":
            if len(node.branches) < 2:
                issue(node.id, "branches", "条件分支至少需要两个分支", "点击增加分支，填写不同情况下的条件。")
            linked = {edge.source_branch for edge in flow.edges if edge.source == node.id}
            for index, branch in enumerate(node.branches):
                if not branch.condition.strip():
                    issue(node.id, "branches", f"{branch_name(index)}还没有填写条件", "在对应分支文本框中说明什么情况下进入这条路径。")
                if branch.id not in linked:
                    issue(node.id, "graph", f"{branch_name(index)}还没有连接后续节点", "从该分支右侧圆点连到要执行的节点。")
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
