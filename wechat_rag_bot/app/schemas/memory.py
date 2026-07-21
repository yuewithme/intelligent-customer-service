from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, RootModel, field_validator, model_validator


MEMORY_SCHEMA_VERSION = "memory.v1"

MemoryActorType = Literal[
    "customer", "assistant", "human_agent", "system", "business_system"
]
MemoryEventType = Literal[
    "customer_message",
    "assistant_message",
    "human_message",
    "contact_snapshot",
    "commerce_event",
    "tool_observation",
    "manual_correction",
    "image_observation",
]
MemorySensitivity = Literal["public", "internal", "sensitive", "restricted"]
MemoryOperationType = Literal[
    "ADD", "REINFORCE", "SUPERSEDE", "DISPUTE", "RESOLVE", "NOOP"
]
MemoryKind = Literal["semantic_fact", "episode"]
MemoryEpisodeType = Literal[
    "product_consultation",
    "purchase",
    "refund",
    "complaint",
    "after_sales",
    "sales_objection",
    "preference_expression",
    "commitment",
]


class RegionFactValue(BaseModel):
    country: str | None = None
    province: str | None = None
    city: str | None = None

    @model_validator(mode="after")
    def require_region_value(self):
        if not any((self.country, self.province, self.city)):
            raise ValueError("region requires country, province, or city")
        return self


class BudgetFactValue(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    scope: str = Field(min_length=1)


class ProductInterestFactValue(BaseModel):
    product_id: str | None = None
    category: str | None = None
    name: str | None = None

    @model_validator(mode="after")
    def require_one_identifier(self):
        if not any((self.product_id, self.category, self.name)):
            raise ValueError("product interest requires product_id, category, or name")
        return self


class PurchaseStatusFactValue(BaseModel):
    order_id: str = Field(min_length=1)
    status: Literal[
        "pending_payment",
        "paid",
        "fulfilled",
        "completed",
        "refund_pending",
        "refunded",
        "closed",
    ]


class PainPointFactValue(BaseModel):
    topic: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class ServicePreferenceFactValue(BaseModel):
    topic: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ServiceCommitmentFactValue(BaseModel):
    owner: Literal["assistant", "human_agent"]
    action: str = Field(min_length=1)
    due_at: datetime | None = None


class DisplayNameFactValue(RootModel[str]):
    root: str = Field(min_length=1)


class PreferredDetailFactValue(RootModel[Literal["concise", "balanced", "detailed"]]):
    pass


class PreferredChannelFactValue(RootModel[Literal["wechat", "phone", "email", "sms"]]):
    pass


FACT_VALUE_MODELS: dict[str, type[BaseModel]] = {
    "identity.display_name": DisplayNameFactValue,
    "location.region": RegionFactValue,
    "communication.preferred_detail": PreferredDetailFactValue,
    "communication.preferred_channel": PreferredChannelFactValue,
    "purchase.budget": BudgetFactValue,
    "purchase.product_interest": ProductInterestFactValue,
    "purchase.status": PurchaseStatusFactValue,
    "service.pain_point": PainPointFactValue,
    "service.preference": ServicePreferenceFactValue,
    "service.commitment": ServiceCommitmentFactValue,
}

FACT_SOURCE_POLICY: dict[str, frozenset[str]] = {
    "identity.display_name": frozenset(
        {"verified_contact_provider", "customer_explicit", "manual_customer_correction"}
    ),
    "location.region": frozenset(
        {"verified_contact_provider", "customer_explicit", "manual_customer_correction"}
    ),
    "communication.preferred_detail": frozenset(
        {"customer_explicit", "manual_customer_correction"}
    ),
    "communication.preferred_channel": frozenset(
        {"customer_explicit", "manual_customer_correction"}
    ),
    "purchase.budget": frozenset(
        {"customer_explicit", "manual_customer_correction"}
    ),
    "purchase.product_interest": frozenset(
        {
            "customer_behavior",
            "customer_explicit",
            "verified_business_system",
            "manual_customer_correction",
        }
    ),
    "purchase.status": frozenset({"verified_business_system"}),
    "service.pain_point": frozenset(
        {"customer_explicit", "manual_customer_correction"}
    ),
    "service.preference": frozenset(
        {"customer_explicit", "manual_customer_correction"}
    ),
    "service.commitment": frozenset(
        {"assistant_commitment", "human_agent_annotation"}
    ),
}


def validate_fact_value(fact_key: str, value: Any) -> Any:
    model = FACT_VALUE_MODELS.get(fact_key)
    if model is None:
        raise ValueError(f"unsupported fact key: {fact_key}")
    validated = model.model_validate(value)
    if isinstance(validated, RootModel):
        return validated.root
    return validated.model_dump(mode="json", exclude_none=True)


class MemorySubjectRead(BaseModel):
    id: str
    tenant_id: str
    status: str
    profile_version: int
    created_at: datetime
    updated_at: datetime


class MemoryIdentityRead(BaseModel):
    id: int
    subject_id: str
    tenant_id: str
    channel: str
    owner_external_id: str
    external_user_id: str
    identity_source: str
    verified: bool
    created_at: datetime
    updated_at: datetime


class MemoryEventCreate(BaseModel):
    schema_version: Literal["memory.v1"] = MEMORY_SCHEMA_VERSION
    event_uid: str = Field(min_length=1, max_length=512)
    tenant_id: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=36)
    session_id: str | None = Field(default=None, max_length=256)
    event_type: MemoryEventType
    actor_type: MemoryActorType
    content: dict[str, Any]
    source_type: str = Field(min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=512)
    trace_id: str | None = Field(default=None, max_length=128)
    occurred_at: datetime
    sensitivity: MemorySensitivity = "internal"

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value


class MemoryEventRead(BaseModel):
    id: int
    schema_version: str
    event_uid: str
    tenant_id: str
    subject_id: str
    session_id: str | None
    event_type: str
    actor_type: str
    content: dict[str, Any]
    source_type: str
    source_id: str
    trace_id: str | None
    occurred_at: datetime
    ingested_at: datetime
    sensitivity: str


class MemoryEventAppendResult(BaseModel):
    event: MemoryEventRead
    created: bool


class LegacyProfileProjection(BaseModel):
    user_id: str
    tenant_id: str
    channel: str
    subject_id: str
    basic_info: dict[str, Any] = Field(default_factory=dict)
    customer_tags: list[str] = Field(default_factory=list)
    product_interests: list[str] = Field(default_factory=list)
    pain_points: list[str] = Field(default_factory=list)
    preference_summary: str | None = None
    active_opportunity: dict[str, Any] = Field(default_factory=dict)


class MemoryOperationCandidate(BaseModel):
    operation: MemoryOperationType
    memory_kind: MemoryKind
    fact_key: str | None = None
    fact_value: Any = None
    evidence_event_ids: list[int] = Field(default_factory=list)
    source_type: str | None = None
    valid_from: datetime | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    supersedes_fact_id: int | None = None
    reason: str = ""
    episode_type: MemoryEpisodeType | None = None
    title: str | None = Field(default=None, max_length=256)
    summary: str | None = Field(default=None, max_length=4000)
    outcome: str | None = Field(default=None, max_length=4000)
    importance: float = Field(default=0.5, ge=0, le=1)
    started_at: datetime | None = None
    ended_at: datetime | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.operation == "NOOP":
            return self
        if not self.evidence_event_ids:
            raise ValueError("memory operation requires evidence_event_ids")
        if self.memory_kind == "semantic_fact":
            if not self.fact_key or self.fact_value is None or not self.source_type:
                raise ValueError(
                    "semantic fact requires fact_key, fact_value, and source_type"
                )
        elif not all(
            (
                self.episode_type,
                self.title,
                self.summary,
                self.started_at,
                self.source_type,
            )
        ):
            raise ValueError(
                "episode requires type, title, summary, start time, and source"
            )
        return self


class ValidatedMemoryOperation(MemoryOperationCandidate):
    normalized_value: str | None = None


class MemoryJobRead(BaseModel):
    id: int
    tenant_id: str
    subject_id: str
    job_type: str
    dedup_key: str
    trigger_event_id: int
    status: str
    attempts: int
    available_at: datetime
    locked_at: datetime | None
    locked_by: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class MemoryApplyResult(BaseModel):
    memory_kind: MemoryKind
    record_id: int | None = None
    created: bool
    operation: MemoryOperationType


class MemoryQueryPlan(BaseModel):
    requested_fact_keys: list[str] = Field(default_factory=list)
    include_episodes: bool = True
    episode_types: list[MemoryEpisodeType] = Field(default_factory=list)
    require_verified_business: bool = False
    query_terms: list[str] = Field(default_factory=list)


class MemoryEvidenceItem(BaseModel):
    event_id: int
    event_uid: str
    event_type: str
    actor_type: str
    source_type: str
    content: dict[str, Any]
    occurred_at: datetime


class MemoryFactContext(BaseModel):
    fact_id: int
    fact_key: str
    fact_value: Any
    source_type: str
    confidence: float
    valid_from: datetime
    evidence_event_ids: list[int] = Field(default_factory=list)


class MemoryEpisodeContext(BaseModel):
    episode_id: int
    episode_type: str
    title: str
    summary: str
    outcome: str | None = None
    importance: float
    started_at: datetime
    ended_at: datetime | None = None
    score: float
    evidence_event_ids: list[int] = Field(default_factory=list)


class MemoryContext(BaseModel):
    schema_version: Literal["memory_context.v1"] = "memory_context.v1"
    subject_id: str
    as_of: datetime
    current_facts: list[MemoryFactContext] = Field(default_factory=list)
    relevant_episodes: list[MemoryEpisodeContext] = Field(default_factory=list)
    working_state: dict[str, Any] = Field(default_factory=dict)
    verified_business_facts: list[MemoryFactContext] = Field(default_factory=list)
    unresolved_conflicts: list[MemoryFactContext] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence: list[MemoryEvidenceItem] = Field(default_factory=list)
