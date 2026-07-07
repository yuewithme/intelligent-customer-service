from pydantic import BaseModel, Field


class ChatLogItem(BaseModel):
    trace_id: str
    channel: str
    user_id: str
    session_id: str | None = None
    message_id: str | None = None
    kb_id: str | None = None
    tenant_id: str | None = None

    user_message: str
    answer: str | None = None

    route: str | None = None
    reply_type: str | None = None
    primary_intent: str | None = None
    secondary_intents: list[str] = Field(default_factory=list)
    sales_stage: str | None = None
    confidence: float | None = None

    template_id: str | None = None
    next_action: str | None = None
    need_human: bool = False

    sources: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)

    latency_ms: int | None = None
    status: str = "success"
    error_code: int | None = None
    error_message: str | None = None
    created_at: str


class ChatLogDetail(ChatLogItem):
    template_score: float | None = None
    policy_reason: str | None = None
    intent_reason: str | None = None
    stage_latencies: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ChatLogListResponse(BaseModel):
    items: list[ChatLogItem]
    total: int
    page: int
    page_size: int


class ChatLogStats(BaseModel):
    total: int
    success_count: int
    failed_count: int
    avg_latency_ms: float | None = None
    route_counts: dict = Field(default_factory=dict)
    intent_counts: dict = Field(default_factory=dict)
    template_counts: dict = Field(default_factory=dict)
    human_count: int = 0
    rag_count: int = 0
    template_count: int = 0


class TalkScriptMatchLogItem(BaseModel):
    id: int
    trace_id: str | None = None
    customer_id: str | None = None
    session_id: str | None = None
    user_message: str
    normalized_message: str | None = None
    status: str
    scene_id: str | None = None
    candidate_question_ids: list[str] = Field(default_factory=list)
    matched_question_id: str | None = None
    template_id: str | None = None
    confidence: float | None = None
    need_slot_filling: bool = False
    need_human: bool = False
    final_answer: str | None = None
    match_reason: str | None = None
    created_at: str | None = None


class TalkScriptMatchLogListResponse(BaseModel):
    items: list[TalkScriptMatchLogItem]
    total: int
    page: int
    page_size: int
