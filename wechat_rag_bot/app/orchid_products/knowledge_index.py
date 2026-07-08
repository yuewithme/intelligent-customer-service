import json
import math
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.models import Base, OrchidKnowledgeChunkModel
from app.services import embedding_service, qdrant_service


ORCHID_KB_IDS = {"kb_orchid_basic", "kb_orchid_advanced", "kb_best_practices"}
_sessionmakers: dict[str, sessionmaker] = {}


def get_session_factory() -> sessionmaker:
    db_url = get_settings().database_url
    factory = _sessionmakers.get(db_url)
    if factory is None:
        engine = create_engine(db_url)
        Base.metadata.create_all(engine, tables=[OrchidKnowledgeChunkModel.__table__])
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[db_url] = factory
    return factory


async def index_orchid_knowledge_chunks(
    batch_size: int | None = None,
    *,
    sync_existing: bool = False,
) -> dict[str, int]:
    settings = get_settings()
    batch_size = batch_size or settings.embedding_batch_size
    factory = get_session_factory()
    indexed = 0
    skipped = 0

    with factory() as session:
        rows = (
            session.query(OrchidKnowledgeChunkModel)
            .order_by(OrchidKnowledgeChunkModel.id)
            .all()
        )
        if not sync_existing:
            rows = [row for row in rows if row.embedding_json is None]
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            missing_rows = [row for row in batch if row.embedding_json is None]
            new_vectors = {}
            if missing_rows:
                texts = [_embedding_text(row) for row in missing_rows]
                vectors = await embedding_service.embed_texts(texts)
                new_vectors = dict(zip([row.id for row in missing_rows], vectors))
            points = []
            for row in batch:
                vector = (
                    new_vectors.get(row.id)
                    if row.embedding_json is None
                    else _load_vector(row.embedding_json)
                )
                if not vector:
                    skipped += 1
                    continue
                if row.embedding_json is None:
                    row.embedding_json = json.dumps(vector, ensure_ascii=False)
                    indexed += 1
                points.append(_qdrant_point(row, vector))
            session.commit()
            if points:
                await qdrant_service.upsert_chunks(points)

    return {"indexed": indexed, "skipped": skipped}


async def search_orchid_knowledge_chunks(
    vector: list[float],
    *,
    kb_id: str,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    if kb_id not in ORCHID_KB_IDS:
        return []

    settings = get_settings()
    limit = top_k or settings.rag_top_k
    factory = get_session_factory()
    scored: list[dict[str, Any]] = []
    with factory() as session:
        rows = (
            session.query(OrchidKnowledgeChunkModel)
            .filter(OrchidKnowledgeChunkModel.embedding_json.is_not(None))
            .all()
        )
        for row in rows:
            stored_vector = _load_vector(row.embedding_json)
            if not stored_vector:
                continue
            score = _cosine(vector, stored_vector)
            scored.append(
                {
                    "text": row.content,
                    "kb_id": kb_id,
                    "doc_id": f"orchid_chunk_{row.id}",
                    "chunk_id": f"orchid_chunk_{row.id}",
                    "file_name": "兰花产品知识库",
                    "file_type": "db",
                    "page": None,
                    "section": row.chunk_title or row.chunk_type,
                    "tenant_id": "tenant_default",
                    "permission": "public",
                    "score": score,
                    "category_name": row.category_name,
                    "variety_name": row.variety_name,
                    "chunk_type": row.chunk_type,
                }
            )

    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def _embedding_text(row: OrchidKnowledgeChunkModel) -> str:
    title = row.chunk_title or row.chunk_type or ""
    return f"{title}\n{row.content}".strip()


def _qdrant_point(row: OrchidKnowledgeChunkModel, vector: list[float]) -> dict[str, Any]:
    chunk_id = f"orchid_chunk_{row.id}"
    return {
        "id": chunk_id,
        "vector": vector,
        "payload": {
            "text": row.content,
            "kb_id": "kb_orchid_basic",
            "doc_id": chunk_id,
            "chunk_id": chunk_id,
            "file_name": "兰花产品知识库",
            "file_type": "db",
            "page": None,
            "section": row.chunk_title or row.chunk_type,
            "tenant_id": "tenant_default",
            "permission": "public",
            "category_name": row.category_name,
            "variety_name": row.variety_name,
            "chunk_type": row.chunk_type,
        },
    }


def _load_vector(value: str | None) -> list[float]:
    if not value:
        return []
    try:
        return [float(item) for item in json.loads(value)]
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _cosine(left: list[float], right: list[float]) -> float:
    size = min(len(left), len(right))
    if size == 0:
        return 0.0
    dot = sum(left[index] * right[index] for index in range(size))
    left_norm = math.sqrt(sum(left[index] * left[index] for index in range(size)))
    right_norm = math.sqrt(sum(right[index] * right[index] for index in range(size)))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)
