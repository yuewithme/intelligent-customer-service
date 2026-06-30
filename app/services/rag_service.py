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


PROMPT_TEMPLATE = """你是兰花私域客服的知识库问答助手。

请根据【知识库资料】回答用户问题。资料与问题相关但没有直接标准答案时，可以把资料中的养护原则、风险提醒和客服话术综合起来，用客服语气综合生成一个自然、温和、可执行的回答。
不要编造知识库资料之外的具体事实，不要承诺资料没有支持的天数、药量、疗效、库存、价格、赔付或售后结论。
如果资料明显低相关、互相冲突，或必须依赖图片/订单/药剂名称等关键信息才能判断，请回答：“知识库中没有找到明确答案。”

【知识库资料】
{context}

【用户问题】
{question}

【回答要求】
1. 回答清楚、直接，语气像真实客服：先回应用户关切，再给建议，避免生硬说教。
2. 不要在回答正文或结尾写“来源”，不要列出来源文件、页码或资料标题。
3. 不要输出与问题无关的内容。
4. 不要编造知识库中不存在的信息。
5. 优先使用每个资料块中的【推荐回复】或“推荐回复”字段内容。
6. 不要复述 chunk 标题、知识类型、适用场景、标签、下一步动作 等元信息。
7. 如果某条资料的推荐回复包含省略号或明显未完句，不要照抄该句；应优先使用其他完整资料块。
8. 如果多个资料块冲突，优先选择与用户问题最直接相关、内容完整、非销售推进话术的资料。
9. 综合生成时要说明边界条件，例如需结合根系状态、植料干湿、通风、光照、季节或现场情况判断；不确定的地方用“建议再确认”表达。
10. 能综合回答时，不要说“知识库中没有找到明确答案”，不要说“无直接对应明确推荐回复来源”，也不要把“资料是否明确”“暂按综合回应”这类系统判断直接告诉用户；只输出客服可直接发送的话。
11. 回答正文不要出现“知识库”“资料”“来源”“推荐回复”这类内部说明词。
"""


LLM_FALLBACK_PROMPT_TEMPLATE = """我先按通用养护原则给你一个参考。

你是兰花私域客服助手。当前不要调用本地知识库，也不要写来源。
请直接回答用户问题，语气自然、温和，像客服在微信里回复客户。

要求：
1. 可以基于通用兰花养护原则给建议，但不要编造具体商品、库存、价格、订单、赔付或药剂疗效。
2. 涉及图片判断、病害严重程度、药剂搭配、售后赔付、订单物流等关键信息不足时，要温和说明需要进一步确认或转人工。
3. 不要在回答正文或结尾写“来源”，不要提“知识库”“资料”“推荐回复”。
4. 回答要简洁可执行，尽量说明边界条件，例如根系状态、植料干湿、通风、光照、季节或现场情况。

用户问题：
{question}
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
        if not settings.rag_knowledge_enabled:
            result = await llm_service.generate_answer(
                LLM_FALLBACK_PROMPT_TEMPLATE.format(question=message.strip())
            )
            answer = result["answer"]
            usage = result.get("usage", {})
            return {
                "answer": answer,
                "sources": sources,
                "session_id": active_session_id,
                "usage": usage,
            }

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
