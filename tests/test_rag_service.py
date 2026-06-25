import pytest

from app.schemas.common import AppError, ErrorCode
from app.services import llm_service, rag_service


@pytest.mark.asyncio
async def test_rag_chat_orchestrates_services(monkeypatch):
    calls = []

    async def fake_embed(text):
        calls.append(("embed", text))
        return [0.1, 0.2]

    async def fake_search(vector, **filters):
        calls.append(("search", vector, filters))
        return [
            {
                "text": "报销需要主管审批。",
                "doc_id": "doc_001",
                "file_name": "员工手册.pdf",
                "page": 12,
                "section": "报销流程",
                "score": 0.87,
            }
        ]

    async def fake_rerank(question, docs, top_n):
        calls.append(("rerank", question, top_n))
        return docs[:top_n]

    async def fake_generate(prompt):
        calls.append(("llm", prompt))
        return {
            "answer": "报销需要主管审批。",
            "usage": {"prompt_tokens": 20, "completion_tokens": 8},
        }

    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service.qdrant_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service.rerank_service, "rerank", fake_rerank)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fake_generate)

    result = await rag_service.rag_chat(
        user_id="user_001",
        message="如何报销？",
        kb_id="kb_default",
        metadata={"tenant_id": "tenant_default", "permission": "public"},
    )

    assert result["answer"] == "报销需要主管审批。"
    assert result["session_id"].startswith("sess_")
    assert result["sources"][0] == {
        "doc_id": "doc_001",
        "file_name": "员工手册.pdf",
        "page": 12,
        "section": "报销流程",
        "score": 0.87,
    }
    assert result["usage"]["completion_tokens"] == 8
    assert calls[1][2]["kb_id"] == "kb_default"
    assert calls[1][2]["tenant_id"] == "tenant_default"
    assert "不要编造" in calls[3][1]
    assert "报销需要主管审批。" in calls[3][1]


@pytest.mark.asyncio
async def test_rag_chat_rejects_empty_message():
    with pytest.raises(AppError) as exc:
        await rag_service.rag_chat("user_001", "  ", "kb_default")

    assert exc.value.code == ErrorCode.MESSAGE_EMPTY


@pytest.mark.asyncio
async def test_rag_chat_returns_grounded_fallback_without_llm(monkeypatch):
    async def fake_embed(text):
        return [0.1]

    async def fake_search(vector, **filters):
        return []

    async def fail_generate(prompt):
        raise AssertionError("LLM must not be called without context")

    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service.qdrant_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fail_generate)

    result = await rag_service.rag_chat("user_001", "未知问题", "kb_default")

    assert result["answer"] == "知识库中没有找到明确答案。"
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_mock_llm_returns_knowledge_text_not_source_heading():
    result = await llm_service.generate_answer(
        rag_service.PROMPT_TEMPLATE.format(
            context="[1] 来源：员工手册.pdf，第 12 页\n报销需要主管审批。",
            question="如何报销？",
        )
    )

    assert result["answer"] == "报销需要主管审批。"
