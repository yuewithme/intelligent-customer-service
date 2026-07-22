from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from app.domains.sales.talk_script.human_handoff_service import request_human_handoff
from app.domains.sales.talk_script.llm_question_classifier import classify_question
from app.domains.sales.talk_script.matcher import match_scene, retrieve_candidate_questions
from app.domains.sales.talk_script.models import ClassifierDecision, TalkScriptMatchResult
from app.domains.sales.talk_script.normalizer import normalize_message
from app.domains.sales.talk_script.repository import (
    get_active_question,
    get_active_template,
    has_sent_template_to_customer,
    list_active_questions,
    list_active_scenes,
    record_match_log,
)


Classifier = Callable[..., Awaitable[ClassifierDecision]]

CRITICAL_HUMAN_REASONS = (
    "human",
    "manual",
    "refund",
    "return",
    "cancel",
    "complaint",
    "report",
    "compensation",
    "resend",
    "replace",
    "人工",
    "退款",
    "退货",
    "取消",
    "投诉",
    "举报",
    "赔付",
    "赔偿",
    "补发",
    "换货",
)


async def match_talk_script(
    *,
    customer_id: str,
    current_message: str,
    trace_id: str | None = None,
    session_id: str | None = None,
    recent_messages: list[str] | None = None,
    customer_tags: dict | None = None,
    sales_stage: str | None = None,
    classifier: Classifier | None = None,
) -> TalkScriptMatchResult:
    normalized_message = normalize_message(current_message)
    scenes = list_active_scenes()
    scene_id = match_scene(normalized_message, scenes, recent_messages)
    if scene_id is None:
        result = TalkScriptMatchResult(
            status="pass_through",
            reason="no_scene_match",
        )
        _record(
            result=result,
            trace_id=trace_id,
            customer_id=customer_id,
            session_id=session_id,
            current_message=current_message,
            normalized_message=normalized_message,
        )
        return result

    questions = list_active_questions(scene_id)
    candidates = retrieve_candidate_questions(
        normalized_message=normalized_message,
        scene_id=scene_id,
        questions=questions,
    )
    if not candidates:
        result = _pass_through(
            scene_id=scene_id,
            candidate_question_ids=[],
            reason="no_candidate_questions",
        )
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
        )
        return result

    candidate_payload = [candidate.model_dump() for candidate in candidates]
    decision = await (classifier or classify_question)(
        current_message=current_message,
        normalized_message=normalized_message,
        recent_messages=recent_messages or [],
        customer_tags=customer_tags or {},
        candidate_questions=candidate_payload,
    )
    candidate_ids = [candidate.question_id for candidate in candidates]

    if decision.need_human:
        if _is_critical_human_request(current_message, decision.reason):
            result = await _handoff(
                customer_id=customer_id,
                current_message=current_message,
                scene_id=scene_id,
                candidate_question_ids=candidate_ids,
                reason="need_human",
                confidence=decision.confidence,
                need_slot_filling=decision.need_slot_filling,
            )
        else:
            result = _pass_through(
                scene_id=scene_id,
                candidate_question_ids=candidate_ids,
                reason="need_human_non_critical",
                confidence=decision.confidence,
                need_slot_filling=decision.need_slot_filling,
            )
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
            match_reason=decision.reason,
        )
        return result

    if decision.need_slot_filling or not decision.matched:
        reason = "need_slot_filling" if decision.need_slot_filling else "classifier_unmatched"
        result = _pass_through(
            scene_id=scene_id,
            candidate_question_ids=candidate_ids,
            reason=reason,
            confidence=decision.confidence,
            need_slot_filling=decision.need_slot_filling,
        )
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
            match_reason=decision.reason,
        )
        return result

    question = get_active_question(decision.question_id or "")
    if question is None:
        result = _pass_through(
            scene_id=scene_id,
            candidate_question_ids=candidate_ids,
            reason="question_not_found",
            confidence=decision.confidence,
        )
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
            match_reason=decision.reason,
        )
        return result

    if decision.confidence < question.confidence_threshold:
        result = _pass_through(
            scene_id=scene_id,
            candidate_question_ids=candidate_ids,
            reason="confidence_below_threshold",
            confidence=decision.confidence,
        )
        result.question_id = question.question_id
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
            match_reason=decision.reason,
        )
        return result

    template = get_active_template(question.default_template_id)
    if template is None:
        result = _pass_through(
            scene_id=scene_id,
            candidate_question_ids=candidate_ids,
            reason="template_not_found",
            confidence=decision.confidence,
        )
        result.question_id = question.question_id
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
            match_reason=decision.reason,
        )
        return result

    if (
        sales_stage
        and template.sales_stage
        and template.sales_stage != sales_stage
    ):
        result = _pass_through(
            scene_id=scene_id,
            candidate_question_ids=candidate_ids,
            reason="sales_stage_template_mismatch",
            confidence=decision.confidence,
        )
        result.question_id = question.question_id
        result.template_id = template.template_id
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
            match_reason="sales_stage_template_mismatch",
        )
        return result

    if has_sent_template_to_customer(customer_id, template.template_id):
        result = TalkScriptMatchResult(
            status="pass_through",
            scene_id=scene_id,
            question_id=question.question_id,
            template_id=template.template_id,
            answer="",
            confidence=decision.confidence,
            need_slot_filling=False,
            need_human=False,
            reason="template_already_sent",
            candidate_question_ids=candidate_ids,
        )
        _record_result(
            result,
            trace_id,
            customer_id,
            session_id,
            current_message,
            normalized_message,
            match_reason="template_already_sent",
        )
        return result

    result = TalkScriptMatchResult(
        status="matched",
        success=True,
        scene_id=scene_id,
        question_id=question.question_id,
        template_id=template.template_id,
        answer=template.answer_default,
        confidence=decision.confidence,
        need_slot_filling=decision.need_slot_filling,
        need_human=False,
        reason=decision.reason,
        candidate_question_ids=candidate_ids,
    )
    _record_result(
        result,
        trace_id,
        customer_id,
        session_id,
        current_message,
        normalized_message,
        match_reason=decision.reason,
    )
    return result


def _is_critical_human_request(text: str, reason: str | None = None) -> bool:
    del reason
    haystack = (text or "").lower()
    return any(word.lower() in haystack for word in CRITICAL_HUMAN_REASONS)


def _pass_through(
    *,
    scene_id: str | None,
    candidate_question_ids: list[str],
    reason: str,
    confidence: float = 0.0,
    need_slot_filling: bool = False,
) -> TalkScriptMatchResult:
    return TalkScriptMatchResult(
        status="pass_through",
        scene_id=scene_id,
        answer="",
        confidence=confidence,
        need_slot_filling=need_slot_filling,
        need_human=False,
        reason=reason,
        candidate_question_ids=candidate_question_ids,
    )


async def _handoff(
    *,
    customer_id: str,
    current_message: str,
    scene_id: str | None,
    candidate_question_ids: list[str],
    reason: str,
    confidence: float = 0.0,
    need_slot_filling: bool = False,
) -> TalkScriptMatchResult:
    handoff = await request_human_handoff(
        customer_id=customer_id,
        current_message=current_message,
        reason=reason,
        context={
            "scene_id": scene_id,
            "candidate_question_ids": candidate_question_ids,
            "confidence": confidence,
        },
    )
    return TalkScriptMatchResult(
        status="handoff",
        scene_id=scene_id,
        answer="",
        confidence=confidence,
        need_slot_filling=need_slot_filling,
        need_human=True,
        reason=reason,
        candidate_question_ids=candidate_question_ids,
        metadata={"handoff": handoff.model_dump()},
    )


def _record_result(
    result: TalkScriptMatchResult,
    trace_id: str | None,
    customer_id: str,
    session_id: str | None,
    current_message: str,
    normalized_message: str,
    match_reason: str | None = None,
) -> None:
    _record(
        result=result,
        trace_id=trace_id,
        customer_id=customer_id,
        session_id=session_id,
        current_message=current_message,
        normalized_message=normalized_message,
        match_reason=match_reason,
    )


def _record(
    *,
    result: TalkScriptMatchResult,
    trace_id: str | None,
    customer_id: str,
    session_id: str | None = None,
    current_message: str,
    normalized_message: str,
    match_reason: str | None = None,
) -> None:
    record_match_log(
        {
            "trace_id": trace_id,
            "customer_id": customer_id,
            "session_id": session_id,
            "user_message": current_message,
            "normalized_message": normalized_message,
            "status": result.status,
            "scene_id": result.scene_id,
            "candidate_question_ids": result.candidate_question_ids,
            "matched_question_id": result.question_id,
            "template_id": result.template_id,
            "confidence": result.confidence,
            "need_slot_filling": result.need_slot_filling,
            "need_human": result.need_human,
            "final_answer": result.answer,
            "match_reason": match_reason or result.reason,
            "created_at": datetime.now(timezone.utc),
        }
    )
