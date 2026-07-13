from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class ActivityFromMessagesRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    message_ids: list[int] = Field(min_length=1)
    title: str = Field(min_length=1, max_length=256)
    summary: str | None = None
    operator_id: str = Field(min_length=1, max_length=128)
    valid_from: datetime | None = None
    valid_until: datetime | None = None

    @model_validator(mode="after")
    def validate_time_range(self):
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("活动结束时间必须晚于开始时间")
        return self


class ActivityUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    summary: str | None = None
    operator_id: str = Field(min_length=1, max_length=128)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    ai_rules: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_time_range(self):
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until <= self.valid_from
        ):
            raise ValueError("活动结束时间必须晚于开始时间")
        return self


class ActivityActionRequest(BaseModel):
    operator_id: str = Field(min_length=1, max_length=128)


class ActivitySwitchesRequest(ActivityActionRequest):
    enabled: bool | None = None
    ai_enabled: bool | None = None

    @model_validator(mode="after")
    def require_switch(self):
        if self.enabled is None and self.ai_enabled is None:
            raise ValueError("至少需要更新一个开关")
        return self


class ActivitySendRequest(ActivityActionRequest):
    conversation_id: str = Field(min_length=1)
