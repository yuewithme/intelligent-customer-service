from typing import Literal

from pydantic import BaseModel, Field, model_validator


MaterialType = Literal["image", "video"]


class MaterialUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    status: Literal["ready", "expired", "disabled"] = "ready"


class MaterialFromMessageRequest(BaseModel):
    conversation_message_id: int = Field(gt=0)
    name: str | None = Field(default=None, max_length=256)


class BulkRecipientRequest(BaseModel):
    w_id: str = Field(min_length=1, max_length=256)
    wc_id: str = Field(min_length=1, max_length=256)


class BulkItemRequest(BaseModel):
    type: Literal["text", "material"]
    content: str | None = Field(default=None, max_length=20000)
    material_id: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_item(self):
        if self.type == "text" and not (self.content or "").strip():
            raise ValueError("文本消息不能为空")
        if self.type == "material" and self.material_id is None:
            raise ValueError("素材消息必须提供 material_id")
        return self


class BulkSendRequest(BaseModel):
    recipients: list[BulkRecipientRequest] = Field(min_length=1, max_length=5000)
    items: list[BulkItemRequest] = Field(min_length=1, max_length=20)
    source_type: str = Field(default="manual", min_length=1, max_length=64)
    source_id: str | None = Field(default=None, max_length=256)
    operator_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_single_account(self):
        if len({recipient.w_id for recipient in self.recipients}) != 1:
            raise ValueError("一个批量任务只能对应一个 wId")
        return self
