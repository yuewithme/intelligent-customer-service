from __future__ import annotations


# These strings are application chrome accidentally echoed by the desktop
# client. They are transport noise, not examples of how a customer may speak.
_INTERNAL_OPERATOR_TITLES = {
    "销售工作台 - 销售 Agent",
    "客户记忆 - 销售 Agent",
    "产品信息 - 销售 Agent",
    "养护手册 - 销售 Agent",
    "销售活动 - 销售 Agent",
    "转人工设置 - 销售 Agent",
    "模型配置 - 销售 Agent",
}


def is_platform_noise_text(value: str) -> bool:
    """Filter only known transport/UI noise; keep emoji and short replies."""
    return str(value or "").strip() in _INTERNAL_OPERATOR_TITLES
