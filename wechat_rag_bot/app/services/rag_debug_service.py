from typing import Any

from app.config import get_settings
from app.schemas.log import RagDebugSearchRequest
from app.services import embedding_service, qdrant_service, rerank_service
from app.services.rag_service import build_rag_prompt


async def debug_rag_search(request: RagDebugSearchRequest) -> dict:
    settings = get_settings()
    message = request.message.strip()
    search_kb_ids = _search_kb_ids(request)
    top_k = request.top_k or settings.rag_top_k
    top_n = request.top_n or settings.rag_top_n

    vector = await embedding_service.embed_text(message)
    candidates: list[dict[str, Any]] = []
    for kb_id in search_kb_ids:
        candidates.extend(
            await qdrant_service.search_chunks(
                vector,
                kb_id=kb_id,
                tenant_id=request.tenant_id,
                permission=request.permission,
                top_k=top_k,
            )
        )

    reranked_docs = await rerank_service.rerank(message, candidates, top_n)
    prompt_preview = None
    prompt_truncated = False
    if request.include_prompt:
        prompt = await build_rag_prompt(question=message, docs=reranked_docs)
        prompt_preview = prompt[: request.max_prompt_chars]
        prompt_truncated = len(prompt) > request.max_prompt_chars

    return {
        "message": message,
        "search_kb_ids": search_kb_ids,
        "tenant_id": request.tenant_id,
        "permission": request.permission,
        "top_k": top_k,
        "top_n": top_n,
        "candidate_count": len(candidates),
        "candidates": [_debug_doc(doc) for doc in candidates],
        "reranked_docs": [_debug_doc(doc) for doc in reranked_docs],
        "prompt_preview": prompt_preview,
        "prompt_truncated": prompt_truncated,
    }


def _search_kb_ids(request: RagDebugSearchRequest) -> list[str]:
    ids = [kb_id for kb_id in request.knowledge_base_ids if kb_id.strip()]
    return ids or [request.kb_id]


def _debug_doc(doc: dict[str, Any]) -> dict:
    return {
        "kb_id": doc.get("kb_id"),
        "doc_id": doc.get("doc_id"),
        "chunk_id": doc.get("chunk_id"),
        "file_name": doc.get("file_name"),
        "page": doc.get("page"),
        "section": doc.get("section"),
        "score": doc.get("score"),
        "text_preview": str(doc.get("text") or "")[:500],
    }
