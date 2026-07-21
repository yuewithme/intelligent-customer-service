from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.reply import OutboundMessage


class PersonaContext(BaseModel):
    persona_id: str
    persona_version: str
    soul: str
    style: str
    policy: str
    mode: str
    mode_instructions: list[str] = Field(default_factory=list)
    relationship_state: dict = Field(default_factory=dict)
    relevant_memories: list[dict] = Field(default_factory=list)
    examples: list[dict] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)


class ReplySpec(BaseModel):
    route: str
    reply_type: str
    reply_goal: str
    render_mode: Literal["persona", "locked", "silent"] = "persona"
    suggested_copy: str = ""
    must_include: list[str] = Field(default_factory=list)
    optional_points: list[str] = Field(default_factory=list)
    verified_facts: dict = Field(default_factory=dict)
    question_slot: str | None = None
    prohibited_claims: list[str] = Field(default_factory=list)
    template_id: str | None = None
    sources: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)
    answer_segments: list[str] = Field(default_factory=list)
    outbound_messages: list[OutboundMessage] = Field(default_factory=list)
    need_human: bool = False
    next_action: str | None = None
    metadata: dict = Field(default_factory=dict)
