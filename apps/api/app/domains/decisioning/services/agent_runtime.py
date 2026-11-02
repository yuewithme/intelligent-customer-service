from __future__ import annotations

import json
import logging
import time
from typing import Any

from pydantic import ValidationError

from app.core.config import get_settings
from app.domains.decisioning.schemas.agent import AgentFinalResponse, AgentTurnDecision, AgentVisibleMessage
from app.domains.conversations.services import service_handoff_service as service_handoff
from app.domains.decisioning.schemas.execution import AgentExecutionContext
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.decisioning.services import agent_reply_policy as reply_policy
from app.domains.decisioning.services.agent_prompt import (
    HARNESS_VERSION,
    build_system_prompt,
    build_tool_result_payload,
    build_turn_payload,
)
from app.domains.decisioning.services.customer_reply_formatter import (
    split_customer_messages,
)
from app.domains.decisioning.services.agent_tools import execute_agent_tool
from app.domains.decisioning.services.service_sop import build_service_collection_context, build_service_context
from app.domains.handoff.services.handoff_notification_service import (
    get_sop_node,
    is_sop_node_handoff_enabled,
)
from app.integrations.ai.services.llm_service import generate_messages_json
from app.domains.orchestration.services.sop_flow_service import (
    get_saved_flow, dialogue_choices, execution_prompt, load_cursor, save_cursor,
    execution_version,
)
from app.domains.sales.services.sop_policy_service import preference_tag_groups
from app.domains.handoff.schemas.handoff_notification import SOP_NODE_IDS


logger = logging.getLogger("wechat_rag_bot.sales_agent")
MAX_TOOL_ROUNDS = 5
MAX_TOOL_CALLS = 10
MAX_SCHEMA_REPAIRS = 1
MAX_HARD_REWRITES = 1
MAX_QUALITY_REWRITES = 1
MAX_TRAJECTORY_REWRITES = 3
MAX_AGENT_MODEL_CALLS = (
    MAX_TOOL_ROUNDS
    + MAX_SCHEMA_REPAIRS
    + MAX_HARD_REWRITES
    + MAX_QUALITY_REWRITES
    + MAX_TRAJECTORY_REWRITES
    + 2
)


async def run_sales_agent(
    *,
    message,
    user_state,
    workspace: dict[str, Any],
) -> FinalReply:
    sop_scope = reply_policy.sop_scope_for_message(message)
    sop_scopes = message.metadata.get("sop_scopes", [sop_scope])
    allowed_scopes = set(sop_scopes) | {"general"}
    care_only = bool(message.metadata.get("care_only_after_preference_handoff"))
    flows = {} if care_only else {scope: flow for scope in sop_scopes if (flow := get_saved_flow(scope)) is not None}
    message.metadata["sop_flow_versions"] = {scope: execution_version(flow) for scope, flow in flows.items()}
    cursors = {scope: load_cursor(message, scope, flow) for scope, flow in flows.items()}
    if "service" in sop_scopes:
        workspace["service_collection"] = build_service_collection_context(workspace.get("profile") or {})
        workspace["service_sop"] = build_service_context(workspace.get("profile") or {})
        if not message.metadata.get("evaluation_id"):
            from app.domains.conversations.services.conversation_service import get_service_delivery_state
            workspace["service_sop"].update(get_service_delivery_state(message.user_id))
    context = AgentExecutionContext(
        message=message,
        user_state=user_state,
        workspace=workspace,
    )
    offer_state = service_handoff.load_service_handoff(message)
    workspace["service_handoff"] = offer_state
    confirmed = service_handoff.confirmed_handoff_reason(message, offer_state)
    if confirmed:
        return await _confirmed_service_handoff(context, confirmed, {}, [])
    if offer_state.get("awaiting") and not message.metadata.get("system_event"):
        # Consume the pending question even if generation falls back or is interrupted.
        service_handoff.save_service_handoff(message, {**offer_state, "awaiting": False})
    event_context = _event_context(message)
    conversation: list[dict[str, str]] = [
        {"role": "system", "content": build_system_prompt(sop_scope=sop_scope, sop_scopes=sop_scopes)},
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
    if care_only:
        conversation.append({"role": "system", "content": (
            "此前产品喜好已在上一轮内部转人工；本轮客户提出新的养护诉求，现已交回 AI。"
            "只处理本轮养护问题，结合最近人工建议和客户新信息，不重记历史喜好、不继续销售或例行挖需，"
            "不发送转接通知、会员介绍或收尾主管邀请，不因旧喜好或销售节点再次转人工。"
            "当前确有新的安全风险或授权事项时仍可转人工。"
        )})
    tool_results: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    total_tool_calls = 0
    usage: dict[str, int] = {}
    latest_decision: AgentTurnDecision | None = None
    attempt_trace: list[dict[str, Any]] = []
    schema_repairs = 0
    hard_rewrites = 0
    quality_rewrites = 0
    trajectory_rewrites = 0
    tool_rounds = 0
    tool_budget_exhausted = False

    for attempt_number in range(1, MAX_AGENT_MODEL_CALLS + 1):
        for scope, flow in flows.items():
            conversation.append({"role": "system", "content": execution_prompt(scope, flow, cursors[scope])})
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
            if decision.sop_node.partition(".")[0] not in allowed_scopes:
                raise ValueError("sop_node_scope_mismatch")
            scope, _, step_id = decision.sop_node.partition(".")
            flow = flows.get(scope)
            if flow:
                if step_id not in dialogue_choices(flow, cursors[scope]):
                    raise ValueError("node_not_reachable_in_saved_flow")
                requires_interest = next(node.require_product_interest for node in flow.steps if node.step_id == step_id)
            else:
                if decision.sop_node not in SOP_NODE_IDS | {"general.reply"}:
                    raise ValueError("unknown_sop_node")
                requires_interest = scope == "seeding"
            if requires_interest and decision.purchase_signal == "none":
                raise ValueError("seeding_requires_product_interest")
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
                        f"sop_node 必须使用 {sorted(allowed_scopes)} 范围内的节点；种草节点必须有明确产品了解意向（interest/direct）。"
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
        if care_only:
            decision.purchase_signal = "none"
        confirmed = service_handoff.confirmed_handoff_reason(message, offer_state, decision.service_handoff)
        if confirmed:
            return await _confirmed_service_handoff(context, confirmed, usage, attempt_trace)
        preference_handoff_requested = any(
            call.name == "human.handoff" and call.arguments.get("reason") == "product_preference_received"
            for call in decision.tool_calls
        ) or bool(decision.final_response and decision.final_response.need_human
            and decision.final_response.handoff_reason == "product_preference_received")
        ask_before_handoff = (
            (decision.service_handoff.invitation_reason != "none"
             or decision.service_handoff.invitation_response != "none")
            and not reply_policy.required_handoff_reason(str(message.message or ""))
            and decision.purchase_signal == "none"
            and not preference_handoff_requested
        )
        if ask_before_handoff:
            requested_handoff = any(call.name == "human.handoff" for call in decision.tool_calls)
            decision.tool_calls = [call for call in decision.tool_calls if call.name != "human.handoff"]
            if (requested_handoff and not decision.tool_calls) or (decision.final_response and decision.final_response.need_human):
                decision.final_response = AgentFinalResponse(messages=[AgentVisibleMessage(type="text", content="我会继续帮您处理这个问题。")])
        if scope in flows:
            cursors[scope] = step_id
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
        if not care_only and "service" in sop_scopes and decision.purchase_signal in {"interest", "direct"}:
            decision.sop_node = "service.repurchase_discovery"
            diagnostic["outcome"] = "service_purchase_handoff"
            return await _sop_node_handoff_reply(decision=decision, context=context, sop_scope="service",
                usage=usage, tool_results=tool_results, attempt_trace=attempt_trace)
        explicit_handoff = any(
            call.name == "human.handoff" for call in decision.tool_calls
        ) or bool(decision.final_response and decision.final_response.need_human)
        decision_scope = decision.sop_node.partition(".")[0]
        if decision_scope == "seeding" and is_sop_node_handoff_enabled("seeding", "seeding.product_interest"):
            decision.sop_node = "seeding.product_interest"
        if (
            not explicit_handoff
            and not care_only
            and is_sop_node_handoff_enabled(decision_scope, decision.sop_node)
        ):
            diagnostic["outcome"] = "sop_node_handoff"
            return await _sop_node_handoff_reply(
                decision=decision,
                context=context,
                sop_scope=decision_scope,
                usage=usage,
                tool_results=tool_results,
                attempt_trace=attempt_trace,
            )
        if decision.tool_calls:
            tool_trajectory_violations = reply_policy.tool_sales_trajectory_violations(decision)
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
                        "content": reply_policy.sales_flow_rewrite_instruction(
                            tool_trajectory_violations
                        ),
                    }
                )
                continue
            if (
                sop_scope == "first_order"
                and "first_order" not in flows
                and str(event_context.get("system_event") or "") == "first_contact"
            ):
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
                preference_recorded = result.status == "recorded" and (
                    (call.name == "service.record" and any(
                        isinstance(item, dict) and item.get("field") == "preference"
                        for item in call.arguments.get("facts", [])
                    ))
                    or (call.name == "customer.tag" and any(preference_tag_groups([str(result.data.get("tag") or "")])))
                )
                if preference_recorded:
                    handoff = await execute_agent_tool(
                        call_id="system_product_preference_handoff", name="human.handoff",
                        arguments={"reason": "product_preference_received", "summary": str(message.message or "")[:1000]},
                        context=context,
                    )
                    round_results.append(handoff.model_dump(mode="json"))
                if (context.handoff or {}).get("reason") == "product_preference_received":
                    break
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
            if (context.handoff or {}).get("reason") == "product_preference_received":
                diagnostic["outcome"] = "product_preference_handoff"
                decision.tool_calls = []
                decision.final_response = AgentFinalResponse(
                    messages=[],
                    need_human=True, handoff_reason="product_preference_received",
                )
                return await _finalize_reply(decision=decision, context=context, usage=usage,
                    tool_results=tool_results, quality_flags=[], attempt_trace=attempt_trace)
            conversation.append(
                {
                    "role": "user",
                    "content": build_tool_result_payload(round_results),
                }
            )
            continue

        if decision.final_response is None:
            continue
        hard_violations = reply_policy.guard_violations(decision, context)
        quality_flags = reply_policy.quality_flags(decision, context)
        trajectory_violations = reply_policy.sales_trajectory_violations(decision, context)
        diagnostic["hard_violations"] = hard_violations
        diagnostic["quality_flags"] = quality_flags
        diagnostic["trajectory_violations"] = trajectory_violations
        if hard_violations and hard_rewrites < MAX_HARD_REWRITES:
            diagnostic["outcome"] = "hard_rewrite_requested"
            hard_rewrites += 1
            conversation.append(
                {
                    "role": "system",
                    "content": reply_policy.hard_rewrite_instruction(hard_violations),
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
                    "content": reply_policy.sales_flow_rewrite_instruction(
                        trajectory_violations
                    ),
                }
            )
            continue
        rewrite_flags = [flag for flag in quality_flags if flag in {
            "too_many_text_messages", "reply_may_be_overexplained",
            "service_introduction_overlaps_handoff_invitation",
        }]
        if rewrite_flags and quality_rewrites < MAX_QUALITY_REWRITES:
            diagnostic["outcome"] = "quality_rewrite_requested"
            quality_rewrites += 1
            conversation.append(
                {
                    "role": "system",
                    "content": reply_policy.quality_rewrite_instruction(rewrite_flags),
                }
            )
            continue
        if trajectory_violations:
            quality_flags = list(
                dict.fromkeys([*quality_flags, *trajectory_violations])
            )
        diagnostic["outcome"] = "accepted"
        selected_scope, _, selected_step = decision.sop_node.partition(".")
        if selected_scope in flows:
            save_cursor(message, selected_scope, flows[selected_scope], selected_step)
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
    suppressed_invitation = False
    used_refs: set[str] = set()
    product_refs = {ref for ref, fact in context.tool_facts.items() if fact.get("tool") == "product.send_card"}
    if product_refs:
        from app.integrations.youzan.services.youzan_product_sync_service import get_product_delivery_settings
        if not get_product_delivery_settings()["ai_send_enabled"]:
            for ref in product_refs:
                context.prepared.pop(ref, None)
    for item in final.messages:
        if item.type == "text":
            content = str(item.content or "").strip()
            for invitation_text in (service_handoff.CLOSING_INVITATION, service_handoff.DISSATISFIED_INVITATION):
                suppressed_invitation = suppressed_invitation or invitation_text in content
                content = content.replace(invitation_text, "").strip()
            if content:
                for message in split_customer_messages(content, split_long=False):
                    visible_texts.append(message)
                    outbound.append(OutboundMessage(type="text", content=message, split=False))
            continue
        ref = str(item.ref or "").strip()
        prepared = context.prepared.get(ref)
        if prepared and ref not in used_refs:
            used_refs.add(ref)
            outbound.extend(prepared)
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
    if reply_policy.is_first_order_opening(context):
        outbound = _insert_opening_image(outbound)
    if (context.handoff or {}).get("reason") == "product_preference_received":
        outbound = []
    offer_state = dict(context.workspace.get("service_handoff") or {})
    if need_human:
        offer_state["awaiting"] = False
    elif not context.message.metadata.get("care_only_after_preference_handoff"):
        offer_state, invitation = service_handoff.advance_service_handoff(context.message, offer_state, decision.service_handoff)
        if invitation:
            outbound.append(OutboundMessage(type="text", content=invitation, split=False))
    else:
        offer_state["awaiting"] = False
    outbound, duplicate_suppressed = reply_policy.deduplicate_reply(outbound, context)
    if duplicate_suppressed:
        quality_flags = list(dict.fromkeys([*quality_flags, "duplicate_reply_suppressed"]))
    if not outbound and not need_human and suppressed_invitation and not duplicate_suppressed:
        outbound.append(OutboundMessage(type="text", content="好的，我继续帮您解答。"))
    if not outbound and not need_human and not duplicate_suppressed:
        await execute_agent_tool(
            call_id="system_empty_reply_handoff",
            name="human.handoff",
            arguments={"reason": "empty_agent_reply", "summary": decision.commercial_judgment},
            context=context,
        )
        need_human = True
        offer_state["awaiting"] = False
    service_handoff.save_service_handoff(context.message, offer_state)
    from app.domains.conversations.services.conversation_progress_service import save_issue_progress
    save_issue_progress(context.message, decision.issue_progress)
    visible_texts = [item.content for item in outbound if item.type == "text"]
    answer = "\n\n".join(visible_texts)
    return FinalReply(
        answer=answer,
        answer_segments=visible_texts,
        outbound_messages=outbound,
        reply_type="sales_agent",
        route="human" if need_human else "agent",
        sources=_dedupe_sources(context.sources),
        usage=usage,
        need_human=need_human,
        next_action="human_handoff" if need_human else final.next_action if outbound else None,
        metadata={
            "agent_runtime": {
                "version": HARNESS_VERSION,
                "trace_id": context.message.trace_id,
                "commercial_judgment": decision.commercial_judgment,
                "relationship_purpose": decision.relationship_purpose,
                "sop_scope": decision.sop_node.partition(".")[0],
                "eligible_sop_scopes": context.message.metadata.get("sop_scopes", []),
                "flow_version": context.message.metadata.get("sop_flow_versions", {}).get(decision.sop_node.partition(".")[0]),
                "sop_node": decision.sop_node,
                "customer_signal": decision.customer_signal,
                "purchase_signal": decision.purchase_signal,
                "service_handoff": {"assessment": decision.service_handoff.model_dump(), "state": offer_state},
                "tool_trace": tool_results,
                "hard_violations": [],
                "quality_flags": quality_flags,
                "attempt_trace": attempt_trace,
                "result": (
                    "human_handoff"
                    if need_human and not outbound
                    else "generated_with_handoff" if need_human else "generated"
                ),
            },
            **({"handoff": context.handoff} if context.handoff else {}),
        },
    )


async def _confirmed_service_handoff(
    context: AgentExecutionContext, reason: str, usage: dict[str, int], attempt_trace: list[dict[str, Any]],
) -> FinalReply:
    decision = AgentTurnDecision(
        commercial_judgment="客户明确要求或确认由人工继续处理当前问题",
        relationship_purpose="按客户意愿转接人工并停止自动回复",
        sop_node="general.reply",
        final_response=AgentFinalResponse(
            messages=[AgentVisibleMessage(type="text", content=service_handoff.HANDOFF_CONFIRMATION)],
            need_human=True, handoff_reason=reason,
        ),
    )
    reply = await _finalize_reply(decision=decision, context=context, usage=usage,
        tool_results=[], quality_flags=[], attempt_trace=attempt_trace)
    reply.reply_type = "human"
    return reply


async def _sop_node_handoff_reply(
    *,
    decision: AgentTurnDecision,
    context: AgentExecutionContext,
    sop_scope: str,
    usage: dict[str, int],
    tool_results: list[dict[str, Any]],
    attempt_trace: list[dict[str, Any]],
) -> FinalReply:
    node = get_sop_node(decision.sop_node)
    node_name = str((node or {}).get("name") or decision.sop_node)
    result = await execute_agent_tool(
        call_id="system_sop_node_handoff",
        name="human.handoff",
        arguments={
            "reason": f"SOP 节点已配置转人工：{node_name}",
            "summary": decision.commercial_judgment,
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
                "commercial_judgment": decision.commercial_judgment,
                "relationship_purpose": decision.relationship_purpose,
                "sop_scope": sop_scope,
                "sop_node": decision.sop_node,
                "customer_signal": decision.customer_signal,
                "purchase_signal": decision.purchase_signal,
                "tool_trace": [*tool_results, result.model_dump(mode="json")],
                "attempt_trace": attempt_trace,
                "result": "sop_node_handoff",
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
    required_handoff = reply_policy.required_handoff_reason(str(message.message or ""))
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
                    "sop_scope": reply_policy.sop_scope_for_message(message),
                    "sop_node": (
                        latest_decision.sop_node if latest_decision else None
                    ),
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
    if system_event == "first_contact" and reply_policy.sop_scope_for_message(message) == "first_order" and not get_saved_flow("first_order"):
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
                    "sop_scope": reply_policy.sop_scope_for_message(message),
                    "sop_node": (
                        latest_decision.sop_node if latest_decision else None
                    ),
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
    if system_event == "first_contact" and reply_policy.sop_scope_for_message(message) == "first_order" and not get_saved_flow("first_order"):
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
                "sop_scope": reply_policy.sop_scope_for_message(message),
                "sop_node": (
                    latest_decision.sop_node
                    if latest_decision
                    else "first_order.opening"
                ),
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
            "sop_node": decision.sop_node,
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
            "sop_node": _truncate_log_text(data.get("sop_node"), 128),
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


def _event_context(message) -> dict[str, Any]:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    allowed = {
        key: metadata.get(key)
        for key in (
            "system_event",
            "is_first_contact",
            "message_type",
            "conversation_message_ids",
            "image_count",
            "recognized_image_count",
            "media",
            "vision_description",
            "attachment_error",
            "business_snapshot",
            "tool_state",
        )
        if metadata.get(key) not in (None, "", [], {})
    }
    if allowed.get("system_event") == "first_contact":
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
