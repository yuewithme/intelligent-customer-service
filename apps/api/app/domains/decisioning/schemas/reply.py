from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutboundMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["text", "image", "video", "link_card", "mini_program", "material"]
    content: str
    material_id: int | None = None


class FinalReply(BaseModel):
    answer: str
    answer_segments: list[str] = Field(default_factory=list)
    outbound_messages: list[OutboundMessage] = Field(default_factory=list)
    reply_type: str
    route: str
    template_id: str | None = None
    sources: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    need_human: bool = False
    next_action: str | None = None
    metadata: dict = Field(default_factory=dict)
