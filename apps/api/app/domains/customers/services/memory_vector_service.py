from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.core.config import get_settings
from app.infrastructure.database.models import MemoryEpisodeModel
from app.shared.schemas.common import AppError, ErrorCode
from app.domains.knowledge.services.embedding_service import embed_text
from app.domains.customers.services.memory_repository import get_memory_session


_memory_points: dict[str, dict[str, Any]] = {}


def _is_memory_mode() -> bool:
    url = get_settings().qdrant_url.strip()
    return not url or "your-qdrant-url" in url


def _client():
    from qdrant_client import AsyncQdrantClient

    settings = get_settings()
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
        trust_env=settings.qdrant_trust_env,
    )


def _point_id(tenant_id: str, subject_id: str, episode_id: int) -> str:
    key = f"memory-episode:{tenant_id}:{subject_id}:{episode_id}"
    return str(uuid5(NAMESPACE_URL, key))


def _episode_text(episode: MemoryEpisodeModel) -> str:
    values = [episode.episode_type, episode.title, episode.summary, episode.outcome]
    return "\n".join(str(value) for value in values if value)


async def ensure_memory_collection() -> None:
    if _is_memory_mode():
        return
    try:
        from qdrant_client.models import Distance, PayloadSchemaType, VectorParams

        settings = get_settings()
        client = _client()
        collection_exists = await client.collection_exists(
            settings.qdrant_memory_collection
        )
        if not collection_exists:
            await client.create_collection(
                collection_name=settings.qdrant_memory_collection,
                vectors_config=VectorParams(
                    size=settings.qdrant_vector_size,
                    distance=getattr(Distance, settings.qdrant_distance.upper()),
                ),
            )
            indexed_fields: set[str] = set()
        else:
            collection = await client.get_collection(
                settings.qdrant_memory_collection
            )
            indexed_fields = set((collection.payload_schema or {}).keys())

        for field_name in ("tenant_id", "subject_id", "status"):
            if field_name in indexed_fields:
                continue
            await client.create_payload_index(
                collection_name=settings.qdrant_memory_collection,
                field_name=field_name,
                field_schema=PayloadSchemaType.KEYWORD,
                wait=True,
            )
    except Exception as exc:
        raise AppError(ErrorCode.QDRANT_FAILED, status_code=502) from exc


async def index_memory_episode(
    *, tenant_id: str, subject_id: str, episode_id: int
) -> bool:
    """Build a replaceable vector projection from an SQL-owned episode."""
    with get_memory_session() as session:
        episode = session.scalar(
            select(MemoryEpisodeModel).where(
                MemoryEpisodeModel.id == episode_id,
                MemoryEpisodeModel.tenant_id == tenant_id,
                MemoryEpisodeModel.subject_id == subject_id,
                MemoryEpisodeModel.status == "active",
            )
        )
        if episode is None:
            return False
        vector = await embed_text(_episode_text(episode))
        settings = get_settings()
        embedding_version = f"{settings.embedding_provider}:{settings.embedding_model}"
        point_id = _point_id(tenant_id, subject_id, episode.id)
        payload = {
            "episode_id": episode.id,
            "tenant_id": tenant_id,
            "subject_id": subject_id,
            "status": episode.status,
            "episode_type": episode.episode_type,
            "started_at": episode.started_at.isoformat(),
            "embedding_version": embedding_version,
        }

        if _is_memory_mode():
            _memory_points[point_id] = {
                "id": point_id,
                "vector": vector,
                "payload": payload,
            }
        else:
            try:
                from qdrant_client.models import PointStruct

                await ensure_memory_collection()
                await _client().upsert(
                    collection_name=settings.qdrant_memory_collection,
                    points=[PointStruct(id=point_id, vector=vector, payload=payload)],
                    wait=True,
                )
            except AppError:
                raise
            except Exception as exc:
                raise AppError(ErrorCode.QDRANT_FAILED, status_code=502) from exc

        episode.embedding_version = embedding_version
        episode.updated_at = datetime.now(timezone.utc)
        session.commit()
        return True


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


async def search_memory_episodes(
    vector: list[float],
    *,
    tenant_id: str,
    subject_id: str,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    limit = top_k or settings.memory_v2_retrieval_top_k
    if _is_memory_mode():
        matches = [
            point
            for point in _memory_points.values()
            if point["payload"]["tenant_id"] == tenant_id
            and point["payload"]["subject_id"] == subject_id
            and point["payload"]["status"] == "active"
        ]
        ranked = sorted(
            matches,
            key=lambda point: _cosine(vector, point["vector"]),
            reverse=True,
        )[:limit]
        return [
            {**point["payload"], "score": _cosine(vector, point["vector"])}
            for point in ranked
        ]

    try:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        await ensure_memory_collection()
        result = await _client().query_points(
            collection_name=settings.qdrant_memory_collection,
            query=vector,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key="tenant_id", match=MatchValue(value=tenant_id)
                    ),
                    FieldCondition(
                        key="subject_id", match=MatchValue(value=subject_id)
                    ),
                    FieldCondition(key="status", match=MatchValue(value="active")),
                ]
            ),
            limit=limit,
            with_payload=True,
        )
        return [
            {**(point.payload or {}), "score": point.score}
            for point in result.points
        ]
    except Exception as exc:
        raise AppError(ErrorCode.QDRANT_FAILED, status_code=502) from exc


async def delete_memory_subject_points(*, tenant_id: str, subject_id: str) -> int:
    """Delete a subject projection; SQL rows remain authoritative."""
    settings = get_settings()
    if _is_memory_mode():
        point_ids = [
            point_id
            for point_id, point in _memory_points.items()
            if point["payload"]["tenant_id"] == tenant_id
            and point["payload"]["subject_id"] == subject_id
        ]
        for point_id in point_ids:
            _memory_points.pop(point_id, None)
        return len(point_ids)
    try:
        from qdrant_client.models import (
            FieldCondition,
            Filter,
            FilterSelector,
            MatchValue,
        )

        await ensure_memory_collection()
        scope_filter = Filter(
            must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="subject_id", match=MatchValue(value=subject_id)),
            ]
        )
        client = _client()
        count = await client.count(
            collection_name=settings.qdrant_memory_collection,
            count_filter=scope_filter,
            exact=True,
        )
        await client.delete(
            collection_name=settings.qdrant_memory_collection,
            points_selector=FilterSelector(filter=scope_filter),
            wait=True,
        )
        return int(count.count)
    except Exception as exc:
        raise AppError(ErrorCode.QDRANT_FAILED, status_code=502) from exc


async def rebuild_memory_subject_index(*, tenant_id: str, subject_id: str) -> int:
    """Recreate every active episode point from SQL for one subject."""
    await delete_memory_subject_points(tenant_id=tenant_id, subject_id=subject_id)
    with get_memory_session() as session:
        episode_ids = list(
            session.scalars(
                select(MemoryEpisodeModel.id).where(
                    MemoryEpisodeModel.tenant_id == tenant_id,
                    MemoryEpisodeModel.subject_id == subject_id,
                    MemoryEpisodeModel.status == "active",
                )
            )
        )
    indexed = 0
    for episode_id in episode_ids:
        indexed += int(
            await index_memory_episode(
                tenant_id=tenant_id,
                subject_id=subject_id,
                episode_id=episode_id,
            )
        )
    return indexed


def reset_memory_vector_cache() -> None:
    _memory_points.clear()
