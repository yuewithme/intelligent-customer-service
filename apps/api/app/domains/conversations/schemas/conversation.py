from pydantic import BaseModel, Field


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
