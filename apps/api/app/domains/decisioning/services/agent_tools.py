from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from app.core.config import get_settings
from app.domains.catalog.services.orchid_material_service import (
    ORCHID_MATERIAL_ASSET,
    ORCHID_MATERIAL_CARD,
    ORCHID_MATERIAL_REF,
)
from app.domains.catalog.services.product_knowledge_service import (
    get_catalog_product,
    search_catalog_products,
)
from app.domains.decisioning.schemas.agent import AgentToolResult
from app.domains.decisioning.schemas.reply import OutboundMessage
from app.domains.sales.services.care_manual_service import (
    get_care_manual,
    test_match_care_manuals,
)
from app.integrations.youzan.services.youzan_ai_tool_service import YouzanAIToolService


logger = logging.getLogger("wechat_rag_bot.sales_agent_tools")


@dataclass
class AgentExecutionContext:
    message: Any
    user_state: Any
    workspace: dict[str, Any]
    prepared: dict[str, list[OutboundMessage]] = field(default_factory=dict)
    tool_facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    handoff: dict[str, Any] | None = None


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    kind: str
    keywords: tuple[str, ...]
    instructions: str


CAPABILITIES = (
    CapabilitySpec(
        "customer.get_context",
        "tool",
        ("客户", "历史", "记忆", "偏好", "承诺", "待办", "触达", "上下文"),
        "输入 {}。返回当前客户画像、最近对话、证据化记忆和今日触达状态；不能替代动态商品或订单查询。",
    ),
    CapabilitySpec(
        "customer.tag",
        "tool",
        ("标签", "盆数", "品种", "客户等级", "L1", "L2", "L3", "L4", "L5", "L6"),
        "输入 {\"tag\":\"标签库中的准确名称\",\"evidence\":\"客户本轮原话片段\"}。当客户亲口提供盆数、主要品种或足以判断 L1-L6 的稳定证据时记录标签；标签只辅助后续判断，不是销售阶段。客户说‘几盆、不多’已经给出了可用于销售判断的小规模信息，不要为了把标签精确到某个区间重新追问；无法稳妥映射时可以不打盆数标签，但继续推进。等级准确名称是 L1 青铜期、L2 白银期、L3 黄金期、L4 铂金期、L5 宗师期、L6 王者期；常用盆数准确名称是 1-10盆、10-30盆、30-50盆、50-100盆、100-200盆、200+盆、1000+盆。L1 潜在或试错期，L2 有少量经验且常见养护问题，L3 有较多品种和稳定经验，L4 收藏与品种档案，L5 艺草研究，L6 品种缔造或高价值交易。不要凭一个品种名、一个痛点或盆数单独拔高等级；证据不足就不打。抖音已购和微信已购属于受保护标签，客户口头自述不能调用本工具写入。",
    ),
    CapabilitySpec(
        "brand.service_facts",
        "tool",
        ("萧岚苑", "服务差异", "同行", "数据证明", "兰园规模", "感染率", "复花率", "抗性", "激素苗", "补发", "寄养", "赠品", "手把手", "一对一", "单品养护", "买后服务"),
        "输入 {}。返回可对外表达的已核实品牌规模、服务效果、风险数据、统一补发寄养赠品权益、选品事实，以及包含单品教程、会员百节视频教学和一对一实时指导的已核实买后服务事实。数据可用于案例证明、同行对比、痛点放大和成交推进，但历史比例不等于对单个客户保证相同结果；会员课程与长期服务按当前客户真实权益表达。网上和花鸟市场来源中，不对版、温室或激素苗、适应性和售后不足是常见且占比较大的风险方向；可以说客户的情况‘可能是、常见于、大概率与这些方向有关’，不能只凭来源断言他手上的兰花一定如此。",
    ),
    CapabilitySpec(
        "knowledge.search",
        "tool",
        ("养护", "兰花", "病害", "烂根", "黄叶", "黑斑", "浇水", "施肥", "上盆", "开花"),
        "输入 {\"query\":\"需要核实的知识问题\"}。返回知识库答案和来源；资料不足时是 not_found，不能把答案扩写成疗效或养活承诺。",
    ),
    CapabilitySpec(
        "product.search",
        "tool",
        ("商品", "产品", "购买", "选品", "价格", "库存", "兰苗", "会员", "陪伴养兰"),
        "根据当前已有事实形成合理推荐方向后即可使用，不要求固定字段、精确盆数或完整画像。没有明确兰花或养护产品购买需求时，推品方向统一为陪伴养兰服务，使用 {\"query\":\"陪伴养兰\",\"limit\":3}；黄叶、烂根、不会养、反复失败、没人指导等养护痛点不能被解释成需要换一盆好养兰花或购买肥药植料。只有客户明确想买、添几盆、看花、选花、请你推荐品种，或主动询问准备购买的兰花花色、香味、苗数、价格、规格、库存、链接、下单时，才按偏好查询具体兰苗；客户只是提到现有品种不算购买需求。只有客户明确索要肥料、植料、杀菌杀虫用品等养护商品，或问题方向已经基本明确且客户强烈要求对应商品时，才查询具体养护产品。客户拒绝服务时不自动改推下一优先级，仍需对应的明确购买需求。这是写给 Agent 的强默认经验，不是运行时关键词硬拦截；搜索不会发送卡片。",
    ),
    CapabilitySpec(
        "product.get",
        "tool",
        ("商品详情", "规格", "权益", "product_ref"),
        "输入 {\"product_ref\":\"product:真实ID\"}。只接受真实目录引用，返回当前完整商品事实和卡片可用性。",
    ),
    CapabilitySpec(
        "product.send_card",
        "tool",
        ("商品卡片", "购买链接", "下单", "发链接", "成交"),
        "输入 {\"product_ref\":\"product:真实ID\"}。商品卡片是试成交动作，只在客户已表达想进一步了解、愿意试试、询问服务或商品内容与价格等清晰正向意向，或明确要链接、下单、已选定时使用。养护痛点、没人指导、一直自己摸索或信息已经收集充分，只说明可以继续塑品和口头推荐，不等于可以发卡。服务端重新核实商品和入口后准备卡片；返回 prepared 后仍不等于 sent。final_response 应说清匹配理由并用 prepared ref 安排位置。",
    ),
    CapabilitySpec(
        "care_manual.search",
        "tool",
        ("养护手册", "单品资料", "品种资料", "收苗", "上盆"),
        "输入 {\"query\":\"品种、商品或养护主题\",\"product_ref\":\"可选\"}。返回已发布手册 material_ref、匹配类型和可用性；不确定时不要随便发送。",
    ),
    CapabilitySpec(
        "material.search",
        "tool",
        ("资料", "教程", "指南", "手册", "学习", "视频", "福利触点"),
        "输入 {\"query\":\"客户问题或资料目的\",\"limit\":5}。返回匹配资料 material_ref、价值、适用范围和权益限制；搜索不等于发送。养兰卡片可发不代表卡内受限视频可看；若客户反馈视频无权限，先问是否在抖音购买过萧岚苑商品，确认买过后请他发送能看到店铺与订单状态的购买截图。截图经系统核验后使用 video_access.request 通过现有通知链联系同事处理，不调用 human.handoff；AI 继续回复，也不能承诺权限已经开通。",
    ),
    CapabilitySpec(
        "video_access.request",
        "tool",
        ("视频看不了", "视频权限", "开权限", "抖音已购", "订单截图"),
        "输入 {}。只在客户发送的抖音购买截图已经由系统核验、工作区已有‘抖音已购’标签时调用；通过现有转人工消息通知链联系值班同事处理视频权限，但不创建人工接管、不改变会话状态。返回 notified 后客户口径只说‘已经联系同事处理了’，不说‘提交处理、提交申请或提交核对’，也不代表权限已经开通。回复保持 need_human=false，由 AI 继续承接客户问题和销售关系。若只有客户口头说买过，先请他发能看到萧岚苑店铺与订单状态的截图，不能调用。",
    ),
    CapabilitySpec(
        "material.send",
        "tool",
        ("发资料", "资料卡片", "发送手册", "释放资料"),
        "输入 {\"material_ref\":\"material:真实引用\"}。只在能说清资料与当前客户的匹配、释放后的销售或关系目的，或客户不购买时把它作为最终福利触点时使用。客户索要资料只是兴趣信号；若基本情况和需求未清，先顺着资料内容挖需，不调用本工具。上下文显示同一资料已经发过时，不因客户继续咨询相关问题而再次调用；先直接解决新问题并判断是否推品，仅在客户明确要求重发、找不到或上次未成功时例外。服务端校验引用、发布状态、权益和近期重复后准备卡片；prepared 不等于 sent。",
    ),
    CapabilitySpec(
        "order.search",
        "tool",
        ("订单", "付款", "发货", "物流", "快递", "手机号"),
        "输入 {\"mobile\":\"客户主动提供的手机号，可选\",\"limit\":3}。只能查询当前客户绑定或其主动提供手机号的订单，返回脱敏信息和 order_ref；查到已付款的真实微信/有赞订单后，系统自动记录微信已购。",
    ),
    CapabilitySpec(
        "order.get",
        "tool",
        ("订单详情", "order_ref", "物流详情"),
        "输入 {\"order_ref\":\"order:真实订单号\",\"mobile\":\"可选\"}。只读当前客户订单；查到已付款真实订单后系统自动记录微信已购，不能修改、退款或取消。",
    ),
    CapabilitySpec(
        "memory.record",
        "tool",
        ("记住", "偏好", "事实", "承诺", "纠正", "待办"),
        "输入 {\"fact\":\"稳定事实或承诺\",\"evidence\":\"客户本轮原话中的原文证据\"}。客户回答养兰经验、环境、稳定偏好、目标或顾虑等会影响后续匹配的信息时，将其作为候选记忆提交。证据必须出现在本轮客户消息；不把模型猜测写成事实。",
    ),
    CapabilitySpec(
        "wakeup.schedule",
        "tool",
        ("跟进", "唤醒", "提醒", "之后", "晚点", "明天", "沉默"),
        "输入 {\"due_in_hours\":12,\"reason\":\"未来重新判断原因\",\"checklist\":[\"届时要核实什么\"]}。只保存重新判断任务，不保存未来话术；范围 1 到 168 小时。",
    ),
    CapabilitySpec(
        "human.handoff",
        "tool",
        ("人工", "退款", "赔偿", "投诉", "改价", "修改订单", "申请优惠", "品质", "权限"),
        "输入 {\"reason\":\"接管原因\",\"summary\":\"已核实事实和未完成事项\"}。创建人工接管；结果为 pending 时只能说已为客户提交或请同事继续核实，不能声称已经处理完成。",
    ),
    CapabilitySpec(
        "experience.need_discovery",
        "experience",
        ("需求", "挖需", "追问", "信息不足", "情况"),
        "挖需是为了收集足以支撑销售匹配的客户事实，不是走表单。只了解目标或问题、养兰经验、环境、选择偏好、预算与顾虑中真正会改变下一步的部分。普通概念或养护知识答疑不是诊断会诊；先专业回答，再优先补最接近推荐就绪的一项事实。技术细节最多追问一轮，客户表示不懂、不记得、没注意、商家给什么就用，或只给猜测答案时，把该细节视为未知但不阻塞，不再索图、让客户检查或换说法追问。提问时通常一次推进一个容易回答的信息；首次提问或目的不明时，先说回答后能得到的具体收益。不按固定字段数量判断完成；当已知事实足以说清客户需求、关键适配条件和推荐理由时，就停止无关追问，转入回答、价值塑造、服务或主动推品；问题不是每轮默认动作。",
    ),
    CapabilitySpec(
        "experience.customer_leveling",
        "experience",
        ("客户等级", "L1", "L2", "L3", "L4", "L5", "L6", "盆数", "品种", "收藏", "艺草", "稀有"),
        "L1-L6 是可修正的客户理解，不是回复路由。L1 多为尚未入门、0 盆想重新开始或少量试养且需要基础认知；L2 有少量经验，但反复遇到黄叶、黑斑、腐苗、烂根、不开花等基础问题；L3 已有较多品种和相对稳定经验，关注病虫害、环境适配和经典品种；L4 关注收藏、瓣型、老种鉴定和品种档案；L5 深入研究艺草、叶艺和进化；L6 涉及育种、命名权、稀有种源或高价值交易。等级要综合客户亲口提供的盆数、养兰时间、问题复杂度和关注点；单个关键词不能决定等级。信息不足保持未知，有新证据时可以修正。",
    ),
    CapabilitySpec(
        "experience.companion_service_fit",
        "experience",
        ("盆数", "养兰时间", "新手", "养死", "黑斑", "黄叶", "腐苗", "不开花", "没人教", "指导", "同行", "陪伴养兰"),
        "盆数是前期分层入口，痛点和失败史是陪伴服务信号，养兰时间用于校准，过去是否有人持续指导用于确认服务缺口。‘几盆、十来盆、不多、几十盆’都是已经回答规模的可用信息；除非跨规模会真实改变眼前选品，否则不再追问精确数字，也不为打标签重问。客户大致在 10 盆以内时，可结合上下文了解是否刚开始以及当前最具体的痛点；已有大致盆数和痛点通常足以开始解释问题并转向服务价值，不再横向盘问。客户 0 盆但曾经养死时，了解他是否愿意重新学习，或过去是否主要靠自己摸索；确认后可从重新选对适配的种苗和建立正确方法切入。客户只说养护痛点且上下文完全没有规模信息时，可补一个大致盆数；仍不足再分轮了解养兰时间，养兰两年内且少量养兰并反复出问题，通常更需要规范养护体系。先用通俗语言概括可能的病理、生理和环境因素，再自然了解原商家买回去后有没有持续指导；不要把问题武断归咎于客户，也不要断言一定是种苗问题。信息足够后，以三个结果检查价值是否足够：客户是否理解过去失败的关键原因或风险、萧岚苑解决方式的相关差异、买后服务怎样具体落地。客户确认没人教、一直自己摸索后，不重复上一轮病因和服务概述；承接这个新信息，说明萧岚苑与部分卖完即止商家的差异，再按真实权益选择对应品种的单品养护教程、会员百节视频教学和师傅一对一实时指导来说明服务如何落地。表达顺序、轮次和取舍由 Agent 根据上下文决定，已经讲清的内容不重复。价值讲清后可以自然邀请客户进一步了解；只有客户给出清晰正向意向后才发送商品卡片试成交，不能把服务缺口本身当成发卡意向。没有明确兰花购买需求时统一用 query‘陪伴养兰’查真实服务商品，不推荐具体兰苗；黄叶、烂根、不会养等痛点不能自动提高兰苗权重。只有明确选花、买花或询问准备购买的兰花交易信息时才转向兰苗；只是提到现有建兰等品种不算。以上是告诉 Agent 的强默认经验，不是固定话术或固定轮次。",
    ),
    CapabilitySpec(
        "experience.product_direction_weighting",
        "experience",
        ("推品", "选品权重", "养兰服务", "陪伴养兰", "购买兰花", "兰苗", "购买意向"),
        "先判断当前成交载体，而不是看到痛点就随便挑一个商品。没有客户明确表达的兰花购买需求时，推品统一落在陪伴养兰服务：先按既有服务塑造思路讲清可能原因、萧岚苑与部分只卖花商家的服务差异，再按真实权益选择单品养护教程、会员百节视频教学和师傅一对一实时指导来说明服务如何落地，然后用 product.search 的 query‘陪伴养兰’查询真实服务型商品。查询不会发卡。商品卡片属于试成交：服务缺口和信息充分只支持塑品、口头推荐与试探意向，客户明确表示想进一步了解、愿意试试、询问内容价格，或要链接下单后，才调用 product.send_card。短肯定回复要与上一条 Agent 问话配对理解：刚问完服务是否更踏实、有帮助或是否愿意了解，客户回答是的、可以、好，就表示认可服务并进入 interest，应推进真实服务卡片试成交，不重复塑品内容。养不好、黄叶、烂根、不会养、反复失败、缺少指导都不能推导出客户想买新兰花。只有客户明确想买、添几盆、看花、选花、请你推荐品种，或询问准备购买的兰花花色、香味、苗数、价格、规格、库存、链接、下单时，才把具体兰苗作为推品方向；仅提到现有品种不算。客户既想买兰花又有养护痛点时可以把兰苗和买后服务结合；客户拒绝服务时不自动改推兰苗。以上是告诉 Agent 的强默认经验，不是固定话术或固定轮次。",
    ),
    CapabilitySpec(
        "experience.relationship_before_product",
        "experience",
        ("新客户", "新好友", "聊天", "关系", "信任", "团队", "专业价值", "购买意向"),
        "新客户前段先理解来意，只收集会影响匹配的客户事实，并通过真实帮助让客户感受到萧岚苑团队专业、负责、愿意长期承接问题。回答客户的非核心知识问题并展示专业后，不围绕该问题继续做精细会诊；回到推荐就绪条件，优先了解盆数与品种、目标或痛点、经验与指导缺口、选择偏好中最有价值的一项。不因新好友或普通兴趣过早推品；但当需求、目标和关键适配条件已足以说清推荐理由时，即使客户没有主动说想买，也应自然转入真实商品查询和主动推荐。客户明确问品种、价格、规格、库存、购买方式或要链接时直接推进，不为挖需而拖延。",
    ),
    CapabilitySpec(
        "experience.material_value",
        "experience",
        ("资料", "手册", "教程", "福利", "触点"),
        "资料是销售资产，不是客户一索要就自动发送的公共附件。索要资料说明客户愿意学习，应借资料会涉及的品种、养护、痛点或目标自然收集一项关键信息。当基本情况和需求尚未形成可用判断时，本轮不发资料，也不用固定‘再问一次’次数规则；当资料已与客户问题和成交目的匹配，或客户不购买时把它作为最终福利触点，才决定释放。真正发送时说明为什么给和重点看什么；发送直播间展示的陪伴养兰资料后，依据工具事实说明收到的是图文版电子档、链接 48 小时内有效，并让客户理解资料只是陪伴指导的其中一步。资料发出后就退到背景，不围绕资料继续组织后续回复；客户提出黄叶等具体问题时，先像没有资料捷径一样直接解决，再根据已有事实判断是否推品，不能用‘资料里有’代替回答，也不重复发送同一资料。只有客户明确要求重发、表示找不到或上次未成功时才考虑重发。若信息仍不足就了解一个真正影响匹配的事实，若已足够就转入有依据的推品。",
    ),
    CapabilitySpec(
        "experience.conversation_progression",
        "experience",
        ("新手", "第一盆", "同行差异", "资料已发", "肯定回复", "好的", "谢谢", "寒暄", "重复", "推进", "下一步"),
        "把对话看成一条连续的关系进度，但上下文只帮助理解客户当前意思，不能要求客户继续上一话题。先判断当前消息是在回答上一问、补充同一件事，还是提出无关的新问题或新的关注点；只有确实承接上一问时，才识别上一轮已完成的动作、客户确认的对象和仍未完成的销售目的。客户答非所问、跳过问题或主动换话题时，以当前明确表达为最高优先级，先承接并回答当前问题，原提问和销售动作退到背景，不能强行拉回或自顾自推进。新手、零基础或正在选第一盆时，服务价值不能停在‘按地区和环境推荐适配品种’：还应让客户理解结缘后不是交付就结束，而会继续通过已核实的教程、会员教学和师傅一对一实时指导陪伴他学会养护；需要具体表达时先读取 brand.service_facts，这个‘选对＋有人教’的闭环是为推荐适配品种建立信任，不是固定铺垫。发送直播间展示的陪伴养兰资料时，依据资料工具事实说明客户收到的是图文版电子档、应在 48 小时内及时查看，并让他理解图文资料只是陪伴指导的一步；交付定位讲清后就继续选品、服务意向或成交。‘好的’‘谢谢’‘是的’等短回复只有在语义上确实回应紧邻动作或提议时，才按确认承接；若客户表达了新的实质内容，就以新内容为主。已确认的内容不再复述；若上一动作还有未讲清且仍与客户当前意思相关的价值环节则补足，已经讲清就推进下一步。这是上下文进度判断经验，不是按短语触发的固定路由。",
    ),
    CapabilitySpec(
        "experience.video_access",
        "experience",
        ("视频看不了", "视频打不开", "视频权限", "抖音购买", "订单截图"),
        "视频看不了是需要解决的权益问题，不把它猜成网络波动，也不用图文内容代替。先问客户是否在抖音购买过萧岚苑商品；确认买过后请他发送能看清萧岚苑店铺与订单状态的截图。口头自述不等于核验通过。系统识别截图并在工作区写入‘抖音已购’后，调用 video_access.request 通过现有通知链联系同事处理；工具返回 notified 后只对客户说‘已经联系同事处理了’。整个过程不调用 human.handoff、不停止 AI 回复，也不承诺权限已经开通；处理完当前问题后，继续根据已有客户事实解决养护问题或推进合适商品与服务。",
    ),
    CapabilitySpec(
        "experience.objection",
        "experience",
        ("贵", "考虑", "犹豫", "拒绝", "沉默", "顾虑", "谢谢", "先看看"),
        "先理解价格、价值、信任、适配还是时机顾虑，只回应真正阻碍。‘好的’‘谢谢’‘我先看看’必须放回紧邻上下文：它可能是在确认提议、转换话题，也可能是礼貌性软收口，不能仅凭短语决定。只有确实是软收口时才降低压力，并在关键需求仍不清楚时换一个更容易回答的角度继续了解，不用被动客服式结尾。明确拒绝时当下收住压力，后续每日触达重新选择服务、价值或成交角度。",
    ),
    CapabilitySpec(
        "experience.close",
        "experience",
        ("成交", "下单", "链接", "库存", "价格", "就要", "购买"),
        "客户问具体价格、库存、规格、链接或明确选择时，核实真实商品后直接给下一步；不要继续无关挖需，也不要把 prepared/queued 说成 sent。",
    ),
    CapabilitySpec(
        "experience.daily_touch",
        "experience",
        ("每日", "触达", "跟进", "沉默", "不回复", "已拒绝"),
        "每日发送是必须业务动作。先看今日 sent、购买状态、最近问题、顾虑和触达主题；每次选择新的关系目的，避免重复催单。已购以服务优先，明确拒绝降低压力但每天一次。",
    ),
    CapabilitySpec(
        "experience.discount",
        "experience",
        ("优惠", "便宜", "申请", "福利", "价格顾虑"),
        "有购买兴趣且价格是主要阻碍时，可自由表达价格价值并提出帮忙问问或申请看看，随后转人工。申请不代表获批，不能编造金额、名额、最低价或期限。",
    ),
    CapabilitySpec(
        "experience.sales_message_composition",
        "experience",
        ("话术", "怎么说", "表达组合", "案例", "数据", "同行对比", "具体方案", "筑梦", "下危机", "风险逆转"),
        "不要先找一句固定话术。先识别客户信号和本轮唯一主要目的，再从关系承接、简洁专业判断、相似案例、已核实数据、已核实同行差异、品牌事实、具体方案、痛点后果、筑梦画面、真实紧迫性、风险逆转、清楚行动中选择最少够用的两到四个元素现场组合。常用组合包括：案例过程+数据证明+可复制第一步；同行常见做法+具体后果+萧岚苑方案+客户利益；简洁判断+问题后果+服务缺口+陪伴方案；失败经历承接+原因重构+服务保障；地区环境+品种适配+陪伴指导；文化故事+具体场景+拥有后的体验；真实风险或窗口+延迟损失+立即方案；核心价值+明确主张+二选一或低门槛行动。一个元素只承担一个作用，已经讲清的不重复。回复保持自然简短，不把元素名称、内部策略或完整公式说给客户。案例、数据、同行差异、权益和紧迫性只能使用本轮工具或权威上下文已经核实的事实；缺少事实就换用其他元素，不自行补齐。",
    ),
    CapabilitySpec(
        "experience.service_first_routing",
        "experience",
        ("推荐优先级", "主推", "陪伴养兰", "兰花", "养护产品", "肥料", "植料", "杀菌", "杀虫"),
        "首单成交载体的默认优先级是陪伴养兰服务＞兰花＞养护产品。没有明确购买方向时，围绕客户的不会养、反复失败、无人指导或系统学习需求主推陪伴养兰服务。只有客户明确想买、添花、选品种，或询问准备购买兰花的花色、香味、规格、价格、库存、链接时，才重点转向兰花。只有客户明确索要肥料、植料、杀菌杀虫用品等养护商品，或在问题方向已基本明确后强烈要求对应商品时，才转向养护产品；普通黄叶、烂根、不开花和病虫害咨询仍先判断其系统养护与持续指导需求，不自动卖用品。客户同时想买兰花又担心养不好时，把兰花与陪伴指导组合；客户拒绝服务时不自动改推下一优先级。具体商品、权益、价格和库存仍查询真实工具。",
    ),
    CapabilitySpec(
        "experience.pain_to_service",
        "experience",
        ("找痛点", "痛点放大", "养死", "反复养不好", "黄叶", "焦尖", "烂根", "不开花", "没人指导", "系统养护"),
        "找痛点的目的，是把客户表面现象与重复试错、苗情损失或无人承接的服务缺口连接起来，并推进陪伴养兰，不是完成深度会诊。先接住客户经历，用一两句给出条件化的常见原因或风险；技术细节最多追问一轮，足以建立专业判断后就停止。随后根据客户事实选择一个后果，再了解或承接过去是否主要靠自己摸索、原商家是否持续指导。客户承认反复失败、没人教或方法不系统后，立即从病因讨论切换到价值塑造：说明单次建议只能处理眼前问题，单品教程和会员百节视频帮助建立方法，师傅一对一实时指导解决他的具体差异；具体内容按真实权益取舍。新手侧重不会判断和缺少方法；反复养不好侧重重复试错；盆数增加但管理不系统侧重预防和系统管理；高阶客户侧重种源、品种知识与专业交流。即使只是简单单点问题，也要先回答客户，再结合盆数、经历、无人指导、系统学习或后续预防自然寻找一个销售切入口；不能因为问题简单就被动结束。没有清晰正向意向时先塑造或试探，不直接发送商品卡片。",
    ),
    CapabilitySpec(
        "experience.value_proof",
        "experience",
        ("塑品", "价值塑造", "案例证明", "数据证明", "品牌实力", "为什么选你们", "同行比较", "不信任"),
        "塑品要让客户逐步得到五个答案：为什么适合我、为什么现在要解决、为什么相信萧岚苑、为什么不只看更低价格、选择后会得到什么。不要一次回答全部；只补当前缺失的一到两个答案。案例使用相似客户身份或地区+原问题+我们采取的具体方案+后续变化+可核实证据，重点展示解决过程。数据要与客户问题、案例或方案相连，不孤立堆数字。同行对比使用市场中有依据的常见做法+可能后果+萧岚苑不同做法+客户实际利益，不能把个别风险说成所有同行事实。陪伴服务优先组合客户问题+单次建议局限+单品教程或会员百节视频+结合实际情况的一对一实时指导+结果愿景，具体内容按真实权益取舍；兰花和养护产品只有在对应购买方向成立后再塑。客户已经认可价值并出现正向意向时停止继续证明，转入试成交。",
    ),
    CapabilitySpec(
        "experience.trial_and_close",
        "experience",
        ("试成交", "逼单", "促单", "成交卡点", "二选一", "筑梦", "下危机", "临门一脚"),
        "试成交是在价值已基本建立后，用低门槛行动测试购买意愿：明确更推荐的方向和一条客户相关理由，再邀请进一步了解、比较两个清楚方向、确认规格，或询问是否发送真实购买入口。还没有清晰正向意向时先试探，不直接发卡；客户认可陪伴方式、想进一步了解、询问内容价格或愿意试试后进入 interest，查询并发送真实服务卡片。逼单只处理最后一个卡点：价格用价值拆解+试错成本+合适替代或申请福利；怕养死用原因重构+真实陪伴服务+风险逆转；选择困难用核心需求+明确主张+二选一；不信任用相似案例+具体过程+已核实证据；再考虑用一个轻问题识别最关键顾虑。养不活包补发、可以由兰园寄养一段时间后再发货、固定有赠品，是适用于所有客户和所有商品的统一风险逆转权益，可以直接用于促单；没有政策细节时不自行编造补发次数和条件、寄养时长、赠品名称和数量。可以用具体生活场景筑梦，也可以用已核实风险、季节窗口、库存或活动下危机；预留、稀缺、优惠和时效必须先有真实工具结果，不能把推测写成当前事实。客户已经要价格、链接或明确选定时停止铺垫，直接查真实商品并给下一步。",
    ),
    CapabilitySpec(
        "experience.source_scenario",
        "experience",
        ("以前养死", "从来没养", "刚养", "朋友送", "网上买", "花鸟市场", "兰花来源"),
        "客户来源和经历是选择销售元素的线索，不是直接下结论的证据。从未养或以前养死：承接信心不足+原因重构+适配选择+陪伴服务，恢复尝试意愿。刚养且暂无明显问题：从客户愿意提供的图片或现状中快速找一个可提升点，给简洁判断后引出科学养护和持续指导，不做全面体检。朋友送：先尊重礼物的情感价值并帮助养好，用一个判断或相似案例建立信任，再转陪伴服务；客户出现明确添花兴趣后才推荐兰花。网上或花鸟市场来源：可以明确说明品种不对版、温室或激素苗、家庭环境适应性不足、售后无人承接是常见且占比较大的风险方向；结合客户苗情使用‘可能是、常见于、大概率与这些方向有关’，但不能仅凭来源断言客户这盆一定是假苗、激素苗或不对版。随后用当前证据说明萧岚苑在选苗适配和买后承接上的差异。涉及野生采挖的法律结论或具体处罚时先查当前权威知识，不从销售经验直接生成。",
    ),
    CapabilitySpec(
        "experience.engagement_followup",
        "experience",
        ("群发", "谢谢分享", "已经有了", "暂时不需要", "留客", "互动", "沉默用户", "内容触达"),
        "群发和跟进的目标不是单向发广告，而是用有价值内容触发回复，再根据回复进入服务、复购或成交判断。客户说谢谢分享：简短承接，再用与其相关的季节、品种或养护方向留下一个容易回应的触点。客户说已经有了：认可已有经验，围绕最近品种或状态邀请交流，获得图片后可切服务，有明确添花兴趣后再切复购。客户说太贵：接住价格，用长期价值、性价比替代或真实福利继续推进。客户说暂时不需要：当轮降低压力，用季节提醒、资料、品种故事或后续回访保留触点。后续内容在品种故事与文化、季节养护、真实案例、资料价值、问题回访和趣味互动之间轮换；每次只有一个新关系目的，不重复问‘考虑得怎么样’。",
    ),
)


async def execute_agent_tool(
    *, call_id: str, name: str, arguments: dict[str, Any], context: AgentExecutionContext
) -> AgentToolResult:
    handlers: dict[str, Callable[..., Awaitable[AgentToolResult]]] = {
        "capability.search": _capability_search,
        "customer.get_context": _customer_context,
        "customer.tag": _customer_tag,
        "brand.service_facts": _brand_service_facts,
        "knowledge.search": _knowledge_search,
        "product.search": _product_search,
        "product.get": _product_get,
        "product.send_card": _product_send_card,
        "care_manual.search": _care_manual_search,
        "material.search": _material_search,
        "material.send": _material_send,
        "video_access.request": _video_access_request,
        "order.search": _order_search,
        "order.get": _order_get,
        "memory.record": _memory_record,
        "wakeup.schedule": _wakeup_schedule,
        "human.handoff": _human_handoff,
    }
    handler = handlers.get(name)
    if handler is None:
        return _result(call_id, name, "invalid_arguments", error="unknown_tool")
    try:
        result = await handler(call_id=call_id, arguments=arguments, context=context)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sales Agent tool %s failed: %s", name, type(exc).__name__)
        result = _result(
            call_id,
            name,
            "temporarily_unavailable",
            error=type(exc).__name__,
        )
    context.tool_facts[call_id] = result.model_dump(mode="json")
    return result


async def _capability_search(*, call_id, arguments, context) -> AgentToolResult:
    del context
    query = _text(arguments.get("query"), maximum=300)
    limit = _limit(arguments.get("limit"), default=5, maximum=8)
    if not query:
        return _result(call_id, "capability.search", "invalid_arguments", error="query_required")
    ranked = sorted(
        CAPABILITIES,
        key=lambda item: (_capability_score(query, item), item.kind == "tool"),
        reverse=True,
    )
    selected = [item for item in ranked if _capability_score(query, item) > 0][:limit]
    if not selected:
        selected = [item for item in CAPABILITIES if item.name in {"customer.get_context", "knowledge.search", "human.handoff"}]
    return _result(
        call_id,
        "capability.search",
        "found",
        capabilities=[
            {"name": item.name, "kind": item.kind, "full_instructions": item.instructions}
            for item in selected
        ],
    )


async def _customer_context(*, call_id, arguments, context) -> AgentToolResult:
    del arguments
    return _result(call_id, "customer.get_context", "found", workspace=context.workspace)


async def _brand_service_facts(*, call_id, arguments, context) -> AgentToolResult:
    del arguments, context
    return _result(
        call_id,
        "brand.service_facts",
        "found",
        positioning=(
            "萧岚苑不只关注兰花卖出：选品时会结合客户的地区、"
            "经验和环境帮忙匹配，结缘后继续承接陪伴养兰手把手指导。"
        ),
        customer_entitlement=(
            "在萧岚苑结缘兰草的兰友，可获得买后陪伴养兰手把手指导承接；"
            "会员专属课程与长期服务按当前商品和客户的真实权益交付。"
        ),
        common_failure_pattern=[
            "前端没有选到适合客户地区、经验和环境的健康苗，会增加后续养护难度。",
            "买回去后缺少持续的一对一实时指导，遇到问题只能自己摸索，容易反复试错。",
        ],
        verified_evidence=[
            {"claim": "专业种植国兰 20 余年", "use_for": "从业经验与专业积累"},
            {"claim": "拥有 200 亩兰棚和 2 千多个品种", "use_for": "兰园规模、资源与选品能力"},
            {"claim": "年培育优质国兰超过 30 万株", "use_for": "培育规模与稳定供给"},
            {"claim": "已服务十几万兰友，带领 1 万多名新手学习养兰", "use_for": "服务规模与新手承接经验"},
            {"claim": "新手养兰方法经过实际验证，整体有效率达到 99%", "use_for": "体系化方法的历史效果"},
            {"claim": "长期没有进行病虫害预防的兰花，感染率超过 70%", "use_for": "病虫害预防和及时处理的必要性"},
            {"claim": "温室激素苗复花率不足 30%", "use_for": "种源和培育方式的重要性"},
            {"claim": "自然苗抗性是温室激素苗的 3 倍", "use_for": "自然苗抗性差异"},
            {"claim": "老客户反馈，按对应方案处理后的焦尖率可降低 80%", "use_for": "焦尖方案的历史客户反馈"},
            {"claim": "陪伴养兰包含百节系统视频教学", "use_for": "系统学习内容的规模"},
        ],
        universal_risk_reversal={
            "scope": "适用于所有客户和所有商品",
            "entitlements": ["养不活包补发", "可以由兰园寄养一段时间后再发货", "固定有赠品"],
            "usage": "可以直接确认三项权益存在，用于怕养不活、价格顾虑、试成交和临门促单。",
            "detail_limit": "未提供补发条件和次数、寄养具体时长、赠品名称和数量，不自行补充这些细节。",
        },
        comparison_boundary=(
            "网上和花鸟市场来源中，品种不对版、温室或激素苗、家庭环境适应性不足、"
            "售后缺少持续指导是常见且占比较大的风险方向。可以结合当前苗情说‘可能是、"
            "常见于、大概率与这些方向有关’，不能只凭购买渠道断言客户这盆一定是假苗、"
            "激素苗或不对版，也不能说所有同行都存在这些问题。"
        ),
        delivery=[
            {
                "name": "对应品种的单品养护教程",
                "details": "客户在萧岚苑结缘兰花后，可获得对应品种的单品养护教程，内容包括收货处理、上盆、浇水、施肥、防病和花期等基础养护。",
            },
            {
                "name": "会员百节视频教学",
                "details": "会员兰友可按当前权益学习系统的百节视频课程，用来建立完整的养兰基础和常见问题处理方法。",
            },
            {
                "name": "养兰师傅一对一实时指导",
                "details": "教程看完或实际养护中有不懂，可以咨询养兰师傅，由师傅结合品种、地区和实际环境进行一对一实时指导。",
            },
        ],
        claim_boundaries=[
            "不承诺一定养活、一定开花或任何疗效。",
            "效果比例是已核实的历史验证或客户反馈，可用于证明和对比，不承诺单个客户必然获得相同比例。",
            "全客户统一风险逆转权益可以直接确认；具体价格、库存、活动、额外会员权益，以及尚未提供的补发、寄养和赠品细节仍需通过对应工具或人工核实。",
        ],
    )


async def _customer_tag(*, call_id, arguments, context) -> AgentToolResult:
    tag = _text(arguments.get("tag"), maximum=100)
    evidence = _text(arguments.get("evidence"), maximum=500)
    current = str(context.message.message or "")
    if not tag or not evidence or evidence not in current:
        return _result(
            call_id,
            "customer.tag",
            "invalid_arguments",
            error="catalog_tag_and_verbatim_current_message_evidence_required",
        )
    metadata = context.message.metadata if isinstance(context.message.metadata, dict) else {}
    if metadata.get("evaluation_id") or metadata.get("is_evaluation"):
        return _result(
            call_id,
            "customer.tag",
            "recorded",
            tag=tag,
            evidence=evidence,
            persisted=False,
        )
    from app.domains.customers.services.user_profile_service import add_ai_customer_tag

    try:
        profile = await add_ai_customer_tag(
            context.message.user_id,
            tag,
            reason="agent_customer_evidence",
            trace_id=context.message.trace_id,
        )
    except ValueError:
        return _result(
            call_id,
            "customer.tag",
            "forbidden",
            reason="unknown_or_protected_customer_tag",
        )
    tags = profile.get("customer_tags") if isinstance(profile, dict) else []
    profile_view = context.workspace.setdefault("profile", {})
    if isinstance(profile_view, dict):
        profile_view["customer_tags"] = list(tags or [])
    return _result(
        call_id,
        "customer.tag",
        "recorded",
        tag=tag,
        evidence=evidence,
        customer_tags=list(tags or []),
        persisted=True,
    )


async def _knowledge_search(*, call_id, arguments, context) -> AgentToolResult:
    query = _text(arguments.get("query"), maximum=500)
    if not query:
        return _result(call_id, "knowledge.search", "invalid_arguments", error="query_required")
    from app.domains.knowledge.services.rag_service import answer_knowledge

    knowledge_message = context.message.model_copy(update={"message": query})
    result = await answer_knowledge(
        knowledge_message,
        context.user_state,
        policy_decision=None,
    )
    answer = str(result.get("answer") or "").strip()
    if not answer or answer in {"__HANDOFF__", "知识库中没有找到明确答案。"}:
        return _result(call_id, "knowledge.search", "not_found")
    sources = [source for source in result.get("sources", []) if isinstance(source, dict)]
    context.sources.extend(sources)
    return _result(call_id, "knowledge.search", "found", answer=answer, sources=sources[:5])


async def _product_search(*, call_id, arguments, context) -> AgentToolResult:
    del context
    query = _text(arguments.get("query"), maximum=100)
    limit = _limit(arguments.get("limit"), default=3, maximum=5)
    if not query:
        return _result(call_id, "product.search", "invalid_arguments", error="query_required")
    products = search_catalog_products(query, limit=limit)
    public = [_public_product(item) for item in products if isinstance(item, dict)]
    return _result(
        call_id,
        "product.search",
        "found" if public else "not_found",
        products=public,
        queried_at=_now_iso(),
    )


async def _product_get(*, call_id, arguments, context) -> AgentToolResult:
    del context
    item_id = _ref_value(arguments.get("product_ref"), "product")
    if not item_id:
        return _result(call_id, "product.get", "invalid_arguments", error="valid_product_ref_required")
    product = get_catalog_product(item_id)
    if not isinstance(product, dict):
        return _result(call_id, "product.get", "not_found")
    return _result(call_id, "product.get", "found", product=_public_product(product, detail=True), queried_at=_now_iso())


async def _product_send_card(*, call_id, arguments, context) -> AgentToolResult:
    item_id = _ref_value(arguments.get("product_ref"), "product")
    if not item_id:
        return _result(call_id, "product.send_card", "invalid_arguments", error="valid_product_ref_required")
    product = get_catalog_product(item_id)
    if not isinstance(product, dict):
        return _result(call_id, "product.send_card", "not_found")
    if _explicitly_unsellable(product):
        return _result(call_id, "product.send_card", "forbidden", reason="product_not_sellable")
    message = _product_card(product)
    if message is None:
        return _result(call_id, "product.send_card", "not_found", reason="card_entry_unavailable")
    context.prepared[call_id] = [message]
    return _result(
        call_id,
        "product.send_card",
        "prepared",
        prepared_refs=[call_id],
        product=_public_product(product),
        delivery_truth="prepared_not_sent",
    )


async def _care_manual_search(*, call_id, arguments, context) -> AgentToolResult:
    del context
    query = _text(arguments.get("query"), maximum=100)
    product_ref = _ref_value(arguments.get("product_ref"), "product")
    if not query and not product_ref:
        return _result(call_id, "care_manual.search", "invalid_arguments", error="query_or_product_ref_required")
    product_name = ""
    if product_ref:
        product = get_catalog_product(product_ref) or {}
        product_name = str(product.get("title") or "")
    matches = test_match_care_manuals(
        query=query,
        product_name=product_name,
        youzan_item_id=product_ref,
        limit=_limit(arguments.get("limit"), default=5, maximum=8),
    )
    items = [_public_manual(item) for item in matches.get("matches", []) if isinstance(item, dict)]
    return _result(
        call_id,
        "care_manual.search",
        "found" if items else "not_found",
        decision=matches.get("decision"),
        auto_send_eligible=bool(matches.get("auto_send_eligible")),
        materials=items,
    )


async def _material_search(*, call_id, arguments, context) -> AgentToolResult:
    query = _text(arguments.get("query"), maximum=200)
    if not query:
        return _result(call_id, "material.search", "invalid_arguments", error="query_required")
    limit = _limit(arguments.get("limit"), default=5, maximum=8)
    materials: list[dict[str, Any]] = [
        {
            "material_ref": ORCHID_MATERIAL_REF,
            "title": ORCHID_MATERIAL_ASSET["title"],
            "value": ORCHID_MATERIAL_ASSET["value"],
            "format": ORCHID_MATERIAL_ASSET["format"],
            "access": ORCHID_MATERIAL_ASSET["access"],
            "service_role": ORCHID_MATERIAL_ASSET["service_role"],
            "use_cases": ORCHID_MATERIAL_ASSET["use_cases"],
            "match_reason": "综合养兰资料与关系资产",
        }
    ]
    manual_result = await _care_manual_search(
        call_id=f"{call_id}:manual",
        arguments={"query": query, "limit": limit},
        context=context,
    )
    materials.extend(manual_result.data.get("materials", []))
    return _result(call_id, "material.search", "found", materials=materials[:limit])


async def _material_send(*, call_id, arguments, context) -> AgentToolResult:
    material_ref = _text(arguments.get("material_ref"), maximum=160)
    material_id = _ref_value(material_ref, "material")
    if not material_id:
        return _result(call_id, "material.send", "invalid_arguments", error="valid_material_ref_required")
    if material_id == "orchid-companion":
        card = ORCHID_MATERIAL_CARD
    elif material_id.startswith("care-manual:"):
        try:
            record = get_care_manual(int(material_id.partition(":")[2]))
        except (ValueError, LookupError):
            return _result(call_id, "material.send", "not_found")
        if not record.get("available"):
            return _result(call_id, "material.send", "forbidden", reason="material_not_published")
        card = {
            "title": record.get("title"),
            "url": record.get("note_url"),
            "description": record.get("card_description") or "对应品种的养护注意事项",
            "thumb_url": record.get("cover_url") or "",
        }
    else:
        return _result(call_id, "material.send", "not_found")
    if not str(card.get("url") or "").strip():
        return _result(call_id, "material.send", "not_found", reason="material_url_unavailable")
    if await _material_recently_sent(context, str(card.get("title") or "")):
        return _result(call_id, "material.send", "forbidden", reason="material_sent_within_30_days")
    payload = {key: card.get(key) or "" for key in ("title", "url", "description", "thumb_url")}
    context.prepared[call_id] = [
        OutboundMessage(type="link_card", content=json.dumps(payload, ensure_ascii=False))
    ]
    return _result(
        call_id,
        "material.send",
        "prepared",
        prepared_refs=[call_id],
        material_ref=material_ref,
        title=payload["title"],
        delivery_truth="prepared_not_sent",
        post_send_facts=(
            {
                "format": ORCHID_MATERIAL_ASSET["format"],
                "access": ORCHID_MATERIAL_ASSET["access"],
                "service_role": ORCHID_MATERIAL_ASSET["service_role"],
            }
            if material_id == "orchid-companion"
            else None
        ),
    )


async def _video_access_request(*, call_id, arguments, context) -> AgentToolResult:
    del arguments
    if not _has_verified_douyin_purchase(context):
        return _result(
            call_id,
            "video_access.request",
            "forbidden",
            reason="verified_douyin_purchase_required",
        )

    from app.domains.handoff.services.handoff_notification_service import (
        enqueue_handoff_notification,
    )

    metadata = context.message.metadata if isinstance(context.message.metadata, dict) else {}
    notification = await enqueue_handoff_notification(
        customer_wc_id=str(metadata.get("wc_id") or context.message.user_id),
        nickname=_text(
            metadata.get("nickname") or metadata.get("display_name"), maximum=200
        ) or None,
        wechat_id=_text(
            metadata.get("alias_name") or metadata.get("wechat_id"), maximum=200
        ) or None,
        handoff_reason="video_access_review",
        trigger_message=str(context.message.message or ""),
        source_reference=context.message.trace_id,
        notification_kind="reminder",
    )
    delivered = bool(notification.get("queued") or notification.get("feishu_sent"))
    if not delivered:
        return _result(
            call_id,
            "video_access.request",
            "temporarily_unavailable",
            reason="human_notification_unavailable",
        )
    return _result(
        call_id,
        "video_access.request",
        "notified",
        permission_status="pending_human_review",
        ai_continues=True,
        customer_reply="已经联系同事处理了",
        notification={
            "queued": int(notification.get("queued") or 0),
            "feishu_sent": bool(notification.get("feishu_sent")),
        },
    )


def _has_verified_douyin_purchase(context: AgentExecutionContext) -> bool:
    profile = context.workspace.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    tags = profile.get("customer_tags")
    tags = tags if isinstance(tags, list) else []
    if "抖音已购" in tags:
        return True
    metadata = context.message.metadata if isinstance(context.message.metadata, dict) else {}
    try:
        return int(metadata.get("verified_order_count") or 0) > 0
    except (TypeError, ValueError):
        return False


async def _order_search(*, call_id, arguments, context) -> AgentToolResult:
    mobile = _text(arguments.get("mobile"), maximum=20)
    try:
        service = YouzanAIToolService.from_settings()
    except RuntimeError:
        return _result(call_id, "order.search", "temporarily_unavailable", reason="order_integration_not_configured")
    result = await service.search_customer_orders(
        customer_id=context.message.user_id,
        mobile=mobile or None,
        limit=_limit(arguments.get("limit"), default=3, maximum=10),
        tenant_id=context.message.tenant_id,
        channel=context.message.channel,
    )
    await _record_verified_wechat_purchase(context, result)
    return _order_tool_result(call_id, "order.search", result)


async def _order_get(*, call_id, arguments, context) -> AgentToolResult:
    order_no = _ref_value(arguments.get("order_ref"), "order")
    if not order_no:
        return _result(call_id, "order.get", "invalid_arguments", error="valid_order_ref_required")
    try:
        service = YouzanAIToolService.from_settings()
    except RuntimeError:
        return _result(call_id, "order.get", "temporarily_unavailable", reason="order_integration_not_configured")
    result = await service.get_customer_order(
        customer_id=context.message.user_id,
        order_no=order_no,
        mobile=_text(arguments.get("mobile"), maximum=20) or None,
        tenant_id=context.message.tenant_id,
        channel=context.message.channel,
    )
    await _record_verified_wechat_purchase(context, result)
    return _order_tool_result(call_id, "order.get", result)


async def _record_verified_wechat_purchase(
    context: AgentExecutionContext, result: dict[str, Any]
) -> None:
    if not _has_verified_paid_order(result):
        return
    metadata = context.message.metadata if isinstance(context.message.metadata, dict) else {}
    if metadata.get("evaluation_id") or metadata.get("is_evaluation"):
        return
    from app.domains.customers.services.user_profile_service import add_verified_customer_tag

    try:
        profile = await add_verified_customer_tag(
            context.message.user_id,
            "微信已购",
            reason="youzan_order_tool_verified_paid_order",
            trace_id=context.message.trace_id,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning("Unable to persist verified WeChat purchase tag: %s", type(exc).__name__)
        return
    tags = profile.get("customer_tags") if isinstance(profile, dict) else []
    profile_view = context.workspace.setdefault("profile", {})
    if isinstance(profile_view, dict):
        profile_view["customer_tags"] = list(tags or [])


def _has_verified_paid_order(result: dict[str, Any]) -> bool:
    if not result.get("ok"):
        return False
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    if data.get("status") != "found":
        return False
    orders: list[dict[str, Any]] = []
    if isinstance(data.get("orders"), list):
        orders.extend(item for item in data["orders"] if isinstance(item, dict))
    if isinstance(data.get("order"), dict):
        orders.append(data["order"])
    paid_statuses = {
        "WAIT_SELLER_SEND_GOODS",
        "WAIT_BUYER_CONFIRM_GOODS",
        "TRADE_BUYER_SIGNED",
        "TRADE_SUCCESS",
    }
    return any(str(order.get("status") or "").upper() in paid_statuses for order in orders)


async def _memory_record(*, call_id, arguments, context) -> AgentToolResult:
    fact = _text(arguments.get("fact"), maximum=500)
    evidence = _text(arguments.get("evidence"), maximum=500)
    current = str(context.message.message or "")
    if not fact or not evidence or evidence not in current:
        return _result(call_id, "memory.record", "invalid_arguments", error="verbatim_current_message_evidence_required")
    if not get_settings().memory_v2_write_enabled:
        return _result(call_id, "memory.record", "temporarily_unavailable", reason="memory_write_disabled")
    from app.domains.customers.services.memory_dual_write_service import dual_write_conversation_event

    event_id = dual_write_conversation_event(
        tenant_id=context.message.tenant_id,
        channel=context.message.channel,
        external_user_id=context.message.user_id,
        owner_external_id=_owner_external_id(context.message.metadata),
        session_id=context.message.session_id,
        role="customer",
        content=f"{evidence}\n候选事实：{fact}",
        source_id=f"agent-memory:{context.message.trace_id}:{call_id}",
        trace_id=context.message.trace_id,
        occurred_at=datetime.now(timezone.utc),
    )
    return _result(call_id, "memory.record", "recorded", event_id=event_id, candidate_only=True)


async def _wakeup_schedule(*, call_id, arguments, context) -> AgentToolResult:
    try:
        due_in_hours = float(arguments.get("due_in_hours"))
    except (TypeError, ValueError):
        return _result(call_id, "wakeup.schedule", "invalid_arguments", error="due_in_hours_required")
    reason = _text(arguments.get("reason"), maximum=500)
    checklist = [
        _text(item, maximum=200)
        for item in arguments.get("checklist", [])
        if _text(item, maximum=200)
    ] if isinstance(arguments.get("checklist"), list) else []
    if not 1 <= due_in_hours <= 168 or not reason:
        return _result(call_id, "wakeup.schedule", "invalid_arguments", error="invalid_schedule")
    from app.domains.sales.services.daily_touch_service import schedule_agent_wakeup

    wakeup = schedule_agent_wakeup(
        customer_id=context.message.user_id,
        tenant_id=context.message.tenant_id,
        due_in_hours=due_in_hours,
        reason=reason,
        checklist=checklist,
        source_trace_id=context.message.trace_id,
    )
    return _result(call_id, "wakeup.schedule", "scheduled", wakeup=wakeup)


async def _human_handoff(*, call_id, arguments, context) -> AgentToolResult:
    reason = _text(arguments.get("reason"), maximum=200)
    summary = _text(arguments.get("summary"), maximum=1000)
    if not reason or not summary:
        return _result(call_id, "human.handoff", "invalid_arguments", error="reason_and_summary_required")
    from app.core.ids import generate_id
    ticket_id = generate_id("handoff")
    context.handoff = {
        "ticket_id": ticket_id,
        "status": "pending",
        "reason": reason,
        "summary": summary,
    }
    return _result(call_id, "human.handoff", "pending", handoff=context.handoff)


def _result(call_id: str, tool: str, status: str, *, prepared_refs=None, **data) -> AgentToolResult:
    return AgentToolResult(
        call_id=call_id,
        tool=tool,
        status=status,
        data=data,
        prepared_refs=list(prepared_refs or []),
    )


def _public_product(product: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    item_id = str(product.get("item_id") or "").strip()
    knowledge = product.get("knowledge") if isinstance(product.get("knowledge"), dict) else {}
    result = {
        "product_ref": f"product:{item_id}" if item_id else "",
        "name": knowledge.get("product_name") or product.get("title"),
        "price_cent": product.get("price_cent"),
        "stock": product.get("stock"),
        "status": product.get("status"),
        "image_url": product.get("image_url"),
        "card_available": bool(product.get("page_path") or product.get("h5_url")),
        "queried_at": _now_iso(),
    }
    for key in (
        "category",
        "flower_color",
        "fragrance",
        "flowering_status",
        "care_scenes",
        "bloom_period",
        "audience_tag",
        "highlighted_features",
        "sales_copy",
    ):
        value = knowledge.get(key)
        if value not in (None, "", []):
            result[key] = value
    if detail:
        result["page_path"] = product.get("page_path")
        result["h5_url"] = product.get("h5_url")
        result["skus"] = product.get("skus") if isinstance(product.get("skus"), list) else []
    return result


def _public_manual(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_ref": f"material:care-manual:{item.get('card_id')}",
        "title": item.get("title"),
        "orchid_name": item.get("orchid_name"),
        "match_type": item.get("match_type"),
        "selected": bool(item.get("selected")),
        "access": "published" if item.get("note_url") else "unavailable",
        "match_reason": "具体品种或商品养护手册",
    }


def _product_card(product: dict[str, Any]) -> OutboundMessage | None:
    settings = get_settings()
    title = str(product.get("title") or "商品详情").strip()
    page_path = str(product.get("page_path") or "").strip()
    if settings.youzan_mini_program_app_id and page_path:
        payload = {
            "display_name": settings.youzan_mini_program_display_name,
            "app_id": settings.youzan_mini_program_app_id,
            "user_name": settings.youzan_mini_program_user_name,
            "icon_url": settings.youzan_mini_program_icon_url,
            "page_path": page_path,
            "thumb_url": product.get("image_url") or "",
            "title": title,
        }
        return OutboundMessage(type="mini_program", content=json.dumps(payload, ensure_ascii=False))
    h5_url = str(product.get("h5_url") or "").strip()
    if not h5_url:
        return None
    price = product.get("price_cent")
    description = f"当前售价{int(price) / 100:g}元，点击查看详情和下单" if isinstance(price, int) else "点击查看商品详情和下单"
    return OutboundMessage(
        type="link_card",
        content=json.dumps(
            {
                "title": title,
                "url": h5_url,
                "description": description,
                "thumb_url": product.get("image_url") or "",
            },
            ensure_ascii=False,
        ),
    )


def _order_tool_result(call_id: str, tool: str, result: dict[str, Any]) -> AgentToolResult:
    if not result.get("ok"):
        code = str((result.get("error") or {}).get("code") or "temporarily_unavailable")
        status = "invalid_arguments" if code == "invalid_arguments" else "temporarily_unavailable"
        return _result(call_id, tool, status, error=code)
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    public = _add_order_refs(data)
    status = str(public.get("status") or "")
    if status == "not_found":
        return _result(call_id, tool, "not_found", **public)
    return _result(call_id, tool, "found", **public, queried_at=_now_iso())


def _add_order_refs(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _add_order_refs(item) for key, item in value.items()}
        order_no = str(value.get("order_no") or value.get("tid") or "").strip()
        if order_no:
            result["order_ref"] = f"order:{order_no}"
        return result
    if isinstance(value, list):
        return [_add_order_refs(item) for item in value]
    return value


async def _material_recently_sent(context: AgentExecutionContext, title: str) -> bool:
    if not title:
        return False
    from app.domains.conversations.services.conversation_service import was_outbound_content_sent

    return was_outbound_content_sent(
        channel=context.message.channel,
        user_id=context.message.user_id,
        content_marker=title,
        within_days=30,
    )


def _explicitly_unsellable(product: dict[str, Any]) -> bool:
    status = str(product.get("status") or "").strip().lower()
    if status in {"offline", "deleted", "sold_out", "unavailable", "forbidden"}:
        return True
    stock = product.get("stock")
    return isinstance(stock, int) and stock <= 0


def _capability_score(query: str, item: CapabilitySpec) -> int:
    normalized = query.casefold()
    return sum(3 if keyword.casefold() in normalized else 0 for keyword in item.keywords) + (
        1 if any(token and token in item.instructions.casefold() for token in re.split(r"[\s，。；、]+", normalized)) else 0
    )


def _ref_value(value: Any, namespace: str) -> str:
    raw = _text(value, maximum=256)
    prefix = f"{namespace}:"
    return raw[len(prefix) :].strip() if raw.startswith(prefix) else ""


def _text(value: Any, *, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _owner_external_id(metadata: dict[str, Any]) -> str:
    for key in ("w_id", "owner_external_id", "wechat_to_user"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
