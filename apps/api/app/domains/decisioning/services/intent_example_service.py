from __future__ import annotations

import asyncio
import json
from math import fsum
from pathlib import Path

from app.core.config import get_settings
from app.shared.schemas.common import AppError
from app.domains.knowledge.services.embedding_service import embed_text, embed_texts
from app.domains.decisioning.services.intent_taxonomy_service import DIMENSIONS, load_intent_taxonomy
from app.core.time import now_iso


_examples: list[dict] = [
    {
        "example_id": "ex_price_default",
        "text": "有点贵，我再考虑一下",
        "route": "template_reply",
        "primary_intent": "price_objection",
        "secondary_intents": ["hesitation"],
        "sales_stage": "closing",
        "created_at": now_iso(),
    }
]
_catalog_embedding_cache: dict[tuple[str, str, int, str], list[list[float]]] = {}
_catalog_cache_lock = asyncio.Lock()


async def add_intent_example(example: dict) -> dict:
    item = dict(example)
    item.setdefault("created_at", now_iso())
    _examples[:] = [
        old for old in _examples if old.get("example_id") != item.get("example_id")
    ]
    _examples.append(item)
    return item


async def retrieve_intent_examples(message: str, top_k: int = 5) -> list[dict]:
    """Retrieve semantic D/G/I label cards, retaining legacy examples for compatibility."""

    text = message.strip()
    if not text:
        return []
    catalog = load_intent_taxonomy()
    cards = catalog["labels"]
    try:
        vectors = await _catalog_vectors(cards, catalog["version"])
        query = await embed_text(text)
        scored = [(_cosine(query, vector), card) for card, vector in zip(cards, vectors)]
    except AppError:
        scored = [(_lexical_similarity(text, _card_search_text(card)), card) for card in cards]

    candidates: list[dict] = []
    for kind in DIMENSIONS:
        matches = sorted(
            (item for item in scored if item[1]["kind"] == kind),
            key=lambda item: item[0],
            reverse=True,
        )[:top_k]
        candidates.extend({**card, "score": round(score, 6)} for score, card in matches)

    legacy_matches = sorted(
        (
            (_lexical_similarity(text, str(example.get("text") or "")), example)
            for example in _examples
        ),
        key=lambda item: item[0],
        reverse=True,
    )[:top_k]
    candidates.extend(
        {**example, "kind": "example", "score": round(score, 6)}
        for score, example in legacy_matches
        if score > 0
    )
    return candidates


async def _catalog_vectors(cards: list[dict], version: str) -> list[list[float]]:
    settings = get_settings()
    key = (
        settings.embedding_provider.lower(),
        settings.embedding_model,
        settings.qdrant_vector_size,
        version,
    )
    cached = _catalog_embedding_cache.get(key)
    if cached is not None:
        return cached
    async with _catalog_cache_lock:
        cached = _catalog_embedding_cache.get(key)
        if cached is not None:
            return cached
        cached = _load_persisted_vectors(key, len(cards))
        if cached is None:
            cached = await embed_texts([_card_search_text(card) for card in cards])
            _persist_vectors(key, cached)
        _catalog_embedding_cache.clear()
        _catalog_embedding_cache[key] = cached
    return cached


async def prewarm_intent_example_index() -> None:
    settings = get_settings()
    if (
        not settings.intent_example_prewarm_enabled
        or settings.embedding_provider.lower() == "mock"
    ):
        return
    catalog = load_intent_taxonomy()
    await _catalog_vectors(catalog["labels"], catalog["version"])


def _load_persisted_vectors(
    key: tuple[str, str, int, str],
    expected_count: int,
) -> list[list[float]] | None:
    path = _cache_path()
    if path is None or not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("key") != list(key):
            return None
        vectors = payload.get("vectors")
        if not isinstance(vectors, list) or len(vectors) != expected_count:
            return None
        expected_size = key[2]
        if any(not isinstance(vector, list) or len(vector) != expected_size for vector in vectors):
            return None
        return [[float(value) for value in vector] for vector in vectors]
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _persist_vectors(
    key: tuple[str, str, int, str],
    vectors: list[list[float]],
) -> None:
    path = _cache_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"key": list(key), "vectors": vectors}, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError:
        return


def _cache_path() -> Path | None:
    value = str(get_settings().intent_embedding_cache_path or "").strip()
    return Path(value) if value else None


def _card_search_text(card: dict) -> str:
    positives = "；".join(card.get("positive_examples", [])[:12])
    return " ".join(
        value
        for value in (
            card.get("id", ""),
            card.get("name", ""),
            card.get("definition", ""),
            card.get("include", ""),
            positives,
        )
        if value
    )


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return fsum(a * b for a, b in zip(left, right))


def _lexical_similarity(left: str, right: str) -> float:
    left_grams = _character_ngrams(left)
    right_grams = _character_ngrams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _character_ngrams(value: str) -> set[str]:
    compact = "".join(value.lower().split())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}
