import hashlib
import math

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


async def embed_text(text: str) -> list[float]:
    settings = get_settings()
    if settings.embedding_provider.lower() in {"mock", "bge"}:
        return _mock_embedding(text, settings.qdrant_vector_size)

    if settings.embedding_provider.lower() == "openai":
        api_key = settings.embedding_api_key or settings.openai_api_key
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{settings.embedding_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={"model": settings.embedding_model, "input": text},
                )
                response.raise_for_status()
                return response.json()["data"][0]["embedding"]
        except Exception as exc:
            raise AppError(ErrorCode.EMBEDDING_FAILED, status_code=502) from exc

    raise AppError(
        ErrorCode.EMBEDDING_FAILED,
        f"不支持的 Embedding Provider: {settings.embedding_provider}",
        status_code=500,
    )


async def embed_texts(texts: list[str]) -> list[list[float]]:
    return [await embed_text(text) for text in texts]

