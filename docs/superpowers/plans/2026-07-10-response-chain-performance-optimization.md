# Response Chain Performance Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将客户消息的主回复链路收敛为“交易模板直答 / 养护知识一次 RAG / 六类人工接管”，删除重复模型与检索调用，并将非关键画像工作移出客户等待路径。

**Architecture:** 保留现有 `intent + policy` 作为唯一主路由。交易事实只走模板和当前业务快照；养护、品种问题只走干净的知识 RAG；人工事项维持现有静默接管。请求内复用 embedding 和检索结果，客户回复返回后再异步处理画像、长期记忆和分析日志。

**Tech Stack:** Python、FastAPI、LangGraph、Qdrant、DashScope、pytest。

---

## Non-negotiable Constraints

- 不新增提示词层，不新增长提示词，不增加正式槽位表单。
- 不扩大人工接管范围；仅保留现有六类人工事项。
- 不为养护问题新增关键词硬规则；通过知识库边界处理内容污染。
- 不运行完整评测，直到任务 1～4 的目标测试均通过。
- 每次改动只处理一个根因；先写失败测试，再写最小实现。

## Target Flow

```text
客户消息
  ↓
会话状态 + 请求级缓存
  ↓
intent + policy
  ├─ human              → 空回复，人工后台接管
  ├─ template_reply     → 当前模板直接回复；未命中仅澄清，不进 RAG
  └─ rag_answer         → 一次 embedding → 一次知识检索 → 必要时重排 → 一次生成
  ↓
立即返回回复
  ↓
异步：画像分析、长期记忆、统计日志
```

## File Map

- `wechat_rag_bot/app/services/chat_orchestrator.py`：请求级缓存、同步/异步职责切分。
- `wechat_rag_bot/app/services/reply_workflow_graph.py`：图谱路径的路由短路。
- `wechat_rag_bot/app/talk_script/service.py`：话术匹配只服务模板路径。
- `wechat_rag_bot/app/services/rag_service.py`：复用向量、缩小检索集合、只在需要时重排。
- `wechat_rag_bot/app/services/knowledge_service.py`：导入时按已有 section/chunk 类型拆分知识用途。
- `wechat_rag_bot/app/services/user_profile_service.py`：画像分析改为后台任务入口。
- `wechat_rag_bot/evaluation/run_evaluation.py`：逐题持久化与系统回复/Judge 分阶段运行。
- `wechat_rag_bot/tests/`：新增各根因对应的回归测试。

## Task 1: Remove Duplicate Route Decisions

**Files:**

- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/talk_script/service.py`
- Test: `wechat_rag_bot/tests/test_reply_workflow_graph.py`
- Test: `wechat_rag_bot/tests/test_talk_script.py`

- [ ] **Step 1: Write failing tests**

Add tests proving that:

```python
@pytest.mark.asyncio
async def test_rag_route_does_not_call_talk_script_matcher(monkeypatch):
    called = False

    async def fail_match(**kwargs):
        nonlocal called
        called = True
        raise AssertionError("rag route must not enter talk-script matching")

    monkeypatch.setattr(reply_workflow_graph, "match_talk_script", fail_match)
    # Build a rag_answer reply with a stubbed answer_knowledge result.
    # Assert it returns a RAG reply and called is False.
```

```python
@pytest.mark.asyncio
async def test_template_route_still_calls_talk_script_matcher(monkeypatch):
    # Stub match_talk_script to a matched template reply.
    # Assert template_reply returns that reply without invoking RAG.
```

- [ ] **Step 2: Verify red**

```powershell
cd wechat_rag_bot
python -m pytest tests/test_reply_workflow_graph.py tests/test_talk_script.py -q
```

Expected: the RAG-route test fails because `TALK_SCRIPT_ROUTES` includes `rag_answer`.

- [ ] **Step 3: Implement minimal route narrowing**

Replace:

```python
TALK_SCRIPT_ROUTES = {"template_reply", "template_then_rag", "rag_answer"}
```

with:

```python
TALK_SCRIPT_ROUTES = {"template_reply", "template_then_rag"}
```

Apply the same condition in the non-graph `_build_reply` path. Do not alter the six human handoff cases.

- [ ] **Step 4: Verify green**

```powershell
python -m pytest tests/test_reply_workflow_graph.py tests/test_talk_script.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/services/reply_workflow_graph.py wechat_rag_bot/app/services/chat_orchestrator.py wechat_rag_bot/tests/test_reply_workflow_graph.py wechat_rag_bot/tests/test_talk_script.py
git commit -m "perf: skip talk script matching for rag routes"
```

## Task 2: Reuse One Request Embedding

**Files:**

- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/services/intent_example_service.py`
- Modify: `wechat_rag_bot/app/services/rag_service.py`
- Test: `wechat_rag_bot/tests/test_chat_orchestrator_reply_graph.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_rag_request_reuses_one_embedding(monkeypatch):
    calls = 0

    async def embed_once(text):
        nonlocal calls
        calls += 1
        return [0.1, 0.2]

    monkeypatch.setattr(embedding_service, "embed_text", embed_once)
    # Execute a route that needs intent examples and RAG.
    # Assert calls == 1.
```

- [ ] **Step 2: Verify red**

```powershell
python -m pytest tests/test_chat_orchestrator_reply_graph.py::test_rag_request_reuses_one_embedding -q
```

Expected: more than one embedding call.

- [ ] **Step 3: Implement request-scoped vector reuse**

Create one local request-context dictionary in `handle_chat`:

```python
request_context = {"embedding": None}
```

Add an optional `embedding: list[float] | None` parameter to intent-example retrieval and RAG retrieval. Generate an embedding only when `request_context["embedding"] is None`, store it, and pass it to subsequent stages. Do not create Redis cache or persistent cache in this task.

- [ ] **Step 4: Verify green**

```powershell
python -m pytest tests/test_chat_orchestrator_reply_graph.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/services/chat_orchestrator.py wechat_rag_bot/app/services/intent_example_service.py wechat_rag_bot/app/services/rag_service.py wechat_rag_bot/tests/test_chat_orchestrator_reply_graph.py
git commit -m "perf: reuse embeddings within chat requests"
```

## Task 3: Separate Care Knowledge from Sales Scripts

**Files:**

- Modify: `wechat_rag_bot/app/services/knowledge_service.py`
- Modify: `wechat_rag_bot/app/services/rag_service.py`
- Modify: knowledge import/reindex command documentation
- Test: `wechat_rag_bot/tests/test_rag_service.py`

- [ ] **Step 1: Write failing tests**

```python
def test_care_retrieval_excludes_sales_sections():
    docs = [
        {"section": "CHUNK CARE-0001｜养护问答", "text": "care"},
        {"section": "CHUNK SCRIPT-0001｜促单", "text": "sales"},
        {"section": "CHUNK FLOW-0001｜成交", "text": "flow"},
    ]
    assert select_care_docs(docs) == [docs[0]]
```

```python
def test_transaction_route_does_not_search_general_knowledge():
    # Assert template_reply invokes no RAG search function.
```

- [ ] **Step 2: Verify red**

```powershell
python -m pytest tests/test_rag_service.py -q
```

- [ ] **Step 3: Implement data-boundary filtering**

Use the existing `section` metadata, not message keywords. For care RAG, retain only chunks whose section begins with one of:

```python
CARE_SECTION_PREFIXES = ("CHUNK CARE-", "CHUNK VARIETY-", "CHUNK KNOWLEDGE-")
```

Exclude `CHUNK SCRIPT-`, `CHUNK FLOW-`, and `CHUNK SOP-` before reranking. Keep them indexed for existing template/talk-script features; do not delete source records.

- [ ] **Step 4: Reindex and inspect sources**

Run the existing reindex command for the filtered collection. Use the RAG debug endpoint or service to query “黑斑黄叶烂根怎么办”, then confirm returned sections are all care/variety knowledge and no `SCRIPT/FLOW/SOP` appears.

- [ ] **Step 5: Verify green and commit**

```powershell
python -m pytest tests/test_rag_service.py -q
git add wechat_rag_bot/app/services/knowledge_service.py wechat_rag_bot/app/services/rag_service.py wechat_rag_bot/tests/test_rag_service.py
git commit -m "fix: isolate care knowledge from sales scripts"
```

## Task 4: Make Profile Analysis Asynchronous

**Files:**

- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/services/user_profile_service.py`
- Test: `wechat_rag_bot/tests/test_chat_api.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_chat_returns_before_profile_analysis_finishes(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_profile(*args, **kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(chat_orchestrator, "update_profile_after_chat", slow_profile)
    # Start handle_chat and assert the reply returns after started is set,
    # before release is set.
```

- [ ] **Step 2: Verify red**

```powershell
python -m pytest tests/test_chat_api.py::test_chat_returns_before_profile_analysis_finishes -q
```

- [ ] **Step 3: Implement minimal background scheduling**

Keep `update_user_state` synchronous. Replace the awaited profile call with:

```python
asyncio.create_task(update_profile_after_chat(message, routed_intent, reply))
```

Wrap the task in a helper that logs exceptions. Do not retry inline and do not alter reply content when profile analysis fails.

- [ ] **Step 4: Verify green and commit**

```powershell
python -m pytest tests/test_chat_api.py tests/test_chat_orchestrator_reply_graph.py -q
git add wechat_rag_bot/app/services/chat_orchestrator.py wechat_rag_bot/app/services/user_profile_service.py wechat_rag_bot/tests/test_chat_api.py
git commit -m "perf: move profile analysis off reply path"
```

## Task 5: Make Evaluation Durable and Two-stage

**Files:**

- Modify: `wechat_rag_bot/evaluation/run_evaluation.py`
- Modify: `wechat_rag_bot/evaluation/retry_evaluation.py`
- Test: `wechat_rag_bot/tests/test_evaluation_runner.py`

- [ ] **Step 1: Write failing tests**

```python
@pytest.mark.asyncio
async def test_completed_chat_is_appended_before_next_chat_finishes(tmp_path, monkeypatch):
    # Make one run_single complete immediately and another wait.
    # Assert raw_responses.jsonl contains the completed ID while the second is pending.
```

```python
def test_judge_only_mode_uses_existing_raw_responses(tmp_path):
    # Seed raw_responses.jsonl, invoke target selection, and assert no chat target is returned.
```

- [ ] **Step 2: Verify red**

```powershell
python -m pytest tests/test_evaluation_runner.py -q
```

- [ ] **Step 3: Implement durable stages**

Add CLI switches:

```text
--chat-only
--judge-only
--resume
```

`--chat-only` writes each chat result immediately. `--judge-only` requires existing raw responses and never calls `/api/v1/chat`. `--resume` skips successful chat rows and successful scores. Keep existing output filenames.

- [ ] **Step 4: Verify green and commit**

```powershell
python -m pytest tests/test_evaluation_runner.py -q
git add wechat_rag_bot/evaluation/run_evaluation.py wechat_rag_bot/evaluation/retry_evaluation.py wechat_rag_bot/tests/test_evaluation_runner.py
git commit -m "fix: make evaluation stages resumable"
```

## Task 6: Measure Before and After

- [ ] Start service with `EVALUATION_MODE=true`.
- [ ] Run `--chat-only` on a fixed 10-case smoke subset at concurrency 1.
- [ ] Record `intent_ms`、`talk_script_ms`、`rag_ms`、`state_update_ms` and total latency for each case.
- [ ] Confirm template cases have `rag_ms=0`; RAG cases have `talk_script_ms=0`; care sources contain no `SCRIPT/FLOW/SOP`.
- [ ] Run Judge only after raw replies are complete. Do not include Judge failures in business-answer score.

## Acceptance Criteria

- 模板命中题不调用 embedding、Qdrant、RAG 或话术分类模型。
- 养护 RAG 题不调用话术匹配；sources 中没有 `SCRIPT`、`FLOW`、`SOP`。
- 单个 RAG 请求最多生成一次 embedding。
- 画像分析失败或变慢不阻塞客户回复。
- 评测运行中断后，已完成系统回复和 Judge 分数均可恢复。
- 常规养护回复目标 P50 为 5～12 秒，复杂题 P95 不超过 20 秒；模板回复目标 1～2 秒。
- 不新增提示词层、不新增大型硬规则、不扩大人工接管范围。
