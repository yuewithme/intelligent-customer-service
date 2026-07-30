import logging
import time
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.ids import generate_id
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.persona import PersonaContext, ReplySpec
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.domains.decisioning.schemas.reply import FinalReply
from app.domains.decisioning.schemas.reply_plan import ReplyPlan
from app.domains.decisioning.services.business_reply_renderer import render_business_reply
from app.domains.decisioning.services.persona_renderer import render_persona_reply
from app.domains.decisioning.services.persona_service import (
    build_persona_context,
    build_reply_spec,
)
from app.domains.decisioning.services.reply_guard_service import finalize_reply_spec, guard_reply_spec
from app.domains.decisioning.services.reply_builder import (
    build_chitchat_reply,
    build_rag_reply,
)
from app.domains.decisioning.services.template_reply_service import build_default_template_reply
from app.domains.sales.talk_script.human_handoff_service import request_human_handoff
from app.integrations.ai.services.llm_service import generate_messages


logger = logging.getLogger("wechat_rag_bot.reply_workflow")
DEMO_CHANNELS = {"web_demo", "mcp_demo"}
EXPLICIT_HUMAN_INTENTS = {"refund_request", "complaint", "human_request"}
EXPLICIT_HUMAN_GOALS = {"request_refund_return", "complain", "request_human"}


class ReplyWorkflowState(TypedDict, total=False):
    plan: ReplyPlan
    intent: IntentResult
    message: Any
    user_state: Any
    stage_latencies: dict[str, int]
    persona_context: PersonaContext
    reply_spec: ReplySpec
    reply: FinalReply
    handoff_reason: str
    handoff_original_route: str | None
    handoff_context: dict | None


def persona_context_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    started = time.perf_counter()
    context = build_persona_context(
        message=state["message"],
        user_state=state["user_state"],
        intent=state["intent"],
    )
    state["stage_latencies"]["persona_context_ms"] = _elapsed_ms(started)
    return {"persona_context": context}


def route_reply_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    del state
    return {}


async def template_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    stage_latencies = state["stage_latencies"]
    stage_started = time.perf_counter()
    plan = state["plan"]
    if state["intent"].primary_goal == "request_material":
        from app.domains.catalog.services.orchid_material_service import (
            orchid_material_chat_result,
        )

        material_result = orchid_material_chat_result(state["message"].message)
        reply = (
            FinalReply.model_validate(material_result)
            if material_result is not None
            else None
        )
    elif plan.business_facts.available:
        reply = await render_business_reply(state["message"], plan.business_facts)
    else:
        reply = await build_default_template_reply(
            state["message"],
            state["intent"],
            state["user_state"],
        )
    stage_latencies["template_ms"] = _elapsed_ms(stage_started)
    if reply is not None:
        return {"reply_spec": _reply_spec(state, reply)}
    stage_latencies["rag_ms"] = 0
    return {
        "handoff_reason": (
            "business_facts_unanswerable_to_handoff"
            if plan.business_facts.available
            else "template_not_found_to_handoff"
        ),
        "handoff_original_route": plan.original_route or plan.action,
    }


def _policy_from_plan(plan: ReplyPlan) -> PolicyDecision:
    return PolicyDecision(
        route=plan.action,
        reason=plan.reason,
        original_route=plan.original_route,
        next_action=plan.next_action,
        knowledge_base_ids=plan.knowledge_base_ids,
        template_ids=plan.template_ids,
        prompt_block_ids=plan.prompt_block_ids,
        context_policy=plan.context_policy,
        retrieval_policy=plan.retrieval_policy,
    )


async def rag_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    from app.domains.knowledge.services.rag_service import answer_knowledge

    stage_latencies = state["stage_latencies"]
    stage_started = time.perf_counter()
    rag_result = await answer_knowledge(
        state["message"],
        state["user_state"],
        policy_decision=_policy_from_plan(state["plan"]),
    )
    for key, value in rag_result.get("stage_latencies", {}).items():
        stage_latencies[f"rag_{key}"] = value
    stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
    stage_latencies.setdefault("template_ms", 0)
    if _is_rag_no_answer(rag_result):
        return {
            "handoff_reason": "rag_no_answer_to_handoff",
            "handoff_original_route": state["plan"].original_route
            or state["plan"].action,
        }
    reply = build_rag_reply(rag_result, state["intent"])
    return {"reply_spec": _reply_spec(state, reply)}


async def build_handoff_reply(
    *,
    message,
    intent: IntentResult,
    reason: str,
    original_route: str | None = None,
    context: dict | None = None,
) -> FinalReply:
    if _should_use_demo_llm_fallback(message, intent):
        return await _build_demo_llm_fallback_reply(
            message=message,
            intent=intent,
            reason=reason,
            original_route=original_route,
            context=context,
        )
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


def _should_use_demo_llm_fallback(message, intent: IntentResult) -> bool:
    metadata = getattr(message, "metadata", {}) or {}
    is_demo = (
        bool(metadata.get("demo"))
        or getattr(message, "channel", "") in DEMO_CHANNELS
    )
    if not is_demo:
        return False
    if intent.need_human:
        return False
    if intent.primary_intent in EXPLICIT_HUMAN_INTENTS:
        return False
    return intent.primary_goal not in EXPLICIT_HUMAN_GOALS


async def _build_demo_llm_fallback_reply(
    *,
    message,
    intent: IntentResult,
    reason: str,
    original_route: str | None,
    context: dict | None,
) -> FinalReply:
    fallback_error = ""
    try:
        result = await generate_messages(
            [
                {
                    "role": "system",
                    "content": (
                        "你是兰花商品客服。当前结构化流程没有得到可发送的答案，"
                        "请直接给客户一个自然、简短、可继续对话的中文回复。"
                        "不得编造商品名称、价格、库存、购买入口、订单、物流、售后状态"
                        "或资料权益；缺少必要信息时只追问一个最关键的问题。"
                        "商品推荐应说明会按需求查询商品库；订单问题优先索取手机号、"
                        "订单号或订单截图。不要提到测试、模型、内部流程或转人工。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"客户原话：{message.message}\n"
                        f"识别意图：{intent.primary_intent}\n"
                        f"原处理路径：{original_route or intent.route}"
                    ),
                },
            ],
            purpose="business",
            temperature=0.2,
        )
        answer = str(result.get("answer") or "").strip()
        usage = result.get("usage") or {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Demo LLM fallback failed: %s", type(exc).__name__)
        answer = ""
        usage = {}
        fallback_error = type(exc).__name__
    if not answer or answer == "__HANDOFF__":
        answer = "我先帮您继续确认一下：您现在主要想咨询养护、选购，还是订单问题？"
    return FinalReply(
        answer=answer,
        answer_segments=[answer],
        reply_type="llm_fallback",
        route=original_route or intent.route or "clarify",
        need_human=False,
        next_action="llm_fallback",
        metadata={
            "demo_llm_fallback": {
                "reason": reason,
                "usage": usage,
                **({"error": fallback_error} if fallback_error else {}),
            },
            "original_route": original_route,
            **(context or {}),
        },
    )


async def handoff_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    reply = await build_handoff_reply(
        message=state["message"],
        intent=state["intent"],
        reason=state["handoff_reason"],
        original_route=state.get("handoff_original_route"),
        context=state.get("handoff_context"),
    )
    return {"reply": reply}


async def human_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    state["stage_latencies"].setdefault("template_ms", 0)
    state["stage_latencies"].setdefault("rag_ms", 0)
    plan = state["plan"]
    reply = await build_handoff_reply(
        message=state["message"],
        intent=state["intent"],
        reason=plan.reason or state["intent"].reason or "human_required",
        original_route=plan.original_route or state["intent"].route,
    )
    return {"reply": reply}


def chitchat_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    state["stage_latencies"].setdefault("template_ms", 0)
    state["stage_latencies"].setdefault("rag_ms", 0)
    return {"reply_spec": _reply_spec(state, build_chitchat_reply(state["intent"]))}


async def persona_render_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    started = time.perf_counter()
    spec = state["reply_spec"]
    try:
        rendered = await render_persona_reply(
            spec=spec,
            context=state["persona_context"],
            current_message=state["message"].message,
        )
    except Exception as exc:
        rendered = spec.model_copy(
            update={
                "metadata": {
                    **spec.metadata,
                    "persona": {
                        "persona_id": state["persona_context"].persona_id,
                        "version": state["persona_context"].persona_version,
                        "mode": state["persona_context"].mode,
                        "render_mode": spec.render_mode,
                        "fallback": type(exc).__name__,
                    },
                }
            }
        )
    state["stage_latencies"]["persona_render_ms"] = _elapsed_ms(started)
    return {"reply_spec": rendered}


def reply_guard_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    started = time.perf_counter()
    guarded = guard_reply_spec(
        spec=state["reply_spec"],
        context=state["persona_context"],
    )
    reply = finalize_reply_spec(guarded)
    state["stage_latencies"]["persona_guard_ms"] = _elapsed_ms(started)
    return {"reply": reply}


def _route_reply(state: ReplyWorkflowState) -> str:
    if state["intent"].primary_goal == "request_material":
        return "template"
    action = state["plan"].action
    if action == "template_reply":
        return "template"
    if action == "rag_answer":
        return "rag"
    if action == "human":
        return "human"
    if action == "chitchat":
        return "chitchat"
    return "human"


def _after_reply_node(state: ReplyWorkflowState) -> str:
    if state.get("reply_spec") is not None:
        return "persona_render"
    return "handoff"


@lru_cache
def _compiled_graph():
    graph = StateGraph(ReplyWorkflowState)
    graph.add_node("persona_context", persona_context_node)
    graph.add_node("route_reply", route_reply_node)
    graph.add_node("template", template_node)
    graph.add_node("rag", rag_node)
    graph.add_node("handoff", handoff_node)
    graph.add_node("human", human_node)
    graph.add_node("chitchat", chitchat_node)
    graph.add_node("persona_render", persona_render_node)
    graph.add_node("reply_guard", reply_guard_node)

    graph.add_edge(START, "persona_context")
    graph.add_edge("persona_context", "route_reply")
    graph.add_conditional_edges(
        "route_reply",
        _route_reply,
        {
            "template": "template",
            "rag": "rag",
            "human": "human",
            "chitchat": "chitchat",
        },
    )
    graph.add_conditional_edges(
        "template",
        _after_reply_node,
        {"persona_render": "persona_render", "handoff": "handoff"},
    )
    graph.add_conditional_edges(
        "rag",
        _after_reply_node,
        {"persona_render": "persona_render", "handoff": "handoff"},
    )
    graph.add_edge("handoff", END)
    graph.add_edge("human", END)
    graph.add_edge("chitchat", "persona_render")
    graph.add_edge("persona_render", "reply_guard")
    graph.add_edge("reply_guard", END)
    return graph.compile()


async def execute_reply_plan(
    *,
    plan: ReplyPlan,
    intent: IntentResult,
    message,
    user_state,
    stage_latencies: dict[str, int],
) -> FinalReply:
    result = await _compiled_graph().ainvoke(
        {
            "plan": plan,
            "intent": intent,
            "message": message,
            "user_state": user_state,
            "stage_latencies": stage_latencies,
        }
    )
    return result["reply"]


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _is_rag_no_answer(rag_result: dict) -> bool:
    answer = (rag_result.get("answer") or "").strip()
    return not answer or answer in {
        "__HANDOFF__",
        "知识库中没有找到明确答案。",
    }


def _reply_spec(state: ReplyWorkflowState, reply: FinalReply) -> ReplySpec:
    spec = build_reply_spec(
        reply=reply,
        plan=state["plan"],
        user_state=state["user_state"],
    )
    return spec.model_copy(
        update={
            "metadata": {
                **spec.metadata,
                "persona_original_copy": reply.answer,
            }
        }
    )
