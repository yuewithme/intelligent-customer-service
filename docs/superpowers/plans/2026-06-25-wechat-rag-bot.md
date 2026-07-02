# WeChat RAG Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable FastAPI API that exposes a unified RAG chat service, WeChat callbacks, and Qdrant-backed knowledge ingestion while preserving the specified contracts.

**Architecture:** Routers only translate transport concerns into schemas and service calls. `rag_service` orchestrates embedding, Qdrant retrieval, reranking, and LLM generation; provider-specific services remain replaceable and support deterministic local fallback for tests. API authentication, error envelopes, IDs, timestamps, logging, and document parsing are shared infrastructure.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic Settings, HTTPX, Qdrant Client, PyPDF, pytest, Docker Compose.

---

## File map

- `wechat_rag_bot/app/main.py`: FastAPI assembly, routers, validation and exception envelopes.
- `wechat_rag_bot/app/config.py`: environment-backed settings.
- `wechat_rag_bot/app/routers/*.py`: chat, WeChat, and knowledge HTTP adapters.
- `wechat_rag_bot/app/schemas/*.py`: fixed API schemas, error codes, and upload response types.
- `wechat_rag_bot/app/services/*.py`: RAG orchestration and isolated provider integrations.
- `wechat_rag_bot/app/db/*.py`: reserved SQLAlchemy model/session boundary.
- `wechat_rag_bot/app/utils/*.py`: IDs, timezone-aware timestamps, structured logging, and API auth.
- `wechat_rag_bot/tests/*.py`: contract and service tests.
- `wechat_rag_bot/.env.example`, `requirements.txt`, `docker-compose.yml`, `README.md`: deployment and usage.

### Task 1: Application foundation and contracts

**Files:**
- Create: `wechat_rag_bot/app/config.py`
- Create: `wechat_rag_bot/app/schemas/common.py`
- Create: `wechat_rag_bot/app/schemas/chat.py`
- Create: `wechat_rag_bot/app/schemas/knowledge.py`
- Create: `wechat_rag_bot/app/utils/ids.py`
- Create: `wechat_rag_bot/app/utils/time.py`
- Create: `wechat_rag_bot/app/utils/logger.py`
- Create: `wechat_rag_bot/app/utils/auth.py`
- Test: `wechat_rag_bot/tests/test_contracts.py`

- [ ] Write tests asserting fixed schema fields, error codes, ID prefixes, and bearer authentication behavior.
- [ ] Run `pytest tests/test_contracts.py -q` and confirm the tests fail because modules do not exist.
- [ ] Implement settings, response envelopes, enums/constants, ID/time helpers, JSON logging, and auth dependency.
- [ ] Run `pytest tests/test_contracts.py -q` and confirm all contract tests pass.

### Task 2: Provider services and RAG orchestration

**Files:**
- Create: `wechat_rag_bot/app/services/embedding_service.py`
- Create: `wechat_rag_bot/app/services/qdrant_service.py`
- Create: `wechat_rag_bot/app/services/rerank_service.py`
- Create: `wechat_rag_bot/app/services/llm_service.py`
- Create: `wechat_rag_bot/app/services/rag_service.py`
- Test: `wechat_rag_bot/tests/test_rag_service.py`

- [ ] Write async tests that monkeypatch provider functions and assert the exact flow, generated session, source mapping, prompt grounding, usage, and empty-message error.
- [ ] Run `pytest tests/test_rag_service.py -q` and confirm failure before implementation.
- [ ] Implement deterministic mock embedding, optional OpenAI-compatible embeddings, Qdrant collection/upsert/search with `kb_id`/`tenant_id`/`permission` filters, pass-through reranking, OpenAI-compatible DeepSeek/OpenAI/DashScope LLM calls, and the fixed `rag_chat` signature.
- [ ] Ensure the no-results path returns `知识库中没有找到明确答案。` without calling the LLM.
- [ ] Run `pytest tests/test_rag_service.py -q` and confirm all tests pass.

### Task 3: WeChat transport

**Files:**
- Create: `wechat_rag_bot/app/services/wechat_service.py`
- Create: `wechat_rag_bot/app/routers/wechat.py`
- Test: `wechat_rag_bot/tests/test_wechat.py`

- [ ] Write tests for valid/invalid SHA-1 verification, XML parsing/escaping, non-text replies, RAG delegation, and duplicate `MsgId` replies.
- [ ] Run `pytest tests/test_wechat.py -q` and confirm failure before implementation.
- [ ] Implement signature verification, safe XML parsing, XML reply generation, bounded in-memory deduplication, and router calls exclusively to `rag_chat`.
- [ ] Run `pytest tests/test_wechat.py -q` and confirm all tests pass.

### Task 4: Unified chat API and application assembly

**Files:**
- Create: `wechat_rag_bot/app/routers/chat.py`
- Create: `wechat_rag_bot/app/main.py`
- Create: package `__init__.py` files
- Test: `wechat_rag_bot/tests/test_chat_api.py`

- [ ] Write API tests for `/api/v1/chat`, missing/invalid bearer credentials, automatic session IDs, empty messages, and unified validation/internal-error envelopes.
- [ ] Run `pytest tests/test_chat_api.py -q` and confirm failure before implementation.
- [ ] Implement the chat adapter, exception mapping, request validation envelope, health endpoint, and router registration.
- [ ] Run `pytest tests/test_chat_api.py -q` and confirm all tests pass.

### Task 5: Knowledge upload and indexing

**Files:**
- Create: `wechat_rag_bot/app/services/knowledge_service.py`
- Create: `wechat_rag_bot/app/routers/knowledge.py`
- Test: `wechat_rag_bot/tests/test_knowledge.py`

- [ ] Write tests for `.txt`, `.md`, `.pdf` dispatch, unsupported formats, safe filenames, chunk overlap, fixed payload fields, provider calls, and upload response envelope.
- [ ] Run `pytest tests/test_knowledge.py -q` and confirm failure before implementation.
- [ ] Implement streaming upload storage, text/PDF extraction, overlap chunking, stable document/chunk IDs, embedding batches, and Qdrant upsert payloads.
- [ ] Run `pytest tests/test_knowledge.py -q` and confirm all tests pass.

### Task 6: Database boundary and delivery files

**Files:**
- Create: `wechat_rag_bot/app/db/models.py`
- Create: `wechat_rag_bot/app/db/session.py`
- Create: `wechat_rag_bot/requirements.txt`
- Create: `wechat_rag_bot/.env.example`
- Create: `wechat_rag_bot/docker-compose.yml`
- Create: `wechat_rag_bot/Dockerfile`
- Create: `wechat_rag_bot/README.md`
- Create: `wechat_rag_bot/data/uploads/.gitkeep`

- [ ] Add minimal SQLAlchemy session/model scaffolding without coupling it to the MVP request path.
- [ ] Add exact required environment variables and dependencies.
- [ ] Add Docker startup using `uvicorn app.main:app`.
- [ ] Document local/Docker startup, all API examples, WeChat configuration, provider modes, security notes, and extension paths.

### Task 7: Verification

**Files:**
- Verify all files above.

- [ ] Run the narrow test files individually and fix failures at their owning layer.
- [ ] Run `pytest -q` after narrow tests pass.
- [ ] Run `python -m compileall app`.
- [ ] Import `app.main:app` and assert required routes are registered.
- [ ] Search for hard-coded secrets and verify the Qdrant payload keys, error codes, route paths, and `rag_chat` signature against the specification.
