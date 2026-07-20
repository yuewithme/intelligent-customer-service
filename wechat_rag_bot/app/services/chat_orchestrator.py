import time

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.schemas.common import AppError, ErrorCode
from app.schemas.intent import IntentResult
from app.schemas.policy import PolicyDecision
from app.schemas.reply import FinalReply
from app.services.channel_service import normalize_chat_request
from app.services.chat_log_service import record_chat_log
from app.services.business_context_service import build_business_context
from app.services.commerce_query_service import build_commerce_context
from app.services.conversation_service import record_ai_turn
from app.services.customer_reply_formatter import (
    coalesce_customer_messages,
    plain_customer_text,
    split_customer_messages,
)
from app.services.intent_example_service import retrieve_intent_examples
from app.services.intent_service import classify_intent
from app.services.policy_service import decide_route
from app.services.policy_engine import decide_policy
from app.services.reply_planner import resolve_reply_plan
from app.services.reply_workflow_graph import execute_reply_plan
from app.services.rule_guard_service import check_rules
from app.services.sales_action_service import apply_sales_action, decide_sales_action
from app.services.sales_stage_knowledge_policy import (
    allowed_knowledge_sources,
    apply_stage_knowledge_policy,
)
from app.services.sales_signal_service import normalize_sales_signals
from app.services.sales_stage_service import decide_sales_stage, normalize_sales_stage
from app.services.shipping_contact_service import extract_shipping_contact
from app.services.state_service import get_user_state, update_user_state
from app.services.tagger_service import build_tag_result
from app.services.user_profile_service import (
    apply_deterministic_profile_update,
    append_conversation_memory,
    get_profile_bundle,
    save_shipping_contact,
)
from app.utils.logger import log_event


async def handle_chat(request: ChatRequest) -> dict:
    started = time.perf_counter()
    candidates: list[dict] = []
    stage_latencies: dict[str, int] = {}
    log_payload: dict = {}
    message = None
    user_state = None
    intent = None
    routed_intent = None
    decision = None
    tag_result = None
    rich_decision = None
    reply = None

    try:
        stage_started = time.perf_counter()
        message = await normalize_chat_request(request)
        is_evaluation = _is_evaluation_request(message)
        stage_latencies["normalize_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        user_state = await get_user_state(message.user_id, message.session_id)
        if is_evaluation:
            _apply_evaluation_context(message, user_state)
        else:
            await _hydrate_user_state_from_profile(message.user_id, user_state)
            shipping_contact = extract_shipping_contact(
                message.message,
                allow_mobile_only=(
                    user_state.metadata.get("commerce_pending") == "order_mobile"
                ),
            )
            if shipping_contact:
                saved_contact = await save_shipping_contact(
                    message.user_id,
                    shipping_contact,
                    tenant_id=message.tenant_id,
                    channel=message.channel,
                )
                _merge_shipping_contact_into_state(user_state, saved_contact)
        stage_latencies["state_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        intent = await check_rules(message, user_state)
        stage_latencies["rule_guard_ms"] = _elapsed_ms(stage_started)
        if intent is None:
            stage_started = time.perf_counter()
            candidates = await retrieve_intent_examples(
                message.message,
                top_k=get_settings().intent_example_top_k,
            )
            stage_latencies["intent_examples_ms"] = _elapsed_ms(stage_started)

            stage_started = time.perf_counter()
            intent = await classify_intent(message, user_state, candidates)
            stage_latencies["intent_ms"] = _elapsed_ms(stage_started)
        else:
            stage_latencies["intent_examples_ms"] = 0
            stage_latencies["intent_ms"] = 0

        stage_started = time.perf_counter()
        decision = await decide_route(intent, user_state, message)
        stage_latencies["policy_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        tag_result = await build_tag_result(
            message=message,
            user_state=user_state,
            intent=intent,
        )
        preliminary_signals = normalize_sales_signals(
            message=message,
            user_state=user_state,
            intent=intent,
            tag_result=tag_result,
        )
        preliminary_decision = decide_sales_stage(
            user_state=user_state,
            intent=intent,
            tag_result=tag_result,
            signal_result=preliminary_signals,
        )
        source_allowlist = allowed_knowledge_sources(
            preliminary_decision.stage,
            intent,
        )
        facts = await build_commerce_context(
            message,
            user_state,
            intent,
            allowed_source_groups=source_allowlist,
        )
        if not facts.available:
            facts = await build_business_context(
                message,
                allowed_source_groups=source_allowlist,
            )
        sales_signals = normalize_sales_signals(
            message=message,
            user_state=user_state,
            intent=intent,
            tag_result=tag_result,
            business_facts=facts,
        )
        normalized_intent = intent.model_copy(
            update={
                "sales_signals": [signal.value for signal in sales_signals.signals],
                "slots": sales_signals.slots,
            }
        )
        sales_stage_decision = decide_sales_stage(
            user_state=user_state,
            intent=normalized_intent,
            tag_result=tag_result,
            signal_result=sales_signals,
            business_facts=facts,
        )
        tag_result = tag_result.model_copy(
            update={
                "stage": sales_stage_decision.stage,
                "entities": sales_signals.slots,
            }
        )
        rich_decision = await decide_policy(tag_result)
        plan = resolve_reply_plan(
            base=decision,
            tagged=rich_decision,
            facts=facts,
        )
        plan = apply_stage_knowledge_policy(
            plan,
            stage=sales_stage_decision.stage,
            intent=normalized_intent,
        )
        stage_latencies["tag_policy_ms"] = _elapsed_ms(stage_started)

        route = plan.action
        routed_intent = normalized_intent.model_copy(
            update={
                "route": route,
                "sales_stage": sales_stage_decision.stage,
                "reason": plan.reason,
                "slots": {
                    **intent.slots,
                    "original_route": plan.original_route or intent.route,
                    "sales_stage_reason": sales_stage_decision.reason,
                },
            }
        )
        sales_action = decide_sales_action(
            user_state=user_state,
            intent=routed_intent,
        )
        user_state.metadata["sales_action"] = sales_action.model_dump()
        user_state.metadata["sales_stage_decision"] = sales_stage_decision.model_dump(
            mode="json"
        )

        stage_started = time.perf_counter()
        reply = await execute_reply_plan(
            plan=plan,
            intent=routed_intent,
            message=message,
            user_state=user_state,
            stage_latencies=stage_latencies,
        )
        reply = apply_sales_action(reply, sales_action)
        stage_latencies["reply_build_ms"] = _elapsed_ms(stage_started)
        reply.metadata["sales_action"] = sales_action.model_dump()
        reply.metadata["sales_stage_decision"] = sales_stage_decision.model_dump(
            mode="json"
        )
        reply.metadata["decision"] = {
            "action": plan.action,
            "reason": plan.reason,
            "original_route": plan.original_route,
            "trace": [step.model_dump() for step in plan.decision_trace],
        }
        if tag_result is not None:
            reply.metadata["tag_result"] = tag_result.model_dump()
        if rich_decision is not None:
            reply.metadata["policy_decision"] = rich_decision.model_dump()
        elif decision is not None:
            reply.metadata["policy_decision"] = decision.model_dump()

        stage_started = time.perf_counter()
        await update_user_state(
            message.user_id,
            message.session_id,
            routed_intent,
            reply,
        )
        if not is_evaluation:
            await append_conversation_memory(
                user_id=message.user_id,
                tenant_id=message.tenant_id,
                session_id=message.session_id,
                role="user",
                content=message.message,
                intent=routed_intent.primary_intent,
                route=reply.route,
                template_id=reply.template_id,
                trace_id=message.trace_id,
            )
            if reply.answer:
                await append_conversation_memory(
                    user_id=message.user_id,
                    tenant_id=message.tenant_id,
                    session_id=message.session_id,
                    role="assistant",
                    content=reply.answer,
                    intent=routed_intent.primary_intent,
                    route=reply.route,
                    template_id=reply.template_id,
                    trace_id=message.trace_id,
                )
            await apply_deterministic_profile_update(message, routed_intent, reply)
        stage_latencies["state_update_ms"] = _elapsed_ms(stage_started)

        result = _to_chat_data(message.session_id, message.trace_id, routed_intent, reply)
        if not is_evaluation:
            await record_ai_turn(message=message, result=result)
        log_payload = _success_log_payload(
            message=message,
            intent=routed_intent,
            decision=decision,
            reply=reply,
        )
        log_event(
            {
                "trace_id": message.trace_id,
                "channel": message.channel,
                "user_id": message.user_id,
                "session_id": message.session_id,
                "kb_id": message.kb_id,
                "route": reply.route,
                "intent": routed_intent.primary_intent,
                "candidate_count": len(candidates),
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "status": "success",
            }
        )
        return result
    except AppError as exc:
        log_payload = _failed_log_payload(
            request=request,
            message=message,
            intent=routed_intent or intent,
            reply=reply,
            error_code=int(exc.code),
            error_message=exc.message,
        )
        raise
    except Exception as exc:
        log_payload = _failed_log_payload(
            request=request,
            message=message,
            intent=routed_intent or intent,
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


def _to_chat_data(
    session_id: str,
    trace_id: str,
    intent: IntentResult,
    reply: FinalReply,
) -> dict:
    template = {}
    if reply.template_id:
        template = {"template_id": reply.template_id}
    public_metadata = _public_reply_metadata(reply.metadata)
    return {
        "answer": plain_customer_text(reply.answer),
        "session_id": session_id,
        "sources": reply.sources,
        "usage": reply.usage,
        "answer_segments": _answer_segments(reply.answer, reply.answer_segments),
        "outbound_messages": [message.model_dump() for message in reply.outbound_messages],
        "reply_type": reply.reply_type,
        "route": reply.route,
        "intent": intent.model_dump(),
        "template": template,
        "need_human": reply.need_human,
        "next_action": reply.next_action,
        "trace_id": trace_id,
        "metadata": public_metadata,
        "handoff": _legacy_handoff(reply.metadata.get("handoff")),
    }


def _public_reply_metadata(metadata: dict) -> dict:
    internal_keys = {
        "business_context",
        "business_facts",
        "decision",
        "reply_plan",
        "sales_stage_decision",
        "tool_state",
    }

    def strip(value):
        if isinstance(value, dict):
            return {
                key: strip(item)
                for key, item in value.items()
                if str(key).lower() not in internal_keys
            }
        if isinstance(value, list):
            return [strip(item) for item in value]
        return value

    cleaned = strip(metadata)
    return cleaned if isinstance(cleaned, dict) else {}


def _legacy_handoff(handoff: dict | None) -> dict | None:
    if not handoff:
        return None
    reason = handoff.get("reason")
    legacy_reason = {
        "clarify_to_handoff": "clarify",
        "rag_no_answer_to_handoff": "rag_no_answer",
    }.get(reason, reason)
    return {**handoff, "reason": legacy_reason}


def _is_evaluation_request(message) -> bool:
    return bool(getattr(message, "metadata", {}).get("evaluation_id"))


def _apply_evaluation_context(message, user_state) -> None:
    metadata = getattr(message, "metadata", {})
    context = metadata.get("evaluation_context") if isinstance(metadata, dict) else None
    if not isinstance(context, dict):
        return
    customer_context = str(context.get("customer_context") or "").strip()
    recent_turns = context.get("recent_turns")
    if customer_context:
        user_state.metadata["profile"] = {"ai_summary": customer_context}
    if isinstance(recent_turns, list):
        user_state.metadata["recent_turns"] = recent_turns


async def _hydrate_user_state_from_profile(user_id: str, user_state) -> None:
    try:
        bundle = await get_profile_bundle(user_id)
    except Exception:  # noqa: BLE001
        return
    profile = bundle.get("profile") if isinstance(bundle, dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    persisted_stage = normalize_sales_stage(profile.get("current_stage"))
    if normalize_sales_stage(user_state.sales_stage) == "unknown" and persisted_stage != "unknown":
        user_state.sales_stage = persisted_stage
    profile_tags = profile.get("customer_tags") or []
    if isinstance(profile_tags, list):
        user_state.customer_tags = _merge_list(user_state.customer_tags, profile_tags)
    user_state.metadata = {
        **user_state.metadata,
        "profile": profile,
        "recent_turns": bundle.get("recent_memories", []),
    }


def _merge_shipping_contact_into_state(user_state, contact: dict[str, str]) -> None:
    if not contact:
        return
    profile = user_state.metadata.get("profile")
    if not isinstance(profile, dict):
        profile = {}
    basic_info = profile.get("basic_info")
    if not isinstance(basic_info, dict):
        basic_info = {}
    user_state.metadata["profile"] = {
        **profile,
        "basic_info": {**basic_info, **contact},
    }


def _merge_list(existing: list, incoming: list) -> list:
    merged = list(existing or [])
    for item in incoming:
        if isinstance(item, str) and item and item not in merged:
            merged.append(item)
    return merged


def _answer_segments(
    answer: str,
    preferred: list[str] | None = None,
) -> list[str]:
    structured = [part.strip() for part in preferred or [] if part.strip()]
    if structured:
        messages = []
        for part in structured:
            message = plain_customer_text(part).replace("\n", " ").strip()
            if message:
                messages.append(message)
        return coalesce_customer_messages(messages)
    return split_customer_messages(answer)


def _success_log_payload(message, intent, decision, reply: FinalReply) -> dict:
    return {
        **_message_log_base(message),
        "answer": reply.answer,
        "route": reply.route,
        "reply_type": reply.reply_type,
        "primary_intent": intent.primary_intent,
        "secondary_intents": intent.secondary_intents,
        "sales_stage": intent.sales_stage,
        "confidence": intent.confidence,
        "template_id": reply.template_id,
        "template_score": reply.metadata.get("score"),
        "next_action": reply.next_action,
        "sources": reply.sources,
        "need_human": reply.need_human,
        "policy_reason": decision.reason,
        "intent_reason": intent.reason,
        "tag_result": reply.metadata.get("tag_result"),
        "policy_decision": reply.metadata.get("policy_decision"),
        "usage": reply.usage,
        "status": "success",
        "error_code": None,
        "error_message": None,
        "metadata": reply.metadata,
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
    payload = _message_log_base(message) if message is not None else _request_log_base(request)
    if intent is not None:
        payload.update(
            {
                "route": intent.route,
                "primary_intent": intent.primary_intent,
                "secondary_intents": intent.secondary_intents,
                "sales_stage": intent.sales_stage,
                "confidence": intent.confidence,
                "intent_reason": intent.reason,
            }
        )
    if reply is not None:
        payload.update(
            {
                "answer": reply.answer,
                "route": reply.route,
                "reply_type": reply.reply_type,
                "template_id": reply.template_id,
                "sources": reply.sources,
                "usage": reply.usage,
                "need_human": reply.need_human,
            }
        )
    payload.update(
        {
            "status": "failed",
            "error_code": error_code,
            "error_message": error_message,
            "created_at": _now_iso(),
        }
    )
    return payload


def _message_log_base(message) -> dict:
    return {
        "trace_id": message.trace_id,
        "request_id": message.trace_id,
        "channel": message.channel,
        "user_id": message.user_id,
        "session_id": message.session_id,
        "message_id": message.message_id,
        "kb_id": message.kb_id,
        "tenant_id": message.tenant_id,
        "permission": message.permission,
        "user_message": message.message,
        "answer": None,
        "sources": [],
        "usage": {},
        "need_human": False,
        "metadata": message.metadata,
    }


def _request_log_base(request: ChatRequest) -> dict:
    return {
        "trace_id": f"request_failed_{int(time.time() * 1000)}",
        "request_id": None,
        "channel": request.channel,
        "user_id": request.user_id,
        "session_id": request.session_id,
        "message_id": None,
        "kb_id": request.kb_id,
        "tenant_id": (request.metadata or {}).get("tenant_id"),
        "permission": (request.metadata or {}).get("permission"),
        "user_message": request.message,
        "answer": None,
        "sources": [],
        "usage": {},
        "need_human": False,
        "metadata": request.metadata or {},
    }


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
