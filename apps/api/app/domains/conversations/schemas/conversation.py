from pydantic import BaseModel, Field


class ConversationItem(BaseModel):
    conversation_id: str
    channel: str
    user_id: str
    user_display_name: str | None = None
    user_avatar_url: str | None = None
    session_id: str | None = None
    tenant_id: str
    status: str
    owner_id: str | None = None
    last_message: str | None = None
    last_route: str | None = None
    last_intent: str | None = None
    handoff_reason: str | None = None
    handoff_ticket_id: str | None = None
    unread_count: int = 0
    created_at: str
    updated_at: str


class ConversationMessage(BaseModel):
    id: int
    conversation_id: str
    trace_id: str | None = None
    message_id: str | None = None
    sender_type: str
    sender_id: str | None = None
    content: str
    route: str | None = None
    primary_intent: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str


class ConversationListResponse(BaseModel):
    items: list[ConversationItem]
    total: int
    page: int
    page_size: int


class ConversationDetail(BaseModel):
    conversation: ConversationItem
    messages: list[ConversationMessage]


class ClaimRequest(BaseModel):
    operator_id: str


class ReplyRequest(BaseModel):
    operator_id: str
    content: str


class ReplyEmojiRequest(BaseModel):
    operator_id: str
    source_message_id: int = Field(gt=0)


class ReplyCareManualRequest(BaseModel):
    operator_id: str
    care_manual_id: int = Field(gt=0)


class StatusActionRequest(BaseModel):
    operator_id: str
    reason: str | None = None
