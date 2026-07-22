import pytest

from app.domains.knowledge.services.rerank_service import rerank


@pytest.mark.asyncio
async def test_hybrid_rerank_promotes_keyword_and_section_matches_over_vector_only():
    docs = [
        {
            "doc_id": "general",
            "text": "兰花日常养护需要注意通风、光照和浇水节奏。",
            "section": "兰花养护",
            "file_name": "care.md",
            "score": 0.95,
        },
        {
            "doc_id": "root_rot",
            "text": "兰花烂根后要先脱盆检查根系，修剪黑腐根，晾根后再上盆。",
            "section": "烂根处理步骤",
            "file_name": "root_rot.md",
            "score": 0.62,
        },
    ]

    ranked = await rerank("兰花烂根怎么办", docs, top_n=2)

    assert [doc["doc_id"] for doc in ranked] == ["root_rot", "general"]
    assert ranked[0]["rerank_score"] > ranked[1]["rerank_score"]
    assert ranked[0]["rerank_reason"]["section_score"] > 0
    assert ranked[0]["rerank_reason"]["keyword_score"] > 0


@pytest.mark.asyncio
async def test_hybrid_rerank_keeps_scores_explainable_and_limits_top_n():
    docs = [
        {"doc_id": "a", "text": "A", "score": 0.1},
        {"doc_id": "b", "text": "B", "score": 0.2},
    ]

    ranked = await rerank("question", docs, top_n=1)

    assert len(ranked) == 1
    assert ranked[0]["doc_id"] == "b"
    assert set(ranked[0]["rerank_reason"]) == {
        "vector_score",
        "keyword_score",
        "section_score",
        "file_name_score",
        "exact_phrase_score",
    }
