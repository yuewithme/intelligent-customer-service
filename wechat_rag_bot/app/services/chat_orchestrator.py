import time

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.schemas.common import AppError, ErrorCode
from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.services.channel_service import normalize_chat_request
from app.services.chat_log_service import record_chat_log
from app.services.conversation_service import record_ai_turn
from app.services.intent_example_service import retrieve_intent_examples
from app.services.intent_service import classify_intent
from app.services.policy_service import decide_route
from app.services.policy_engine import decide_policy
from app.services.reply_builder import (
    build_chitchat_reply,
    build_clarify_reply,
    build_rag_reply,
    build_unsupported_reply,
)
from app.services.rule_guard_service import check_rules
from app.services.state_service import get_user_state, update_user_state
from app.services.tagger_service import build_tag_result
from app.services.template_reply_service import build_default_template_reply
from app.services.user_profile_service import (
    append_conversation_memory,
    get_profile_bundle,
    update_profile_after_chat,
)
from app.talk_script.service import match_talk_script
from app.talk_script.human_handoff_service import request_human_handoff
from app.utils.ids import generate_id
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
        stage_latencies["normalize_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        user_state = await get_user_state(message.user_id, message.session_id)
        await _hydrate_user_state_from_profile(message.user_id, user_state)
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
        rich_decision = await decide_policy(tag_result)
        if rich_decision.reason != "default_tag_policy":
            decision = rich_decision
        stage_latencies["tag_policy_ms"] = _elapsed_ms(stage_started)

        route = decision.route
        routed_intent = intent.model_copy(
            update={
                "route": route,
                "reason": decision.reason or intent.reason,
                "slots": {
                    **intent.slots,
                    "original_route": decision.original_route or intent.route,
                },
            }
        )

        stage_started = time.perf_counter()
        if get_settings().reply_graph_enabled:
            from app.services.reply_workflow_graph import build_reply_with_graph

            reply = await build_reply_with_graph(
                route=route,
                intent=routed_intent,
                message=message,
                user_state=user_state,
                stage_latencies=stage_latencies,
                policy_decision=rich_decision or decision,
            )
        else:
            reply = await _build_reply(
                route,
                routed_intent,
                message,
                user_state,
                stage_latencies,
                policy_decision=rich_decision or decision,
            )
        stage_latencies["reply_build_ms"] = _elapsed_ms(stage_started)
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
        await update_profile_after_chat(message, routed_intent, reply)
        stage_latencies["state_update_ms"] = _elapsed_ms(stage_started)

        result = _to_chat_data(message.session_id, message.trace_id, routed_intent, reply)
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


async def _build_reply(
    route: str,
    intent: IntentResult,
    message,
    user_state,
    stage_latencies: dict[str, int] | None = None,
    policy_decision=None,
) -> FinalReply:
    stage_latencies = stage_latencies if stage_latencies is not None else {}
    if route in {"template_reply", "template_then_rag", "rag_answer"}:
        stage_started = time.perf_counter()
        talk_script = await match_talk_script(
            customer_id=message.user_id,
            current_message=message.message,
            trace_id=message.trace_id,
            session_id=message.session_id,
            recent_messages=[],
            customer_tags={"tags": user_state.customer_tags},
        )
        stage_latencies["talk_script_ms"] = _elapsed_ms(stage_started)
        if talk_script.status == "matched":
            stage_latencies.setdefault("template_ms", 0)
            stage_latencies.setdefault("rag_ms", 0)
            return FinalReply(
                answer=talk_script.answer,
                reply_type="template",
                route="template_reply",
                template_id=talk_script.template_id,
                need_human=False,
                metadata={
                    "talk_script": talk_script.model_dump(),
                    "score": talk_script.confidence,
                },
            )
        if talk_script.status == "handoff" and not _is_soft_talk_script_handoff(
            talk_script.reason
        ):
            stage_latencies.setdefault("template_ms", 0)
            stage_latencies.setdefault("rag_ms", 0)
            return await build_handoff_reply(
                message=message,
                intent=intent,
                reason=talk_script.reason or "talk_script_to_handoff",
                original_route=route,
                context={"talk_script": talk_script.model_dump()},
            )
    else:
        stage_latencies.setdefault("talk_script_ms", 0)
    if route == "template_reply":
        stage_started = time.perf_counter()
        reply = await build_default_template_reply(message, intent, user_state)
        stage_latencies["template_ms"] = _elapsed_ms(stage_started)
        stage_latencies.setdefault("rag_ms", 0)
        if reply is not None:
            return reply
        return await build_handoff_reply(
            message=message,
            intent=intent,
            reason="template_not_matched_to_handoff",
            original_route="template_reply",
        )
    if route == "rag_answer":
        from app.services.rag_service import answer_knowledge

        stage_started = time.perf_counter()
        rag_result = await answer_knowledge(
            message,
            user_state,
            policy_decision=policy_decision,
        )
        stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
        stage_latencies.setdefault("template_ms", 0)
        if _is_rag_no_answer(rag_result):
            return await build_handoff_reply(
                message=message,
                intent=intent,
                reason="rag_no_answer_to_handoff",
                original_route="rag_answer",
            )
        return build_rag_reply(rag_result, intent)
    if route == "template_then_rag":
        from app.services.rag_service import answer_knowledge

        stage_latencies.setdefault("template_ms", 0)
        stage_started = time.perf_counter()
        rag_result = await answer_knowledge(
            message,
            user_state,
            policy_decision=policy_decision,
        )
        stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
        if _is_rag_no_answer(rag_result):
            return await build_handoff_reply(
                message=message,
                intent=intent,
                reason="rag_no_answer_to_handoff",
                original_route="template_then_rag",
            )
        return build_rag_reply(rag_result, intent)
    stage_latencies.setdefault("template_ms", 0)
    stage_latencies.setdefault("rag_ms", 0)
    if route == "human":
        return await build_handoff_reply(
            message=message,
            intent=intent,
            reason=intent.reason or "human_required",
            original_route=intent.slots.get("original_route") or intent.route,
        )
    if route == "chitchat":
        return build_chitchat_reply(intent)
    if route == "unsupported":
        return build_unsupported_reply(intent)
    return build_clarify_reply(intent)


async def build_handoff_reply(
    *,
    message,
    intent: IntentResult,
    reason: str,
    original_route: str | None = None,
    context: dict | None = None,
) -> FinalReply:
    ticket_id = generate_id("handoff")
    handoff = await request_human_handoff(
        customer_id=message.user_id,
        current_message=message.message,
        reason=reason,
        context={
            "ticket_id": ticket_id,
            "trace_id": message.trace_id,
            "session_id": message.session_id,
            "intent": intent.model_dump(),
            "original_route": original_route,
            **(context or {}),
        },
    )
    return FinalReply(
        answer="",
        reply_type="human",
        route="human",
        need_human=True,
        next_action="human_handoff",
        metadata={
            "handoff": {
                "ticket_id": ticket_id,
                "status": handoff.status,
                "reason": reason,
            },
            "original_route": original_route,
            **(context or {}),
        },
    )


def _is_rag_no_answer(rag_result: dict) -> bool:
    answer = (rag_result.get("answer") or "").strip()
    return (
        not answer
        or answer == "知识库中没有找到明确答案。"
    )


def _is_soft_talk_script_handoff(reason: str | None) -> bool:
    return reason in {
        "classifier_unmatched",
        "confidence_below_threshold",
        "no_candidate_questions",
        "question_not_found",
        "template_not_found",
    }


def _to_chat_data(
    session_id: str,
    trace_id: str,
    intent: IntentResult,
    reply: FinalReply,
) -> dict:
    template = {}
    if reply.template_id:
        template = {"template_id": reply.template_id}
    return {
        "answer": reply.answer,
        "session_id": session_id,
        "sources": reply.sources,
        "usage": reply.usage,
        "answer_segments": _answer_segments(reply.answer),
        "reply_type": reply.reply_type,
        "route": reply.route,
        "intent": intent.model_dump(),
        "template": template,
        "need_human": reply.need_human,
        "next_action": reply.next_action,
        "trace_id": trace_id,
        "metadata": reply.metadata,
        "handoff": _legacy_handoff(reply.metadata.get("handoff")),
    }


def _legacy_handoff(handoff: dict | None) -> dict | None:
    if not handoff:
        return None
    reason = handoff.get("reason")
    legacy_reason = {
        "clarify_to_handoff": "clarify",
        "rag_no_answer_to_handoff": "rag_no_answer",
    }.get(reason, reason)
    return {**handoff, "reason": legacy_reason}


async def _hydrate_user_state_from_profile(user_id: str, user_state) -> None:
    try:
        bundle = await get_profile_bundle(user_id)
    except Exception:  # noqa: BLE001
        return
    profile = bundle.get("profile") if isinstance(bundle, dict) else {}
    if not isinstance(profile, dict):
        profile = {}
    profile_tags = profile.get("customer_tags") or []
    if isinstance(profile_tags, list):
        user_state.customer_tags = _merge_list(user_state.customer_tags, profile_tags)
    user_state.metadata = {
        **user_state.metadata,
        "profile": profile,
        "recent_turns": bundle.get("recent_memories", []),
    }


def _merge_list(existing: list, incoming: list) -> list:
    merged = list(existing or [])
    for item in incoming:
        if isinstance(item, str) and item and item not in merged:
            merged.append(item)
    return merged


def _answer_segments(answer: str) -> list[str]:
    return [part.strip() for part in answer.splitlines() if part.strip()]


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
