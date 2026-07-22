from typing import Literal

from pydantic import BaseModel, Field, model_validator


AnnotationStatus = Literal["confirmed", "corrected", "uncertain", "excluded"]


class IntentAnnotationRequest(BaseModel):
    status: AnnotationStatus
    primary_domain: str | None = None
    secondary_domains: list[str] = Field(default_factory=list)
    primary_goal: str | None = None
    secondary_goals: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    scope: Literal["in_scope", "ambiguous", "out_of_scope"] | None = None
    note: str | None = Field(default=None, max_length=2000)
    annotator_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_corrected_labels(self):
        self.secondary_domains = list(dict.fromkeys(self.secondary_domains))
        self.secondary_goals = list(dict.fromkeys(self.secondary_goals))
        self.issues = list(dict.fromkeys(self.issues))
        if self.status == "corrected" and (
            not self.primary_domain or not self.primary_goal or not self.scope
        ):
            raise ValueError("修正标注必须填写 Domain、Goal 和 scope")
        if self.primary_domain and self.primary_domain in self.secondary_domains:
            raise ValueError("主 Domain 不能同时出现在次要 Domain 中")
        if self.primary_goal and self.primary_goal in self.secondary_goals:
            raise ValueError("主 Goal 不能同时出现在次要 Goal 中")
        return self


class IntentAnnotationItem(BaseModel):
    id: int
    trace_id: str
    status: AnnotationStatus
    primary_domain: str | None = None
    secondary_domains: list[str] = Field(default_factory=list)
    primary_goal: str | None = None
    secondary_goals: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    scope: str | None = None
    note: str | None = None
    annotator_id: str
    taxonomy_version: str
    created_at: str


class IntentObservationItem(BaseModel):
    id: int
    trace_id: str
    channel: str
    user_id: str
    session_id: str | None = None
    message_id: str | None = None
    tenant_id: str | None = None
    conversation_id: str | None = None
    conversation_message_ids: list[int] = Field(default_factory=list)
    user_message: str
    taxonomy_version: str
    classifier_source: str
    classifier_provider: str | None = None
    classifier_model: str | None = None
    primary_domain: str | None = None
    secondary_domains: list[str] = Field(default_factory=list)
    primary_goal: str | None = None
    secondary_goals: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    scope: str
    evidence: list[dict] = Field(default_factory=list)
    confidence: float | None = None
    intent_reason: str | None = None
    predicted_route: str | None = None
    final_route: str | None = None
    primary_intent: str | None = None
    sales_stage: str | None = None
    annotation_status: str = "pending"
    annotation_origin: str = "automatic"
    needs_review: bool = True
    latest_annotation: IntentAnnotationItem | None = None
    created_at: str
    updated_at: str


class IntentObservationDetail(IntentObservationItem):
    context: list[dict] = Field(default_factory=list)
    raw_prediction: dict = Field(default_factory=dict)
    candidate_labels: list[dict] = Field(default_factory=list)
    annotation_history: list[IntentAnnotationItem] = Field(default_factory=list)


class IntentObservationListResponse(BaseModel):
    items: list[IntentObservationItem]
    total: int
    page: int
    page_size: int
    pending_count: int = 0
    reviewed_count: int = 0
    accepted_count: int = 0
    corrected_count: int = 0
