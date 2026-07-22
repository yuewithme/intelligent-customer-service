# Evaluation And Sales Chain Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修正评测口径与多轮上下文，接入结构化业务事实，同步保存销售槽位，并用现有路由和模型配置缩短回复链路。

**Architecture:** 保留现有 `intent + policy + sales action` 编排，将评测上下文改为运行时临时会话，将商品/会员/活动/订单事实作为结构化 `business_context` 传入模板路径。养护继续使用干净 RAG；信息不全保持 clarify；只有明确人工事项才空回复接管。

**Tech Stack:** Python、FastAPI、LangGraph、SQLAlchemy、Qdrant、pytest。

---

## Task 1: Make Evaluation Results Valid

**Files:**
- Modify: `wechat_rag_bot/evaluation/run_evaluation.py`
- Test: `wechat_rag_bot/tests/test_evaluation_runner.py`

- [ ] Add failing tests proving API latency starts after semaphore acquisition, empty responses are pending, multi-turn messages contain accumulated customer/assistant transcript, and boundary items are executed.
- [ ] Implement `is_successful_chat_result`, role-preserving context builders, boundary targets, structured `tool_state` metadata, and separate `queue_wait_ms`/`latency_ms`.
- [ ] Run `python -m pytest tests/test_evaluation_runner.py -q`; do not call a live chat API.
- [ ] Commit `fix: correct evaluation context and completion tracking`.

## Task 2: Add Structured Business Context

**Files:**
- Create: `wechat_rag_bot/app/services/business_context_service.py`
- Modify: `wechat_rag_bot/app/orchid_products/repository.py`
- Modify: `wechat_rag_bot/app/services/template_reply_service.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Test: `wechat_rag_bot/tests/test_business_context_service.py`
- Test: `wechat_rag_bot/tests/test_reply_workflow_graph.py`

- [ ] Add failing tests for reading SKU facts, preserving metadata `business_snapshot`/`tool_state`, and returning a grounded template reply without RAG.
- [ ] Add read-only SKU query functions and a compact `BusinessContext` builder; consume only supplied snapshots and stored SKU facts.
- [ ] Make the template path use grounded business context before generic fallback; never invent missing price, stock, approval, payment, or fulfillment results.
- [ ] Run the two targeted test files and commit `feat: ground transaction replies in business context`.

## Task 3: Persist Sales Slots Synchronously

**Files:**
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/services/state_service.py`
- Modify: `wechat_rag_bot/app/services/sales_action_service.py`
- Test: `wechat_rag_bot/tests/test_chat_orchestrator_reply_graph.py`
- Test: `wechat_rag_bot/tests/test_sales_action_service.py`

- [ ] Add failing tests proving budget, region, plant count, preferences, asked slots, and blockers survive the next turn without profile analysis.
- [ ] Merge deterministic tag entities into current intent slots before sales-action selection and store them in `UserState.metadata.active_opportunity` during synchronous state update.
- [ ] Keep LLM profile analysis asynchronous and use it only for long-term enrichment.
- [ ] Run targeted tests and commit `fix: retain sales context across turns`.

## Task 4: Keep Clarification Out Of RAG And Human Handoff

**Files:**
- Modify: `wechat_rag_bot/app/services/policy_service.py`
- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/services/reply_builder.py`
- Modify: `wechat_rag_bot/app/talk_script/service.py`
- Test: `wechat_rag_bot/tests/test_reply_workflow_graph.py`
- Test: `wechat_rag_bot/tests/test_talk_script.py`

- [ ] Add failing tests proving intent clarify stays clarify, RAG no-answer becomes a natural follow-up, and ordinary order/payment/logistics words do not trigger handoff.
- [ ] Reuse current intent slots and sales question slot to ask one concise question; remove broad handoff words instead of adding rules.
- [ ] Preserve empty reply only for explicit human/refund/return/cancel/complaint/compensation/resend/replace cases.
- [ ] Run targeted tests and commit `fix: reserve handoff for explicit human cases`.

## Task 5: Remove Avoidable Model And Retrieval Calls

**Files:**
- Modify: `wechat_rag_bot/app/services/intent_service.py`
- Modify: `wechat_rag_bot/app/services/rag_service.py`
- Test: `wechat_rag_bot/tests/test_intent.py`
- Test: `wechat_rag_bot/tests/test_rag_service.py`

- [ ] Add failing tests proving confident existing soft-rule matches bypass intent LLM and default orchid RAG searches only the clean orchid KB.
- [ ] Evaluate existing soft rules before LLM only when they produce a non-clarify route above the configured confidence threshold.
- [ ] Use `kb_orchid_basic` as the default RAG collection for orchid knowledge; keep policy-provided KB IDs unchanged.
- [ ] Add RAG substage timing for embedding, search, rerank, prompt, and generation without changing prompts.
- [ ] Run targeted tests and commit `perf: short circuit clear routes and duplicate retrieval`.

## Final Verification

- [ ] Run `python -m pytest -q`.
- [ ] Run `git diff --check` and review only task-related files.
- [ ] Do not start uvicorn, do not call DashScope/Qdrant, and do not run the 55-item evaluation dataset.
- [ ] Push verified commits to `main` according to the repository workflow.
