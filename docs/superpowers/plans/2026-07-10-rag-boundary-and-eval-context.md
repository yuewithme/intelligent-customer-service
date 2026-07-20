# RAG Boundary and Evaluation Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate care knowledge from sales scripts, prevent transactional facts from falling into generic RAG, and publish a context-complete evaluation dataset without running it.

**Architecture:** Keep the existing routes and services. Filter the existing orchid knowledge index by its existing `chunk_type`, retain only knowledge-oriented chunks for RAG, and use the existing template route for business facts with a single safe template-miss reply. Create `dataset_v2` by preserving real cases, expanding context-dependent turns with the recorded profile/recent dialogue, and moving irreducibly contextless items to an excluded manifest.

**Tech Stack:** Python, FastAPI services, Qdrant index metadata, JSONL evaluation dataset, pytest, Node.js validator.

---

### Task 1: Restrict RAG to knowledge chunks

**Files:**
- Modify: `wechat_rag_bot/app/orchid_products/knowledge_index.py`
- Test: `wechat_rag_bot/tests/test_orchid_knowledge_index.py`

- [ ] Add a failing test asserting `search_orchid_knowledge_chunks` excludes a row whose `chunk_type` is `sales_script` and retains `care_knowledge`.
- [ ] Run `python -m pytest tests/test_orchid_knowledge_index.py -q` and confirm the exclusion assertion fails.
- [ ] Add a single allow-list of existing knowledge chunk types at the index search boundary; do not inspect message keywords or add prompt conditions.
- [ ] Run the targeted test and commit the index boundary change.

### Task 2: Keep transactional template misses out of generic RAG

**Files:**
- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Test: `wechat_rag_bot/tests/test_reply_workflow_graph.py`

- [ ] Add a failing test where an `ask_price` template miss returns the existing safe confirmation reply and never calls `answer_knowledge`.
- [ ] Run the single test and confirm it fails because the current template node calls RAG.
- [ ] Reuse `build_clarify_reply` with a transaction-specific intent route for the template miss; do not create a new prompt or a new human-handoff rule.
- [ ] Run focused graph/orchestrator tests and commit the route-boundary change.

### Task 3: Reduce duplicated RAG instructions

**Files:**
- Modify: `wechat_rag_bot/app/services/rag_service.py`
- Test: `wechat_rag_bot/tests/test_rag_service.py`

- [ ] Add a failing test for one compact safety boundary in the shared RAG instruction: unsupported facts are not confirmed and missing diagnostic facts are requested.
- [ ] Replace duplicate long principle lists in the main and fallback templates with the same compact instruction; remove conversion-oriented role wording from care generation.
- [ ] Run `python -m pytest tests/test_rag_service.py -q` and commit.

### Task 4: Publish a context-complete evaluation dataset

**Files:**
- Create: `docs/evaluation/dataset_v2/`
- Create: `docs/evaluation/dataset_v2/context_review.md`
- Create: `docs/evaluation/dataset_v2/excluded_contextless.jsonl`
- Reuse: `docs/evaluation/dataset_v1/validate_dataset.mjs`

- [ ] Copy v1 into v2 without changing real-case IDs.
- [ ] For each short customer utterance requiring profile, recent dialogue, product facts, or policy facts, add those facts to `conversation` as context; do not invent facts beyond its source case or existing snapshot.
- [ ] Move any item that cannot be made self-contained from source material into `excluded_contextless.jsonl` with its reason.
- [ ] Add a review table containing ID, original short utterance, supplied context source, and retained/excluded decision.
- [ ] Run the dataset validator against v2 and verify every retained item has a meaningful final user message plus sufficient contextual facts.

### Task 5: Final verification and handoff

- [ ] Run targeted tests for Tasks 1-3 and `node docs/evaluation/dataset_v1/validate_dataset.mjs docs/evaluation/dataset_v2` if supported, otherwise use the validator’s documented v2 invocation.
- [ ] Do not start Uvicorn or run `evaluation.run_evaluation`.
- [ ] Run `git diff --check`, inspect staged changes for secrets, commit only implementation/tests/dataset v2, and push `main`.
