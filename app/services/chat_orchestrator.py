import time

from app.config import get_settings
from app.schemas.chat import ChatRequest
from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply
from app.services.channel_service import normalize_chat_request
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
    message = await normalize_chat_request(request)
    user_state = await get_user_state(message.user_id, message.session_id)
    candidates: list[dict] = []

    intent = await check_rules(message, user_state)
    if intent is None:
        candidates = await retrieve_intent_examples(
            message.message,
            top_k=get_settings().intent_example_top_k,
        )
        intent = await classify_intent(message, user_state, candidates)

    decision = await decide_route(intent, user_state, message)
    route = decision.fallback_route if not decision.allowed else decision.route
    routed_intent = intent.model_copy(update={"route": route})
    reply = await _build_reply(route, routed_intent, message, user_state)

    await update_user_state(
        message.user_id,
        message.session_id,
        routed_intent,
        reply,
    )

    result = _to_chat_data(message.session_id, message.trace_id, routed_intent, reply)
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


async def _build_reply(
    route: str,
    intent: IntentResult,
    message,
    user_state,
) -> FinalReply:
    if route == "template_reply":
        template = await select_template(message, intent, user_state)
        if template is None:
            return build_unsupported_reply(intent)
        template_reply = await render_template(template, message, user_state)
        return build_template_reply(template_reply, intent)
    if route == "rag_answer":
        from app.services.rag_service import answer_knowledge

        return build_rag_reply(await answer_knowledge(message, user_state), intent)
    if route == "template_then_rag":
        from app.services.rag_service import answer_knowledge

        template = await select_template(message, intent, user_state)
        if template is None:
            return build_rag_reply(await answer_knowledge(message, user_state), intent)
        template_reply = await render_template(template, message, user_state)
        rag_result = await answer_knowledge(message, user_state)
        return build_template_then_rag_reply(template_reply, rag_result, intent)
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
