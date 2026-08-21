from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.core.logger import log_event
from app.core.trace_context import reset_trace_id, set_trace_id
from app.domains.conversations.schemas.chat import ChatRequest
from app.domains.conversations.services.channel_service import normalize_chat_request
from app.domains.conversations.services.chat_log_service import record_chat_log
from app.domains.conversations.services.conversation_service import (
    AI_WAITING,
    conversation_blocks_ai,
    record_ai_turn,
    record_customer_message,
    recover_automatic_handoff,
)
from app.domains.conversations.services.state_service import (
    get_user_state,
    update_user_state,
)
from app.domains.customers.services.memory_rollout_service import (
    prepare_memory_context_for_request,
)
from app.domains.customers.services.user_profile_service import (
    append_conversation_memory,
    get_profile_bundle,
)
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.decisioning.services.agent_prompt import (
    FULL_CONVERSATION_CHAR_BUDGET,
)
from app.domains.decisioning.services.agent_runtime import run_sales_agent
from app.domains.decisioning.services.customer_reply_formatter import (
    plain_customer_text,
    split_customer_messages,
)
from app.domains.sales.services.service_material_touch_service import (
    get_agent_relationship_state,
    record_agent_relationship_state,
)
from app.shared.schemas.common import AppError, ErrorCode


async def handle_chat(request: ChatRequest) -> dict:
    """Run the single autonomous Sales Agent path.

    Intent classification, sales-stage routing, ReplyPlan, templates and shadow
    decision graphs are deliberately absent from this runtime entrypoint.
    """
    started = time.perf_counter()
    stage_latencies: dict[str, int] = {}
    log_payload: dict[str, Any] = {}
    message = None
    intent = None
    reply = None
    trace_token = None
    is_evaluation = False

    try:
        stage_started = time.perf_counter()
        message = await normalize_chat_request(request)
        trace_token = set_trace_id(message.trace_id)
        is_evaluation = bool(message.metadata.get("evaluation_id"))
        stage_latencies["normalize_ms"] = _elapsed_ms(stage_started)

        if not str(message.message or "").strip() and not message.metadata.get(
            "system_event"
        ):
            raise AppError(ErrorCode.MESSAGE_EMPTY)

        if _is_technical_noise(message):
            intent = _agent_intent(message, None, ignored=True)
            reply = FinalReply(
                answer="",
                reply_type="ignored_technical_event",
                route="agent",
                metadata={"ignored": True},
            )
            return _to_chat_data(message.session_id, message.trace_id, intent, reply)

        if not is_evaluation:
            await recover_automatic_handoff(
                channel=message.channel,
                user_id=message.user_id,
                session_id=message.session_id,
            )
        if not is_evaluation and conversation_blocks_ai(
            channel=message.channel,
            user_id=message.user_id,
            session_id=message.session_id,
        ):
            if not _is_gateway_managed_eyun(message) and not message.metadata.get(
                "skip_customer_record"
            ):
                await record_customer_message(
                    channel=message.channel,
                    user_id=message.user_id,
                    session_id=message.session_id,
                    content=message.message,
                    message_id=message.message_id,
                    tenant_id=message.tenant_id,
                    status=AI_WAITING,
                    metadata={**message.metadata, "ai_blocked": True},
                )
            intent = IntentResult(
                route="human",
                primary_intent="human_handoff_active",
                primary_domain="conversation",
                primary_goal="request_human",
                scope="in_scope",
                classifier_source="state_guard",
                confidence=1.0,
                need_human=True,
                reason="human_handoff_locked",
            )
            reply = FinalReply(
                answer="",
                reply_type="human",
                route="human",
                need_human=True,
                next_action="human_handoff",
                metadata={"handoff_locked": True},
            )
            result = _to_chat_data(
                message.session_id,
                message.trace_id,
                intent,
                reply,
            )
            log_payload = _success_log_payload(message, intent, reply)
            return result

        stage_started = time.perf_counter()
        user_state = await get_user_state(message.user_id, message.session_id)
        user_state.metadata.pop("memory_v2_context", None)
        user_state.metadata.pop("memory_v2_trace", None)
        _apply_evaluation_context(message, user_state)
        if not is_evaluation:
            memory_context, memory_trace = await prepare_memory_context_for_request(
                message
            )
            user_state.metadata["memory_v2_trace"] = memory_trace
            if memory_context is not None:
                user_state.metadata["memory_v2_context"] = memory_context.model_dump(
                    mode="json"
                )
            stage_latencies["memory_v2_ms"] = int(
                memory_trace.get("latency_ms") or 0
            )
        profile_bundle = await get_profile_bundle(
            message.user_id,
            conversation_char_budget=FULL_CONVERSATION_CHAR_BUDGET,
            conversation_session_id=message.session_id,
        )
        workspace = _customer_workspace(
            message=message,
            user_state=user_state,
            profile_bundle=profile_bundle,
        )
        user_state.metadata["profile"] = workspace.get("profile", {})
        user_state.metadata["recent_turns"] = workspace.get("recent_turns", [])
        stage_latencies["workspace_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        reply = await run_sales_agent(
            message=message,
            user_state=user_state,
            workspace=workspace,
        )
        stage_latencies["agent_ms"] = _elapsed_ms(stage_started)
        agent_metadata = reply.metadata.get("agent_runtime")
        agent_metadata = agent_metadata if isinstance(agent_metadata, dict) else {}
        intent = _agent_intent(message, agent_metadata, need_human=reply.need_human)

        stage_started = time.perf_counter()
        await update_user_state(
            message.user_id,
            message.session_id,
            intent,
            reply,
        )
        record_agent_relationship_state(
            customer_id=message.user_id,
            tenant_id=message.tenant_id,
            customer_signal=str(agent_metadata.get("customer_signal") or "none"),
            commercial_judgment=str(
                agent_metadata.get("commercial_judgment") or ""
            ),
            relationship_purpose=str(
                agent_metadata.get("relationship_purpose") or ""
            ),
            source_trace_id=message.trace_id,
        )
        if (
            not is_evaluation
            and not message.metadata.get("skip_conversation_memory")
            and not message.metadata.get("system_event")
        ):
            await append_conversation_memory(
                user_id=message.user_id,
                tenant_id=message.tenant_id,
                session_id=message.session_id,
                role="user",
                content=message.message,
                intent="autonomous_sales_turn",
                route=reply.route,
                trace_id=message.trace_id,
                channel=message.channel,
                owner_external_id=_memory_owner_external_id(message.metadata),
                source_id=message.message_id or message.trace_id,
            )
            if reply.answer:
                await append_conversation_memory(
                    user_id=message.user_id,
                    tenant_id=message.tenant_id,
                    session_id=message.session_id,
                    role="assistant",
                    content=reply.answer,
                    intent="autonomous_sales_turn",
                    route=reply.route,
                    trace_id=message.trace_id,
                    channel=message.channel,
                    owner_external_id=_memory_owner_external_id(message.metadata),
                    source_id=message.trace_id,
                )
        stage_latencies["state_ms"] = _elapsed_ms(stage_started)

        result = _to_chat_data(
            message.session_id,
            message.trace_id,
            intent,
            reply,
        )
        await _record_workbench_turn(
            message=message,
            result=result,
            is_evaluation=is_evaluation,
        )
        log_payload = _success_log_payload(message, intent, reply)
        log_event(
            {
                "trace_id": message.trace_id,
                "channel": message.channel,
                "user_id": message.user_id,
                "session_id": message.session_id,
                "kb_id": message.kb_id,
                "route": reply.route,
                "intent": "autonomous_sales_turn",
                "latency_ms": _elapsed_ms(started),
                "status": "success",
            }
        )
        return result
    except AppError as exc:
        log_payload = _failed_log_payload(
            request=request,
            message=message,
            intent=intent,
            reply=reply,
            error_code=int(exc.code),
            error_message=exc.message,
        )
        raise
    except Exception as exc:
        log_payload = _failed_log_payload(
            request=request,
            message=message,
            intent=intent,
            reply=reply,
            error_code=int(ErrorCode.INTERNAL_ERROR),
            error_message=str(exc),
        )
        raise
    finally:
        if log_payload:
            log_payload["latency_ms"] = _elapsed_ms(started)
            log_payload["stage_latencies"] = stage_latencies
            await record_chat_log(log_payload)
        if trace_token is not None:
            reset_trace_id(trace_token)


def _customer_workspace(*, message, user_state, profile_bundle: dict) -> dict[str, Any]:
    profile = profile_bundle.get("profile") if isinstance(profile_bundle, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    # Historical sales-stage fields are intentionally excluded. The Agent sees
    # customer facts and relationship evidence instead of a workflow position.
    profile_view = {
        key: profile.get(key)
        for key in (
            "basic_info",
            "customer_tags",
            "product_interests",
            "preference_summary",
            "pain_points",
            "ai_summary",
            "is_human_handoff",
            "human_handoff_status",
            "last_active_at",
            "friend_added_at",
        )
        if profile.get(key) not in (None, "", [], {})
    }
    recent = profile_bundle.get("recent_memories") if isinstance(profile_bundle, dict) else []
    recent = recent if isinstance(recent, list) else []
    evaluation_recent = user_state.metadata.get("recent_turns")
    if _is_evaluation_request(message) and isinstance(evaluation_recent, list):
        recent = evaluation_recent
        evaluation_profile = user_state.metadata.get("profile")
        if isinstance(evaluation_profile, dict):
            profile_view = {
                **profile_view,
                **{
                    key: value
                    for key, value in evaluation_profile.items()
                    if value not in (None, "", [], {})
                },
            }
    basic_info = profile_view.get("basic_info")
    if isinstance(basic_info, dict):
        excluded_name_fields = {
            "nickname",
            "remark_name",
            "display_name",
            "customer_name",
        }
        basic_info = {
            key: value
            for key, value in basic_info.items()
            if key not in excluded_name_fields
        }
        if basic_info:
            profile_view["basic_info"] = basic_info
        else:
            profile_view.pop("basic_info", None)
    memory_context = user_state.metadata.get("memory_v2_context")
    relationship = get_agent_relationship_state(message.user_id)
    workspace = {
        "profile": profile_view,
        "recent_turns": [
            {
                "role": item.get("role"),
                "content": str(item.get("content") or "")[:1200],
                "created_at": item.get("created_at"),
            }
            for item in recent
            if isinstance(item, dict) and item.get("content")
        ],
        "evidence_memory": (
            memory_context if isinstance(memory_context, dict) else {}
        ),
        "relationship_state": relationship,
        "known_business_context": _safe_business_context(message.metadata),
        "unknowns": [
            "动态商品、价格、库存、订单和权益必须在当前轮通过工具核实"
        ],
    }
    return workspace


def _agent_intent(
    message,
    metadata: dict[str, Any] | None,
    *,
    need_human: bool = False,
    ignored: bool = False,
) -> IntentResult:
    metadata = metadata or {}
    return IntentResult(
        route="human" if need_human else "agent",
        primary_intent=(
            "technical_event_ignored" if ignored else "autonomous_sales_turn"
        ),
        primary_domain="sales_relationship",
        primary_goal="advance_relationship",
        scope="in_scope",
        classifier_source="sales_agent",
        confidence=1.0,
        need_human=need_human,
        reason="single_agent_runtime",
        slots={
            "customer_signal": metadata.get("customer_signal", "none"),
            **(
                {"system_event": message.metadata.get("system_event")}
                if message.metadata.get("system_event")
                else {}
            ),
        },
    )


def _to_chat_data(
    session_id: str,
    trace_id: str,
    intent: IntentResult,
    reply: FinalReply,
) -> dict[str, Any]:
    public_metadata = _public_reply_metadata(reply.metadata)
    return {
        "answer": plain_customer_text(reply.answer),
        "session_id": session_id,
        "sources": reply.sources,
        "usage": reply.usage,
        "answer_segments": _answer_segments(reply.answer, reply.answer_segments),
        "outbound_messages": [
            message.model_dump() for message in reply.outbound_messages
        ],
        "reply_type": reply.reply_type,
        "route": reply.route,
        "intent": intent.model_dump(),
        "template": {},
        "need_human": reply.need_human,
        "next_action": reply.next_action,
        "trace_id": trace_id,
        "metadata": public_metadata,
        "handoff": reply.metadata.get("handoff"),
    }


async def _record_workbench_turn(
    *, message, result: dict[str, Any], is_evaluation: bool
) -> None:
    if not is_evaluation and _is_gateway_managed_eyun(message):
        return
    if not is_evaluation:
        await record_ai_turn(message=message, result=result)
        return
    evaluation_id = str(message.metadata.get("evaluation_id") or "").strip()
    workbench_message = message.model_copy(
        update={
            "channel": "wechat",
            "metadata": {
                **message.metadata,
                "display_name": f"测试案例｜{evaluation_id}",
                "evaluation_id": evaluation_id,
                "is_evaluation": True,
                "skip_customer_record": False,
                "suppress_handoff_notification": True,
            },
        }
    )
    await record_ai_turn(message=workbench_message, result=result)


def _public_reply_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    # Keep customer/API output free of reasoning, tool schemas and internal
    # customer workspaces. Delivery metadata may still be added by the gateway.
    return {
        key: value
        for key, value in metadata.items()
        if key not in {"agent_runtime", "business_context", "tool_state"}
    }


def _success_log_payload(message, intent: IntentResult, reply: FinalReply) -> dict:
    agent = reply.metadata.get("agent_runtime")
    agent = agent if isinstance(agent, dict) else {}
    return {
        **_message_log_base(message),
        "answer": reply.answer,
        "route": reply.route,
        "reply_type": reply.reply_type,
        "primary_intent": intent.primary_intent,
        "secondary_intents": [],
        "sales_stage": None,
        "confidence": intent.confidence,
        "template_id": None,
        "next_action": reply.next_action,
        "sources": reply.sources,
        "need_human": reply.need_human,
        "policy_reason": "single_agent_runtime",
        "intent_reason": intent.reason,
        "usage": reply.usage,
        "status": "success",
        "error_code": None,
        "error_message": None,
        "metadata": {
            "agent_runtime": agent,
            **({"handoff": reply.metadata.get("handoff")} if reply.metadata.get("handoff") else {}),
        },
        "created_at": _now_iso(),
    }


def _failed_log_payload(
    *,
    request: ChatRequest,
    message,
    intent: IntentResult | None,
    reply: FinalReply | None,
    error_code: int,
    error_message: str,
) -> dict:
    payload = _message_log_base(message) if message is not None else {
        "trace_id": None,
        "channel": request.channel,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "kb_id": request.kb_id,
        "message": request.message,
    }
    payload.update(
        {
            "route": reply.route if reply is not None else None,
            "primary_intent": intent.primary_intent if intent is not None else None,
            "answer": reply.answer if reply is not None else "",
            "status": "failed",
            "error_code": error_code,
            "error_message": error_message,
            "created_at": _now_iso(),
        }
    )
    return payload


def _message_log_base(message) -> dict[str, Any]:
    return {
        "trace_id": message.trace_id,
        "channel": message.channel,
        "user_id": message.user_id,
        "session_id": message.session_id,
        "kb_id": message.kb_id,
        "message": message.message,
        "message_id": message.message_id,
        "tenant_id": message.tenant_id,
    }


def _safe_business_context(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata.get(key)
        for key in (
            "business_snapshot",
            "tool_state",
            "media",
            "vision_description",
            "attachment_error",
        )
        if metadata.get(key) not in (None, "", [], {})
    }


def _is_evaluation_request(message) -> bool:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    return bool(str(metadata.get("evaluation_id") or "").strip())


def _apply_evaluation_context(message, user_state) -> None:
    """Load offline case context without restoring any legacy routing fields."""
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    context = metadata.get("evaluation_context")
    if not isinstance(context, dict):
        return
    customer_context = str(context.get("customer_context") or "").strip()
    if customer_context:
        profile = user_state.metadata.get("profile")
        profile = dict(profile) if isinstance(profile, dict) else {}
        profile["ai_summary"] = customer_context
        user_state.metadata["profile"] = profile
    recent_turns = context.get("recent_turns")
    if isinstance(recent_turns, list):
        user_state.metadata["recent_turns"] = [
            {
                "role": str(item.get("role") or ""),
                "content": str(item.get("content") or ""),
            }
            for item in recent_turns[-10:]
            if isinstance(item, dict) and item.get("content")
        ]


def _is_technical_noise(message) -> bool:
    event_type = str(message.metadata.get("event_type") or "").strip().lower()
    return event_type in {
        "heartbeat",
        "keepalive",
        "duplicate_callback",
        "self_echo",
        "login_event",
        "account_status",
    }


def _is_gateway_managed_eyun(message) -> bool:
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    return (
        metadata.get("provider") == "eyun"
        and metadata.get("provider_delivery_mode") != "simulated"
    )


def _memory_owner_external_id(metadata: dict[str, Any]) -> str:
    for key in ("w_id", "owner_external_id", "wechat_to_user"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


def _answer_segments(answer: str, preferred: list[str] | None = None) -> list[str]:
    structured = [part.strip() for part in preferred or [] if part.strip()]
    if structured:
        messages = []
        for part in structured:
            normalized = plain_customer_text(part).replace("\n", " ").strip()
            if normalized:
                messages.append(normalized)
        return messages
    return split_customer_messages(answer)


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
