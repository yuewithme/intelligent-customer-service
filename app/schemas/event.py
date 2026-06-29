from pydantic import BaseModel, Field


class NormalizedMessage(BaseModel):
    trace_id: str
    channel: str
    user_id: str
    session_id: str
    message_id: str | None = None
    message: str
    kb_id: str
    tenant_id: str = "tenant_default"
    permission: str = "public"
    metadata: dict = Field(default_factory=dict)
