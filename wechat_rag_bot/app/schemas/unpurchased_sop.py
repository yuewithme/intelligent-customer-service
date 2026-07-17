from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MessageType = Literal["text", "image", "video", "material"]


def _validate_hhmm(value: str) -> str:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("时间格式必须为 HH:MM")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("时间格式必须为 HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("时间格式必须为 HH:MM")
    return f"{hour:02d}:{minute:02d}"


class UnpurchasedSopUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    enabled: bool
    dry_run: bool = True
    send_window_start: str = "09:00"
    send_window_end: str = "20:00"
    contact_poll_interval_minutes: int = Field(default=120, ge=5, le=1440)
    contact_missing_threshold: int = Field(default=3, ge=1, le=10)

    @field_validator("send_window_start", "send_window_end")
    @classmethod
    def validate_time(cls, value: str) -> str:
        return _validate_hhmm(value)

    @model_validator(mode="after")
    def validate_window(self):
        if self.send_window_end <= self.send_window_start:
            raise ValueError("发送结束时间必须晚于开始时间")
        return self


class UnpurchasedSopMessageRequest(BaseModel):
    message_type: MessageType
    content: str = Field(min_length=1, max_length=20000)
    preview_url: str | None = Field(default=None, max_length=4000)
    material_id: int | None = Field(default=None, gt=0)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息内容不能为空")
        return value

    @model_validator(mode="after")
    def validate_media(self):
        if self.message_type in {"image", "video"} and not self.content.startswith(
            ("http://", "https://")
        ):
            raise ValueError("图片和视频必须使用公网 HTTP(S) 地址")
        if self.message_type == "video" and not (self.preview_url or "").strip():
            raise ValueError("视频消息必须提供封面图")
        if self.message_type == "video" and not str(self.preview_url).startswith(
            ("http://", "https://")
        ):
            raise ValueError("视频封面必须使用公网 HTTP(S) 地址")
        if self.message_type == "material" and self.material_id is None:
            raise ValueError("批量媒体消息必须选择微信素材")
        return self


class UnpurchasedSopStepRequest(BaseModel):
    day_offset: int = Field(ge=0, le=3650)
    send_time: str | None = None
    send_time_start: str | None = None
    send_time_end: str | None = None
    messages: list[UnpurchasedSopMessageRequest] = Field(
        default_factory=list, max_length=20
    )
    message_type: MessageType | None = None
    content: str | None = Field(default=None, max_length=20000)
    preview_url: str | None = Field(default=None, max_length=4000)
    position: int = Field(default=0, ge=0)
    enabled: bool = True

    @field_validator("send_time", "send_time_start", "send_time_end")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        return _validate_hhmm(value) if value is not None else None

    @model_validator(mode="after")
    def normalize_messages(self):
        start = self.send_time_start or self.send_time
        end = self.send_time_end or self.send_time or start
        if start is None or end is None:
            raise ValueError("节点必须设置发送时间范围")
        if end < start:
            raise ValueError("发送时间范围的结束时间不能早于开始时间")
        self.send_time_start = start
        self.send_time_end = end
        self.send_time = start
        if not self.messages:
            if self.message_type is None or not (self.content or "").strip():
                raise ValueError("节点至少需要一条消息")
            self.messages = [
                UnpurchasedSopMessageRequest(
                    message_type=self.message_type,
                    content=str(self.content),
                    preview_url=self.preview_url,
                )
            ]
        first = self.messages[0]
        self.message_type = first.message_type
        self.content = first.content
        self.preview_url = first.preview_url
        return self


class UnpurchasedSopTestSendRequest(BaseModel):
    step_id: int = Field(gt=0)
    contact_id: int | None = Field(default=None, gt=0)
    contact_ids: list[int] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def normalize_contacts(self):
        values = ([self.contact_id] if self.contact_id is not None else []) + self.contact_ids
        self.contact_ids = list(dict.fromkeys(values))
        if not self.contact_ids:
            raise ValueError("至少选择一个联系人")
        if len(self.contact_ids) > 50:
            raise ValueError("单次最多选择 50 位联系人")
        if any(value <= 0 for value in self.contact_ids):
            raise ValueError("联系人 ID 必须大于 0")
        return self
