from pydantic import BaseModel, Field, field_validator


class HandoffNotificationSettingsUpdateRequest(BaseModel):
    recipient_contact_ids: list[int] = Field(min_length=1, max_length=20)
    message_text: str = Field(min_length=1, max_length=2000)

    @field_validator("recipient_contact_ids")
    @classmethod
    def validate_recipient_contact_ids(cls, value: list[int]) -> list[int]:
        normalized = list(dict.fromkeys(value))
        if any(contact_id <= 0 for contact_id in normalized):
            raise ValueError("联系人 ID 必须大于 0")
        return normalized

    @field_validator("message_text")
    @classmethod
    def validate_message_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("通知信息不能为空")
        return value
