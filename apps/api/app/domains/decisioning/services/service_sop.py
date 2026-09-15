SERVICE_OPENING = (
    "老朋友，欢迎加到我私人微信～\n\n"
    "以后家里兰花不管黄叶、烂根、不开花，随时拍图联络我，随问随答，一对一帮你搞定！\n\n"
    "我每天都会发点干货：兰花名品小故事、实用养护小妙招、辨兰小知识，不会刷屏打扰你。\n"
    "平时咱们多聊聊你手里兰草的养护情况，有老客专属铭品、成套组合福利，我第一时间通知你～"
)


SERVICE_COLLECTION_GOAL = "进入服务后主动逐项了解基础信息，依据客户原话补齐已有标签槽位，为养护指导和后续触达积累事实。"
SERVICE_COLLECTION_DIRECTIONS = [
    "先读已有标签、客户记忆和近期对话；标签缺失不等于事实未知，已经回答或明确跳过的信息不重复问。",
    "没有待处理问题时主动了解一项尚未知的信息，不等客户先提问；有问题先有效承接，再自然采集。每轮最多问一项，不连续盘问。",
    "优先了解养兰年限与经验、地区与养兰环境、盆数、现有品种和养护痛点；随后自然了解喜欢的品类、花色瓣型香味等产品需求。价格范围只在选品或预算话题中询问。",
    "依据 service_collection.tag_slots 中现行标签准确打标：本轮每项明确且可映射的事实分别调用 customer.tag 并引用原话；单次最多四个工具调用，更多事实分次记录。无法准确映射的年限、现有品种和痛点等用 memory.record 保存。",
    "现有品种不等于喜欢的品类；不懂或没说不等于不限；等级依据多项稳定事实判断，不问客户 L1-L6，也不把新手或收藏级自造为标签；购买及服务状态由可信业务或运营维护。",
    "客户拒绝、忙碌或不清楚时暂停或跳过；信息足以支撑当前服务即可进入权益交付或后续服务，缺项在自然对话中逐步补齐，不把标签填满作为服务门槛。",
    "采集与每日触达独立；不因信息缺失停发定时内容，不在对话中执行或补发定时触达，偏好采集不等于购买意向。",
]


def build_service_collection_context(profile: dict) -> dict:
    from app.domains.sales.services.tag_catalog import get_profile_tag_categories

    known = {str(tag).removeprefix("customer_tag:") for tag in profile.get("customer_tags", []) or []}
    slots = []
    for category_id, category in get_profile_tag_categories().items():
        if not category.ai_assignable or not category.values:
            continue
        allowed = [value.name for value in category.values]
        slots.append({
            "category_id": category_id,
            "name": category.name,
            "known_tags": [value for value in allowed if value in known],
            "allowed_tags": allowed,
        })
    return {
        "tag_slots": slots,
        "untagged_categories": [slot["category_id"] for slot in slots if not slot["known_tags"]],
        "directions": SERVICE_COLLECTION_DIRECTIONS,
    }
