# Unified Reply Decision Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current multi-layer route overrides and duplicate reply executors with one typed `ReplyPlan`, while preventing raw business-state fields from reaching customers.

**Architecture:** Keep the FastAPI modular monolith and existing intent, tag, policy, RAG, talk-script, and handoff services. Treat their outputs as evidence, resolve them once in a pure planner, and execute the resulting `ReplyPlan` only through LangGraph. Business facts remain grounded inputs and are rendered into customer language by a dedicated service instead of changing routes inside the orchestrator.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, LangGraph, SQLAlchemy, pytest, existing evaluation runner.

---

## Scope and success checks

This plan covers the first and highest-value optimization project only: the reply decision core. Database replacement, distributed events, and worker separation are independent projects and should begin only after this plan passes its rollout gate.

The change is successful when all of the following are true:

- `handle_chat()` asks one planner for one `ReplyPlan`; it no longer contains policy precedence or the `structured_business_context` route override.
- LangGraph is the only reply executor; `_build_reply()` and `REPLY_GRAPH_ENABLED` are removed.
- `reply_workflow_graph.py` no longer imports private helpers from `chat_orchestrator.py`.
- Raw keys such as `payment_status`, `order_lookup`, `activity_status`, and `shipping_date_change_executed` do not appear in customer answers.
- Refund requests still produce an empty answer with `need_human=true` and `next_action="human_handoff"`.
- The three boundary cases `b05_price_changed`, `b06_order_not_found`, and `b07_payment_failed` produce grounded customer language.
- The full Python test suite passes.
- Dataset v2 completes all 55 cases, has zero internal-field leaks, introduces no new severe-error cases, and does not reduce the existing 34.68 average score. The first quality target after structural parity is an average score of at least 50.

## Target information flow

```text
NormalizedMessage + UserState
          |
          v
rule / intent / tag / sales-stage evidence
          |
          v
      reply_planner.plan_reply()
          |
          v
        ReplyPlan
  action + facts + constraints + trace
          |
          v
 reply_workflow_graph.execute_reply_plan()
          |
          +--> talk script / template
          +--> grounded business renderer
          +--> RAG
          +--> handoff / clarify / chitchat
          |
          v
        FinalReply
          |
          v
state update + conversation + chat log
```

## File map

- Create `wechat_rag_bot/app/schemas/reply_plan.py`: typed planner input/output contract and decision trace.
- Create `wechat_rag_bot/app/services/reply_planner.py`: the only place that resolves policy precedence.
- Create `wechat_rag_bot/app/services/business_reply_renderer.py`: grounded customer-facing rendering for snapshots and tool state.
- Create `wechat_rag_bot/tests/test_reply_planner.py`: pure decision-table tests.
- Create `wechat_rag_bot/tests/test_business_reply_renderer.py`: leak, grounding, and boundary tests.
- Modify `wechat_rag_bot/app/services/business_context_service.py`: return facts only; remove `to_reply()`.
- Modify `wechat_rag_bot/app/services/chat_orchestrator.py`: collect evidence, call planner, execute one plan, remove legacy executor.
- Modify `wechat_rag_bot/app/services/reply_workflow_graph.py`: accept `ReplyPlan`, own helper functions, add grounded-business execution.
- Modify `wechat_rag_bot/app/services/template_reply_service.py`: stop short-circuiting on business context.
- Modify `wechat_rag_bot/app/services/llm_service.py`: add a `business` model purpose using existing fallback behavior.
- Modify `wechat_rag_bot/app/config.py`: add optional business-renderer provider/model settings.
- Modify `wechat_rag_bot/app/services/chat_log_service.py` and `wechat_rag_bot/app/schemas/log.py`: persist the decision trace in metadata without changing the public response contract.
- Modify existing reply graph, orchestrator, business-context, contract, and evaluation tests.

### Task 1: Lock current public contracts and failing business regressions

**Files:**
- Modify: `wechat_rag_bot/tests/test_business_context_service.py`
- Modify: `wechat_rag_bot/tests/test_contracts.py`
- Create: `wechat_rag_bot/tests/test_business_reply_renderer.py`

- [ ] **Step 1: Replace the raw-context expectation with customer-language regression tests**

Add these cases to `tests/test_business_reply_renderer.py`:

```python
import pytest

from app.schemas.event import NormalizedMessage


def _message(text: str, tool_state: dict) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="trace_business",
        channel="api",
        user_id="user_business",
        session_id="session_business",
        message=text,
        kb_id="kb_default",
        metadata={"tool_state": tool_state},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "tool_state", "must_contain"),
    [
        ("还是按66元下单吧。", {"current_bundle_price": 72, "historical_price": 66}, "72"),
        ("我已经下单了，帮我开课程。", {"order_lookup": "not_found"}, "没有查询到"),
        ("我刚付过了，怎么还显示失败？", {"payment_status": "failed"}, "支付失败"),
    ],
)
async def test_business_reply_uses_customer_language(text, tool_state, must_contain):
    from app.services.business_reply_renderer import render_business_reply

    reply = await render_business_reply(_message(text, tool_state))

    assert must_contain in reply.answer
    assert not any(key in reply.answer for key in tool_state)
    assert reply.route == "template_reply"
    assert reply.need_human is False
```

- [ ] **Step 2: Preserve the refund handoff contract**

Add this assertion to `tests/test_contracts.py` using the file's imported `TestClient` and `app`:

```python
def test_refund_contract_keeps_empty_handoff_reply():
    response = TestClient(app).post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "refund_contract_user",
            "message": "会员我不想要了，怎么退款？",
            "metadata": {"tool_state": {"order_status": "paid", "refund_policy": "未提供"}},
        },
    )
    data = response.json()["data"]
    assert data["answer"] == ""
    assert data["need_human"] is True
    assert data["next_action"] == "human_handoff"
```

- [ ] **Step 3: Run the new regression test and verify it fails for the missing renderer**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_business_reply_renderer.py -q
```

Expected: collection or import failure stating that `app.services.business_reply_renderer` does not exist.

- [ ] **Step 4: Run the existing public-contract test before structural changes**

Run:

```powershell
python -m pytest tests/test_contracts.py -q
```

Expected: all existing contract tests pass; if the new refund test exposes an existing mismatch, record that test as a required compatibility fix in Task 5 rather than weakening it.

### Task 2: Introduce the typed ReplyPlan contract

**Files:**
- Create: `wechat_rag_bot/app/schemas/reply_plan.py`
- Create: `wechat_rag_bot/tests/test_reply_planner.py`

- [ ] **Step 1: Write serialization and immutability tests**

Create `tests/test_reply_planner.py` with:

```python
from app.schemas.reply_plan import BusinessFacts, DecisionStep, ReplyPlan


def test_reply_plan_keeps_facts_constraints_and_trace_separate():
    plan = ReplyPlan(
        action="template_reply",
        original_route="clarify",
        reason="business_facts_available",
        business_facts=BusinessFacts(tool_state={"payment_status": "failed"}),
        decision_trace=[
            DecisionStep(source="base_policy", proposed_action="clarify", reason="low_confidence"),
            DecisionStep(source="planner", proposed_action="template_reply", reason="business_facts_available"),
        ],
    )

    assert plan.business_facts.tool_state == {"payment_status": "failed"}
    assert plan.decision_trace[-1].source == "planner"
    assert plan.model_dump()["action"] == "template_reply"
```

- [ ] **Step 2: Run the contract test and verify it fails**

Run:

```powershell
python -m pytest tests/test_reply_planner.py::test_reply_plan_keeps_facts_constraints_and_trace_separate -q
```

Expected: import failure for `app.schemas.reply_plan`.

- [ ] **Step 3: Implement the contract**

Create `app/schemas/reply_plan.py`:

```python
from typing import Any, Literal

from pydantic import BaseModel, Field


ReplyAction = Literal[
    "template_reply",
    "rag_answer",
    "human",
    "chitchat",
    "unsupported",
    "clarify",
]


class BusinessFacts(BaseModel):
    snapshot: str = ""
    tool_state: dict[str, Any] = Field(default_factory=dict)
    skus: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.snapshot or self.tool_state or self.skus)


class DecisionStep(BaseModel):
    source: str
    proposed_action: str
    reason: str
    accepted: bool = True


class ReplyPlan(BaseModel):
    action: ReplyAction
    original_route: str | None = None
    reason: str
    need_human: bool = False
    next_action: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    template_ids: list[str] = Field(default_factory=list)
    prompt_block_ids: list[str] = Field(default_factory=list)
    context_policy: dict[str, Any] = Field(default_factory=dict)
    retrieval_policy: dict[str, Any] = Field(default_factory=dict)
    business_facts: BusinessFacts = Field(default_factory=BusinessFacts)
    decision_trace: list[DecisionStep] = Field(default_factory=list)
```

- [ ] **Step 4: Run the new contract test**

Run:

```powershell
python -m pytest tests/test_reply_planner.py::test_reply_plan_keeps_facts_constraints_and_trace_separate -q
```

Expected: `1 passed`.

### Task 3: Make policy precedence a pure, tested decision table

**Files:**
- Create: `wechat_rag_bot/app/services/reply_planner.py`
- Modify: `wechat_rag_bot/tests/test_reply_planner.py`

- [ ] **Step 1: Add precedence tests**

Append to `tests/test_reply_planner.py`:

```python
from app.schemas.policy import PolicyDecision
from app.schemas.reply_plan import BusinessFacts
from app.services.reply_planner import resolve_reply_plan


def _decision(route: str, reason: str, **updates) -> PolicyDecision:
    return PolicyDecision(route=route, reason=reason, **updates)


def test_explicit_human_requirement_has_highest_precedence():
    plan = resolve_reply_plan(
        base=_decision("human", "human_required", next_action="human_handoff"),
        tagged=_decision("rag_answer", "advanced_customer_level_professional_rag"),
        facts=BusinessFacts(snapshot="会员39.9元"),
    )
    assert plan.action == "human"
    assert plan.need_human is True
    assert plan.next_action == "human_handoff"


def test_specific_tag_policy_overrides_base_policy_once():
    plan = resolve_reply_plan(
        base=_decision("template_reply", "template_intent"),
        tagged=_decision(
            "rag_answer",
            "beginner_orchid_care_policy",
            knowledge_base_ids=["kb_orchid_basic"],
        ),
        facts=BusinessFacts(),
    )
    assert plan.action == "rag_answer"
    assert plan.knowledge_base_ids == ["kb_orchid_basic"]


def test_business_facts_are_attached_without_overriding_a_human_route():
    plan = resolve_reply_plan(
        base=_decision("human", "human_required", next_action="human_handoff"),
        tagged=_decision("human", "tag_high_risk_to_human", next_action="human_handoff"),
        facts=BusinessFacts(tool_state={"order_status": "paid"}),
    )
    assert plan.action == "human"
    assert plan.business_facts.tool_state == {"order_status": "paid"}


def test_business_facts_do_not_replace_a_knowledge_route():
    plan = resolve_reply_plan(
        base=_decision("rag_answer", "knowledge_intent"),
        tagged=_decision("rag_answer", "default_tag_policy"),
        facts=BusinessFacts(snapshot="会员39.9元"),
    )
    assert plan.action == "rag_answer"
```

- [ ] **Step 2: Run the planner tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_reply_planner.py -q
```

Expected: import failure for `app.services.reply_planner`.

- [ ] **Step 3: Implement the pure resolver**

Create `app/services/reply_planner.py`:

```python
from app.schemas.policy import PolicyDecision
from app.schemas.reply_plan import BusinessFacts, DecisionStep, ReplyPlan


def resolve_reply_plan(
    *,
    base: PolicyDecision,
    tagged: PolicyDecision,
    facts: BusinessFacts,
) -> ReplyPlan:
    trace = [
        DecisionStep(
            source="base_policy",
            proposed_action=base.route,
            reason=base.reason or "base_policy",
        ),
        DecisionStep(
            source="tag_policy",
            proposed_action=tagged.route,
            reason=tagged.reason or "tag_policy",
            accepted=tagged.reason != "default_tag_policy",
        ),
    ]

    if base.route == "human" or tagged.route == "human":
        selected = tagged if tagged.route == "human" else base
        action = "human"
    elif tagged.reason != "default_tag_policy":
        selected = tagged
        action = tagged.route
    else:
        selected = base
        action = base.route

    trace.append(
        DecisionStep(
            source="planner",
            proposed_action=action,
            reason=selected.reason or "selected_policy",
        )
    )
    return ReplyPlan(
        action=action,
        original_route=selected.original_route or base.original_route or base.route,
        reason=selected.reason or "selected_policy",
        need_human=action == "human",
        next_action="human_handoff" if action == "human" else selected.next_action,
        knowledge_base_ids=list(selected.knowledge_base_ids),
        template_ids=list(selected.template_ids),
        prompt_block_ids=list(selected.prompt_block_ids),
        context_policy=dict(selected.context_policy),
        retrieval_policy=dict(selected.retrieval_policy),
        business_facts=facts,
        decision_trace=trace,
    )
```

- [ ] **Step 4: Run the planner tests**

Run:

```powershell
python -m pytest tests/test_reply_planner.py -q
```

Expected: all reply planner tests pass.

### Task 4: Convert business context from a reply shortcut into grounded facts

**Files:**
- Modify: `wechat_rag_bot/app/services/business_context_service.py`
- Create: `wechat_rag_bot/app/services/business_reply_renderer.py`
- Modify: `wechat_rag_bot/app/services/template_reply_service.py`
- Modify: `wechat_rag_bot/app/services/llm_service.py`
- Modify: `wechat_rag_bot/app/config.py`
- Modify: `wechat_rag_bot/tests/test_business_context_service.py`
- Modify: `wechat_rag_bot/tests/test_business_reply_renderer.py`

- [ ] **Step 1: Change context tests to expect facts, never a ready reply**

Replace calls to `context.to_reply()` in `tests/test_business_context_service.py` with:

```python
context = await build_business_context(message)
assert context.available is True
assert "东方红荷" in context.snapshot
assert context.tool_state == {}
```

Delete the test that asserts business context bypasses `select_template`; Task 5 will test that execution chooses the renderer from `ReplyPlan`.

- [ ] **Step 2: Make `build_business_context()` return `BusinessFacts`**

In `business_context_service.py`, import `BusinessFacts`, remove the local `BusinessContext` model and its `to_reply()`, and return:

```python
return BusinessFacts(
    snapshot=str(metadata.get("business_snapshot") or "").strip(),
    tool_state=metadata.get("tool_state")
    if isinstance(metadata.get("tool_state"), dict)
    else {},
    skus=list_orchid_skus(variety_names=requested, limit=10) if requested else [],
)
```

- [ ] **Step 3: Add business-specific model routing**

Add these settings to `Settings` in `app/config.py`:

```python
business_llm_provider: str = ""
business_llm_model: str = ""
```

Add this chain to `_resolve_model_config()` in `llm_service.py`:

```python
"business": (
    ("business_llm_provider", "business_llm_model"),
    ("rag_llm_provider", "rag_llm_model"),
),
```

- [ ] **Step 4: Implement a fact-only prompt and output guard**

Create `app/services/business_reply_renderer.py`:

```python
import json

from app.schemas.reply import FinalReply
from app.schemas.reply_plan import BusinessFacts
from app.services.llm_service import generate_answer


INTERNAL_KEYS = {
    "payment_status",
    "order_lookup",
    "activity_status",
    "shipping_date_change_executed",
    "course_status",
}


def build_business_prompt(*, question: str, facts: BusinessFacts) -> str:
    payload = json.dumps(facts.model_dump(), ensure_ascii=False, sort_keys=True)
    return f"""你是兰花客服。只根据给定事实回答当前问题。
规则：
1. 不得增加价格、库存、权益、执行结果或时间承诺。
2. false、unknown、unverified、not_found、failed 表示事项未完成或未确认。
3. 不得向客户输出英文内部字段名。
4. 已付款、已执行和已开通必须有明确的肯定事实才能确认。
5. 用简洁自然的中文说明现状和下一步。

【业务事实】
{payload}

【客户当前问题】
{question.strip()}
"""


def _contains_internal_key(answer: str, facts: BusinessFacts) -> bool:
    keys = INTERNAL_KEYS | set(facts.tool_state)
    return any(key in answer for key in keys)


async def render_business_reply(message, facts: BusinessFacts | None = None) -> FinalReply:
    facts = facts or BusinessFacts(
        snapshot=str(message.metadata.get("business_snapshot") or ""),
        tool_state=dict(message.metadata.get("tool_state") or {}),
    )
    result = await generate_answer(
        build_business_prompt(question=message.message, facts=facts),
        purpose="business",
    )
    answer = str(result.get("answer") or "").strip()
    if not answer or _contains_internal_key(answer, facts):
        answer = "当前业务状态需要进一步核验，我会根据查询或操作结果再向您确认。"
    return FinalReply(
        answer=answer,
        reply_type="template",
        route="template_reply",
        usage=dict(result.get("usage") or {}),
        metadata={"business_facts_used": True},
    )
```

- [ ] **Step 5: Make the mock-model regression deterministic**

In `tests/test_business_reply_renderer.py`, monkeypatch `generate_answer` per case so the test asserts the renderer contract independently of the configured model:

```python
async def fake_generate_answer(prompt: str, purpose: str = "rag") -> dict:
    assert purpose == "business"
    if "current_bundle_price" in prompt:
        return {"answer": "历史价格是66元，当前有效价格是72元，请确认是否继续。", "usage": {}}
    if "order_lookup" in prompt:
        return {"answer": "当前没有查询到对应订单，请核对订单号或下单账号。", "usage": {}}
    return {"answer": "系统当前显示支付失败，请先核对是否实际扣款。", "usage": {}}
```

Patch `app.services.business_reply_renderer.generate_answer` before calling the renderer.

- [ ] **Step 6: Run grounded rendering tests**

Run:

```powershell
python -m pytest tests/test_business_context_service.py tests/test_business_reply_renderer.py tests/test_ai_model_routing.py -q
```

Expected: all selected tests pass and no assertion accepts raw internal keys in customer answers.

### Task 5: Make ReplyPlan the only input to LangGraph

**Files:**
- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
- Modify: `wechat_rag_bot/tests/test_reply_workflow_graph.py`
- Modify: `wechat_rag_bot/tests/test_sales_followup_eval_cases.py`

- [ ] **Step 1: Change graph tests to build a ReplyPlan**

Add this helper to `tests/test_reply_workflow_graph.py`:

```python
from app.schemas.reply_plan import BusinessFacts, ReplyPlan


def _plan(action: str, **updates) -> ReplyPlan:
    return ReplyPlan(
        action=action,
        reason="test_plan",
        original_route=action,
        **updates,
    )
```

Change each graph call from `build_reply_with_graph()` to the following concrete entry-point shape, substituting only the action and intent already declared by that test:

```python
reply = await reply_workflow_graph.execute_reply_plan(
    plan=_plan("rag_answer"),
    intent=_intent("rag_answer"),
    message=_message(),
    user_state=_state(),
    stage_latencies={},
)
```

- [ ] **Step 2: Add a grounded-business branch test**

```python
@pytest.mark.asyncio
async def test_plan_with_business_facts_uses_grounded_renderer(monkeypatch):
    from app.services import reply_workflow_graph

    async def render(message, facts):
        assert facts.tool_state == {"payment_status": "failed"}
        return FinalReply(
            answer="系统当前显示支付失败，请先核对扣款状态。",
            reply_type="template",
            route="template_reply",
        )

    monkeypatch.setattr(reply_workflow_graph, "render_business_reply", render)
    reply = await reply_workflow_graph.execute_reply_plan(
        plan=_plan(
            "template_reply",
            business_facts=BusinessFacts(tool_state={"payment_status": "failed"}),
        ),
        intent=_intent("template_reply", primary_intent="payment_intent"),
        message=_message("我刚付过了，怎么还显示失败？"),
        user_state=_state(),
        stage_latencies={},
    )
    assert "支付失败" in reply.answer
```

- [ ] **Step 3: Move shared helpers out of the orchestrator dependency direction**

In `reply_workflow_graph.py`:

- Remove the entire `from app.services.chat_orchestrator import` block that imports `_elapsed_ms`, `_is_rag_no_answer`, `_is_soft_talk_script_handoff`, and `build_handoff_reply`.
- Define local `_elapsed_ms()`, `_is_rag_no_answer()`, and `_is_soft_talk_script_handoff()` helpers with the same behavior covered by existing tests.
- Import `request_human_handoff` and build the handoff `FinalReply` inside the graph module or a new focused `handoff_reply_service.py`; do not import the orchestrator.

The handoff result must retain:

```python
FinalReply(
    answer="",
    reply_type="human",
    route="human",
    need_human=True,
    next_action="human_handoff",
    metadata={"handoff": handoff},
)
```

- [ ] **Step 4: Route by `plan.action` and facts**

Replace the graph state's `route` and `policy_decision` fields with `plan: ReplyPlan`. Before the normal template branch, select grounded rendering only when facts are available:

```python
async def template_node(state: ReplyWorkflowState) -> ReplyWorkflowState:
    plan = state["plan"]
    if plan.business_facts.available:
        reply = await render_business_reply(state["message"], plan.business_facts)
        return {"reply": reply}
    reply = await build_default_template_reply(
        state["message"], state["intent"], state["user_state"]
    )
    return {"reply": reply or build_clarify_reply(state["intent"])}
```

For RAG, convert the plan into the existing `PolicyDecision` shape only at the adapter boundary:

```python
policy = PolicyDecision(
    route=plan.action,
    reason=plan.reason,
    original_route=plan.original_route,
    knowledge_base_ids=plan.knowledge_base_ids,
    template_ids=plan.template_ids,
    prompt_block_ids=plan.prompt_block_ids,
    context_policy=plan.context_policy,
    retrieval_policy=plan.retrieval_policy,
)
```

- [ ] **Step 5: Rename the graph entry point**

Expose exactly this signature:

```python
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
```

- [ ] **Step 6: Run graph tests**

Run:

```powershell
python -m pytest tests/test_reply_workflow_graph.py tests/test_sales_followup_eval_cases.py -q
```

Expected: all graph and sales-follow-up tests pass.

### Task 6: Simplify handle_chat to evidence collection, planning, execution, and commit

**Files:**
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/config.py`
- Modify: `wechat_rag_bot/tests/test_chat_orchestrator_reply_graph.py`
- Modify: `wechat_rag_bot/tests/test_tag_driven_chat_flow.py`

- [ ] **Step 1: Replace graph-toggle tests with one planner/executor contract test**

Delete `test_reply_graph_disabled_uses_legacy_build_reply`. Replace `test_reply_graph_enabled_calls_graph_builder` with this test, reusing `_install_common_orchestrator_fakes()` already defined in the file:

```python
@pytest.mark.asyncio
async def test_orchestrator_executes_the_single_planned_reply(monkeypatch):
    from app.schemas.reply_plan import BusinessFacts
    from app.services import chat_orchestrator

    _install_common_orchestrator_fakes(monkeypatch, chat_orchestrator)
    captured = {}

    monkeypatch.setattr(
        chat_orchestrator,
        "get_settings",
        lambda: SimpleNamespace(intent_example_top_k=5),
    )

    async def build_business_context(message):
        assert message.trace_id == "trace_001"
        return BusinessFacts()

    async def execute_reply_plan(**kwargs):
        captured["plan"] = kwargs["plan"]
        return FinalReply(
            answer="planned answer",
            reply_type="template",
            route="template_reply",
        )

    monkeypatch.setattr(chat_orchestrator, "build_business_context", build_business_context)
    monkeypatch.setattr(chat_orchestrator, "execute_reply_plan", execute_reply_plan)
    result = await chat_orchestrator.handle_chat(
        ChatRequest(
            channel="api",
            user_id="user_001",
            message="hello",
            kb_id="kb_default",
        )
    )
    assert result["answer"] == "planned answer"
    assert captured["plan"].decision_trace
```

- [ ] **Step 2: Import the planner and graph normally**

At module scope in `chat_orchestrator.py`, add:

```python
from app.services.business_context_service import build_business_context
from app.services.reply_planner import resolve_reply_plan
from app.services.reply_workflow_graph import execute_reply_plan
```

The graph no longer imports the orchestrator, so the lazy import is unnecessary.

- [ ] **Step 3: Replace route override logic with one plan**

After base and tag decisions have been computed, use:

```python
facts = await build_business_context(message)
plan = resolve_reply_plan(
    base=decision,
    tagged=rich_decision,
    facts=facts,
)
route = plan.action
routed_intent = intent.model_copy(
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
reply = await execute_reply_plan(
    plan=plan,
    intent=routed_intent,
    message=message,
    user_state=user_state,
    stage_latencies=stage_latencies,
)
reply.metadata["decision"] = {
    "action": plan.action,
    "reason": plan.reason,
    "original_route": plan.original_route,
    "trace": [step.model_dump() for step in plan.decision_trace],
}
```

Delete the `has_business_context()` override block entirely.

- [ ] **Step 4: Remove the legacy executor**

Delete `_build_reply()` from `chat_orchestrator.py`, remove imports used only by it, and remove `reply_graph_enabled` from `Settings`. Search for stale references:

```powershell
rg -n "_build_reply|reply_graph_enabled|REPLY_GRAPH_ENABLED|build_reply_with_graph" wechat_rag_bot
```

Expected: no application references and no obsolete tests.

- [ ] **Step 5: Run orchestrator and tag-flow tests**

Run:

```powershell
python -m pytest tests/test_chat_orchestrator_reply_graph.py tests/test_tag_driven_chat_flow.py tests/test_contracts.py -q
```

Expected: all selected tests pass and public chat fields remain unchanged.

### Task 7: Persist explainability without exposing planner internals to customers

**Files:**
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/services/chat_log_service.py`
- Modify: `wechat_rag_bot/tests/test_chat_logs.py`
- Modify: `wechat_rag_bot/tests/test_tag_driven_chat_flow.py`

- [ ] **Step 1: Add a log regression test**

Add to `tests/test_chat_logs.py`:

```python
def test_chat_log_metadata_keeps_reply_decision_trace_without_business_secrets():
    from app.services.chat_log_service import sanitize_log_payload

    payload = {
        "user_message": "我刚付过了，怎么还显示失败？",
        "answer": "系统当前显示支付失败，请先核对是否实际扣款。",
        "metadata": {
            "decision": {
            "action": "template_reply",
            "reason": "template_intent",
                "trace": [
                {"source": "planner", "proposed_action": "template_reply", "reason": "template_intent"}
            ],
            },
            "authorization": "Bearer secret",
        }
    }
    record = sanitize_log_payload(payload)
    assert record["metadata"]["decision"]["trace"][0]["source"] == "planner"
    assert "business_facts" not in record["metadata"]["decision"]
    assert "authorization" not in record["metadata"]
```

- [ ] **Step 2: Log a reduced planner view**

Before constructing the success log payload, keep only the reduced `reply.metadata["decision"]` structure added in Task 6. Do not add a full `reply_plan` object.

Do not place `business_facts`, raw prompts, tokens, authorization values, or provider payloads in the decision log.

- [ ] **Step 3: Keep the public metadata contract intentional**

In `_to_chat_data()`, exclude the complete `reply_plan` object from customer-facing metadata and retain the existing `policy_decision`, `tag_result`, and sales-action compatibility fields until clients migrate. The admin log may receive `decision`; the chat client must not receive raw tool state through this path.

- [ ] **Step 4: Run logging tests**

Run:

```powershell
python -m pytest tests/test_chat_logs.py tests/test_tag_driven_chat_flow.py -q
```

Expected: all selected tests pass and the reduced decision trace is queryable from logs.

### Task 8: Full verification, evaluation gate, and controlled rollout

**Files:**
- Modify: `wechat_rag_bot/docs/project_framework.md`
- Modify: `wechat_rag_bot/README.md`
- Modify: `docs/evaluation/dataset_v2/README.md`

- [ ] **Step 1: Run formatting and import checks**

Run:

```powershell
cd wechat_rag_bot
python -m compileall app
git diff --check
```

Expected: both commands exit with code 0.

- [ ] **Step 2: Run the full backend test suite**

Run:

```powershell
python -m pytest -q
```

Expected: all tests pass. Do not proceed to evaluation if any test fails.

- [ ] **Step 3: Run the three boundary regressions through the real API**

Run the deterministic renderer regressions that represent `b05_price_changed`, `b06_order_not_found`, and `b07_payment_failed`:

```powershell
python -m pytest tests/test_business_reply_renderer.py -q
```

Expected for all three:

- HTTP 200;
- non-empty customer answer;
- no raw `tool_state` key in the answer;
- no invented successful action;
- `need_human=false`.

- [ ] **Step 4: Run all 55 dataset v2 cases and compare with the saved baseline**

With the API running at `http://127.0.0.1:8000`, execute the repository evaluator and save a new result directory without overwriting the existing baseline:

```powershell
python -m evaluation.run_evaluation `
  --base-url http://127.0.0.1:8000 `
  --dataset-dir ../docs/evaluation/dataset_v2 `
  --output-dir ../evaluation_results/unified_reply_plan_2026-07-10 `
  --concurrency 3 `
  --judge-concurrency 2 `
  --judge-attempts 3 `
  --timeout 120
```

Acceptance checks:

```text
completed cases = 55
internal field leaks = 0
new severe-error cases = 0
average score >= 34.68 for structural parity
target average score >= 50 before deploying the new image to production
```

- [ ] **Step 5: Document the authoritative decision flow**

Update `docs/project_framework.md` and the backend README so they state:

```text
intent/tag/policy services produce evidence
reply_planner is the only precedence resolver
ReplyPlan is the internal execution contract
LangGraph is the only reply executor
business facts never directly become customer text
```

Remove documentation for `REPLY_GRAPH_ENABLED` and the legacy reply path.

- [ ] **Step 6: Review the final diff for scope and secrets**

Run:

```powershell
git diff --check
git diff --stat
git diff -- wechat_rag_bot/app wechat_rag_bot/tests wechat_rag_bot/docs README.md
```

Confirm that generated evaluation output, databases, `.env` files, credentials, and unrelated user changes are not staged.

## Follow-on projects after this plan

These are intentionally separate because each changes a different failure domain.

1. **Private-contact profile persistence**
   - Execute `docs/superpowers/plans/2026-07-10-private-contact-profile-persistence.md` after this reply-decision plan because both modify `chat_orchestrator.py`.
   - Add a provider identity table so every valid Eyun private contact gets one durable profile before conversation recording.
   - Carry the canonical internal user ID through callback, queue, chat, memory, event, and admin flows.
   - Replace per-message in-process profile analysis with a coalesced database refresh job.

2. **Quality policy and evaluation hardening**
   - Convert dataset v2 boundary cases into deterministic API regressions.
   - Add care-answer uncertainty and no-guarantee guards.
   - Add provider retry, timeout budget, and per-call latency/error metadata.
   - Gate releases on severe-error rate, handoff precision, field leakage, and score.

3. **Persistence and worker separation**
   - Introduce Alembic before changing database engines.
   - Move SQLite business data to PostgreSQL.
   - Move volatile user state and SSE fan-out to Redis.
   - Run Eyun inbound/outbound processing as one separately deployed worker.
   - Add claim/lease semantics so two workers cannot process the same row.

4. **Async and production scaling**
   - Replace synchronous SQLAlchemy calls inside async functions with SQLAlchemy async sessions or bounded thread execution.
   - Add readiness checks for the database, Qdrant, and worker lease.
   - Load-test one and two API replicas before enabling horizontal scaling.

## Self-review record

- Spec coverage: the plan addresses the identified primary pain—fragmented precedence, duplicate execution, raw business-state replies, and poor explainability—without expanding into an unrelated rewrite.
- Type consistency: all later tasks use `ReplyPlan.action`, `ReplyPlan.business_facts`, `DecisionStep.proposed_action`, and `execute_reply_plan()` as defined in Tasks 2 and 5.
- Public compatibility: `FinalReply`, `/api/v1/chat`, refund handoff behavior, and existing response route names remain stable.
- Migration safety: the old executor is removed only after graph contract and regression tests are updated; infrastructure migration is deferred to independent plans.
- Rollback safety: retain the previously deployed container image tag and SQLite volume snapshot until the new image passes the production smoke test; rollback uses the prior image rather than retaining two code paths.
