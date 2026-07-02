# LangGraph Reply Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add LangGraph to the existing intelligent客服主链路 by graphing the reply-building stage while preserving the current API contract, logging, state updates, RAG service, talk-script logic, and human handoff behavior.

**Architecture:** Keep `handle_chat()` as the stable outer orchestrator and replace only the `_build_reply()` decision tree with an equivalent LangGraph-backed workflow behind a feature flag. The graph will call existing services (`match_talk_script`, `select_template`, `answer_knowledge`, `build_handoff_reply`, reply builders) instead of reimplementing business logic.

**Tech Stack:** FastAPI, Pydantic, pytest, LangGraph, existing app services in `app/services` and `app/talk_script`.

---

## Current Main Chain

Relevant current flow:

```text
POST /api/v1/chat
  -> app.routers.chat.chat
  -> app.services.chat_orchestrator.handle_chat
    -> normalize_chat_request
    -> get_user_state
    -> check_rules
    -> retrieve_intent_examples
    -> classify_intent
    -> decide_route
    -> _build_reply
      -> match_talk_script
      -> template / rag / template_then_rag / human / fallback
    -> update_user_state
    -> append_conversation_memory
    -> update_profile_after_chat
    -> record_chat_log
```

The LangGraph integration must start at `_build_reply`, not at the API layer.

## File Structure

- Modify: `requirements.txt`
  - Add `langgraph` with a conservative version range.
- Modify: `app/config.py`
  - Add `reply_graph_enabled: bool = False`.
- Create: `app/services/reply_workflow_graph.py`
  - Own the LangGraph `StateGraph`, graph state, nodes, and route functions.
  - Return the existing `FinalReply` type.
- Modify: `app/services/chat_orchestrator.py`
  - Keep `_build_reply()` as fallback.
  - Add `build_reply_with_graph()` call behind `REPLY_GRAPH_ENABLED`.
- Test: `tests/test_reply_workflow_graph.py`
  - Unit-level tests for graph behavior using monkeypatched existing services.
- Test: `tests/test_chat_orchestrator_reply_graph.py`
  - Integration-level tests proving the feature flag preserves existing API behavior and stage latency fields.
- Docs: `README.md`
  - Add a short configuration note for `REPLY_GRAPH_ENABLED`.

## Phase 1: Dependency And Flag

### Task 1: Add LangGraph Dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependency**

Add:

```text
langgraph>=0.2,<1.0
```

- [ ] **Step 2: Install dependencies**

Run:

```bash
python -m pip install -r requirements.txt
```

Expected: command exits 0.

- [ ] **Step 3: Verify import**

Run:

```bash
python -c "from langgraph.graph import StateGraph, START, END; print('langgraph ok')"
```

Expected:

```text
langgraph ok
```

### Task 2: Add Feature Flag

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_contracts.py`

- [ ] **Step 1: Add settings field**

In `Settings`, add:

```python
reply_graph_enabled: bool = False
```

- [ ] **Step 2: Add a narrow config test**

In `tests/test_contracts.py`, add:

```python
def test_reply_graph_flag_defaults_to_disabled(monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.delenv("REPLY_GRAPH_ENABLED", raising=False)

    try:
        settings = get_settings()
        assert settings.reply_graph_enabled is False
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 3: Run the narrow test**

Run:

```bash
python -m pytest tests/test_contracts.py::test_reply_graph_flag_defaults_to_disabled -q
```

Expected: `1 passed`.

## Phase 2: Graph Equivalent Of `_build_reply`

### Task 3: Create Reply Workflow State And Skeleton

**Files:**
- Create: `app/services/reply_workflow_graph.py`
- Test: `tests/test_reply_workflow_graph.py`

- [ ] **Step 1: Create initial graph module**

Create `app/services/reply_workflow_graph.py`:

```python
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.schemas.intent import IntentResult
from app.schemas.reply import FinalReply


class ReplyWorkflowState(TypedDict, total=False):
    route: str
    intent: IntentResult
    message: object
    user_state: object
    stage_latencies: dict[str, int]
    talk_script_status: str
    reply: FinalReply


async def route_entry_node(state: ReplyWorkflowState) -> dict:
    return {}


def route_from_entry(state: ReplyWorkflowState) -> Literal[
    "talk_script",
    "template_reply",
    "rag_answer",
    "template_then_rag",
    "human",
    "chitchat",
    "unsupported",
    "clarify",
]:
    route = state["route"]
    if route in {"template_reply", "template_then_rag", "rag_answer"}:
        return "talk_script"
    if route in {"template_reply", "rag_answer", "template_then_rag", "human", "chitchat", "unsupported"}:
        return route
    return "clarify"


def build_reply_workflow_graph():
    graph = StateGraph(ReplyWorkflowState)
    graph.add_node("route_entry", route_entry_node)
    graph.add_edge(START, "route_entry")
    graph.add_conditional_edges("route_entry", route_from_entry)
    return graph
```

- [ ] **Step 2: Add import test**

Create `tests/test_reply_workflow_graph.py`:

```python
def test_reply_workflow_graph_imports():
    from app.services.reply_workflow_graph import build_reply_workflow_graph

    assert build_reply_workflow_graph is not None
```

- [ ] **Step 3: Run import test**

Run:

```bash
python -m pytest tests/test_reply_workflow_graph.py::test_reply_workflow_graph_imports -q
```

Expected: `1 passed`.

### Task 4: Implement Talk Script Node

**Files:**
- Modify: `app/services/reply_workflow_graph.py`
- Test: `tests/test_reply_workflow_graph.py`

- [ ] **Step 1: Move equivalent talk-script behavior into graph node**

Add imports:

```python
import time

from app.schemas.reply import FinalReply
from app.talk_script.service import match_talk_script
```

Add helper:

```python
def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)
```

Add node:

```python
async def talk_script_node(state: ReplyWorkflowState) -> dict:
    message = state["message"]
    user_state = state["user_state"]
    stage_latencies = state["stage_latencies"]

    started = time.perf_counter()
    talk_script = await match_talk_script(
        customer_id=message.user_id,
        current_message=message.message,
        trace_id=message.trace_id,
        session_id=message.session_id,
        recent_messages=[],
        customer_tags={"tags": user_state.customer_tags},
    )
    stage_latencies["talk_script_ms"] = _elapsed_ms(started)

    if talk_script.status == "matched":
        stage_latencies.setdefault("template_ms", 0)
        stage_latencies.setdefault("rag_ms", 0)
        return {
            "talk_script_status": "matched",
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
            ),
        }

    if talk_script.status == "handoff":
        return {"talk_script_status": "handoff", "talk_script_result": talk_script}

    return {"talk_script_status": "pass_through"}
```

Add the node and conditional edge:

```python
graph.add_node("talk_script", talk_script_node)
```

- [ ] **Step 2: Route after talk script**

Add:

```python
def route_after_talk_script(state: ReplyWorkflowState) -> Literal[
    "done",
    "talk_script_handoff",
    "template_reply",
    "rag_answer",
    "template_then_rag",
]:
    if state.get("reply") is not None:
        return "done"
    if state.get("talk_script_status") == "handoff":
        return "talk_script_handoff"
    route = state["route"]
    if route == "template_reply":
        return "template_reply"
    if route == "template_then_rag":
        return "template_then_rag"
    return "rag_answer"
```

- [ ] **Step 3: Add tests for matched talk script**

Use monkeypatch to replace `match_talk_script` and assert the graph returns a `FinalReply` with `reply_type="template"` and `route="template_reply"`.

Run:

```bash
python -m pytest tests/test_reply_workflow_graph.py -q
```

Expected: all graph tests pass.

### Task 5: Implement Existing Route Nodes

**Files:**
- Modify: `app/services/reply_workflow_graph.py`
- Test: `tests/test_reply_workflow_graph.py`

- [ ] **Step 1: Implement `template_reply_node`**

Use existing services:

```python
from app.services.template_service import render_template, select_template
from app.services.reply_builder import build_template_reply
```

Behavior must match current `_build_reply()`:

- call `select_template`
- if no template, call shared handoff helper
- otherwise call `render_template`
- return `build_template_reply(...)`
- set `template_ms`
- set `rag_ms` to 0

- [ ] **Step 2: Implement `rag_answer_node`**

Use:

```python
from app.services.rag_service import answer_knowledge
from app.services.reply_builder import build_rag_reply
from app.services.chat_orchestrator import _is_rag_no_answer, build_handoff_reply
```

Behavior must match current `_build_reply()`:

- call `answer_knowledge`
- set `rag_ms`
- set `template_ms` to 0
- if `_is_rag_no_answer`, build handoff with reason `rag_no_answer_to_handoff`
- otherwise return `build_rag_reply`

- [ ] **Step 3: Implement `template_then_rag_node`**

Behavior must match current `_build_reply()`:

- select and render template
- call `answer_knowledge`
- if template missing, handoff reason `template_not_found_to_handoff`
- if RAG no answer, handoff reason `rag_no_answer_to_handoff`
- otherwise return `build_template_then_rag_reply`

- [ ] **Step 4: Implement non-RAG route nodes**

Use existing builders:

```python
build_chitchat_reply
build_clarify_reply
build_unsupported_reply
```

For `human`, call existing `build_handoff_reply`.

- [ ] **Step 5: Add graph completion node**

Every path must return:

```python
{"reply": FinalReply(...)}
```

Then terminate at `END`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python -m pytest tests/test_reply_workflow_graph.py -q
```

Expected: all graph route tests pass.

## Phase 3: Wire Into Current Orchestrator

### Task 6: Add Graph Entry Function

**Files:**
- Modify: `app/services/reply_workflow_graph.py`

- [ ] **Step 1: Add public function**

Add:

```python
reply_workflow_graph = build_reply_workflow_graph()


async def build_reply_with_graph(
    *,
    route: str,
    intent: IntentResult,
    message,
    user_state,
    stage_latencies: dict[str, int],
) -> FinalReply:
    result = await reply_workflow_graph.ainvoke(
        {
            "route": route,
            "intent": intent,
            "message": message,
            "user_state": user_state,
            "stage_latencies": stage_latencies,
        }
    )
    return result["reply"]
```

- [ ] **Step 2: Add direct test**

Assert `build_reply_with_graph(...)` returns the same `FinalReply` shape for a monkeypatched `rag_answer` path.

Run:

```bash
python -m pytest tests/test_reply_workflow_graph.py -q
```

Expected: all pass.

### Task 7: Add Feature-Flagged Orchestrator Call

**Files:**
- Modify: `app/services/chat_orchestrator.py`
- Test: `tests/test_chat_orchestrator_reply_graph.py`

- [ ] **Step 1: Change only the reply-building call site**

Replace:

```python
reply = await _build_reply(route, routed_intent, message, user_state, stage_latencies)
```

With:

```python
if get_settings().reply_graph_enabled:
    from app.services.reply_workflow_graph import build_reply_with_graph

    reply = await build_reply_with_graph(
        route=route,
        intent=routed_intent,
        message=message,
        user_state=user_state,
        stage_latencies=stage_latencies,
    )
else:
    reply = await _build_reply(route, routed_intent, message, user_state, stage_latencies)
```

- [ ] **Step 2: Test flag disabled preserves old behavior**

Add test:

```python
def test_reply_graph_disabled_uses_existing_build_reply(monkeypatch):
    monkeypatch.setenv("REPLY_GRAPH_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    assert get_settings().reply_graph_enabled is False
```

- [ ] **Step 3: Test flag enabled calls graph**

Monkeypatch `app.services.reply_workflow_graph.build_reply_with_graph` to return a known `FinalReply`; call `handle_chat` with a safe message and assert response contains that reply.

- [ ] **Step 4: Run focused orchestrator tests**

Run:

```bash
python -m pytest tests/test_chat_orchestrator_reply_graph.py -q
```

Expected: all pass.

## Phase 4: Reply Review Node

### Task 8: Add Conservative Reply Review

**Files:**
- Modify: `app/services/reply_workflow_graph.py`
- Test: `tests/test_reply_workflow_graph.py`

- [ ] **Step 1: Add review node after generated replies**

Rules for first version:

- Do not review `human` replies.
- Do not block fixed matched talk-script replies unless they contain explicit risky commitments.
- Flag risky text containing:
  - `百分百成活`
  - `保证成活`
  - `保证治好`
  - `一定赔`
  - `包赔`
  - `药到病除`

- [ ] **Step 2: Route risky generated replies to handoff**

If a risky generated reply is detected, replace reply with `build_handoff_reply(...)` reason:

```text
reply_review_risk_to_handoff
```

- [ ] **Step 3: Add tests**

Test:

- normal `rag_answer` passes
- risky generated text routes to human
- existing `stage_latencies` remains populated

Run:

```bash
python -m pytest tests/test_reply_workflow_graph.py -q
```

Expected: all pass.

## Phase 5: Verification Against Existing Dataset

### Task 9: Regression Tests

**Files:**
- Existing tests only

- [ ] **Step 1: Run route and fallback tests**

Run:

```bash
python -m pytest tests/test_chat_intent_routes.py tests/test_handoff_fallbacks.py tests/test_talk_script.py -q
```

Expected: all pass.

- [ ] **Step 2: Run RAG-focused tests**

Run:

```bash
python -m pytest tests/test_rag_service.py tests/test_qdrant_service.py tests/test_embedding_service.py -q
```

Expected: all pass.

- [ ] **Step 3: Run full suite**

Run:

```bash
python -m pytest -q
```

Expected: all pass.

### Task 10: Real Dataset Comparison

**Files:**
- Use existing evaluation script if kept: `tmp/run_real_dataset_eval.py`
- Output: `tmp/real_dataset_eval/...`

- [ ] **Step 1: Run current baseline with graph disabled**

Run:

```bash
$env:REPLY_GRAPH_ENABLED="false"
python tmp\run_real_dataset_eval.py --dataset "D:\项目文件\兰花AI机器人\AI内容\兰花客服图片问题案例集 问题版.xlsx"
```

Expected:

- 49 rows recorded
- no batch-level failure

- [ ] **Step 2: Run graph-enabled comparison**

Run:

```bash
$env:REPLY_GRAPH_ENABLED="true"
python tmp\run_real_dataset_eval.py --dataset "D:\项目文件\兰花AI机器人\AI内容\兰花客服图片问题案例集 问题版.xlsx"
```

Expected:

- 49 rows recorded
- no batch-level failure
- route counts should remain explainable
- `rag_answer`, `template_reply`, and `human` cases should preserve existing contract fields

- [ ] **Step 3: Compare result files**

Compare:

- `route`
- `reply_type`
- `need_human`
- `source_count`
- `answer`
- `handoff.reason`
- `latency_ms`

Acceptance threshold for first graph rollout:

- No API schema regression.
- No missing `trace_id`, `route`, `reply_type`, `need_human`.
- No increase in failed rows.
- Any route-count change must be explained by review node or intentional graph behavior.

## Rollout Plan

1. Keep `REPLY_GRAPH_ENABLED=false` by default.
2. Merge graph code with tests while disabled.
3. Run real dataset comparison locally.
4. Enable in local `.env` only.
5. Review logs for:
   - `talk_script_ms`
   - `template_ms`
   - `rag_ms`
   - final `route`
   - `need_human`
   - handoff reasons
6. Only after behavior is stable, enable in staging or demo environment.

## Success Checks

- Existing `/api/v1/chat` response schema unchanged.
- Existing `FinalReply` behavior preserved for all current routes.
- Existing log and profile update behavior unchanged because `handle_chat()` remains the outer orchestrator.
- Graph is behind `REPLY_GRAPH_ENABLED`.
- Graph tests cover talk-script matched, talk-script handoff, template, rag, template+rag, human, chitchat, unsupported, clarify.
- Real dataset runs produce complete 49-row result files.

## Non-Goals

- Do not replace `handle_chat()` in the first implementation.
- Do not replace `rag_service.py` with LangChain/LangGraph.
- Do not change Excel fields or dataset schema.
- Do not introduce autonomous tool-calling agents for customer replies.
- Do not change public API response fields.

## Self-Review

- Spec coverage: The plan targets the real existing主链路 and identifies `_build_reply` as the first LangGraph integration point.
- Placeholder scan: No implementation step relies on an unspecified route or file. Risk rules and acceptance checks are explicit.
- Type consistency: The graph returns the existing `FinalReply` object and uses current `IntentResult`, `message`, `user_state`, and `stage_latencies` inputs.
