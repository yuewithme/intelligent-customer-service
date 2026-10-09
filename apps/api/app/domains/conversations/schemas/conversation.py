from typing import Literal

from pydantic import BaseModel, Field


class ConversationListActionRequest(BaseModel):
    action: Literal["hide", "pin", "unpin", "unread", "mute", "unmute"]


class ClaimRequest(BaseModel):
    operator_id: str


class ReplyDraftRequest(BaseModel):
    operator_id: str
    editor_id: str = Field(min_length=1, max_length=64)
    active: bool
    remaining_seconds: float = Field(default=60, gt=0, le=60)


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
