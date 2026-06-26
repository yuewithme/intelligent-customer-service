from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode


_memory_points: list[dict[str, Any]] = []


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


def _point_id(chunk_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, chunk_id))


async def ensure_collection() -> None:
    if _is_memory_mode():
        return
    try:
        from qdrant_client.models import Distance, VectorParams

        settings = get_settings()
        client = _client()
        if not await client.collection_exists(settings.qdrant_collection):
            await client.create_collection(
                collection_name=settings.qdrant_collection,
                vectors_config=VectorParams(
                    size=settings.qdrant_vector_size,
                    distance=getattr(Distance, settings.qdrant_distance.upper()),
                ),
            )
    except Exception as exc:
        raise AppError(ErrorCode.QDRANT_FAILED, status_code=502) from exc


async def upsert_chunks(points: list[dict[str, Any]]) -> None:
    if _is_memory_mode():
        _memory_points.extend(points)
        return
    try:
        from qdrant_client.models import PointStruct

        await ensure_collection()
        settings = get_settings()
        await _client().upsert(
            collection_name=settings.qdrant_collection,
            points=[
                PointStruct(
                    id=_point_id(point["id"]),
                    vector=point["vector"],
                    payload=point["payload"],
                )
                for point in points
            ],
            wait=True,
        )
    except AppError:
        raise
    except Exception as exc:
        raise AppError(ErrorCode.QDRANT_FAILED, status_code=502) from exc


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


async def search_chunks(
    vector: list[float],
    *,
    kb_id: str,
    tenant_id: str = "tenant_default",
    permission: str = "public",
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    settings = get_settings()
    limit = top_k or settings.rag_top_k
    if _is_memory_mode():
        matches = [
            point
            for point in _memory_points
            if point["payload"]["kb_id"] == kb_id
            and point["payload"]["tenant_id"] == tenant_id
            and point["payload"]["permission"] == permission
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

        result = await _client().query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            query_filter=Filter(
                must=[
                    FieldCondition(key="kb_id", match=MatchValue(value=kb_id)),
                    FieldCondition(
                        key="tenant_id", match=MatchValue(value=tenant_id)
                    ),
                    FieldCondition(
                        key="permission", match=MatchValue(value=permission)
                    ),
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
