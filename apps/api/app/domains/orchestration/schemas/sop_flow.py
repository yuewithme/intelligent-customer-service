from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.domains.orchestration.schemas.definitions import CanvasPosition, Identifier

SopScope = Literal["first_order", "service", "seeding"]


class FlowEntryRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_tags: list[str] = Field(default_factory=list, max_length=30)
    tag_categories: list[str] = Field(default_factory=list, max_length=15)
    excluded_tags: list[str] = Field(default_factory=list, max_length=30)
    fallback_only: bool = False


class FlowSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    copy_type: Literal["名品故事", "养护科普", "话题种草"]
    match_preferences: bool = False


class FlowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_id: Identifier
    name: str = Field(min_length=1, max_length=80)
    type: Literal["agent_stage", "decision", "wait", "action"]
    description: str = Field(default="", max_length=2000)
    goal: str = Field(default="", max_length=2000)
    directions: list[str] = Field(default_factory=list, max_length=20)
    handoff_enabled: bool = False
    require_product_interest: bool = False
    schedule: FlowSchedule | None = None

    @model_validator(mode="after")
    def executable(self):
        if (self.type == "action") != (self.schedule is not None):
            raise ValueError("定时触达节点必须配置时间和素材类型，其他节点不能配置定时动作")
        if self.type == "agent_stage" and not self.goal.strip():
            raise ValueError("对话节点必须填写执行目标")
        if self.type != "agent_stage" and self.handoff_enabled:
            raise ValueError("只有对话节点支持转人工")
        if self.type != "agent_stage" and self.require_product_interest:
            raise ValueError("只有对话节点支持产品了解意向条件")
        return self


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transition_id: Identifier
    from_step: Identifier
    to_step: Identifier | None = None
    outcome: Literal["complete"] | None = None
    label: str = Field(min_length=1, max_length=128)
    priority: int = Field(default=100, ge=0, le=10000)

    @model_validator(mode="after")
    def destination(self):
        if (self.to_step is None) == (self.outcome is None):
            raise ValueError("连线必须选择一个目标节点或结束分支")
        return self


class SopFlowDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    start_step_id: Identifier
    entry_rule: FlowEntryRule
    steps: list[FlowNode] = Field(min_length=1, max_length=60)
    transitions: list[FlowEdge] = Field(default_factory=list, max_length=150)
    layout: dict[str, CanvasPosition]

    @model_validator(mode="after")
    def graph(self):
        ids = {node.step_id for node in self.steps}
        if len(ids) != len(self.steps) or self.start_step_id not in ids:
            raise ValueError("节点标识必须唯一，入口必须指向现有节点")
        if set(self.layout) != ids:
            raise ValueError("每个节点必须有且只有一个画布坐标")
        if any(not (0 <= p.x <= 12000 and 0 <= p.y <= 12000) for p in self.layout.values()):
            raise ValueError("节点坐标必须在画布范围内")
        edge_ids = {edge.transition_id for edge in self.transitions}
        pairs = {(edge.from_step, edge.to_step) for edge in self.transitions}
        if len(edge_ids) != len(self.transitions) or len(pairs) != len(self.transitions):
            raise ValueError("不能重复添加同一条连线")
        children = {key: [] for key in ids}
        for edge in self.transitions:
            if edge.from_step not in ids or (edge.to_step is not None and edge.to_step not in ids):
                raise ValueError("连线不能引用已删除的节点")
            if edge.to_step:
                children[edge.from_step].append(edge.to_step)
        seen, visiting = set(), set()
        def visit(key):
            if key in visiting:
                raise ValueError("不支持循环连线；每日重复由触达时间自动调度")
            if key in seen:
                return
            visiting.add(key)
            for child in children[key]:
                visit(child)
            visiting.remove(key)
            seen.add(key)
        visit(self.start_step_id)
        if seen != ids:
            raise ValueError("所有节点都必须从入口连通，请连接或删除孤立节点")
        by_id = {node.step_id: node for node in self.steps}
        automatic = set()
        def auto_visit(key):
            if key in automatic or by_id[key].type == "agent_stage":
                return
            automatic.add(key)
            for child in children[key]:
                auto_visit(child)
        auto_visit(self.start_step_id)
        for node in self.steps:
            if node.type == "action" and (node.step_id not in automatic or children[node.step_id]):
                raise ValueError("定时触达须在独立的自动分支末端，不能串接在对话后或继续连接其他节点")
        return self
