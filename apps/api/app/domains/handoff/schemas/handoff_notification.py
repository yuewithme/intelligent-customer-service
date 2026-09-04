from pydantic import BaseModel, Field, field_validator


class HandoffNotificationSettingsUpdateRequest(BaseModel):
    global_handoff_enabled: bool = False
    recipient_contact_ids: list[int] = Field(min_length=1, max_length=20)
    message_text: str = Field(min_length=1, max_length=2000)
    sop_node_handoff: dict[str, bool] | None = None

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

    @field_validator("sop_node_handoff")
    @classmethod
    def validate_sop_node_handoff(
        cls, value: dict[str, bool] | None
    ) -> dict[str, bool] | None:
        if value is None:
            return None
        from app.domains.handoff.services.handoff_notification_service import (
            SOP_NODE_IDS,
        )

        unknown = sorted(set(value) - SOP_NODE_IDS)
        if unknown:
            raise ValueError(f"未知 SOP 节点：{unknown}")
        return value
