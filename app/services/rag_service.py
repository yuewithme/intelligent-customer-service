import time
from typing import Any

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode
from app.services import (
    embedding_service,
    llm_service,
    qdrant_service,
    rerank_service,
)
from app.utils.ids import generate_id
from app.utils.logger import log_event
from app.utils.time import now_iso


PROMPT_TEMPLATE = """你是一个知识库问答助手。

请严格根据【知识库资料】回答用户问题。
如果资料中没有明确答案，请回答：“知识库中没有找到明确答案。”
不要编造，不要使用资料外的信息。

【知识库资料】
{context}

【用户问题】
{question}

【回答要求】
1. 回答清楚、直接。
2. 如果有来源，结尾列出来源文件和页码。
3. 不要输出与问题无关的内容。
4. 不要编造知识库中不存在的信息。
"""


def _source(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "doc_id": doc["doc_id"],
        "file_name": doc["file_name"],
        "page": doc.get("page"),
        "section": doc.get("section"),
        "score": doc.get("score"),
    }


def _context(docs: list[dict[str, Any]]) -> str:
    blocks = []
    for index, doc in enumerate(docs, start=1):
        page = f"，第 {doc['page']} 页" if doc.get("page") else ""
        blocks.append(
            f"[{index}] 来源：{doc.get('file_name', '未知文件')}{page}\n"
            f"{doc.get('text', '')}"
        )
    return "\n\n".join(blocks)


async def rag_chat(
    user_id: str,
    message: str,
    kb_id: str,
    session_id: str | None = None,
    channel: str = "api",
    metadata: dict | None = None,
) -> dict:
    started = time.perf_counter()
    request_id = generate_id("request")
    active_session_id = session_id or generate_id("session")
    metadata = metadata or {}
    status = "success"
    answer = ""
    sources: list[dict[str, Any]] = []

    if not message or not message.strip():
        raise AppError(ErrorCode.MESSAGE_EMPTY)

    try:
        settings = get_settings()
        vector = await embedding_service.embed_text(message.strip())
        candidates = await qdrant_service.search_chunks(
            vector,
            kb_id=kb_id,
            tenant_id=metadata.get("tenant_id", "tenant_default"),
            permission=metadata.get("permission", "public"),
            top_k=settings.rag_top_k,
        )
        docs = await rerank_service.rerank(
            message.strip(), candidates, settings.rag_top_n
        )
        sources = [_source(doc) for doc in docs]
        if docs:
            result = await llm_service.generate_answer(
                PROMPT_TEMPLATE.format(
                    context=_context(docs),
                    question=message.strip(),
                )
            )
            answer = result["answer"]
            usage = result.get("usage", {})
        else:
            answer = "知识库中没有找到明确答案。"
            usage = {"prompt_tokens": 0, "completion_tokens": 0}
        return {
            "answer": answer,
            "sources": sources,
            "session_id": active_session_id,
            "usage": usage,
        }
    except Exception:
        status = "failed"
        raise
    finally:
        log_event(
            {
                "request_id": request_id,
                "channel": channel,
                "user_id": user_id,
                "session_id": active_session_id,
                "kb_id": kb_id,
                "question": message,
                "answer": answer,
                "sources": sources,
                "latency_ms": round((time.perf_counter() - started) * 1000),
                "status": status,
                "created_at": now_iso(),
            }
        )


async def answer_knowledge(message, user_state) -> dict:
    del user_state
    result = await rag_chat(
        user_id=message.user_id,
        message=message.message,
        kb_id=message.kb_id,
        session_id=message.session_id,
        channel=message.channel,
        metadata={
            **message.metadata,
            "tenant_id": message.tenant_id,
            "permission": message.permission,
        },
    )
    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "usage": result.get("usage", {}),
    }
