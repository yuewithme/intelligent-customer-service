import time

from app.core.config import get_settings
from app.domains.conversations.schemas.chat import ChatRequest
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.decisioning.schemas.reply import FinalReply, OutboundMessage
from app.domains.conversations.services.channel_service import normalize_chat_request
from app.domains.conversations.services.chat_log_service import record_chat_log
from app.domains.decisioning.services.business_context_service import build_business_context
from app.domains.decisioning.services.business_action_service import (
    resolve_business_action,
)
from app.domains.catalog.services.commerce_query_service import (
    build_commerce_context,
    verified_membership_brand_facts,
)
from app.domains.conversations.services.conversation_service import (
    AI_WAITING,
    conversation_blocks_ai,
    record_ai_turn,
    record_customer_message,
)
from app.domains.decisioning.services.customer_reply_formatter import (
    coalesce_customer_messages,
    plain_customer_text,
    split_customer_messages,
)
from app.domains.decisioning.services.intent_example_service import retrieve_intent_examples
from app.domains.decisioning.services.intent_observation_service import (
    finalize_intent_observation,
    record_intent_observation,
)
from app.domains.decisioning.services.intent_service import (
    classify_by_fast_rule,
    classify_intent,
    classify_material_followup,
    schedule_intent_shadow_evaluation,
)
from app.domains.customers.services.memory_rollout_service import prepare_memory_context_for_request
from app.domains.decisioning.services.policy_service import decide_route
from app.domains.decisioning.services.policy_engine import decide_policy
from app.domains.decisioning.services.reply_planner import resolve_reply_plan
from app.domains.decisioning.services.reply_shadow_service import (
    schedule_reply_shadow_evaluation,
)
from app.domains.decisioning.services.reply_workflow_graph import execute_reply_plan
from app.domains.decisioning.services.rule_guard_service import check_rules
from app.domains.sales.services.sales_action_service import apply_sales_action, decide_sales_action
from app.domains.sales.services.sales_stage_knowledge_policy import (
    allowed_knowledge_sources,
    apply_stage_knowledge_policy,
)
from app.domains.sales.services.sales_signal_service import normalize_sales_signals
from app.domains.sales.services.sales_stage_service import decide_sales_stage, normalize_sales_stage
from app.domains.sales.services.shipping_contact_service import extract_shipping_contact
from app.domains.conversations.services.state_service import get_user_state, update_user_state
from app.domains.sales.services.tagger_service import build_tag_result
from app.domains.customers.services.user_profile_service import (
    apply_deterministic_profile_update,
    append_conversation_memory,
    get_profile_bundle,
    save_shipping_contact,
)
from app.core.logger import log_event
from app.core.trace_context import reset_trace_id, set_trace_id


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
    is_evaluation = False
    intent_observation_recorded = False
    trace_token = None

    try:
        stage_started = time.perf_counter()
        message = await normalize_chat_request(request)
        trace_token = set_trace_id(message.trace_id)
        is_evaluation = _is_evaluation_request(message)
        stage_latencies["normalize_ms"] = _elapsed_ms(stage_started)

        if not is_evaluation and conversation_blocks_ai(
            channel=message.channel,
            user_id=message.user_id,
            session_id=message.session_id,
        ):
            if message.metadata.get("provider") != "eyun" and not message.metadata.get(
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
            routed_intent = IntentResult(
                route="human",
                primary_intent="human_handoff_active",
                primary_domain="conversation",
                primary_goal="unclear",
                scope="ambiguous",
                classifier_source="state_guard",
                confidence=1.0,
                need_human=True,
                reason="human_handoff_locked",
            )
            intent = routed_intent
            await record_intent_observation(
                message=message,
                intent=routed_intent,
                candidates=[],
                context=[],
            )
            intent_observation_recorded = True
            decision = PolicyDecision(
                route="human",
                reason="human_handoff_locked",
                original_route="human",
                next_action="human_handoff",
            )
            reply = FinalReply(
                answer="",
                reply_type="human",
                route="human",
                need_human=True,
                next_action="human_handoff",
                metadata={"handoff_locked": True},
            )
            stage_latencies.update(
                {
                    "state_ms": 0,
                    "rule_guard_ms": 0,
                    "intent_examples_ms": 0,
                    "intent_ms": 0,
                    "policy_ms": 0,
                    "tag_policy_ms": 0,
                    "reply_build_ms": 0,
                    "state_update_ms": 0,
                }
            )
            result = _to_chat_data(
                message.session_id, message.trace_id, routed_intent, reply
            )
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
                    "route": "human",
                    "intent": routed_intent.primary_intent,
                    "candidate_count": 0,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "status": "success",
                }
            )
            return result

        stage_started = time.perf_counter()
        user_state = await get_user_state(message.user_id, message.session_id)
        if is_evaluation:
            _apply_evaluation_context(message, user_state)
        else:
            await _hydrate_user_state_from_profile(message.user_id, user_state)
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
            intent = classify_material_followup(
                message.message,
                user_state.metadata.get("recent_turns", []),
            )
        if intent is None:
            intent = classify_by_fast_rule(message.message)
            if intent is not None:
                stage_latencies["intent_examples_ms"] = 0
                stage_latencies["intent_ms"] = 0
            else:
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

        if not is_evaluation:
            schedule_intent_shadow_evaluation(
                message=message,
                user_state=user_state,
                primary=intent,
                candidates=candidates,
            )
            await record_intent_observation(
                message=message,
                intent=intent,
                candidates=candidates,
                context=user_state.metadata.get("recent_turns", []),
            )
            intent_observation_recorded = True

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
        business_action = resolve_business_action(
            message=message,
            intent=intent,
            user_state=user_state,
        )
        source_allowlist = allowed_knowledge_sources(
            preliminary_decision.stage,
            intent,
            business_action=business_action,
        )
        facts = await build_commerce_context(
            message,
            user_state,
            intent,
            business_action=business_action,
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
            business_action=business_action,
        )
        stage_latencies["tag_policy_ms"] = _elapsed_ms(stage_started)

        route = plan.action
        routed_intent = normalized_intent.model_copy(
            update={
                "route": route,
                "sales_stage": sales_stage_decision.stage,
                "reason": plan.reason,
                "slots": {
                    **normalized_intent.slots,
                    "original_route": plan.original_route or intent.route,
                    "sales_stage_reason": sales_stage_decision.reason,
                },
            }
        )
        if not is_evaluation:
            await finalize_intent_observation(message.trace_id, routed_intent)
        sales_action = decide_sales_action(
            user_state=user_state,
            intent=routed_intent,
        )
        if sales_action.sales_action == "discover_pain":
            sales_action = sales_action.model_copy(
                update={
                    "brand_value_facts": (
                        []
                        if _pain_brand_value_already_present(user_state)
                        else verified_membership_brand_facts()
                    ),
                }
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
        persona_meta = reply.metadata.get("persona", {})
        if not persona_meta.get("sales_action_rendered"):
            reply = apply_sales_action(reply, sales_action)
        stage_latencies["reply_build_ms"] = _elapsed_ms(stage_started)
        sales_action_payload = sales_action.model_dump()
        emitted_question_slot = reply.metadata.get("emitted_question_slot")
        if isinstance(emitted_question_slot, str) and emitted_question_slot:
            sales_action_payload["emitted_question_slot"] = emitted_question_slot
        reply.metadata["sales_action"] = sales_action_payload
        reply.metadata["sales_stage_decision"] = sales_stage_decision.model_dump(
            mode="json"
        )
        reply.metadata["business_action"] = business_action
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
        if _should_prepend_opening(
            message=message,
            user_state=user_state,
            is_evaluation=is_evaluation,
        ):
            reply = _prepend_opening(reply)
            user_state.metadata["opening_sent"] = True

        if not is_evaluation:
            schedule_reply_shadow_evaluation(
                message=message,
                user_state=user_state,
                intent=routed_intent,
                plan=plan,
                reply=reply,
            )

        stage_started = time.perf_counter()
        await update_user_state(
            message.user_id,
            message.session_id,
            routed_intent,
            reply,
        )
        if not is_evaluation and not message.metadata.get("skip_conversation_memory"):
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
                    intent=routed_intent.primary_intent,
                    route=reply.route,
                    template_id=reply.template_id,
                    trace_id=message.trace_id,
                    channel=message.channel,
                    owner_external_id=_memory_owner_external_id(message.metadata),
                    source_id=message.trace_id,
                )
            await apply_deterministic_profile_update(message, routed_intent, reply)
        stage_latencies["state_update_ms"] = _elapsed_ms(stage_started)

        result = _to_chat_data(message.session_id, message.trace_id, routed_intent, reply)
        await _record_workbench_turn(
            message=message,
            result=result,
            is_evaluation=is_evaluation,
        )
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
        if message is not None and not is_evaluation and not intent_observation_recorded:
            fallback_intent = intent or routed_intent or IntentResult(
                route="clarify",
                primary_intent="unknown",
                primary_domain="conversation",
                primary_goal="unclear",
                scope="ambiguous",
                classifier_source="pipeline_error",
                confidence=0.0,
                reason="pipeline_failed_before_intent",
            )
            await record_intent_observation(
                message=message,
                intent=fallback_intent,
                candidates=candidates,
                context=(
                    user_state.metadata.get("recent_turns", [])
                    if user_state is not None
                    else []
                ),
            )
        if log_payload:
            log_payload["latency_ms"] = _elapsed_ms(started)
            log_payload["stage_latencies"] = stage_latencies
            await record_chat_log(log_payload)
        if trace_token is not None:
            reset_trace_id(trace_token)


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


def _should_prepend_opening(*, message, user_state, is_evaluation: bool) -> bool:
    if user_state.metadata.get("opening_sent"):
        return False
    return bool(message.metadata.get("prepend_opening")) or is_evaluation


def _pain_brand_value_already_present(user_state) -> bool:
    metadata = getattr(user_state, "metadata", {})
    turns = metadata.get("recent_turns") if isinstance(metadata, dict) else None
    if not isinstance(turns, list):
        return False
    return any(
        isinstance(turn, dict)
        and str(turn.get("role") or "") == "assistant"
        and "萧岚苑" in str(turn.get("content") or "")
        and any(
            marker in str(turn.get("content") or "")
            for marker in ("反复试错", "少走弯路", "理清养护", "结合具体", "针对具体")
        )
        for turn in turns
    )


def _prepend_opening(reply: FinalReply) -> FinalReply:
    from app.domains.decisioning.services.reply_builder import build_opening_reply

    opening = build_opening_reply()
    reply_segments = list(reply.answer_segments)
    if not reply_segments and reply.answer.strip():
        reply_segments = [reply.answer.strip()]
    reply_messages = list(reply.outbound_messages)
    if not reply_messages:
        reply_messages = [
            OutboundMessage(type="text", content=segment)
            for segment in reply_segments
        ]
    answer_segments = [opening.answer, *reply_segments]
    return reply.model_copy(
        update={
            "answer": "\n\n".join(
                segment for segment in answer_segments if segment.strip()
            ),
            "answer_segments": answer_segments,
            "outbound_messages": [
                *opening.outbound_messages,
                *reply_messages,
            ],
            "metadata": {
                **reply.metadata,
                "opening_prepended": True,
            },
        }
    )


def _public_reply_metadata(metadata: dict) -> dict:
    internal_keys = {
        "business_context",
        "business_facts",
        "decision",
        "memory_v2_context",
        "memory_v2_trace",
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


async def _record_workbench_turn(*, message, result: dict, is_evaluation: bool) -> None:
    if not is_evaluation and message.metadata.get("provider") == "eyun":
        # The Eyun gateway records the exact queued outbound sequence, including
        # opening messages and cards, after it composes the provider payload.
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
    await record_ai_turn(
        message=workbench_message,
        result={
            **result,
            # Simulated conversations preserve the expected handoff state for
            # evaluation while the message flag suppresses real notifications.
        },
    )


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


def _memory_owner_external_id(metadata: dict) -> str:
    for key in ("w_id", "owner_external_id", "wechat_to_user"):
        value = str(metadata.get(key) or "").strip()
        if value:
            return value
    return ""


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
