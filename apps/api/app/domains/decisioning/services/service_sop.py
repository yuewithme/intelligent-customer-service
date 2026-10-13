SERVICE_OPENING = (
    "您好，我是萧岚苑的小兰，后面陪您一起把兰花养好～\n\n"
    "方便把直播间或线上购买兰花的订单截图发我吗？能看清店铺、品种和订单状态就行，"
    "姓名、电话和地址可以遮住。我给您找对应品种的养护教程，后面照着您的实际情况指导。"
)

SERVICE_INTRODUCTION = (
    "您已经是我们的会员啦，后面我们会手把手陪您学会养兰。"
    "有些商家卖完苗，后续就要兰友自己摸索；我们会结合您当地的气候和家里的环境，"
    "帮您把品种养护、浇水和日常管理一步步弄明白。"
    "配套的养护资料和视频课程也可以结合实际问题学习，拿不准时随时拍图来，我们一对一帮您看。"
)
SERVICE_ENVIRONMENT_REQUEST = (
    "为了给您更贴合的养护建议，您在什么省份呀？也方便拍一张家里准备摆放兰花的位置给我，"
    "我看看光照和通风，再按您的环境给建议。"
)
MEMBER_DAY_INTRODUCTION = (
    "直播间平时主要展示几个品种，微信这边除了持续帮您看养护，"
    "每周四还有会员活动，会挑选受欢迎的名品和会员放漏福利。"
    "当期品种和优惠以活动页面为准，有空可以进小程序逛逛、看看花，买不买都没关系～"
)

MEMBER_DAY_MINI_PROGRAM_CARD = {
    "display_name": "萧岚苑",
    "app_id": "wxdb64bd6b936d0fe9",
    "user_name": "gh_cd267efb4a39@app",
    "icon_url": "https://mmbiz.qpic.cn/sz_mmbiz_png/q0LQTBxIkNbFkS6HSSLEI4VJKFic7Lnez2wrxHv5ibOFwZzNtHo2XK5oQwPQPZUQ5o1pHd5X0BwQiamia2QsOs0qcQ/640?wx_fmt=png&wxfrom=200",
    "page_path": "pages/common/blank-page/index.html?weappSharePath=%2Fpages%2Fhome%2Ffeature%2Findex%3Ffrom_params%3Dwecom_uuid~1y4jvj349dg8c!helper_kdt_id~150122956!staff_id~23075918!from_source~wecom_helper!wa_alias~2ocrzx21d6xhz%26title%3D%E5%91%A8%E5%9B%9B%E4%BC%9A%E5%91%98%E6%97%A5%26from_uuid%3DqNgYfwKn5PNfprk1791787364489%26shopAutoEnter%3D1%26alias%3Dbv5eXgQNfZ%26kdt_id%3D180281510%26fromBiz%3Dwa%26isFromBlankPage%3D1%26share_cmpt%3Dnative_wechat",
    "title": "萧岚苑",
    "thumb_url": "https://sales-agent.hzwohu.com/service-member-day.jpg"
}

SERVICE_COLLECTION_GOAL = "进入服务后主动逐项了解基础信息，依据客户原话补齐已有标签槽位，为养护指导和后续触达积累事实。"
SERVICE_COLLECTION_DIRECTIONS = [
    "先读已有标签、客户记忆和近期对话；标签缺失不等于事实未知，已经回答或明确跳过的信息不重复问。",
    "客户准备收尾时优先进入周四会员日介绍，不为补齐缺项延长对话；其余没有待处理问题的交流中主动了解一项尚未知的信息，不等客户先提问；有问题先有效承接，再自然采集。每轮最多问一项，不连续盘问。",
    "优先了解养兰年限与经验、地区与养兰环境、盆数、现有品种和养护痛点；仅在客户从未表达过任何喜好时，自然了解一项品类、花型或收藏喜好；任意喜好已表达后永久停止例行挖需。价格范围只在选品或预算话题中询问。",
    "依据 service_collection.tag_slots 中现行标签准确打标：本轮每项明确且可映射的事实分别调用 customer.tag 并引用原话；单次最多四个工具调用，更多事实分次记录。年限、现有品种、痛点及任意喜好用 service.record 保存到用户档案；能映射的标签仍调用 customer.tag。",
    "现有品种不等于喜欢的品类；不懂或没说不等于不限；L1-L2 对应新手、L3-L4 对应进阶、L5-L6 对应收藏级，依据多项稳定事实判断，不问客户 L1-L6，不新增等级标签；购买及服务状态由可信业务或运营维护。",
    "客户拒绝、忙碌或不清楚时暂停或跳过；信息足以支撑当前服务即可自然收尾并介绍周四会员日，缺项在自然对话中逐步补齐，不把标签填满作为服务门槛。",
    "四项档案维度为养兰年限、养护环境、现有品种、养护痛点；逐步补齐，不逼问，不把未知当成没有。",
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


def build_service_context(profile: dict) -> dict:
    from app.domains.sales.services.sop_policy_service import preference_tag_groups

    facts = profile.get("service_facts") or {}
    tags = profile.get("customer_tags") or []
    any_preference = any(preference_tag_groups(tags)) or bool(facts.get("preference") or profile.get("preference_summary"))
    return {
        "facts": facts,
        "preference_received": bool(any_preference),
        "collect_preferences": not any_preference,
        "experience_groups": {"新手": ["L1", "L2"], "进阶": ["L3", "L4"], "收藏级": ["L5", "L6"]},
        "verified_purchase": bool({str(tag).removeprefix("customer_tag:") for tag in tags} & {"抖音已购", "微信已购"}),
    }
