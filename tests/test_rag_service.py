import pytest

from app.schemas.common import AppError, ErrorCode
from app.services import llm_service, rag_service


@pytest.mark.asyncio
async def test_rag_chat_orchestrates_services(monkeypatch):
    from app.config import get_settings

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
    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()

    try:
        result = await rag_service.rag_chat(
            user_id="user_001",
            message="如何报销？",
            kb_id="kb_default",
            metadata={"tenant_id": "tenant_default", "permission": "public"},
        )
    finally:
        get_settings.cache_clear()

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
    assert "只根据【知识库资料】回答【用户问题】" in calls[3][1]
    assert "报销需要主管审批。" in calls[3][1]


@pytest.mark.asyncio
async def test_rag_chat_skips_knowledge_retrieval_by_default(monkeypatch):
    from app.config import get_settings

    async def fail_embed(text):
        del text
        raise AssertionError("default RAG fallback should not query local knowledge")

    async def fake_generate(prompt):
        assert "【知识库资料】" not in prompt
        assert "私域销售首单推进_AI客服知识库.md" not in prompt
        return {
            "answer": "可以先放在通风散光的位置观察，结合根系和植料干湿情况再决定是否浇水。",
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
        }

    monkeypatch.delenv("RAG_KNOWLEDGE_ENABLED", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fail_embed)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fake_generate)

    try:
        result = await rag_service.rag_chat(
            "user_001",
            "修根后要干一些再浇吗？",
            "kb_default",
        )
    finally:
        get_settings.cache_clear()

    assert result["answer"] == "可以先放在通风散光的位置观察，结合根系和植料干湿情况再决定是否浇水。"
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_rag_chat_rejects_empty_message():
    with pytest.raises(AppError) as exc:
        await rag_service.rag_chat("user_001", "  ", "kb_default")

    assert exc.value.code == ErrorCode.MESSAGE_EMPTY


@pytest.mark.asyncio
async def test_rag_chat_returns_grounded_fallback_without_llm(monkeypatch):
    from app.config import get_settings

    async def fake_embed(text):
        return [0.1]

    async def fake_search(vector, **filters):
        return []

    async def fail_generate(prompt):
        raise AssertionError("LLM must not be called without context")

    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service.qdrant_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fail_generate)
    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()

    try:
        result = await rag_service.rag_chat("user_001", "未知问题", "kb_default")
    finally:
        get_settings.cache_clear()

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


def test_rag_prompt_discourages_metadata_and_truncated_chunks():
    prompt = rag_service.PROMPT_TEMPLATE.format(
        context="[1] 来源：知识库.md\n**知识类型**：养护问答\n**推荐回复**：浇水见干见湿。\n**下一步动作**：继续追问。",
        question="怎么浇水？",
    )

    assert "兰花/蝴蝶兰私域客服" in prompt
    assert "只根据【知识库资料】回答【用户问题】" in prompt
    assert "优先使用资料块中的【推荐回复】" in prompt
    assert "不要复述资料块中的标题、知识类型、适用场景、标签、下一步动作、来源文件、页码" in prompt
    assert "推荐回复包含省略号、明显断句、内容不完整" in prompt
    assert "参考【知识库资料】回答，组合生成自然、温和、可执行的客服回答" in prompt
    assert "不要自行推断，转人工处理" in prompt
    assert "请转人工处理" in prompt
    assert "能综合回答时，不要说“知识库中没有找到明确答案”" in prompt
    assert "回答正文不要出现“知识库”“资料”“来源”“推荐回复”“根据资料”“系统判断”“暂按综合回应”" in prompt
    assert "只输出最终客服回复" in prompt


@pytest.mark.asyncio
async def test_volcengine_llm_uses_ark_openai_compatible_endpoint(monkeypatch):
    from app.config import get_settings

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": "火山方舟回答"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            }

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    monkeypatch.setenv("LLM_PROVIDER", "volcengine")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    monkeypatch.setenv("VOLCENGINE_API_KEY", "ark_test_key")
    get_settings.cache_clear()
    monkeypatch.setattr(llm_service.httpx, "AsyncClient", FakeClient)

    try:
        result = await llm_service.generate_answer("测试 prompt")
    finally:
        get_settings.cache_clear()

    assert captured["url"] == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer ark_test_key"
    assert captured["json"]["model"] == "deepseek-chat"
    assert result["answer"] == "火山方舟回答"
