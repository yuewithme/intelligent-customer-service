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
    model_calls: list[dict] = Field(default_factory=list)
    intent_shadow_runs: list[dict] = Field(default_factory=list)


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
    stage_avg_ms: dict = Field(default_factory=dict)
    model_call_stats: list[dict] = Field(default_factory=list)


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


class TalkScriptMatchStats(BaseModel):
    total: int
    matched_count: int = 0
    handoff_count: int = 0
    pass_through_count: int = 0
    human_count: int = 0
    avg_confidence: float | None = None
    status_counts: dict = Field(default_factory=dict)
    reason_counts: dict = Field(default_factory=dict)
    scene_counts: dict = Field(default_factory=dict)
    template_counts: dict = Field(default_factory=dict)
    low_confidence_items: list[TalkScriptMatchLogItem] = Field(default_factory=list)


class RagDebugSearchRequest(BaseModel):
    message: str = Field(min_length=1)
    kb_id: str = Field(min_length=1)
    knowledge_base_ids: list[str] = Field(default_factory=list)
    tenant_id: str = "tenant_default"
    permission: str = "public"
    top_k: int | None = Field(default=None, ge=1, le=100)
    top_n: int | None = Field(default=None, ge=1, le=50)
    include_prompt: bool = False
    max_prompt_chars: int = Field(default=12000, ge=0, le=50000)


class RagDebugDoc(BaseModel):
    kb_id: str | None = None
    doc_id: str | None = None
    chunk_id: str | None = None
    file_name: str | None = None
    page: int | None = None
    section: str | None = None
    score: float | None = None
    rerank_score: float | None = None
    rerank_reason: dict = Field(default_factory=dict)
    text_preview: str = ""


class RagDebugSearchResponse(BaseModel):
    message: str
    search_kb_ids: list[str]
    tenant_id: str
    permission: str
    top_k: int
    top_n: int
    candidate_count: int
    candidates: list[RagDebugDoc] = Field(default_factory=list)
    reranked_docs: list[RagDebugDoc] = Field(default_factory=list)
    prompt_preview: str | None = None
    prompt_truncated: bool = False
