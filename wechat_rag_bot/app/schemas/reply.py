from pydantic import BaseModel, Field


class FinalReply(BaseModel):
    answer: str
    answer_segments: list[str] = Field(default_factory=list)
    reply_type: str
    route: str
    template_id: str | None = None
    sources: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    need_human: bool = False
    next_action: str | None = None
    metadata: dict = Field(default_factory=dict)
