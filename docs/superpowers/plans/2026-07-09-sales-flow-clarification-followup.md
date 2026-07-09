# Sales Flow Clarification Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make incomplete customer messages handled by natural, sales-stage-aware AI follow-up instead of broad hard handoff, while keeping true human handoff silent for real human takeover.

**Architecture:** Shrink hard handoff rules to true business-risk cases, convert most talk-script/template misses into LLM fallback, and encode “answer + natural 1-2 question follow-up + sales next step” into the RAG/fallback prompt. Do not build a heavy slot-filling state machine in this phase; use lightweight policy helpers and tests around the existing orchestrator graph.

**Tech Stack:** Python, FastAPI service layer, pytest, existing reply workflow graph, talk script matcher, policy engine, RAG prompt builder, evaluation dataset under `docs/evaluation/dataset_v1`.

---

## File Structure

- Modify: `wechat_rag_bot/app/talk_script/service.py`
  - Convert non-critical talk-script handoff outcomes into `pass_through`.
  - Only preserve hard handoff for explicitly risky reasons.

- Modify: `wechat_rag_bot/app/talk_script/llm_question_classifier.py`
  - Update classifier prompt so “苗情严重/信息不足” means “ask naturally / pass through”, not `need_human=true`.

- Modify: `wechat_rag_bot/app/talk_script/matcher.py`
  - Remove care-problem keywords from forced high-risk scene behavior if they cause `S05` hard handoff.

- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
  - Change missing default template behavior from `template_not_matched_to_handoff` to LLM/RAG fallback.
  - Keep explicit human route silent.

- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
  - Mirror graph behavior if the non-graph `_build_reply` path is still used.
  - Keep `build_handoff_reply(answer="")` unchanged.

- Modify: `wechat_rag_bot/app/services/policy_engine.py`
  - Stop routing advanced customer levels to human by default.
  - Restrict `tag_high_risk_to_human` to true high-risk/intended-human cases.

- Modify: `wechat_rag_bot/app/services/tagger_service.py`
  - Ensure normal orchid-care pain words do not become high risk through profile state unless there was a real prior handoff risk.

- Modify: `wechat_rag_bot/app/services/rag_service.py`
  - Add sales-flow-aware natural follow-up instructions to the main and fallback prompts.

- Test: `wechat_rag_bot/tests/test_talk_script.py`
  - Add/adjust tests for pass-through instead of handoff.

- Test: `wechat_rag_bot/tests/test_reply_workflow_graph.py`
  - Add/adjust tests for template miss fallback and natural follow-up path.

- Test: `wechat_rag_bot/tests/test_policy_engine.py`
  - Add tests for narrowed high-risk and advanced-level behavior.

- Test: `wechat_rag_bot/tests/test_rag_service.py`
  - Assert prompt contains sales-stage-aware follow-up constraints.

- Optional evaluation rerun:
  - `python -m wechat_rag_bot.evaluation.run_evaluation ...` or the existing runner command used for the baseline.

---

## Product Rules to Implement

### Reduction-first principle

This change is a cleanup, not a rule expansion.

Do not add another large decision tree, slot-filling framework, or long restrictive prompt. Remove broad handoff paths and let the LLM handle most ordinary care, recommendation, member, and sales replies. Add only the smallest fallback needed to avoid empty replies in non-human cases.

### Human handoff remains silent

Keep:

```python
FinalReply(
    answer="",
    reply_type="human",
    route="human",
    need_human=True,
    next_action="human_handoff",
)
```

This is intentional because the business wants the human sales rep to take over without exposing an AI transition message.

### Keep only six hard handoff cases

Preserve silent handoff only for:

1. explicit human/customer-service request;
2. refund, return, or order cancellation;
3. complaint, report, or strong dissatisfaction;
4. compensation, replacement, or resend request;
5. concrete order/payment/logistics state that requires manual lookup;
6. dangerous drug dosage or chemical mixing.

Everything else should pass to LLM/RAG or ask a short natural follow-up.

### Follow-up style

Keep follow-up lightweight:

```text
信息不足时，先给安全方向，再自然追问 1-2 个关键问题；不要直接转人工。
```

Do not build formal slot collection in this phase. The follow-up should sound like a sales teacher continuing the conversation, not a form.

Sales-flow fit should be implicit and short: pain discovery, recommendation, objection handling, or transaction progress.

---

## Task 1: Narrow Talk-script Handoff

**Files:**
- Modify: `wechat_rag_bot/app/talk_script/service.py`
- Modify: `wechat_rag_bot/app/talk_script/llm_question_classifier.py`
- Test: `wechat_rag_bot/tests/test_talk_script.py`

- [ ] **Step 1: Write failing tests for pass-through on incomplete/non-critical cases**

Add tests near existing handoff tests in `wechat_rag_bot/tests/test_talk_script.py`:

```python
@pytest.mark.asyncio
async def test_match_talk_script_passes_through_when_classifier_needs_slot_filling(talk_script_db):
    from app.talk_script.service import match_talk_script
    from app.talk_script.models import QuestionClassifyResult

    async def classifier(**kwargs):
        del kwargs
        return QuestionClassifyResult(
            matched=False,
            question_id=None,
            confidence=0.0,
            need_slot_filling=True,
            need_human=False,
            reason="信息不足，需要追问",
        )

    result = await match_talk_script(
        customer_id="cust_slot",
        current_message="植料和肥料怎么办？",
        classifier=classifier,
    )

    assert result.status == "pass_through"
    assert result.need_human is False
    assert result.reason == "need_slot_filling"
```

```python
@pytest.mark.asyncio
async def test_match_talk_script_passes_through_when_classifier_marks_care_issue_human(talk_script_db):
    from app.talk_script.service import match_talk_script
    from app.talk_script.models import QuestionClassifyResult

    async def classifier(**kwargs):
        del kwargs
        return QuestionClassifyResult(
            matched=False,
            question_id=None,
            confidence=0.9,
            need_slot_filling=False,
            need_human=True,
            reason="苗情严重",
        )

    result = await match_talk_script(
        customer_id="cust_care_handoff",
        current_message="黑斑黄叶腐苗，去年全军覆没了",
        classifier=classifier,
    )

    assert result.status == "pass_through"
    assert result.need_human is False
    assert result.reason == "need_human_non_critical"
```

```python
@pytest.mark.asyncio
async def test_match_talk_script_keeps_handoff_for_refund_request(talk_script_db):
    from app.talk_script.service import match_talk_script
    from app.talk_script.models import QuestionClassifyResult

    async def classifier(**kwargs):
        del kwargs
        return QuestionClassifyResult(
            matched=False,
            question_id=None,
            confidence=0.95,
            need_slot_filling=False,
            need_human=True,
            reason="明确退款",
        )

    result = await match_talk_script(
        customer_id="cust_refund",
        current_message="我要退款，给我转人工",
        classifier=classifier,
    )

    assert result.status == "handoff"
    assert result.need_human is True
    assert result.reason == "need_human"
```

- [ ] **Step 2: Run targeted tests and confirm failure**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_talk_script.py -q
```

Expected before implementation: the new pass-through tests fail because `need_slot_filling` and `need_human` currently become handoff.

- [ ] **Step 3: Implement minimal critical-human detection helper**

Keep this helper intentionally small. Do not add a broad keyword list for orchid-care symptoms.

In `wechat_rag_bot/app/talk_script/service.py`, add:

```python
CRITICAL_HUMAN_REASONS = ("人工", "退款", "退货", "取消", "投诉", "赔付", "补发", "换货", "订单", "支付", "物流", "药剂剂量")


def _is_critical_human_request(text: str, reason: str | None = None) -> bool:
    haystack = f"{text or ''} {reason or ''}"
    return any(word in haystack for word in CRITICAL_HUMAN_REASONS)
```

- [ ] **Step 4: Convert non-critical classifier failures into pass-through**

In `match_talk_script`, replace the current block:

```python
if decision.need_human or decision.need_slot_filling or not decision.matched:
    reason = (
        "need_human"
        if decision.need_human
        else "need_slot_filling"
        if decision.need_slot_filling
        else "classifier_unmatched"
    )
    result = await _handoff(...)
```

with:

```python
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
        result = TalkScriptMatchResult(
            status="pass_through",
            scene_id=scene_id,
            answer="",
            confidence=decision.confidence,
            need_slot_filling=decision.need_slot_filling,
            need_human=False,
            reason="need_human_non_critical",
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

if decision.need_slot_filling or not decision.matched:
    reason = "need_slot_filling" if decision.need_slot_filling else "classifier_unmatched"
    result = TalkScriptMatchResult(
        status="pass_through",
        scene_id=scene_id,
        answer="",
        confidence=decision.confidence,
        need_slot_filling=decision.need_slot_filling,
        need_human=False,
        reason=reason,
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
```

- [ ] **Step 5: Update classifier prompt wording**

In `wechat_rag_bot/app/talk_script/llm_question_classifier.py`, replace the rule that says severe after-sales/plant condition should prefer `need_human=true` with:

```text
4. 只有用户明确要求人工、退款、退货、赔付、补发、投诉、举报或订单纠纷时，才输出 need_human=true。
5. 黑斑、黄叶、烂根、腐苗、快死了、不开花、不会养，如果只是咨询养护或需要判断原因，不要输出 need_human=true；信息不足时输出 need_slot_filling=true，让后续大模型自然追问。
```

- [ ] **Step 6: Run tests**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_talk_script.py -q
```

Expected: all talk-script tests pass after updating any old assertions that expected low-confidence or slot-filling handoff.

- [ ] **Step 7: Commit**

```powershell
git add wechat_rag_bot/app/talk_script/service.py wechat_rag_bot/app/talk_script/llm_question_classifier.py wechat_rag_bot/tests/test_talk_script.py
git commit -m "fix: narrow talk script human handoff"
```

---

## Task 2: Make Template Miss Fall Back to LLM/RAG

**Files:**
- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Test: `wechat_rag_bot/tests/test_reply_workflow_graph.py`

- [ ] **Step 1: Write failing graph test**

Replace or add near `test_template_reply_missing_default_template_handoffs`:

```python
@pytest.mark.asyncio
async def test_template_reply_missing_default_template_falls_back_to_rag(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {
            "answer": "可以，我先按新手稳妥好养的方向给您挑。您是想先买一盆便宜练手，还是想要开花香味明显一点的？",
            "sources": [],
        }

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_reply",
        intent=_intent("template_reply", primary_intent="order_intent"),
        message=_message("那您推荐一款吧"),
        user_state=_state(),
        stage_latencies={},
    )

    _assert_reply(reply)
    assert reply.route == "rag_answer"
    assert reply.reply_type == "rag"
    assert reply.need_human is False
    assert "推荐" in reply.answer or "挑" in reply.answer
```

- [ ] **Step 2: Run targeted test and confirm failure**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_reply_workflow_graph.py::test_template_reply_missing_default_template_falls_back_to_rag -q
```

Expected before implementation: fails because missing template currently handoffs.

- [ ] **Step 3: Implement graph fallback**

In `wechat_rag_bot/app/services/reply_workflow_graph.py`, change template-miss behavior from:

```python
return {
    "handoff_reason": "template_not_matched_to_handoff",
    "handoff_original_route": "template_reply",
    "handoff_context": None,
}
```

to calling the same RAG fallback path used by `rag_answer`:

```python
from app.services.rag_service import answer_knowledge

rag_result = await answer_knowledge(
    state["message"],
    state["user_state"],
    policy_decision=state.get("policy_decision"),
)
state["stage_latencies"]["rag_ms"] = _elapsed_ms(stage_started)
if _is_rag_no_answer(rag_result):
    return {
        "handoff_reason": "rag_no_answer_to_handoff",
        "handoff_original_route": "template_reply",
        "handoff_context": {"template_miss": True},
    }
return {"reply": build_rag_reply(rag_result, state["intent"])}
```

Use the actual helper names already present in the file; do not duplicate timing helpers if they already exist.

- [ ] **Step 4: Mirror behavior in non-graph orchestrator**

In `wechat_rag_bot/app/services/chat_orchestrator.py`, replace:

```python
return await build_handoff_reply(
    message=message,
    intent=intent,
    reason="template_not_matched_to_handoff",
    original_route="template_reply",
)
```

with:

```python
from app.services.rag_service import answer_knowledge

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
        original_route="template_reply",
        context={"template_miss": True},
    )
return build_rag_reply(rag_result, intent)
```

- [ ] **Step 5: Run graph tests**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_reply_workflow_graph.py -q
```

Expected: graph tests pass after updating old test expectations.

- [ ] **Step 6: Commit**

```powershell
git add wechat_rag_bot/app/services/reply_workflow_graph.py wechat_rag_bot/app/services/chat_orchestrator.py wechat_rag_bot/tests/test_reply_workflow_graph.py
git commit -m "fix: fallback to llm on template miss"
```

---

## Task 3: Add Minimal Natural Follow-up Prompting

**Files:**
- Modify: `wechat_rag_bot/app/services/rag_service.py`
- Test: `wechat_rag_bot/tests/test_rag_service.py`

- [ ] **Step 1: Add minimal prompt tests**

In `wechat_rag_bot/tests/test_rag_service.py`, add:

```python
def test_rag_prompt_uses_minimal_natural_followup_rule():
    from app.services import rag_service

    prompt = rag_service.PROMPT_TEMPLATE.format(
        context="兰花养护资料",
        question="黑斑黄叶腐苗，去年全军覆没",
    )

    assert "信息不足时" in prompt
    assert "自然追问 1-2 个" in prompt
    assert "不要直接转人工" in prompt
```

- [ ] **Step 2: Run prompt test and confirm failure**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_rag_service.py::test_rag_prompt_uses_minimal_natural_followup_rule -q
```

Expected before implementation: fails because the compact rule is not present.

- [ ] **Step 3: Add one compact rule to both prompts**

In `PROMPT_TEMPLATE` and `LLM_FALLBACK_PROMPT_TEMPLATE`, add only this short rule:

```text
信息不足时，先给安全方向，再结合当前销售意图自然追问 1-2 个关键问题；不要像表单，也不要直接转人工。
```

Do not add detailed stage lists or field-collection rules.

- [ ] **Step 4: Run prompt tests**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_rag_service.py -q
```

Expected: prompt tests pass.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/services/rag_service.py wechat_rag_bot/tests/test_rag_service.py
git commit -m "feat: add minimal natural followup prompt"
```

---

## Task 4: Narrow Tag Policy High-risk Handoff

**Files:**
- Modify: `wechat_rag_bot/app/services/policy_engine.py`
- Modify: `wechat_rag_bot/app/services/tagger_service.py`
- Test: `wechat_rag_bot/tests/test_policy_engine.py`
- Test: `wechat_rag_bot/tests/test_customer_level_policy.py`

- [ ] **Step 1: Add tests for advanced customer and care pain words**

In `wechat_rag_bot/tests/test_policy_engine.py`, add:

```python
@pytest.mark.asyncio
async def test_advanced_customer_level_uses_rag_not_default_handoff():
    from app.schemas.tag import TagResult
    from app.services.policy_engine import decide_policy

    tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="advanced",
        emotion="neutral",
        stage="pain_confirmed",
        risk_level="normal",
        confidence=0.9,
        labels=["customer_tag:L5 宗师期"],
        reason="高级客户咨询养护",
    )

    decision = await decide_policy(tag)

    assert decision.route == "rag_answer"
    assert decision.next_action is None
    assert decision.reason != "advanced_customer_level_to_human"
```

```python
@pytest.mark.asyncio
async def test_high_risk_policy_only_handoffs_when_tag_route_is_human():
    from app.schemas.tag import TagResult
    from app.services.policy_engine import decide_policy

    tag = TagResult(
        intent="care_question",
        route="rag_answer",
        segment="unknown",
        emotion="anxious",
        stage="after_sale",
        risk_level="normal",
        confidence=0.85,
        labels=["pain_point:烂根", "pain_point:黄叶"],
        reason="严重养护问题但没有退款投诉",
    )

    decision = await decide_policy(tag)

    assert decision.route == "rag_answer"
    assert decision.next_action is None
```

- [ ] **Step 2: Run policy tests and confirm failure**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_policy_engine.py tests/test_customer_level_policy.py -q
```

Expected before implementation: advanced-level tests may fail because advanced levels route to human.

- [ ] **Step 3: Change advanced-level policy to prompt style, not handoff**

In `wechat_rag_bot/app/services/policy_engine.py`, replace the advanced-level handoff block:

```python
if advanced_level is not None:
    return PolicyDecision(
        route="human",
        action="human",
        reason="advanced_customer_level_to_human",
        original_route=tag.route,
        next_action="human_handoff",
        template_ids=[f"handoff_customer_level_{advanced_level.lower()}"],
    )
```

with:

```python
if advanced_level is not None and tag.route != "human" and tag.risk_level != "high":
    return PolicyDecision(
        route="rag_answer",
        action="rag_answer",
        reason="advanced_customer_level_professional_rag",
        original_route=tag.route,
        prompt_block_ids=[
            "base.customer_service",
            "tone.concise_professional",
            *customer_level_prompt_blocks,
            *business_tag_prompt_blocks,
            "output.customer_reply",
        ],
        context_policy={
            "recent_turns": 6,
            "include_profile_summary": True,
            "include_long_memory_summary": True,
        },
        retrieval_policy={},
    )
```

- [ ] **Step 4: Restrict high-risk handoff**

Keep this behavior:

```python
if tag.risk_level == "high" or tag.route == "human":
    return PolicyDecision(route="human", ...)
```

but ensure `tagger_service._risk_from` only returns `high` from:

```python
intent.need_human
intent.primary_intent in {"complaint", "refund_request", "human_request"}
```

Do not set high risk from orchid-care words alone.

- [ ] **Step 5: Run policy tests**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_policy_engine.py tests/test_customer_level_policy.py tests/test_tagger_service.py -q
```

Expected: tests pass after updating old tests that asserted advanced default handoff.

- [ ] **Step 6: Commit**

```powershell
git add wechat_rag_bot/app/services/policy_engine.py wechat_rag_bot/app/services/tagger_service.py wechat_rag_bot/tests/test_policy_engine.py wechat_rag_bot/tests/test_customer_level_policy.py
git commit -m "fix: narrow tag policy human handoff"
```

---

## Task 5: Evaluation-focused Regression Tests

**Files:**
- Modify: `wechat_rag_bot/tests/test_reply_workflow_graph.py`
- Optional: Create `wechat_rag_bot/tests/test_sales_followup_eval_cases.py`

- [ ] **Step 1: Add tests modeled after zero-score eval cases**

Create `wechat_rag_bot/tests/test_sales_followup_eval_cases.py`:

```python
import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.talk_script.models import TalkScriptMatchResult


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_eval",
        channel="api",
        user_id="eval_user",
        session_id="eval_session",
        message=text,
        kb_id="kb_default",
    )


def _state() -> UserState:
    return UserState(user_id="eval_user", session_id="eval_session")


def _intent(route: str, primary_intent: str) -> IntentResult:
    return IntentResult(
        route=route,
        primary_intent=primary_intent,
        confidence=0.9,
        reason="eval_case",
    )


@pytest.mark.asyncio
async def test_disease_case_generates_followup_not_handoff(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {
            "answer": "黑斑、黄叶、腐苗通常要结合浇水、植料和通风判断，先别急着继续用药。您方便拍一下叶片和盆面吗？再告诉我平时大概多久浇一次水。",
            "sources": [],
        }

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="rag_answer",
        intent=_intent("rag_answer", "care_question"),
        message=_message("经常有黑斑、黄叶和腐苗，去年买的现在全军覆没了。"),
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.route == "rag_answer"
    assert reply.need_human is False
    assert reply.answer
    assert "拍" in reply.answer


@pytest.mark.asyncio
async def test_recommend_short_sentence_asks_sales_qualifying_question(monkeypatch):
    from app.services import reply_workflow_graph

    async def pass_talk_script(**kwargs):
        del kwargs
        return TalkScriptMatchResult(status="pass_through")

    async def answer_knowledge(message, user_state, policy_decision=None):
        del message, user_state, policy_decision
        return {
            "answer": "可以，我先按新手稳妥好养的方向给您挑。您是想先买一盆便宜练手，还是想要开花香味明显一点的？",
            "sources": [],
        }

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", pass_talk_script)
    monkeypatch.setattr("app.services.rag_service.answer_knowledge", answer_knowledge)

    reply = await reply_workflow_graph.build_reply_with_graph(
        route="template_reply",
        intent=_intent("template_reply", "order_intent"),
        message=_message("那您推荐一款吧。"),
        user_state=_state(),
        stage_latencies={},
    )

    assert reply.route == "rag_answer"
    assert reply.need_human is False
    assert "新手" in reply.answer or "练手" in reply.answer
```

- [ ] **Step 2: Run regression tests**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_sales_followup_eval_cases.py -q
```

Expected: pass.

- [ ] **Step 3: Run focused suite**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_talk_script.py tests/test_reply_workflow_graph.py tests/test_policy_engine.py tests/test_rag_service.py tests/test_sales_followup_eval_cases.py -q
```

Expected: pass.

- [ ] **Step 4: Commit**

```powershell
git add wechat_rag_bot/tests/test_sales_followup_eval_cases.py
git commit -m "test: cover sales followup eval cases"
```

---

## Task 6: Re-run the Sales Evaluation and Compare

**Files:**
- Read: `docs/evaluation/dataset_v1/single_turn.jsonl`
- Read: `docs/evaluation/dataset_v1/multi_turn.jsonl`
- Create: `evaluation_results/followup_handoff_narrowing_2026-07-09/`

- [ ] **Step 1: Start local service using existing dev settings**

Run from `wechat_rag_bot`:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Expected: app starts without import errors.

- [ ] **Step 2: Run evaluation**

Use the existing evaluation runner command pattern from the baseline. If the runner supports output directory:

```powershell
cd wechat_rag_bot
python -m wechat_rag_bot.evaluation.run_evaluation --base-url http://127.0.0.1:8000 --output-dir ../evaluation_results/followup_handoff_narrowing_2026-07-09
```

If the module path differs, run the existing committed runner script directly:

```powershell
cd wechat_rag_bot
python evaluation/run_evaluation.py --base-url http://127.0.0.1:8000 --output-dir ../evaluation_results/followup_handoff_narrowing_2026-07-09
```

- [ ] **Step 3: Compare specific zero-score IDs**

Check these IDs improved from empty handoff:

```text
c01_n02_symptom_probe
c04_n02_disease_response
c07_n04_urgent_action
c01_n06_member_offer
c02_n07_member_policy
c05_n06_value_objection
c06_n05_member_policy
c08_n04_beginner_recommendation
c09_n03_product_recommend
c09_n04_bundle
m01_disease_to_member
m06_reflowering
```

Success criteria:

- empty answer count decreases materially;
- non-critical care/recommendation/member cases do not return `route=human`;
- true human handoff cases still return `answer=""`;
- mean score improves versus baseline `17.92`;
- no increase in critical error rate from unsafe over-answering.

- [ ] **Step 4: Document results**

Create `evaluation_results/followup_handoff_narrowing_2026-07-09/analysis.md` with:

```markdown
# Follow-up and Handoff Narrowing Evaluation

## Summary

- Baseline mean:
- New mean:
- Baseline empty answer count:
- New empty answer count:
- Baseline human route count:
- New human route count:
- Critical error rate:

## Recovered cases

| ID | Before | After | Note |
| --- | --- | --- | --- |

## Remaining failures

| ID | Failure type | Next action |
| --- | --- | --- |

## Decision

Proceed / hold / needs another fix.
```

- [ ] **Step 5: Commit evaluation report if no secrets**

```powershell
git add evaluation_results/followup_handoff_narrowing_2026-07-09/analysis.md
git commit -m "test: evaluate narrowed handoff followup behavior"
```

---

## Final Verification

- [ ] **Step 1: Run targeted tests**

```powershell
cd wechat_rag_bot
python -m pytest tests/test_talk_script.py tests/test_reply_workflow_graph.py tests/test_policy_engine.py tests/test_rag_service.py tests/test_sales_followup_eval_cases.py -q
```

- [ ] **Step 2: Run dataset validator**

```powershell
node docs/evaluation/dataset_v1/validate_dataset.mjs
```

- [ ] **Step 3: Run Git checks**

```powershell
git diff --check
git status --short
```

- [ ] **Step 4: Review staged diff before final commit/push**

```powershell
git diff --cached
```

Check that no `.env`, database files, API keys, tokens, or unrelated user changes are staged.

---

## Self-review

Spec coverage:

- Shrink talk-script hard handoff: Task 1.
- Template miss fallback: Task 2.
- Natural, non-formal follow-up: Task 3.
- Sales-flow-aware follow-up: Task 3 and Task 5.
- Keep silent human handoff: Product rules and Tasks 1-2.
- Add information follow-up: Tasks 1, 3, 5.
- Narrow tag/high-risk policy: Task 4.
- Use evaluation set to verify: Task 6.

Placeholder scan:

- No TBD/TODO placeholders.
- All implementation tasks name exact files and commands.

Type consistency:

- Uses existing `TalkScriptMatchResult`, `QuestionClassifyResult`, `PolicyDecision`, `FinalReply`, `IntentResult`, `NormalizedMessage`, and `UserState`.
- Uses existing route names: `pass_through`, `handoff`, `rag_answer`, `template_reply`, `human`.
