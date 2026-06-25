from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    channel: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    session_id: str | None = None
    message: str
    kb_id: str = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)


class SourceItem(BaseModel):
    doc_id: str
    file_name: str
    page: int | None = None
    section: str | None = None
    score: float | None = None


class ChatData(BaseModel):
    answer: str
    session_id: str
    sources: list[SourceItem]
    usage: dict


class APIResponse(BaseModel):
    code: int
    message: str
    data: Any | None = None

