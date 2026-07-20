# LLM Orchid Import And Route Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use the model's own judgment to classify the orchid Excel content into the existing `orchid_*` tables, and fix the current chat route test failures with minimal routing changes.

**Architecture:** Keep SQLAlchemy table definitions and repository persistence, but replace rule-script classification with LLM-curated batch extraction. For chat failures, keep the existing policy pipeline and adjust only the fallback behavior that currently turns template routes into handoff.

**Tech Stack:** Python, SQLAlchemy, SQLite local `rag.db`, optional PostgreSQL/pgvector later, pytest.

---

## Part A: LLM-Curated Orchid Content Import

### Task 1: Freeze Raw Input And Existing Tables

**Files:**
- Read-only input: `D:/项目文件/兰花AI机器人/兰花-产品信息拆解表清洗版.xlsx`
- Existing DB: `D:/Codex产品开发/智能客服/rag.db`
- Existing tables: `wechat_rag_bot/app/db/models.py`

- [ ] **Step 1: Export raw rows without classification**

Use a read-only workbook pass to obtain raw rows and sheet names only. Do not infer fields with regex or rules in this step.

Expected sheet mapping:

```text
通用知识点 -> raw common knowledge rows
建兰/春兰/春剑/寒兰/墨兰/莲瓣兰/蕙兰/其他品类 -> raw variety rows
链接详情 -> raw SKU rows
热门品种拆解 -> raw professional breakdown rows
私域运营团队2 -> raw sales copy rows
```

- [ ] **Step 2: Confirm DB target before write**

Use this exact DB URL when writing:

```powershell
$env:DATABASE_URL='sqlite:///D:/Codex产品开发/智能客服/rag.db'
```

Success check:

```powershell
python -c "import sqlite3; c=sqlite3.connect(r'D:/Codex产品开发/智能客服/rag.db'); print(c.execute('select count(*) from sqlite_master').fetchone()[0])"
```

Expected: command exits 0 and prints a positive integer.

### Task 2: LLM Batch Classification Contract

**Files:**
- Create if implementing persisted review: `wechat_rag_bot/data/orchid_llm_batches/`
- Write destination remains DB tables, not a new permanent business table.

- [ ] **Step 1: Classify each row using LLM judgment**

For each Excel row, the agent must read the actual row content and produce this normalized object by reasoning over the text:

```json
{
  "record_kind": "common_knowledge | variety | sku | sales_copy | hot_breakdown",
  "category_name": "",
  "variety_name": "",
  "aliases": [],
  "source_type": "",
  "origin_area": "",
  "history_background": "",
  "summary": "",
  "traits": [
    {"trait_type": "花色", "trait_value": "", "keywords": []}
  ],
  "value_points": [
    {"value_type": "故事背景", "title": "", "content": "", "keywords": []}
  ],
  "sku": {
    "seedling_count": "",
    "package_spec": "",
    "flower_bud_status": "",
    "price": null,
    "price_text": ""
  },
  "common_knowledge": {
    "knowledge_category": "",
    "knowledge_type": "",
    "applies_to_category": "",
    "content": ""
  },
  "sales_copy": {
    "writer_name": "",
    "target_audience": "",
    "product_background": "",
    "leaf_posture": "",
    "petal_type": "",
    "flower_color": "",
    "fragrance": "",
    "flowering_period": "",
    "care_difficulty": "",
    "usage_scene": "",
    "selling_points": ""
  },
  "hot_breakdown": {
    "status_history_supply_price_authenticity": "",
    "aesthetic_traits": "",
    "cultivation_care": "",
    "consensus_reputation": ""
  },
  "knowledge_chunks": [
    {"chunk_type": "故事背景", "chunk_title": "", "content": ""}
  ],
  "uncertain_fields": []
}
```

Classification rules:

```text
1. Only fill a field when the row content supports it.
2. Do not create approval, workflow, audit, owner, status, or publishing fields.
3. Keep sales-copy wording in `orchid_sales_copy`; do not let it overwrite product facts.
4. Put exact SKU price/spec rows only into `orchid_skus`.
5. Put long explanatory text into `orchid_knowledge_chunks`.
6. If a field is ambiguous, keep the original text in a broader content field and list the field name in `uncertain_fields`.
7. Prefer fewer, accurate fields over many speculative fields.
```

- [ ] **Step 2: Review batch counts after every batch**

Recommended batch size:

```text
10品种 rows per batch
30 SKU rows per batch
20 common/hot/sales rows per batch
```

After each batch, report:

```json
{
  "batch": 1,
  "varieties": 10,
  "traits": 42,
  "value_points": 31,
  "skus": 0,
  "knowledge_chunks": 83,
  "uncertain_fields": 4
}
```

### Task 3: Persist LLM-Curated Results

**Files:**
- Use existing: `wechat_rag_bot/app/orchid_products/repository.py`
- Do not use existing rule classifier in `wechat_rag_bot/app/orchid_products/excel_importer.py` for final classification.

- [ ] **Step 1: Convert LLM-curated objects to DB payload**

Map LLM output to:

```text
orchid_categories
orchid_varieties
orchid_variety_traits
orchid_value_points
orchid_skus
orchid_common_knowledge
orchid_sales_copy
orchid_hot_breakdowns
orchid_knowledge_chunks
```

- [ ] **Step 2: Insert in a single transaction per reviewed batch**

Use repository persistence only as a DB writer. Do not let it classify or infer content.

Success check after final import:

```powershell
$env:PYTHONIOENCODING='utf-8'
python -c "import sqlite3,json; db=r'D:/Codex产品开发/智能客服/rag.db'; c=sqlite3.connect(db); ts=['orchid_categories','orchid_varieties','orchid_variety_traits','orchid_value_points','orchid_skus','orchid_common_knowledge','orchid_sales_copy','orchid_hot_breakdowns','orchid_knowledge_chunks']; print(json.dumps({t:c.execute(f'select count(*) from {t}').fetchone()[0] for t in ts}, ensure_ascii=False, indent=2))"
```

Expected: all table counts are non-zero except only fields genuinely absent from source content.

---

## Part B: Minimal Chat Route Test Fix

### Task 4: Add Failing Tests For Template Fallback

**Files:**
- Modify: `wechat_rag_bot/tests/test_reply_workflow_graph.py`
- Modify: `wechat_rag_bot/tests/test_chat_intent_routes.py`

- [ ] **Step 1: Add a test proving template route falls back to default template service**

Add a focused test that mocks `match_talk_script` to return not matched, then asserts `template_reply` still uses `tpl_price_objection_default` instead of handoff.

Expected failure before implementation:

```text
Expected route template_reply, got human
```

- [ ] **Step 2: Add a test proving explicit high-risk intents still hand off**

Keep refund, complaint, and human request tests unchanged.

Expected: these continue to return `route == "human"`.

### Task 5: Replace Template Miss Handoff With Default Template Fallback

**Files:**
- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`

- [ ] **Step 1: Use existing template service in graph template node**

In `template_node`, replace:

```python
return {
    "handoff_reason": "talk_script_not_matched_to_handoff",
    "handoff_original_route": "template_reply",
    "handoff_context": None,
}
```

with logic equivalent to:

```python
from app.services.template_service import select_template, render_template
from app.services.reply_builder import build_template_reply

template = await select_template(
    state["message"],
    state["intent"],
    state["user_state"],
)
if template is None:
    return {
        "handoff_reason": "template_not_matched_to_handoff",
        "handoff_original_route": "template_reply",
        "handoff_context": None,
    }
template_reply = await render_template(template, state["message"], state["user_state"])
return {"reply": build_template_reply(template_reply, state["intent"])}
```

- [ ] **Step 2: Apply the same fallback to non-graph path**

In `chat_orchestrator._build_reply`, for `route == "template_reply"`, call the same `select_template` / `render_template` path before handoff.

Success check:

```powershell
python -m pytest tests/test_chat_intent_routes.py::test_price_objection_uses_template_when_template_matches tests/test_contracts.py::test_chat_response_preserves_old_fields_and_adds_router_fields -q
```

Expected: both pass.

### Task 6: Resolve Care Route Test Expectation

**Files:**
- Modify: `wechat_rag_bot/tests/test_contracts.py`

- [ ] **Step 1: Decide product behavior**

Current code and prompts define pure orchid-care questions as `rag_answer`, not `human`.

Recommended behavior:

```text
兰花怎么养护？ -> rag_answer
这个有点贵 -> template_reply
这个有点贵，而且我怕养不活 -> template_then_rag or rag_answer depending current product policy, but not human unless RAG has no answer
```

- [ ] **Step 2: Update only stale expectations**

Change the stale case:

```python
("兰花怎么养护？", "human")
```

to:

```python
("兰花怎么养护？", "rag_answer")
```

For mixed price + care, prefer `template_then_rag` if template composition is implemented, otherwise document why current graph returns RAG-only.

Success check:

```powershell
python -m pytest tests/test_contracts.py::test_mock_intent_routes_knowledge_price_and_mixed_messages -q
```

Expected: pass with behavior aligned to product policy.

### Task 7: Verification

**Files:**
- No new production files beyond Tasks 4-6.

- [ ] **Step 1: Run narrow tests**

```powershell
python -m pytest tests/test_chat_intent_routes.py tests/test_contracts.py tests/test_reply_workflow_graph.py -q
```

Expected: all pass.

- [ ] **Step 2: Run orchid import tests**

```powershell
python -m pytest tests/test_orchid_product_importer.py tests/test_orchid_product_repository.py -q
```

Expected: all pass.

- [ ] **Step 3: Run full backend suite**

```powershell
python -m pytest
```

Expected: no failures.

- [ ] **Step 4: Check diff**

```powershell
git diff --check
```

Expected: exit 0.
