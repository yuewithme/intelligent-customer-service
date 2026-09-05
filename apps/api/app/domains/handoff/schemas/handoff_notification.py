from pydantic import BaseModel, Field, field_validator


SOP_NODE_GROUPS = (
    {
        "sop_scope": "first_order",
        "name": "首单 SOP",
        "nodes": (
            ("first_order.opening", "破冰", "新客首次沟通和关系建立。"),
            ("first_order.need_discovery", "挖需求", "理解养护、商品或服务需求。"),
            ("first_order.pain_discovery", "找痛点", "识别客户最在意的问题和服务缺口。"),
            ("first_order.recommendation", "推品", "根据已知事实推荐匹配的服务或商品。"),
            ("first_order.value_building", "塑品", "说明方案价值、服务差异和匹配理由。"),
            ("first_order.trial_close", "试成交", "在价值建立后测试客户购买意愿。"),
            ("first_order.closing", "逼单 / 成交推进", "处理最后顾虑并给出明确下一步。"),
        ),
    },
    {
        "sop_scope": "service",
        "name": "服务 SOP",
        "nodes": (
            ("service.need_discovery", "问题处理与服务挖需", "先处理当前问题，再了解会影响后续服务的信息。"),
            ("service.member_benefit", "会员权益交付", "结合当前问题交付相关教程、资料或指导。"),
            ("service.post_service_close", "服务收口与偏好采集", "问题解决后完成关系承接并采集稳定偏好。"),
            ("service.repurchase_discovery", "复购需求挖掘", "客户出现真实新需求时进行复购匹配。"),
            ("service.relationship_maintenance", "长期关系维护", "围绕回访、养护内容和承诺延续服务关系。"),
        ),
    },
)
SOP_NODE_IDS = frozenset(
    node_id
    for group in SOP_NODE_GROUPS
    for node_id, _name, _description in group["nodes"]
)


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
        unknown = sorted(set(value) - SOP_NODE_IDS)
        if unknown:
            raise ValueError(f"未知 SOP 节点：{unknown}")
        return value
