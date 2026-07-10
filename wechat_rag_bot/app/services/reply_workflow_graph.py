import time
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas.intent import IntentResult
from app.schemas.policy import PolicyDecision
from app.schemas.reply import FinalReply
from app.services.chat_orchestrator import (
    _elapsed_ms,
    _is_rag_no_answer,
    _is_soft_talk_script_handoff,
    build_handoff_reply,
)
from app.services.reply_builder import (
    build_chitchat_reply,
    build_clarify_reply,
    build_rag_reply,
    build_unsupported_reply,
)
from app.services.template_reply_service import build_default_template_reply
from app.talk_script.service import match_talk_script


class ReplyWorkflowState(TypedDict, total=False):
    route: str
    intent: IntentResult
    message: Any
    user_state: Any
    policy_decision: PolicyDecision | None
    stage_latencies: dict[str, int]
    reply: FinalReply
    handoff_reason: str
    handoff_original_route: str | None
    handoff_context: dict | None


TALK_SCRIPT_ROUTES = {"template_reply", "template_then_rag"}


async def talk_script_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    route = state["route"]
    stage_latencies = state["stage_latencies"]
    if route not in TALK_SCRIPT_ROUTES:
        stage_latencies.setdefault("talk_script_ms", 0)
        return {}

    message = state["message"]
    user_state = state["user_state"]
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
        return {
            "reply": FinalReply(
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
        }
    if talk_script.status == "handoff" and not _is_soft_talk_script_handoff(
        talk_script.reason
    ):
        stage_latencies.setdefault("template_ms", 0)
        stage_latencies.setdefault("rag_ms", 0)
        return {
            "handoff_reason": talk_script.reason or "talk_script_to_handoff",
            "handoff_original_route": route,
            "handoff_context": {"talk_script": talk_script.model_dump()},
        }
    return {}


def route_reply_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    del state
    return {}


async def template_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    stage_latencies = state["stage_latencies"]
    stage_started = time.perf_counter()
    reply = await build_default_template_reply(
        state["message"],
        state["intent"],
        state["user_state"],
    )
    stage_latencies["template_ms"] = _elapsed_ms(stage_started)
    if reply is not None:
        return {"reply": reply}
    stage_latencies["rag_ms"] = 0
    return {"reply": build_clarify_reply(state["intent"])}


async def rag_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    from app.services.rag_service import answer_knowledge

    stage_latencies = state["stage_latencies"]
    stage_started = time.perf_counter()
    rag_result = await answer_knowledge(
        state["message"],
        state["user_state"],
        policy_decision=state.get("policy_decision"),
    )
    for key, value in rag_result.get("stage_latencies", {}).items():
        stage_latencies[f"rag_{key}"] = value
    stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
    stage_latencies.setdefault("template_ms", 0)
    if _is_rag_no_answer(rag_result):
        return {"reply": build_clarify_reply(state["intent"])}
    return {"reply": build_rag_reply(rag_result, state["intent"])}


async def template_then_rag_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    from app.services.rag_service import answer_knowledge

    stage_latencies = state["stage_latencies"]
    stage_latencies.setdefault("template_ms", 0)

    stage_started = time.perf_counter()
    rag_result = await answer_knowledge(
        state["message"],
        state["user_state"],
        policy_decision=state.get("policy_decision"),
    )
    for key, value in rag_result.get("stage_latencies", {}).items():
        stage_latencies[f"rag_{key}"] = value
    stage_latencies["rag_ms"] = _elapsed_ms(stage_started)
    if _is_rag_no_answer(rag_result):
        return {"reply": build_clarify_reply(state["intent"])}
    return {"reply": build_rag_reply(rag_result, state["intent"])}


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
    reply = await build_handoff_reply(
        message=state["message"],
        intent=state["intent"],
        reason=state["intent"].reason or "human_required",
        original_route=state["intent"].slots.get("original_route") or state["intent"].route,
    )
    return {"reply": reply}


def chitchat_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    state["stage_latencies"].setdefault("template_ms", 0)
    state["stage_latencies"].setdefault("rag_ms", 0)
    return {"reply": build_chitchat_reply(state["intent"])}


def unsupported_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    state["stage_latencies"].setdefault("template_ms", 0)
    state["stage_latencies"].setdefault("rag_ms", 0)
    return {"reply": build_unsupported_reply(state["intent"])}


def clarify_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    state["stage_latencies"].setdefault("template_ms", 0)
    state["stage_latencies"].setdefault("rag_ms", 0)
    return {"reply": build_clarify_reply(state["intent"])}


def _after_talk_script(state: ReplyWorkflowState) -> str:
    if state.get("reply") is not None:
        return END
    if state.get("handoff_reason"):
        return "handoff"
    return "route_reply"


def _route_reply(state: ReplyWorkflowState) -> str:
    route = state["route"]
    if route == "template_reply":
        return "template"
    if route == "rag_answer":
        return "rag"
    if route == "template_then_rag":
        return "template_then_rag"
    if route == "human":
        return "human"
    if route == "chitchat":
        return "chitchat"
    if route == "unsupported":
        return "unsupported"
    return "clarify"


def _after_reply_node(state: ReplyWorkflowState) -> str:
    if state.get("reply") is not None:
        return END
    return "handoff"


@lru_cache
def _compiled_graph():
    graph = StateGraph(ReplyWorkflowState)
    graph.add_node("talk_script", talk_script_node)
    graph.add_node("route_reply", route_reply_node)
    graph.add_node("template", template_node)
    graph.add_node("rag", rag_node)
    graph.add_node("template_then_rag", template_then_rag_node)
    graph.add_node("handoff", handoff_node)
    graph.add_node("human", human_node)
    graph.add_node("chitchat", chitchat_node)
    graph.add_node("unsupported", unsupported_node)
    graph.add_node("clarify", clarify_node)

    graph.add_edge(START, "talk_script")
    graph.add_conditional_edges(
        "talk_script",
        _after_talk_script,
        {END: END, "handoff": "handoff", "route_reply": "route_reply"},
    )
    graph.add_conditional_edges(
        "route_reply",
        _route_reply,
        {
            "template": "template",
            "rag": "rag",
            "template_then_rag": "template_then_rag",
            "human": "human",
            "chitchat": "chitchat",
            "unsupported": "unsupported",
            "clarify": "clarify",
        },
    )
    graph.add_conditional_edges(
        "template",
        _after_reply_node,
        {END: END, "handoff": "handoff"},
    )
    graph.add_conditional_edges(
        "rag",
        _after_reply_node,
        {END: END, "handoff": "handoff"},
    )
    graph.add_conditional_edges(
        "template_then_rag",
        _after_reply_node,
        {END: END, "handoff": "handoff"},
    )
    graph.add_edge("handoff", END)
    graph.add_edge("human", END)
    graph.add_edge("chitchat", END)
    graph.add_edge("unsupported", END)
    graph.add_edge("clarify", END)
    return graph.compile()


async def build_reply_with_graph(
    *,
    route: str,
    intent: IntentResult,
    message,
    user_state,
    stage_latencies: dict[str, int],
    policy_decision: PolicyDecision | None = None,
) -> FinalReply:
    result = await _compiled_graph().ainvoke(
        {
            "route": route,
            "intent": intent,
            "message": message,
            "user_state": user_state,
            "policy_decision": policy_decision,
            "stage_latencies": stage_latencies,
        }
    )
    return result["reply"]
