from __future__ import annotations

from typing import Any


# This module is an asset registry, not a conversation route. The Agent decides
# whether and when the material is useful; the tool layer only exposes verified
# facts and prepares the real card.
ORCHID_MATERIAL_REF = "material:orchid-companion"
ORCHID_MATERIAL_CARD: dict[str, str] = {
    "note_id": "24482256",
    "note_alias": "0Ja8r3cajo",
    "title": "萧岚苑陪伴养兰资料",
    "url": "https://h5.youzan.com/wscshop/shopnote/detail?noteAlias=0Ja8r3cajo",
    "description": (
        "《萧岚苑陪伴养兰资料》系统性收录我家兰圃秘传技艺和养兰心得，"
        "让你5分钟轻松学会养兰基础，新手上道更轻松。"
    ),
    "thumb_url": (
        "http://150.158.52.233/static/orchid-material/"
        "companion-material-card-thumb.jpg"
    ),
}
ORCHID_MATERIAL_ASSET: dict[str, Any] = {
    "material_ref": ORCHID_MATERIAL_REF,
    "title": ORCHID_MATERIAL_CARD["title"],
    "value": "系统理解养兰基础、常见问题和萧岚苑陪伴服务方式",
    "format": "直播间展示的是图文版养兰资料，私域通过电子档链接卡片发放。",
    "access": "电子档链接发放后 48 小时内有效，应提醒客户及时查看；卡内受限视频需在核实购买权益后处理。",
    "service_role": "图文电子档只是陪伴养兰指导的其中一步，不代表全部服务。",
    "use_cases": ["养兰入门", "常见养护问题", "成交后的陪伴服务说明"],
    "card": ORCHID_MATERIAL_CARD,
}
