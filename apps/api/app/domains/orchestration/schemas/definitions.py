from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Identifier = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_.-]*$"),
]
SemanticVersion = Annotated[
    str,
    Field(pattern=r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$"),
]


class StrictDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConditionClause(StrictDefinition):
    kind: Literal["fact", "semantic"]
    description: str = Field(min_length=1, max_length=1000)
    path: str | None = Field(default=None, min_length=1, max_length=256)
    operator: Literal[
        "eq",
        "neq",
        "in",
        "not_in",
        "exists",
        "truthy",
        "falsy",
        "contains",
        "gte",
        "lte",
    ] | None = None
    value: Any = None

    @model_validator(mode="after")
    def validate_condition_shape(self):
        if self.kind == "fact":
            if not self.path or not self.operator:
                raise ValueError("fact condition requires path and operator")
            if self.operator not in {"exists", "truthy", "falsy"} and self.value is None:
                raise ValueError(f"{self.operator} condition requires value")
        elif self.path is not None or self.operator is not None or self.value is not None:
            raise ValueError("semantic condition only accepts description")
        return self


class ConditionGroup(StrictDefinition):
    mode: Literal["all", "any"] = "all"
    conditions: list[ConditionClause] = Field(min_length=1)


class RetryPolicy(StrictDefinition):
    max_attempts: int = Field(default=1, ge=1, le=5)
    backoff_seconds: float = Field(default=0, ge=0, le=300)


class CapabilityExecutionSpec(StrictDefinition):
    adapter: Literal["local_handler"] = "local_handler"
    handler_key: Identifier
    timeout_seconds: int = Field(default=15, ge=1, le=300)
    idempotency: Literal["none", "safe", "required_key"] = "none"
    retry: RetryPolicy = Field(default_factory=RetryPolicy)


class CapabilityUiSpec(StrictDefinition):
    visibility: Literal["hidden", "configurable", "draggable"]
    group: str = Field(min_length=1, max_length=64)
    icon: str | None = Field(default=None, max_length=64)
    summary: str = Field(min_length=1, max_length=300)


class CapabilityBusinessSpec(StrictDefinition):
    action: str = Field(min_length=1, max_length=1000)
    data_source: str = Field(min_length=1, max_length=500)
    ai_mode: Literal[
        "automatic",
        "conditional",
        "workflow_only",
        "human_confirm",
    ]
    ai_mode_description: str = Field(min_length=1, max_length=1000)
    customer_contact: Literal[
        "none",
        "reply_support",
        "direct_message",
        "direct_card",
        "conversation_handoff",
    ]
    customer_contact_description: str = Field(min_length=1, max_length=1000)
    staff_notification: bool = False
    permission_description: str = Field(min_length=1, max_length=1000)
    result_description: str = Field(min_length=1, max_length=1000)
    risk_level: Literal["low", "medium", "high"]
    risk_description: str = Field(min_length=1, max_length=1000)


class CapabilityManifest(StrictDefinition):
    schema_version: Literal["capability_manifest.v1"] = "capability_manifest.v1"
    capability_id: Identifier
    version: SemanticVersion
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    kind: Literal["query", "action", "human", "internal"]
    status: Literal["draft", "published", "deprecated"] = "draft"
    ui: CapabilityUiSpec
    business: CapabilityBusinessSpec
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    config_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    execution: CapabilityExecutionSpec
    permissions: list[str] = Field(default_factory=list)
    side_effects: list[
        Literal[
            "customer_state",
            "prepared_customer_message",
            "external_write",
            "human_notification",
            "conversation_handoff",
        ]
    ] = Field(default_factory=list)
    preconditions: ConditionGroup | None = None
    usage_guidance: list[str] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list)


class CapabilityCatalog(StrictDefinition):
    schema_version: Literal["capability_catalog.v1"] = "capability_catalog.v1"
    capabilities: list[CapabilityManifest] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_versions(self):
        keys = [(item.capability_id, item.version) for item in self.capabilities]
        if len(keys) != len(set(keys)):
            raise ValueError("capability id and version must be unique")
        return self


class CapabilityDependency(StrictDefinition):
    capability_id: Identifier
    version_constraint: str = Field(min_length=1, max_length=64)
    required: bool = True


class StepCapabilityBinding(StrictDefinition):
    capability_id: Identifier
    usage: Literal["allowed", "required"] = "allowed"
    purpose: str = Field(min_length=1, max_length=500)


class CollectedFact(StrictDefinition):
    key: Identifier
    description: str = Field(min_length=1, max_length=500)
    required_for_completion: bool = False


class StepBase(StrictDefinition):
    step_id: Identifier
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)


class AgentStageStep(StepBase):
    type: Literal["agent_stage"]
    goal: str = Field(min_length=1, max_length=2000)
    directions: list[str] = Field(min_length=1)
    collect: list[CollectedFact] = Field(default_factory=list)
    capabilities: list[StepCapabilityBinding] = Field(default_factory=list)
    completion: ConditionGroup
    max_turns: int | None = Field(default=None, ge=1, le=100)


class DecisionStep(StepBase):
    type: Literal["decision"]
    goal: str = Field(min_length=1, max_length=1000)
    strategy: Literal["rules", "agent", "hybrid"] = "hybrid"


class ActionStep(StepBase):
    type: Literal["action"]
    capability_id: Identifier
    arguments: dict[str, Any] = Field(default_factory=dict)
    on_error_outcome: Identifier | None = None


class DurationSpec(StrictDefinition):
    value: int | None = Field(default=None, ge=1)
    parameter: Identifier | None = None
    unit: Literal["seconds", "minutes", "hours", "days"] = "seconds"

    @model_validator(mode="after")
    def require_value_or_parameter(self):
        if (self.value is None) == (self.parameter is None):
            raise ValueError("duration requires exactly one of value or parameter")
        return self


class WaitStep(StepBase):
    type: Literal["wait"]
    resume_events: list[str] = Field(min_length=1)
    timeout: DurationSpec


ExperienceStep = Annotated[
    AgentStageStep | DecisionStep | ActionStep | WaitStep,
    Field(discriminator="type"),
]


class ExperienceTransition(StrictDefinition):
    transition_id: Identifier
    from_step: Identifier
    to_step: Identifier | None = None
    outcome: Identifier | None = None
    label: str = Field(min_length=1, max_length=128)
    priority: int = Field(default=100, ge=0, le=10000)
    condition: ConditionGroup | None = None

    @model_validator(mode="after")
    def require_single_destination(self):
        if (self.to_step is None) == (self.outcome is None):
            raise ValueError("transition requires exactly one of to_step or outcome")
        return self


class ExperienceEntry(StrictDefinition):
    events: list[str] = Field(min_length=1)
    start_step_id: Identifier
    conditions: ConditionGroup | None = None
    priority: int = Field(default=100, ge=0, le=10000)


class ExperienceOutcome(StrictDefinition):
    outcome_id: Identifier
    name: str = Field(min_length=1, max_length=128)
    terminal: bool = True
    next_package_id: Identifier | None = None
    result_tags: list[str] = Field(default_factory=list)


class CanvasPosition(StrictDefinition):
    x: float
    y: float


class ExperiencePackageDefinition(StrictDefinition):
    schema_version: Literal["experience_package.v1"] = "experience_package.v1"
    package_id: Identifier
    version: SemanticVersion
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    status: Literal["draft", "published", "deprecated"] = "draft"
    tags: list[str] = Field(default_factory=list)
    goals: list[str] = Field(min_length=1)
    entry: ExperienceEntry
    parameters_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    context_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    global_rules: list[str] = Field(min_length=1)
    capability_dependencies: list[CapabilityDependency] = Field(default_factory=list)
    steps: list[ExperienceStep] = Field(min_length=1)
    transitions: list[ExperienceTransition] = Field(min_length=1)
    outcomes: list[ExperienceOutcome] = Field(min_length=1)
    success_metrics: list[str] = Field(default_factory=list)
    layout: dict[str, CanvasPosition] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self):
        step_ids = [step.step_id for step in self.steps]
        outcome_ids = [outcome.outcome_id for outcome in self.outcomes]
        dependency_ids = [item.capability_id for item in self.capability_dependencies]
        transition_ids = [item.transition_id for item in self.transitions]

        self._require_unique("step", step_ids)
        self._require_unique("outcome", outcome_ids)
        self._require_unique("capability dependency", dependency_ids)
        self._require_unique("transition", transition_ids)

        known_steps = set(step_ids)
        known_outcomes = set(outcome_ids)
        known_dependencies = set(dependency_ids)
        parameter_properties = self.parameters_schema.get("properties", {})
        known_parameters = (
            set(parameter_properties)
            if isinstance(parameter_properties, dict)
            else set()
        )
        if self.entry.start_step_id not in known_steps:
            raise ValueError("entry start_step_id must reference an existing step")

        outgoing: dict[str, list[ExperienceTransition]] = {
            step_id: [] for step_id in step_ids
        }
        for transition in self.transitions:
            if transition.from_step not in known_steps:
                raise ValueError(
                    f"transition {transition.transition_id} has unknown from_step"
                )
            if transition.to_step and transition.to_step not in known_steps:
                raise ValueError(
                    f"transition {transition.transition_id} has unknown to_step"
                )
            if transition.outcome and transition.outcome not in known_outcomes:
                raise ValueError(
                    f"transition {transition.transition_id} has unknown outcome"
                )
            outgoing[transition.from_step].append(transition)

        for step_id, items in outgoing.items():
            if not items:
                raise ValueError(f"step {step_id} requires an outgoing transition")
            if sum(item.condition is None for item in items) > 1:
                raise ValueError(f"step {step_id} has multiple default transitions")

        for step in self.steps:
            referenced = []
            if isinstance(step, AgentStageStep):
                referenced.extend(item.capability_id for item in step.capabilities)
            elif isinstance(step, ActionStep):
                referenced.append(step.capability_id)
                if step.on_error_outcome and step.on_error_outcome not in known_outcomes:
                    raise ValueError(
                        f"step {step.step_id} has unknown on_error_outcome"
                    )
            elif (
                isinstance(step, WaitStep)
                and step.timeout.parameter
                and step.timeout.parameter not in known_parameters
            ):
                raise ValueError(
                    f"step {step.step_id} references unknown timeout parameter"
                )
            missing = set(referenced) - known_dependencies
            if missing:
                raise ValueError(
                    f"step {step.step_id} uses undeclared capabilities: {sorted(missing)}"
                )

        unknown_layout_steps = set(self.layout) - known_steps
        if unknown_layout_steps:
            raise ValueError(
                f"layout references unknown steps: {sorted(unknown_layout_steps)}"
            )

        reachable = {self.entry.start_step_id}
        frontier = [self.entry.start_step_id]
        while frontier:
            current = frontier.pop()
            for transition in outgoing[current]:
                if transition.to_step and transition.to_step not in reachable:
                    reachable.add(transition.to_step)
                    frontier.append(transition.to_step)
        unreachable = known_steps - reachable
        if unreachable:
            raise ValueError(f"unreachable steps: {sorted(unreachable)}")
        return self

    @staticmethod
    def _require_unique(label: str, values: list[str]) -> None:
        if len(values) != len(set(values)):
            raise ValueError(f"{label} ids must be unique")
