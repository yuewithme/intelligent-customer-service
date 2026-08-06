from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.domains.decisioning.schemas.agent import AgentTurnDecision
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.decisioning.services.agent_prompt import (
    HARNESS_VERSION,
    build_system_prompt,
    build_tool_result_payload,
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
MAX_TOOL_ROUNDS = 5
MAX_TOOL_CALLS = 10
MAX_SCHEMA_REPAIRS = 1
MAX_HARD_REWRITES = 1
MAX_TRAJECTORY_REWRITES = 2
MAX_AGENT_MODEL_CALLS = (
    MAX_TOOL_ROUNDS
    + MAX_SCHEMA_REPAIRS
    + MAX_HARD_REWRITES
    + MAX_TRAJECTORY_REWRITES
    + 2
)
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
_LOW_INFORMATION_MARKERS = (
    "不懂",
    "不太懂",
    "不知道",
    "不清楚",
    "不记得",
    "记不清",
    "没注意",
    "没看",
    "看不出来",
    "说不准",
    "商家给什么",
    "商家给的",
)
_UNCERTAIN_ANSWER_MARKERS = ("好像", "可能", "大概", "应该", "估计")
_ACTIVE_CARE_RISK_MARKERS = (
    "烂根",
    "腐苗",
    "软腐",
    "发臭",
    "枯萎",
    "倒苗",
    "病斑",
    "黑斑",
    "虫害",
    "大量黄叶",
    "根发黑",
)
_FOLLOWUP_ACTION_MARKERS = (
    "等待客户",
    "等客户",
    "客户反馈",
    "继续追问",
    "继续确认",
    "让客户",
    "请客户",
    "进一步了解",
)
_TECHNICAL_TOPIC_MARKERS = {
    "medium": (
        "植料",
        "树皮",
        "石子",
        "火山石",
        "颗粒",
        "粉末",
        "细土",
        "盆面",
        "表层",
        "拨开",
        "透气",
        "沥水",
    ),
    "watering": ("浇水", "干湿", "湿度", "干透", "积水"),
    "environment": ("通风", "光照", "温度", "室内", "室外", "阳台", "朝向"),
    "fertilizing": ("施肥", "肥料", "缓释肥", "营养液", "肥水"),
    "symptom_detail": (
        "叶尖",
        "叶基",
        "叶片",
        "斑点",
        "叶片颜色",
        "根的颜色",
        "软硬",
        "变化速度",
        "根系",
    ),
}
_TUTORIAL_DELIVERY_MARKERS = (
    "单品养护教程",
    "单品养护手册",
    "对应品种的养护教程",
    "对应品种的单品",
)
_ONE_TO_ONE_DELIVERY_MARKERS = (
    "一对一指导",
    "一对一实操",
    "师傅一对一",
    "养兰师傅",
)
_VIDEO_ACCESS_NOTIFICATION_CLAIMS = (
    "已提醒同事",
    "已经提醒同事",
    "已通知同事",
    "已经通知同事",
    "已联系同事处理",
    "已经联系同事处理",
    "已提交权限处理",
    "已经提交权限处理",
    "已提交权限申请",
    "已经提交权限申请",
    "已提交给同事核对",
    "已经提交给同事核对",
)
_VIDEO_ACCESS_INCORRECT_WORDING = (
    "提交处理",
    "提交申请",
    "提交核对",
    "提交权限",
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
        {
            "role": "user",
            "content": build_turn_payload(
                customer_message=message.message,
                customer_workspace=workspace,
                event_context=event_context,
                tool_results=[],
            ),
        },
    ]
    tool_results: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    total_tool_calls = 0
    usage: dict[str, int] = {}
    latest_decision: AgentTurnDecision | None = None
    attempt_trace: list[dict[str, Any]] = []
    schema_repairs = 0
    hard_rewrites = 0
    trajectory_rewrites = 0
    tool_rounds = 0
    tool_budget_exhausted = False

    for attempt_number in range(1, MAX_AGENT_MODEL_CALLS + 1):
        raw: dict[str, Any] | None = None
        started = time.perf_counter()
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
            attempt_trace.append(
                _invalid_attempt_diagnostic(
                    attempt_number=attempt_number,
                    raw=raw,
                    error=type(exc).__name__,
                    duration_ms=_elapsed_ms(started),
                )
            )
            if schema_repairs >= MAX_SCHEMA_REPAIRS:
                return await _safe_fallback(
                    message=message,
                    latest_decision=latest_decision,
                    context=context,
                    usage=usage,
                    tool_results=tool_results,
                    attempt_trace=attempt_trace,
                    failure_reason="invalid_agent_schema",
                )
            schema_repairs += 1
            conversation.append(
                {
                    "role": "system",
                    "content": (
                        "上一个输出不符合 Agent JSON 契约。工具调用与最终回复必须二选一："
                        "需要调用工具时 final_response 必须为 null；准备回复客户时 tool_calls 必须为空。"
                        "未进入最终回复的文字和卡片都没有发送给客户。请按规定结构重新判断，不要输出解释。"
                    ),
                }
            )
            continue
        except Exception as exc:
            logger.exception("Sales Agent model call failed: %s", type(exc).__name__)
            attempt_trace.append(
                {
                    "attempt": attempt_number,
                    "outcome": "model_failure",
                    "error": type(exc).__name__,
                    "duration_ms": _elapsed_ms(started),
                }
            )
            return await _safe_fallback(
                message=message,
                latest_decision=latest_decision,
                context=context,
                usage=usage,
                tool_results=tool_results,
                attempt_trace=attempt_trace,
                failure_reason="model_failure",
            )

        latest_decision = decision
        diagnostic = _decision_diagnostic(
            attempt_number=attempt_number,
            raw=raw,
            decision=decision,
            duration_ms=_elapsed_ms(started),
        )
        attempt_trace.append(diagnostic)
        conversation.append(
            {
                "role": "assistant",
                "content": json.dumps(decision.model_dump(mode="json"), ensure_ascii=False),
            }
        )
        if decision.tool_calls:
            tool_trajectory_violations = _tool_sales_trajectory_violations(decision)
            diagnostic["trajectory_violations"] = tool_trajectory_violations
            if tool_trajectory_violations:
                if trajectory_rewrites < MAX_TRAJECTORY_REWRITES:
                    diagnostic["outcome"] = "trajectory_rewrite_requested"
                    trajectory_rewrites += 1
                else:
                    diagnostic["outcome"] = "premature_card_suppressed"
                conversation.append(
                    {
                        "role": "system",
                        "content": _sales_flow_rewrite_instruction(
                            tool_trajectory_violations
                        ),
                    }
                )
                continue
            if str(event_context.get("system_event") or "") == "first_contact":
                diagnostic["outcome"] = "opening_tool_blocked"
                diagnostic["hard_violations"] = ["opening_tool_call_forbidden"]
                if hard_rewrites >= MAX_HARD_REWRITES:
                    return await _safe_fallback(
                        message=message,
                        latest_decision=latest_decision,
                        context=context,
                        usage=usage,
                        tool_results=tool_results,
                        attempt_trace=attempt_trace,
                        failure_reason="invalid_opening",
                    )
                hard_rewrites += 1
                conversation.append(
                    {
                        "role": "system",
                        "content": "新好友开场不调用工具，也不发送商品或资料。请直接按两条短文字的开场结构重新输出。",
                    }
                )
                continue
            if (
                tool_rounds >= MAX_TOOL_ROUNDS
                or total_tool_calls + len(decision.tool_calls) > MAX_TOOL_CALLS
            ):
                diagnostic["outcome"] = "tool_budget_exhausted"
                if tool_budget_exhausted:
                    return await _safe_fallback(
                        message=message,
                        latest_decision=latest_decision,
                        context=context,
                        usage=usage,
                        tool_results=tool_results,
                        attempt_trace=attempt_trace,
                        failure_reason="tool_budget_exhausted",
                    )
                tool_budget_exhausted = True
                conversation.append(
                    {
                        "role": "system",
                        "content": "工具调用总数已达到上限。请使用现有事实给出安全、自然的最终回复；事实不足时转人工。",
                    }
                )
                continue
            tool_rounds += 1
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
            diagnostic["outcome"] = "tool_calls_executed"
            diagnostic["tool_results"] = [
                {
                    "call_id": result.get("call_id"),
                    "tool": result.get("tool"),
                    "status": result.get("status"),
                }
                for result in round_results
            ]
            conversation.append(
                {
                    "role": "user",
                    "content": build_tool_result_payload(round_results),
                }
            )
            continue

        if decision.final_response is None:
            continue
        hard_violations = _guard_violations(decision, context)
        quality_flags = _quality_flags(decision)
        trajectory_violations = _sales_trajectory_violations(decision, context)
        diagnostic["hard_violations"] = hard_violations
        diagnostic["quality_flags"] = quality_flags
        diagnostic["trajectory_violations"] = trajectory_violations
        if hard_violations and hard_rewrites < MAX_HARD_REWRITES:
            diagnostic["outcome"] = "hard_rewrite_requested"
            hard_rewrites += 1
            conversation.append(
                {
                    "role": "system",
                    "content": _hard_rewrite_instruction(hard_violations),
                }
            )
            continue
        if hard_violations:
            diagnostic["outcome"] = "hard_blocked"
            logger.warning(
                "Sales Agent final response blocked: %s",
                ",".join(hard_violations),
            )
            return await _safe_fallback(
                message=message,
                latest_decision=latest_decision,
                context=context,
                usage=usage,
                tool_results=tool_results,
                attempt_trace=attempt_trace,
                failure_reason="hard_boundary_not_repaired",
            )
        if trajectory_violations and trajectory_rewrites < MAX_TRAJECTORY_REWRITES:
            diagnostic["outcome"] = "trajectory_rewrite_requested"
            trajectory_rewrites += 1
            conversation.append(
                {
                    "role": "system",
                    "content": _sales_flow_rewrite_instruction(
                        trajectory_violations
                    ),
                }
            )
            continue
        if trajectory_violations:
            quality_flags = list(
                dict.fromkeys([*quality_flags, *trajectory_violations])
            )
        diagnostic["outcome"] = "accepted"
        return await _finalize_reply(
            decision=decision,
            context=context,
            usage=usage,
            tool_results=tool_results,
            quality_flags=quality_flags,
            attempt_trace=attempt_trace,
        )

    return await _safe_fallback(
        message=message,
        latest_decision=latest_decision,
        context=context,
        usage=usage,
        tool_results=tool_results,
        attempt_trace=attempt_trace,
        failure_reason="model_call_budget_exhausted",
    )


async def _finalize_reply(
    *,
    decision: AgentTurnDecision,
    context: AgentExecutionContext,
    usage: dict[str, int],
    tool_results: list[dict[str, Any]],
    quality_flags: list[str],
    attempt_trace: list[dict[str, Any]],
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
        await execute_agent_tool(
            call_id="system_empty_reply_handoff",
            name="human.handoff",
            arguments={
                "reason": "empty_agent_reply",
                "summary": decision.commercial_judgment,
            },
            context=context,
        )
        need_human = True
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
                "trace_id": context.message.trace_id,
                "commercial_judgment": decision.commercial_judgment,
                "relationship_purpose": decision.relationship_purpose,
                "customer_signal": decision.customer_signal,
                "purchase_signal": decision.purchase_signal,
                "tool_trace": tool_results,
                "hard_violations": [],
                "quality_flags": quality_flags,
                "attempt_trace": attempt_trace,
                "result": (
                    "human_handoff"
                    if need_human and not outbound
                    else "sent_with_handoff" if need_human else "sent"
                ),
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
    attempt_trace: list[dict[str, Any]],
    failure_reason: str,
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
                    "trace_id": context.message.trace_id,
                    "commercial_judgment": (
                        latest_decision.commercial_judgment
                        if latest_decision is not None
                        else "客户当前请求超出 Agent 的执行权限"
                    ),
                    "relationship_purpose": "及时交给有权限的人工负责到底",
                    "customer_signal": (
                        latest_decision.customer_signal if latest_decision else "none"
                    ),
                    "purchase_signal": (
                        latest_decision.purchase_signal if latest_decision else "none"
                    ),
                    "tool_trace": tool_results,
                    "attempt_trace": attempt_trace,
                    "hard_boundary_fallback": required_handoff,
                    "failure_reason": failure_reason,
                    "result": "human_handoff",
                },
                **({"handoff": context.handoff} if context.handoff else {}),
            },
        )
    system_event = str((message.metadata or {}).get("system_event") or "")
    if system_event == "first_contact":
        intro = "您好，我是萧岚苑的小兰，我们团队平时都在和兰花打交道，后面养护上有什么拿不准都可以找我。"
        question = "为了后面给您更贴合的养护建议和资料，我先了解一下，您家里现在大概养了多少盆，主要都是什么品种呀？"
        texts = [intro, question]
        text = "\n\n".join(texts)
        purpose = "完成自然自我介绍，并了解客户当前盆数和主要品种"
    else:
        if context.handoff is None:
            await execute_agent_tool(
                call_id="system_runtime_handoff",
                name="human.handoff",
                arguments={
                    "reason": "agent_runtime_failure",
                    "summary": (
                        latest_decision.commercial_judgment
                        if latest_decision is not None
                        else "Agent 未形成可安全发送的完整回复"
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
                    "trace_id": context.message.trace_id,
                    "commercial_judgment": (
                        latest_decision.commercial_judgment
                        if latest_decision is not None
                        else "Agent 未形成可安全发送的完整回复"
                    ),
                    "relationship_purpose": "交给人工继续处理当前客户问题",
                    "customer_signal": (
                        latest_decision.customer_signal if latest_decision else "none"
                    ),
                    "purchase_signal": (
                        latest_decision.purchase_signal if latest_decision else "none"
                    ),
                    "tool_trace": tool_results,
                    "attempt_trace": attempt_trace,
                    "failure_reason": failure_reason,
                    "result": "human_handoff",
                },
                **({"handoff": context.handoff} if context.handoff else {}),
            },
        )
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
                "trace_id": context.message.trace_id,
                "commercial_judgment": judgment,
                "relationship_purpose": purpose,
                "customer_signal": (
                    latest_decision.customer_signal if latest_decision else "none"
                ),
                "purchase_signal": (
                    latest_decision.purchase_signal if latest_decision else "none"
                ),
                "tool_trace": tool_results,
                "attempt_trace": attempt_trace,
                "fallback": True,
                "failure_reason": failure_reason,
                "result": "opening_fallback",
            }
        },
    )


def _decision_diagnostic(
    *,
    attempt_number: int,
    raw: dict[str, Any],
    decision: AgentTurnDecision,
    duration_ms: int,
) -> dict[str, Any]:
    final = decision.final_response
    visible_messages: list[dict[str, Any]] = []
    if final is not None:
        for item in final.messages:
            if item.type == "text":
                visible_messages.append(
                    {"type": "text", "content": _truncate_log_text(item.content)}
                )
            else:
                visible_messages.append(
                    {"type": "prepared", "ref": _truncate_log_text(item.ref, 128)}
                )
    return {
        "attempt": attempt_number,
        "provider": raw.get("provider"),
        "model": raw.get("model"),
        "provider_request_id": raw.get("provider_request_id"),
        "duration_ms": duration_ms,
        "outcome": "validated",
        "decision": {
            "commercial_judgment": _truncate_log_text(
                decision.commercial_judgment, 800
            ),
            "relationship_purpose": _truncate_log_text(
                decision.relationship_purpose, 400
            ),
            "customer_signal": decision.customer_signal,
            "purchase_signal": decision.purchase_signal,
            "tool_calls": [
                {"call_id": call.call_id, "name": call.name}
                for call in decision.tool_calls
            ],
            "final_response": (
                {
                    "messages": visible_messages,
                    "need_human": final.need_human,
                    "handoff_reason": _truncate_log_text(
                        final.handoff_reason, 256
                    ),
                    "next_action": _truncate_log_text(final.next_action, 400),
                }
                if final is not None
                else None
            ),
        },
    }


def _invalid_attempt_diagnostic(
    *,
    attempt_number: int,
    raw: dict[str, Any] | None,
    error: str,
    duration_ms: int,
) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    data = raw.get("data")
    data = data if isinstance(data, dict) else {}
    final = data.get("final_response")
    final = final if isinstance(final, dict) else None
    return {
        "attempt": attempt_number,
        "provider": raw.get("provider"),
        "model": raw.get("model"),
        "provider_request_id": raw.get("provider_request_id"),
        "duration_ms": duration_ms,
        "outcome": "invalid_schema",
        "error": error,
        "decision": {
            "commercial_judgment": _truncate_log_text(
                data.get("commercial_judgment"), 800
            ),
            "relationship_purpose": _truncate_log_text(
                data.get("relationship_purpose"), 400
            ),
            "customer_signal": _truncate_log_text(data.get("customer_signal"), 64),
            "purchase_signal": _truncate_log_text(data.get("purchase_signal"), 64),
            "final_response": _sanitize_raw_final(final),
        },
    }


def _sanitize_raw_final(final: dict[str, Any] | None) -> dict[str, Any] | None:
    if final is None:
        return None
    messages = final.get("messages")
    safe_messages: list[dict[str, Any]] = []
    if isinstance(messages, list):
        for item in messages[:5]:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "")
            if item_type == "text":
                safe_messages.append(
                    {
                        "type": "text",
                        "content": _truncate_log_text(item.get("content")),
                    }
                )
            elif item_type == "prepared":
                safe_messages.append(
                    {
                        "type": "prepared",
                        "ref": _truncate_log_text(item.get("ref"), 128),
                    }
                )
    return {
        "messages": safe_messages,
        "need_human": bool(final.get("need_human")),
        "handoff_reason": _truncate_log_text(final.get("handoff_reason"), 256),
        "next_action": _truncate_log_text(final.get("next_action"), 400),
    }


def _truncate_log_text(value: Any, limit: int = 1200) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}…"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


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
    opening_event = _is_opening_system_event(context)
    opening_profile_question = False
    if opening_event and final is not None:
        opening_texts = [item for item in final.messages if item.type == "text"]
        if len(opening_texts) == 2:
            opening_profile_question = _is_opening_profile_question(
                str(opening_texts[1].content or "")
            )
    if opening_event:
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
            if not opening_profile_question:
                violations.append("opening_profile_question_invalid")
            if any(marker in question for marker in _OPENING_SALES_PUSH_MARKERS):
                violations.append("opening_sales_push_question")
    lowered = text.casefold()
    if any(marker.casefold() in lowered for marker in _INTERNAL_MARKERS):
        violations.append("internal_state_leak")
    if any(claim in text for claim in _FORBIDDEN_PROMOTION_CLAIMS):
        violations.append("unverified_promotion_claim")
    if (
        _contains_specific_brand_service_claim(text)
        and not _has_found_tool(context, "brand.service_facts")
    ):
        violations.append("unverified_brand_service_claim")
    if (
        _is_video_access_context(text, str(context.message.message or ""))
        and any(claim in text for claim in _VIDEO_ACCESS_NOTIFICATION_CLAIMS)
        and not _has_tool_status(context, "video_access.request", "notified")
    ):
        violations.append("unverified_video_access_notification")
    if _is_video_access_context(
        text, str(context.message.message or "")
    ) and any(wording in text for wording in _VIDEO_ACCESS_INCORRECT_WORDING):
        violations.append("incorrect_video_access_wording")
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


def _quality_flags(decision: AgentTurnDecision) -> list[str]:
    final = decision.final_response
    if final is None:
        return []
    text = "\n".join(
        str(item.content or "") for item in final.messages if item.type == "text"
    )
    flags: list[str] = []
    if _customer_question_count(text) > 1:
        flags.append("too_many_customer_questions")
    if _LIST_STYLE_PATTERN.search(text) or _MARKDOWN_HEADING_PATTERN.search(text):
        flags.append("non_conversational_list_style")
    if any(marker in text for marker in _CUSTOMER_QUOTE_MARKERS):
        flags.append("unnecessary_customer_quotes")
    return flags


def _tool_sales_trajectory_violations(
    decision: AgentTurnDecision,
) -> list[str]:
    if decision.purchase_signal != "none":
        return []
    if any(call.name == "product.send_card" for call in decision.tool_calls):
        return ["premature_product_card_without_customer_interest"]
    return []


def _sales_trajectory_violations(
    decision: AgentTurnDecision,
    context: AgentExecutionContext,
) -> list[str]:
    """Detect repeated low-value technical discovery before it reaches customers."""
    final = decision.final_response
    if final is None:
        return []
    visible_text = "\n".join(
        str(item.content or "") for item in final.messages if item.type == "text"
    )
    violations: list[str] = []
    if _repeats_recent_assistant_content(visible_text, context):
        violations.append("repeats_recent_assistant_content")
    if decision.purchase_signal == "none" and any(
        item.type == "prepared"
        and str(
            (context.tool_facts.get(str(item.ref or "")) or {}).get("tool") or ""
        )
        == "product.send_card"
        for item in final.messages
    ):
        violations.append("premature_product_card_without_customer_interest")
    question_text = "\n".join(
        re.findall(r"[^。！!；;，,？?\n]*[？?]", visible_text)
    )
    next_action = str(final.next_action or "")
    action_is_followup = any(
        marker in next_action for marker in _FOLLOWUP_ACTION_MARKERS
    )
    candidate = "\n".join(
        part for part in (question_text, next_action if action_is_followup else "") if part
    )
    candidate_topics = _technical_topics(candidate)
    if not candidate_topics:
        return list(dict.fromkeys(violations))

    recent = context.workspace.get("recent_turns")
    recent = recent if isinstance(recent, list) else []
    recent = [item for item in recent[-8:] if isinstance(item, dict)]
    current_user_text = str(context.message.message or "")
    user_texts = [
        str(item.get("content") or "")
        for item in recent
        if str(item.get("role") or "").lower() in {"user", "customer"}
    ]
    user_texts.append(current_user_text)
    recent_user_context = "\n".join(user_texts[-4:])
    active_care_risk = any(
        marker in recent_user_context for marker in _ACTIVE_CARE_RISK_MARKERS
    )

    low_information_topics: set[str] = set()
    for text in user_texts[-4:]:
        if _is_low_information_answer(text):
            low_information_topics.update(_technical_topics(text))

    prior_topic_questions: dict[str, int] = {}
    prior_question_texts: list[str] = []
    for item in recent:
        if str(item.get("role") or "").lower() not in {"assistant", "agent"}:
            continue
        text = str(item.get("content") or "")
        if _customer_question_count(text) == 0:
            continue
        prior_question_texts.append(text)
        for topic in _technical_topics(text):
            prior_topic_questions[topic] = prior_topic_questions.get(topic, 0) + 1

    if not active_care_risk and candidate_topics & low_information_topics:
        violations.append("customer_cannot_answer_non_core_followup")
    if not active_care_risk and any(
        prior_topic_questions.get(topic, 0) >= 1 for topic in candidate_topics
    ):
        violations.append("repeated_non_core_topic_followup")
    if any(
        _questions_are_near_duplicates(question_text, prior)
        for prior in prior_question_texts
    ):
        violations.append("repeated_customer_question")
    return list(dict.fromkeys(violations))


def _contains_specific_brand_service_claim(text: str) -> bool:
    return any(marker in text for marker in _TUTORIAL_DELIVERY_MARKERS) and any(
        marker in text for marker in _ONE_TO_ONE_DELIVERY_MARKERS
    )


def _is_video_access_context(reply_text: str, customer_text: str) -> bool:
    combined = f"{customer_text}\n{reply_text}"
    return "视频" in combined and any(
        marker in combined
        for marker in ("看不了", "打不开", "无法播放", "权限", "开通")
    )


def _sales_flow_rewrite_instruction(violations: list[str]) -> str:
    focused_instructions: list[str] = []
    if "repeats_recent_assistant_content" in violations:
        focused_instructions.append(
            "当前候选回复复述了上一轮已经真实发送的内容。不要再次解释同一病因或重复已经讲过的服务概述；"
            "先承接客户刚提供的新信息，再推进尚未讲清的价值。若客户刚确认一直自己摸索，"
            "应说明萧岚苑与部分卖完后缺少持续承接的商家有何不同，并具体讲清单品养护教程和师傅一对一指导如何帮助他。"
        )
    if "premature_product_card_without_customer_interest" in violations:
        focused_instructions.append(
            "客户目前只暴露了养护痛点或服务缺口，还没有表达想进一步了解、愿意试试、询问内容价格、要链接或下单等清晰正向意向。"
            "不要调用 product.send_card，也不要在 final_response 中放商品卡片。先完成服务价值塑造并自然邀请客户进一步了解；"
            "本轮商品查询只用于辅助判断，不向客户发送；等客户形成 interest 或 direct 信号后重新核实并发卡试成交。"
        )
    if focused_instructions:
        return (
            "".join(focused_instructions)
            + "请基于完整上下文重新做本轮商业判断和客户可见回复，不要只改写个别句子。"
            f"本次偏离原因：{', '.join(violations)}"
        )
    return (
        "当前方案仍在细化或等待同一个非核心技术问题，偏离了推进成交的目标。"
        "放弃这个细节，不再追问、索图、让客户检查，也不要把等待该细节写进 next_action。"
        "保留对客户当前问题的专业回答；然后用已有事实推进更高价值动作："
        "若已足以说明匹配理由，就查询真实商品或服务并主动推荐；若仍不足，"
        "只了解盆数与主要品种、明确目标或痛点、经验与失败史、持续指导缺口、"
        "选择偏好中最接近推荐就绪的一项。请重新做完整商业判断，而不只是改写问句。"
        f"本次停滞原因：{', '.join(violations)}"
    )


def _repeats_recent_assistant_content(
    visible_text: str,
    context: AgentExecutionContext,
) -> bool:
    recent = context.workspace.get("recent_turns")
    recent = recent if isinstance(recent, list) else []
    prior_chunks: list[str] = []
    for item in recent[-8:]:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").lower() not in {
            "assistant",
            "agent",
            "sales_agent",
        }:
            continue
        prior_chunks.extend(_repeatable_text_chunks(str(item.get("content") or "")))
    if not prior_chunks:
        return False
    current_chunks = _repeatable_text_chunks(visible_text)
    return any(
        current == prior
        or (len(current) >= 36 and current in prior)
        or (len(prior) >= 36 and prior in current)
        for current in current_chunks
        for prior in prior_chunks
    )


def _repeatable_text_chunks(text: str) -> list[str]:
    chunks = re.split(r"\n\s*\n+", str(text or ""))
    normalized = [_normalize_repeat_text(chunk) for chunk in chunks]
    return [chunk for chunk in normalized if len(chunk) >= 24]


def _normalize_repeat_text(text: str) -> str:
    return re.sub(
        r"[\s\"'“”‘’《》〈〉，。！？；：、,.!?;:（）()【】\[\]]+",
        "",
        str(text or "").casefold(),
    )


def _hard_rewrite_instruction(violations: list[str]) -> str:
    if any(
        violation in violations
        for violation in (
            "unverified_video_access_notification",
            "incorrect_video_access_wording",
        )
    ):
        return (
            "视频权限处理的客户口径只使用‘已经联系同事处理了’，"
            "不要说提交处理、提交申请或提交核对。不能在没有真实工具结果时声称已经联系同事。"
            "若工作区已有‘抖音已购’验证标签，先调用 video_access.request；"
            "若还没有，只询问是否在抖音购买并请客户发送能看到店铺与订单状态的截图。"
            "不要调用 human.handoff，保持 AI 继续回复，也不要声称权限已经开通。"
            f"本次硬违规：{', '.join(violations)}"
        )
    if "unverified_brand_service_claim" in violations:
        return (
            "具体的单品养护教程和师傅一对一指导属于需要核实的服务事实。"
            "先调用 brand.service_facts；工具返回 found 后，再基于结果完整保留服务价值说明。"
            "不要为了修复事实边界而删掉塑品步骤，也不要输出内部说明。"
            f"本次硬违规：{', '.join(violations)}"
        )
    return (
        "客户可见回复包含不能发送的事实或权限问题。只改写这些问题，"
        "保留其余已核实且有用的内容，不要输出内部说明，也不要重复调用已经成功的工具。"
        f"本次硬违规：{', '.join(violations)}"
    )


def _technical_topics(text: str) -> set[str]:
    normalized = re.sub(r"\s+", "", str(text or ""))
    return {
        topic
        for topic, markers in _TECHNICAL_TOPIC_MARKERS.items()
        if any(marker in normalized for marker in markers)
    }


def _is_low_information_answer(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    if any(marker in normalized for marker in _LOW_INFORMATION_MARKERS):
        return True
    if _customer_question_count(normalized) == 0 and any(
        marker in normalized for marker in _UNCERTAIN_ANSWER_MARKERS
    ):
        return True
    return normalized.endswith("吧") and len(normalized) <= 20


def _questions_are_near_duplicates(candidate: str, previous: str) -> bool:
    left = _question_fingerprint(candidate)
    right = _question_fingerprint(previous)
    if min(len(left), len(right)) < 6:
        return False
    if left in right or right in left:
        return True
    left_pairs = {left[index : index + 2] for index in range(len(left) - 1)}
    right_pairs = {right[index : index + 2] for index in range(len(right) - 1)}
    union = left_pairs | right_pairs
    return bool(union) and len(left_pairs & right_pairs) / len(union) >= 0.45


def _question_fingerprint(text: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or ""))
    for filler in (
        "您",
        "请问",
        "方便",
        "麻烦",
        "现在",
        "之前",
        "一下",
        "可以",
        "能不能",
        "还是",
        "看看",
        "回忆",
    ):
        normalized = normalized.replace(filler, "")
    return normalized


def _customer_question_count(text: str) -> int:
    return text.count("？") + text.count("?")


def _is_opening_profile_question(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text)
    has_quantity = bool(re.search(r"(?:多少|几|大概|现在).{0,8}盆|盆.{0,6}(?:多少|几)", normalized))
    has_variety = "品种" in normalized or bool(
        re.search(r"主要.{0,6}(?:什么|哪类|哪种).{0,4}兰", normalized)
    )
    punctuation_count = normalized.count("？") + normalized.count("?")
    return has_quantity and has_variety and punctuation_count == 1


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


def _has_tool_status(
    context: AgentExecutionContext,
    name: str,
    *statuses: str,
) -> bool:
    return any(
        fact.get("tool") == name and fact.get("status") in statuses
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
            "例如更贴合的养护建议或资料，再用一个自然问句关联询问客户家里当前大概养了多少盆、主要是什么品种。"
            "不要问客户要不要买、看花、选花、预算或价格，不要假设他有购买意向。固定图片会由发送网关插在两条文字之间，"
            "你不要调用工具、安排卡片或资料，也不要提到系统事件。盆数和主要品种必须问到，但措辞可以自然变化，不使用编号或调查表。"
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
