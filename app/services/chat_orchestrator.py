import time

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.schemas.common import AppError, ErrorCode
from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.services.channel_service import normalize_chat_request
from app.services.chat_log_service import record_chat_log
from app.services.intent_example_service import retrieve_intent_examples
from app.services.intent_service import classify_intent
from app.services.policy_service import decide_route
from app.services.reply_builder import (
    build_chitchat_reply,
    build_clarify_reply,
    build_human_reply,
    build_rag_reply,
    build_template_reply,
    build_template_then_rag_reply,
    build_unsupported_reply,
)
from app.services.rule_guard_service import check_rules
from app.services.state_service import get_user_state, update_user_state
from app.services.template_service import render_template, select_template
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
    reply = None

    try:
        stage_started = time.perf_counter()
        message = await normalize_chat_request(request)
        stage_latencies["normalize_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        user_state = await get_user_state(message.user_id, message.session_id)
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

        route = decision.fallback_route if not decision.allowed else decision.route
        routed_intent = intent.model_copy(update={"route": route})

        stage_started = time.perf_counter()
        reply = await _build_reply(route, routed_intent, message, user_state, stage_latencies)
        stage_latencies["reply_build_ms"] = _elapsed_ms(stage_started)

        stage_started = time.perf_counter()
        await update_user_state(
            message.user_id,
            message.session_id,
            routed_intent,
            reply,
        )
        stage_latencies["state_update_ms"] = _elapsed_ms(stage_started)

        result = _to_chat_data(message.session_id, message.trace_id, routed_intent, reply)
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
) -> FinalReply:
    stage_latencies = stage_latencies if stage_latencies is not None else {}
    if route == "template_reply":
        stage_started = time.perf_counter()
        template = await select_template(message, intent, user_state)
        if template is None:
            stage_latencies["template_ms"] = _elapsed_ms(stage_started)
            stage_latencies.setdefault("rag_ms", 0)
            return build_unsupported_reply(intent)
        template_reply = await render_template(template, message, user_state)
        stage_latencies["template_ms"] = _elapsed_ms(stage_started)
        stage_latencies.setdefault("rag_ms", 0)
        return build_template_reply(template_reply, intent)
    if route == "rag_answer":
        from app.services.rag_service import answer_knowledge

        stage_started = time.perf_counter()
        rag_result = await answer_knowledge(message, user_state)
        stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
        stage_latencies.setdefault("template_ms", 0)
        return build_rag_reply(rag_result, intent)
    if route == "template_then_rag":
        from app.services.rag_service import answer_knowledge

        stage_started = time.perf_counter()
        template = await select_template(message, intent, user_state)
        if template is None:
            stage_latencies["template_ms"] = _elapsed_ms(stage_started)
            stage_started = time.perf_counter()
            rag_result = await answer_knowledge(message, user_state)
            stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
            return build_rag_reply(rag_result, intent)
        template_reply = await render_template(template, message, user_state)
        stage_latencies["template_ms"] = _elapsed_ms(stage_started)
        stage_started = time.perf_counter()
        rag_result = await answer_knowledge(message, user_state)
        stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
        return build_template_then_rag_reply(template_reply, rag_result, intent)
    stage_latencies.setdefault("template_ms", 0)
    stage_latencies.setdefault("rag_ms", 0)
    if route == "human":
        return build_human_reply(intent)
    if route == "chitchat":
        return build_chitchat_reply(intent)
    if route == "unsupported":
        return build_unsupported_reply(intent)
    return build_clarify_reply(intent)


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
        "reply_type": reply.reply_type,
        "route": reply.route,
        "intent": intent.model_dump(),
        "template": template,
        "need_human": reply.need_human,
        "next_action": reply.next_action,
        "trace_id": trace_id,
    }


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
