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
        "knowledge.search",
        "tool",
        ("养护", "兰花", "病害", "烂根", "黄叶", "黑斑", "浇水", "施肥", "上盆", "开花"),
        "输入 {\"query\":\"需要核实的知识问题\"}。返回知识库答案和来源；资料不足时是 not_found，不能把答案扩写成疗效或养活承诺。",
    ),
    CapabilitySpec(
        "product.search",
        "tool",
        ("商品", "产品", "购买", "选品", "价格", "库存", "兰苗", "会员", "陪伴养兰"),
        "客户已表达选购或购买意向时可直接使用；客户未主动要商品时，只要已知需求、目标和关键适配条件足以说清推荐理由，也可主动查询。如果一个未知仍会明显改变选品，先每轮追问一项；新好友身份、普通兴趣和空泛养兰话题本身不足以调用。输入 {\"query\":\"商品名或已明确的匹配需求\",\"limit\":3}；搜索不会发送卡片。",
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
        "输入 {\"product_ref\":\"product:真实ID\"}。客户已明确选择，或已收集足够信息并形成有依据的主动推荐时使用。服务端重新核实商品和入口后准备卡片；返回 prepared 后仍不等于 sent。final_response 应说清匹配理由并用 prepared ref 安排位置。",
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
        "输入 {\"query\":\"客户问题或资料目的\",\"limit\":5}。返回匹配资料 material_ref、价值、适用范围和权益限制；搜索不等于发送。养兰卡片可发不代表卡内受限视频可看；若客户反馈视频无权限，先说明需核验购买权益，可请客户提供抖音购买截图并转人工，不能自行开通或承诺。",
    ),
    CapabilitySpec(
        "material.send",
        "tool",
        ("发资料", "资料卡片", "发送手册", "释放资料"),
        "输入 {\"material_ref\":\"material:真实引用\"}。只在能说清资料与当前客户的匹配、释放后的销售或关系目的，或客户不购买时把它作为最终福利触点时使用。客户索要资料只是兴趣信号；若基本情况和需求未清，先顺着资料内容挖需，不调用本工具。服务端校验引用、发布状态、权益和近期重复后准备卡片；prepared 不等于 sent。发送后继续挖需或转入有依据的推品。",
    ),
    CapabilitySpec(
        "order.search",
        "tool",
        ("订单", "付款", "发货", "物流", "快递", "手机号"),
        "输入 {\"mobile\":\"客户主动提供的手机号，可选\",\"limit\":3}。只能查询当前客户绑定或其主动提供手机号的订单，返回脱敏信息和 order_ref。",
    ),
    CapabilitySpec(
        "order.get",
        "tool",
        ("订单详情", "order_ref", "物流详情"),
        "输入 {\"order_ref\":\"order:真实订单号\",\"mobile\":\"可选\"}。只读当前客户订单；不能修改、退款或取消。",
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
        "挖需是为了收集足以支撑销售匹配的客户事实，不是走表单。前段根据客户话题逐步了解目标或问题、养兰经验、环境、选择偏好、预算与顾虑中真正会改变推荐的部分，每轮只问一项。首次提问或目的不明时，先说回答后能得到的具体收益。不按固定字段数量判断完成；当已知事实足以说清客户需求、关键适配条件和推荐理由时，就停止无关追问并转入主动推品；如果一个未知还会明显改变推荐，就继续问。",
    ),
    CapabilitySpec(
        "experience.relationship_before_product",
        "experience",
        ("新客户", "新好友", "聊天", "关系", "信任", "团队", "专业价值", "购买意向"),
        "新客户前段先理解来意，围绕当前话题每轮收集一项会影响匹配的客户事实，并通过真实帮助让客户感受到萧岚苑团队专业、负责、愿意长期承接问题。不因新好友或普通兴趣过早推品；但当需求、目标和关键适配条件已足以说清推荐理由时，即使客户没有主动说想买，也应自然转入真实商品查询和主动推荐。客户明确问品种、价格、规格、库存、购买方式或要链接时直接推进，不为挖需而拖延。",
    ),
    CapabilitySpec(
        "experience.material_value",
        "experience",
        ("资料", "手册", "教程", "福利", "触点"),
        "资料是销售资产，不是客户一索要就自动发送的公共附件。索要资料说明客户愿意学习，应借资料会涉及的品种、养护、痛点或目标自然收集一项关键信息。当基本情况和需求尚未形成可用判断时，本轮不发资料，也不用固定‘再问一次’次数规则；当资料已与客户问题和成交目的匹配，或客户不购买时把它作为最终福利触点，才决定释放。真正发送时说明为什么给和重点看什么；发送后若信息仍不足就顺资料继续挖需，若已足够就转入有依据的推品，不被动结束。",
    ),
    CapabilitySpec(
        "experience.objection",
        "experience",
        ("贵", "考虑", "犹豫", "拒绝", "沉默", "顾虑", "谢谢", "先看看"),
        "先理解价格、价值、信任、适配还是时机顾虑，只回应真正阻碍。‘好的’‘谢谢’‘我先看看’是礼貌性软收口，不是明确拒绝；关键需求仍不清楚时，降低压力并换一个更容易回答的角度继续了解，不用被动客服式结尾。明确拒绝时当下收住压力，后续每日触达重新选择服务、价值或成交角度。",
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
)


async def execute_agent_tool(
    *, call_id: str, name: str, arguments: dict[str, Any], context: AgentExecutionContext
) -> AgentToolResult:
    handlers: dict[str, Callable[..., Awaitable[AgentToolResult]]] = {
        "capability.search": _capability_search,
        "customer.get_context": _customer_context,
        "knowledge.search": _knowledge_search,
        "product.search": _product_search,
        "product.get": _product_get,
        "product.send_card": _product_send_card,
        "care_manual.search": _care_manual_search,
        "material.search": _material_search,
        "material.send": _material_send,
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
            "access": ORCHID_MATERIAL_ASSET["access"],
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
    )


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
    return _order_tool_result(call_id, "order.get", result)


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
