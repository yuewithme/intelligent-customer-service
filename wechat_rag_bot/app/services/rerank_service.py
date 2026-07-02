from typing import Any


async def rerank(
    question: str,
    docs: list[dict[str, Any]],
    top_n: int,
) -> list[dict[str, Any]]:
    del question
    return docs[:top_n]

