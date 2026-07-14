# WeChat Context, Contact, and RAG Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复固定开场后的多轮理解、新联系人资料补偿、永久失败无限重试和养护检索混入销售资料，并补齐真实链路回归测试。

**Architecture:** 固定开场在风险控制工作器内同时写入用户/助手记忆，意图服务通过最近助手追问识别盆数/品种回答。联系人资料为空时由去重的后台任务调用易云 `/initAddressList`，等待后重新查询并回填画像与会话。出站消息在第二次失败后进入终态，养护检索只接受显式养护类知识块。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy、httpx、pytest/pytest-asyncio、SQLite。

---

### Task 1: 固定开场进入记忆并支持上下文回答

**Files:**
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Modify: `wechat_rag_bot/app/services/intent_service.py`
- Modify: `wechat_rag_bot/app/services/reply_builder.py`
- Test: `wechat_rag_bot/tests/test_opening_message.py`
- Create: `wechat_rag_bot/tests/test_opening_followup_context.py`

- [ ] **Step 1: 写固定开场记忆失败测试**

```python
@pytest.mark.asyncio
async def test_first_inbound_persists_user_and_opening_memories(monkeypatch):
    # 处理首条私聊后，断言 append_conversation_memory 依次收到 user 和 assistant。
    assert [(row["role"], row["content"]) for row in memories] == [
        ("user", "我是兰亭"),
        ("assistant", opening_text),
    ]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_opening_message.py::test_first_inbound_persists_user_and_opening_memories`

Expected: FAIL，当前首条分支没有写入 conversation memory。

- [ ] **Step 3: 最小实现固定开场记忆**

```python
async def _record_opening_memories(batch: dict[str, Any], answer: str) -> None:
    common = {
        "user_id": batch["from_user"] or batch["target_wc_id"],
        "tenant_id": "tenant_default",
        "session_id": batch["from_group"],
        "route": "chitchat",
    }
    await append_conversation_memory(role="user", content=batch["content"], **common)
    await append_conversation_memory(
        role="assistant", content=answer, intent="opening", **common
    )
```

- [ ] **Step 4: 写并运行多轮上下文失败测试**

```python
@pytest.mark.asyncio
async def test_opening_followup_uses_recent_assistant_question():
    state.metadata["recent_turns"] = [
        {"role": "assistant", "content": "家里养了多少盆？具体有哪些品种？"}
    ]
    intent = await classify_intent(message("我们家养了100盆，建兰为主吧"), state)
    assert intent.primary_intent == "profile_answer"
    assert intent.route == "chitchat"
```

Run: `pytest -q tests/test_opening_followup_context.py`

Expected: FAIL，当前分类器忽略 `recent_turns`。

- [ ] **Step 5: 实现上下文识别和自然确认回复**

```python
def classify_opening_followup(text: str, recent_turns: list[dict]) -> IntentResult | None:
    if not _recent_assistant_asked_for_orchid_profile(recent_turns):
        return None
    if not (PLANT_COUNT_PATTERN.search(text) or _mentions_orchid_variety(text)):
        return None
    return IntentResult(
        route="chitchat",
        primary_intent="profile_answer",
        sales_stage="need_discovery",
        confidence=0.98,
        reason="opening_profile_answer",
    )
```

`build_chitchat_reply` 对 `profile_answer` 返回“收到，已经记下您的养兰规模和主要品种。您现在最想解决哪一类养护问题？”。

- [ ] **Step 6: 运行相关测试**

Run: `pytest -q tests/test_opening_message.py tests/test_opening_followup_context.py tests/test_intent_service.py tests/test_reply_workflow_graph.py`

Expected: PASS。

### Task 2: 新联系人通讯录同步、延迟刷新和双向回填

**Files:**
- Modify: `wechat_rag_bot/app/config.py`
- Modify: `wechat_rag_bot/app/services/eyun_contact_service.py`
- Modify: `wechat_rag_bot/app/services/eyun_callback_service.py`
- Modify: `wechat_rag_bot/app/services/conversation_service.py`
- Modify: `wechat_rag_bot/.env.example`
- Modify: `deploy/env/backend.prod.env.example`
- Test: `wechat_rag_bot/tests/test_eyun_contact_service.py`
- Create: `wechat_rag_bot/tests/test_eyun_contact_refresh.py`

- [ ] **Step 1: 写联系人补偿失败测试**

```python
@pytest.mark.asyncio
async def test_empty_contact_initializes_then_refreshes_and_backfills(monkeypatch):
    await refresh_eyun_contact(
        w_id="wid", wc_id="customer", user_id="customer", delay_seconds=0
    )
    assert calls == ["initAddressList", "sleep", "getContact", "profile", "conversation"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_eyun_contact_refresh.py`

Expected: FAIL，当前没有初始化和回填函数。

- [ ] **Step 3: 实现初始化、去重调度和回填**

```python
async def initialize_eyun_contacts(*, w_id: str) -> bool:
    result = await _post_eyun("/initAddressList", {"wId": w_id}, timeout=190)
    return str(result.get("code")) == "1000"

async def refresh_eyun_contact(..., delay_seconds: float | None = None) -> None:
    if not await initialize_eyun_contacts(w_id=w_id):
        return
    await asyncio.sleep(delay_seconds or settings.eyun_contact_refresh_delay_seconds)
    snapshot = await get_eyun_contact_snapshot(w_id=w_id, wc_id=wc_id)
    if snapshot:
        await ensure_user_profile(user_id, basic_info=snapshot, ...)
        await update_customer_identity(channel="wechat", user_id=user_id, metadata=snapshot)
```

`schedule_eyun_contact_refresh` 以 `(w_id, wc_id)` 去重后台任务；callback 仅在昵称、备注、头像均为空时调度。

- [ ] **Step 4: 运行联系人相关测试**

Run: `pytest -q tests/test_eyun_contact_service.py tests/test_eyun_contact_refresh.py tests/test_eyun_callback.py tests/test_admin_conversations.py`

Expected: PASS。

### Task 3: 永久失败最多尝试两次

**Files:**
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Create: `wechat_rag_bot/tests/test_eyun_outbound_retry_limit.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_outbound_becomes_failed_after_second_send_failure():
    # attempts=1 的 queued 消息再次发送失败。
    await process_due_eyun_outbound_messages()
    assert row.attempts == 2
    assert row.status == "failed"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_eyun_outbound_retry_limit.py`

Expected: FAIL，当前第二次失败仍回到 queued。

- [ ] **Step 3: 实现终态**

```python
attempts = (row.attempts or 0) + 1
row.attempts = attempts
row.status = "failed" if attempts >= 2 else "queued"
if row.status == "queued":
    row.due_at = utcnow() + timedelta(seconds=30)
```

- [ ] **Step 4: 运行出站工作器测试**

Run: `pytest -q tests/test_eyun_outbound_retry_limit.py tests/test_message_risk_control.py`

Expected: PASS。

### Task 4: 养护问题只允许养护知识进入 RAG

**Files:**
- Modify: `wechat_rag_bot/app/services/rag_service.py`
- Modify: `wechat_rag_bot/tests/test_rag_service.py`

- [ ] **Step 1: 扩展失败测试覆盖商品资料**

```python
def test_care_retrieval_keeps_only_explicit_care_docs():
    docs = [care_doc, product_basic_doc, product_price_doc, sales_copy_doc]
    assert select_care_docs(docs) == [care_doc]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest -q tests/test_rag_service.py::test_care_retrieval_keeps_only_explicit_care_docs`

Expected: FAIL，当前普通商品基础信息会被保留。

- [ ] **Step 3: 实现显式养护白名单**

```python
CARE_MARKERS = ("养护", "浇水", "施肥", "烂根", "病虫", "植料", "通风", "光照", "换盆", "修根")

def select_care_docs(docs):
    return [doc for doc in docs if _is_explicit_care_doc(doc) and not _is_sales_doc(doc)]
```

白名单同时检查 `section`、`chunk_type`、`entity_type`、`source_table` 和正文摘要；商品名称、价格、规格、成交话术不满足养护标记时全部排除。

- [ ] **Step 4: 运行 RAG 测试**

Run: `pytest -q tests/test_rag_service.py tests/test_rag_policy_prompt.py tests/test_reply_workflow_graph.py`

Expected: PASS。

### Task 5: 综合验证与发布

**Files:**
- Review: all files modified above

- [ ] **Step 1: 运行五项窄范围测试集合**

Run: `pytest -q tests/test_opening_message.py tests/test_opening_followup_context.py tests/test_eyun_contact_service.py tests/test_eyun_contact_refresh.py tests/test_eyun_callback.py tests/test_eyun_outbound_retry_limit.py tests/test_message_risk_control.py tests/test_rag_service.py tests/test_rag_policy_prompt.py tests/test_reply_workflow_graph.py`

Expected: PASS。

- [ ] **Step 2: 检查差异和敏感信息**

Run: `git diff --check`，随后审阅 `git diff`；只暂存本计划和本次实现文件，不暂存既有用户改动、数据库、环境文件或生成物。

- [ ] **Step 3: 提交并推送**

```bash
git commit -m "fix: restore reliable wechat reply context"
git pull --rebase origin main
git push origin main
```

Expected: 提交成功，远端未分叉且 `main` 推送成功。
