from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.domains.decisioning.schemas.agent import AgentTurnDecision
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.decisioning.services.agent_prompt import (
    HARNESS_VERSION,
    build_system_prompt,
    build_turn_payload,
)
from app.domains.decisioning.services.customer_reply_formatter import (
    split_customer_messages,
)
from app.domains.decisioning.services.agent_tools import (
    AgentExecutionContext,
    execute_agent_tool,
)
from app.integrations.ai.services.llm_service import generate_messages_json


logger = logging.getLogger("wechat_rag_bot.sales_agent")
MAX_AGENT_STEPS = 5
MAX_TOOL_CALLS = 10
_FORBIDDEN_PROMOTION_CLAIMS = (
    "申请成功",
    "已经申请到",
    "给您申请到",
    "最低价",
    "全网最低",
    "仅剩",
    "最后一个名额",
    "最后几个名额",
    "倒计时",
)
_INTERNAL_MARKERS = (
    "commercial_judgment",
    "relationship_purpose",
    "tool_results",
    "customer_workspace",
    "system prompt",
    "系统提示词",
    "用户画像字段",
    "销售阶段",
    "reply_plan",
)
_SENT_SUCCESS_PATTERNS = (
    re.compile(r"(?:已经|已)(?:给您)?(?:把)?(?:资料|卡片|链接)(?:发|发送)"),
    re.compile(r"(?:资料|卡片|链接)(?:已经|已)(?:给您)?(?:发|发送)"),
    re.compile(r"(?:已经|已)(?:给您)?发送(?:成功|过去|好了)"),
)
_PRICE_PATTERN = re.compile(r"(?<!\d)(\d+(?:\.\d{1,2})?)\s*(?:元|块)(?!\d)")
_STOCK_PATTERN = re.compile(r"(?:库存|还剩|剩余)\s*(\d+)\s*(?:件|盆|株|个)")
_URL_PATTERN = re.compile(r"https?://[^\s<>\"，。！、；：）)\]}]+")
_LIST_STYLE_PATTERN = re.compile(
    r"(?m)^\s*(?:[-*•·]|(?:\d+|[一二三四五六七八九十]+)[.、．)）])\s*"
)
_MARKDOWN_HEADING_PATTERN = re.compile(r"(?m)^\s*#{1,6}\s+")
_CUSTOMER_QUOTE_MARKERS = "“”‘’「」『』《》【】\"'"
_SUBSTANTIVE_QUESTION_PATTERN = re.compile(
    r"哪一种|哪一款|哪种|哪个|哪里|哪儿|什么|多少|多久|多大|几(?:盆|株|天|次|年)?|"
    r"怎么|为什么|有没有|是否|是不是|能不能|可不可以|还是"
)
_OPENING_SALES_PUSH_MARKERS = (
    "购买",
    "想买",
    "下单",
    "价格",
    "预算",
    "看花",
    "选花",
    "挑花",
    "商品",
    "产品",
)


async def run_sales_agent(
    *,
    message,
    user_state,
    workspace: dict[str, Any],
) -> FinalReply:
    context = AgentExecutionContext(
        message=message,
        user_state=user_state,
        workspace=workspace,
    )
    event_context = _event_context(message)
    conversation: list[dict[str, str]] = [
        {"role": "system", "content": build_system_prompt()},
    ]
    tool_results: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    total_tool_calls = 0
    usage: dict[str, int] = {}
    latest_decision: AgentTurnDecision | None = None

    for step in range(MAX_AGENT_STEPS):
        conversation.append(
            {
                "role": "user",
                "content": build_turn_payload(
                    customer_message=message.message,
                    customer_workspace=workspace,
                    event_context=event_context,
                    tool_results=tool_results,
                ),
            }
        )
        try:
            raw = await generate_messages_json(
                conversation,
                purpose="business",
                temperature=0.35,
                prompt_version=HARNESS_VERSION,
            )
            _merge_usage(usage, raw.get("usage"))
            decision = AgentTurnDecision.model_validate(raw.get("data"))
        except (ValidationError, TypeError, ValueError) as exc:
            logger.warning("Sales Agent returned invalid decision: %s", type(exc).__name__)
            conversation.append(
                {
                    "role": "system",
                    "content": "上一个输出不符合 Agent JSON 契约。请按规定结构重新判断，不要输出解释。",
                }
            )
            continue

        latest_decision = decision
        conversation.append(
            {
                "role": "assistant",
                "content": json.dumps(decision.model_dump(mode="json"), ensure_ascii=False),
            }
        )
        if decision.tool_calls:
            if str(event_context.get("system_event") or "") == "first_contact":
                conversation.append(
                    {
                        "role": "system",
                        "content": "新好友开场不调用工具，也不发送商品或资料。请直接按两条短文字的开场结构重新输出。",
                    }
                )
                continue
            if total_tool_calls + len(decision.tool_calls) > MAX_TOOL_CALLS:
                conversation.append(
                    {
                        "role": "system",
                        "content": "工具调用总数已达到上限。请使用现有事实给出安全、自然的最终回复；事实不足时转人工。",
                    }
                )
                continue
            round_results = []
            for call in decision.tool_calls:
                if call.call_id in seen_call_ids:
                    round_results.append(
                        {
                            "call_id": call.call_id,
                            "tool": call.name,
                            "status": "invalid_arguments",
                            "data": {"error": "duplicate_call_id"},
                        }
                    )
                    continue
                seen_call_ids.add(call.call_id)
                total_tool_calls += 1
                result = await execute_agent_tool(
                    call_id=call.call_id,
                    name=call.name,
                    arguments=call.arguments,
                    context=context,
                )
                round_results.append(result.model_dump(mode="json"))
            tool_results.extend(round_results)
            continue

        if decision.final_response is None:
            continue
        violations = _guard_violations(decision, context)
        if violations and step < MAX_AGENT_STEPS - 1:
            tool_results.append(
                {
                    "call_id": "system_guard",
                    "tool": "system.hard_boundary",
                    "status": "forbidden",
                    "data": {"violations": violations},
                }
            )
            conversation.append(
                {
                    "role": "system",
                    "content": (
                        "客户可见回复触发硬边界。基于反馈重写；不要删除已核实的有用信息，不要输出内部说明。"
                        "如果是表达问题，每轮只问一个信息点，不用编号、项目符号、Markdown，也不要给普通词语加引号。"
                        f"本次问题：{', '.join(violations)}"
                    ),
                }
            )
            continue
        if violations:
            logger.warning("Sales Agent final response blocked: %s", ",".join(violations))
            break
        return await _finalize_reply(
            decision=decision,
            context=context,
            usage=usage,
            tool_results=tool_results,
        )

    return await _safe_fallback(
        message=message,
        latest_decision=latest_decision,
        context=context,
        usage=usage,
        tool_results=tool_results,
    )


async def _finalize_reply(
    *,
    decision: AgentTurnDecision,
    context: AgentExecutionContext,
    usage: dict[str, int],
    tool_results: list[dict[str, Any]],
) -> FinalReply:
    final = decision.final_response
    assert final is not None
    outbound: list[OutboundMessage] = []
    visible_texts: list[str] = []
    for item in final.messages:
        if item.type == "text":
            content = str(item.content or "").strip()
            if content:
                for message in split_customer_messages(content):
                    visible_texts.append(message)
                    outbound.append(OutboundMessage(type="text", content=message))
            continue
        ref = str(item.ref or "").strip()
        prepared = context.prepared.get(ref)
        if prepared:
            outbound.extend(prepared)
    outbound = outbound[:5]
    visible_texts = [message.content for message in outbound if message.type == "text"]

    if final.need_human and context.handoff is None:
        await execute_agent_tool(
            call_id="system_handoff",
            name="human.handoff",
            arguments={
                "reason": final.handoff_reason or "human_required",
                "summary": decision.commercial_judgment,
            },
            context=context,
        )
    need_human = context.handoff is not None or final.need_human
    answer = "\n\n".join(visible_texts)
    if not outbound and not need_human:
        answer = "您接着说就行，我先按您现在最想解决的问题帮您看。"
        outbound = [OutboundMessage(type="text", content=answer)]
        visible_texts = [answer]
    if _is_opening_system_event(context):
        outbound = _insert_opening_image(outbound)
    return FinalReply(
        answer=answer,
        answer_segments=visible_texts,
        outbound_messages=outbound,
        reply_type="sales_agent",
        route="human" if need_human else "agent",
        sources=_dedupe_sources(context.sources),
        usage=usage,
        need_human=need_human,
        next_action="human_handoff" if need_human else final.next_action,
        metadata={
            "agent_runtime": {
                "version": HARNESS_VERSION,
                "commercial_judgment": decision.commercial_judgment,
                "relationship_purpose": decision.relationship_purpose,
                "customer_signal": decision.customer_signal,
                "tool_trace": tool_results,
            },
            **({"handoff": context.handoff} if context.handoff else {}),
        },
    )


async def _safe_fallback(
    *,
    message,
    latest_decision: AgentTurnDecision | None,
    context: AgentExecutionContext,
    usage: dict[str, int],
    tool_results: list[dict[str, Any]],
) -> FinalReply:
    required_handoff = _required_handoff_reason(str(message.message or ""))
    if required_handoff:
        if context.handoff is None:
            await execute_agent_tool(
                call_id="system_required_handoff",
                name="human.handoff",
                arguments={
                    "reason": required_handoff,
                    "summary": (
                        latest_decision.commercial_judgment
                        if latest_decision is not None
                        else "客户当前请求需要人工处理"
                    ),
                },
                context=context,
            )
        return FinalReply(
            answer="",
            answer_segments=[],
            outbound_messages=[],
            reply_type="human",
            route="human",
            usage=usage,
            need_human=True,
            next_action="human_handoff",
            metadata={
                "agent_runtime": {
                    "version": HARNESS_VERSION,
                    "commercial_judgment": (
                        latest_decision.commercial_judgment
                        if latest_decision is not None
                        else "客户当前请求超出 Agent 的执行权限"
                    ),
                    "relationship_purpose": "及时交给有权限的人工负责到底",
                    "customer_signal": (
                        latest_decision.customer_signal if latest_decision else "none"
                    ),
                    "tool_trace": tool_results,
                    "hard_boundary_fallback": required_handoff,
                },
                **({"handoff": context.handoff} if context.handoff else {}),
            },
        )
    system_event = str((message.metadata or {}).get("system_event") or "")
    if system_event == "first_contact":
        intro = "您好，我是萧岚苑的小兰，我们团队平时都在和兰花打交道，后面养护上有什么拿不准都可以找我。"
        question = "我先了解一下您的情况，后面给您的养护建议和资料也能更贴合。您是刚接触兰花，还是家里已经养了一些？"
        texts = [intro, question]
        text = "\n\n".join(texts)
        purpose = "完成自然自我介绍，并用一个低压力问题了解客户来意"
    elif system_event == "daily_touch":
        text = "最近养兰有哪里拿不准，您随时拍张照片发我，我帮您一起看看。"
        texts = [text]
        purpose = "用低压力的专业服务完成今日关系触达"
    else:
        text = "您接着说就行，我先按您现在最想解决的问题帮您看。"
        texts = [text]
        purpose = "安全承接客户并保持对话"
    judgment = (
        latest_decision.commercial_judgment
        if latest_decision is not None
        else "当前模型决策未形成可安全发送的完整回复"
    )
    outbound = [OutboundMessage(type="text", content=content) for content in texts]
    if system_event == "first_contact":
        outbound = _insert_opening_image(outbound)
    return FinalReply(
        answer=text,
        answer_segments=texts,
        outbound_messages=outbound,
        reply_type="sales_agent_fallback",
        route="agent",
        usage=usage,
        metadata={
            "agent_runtime": {
                "version": HARNESS_VERSION,
                "commercial_judgment": judgment,
                "relationship_purpose": purpose,
                "customer_signal": (
                    latest_decision.customer_signal if latest_decision else "none"
                ),
                "tool_trace": tool_results,
                "fallback": True,
            }
        },
    )


def _guard_violations(
    decision: AgentTurnDecision,
    context: AgentExecutionContext,
) -> list[str]:
    final = decision.final_response
    if final is None:
        return []
    text = "\n".join(
        str(item.content or "") for item in final.messages if item.type == "text"
    )
    violations: list[str] = []
    question_count = _customer_question_count(text)
    if question_count > 1:
        violations.append("too_many_customer_questions")
    if _LIST_STYLE_PATTERN.search(text) or _MARKDOWN_HEADING_PATTERN.search(text):
        violations.append("non_conversational_list_style")
    if any(marker in text for marker in _CUSTOMER_QUOTE_MARKERS):
        violations.append("unnecessary_customer_quotes")
    if _is_opening_system_event(context):
        text_messages = [item for item in final.messages if item.type == "text"]
        if len(final.messages) != 2 or len(text_messages) != 2:
            violations.append("invalid_opening_message_structure")
        else:
            intro = str(text_messages[0].content or "")
            question = str(text_messages[1].content or "")
            if "萧岚苑" not in intro or "小兰" not in intro:
                violations.append("opening_identity_missing")
            if _customer_question_count(intro) != 0:
                violations.append("opening_intro_contains_question")
            if _customer_question_count(question) != 1:
                violations.append("opening_needs_question_invalid")
            if any(marker in question for marker in _OPENING_SALES_PUSH_MARKERS):
                violations.append("opening_sales_push_question")
    lowered = text.casefold()
    if any(marker.casefold() in lowered for marker in _INTERNAL_MARKERS):
        violations.append("internal_state_leak")
    if any(claim in text for claim in _FORBIDDEN_PROMOTION_CLAIMS):
        violations.append("unverified_promotion_claim")
    if any(pattern.search(text) for pattern in _SENT_SUCCESS_PATTERNS):
        violations.append("unverified_delivery_success")
    required_handoff = _required_handoff_reason(str(context.message.message or ""))
    if required_handoff and context.handoff is None and not final.need_human:
        violations.append(f"required_handoff:{required_handoff}")
    prices = {round(float(value), 2) for value in _PRICE_PATTERN.findall(text)}
    if prices:
        verified = _verified_prices(context)
        if not prices.issubset(verified):
            violations.append("unverified_price")
    if any(
        marker in text
        for marker in (
            "有货",
            "现货",
            "库存充足",
            "库存不足",
            "缺货",
            "没货",
            "无货",
            "售罄",
        )
    ):
        if not _has_found_tool(context, "product.search", "product.get"):
            violations.append("unverified_inventory")
    stock_values = {int(value) for value in _STOCK_PATTERN.findall(text)}
    if stock_values and not stock_values.issubset(_verified_stocks(context)):
        violations.append("unverified_stock_count")
    if any(
        marker in text
        for marker in (
            "待付款",
            "已付款",
            "已经付款",
            "已发货",
            "已经发出",
            "运输中",
            "派送中",
            "物流显示",
            "已签收",
            "已完成",
        )
    ):
        if not _has_found_tool(context, "order.search", "order.get"):
            violations.append("unverified_order_status")
    urls = set(_URL_PATTERN.findall(text))
    if urls and not urls.issubset(_verified_urls(context)):
        violations.append("unverified_url")
    for item in final.messages:
        if item.type == "prepared" and str(item.ref or "") not in context.prepared:
            violations.append("unknown_prepared_ref")
    return list(dict.fromkeys(violations))


def _customer_question_count(text: str) -> int:
    punctuation_count = text.count("？") + text.count("?")
    if punctuation_count == 0:
        return 0
    clauses = [
        part.strip() for part in re.split(r"[，,；;、！？?]", text) if part.strip()
    ]
    question_clauses = sum(
        1
        for clause in clauses
        if _SUBSTANTIVE_QUESTION_PATTERN.search(clause)
        or clause.endswith(("吗", "呢"))
    )
    substantive_markers = len(_SUBSTANTIVE_QUESTION_PATTERN.findall(text))
    return max(punctuation_count, question_clauses, substantive_markers)


def _is_opening_system_event(context: AgentExecutionContext) -> bool:
    metadata = context.message.metadata
    return isinstance(metadata, dict) and metadata.get("system_event") == "first_contact"


def _insert_opening_image(
    messages: list[OutboundMessage],
) -> list[OutboundMessage]:
    if len(messages) != 2 or any(message.type != "text" for message in messages):
        return messages
    settings = get_settings()
    image_url = settings.eyun_opening_image_url.strip()
    material_id = settings.eyun_opening_material_id
    if material_id and image_url:
        image = OutboundMessage(
            type="image", content=image_url, material_id=material_id
        )
    elif material_id:
        image = OutboundMessage(
            type="material", content="[开场白图片]", material_id=material_id
        )
    elif image_url:
        image = OutboundMessage(type="image", content=image_url)
    else:
        return messages
    return [messages[0], image, messages[1]]


def _required_handoff_reason(text: str) -> str | None:
    """Enforce authorization boundaries, not sales intent or reply wording."""
    normalized = re.sub(r"\s+", "", text)
    if not normalized:
        return None
    if re.search(r"(?:转|找|要|叫|联系|换)(?:一下)?(?:人工|真人|客服)", normalized):
        return "customer_requested_human"
    if any(
        marker in normalized
        for marker in (
            "退款",
            "退货",
            "投诉",
            "赔偿",
            "索赔",
            "改价",
            "修改订单",
            "修改地址",
            "改地址",
            "取消订单",
        )
    ):
        return "authorized_human_action_required"
    return None


def _verified_prices(context: AgentExecutionContext) -> set[float]:
    prices: set[float] = set()
    for fact in context.tool_facts.values():
        if fact.get("status") not in {"found", "prepared"}:
            continue
        _collect_prices(fact.get("data"), prices)
    return prices


def _verified_stocks(context: AgentExecutionContext) -> set[int]:
    stocks: set[int] = set()
    for fact in context.tool_facts.values():
        if fact.get("tool") not in {"product.search", "product.get", "product.send_card"}:
            continue
        if fact.get("status") not in {"found", "prepared"}:
            continue
        _collect_named_ints(fact.get("data"), "stock", stocks)
    return stocks


def _verified_urls(context: AgentExecutionContext) -> set[str]:
    urls: set[str] = set()
    for fact in context.tool_facts.values():
        if fact.get("status") not in {"found", "prepared"}:
            continue
        _collect_urls(fact.get("data"), urls)
    return urls


def _collect_prices(value: Any, output: set[float]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "price_cent" and isinstance(item, int):
                output.add(round(item / 100, 2))
            else:
                _collect_prices(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_prices(item, output)


def _collect_named_ints(value: Any, target_key: str, output: set[int]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == target_key and isinstance(item, int):
                output.add(item)
            else:
                _collect_named_ints(item, target_key, output)
    elif isinstance(value, list):
        for item in value:
            _collect_named_ints(item, target_key, output)


def _collect_urls(value: Any, output: set[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_urls(item, output)
    elif isinstance(value, list):
        for item in value:
            _collect_urls(item, output)
    elif isinstance(value, str) and value.startswith(("http://", "https://")):
        output.add(value)


def _has_found_tool(context: AgentExecutionContext, *names: str) -> bool:
    return any(
        fact.get("tool") in names and fact.get("status") in {"found", "prepared"}
        for fact in context.tool_facts.values()
    )


def _event_context(message) -> dict[str, Any]:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    allowed = {
        key: metadata.get(key)
        for key in (
            "system_event",
            "daily_touch",
            "is_first_contact",
            "message_type",
            "media",
            "vision_description",
            "attachment_error",
            "business_snapshot",
            "tool_state",
        )
        if metadata.get(key) not in (None, "", [], {})
    }
    if allowed.get("system_event") == "daily_touch":
        allowed["instruction"] = (
            "这是系统每日触达唤醒，不是客户原话。必须根据最新工作区生成一条有商业判断和关系目的的自然消息；"
            "不要提到系统、任务、计数或唤醒。"
        )
    elif allowed.get("system_event") == "first_contact":
        allowed["instruction"] = (
            "这是新好友建立事件，不是客户原话。只生成两条短文字：第一条自然介绍自己是萧岚苑的小兰，"
            "并自然带出团队长期做兰花、后面愿意继续帮客户看养护问题；第二条先从客户视角简短说明回答后能得到什么，"
            "例如更贴合的养护建议或资料，再只问一个容易回答、能让客户自然开口的挖需问题。"
            "不要问客户要不要买、看花、选花、预算或价格，不要假设他有购买意向。固定图片会由发送网关插在两条文字之间，"
            "你不要调用工具、安排卡片或资料，也不要提到系统事件。措辞可以自然变化，但不要机械盘问地区、盆数和品种。"
        )
    return allowed


def _merge_usage(target: dict[str, int], incoming: Any) -> None:
    if not isinstance(incoming, dict):
        return
    for key, value in incoming.items():
        if isinstance(value, int):
            target[key] = target.get(key, 0) + value


def _dedupe_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        key = json.dumps(source, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(source)
    return result[:8]
