import hashlib
import math
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings
from app.schemas.common import AppError, ErrorCode


def _mock_embedding(text: str, size: int) -> list[float]:
    vector = [0.0] * size
    tokens = text.encode("utf-8") or b"\0"
    for index, value in enumerate(tokens):
        vector[index % size] += (value - 127.5) / 127.5
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


@lru_cache(maxsize=4)
def _bge_model(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def _as_float_list(vector: Any) -> list[float]:
    if hasattr(vector, "tolist"):
        vector = vector.tolist()
    return [float(value) for value in vector]


def _as_float_lists(vectors: Any) -> list[list[float]]:
    if hasattr(vectors, "tolist"):
        vectors = vectors.tolist()
    return [_as_float_list(vector) for vector in vectors]


def _validate_vector_size(vector: list[float], model_name: str, expected_size: int) -> None:
    if len(vector) != expected_size:
        raise AppError(
            ErrorCode.EMBEDDING_FAILED,
            (
                f"Embedding 维度不匹配: model={model_name} 输出 {len(vector)} 维, "
                f"QDRANT_VECTOR_SIZE={expected_size}"
            ),
            status_code=500,
        )


async def embed_text(text: str) -> list[float]:
    settings = get_settings()
    provider = settings.embedding_provider.lower()
    if provider == "mock":
        return _mock_embedding(text, settings.qdrant_vector_size)

    if provider == "bge":
        try:
            vector = _as_float_list(
                _bge_model(settings.embedding_model).encode(
                    text,
                    normalize_embeddings=True,
                )
            )
            _validate_vector_size(
                vector,
                settings.embedding_model,
                settings.qdrant_vector_size,
            )
            return vector
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ErrorCode.EMBEDDING_FAILED, status_code=502) from exc

    if provider == "openai":
        api_key = settings.embedding_api_key or settings.openai_api_key
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": settings.embedding_model, "input": text},
                )
                response.raise_for_status()
                vector = response.json()["data"][0]["embedding"]
                _validate_vector_size(
                    vector,
                    settings.embedding_model,
                    settings.qdrant_vector_size,
                )
                return vector
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ErrorCode.EMBEDDING_FAILED, status_code=502) from exc

    raise AppError(
        ErrorCode.EMBEDDING_FAILED,
        f"不支持的 Embedding Provider: {settings.embedding_provider}",
        status_code=500,
    )


async def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    settings = get_settings()
    if settings.embedding_provider.lower() == "bge":
        try:
            vectors = []
            batch_size = getattr(settings, "embedding_batch_size", 16)
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                vectors.extend(
                    _as_float_lists(
                        _bge_model(settings.embedding_model).encode(
                            batch,
                            normalize_embeddings=True,
                        )
                    )
                )
            for vector in vectors:
                _validate_vector_size(
                    vector,
                    settings.embedding_model,
                    settings.qdrant_vector_size,
                )
            return vectors
        except AppError:
            raise
        except Exception as exc:
            raise AppError(ErrorCode.EMBEDDING_FAILED, status_code=502) from exc

    return [await embed_text(text) for text in texts]
