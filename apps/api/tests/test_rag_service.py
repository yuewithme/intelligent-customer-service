import pytest

from app.shared.schemas.common import AppError, ErrorCode
from app.domains.conversations.schemas.context import ContextPackage
from app.domains.decisioning.schemas.policy import PolicyDecision
from app.services import llm_service, rag_service


def test_care_retrieval_excludes_sales_sections():
    docs = [
        {"section": "CHUNK CARE-0001｜养护问答", "text": "care"},
        {"section": "CHUNK SCRIPT-0001｜催单", "text": "sales"},
        {"section": "CHUNK FLOW-0001｜成交", "text": "flow"},
        {"section": "CHUNK SOP-0001｜跟进", "text": "sop"},
        {
            "section": "青山玉泉 - 花期话术",
            "chunk_type": "花期",
            "text": "legacy sales copy",
        },
        {
            "section": "青山玉泉 - 花期",
            "source_table": "orchid_sales_copy",
            "text": "typed sales copy",
        },
        {"section": "烂根处理", "text": "structured orchid knowledge"},
        {
            "section": "小国魂 - 产品基础信息",
            "entity_type": "orchid_product",
            "text": "建兰经典色花，价格十几元起。",
        },
        {
            "section": "国魂 - 交易认知",
            "source_table": "orchid_products",
            "text": "主流成交价 80-200 元每苗。",
        },
    ]

    assert rag_service.select_care_docs(docs) == [docs[0], docs[6]]


def test_care_retrieval_excludes_product_advantage_docs_even_with_care_words():
    docs = [
        {
            "section": "蕙兰优势",
            "text": "蕙兰根系发达，养护简单，适合购买。",
        },
        {
            "section": "烂根处理",
            "text": "烂根后先检查植料和通风。",
        },
    ]

    assert rag_service.select_care_docs(docs) == [docs[1]]


def test_product_recommendation_retrieval_keeps_product_copy_only():
    docs = [
        {"section": "小国魂 - 养护难度话术", "text": "皮实好养"},
        {"section": "CHUNK SCRIPT-0001｜催单", "text": "立即下单"},
        {"section": "小国魂 - 产品基础信息", "text": "适合新手"},
    ]

    assert rag_service.select_product_recommendation_docs(docs) == [docs[0], docs[2]]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (
            "这两款都比较适合新手。购买后会给您开通视频课程和一对一群。",
            "这两款都比较适合新手。",
        ),
        (
            "塑料盆可以继续用，我再送您一些植料并随货发出。",
            "__HANDOFF__",
        ),
        (
            "这款适合新手，视频课程是否包含还需要核实。",
            "这款适合新手，视频课程是否包含还需要核实。",
        ),
        (
            "好养的可以看看建兰市长红梅，这款很适合新手，"
            "咱们配套的视频课程里也有详细的养护教学。"
            "您打算把花放在阳台还是客厅养呢？",
            "好养的可以看看建兰市长红梅，这款很适合新手，"
            "您打算把花放在阳台还是客厅养呢？",
        ),
    ],
)
def test_rag_removes_unverified_service_and_fulfillment_claims(answer, expected):
    assert rag_service.remove_unverified_capability_claims(answer) == expected


def test_rag_keeps_service_claims_backed_by_verified_membership_facts():
    answer = (
        "这种根系问题容易反复，萧岚苑有系统的视频课程，"
        "也有老师结合实际情况做一对一指导，能少走一些弯路。"
    )

    assert rag_service.remove_unverified_capability_claims(
        answer,
        ["系统的视频课程", "结合具体养护问题的一对一指导"],
    ) == answer


def test_care_reply_quality_requires_evidence_and_one_verified_brand_bridge():
    context = ContextPackage(
        profile_summary={"ai_summary": "客户在西安，刚入门。"},
        session_state={
            "sales_action": {
                "sales_action": "discover_pain",
                "brand_value_facts": [
                    {
                        "brand": "萧岚苑",
                        "service_capabilities": [
                            "系统的视频课程",
                            "结合具体养护问题的一对一指导",
                        ],
                    }
                ],
            }
        }
    )

    assert rag_service.care_reply_violations(
        "西安气候干燥，先减少浇水。",
        message="一盆很多黑根空根，我修剪重新栽了。",
        context=context,
    ) == [
        "unsupported_regional_environment_claim",
        "missing_verified_brand_bridge",
    ]
    assert rag_service.care_reply_violations(
        "西安天气热，两三天浇一次容易积水。",
        message="天气热，我两三天浇一次。",
        context=context,
    ) == [
        "unsupported_regional_environment_claim",
    ]
    assert rag_service.care_reply_violations(
        "西安现在气候干燥，先减少浇水。",
        message="一盆很多黑根空根，我修剪重新栽了。",
        context=context,
    ) == [
        "unsupported_regional_environment_claim",
        "missing_verified_brand_bridge",
    ]
    assert "unsupported_regional_environment_claim" in (
        rag_service.care_reply_violations(
            "西安现在气温低，兰花生长会慢一些。",
            message="怎么才能养好根？",
            context=context,
        )
    )
    assert rag_service.care_reply_violations(
        "黑根空根常和根系长期缺氧有关，先按植料实际干湿控制浇水。"
        "萧岚苑有老师结合实际情况做一对一指导，能帮您逐步排掉反复烂根的诱因。",
        message="一盆很多黑根空根，我修剪重新栽了。",
        context=context,
    ) == []


def test_care_reply_finalizer_removes_live_region_claim_and_closes_brand_gap():
    context = ContextPackage(
        profile_summary={"ai_summary": "客户在西安，刚入门。"},
        session_state={
            "sales_action": {
                "sales_action": "discover_pain",
                "brand_value_facts": [
                    {
                        "service_capabilities": [
                            "系统的视频课程",
                            "结合具体养护问题的一对一指导",
                        ]
                    }
                ],
            }
        }
    )

    answer = rag_service._finalize_repaired_care_answer(
        "西安气温低，兰花生长慢。先看植料实际干湿再浇水。",
        message="黑根空根怎么处理？",
        context=context,
    )

    assert "西安气温低" not in answer
    assert "先看植料实际干湿再浇水" in answer
    assert "萧岚苑" in answer
    assert "系统视频课" in answer
    assert "一对一指导" in answer


def test_care_reply_rewrites_or_removes_a_repeated_follow_up_question():
    repeated = "你目前用的植料是树皮还是水苔"
    context = ContextPackage(
        recent_turns=[
            {"role": "assistant", "content": f"先保持通风。{repeated}"}
        ],
        session_state={"sales_action": {"sales_action": "discover_pain"}},
    )
    answer = f"浇水要看植料实际干湿，不要只按天数。{repeated}"

    assert rag_service.care_reply_violations(
        answer,
        message="天气热，我两三天浇一次。",
        context=context,
    ) == ["repeated_follow_up_question"]
    assert rag_service._finalize_repaired_care_answer(
        answer,
        message="天气热，我两三天浇一次。",
        context=context,
    ) == "浇水要看植料实际干湿，不要只按天数。"


def test_product_catalog_docs_only_include_fields_released_for_the_stage():
    products = [
        {
            "item_id": "1001",
            "title": "小国魂",
            "price_cent": 2990,
            "stock": 8,
            "knowledge": {
                "product_name": "小国魂",
                "flower_color": "红色",
                "highlighted_features": "稀有卖点",
                "sales_copy": "立即下单话术",
            },
        }
    ]

    catalog_doc = rag_service._catalog_product_docs(products, {"product_catalog"})[0]
    value_doc = rag_service._catalog_product_docs(
        products, {"product_catalog", "product_value"}
    )[0]

    assert "小国魂" in catalog_doc["text"]
    assert "红色" in catalog_doc["text"]
    assert "稀有卖点" not in catalog_doc["text"]
    assert "立即下单话术" not in catalog_doc["text"]
    assert "29.90" not in catalog_doc["text"]
    assert "当前库存" not in catalog_doc["text"]
    assert "稀有卖点" in value_doc["text"]
    assert "29.90" not in value_doc["text"]


def test_stage_source_filter_blocks_product_and_promotion_rows_from_care_only_access():
    docs = [
        {"source_table": "orchid_common_knowledge", "text": "养护"},
        {"source_table": "youzan_product_knowledge", "text": "商品"},
        {"source_table": "orchid_sales_copy", "text": "塑品"},
        {"source_table": "activities", "text": "活动"},
    ]

    assert rag_service.select_stage_allowed_docs(docs, {"care_safe"}) == [docs[0]]


def test_rag_removes_business_claims_not_allowed_by_care_sources():
    answer = (
        "烂根常见原因是植料长期积水、通风不足。"
        "推荐您购买建兰忆香荷，这款售价68元且有现货。"
        "您的订单已经支付成功。"
    )

    result = rag_service.remove_disallowed_business_claims(
        answer,
        {"customer_context", "care_safe"},
    )

    assert result == "烂根常见原因是植料长期积水、通风不足。"


def test_product_recommendation_retrieval_question_includes_recent_context():
    context = ContextPackage(
        recent_turns=[
            {"role": "user", "content": "国兰有什么推荐的"},
            {"role": "assistant", "content": "推荐小国魂和蕙兰。"},
        ]
    )
    policy = PolicyDecision(
        route="rag_answer",
        retrieval_policy={"mode": "product_recommendation"},
    )

    question = rag_service.build_retrieval_question(
        "我想找好养活的",
        context,
        policy,
    )

    assert "国兰有什么推荐的" in question
    assert "推荐小国魂和蕙兰" in question
    assert question.endswith("user: 我想找好养活的")


@pytest.mark.asyncio
async def test_product_recommendation_uses_new_catalog_only(monkeypatch):
    from app.core.config import get_settings

    async def fail_embed(text):
        del text
        raise AssertionError("local product recommendations must not require embeddings")

    async def fail_legacy_search(*args, **kwargs):
        del args, kwargs
        raise AssertionError("product recommendation must not query legacy knowledge")

    async def fake_rerank(question, docs, top_n):
        assert "好养" in question
        assert docs[0]["source_table"] == "youzan_product_knowledge"
        return docs[:top_n]

    async def fake_generate(prompt, *, purpose):
        assert "逸红双娇" in prompt
        assert "¥29.90" in prompt
        assert purpose == "rag"
        return {"answer": "推荐小国魂，别名逸红双娇。", "usage": {}}

    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fail_embed)
    monkeypatch.setattr(rag_service.qdrant_service, "search_chunks", fail_legacy_search)
    monkeypatch.setattr(rag_service, "search_orchid_knowledge_chunks", fail_legacy_search)
    monkeypatch.setattr(
        rag_service,
        "search_catalog_products",
        lambda keyword, limit: [
            {
                "item_id": "1001",
                "title": "小国魂 3苗",
                "price_cent": 2990,
                "stock": 8,
                "knowledge": {
                    "product_name": "小国魂",
                    "aliases": "逸红双娇",
                    "care_scenes": "好养",
                },
            }
        ],
    )
    monkeypatch.setattr(rag_service.rerank_service, "rerank", fake_rerank)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fake_generate)

    try:
        result = await rag_service.rag_chat(
            user_id="user_001",
            message="想找好养的兰花",
            kb_id="kb_default",
            metadata={"tenant_id": "tenant_default", "permission": "public"},
            policy=PolicyDecision(
                route="rag_answer",
                retrieval_policy={"mode": "product_recommendation"},
            ),
        )
    finally:
        get_settings.cache_clear()

    assert result["answer"] == "推荐小国魂，别名逸红双娇。"
    assert result["sources"][0]["file_name"] == "产品知识库"


@pytest.mark.asyncio
async def test_explicit_recommendation_bypasses_embeddings_even_if_policy_mode_is_lost(
    monkeypatch,
):
    from app.core.config import get_settings

    async def fail_embed(text):
        del text
        raise AssertionError("explicit product recommendation must stay on local catalog")

    async def fake_rerank(question, docs, top_n):
        del question, top_n
        return docs

    async def fake_generate(prompt, *, purpose):
        del prompt, purpose
        return {"answer": "推荐小国魂。", "usage": {}}

    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fail_embed)
    monkeypatch.setattr(
        rag_service,
        "search_catalog_products",
        lambda keyword, limit: [
            {
                "item_id": "1001",
                "title": "小国魂",
                "knowledge": {"product_name": "小国魂", "care_scenes": "好养"},
            }
        ],
    )
    monkeypatch.setattr(rag_service.rerank_service, "rerank", fake_rerank)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fake_generate)

    try:
        result = await rag_service.rag_chat(
            user_id="user_001",
            message="推荐一款好养的兰花",
            kb_id="kb_default",
            metadata={"tenant_id": "tenant_default", "permission": "public"},
            policy=PolicyDecision(route="rag_answer", retrieval_policy={}),
        )
    finally:
        get_settings.cache_clear()

    assert result["answer"] == "推荐小国魂。"


def test_generic_rag_excludes_legacy_product_rows_but_keeps_common_care():
    docs = [
        {
            "source_table": "orchid_variety_traits",
            "section": "旧品种特征",
            "text": "旧商品资料",
        },
        {
            "source_table": "orchid_common_knowledge",
            "section": "通用养护",
            "text": "浇水见干见湿",
        },
    ]

    assert rag_service._select_non_sales_docs(docs) == [docs[1]]


@pytest.mark.asyncio
async def test_rag_chat_orchestrates_services(monkeypatch):
    from app.core.config import get_settings

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

    async def fake_generate(prompt, *, purpose):
        calls.append(("llm", prompt))
        assert purpose == "rag_fast"
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
    assert set(result["stage_latencies"]) == {
        "embedding_ms",
        "search_ms",
        "rerank_ms",
        "prompt_ms",
        "generation_ms",
    }
    assert calls[1][2]["kb_id"] == "kb_orchid_basic"
    assert calls[1][2]["tenant_id"] == "tenant_default"
    llm_prompt = next(call[1] for call in calls if call[0] == "llm")
    assert "读取【参考资料】与【用户问题】" in llm_prompt
    assert "报销需要主管审批。" in llm_prompt


@pytest.mark.asyncio
async def test_rag_chat_uses_policy_knowledge_base_and_prompt_blocks(monkeypatch):
    from app.core.config import get_settings

    calls = []
    policy = PolicyDecision(
        route="rag_answer",
        knowledge_base_ids=["kb_orchid_basic"],
        prompt_block_ids=["base.customer_service", "segment.beginner"],
    )
    context = ContextPackage(session_state={"sales_stage": "consulting"})

    async def fake_embed(text):
        del text
        return [0.1, 0.2]

    async def fake_search(vector, **filters):
        del vector
        calls.append(("search", filters))
        return [
            {
                "text": "beginner care snippet",
                "doc_id": "doc_policy",
                "file_name": "policy.md",
                "score": 0.9,
            }
        ]

    async def fake_rerank(question, docs, top_n):
        del question
        return docs[:top_n]

    async def fake_build_prompt(*, question, docs, policy=None, context=None, templates=None):
        del docs
        calls.append(("prompt", question, policy, context, templates))
        return "policy prompt"

    async def fake_generate(prompt, *, purpose):
        assert prompt == "policy prompt"
        assert purpose == "rag_fast"
        return {"answer": "policy answer", "usage": {"prompt_tokens": 1}}

    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service.qdrant_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service.rerank_service, "rerank", fake_rerank)
    monkeypatch.setattr(rag_service, "build_rag_prompt", fake_build_prompt)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fake_generate)

    try:
        result = await rag_service.rag_chat(
            user_id="user_001",
            message="how to care",
            kb_id="kb_default",
            metadata={"tenant_id": "tenant_default", "permission": "public"},
            policy=policy,
            context=context,
            templates=["opening_beginner_care"],
        )
    finally:
        get_settings.cache_clear()

    assert result["answer"] == "policy answer"
    assert calls[0][0] == "search"
    assert calls[0][1]["kb_id"] == "kb_orchid_basic"
    assert calls[1] == ("prompt", "how to care", policy, context, ["opening_beginner_care"])


@pytest.mark.asyncio
async def test_rag_chat_uses_clean_orchid_kb_for_default_kb(monkeypatch):
    from app.core.config import get_settings

    searched_kb_ids = []

    async def fake_embed(text):
        del text
        return [0.1, 0.2]

    async def fake_search(vector, **filters):
        del vector
        searched_kb_ids.append(filters["kb_id"])
        if filters["kb_id"] != "kb_orchid_basic":
            return []
        return [
            {
                "text": "建兰日常养护要注意通风和植料干湿。",
                "doc_id": "orchid_chunk_1",
                "file_name": "兰花产品知识库",
                "score": 0.88,
            }
        ]

    async def fake_rerank(question, docs, top_n):
        del question
        return docs[:top_n]

    async def fake_generate(prompt, *, purpose):
        assert "建兰日常养护" in prompt
        assert purpose == "rag_fast"
        return {"answer": "建兰养护先看通风和植料干湿。", "usage": {}}

    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fake_embed)
    monkeypatch.setattr(rag_service.qdrant_service, "search_chunks", fake_search)
    monkeypatch.setattr(rag_service.rerank_service, "rerank", fake_rerank)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fake_generate)

    try:
        result = await rag_service.rag_chat(
            user_id="user_001",
            message="建兰怎么养护？",
            kb_id="kb_default",
            metadata={"tenant_id": "tenant_default", "permission": "public"},
        )
    finally:
        get_settings.cache_clear()

    assert searched_kb_ids == ["kb_orchid_basic"]
    assert result["answer"] == "建兰养护先看通风和植料干湿。"
    assert result["sources"][0]["file_name"] == "兰花产品知识库"


@pytest.mark.asyncio
async def test_rag_chat_skips_knowledge_retrieval_when_disabled(monkeypatch):
    from app.core.config import get_settings

    async def fail_embed(text):
        del text
        raise AssertionError("default RAG fallback should not query local knowledge")

    async def fail_generate(prompt):
        del prompt
        raise AssertionError("disabled knowledge must not use an ungrounded LLM fallback")

    monkeypatch.setenv("RAG_KNOWLEDGE_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(rag_service.embedding_service, "embed_text", fail_embed)
    monkeypatch.setattr(rag_service.llm_service, "generate_answer", fail_generate)

    try:
        result = await rag_service.rag_chat(
            "user_001",
            "修根后要干一些再浇吗？",
            "kb_default",
        )
    finally:
        get_settings.cache_clear()

    assert result["answer"] == ""
    assert result["sources"] == []


@pytest.mark.asyncio
async def test_rag_chat_rejects_empty_message():
    with pytest.raises(AppError) as exc:
        await rag_service.rag_chat("user_001", "  ", "kb_default")

    assert exc.value.code == ErrorCode.MESSAGE_EMPTY


@pytest.mark.asyncio
async def test_rag_chat_returns_empty_answer_without_context_or_llm(monkeypatch):
    from app.core.config import get_settings

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

    assert result["answer"] == ""
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

    assert "顶尖的私域销售客服" in prompt
    assert "读取【参考资料】与【用户问题】" in prompt
    assert "必须优先参考【参考资料】回答" in prompt
    assert "如果资料内容存在轻微差异，但能提炼共同原则" in prompt
    assert "不要出现“参考资料”“知识库”“资料显示”“根据资料”“系统判断”“推荐回复”" in prompt
    assert "直接输出可发送给用户的完整客服话术" in prompt


def test_rag_prompt_uses_internal_handoff_marker_when_unanswerable():
    prompt = rag_service.PROMPT_TEMPLATE.format(
        context="兰花养护资料",
        question="黑斑黄叶腐苗，去年全军覆没",
    )
    assert "信息不足" in prompt
    assert "不要追问或编造" in prompt
    assert "只输出 __HANDOFF__" in prompt
    assert "内部控制标记" in prompt


@pytest.mark.asyncio
async def test_rag_prompt_includes_sales_action_constraints():
    from app.domains.conversations.schemas.context import ContextPackage
    from app.domains.decisioning.schemas.policy import PolicyDecision

    prompt = await rag_service.build_rag_prompt(
        question="多少钱",
        docs=[{"file_name": "product.md", "text": "价格为199元"}],
        policy=PolicyDecision(route="rag_answer"),
        context=ContextPackage(
            session_state={
                "sales_action": {
                    "reply_goal": "回答价格并确认使用数量",
                    "sales_action": "discover_need",
                    "question_slot": "plant_count",
                }
            }
        ),
    )

    assert "回答价格并确认使用数量" in prompt
    assert "plant_count" in prompt
    assert "Answer the user's current question first" in prompt
    assert "Ask at most one follow-up question" in prompt


@pytest.mark.asyncio
async def test_volcengine_llm_uses_ark_openai_compatible_endpoint(monkeypatch):
    from app.core.config import get_settings

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
            captured["timeout"] = timeout

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
    assert captured["timeout"] == 60
    assert result["answer"] == "火山方舟回答"
