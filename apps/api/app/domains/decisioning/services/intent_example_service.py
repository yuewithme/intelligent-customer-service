from __future__ import annotations

import asyncio
from collections import Counter
import json
import logging
from math import fsum, log, sqrt
from pathlib import Path
from time import monotonic

from app.core.config import get_settings
from app.shared.schemas.common import AppError
from app.domains.knowledge.services.embedding_service import embed_text, embed_texts
from app.domains.decisioning.services.intent_taxonomy_service import (
    DIMENSIONS,
    load_intent_taxonomy,
    normalize_taxonomy_value,
)
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
_labeled_example_cache: dict[str, tuple[float, list[dict]]] = {}
_labeled_example_cache_lock = asyncio.Lock()
logger = logging.getLogger("wechat_rag_bot.intent_examples")


async def add_intent_example(example: dict) -> dict:
    item = dict(example)
    item.setdefault("created_at", now_iso())
    _examples[:] = [
        old for old in _examples if old.get("example_id") != item.get("example_id")
    ]
    _examples.append(item)
    return item


async def retrieve_intent_examples(message: str, top_k: int = 5) -> list[dict]:
    """Retrieve D/G/I cards and trusted, similar historical labeling examples."""

    text = message.strip()
    if not text:
        return []
    catalog = load_intent_taxonomy()
    cards = catalog["labels"]
    labeled_matches = await _retrieve_labeled_examples(text)
    try:
        vectors = await _catalog_vectors(cards, catalog["version"])
        query = await embed_text(text)
        scored = [(_cosine(query, vector), card) for card, vector in zip(cards, vectors)]
    except AppError:
        scored = [(_lexical_similarity(text, _card_search_text(card)), card) for card in cards]

    label_evidence = _label_evidence(labeled_matches)
    candidates: list[dict] = []
    for kind in DIMENSIONS:
        matches = sorted(
            (item for item in scored if item[1]["kind"] == kind),
            key=lambda item: (
                item[0] + 0.35 * label_evidence.get((kind, item[1]["id"]), 0.0),
                item[0],
            ),
            reverse=True,
        )[:top_k]
        candidates.extend(
            {
                **card,
                "score": round(score, 6),
                "example_score": round(
                    label_evidence.get((kind, card["id"]), 0.0), 6
                ),
            }
            for score, card in matches
        )

    candidates.extend(labeled_matches)
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


async def _retrieve_labeled_examples(message: str) -> list[dict]:
    settings = get_settings()
    if (
        not settings.intent_labeled_example_enabled
        or settings.intent_labeled_example_top_k <= 0
    ):
        return []
    samples = await _trusted_training_samples()
    best_by_text: dict[str, tuple[float, dict]] = {}
    message_grams = _character_ngrams(message)
    minimum_shared_grams = 2 if len(message_grams) >= 4 else 1
    corpus_size, inverse_document_frequency = _example_corpus_statistics(samples)
    for sample in samples:
        text = str(sample.get("text") or "").strip()
        normalized_text = "".join(text.lower().split())
        if not normalized_text:
            continue
        text_grams = _character_ngrams(text)
        shared_grams = len(message_grams & text_grams)
        base_score = _weighted_ngram_similarity(
            message_grams,
            text_grams,
            inverse_document_frequency,
            corpus_size,
        )
        if shared_grams < minimum_shared_grams or base_score <= 0.03:
            continue
        score = base_score
        annotation = sample.get("annotation") or {}
        if annotation.get("origin") == "human":
            score += 0.02
        if annotation.get("status") == "corrected":
            score += 0.02
        item = (min(score, 1.0), sample)
        previous = best_by_text.get(normalized_text)
        if previous is None or item[0] > previous[0]:
            best_by_text[normalized_text] = item

    selected: list[dict] = []
    label_pair_counts: dict[tuple[str, str], int] = {}
    ranked = sorted(
        best_by_text.values(), key=lambda item: item[0], reverse=True
    )
    minimum_score = max(0.08, ranked[0][0] * 0.35) if ranked else 1.0
    for score, sample in ranked:
        if score < minimum_score:
            break
        labels = sample["labels"]
        pair = (labels["primary_domain"], labels["primary_goal"])
        if label_pair_counts.get(pair, 0) >= 2:
            continue
        label_pair_counts[pair] = label_pair_counts.get(pair, 0) + 1
        selected.append(_candidate_from_training_sample(sample, score))
        if len(selected) >= settings.intent_labeled_example_top_k:
            break
    return selected


async def _trusted_training_samples() -> list[dict]:
    settings = get_settings()
    key = f"{settings.chat_log_db_url}|{load_intent_taxonomy()['version']}"
    now = monotonic()
    cached = _labeled_example_cache.get(key)
    if cached is not None and cached[0] > now:
        return cached[1]
    async with _labeled_example_cache_lock:
        cached = _labeled_example_cache.get(key)
        if cached is not None and cached[0] > monotonic():
            return cached[1]
        try:
            from app.domains.decisioning.services.intent_observation_service import (
                build_training_dataset,
            )

            raw_samples = await build_training_dataset(redact_pii=True)
            samples = [
                normalized
                for sample in raw_samples
                if (normalized := _normalize_training_sample(sample)) is not None
            ]
        except Exception:
            logger.exception("failed to load trusted intent labeling examples")
            samples = []
        _labeled_example_cache.clear()
        _labeled_example_cache[key] = (
            monotonic() + settings.intent_labeled_example_cache_seconds,
            samples,
        )
        return samples


def _normalize_training_sample(sample: dict) -> dict | None:
    labels = sample.get("labels")
    if not isinstance(labels, dict):
        return None
    domain = normalize_taxonomy_value("domain", labels.get("primary_domain"))
    goal = normalize_taxonomy_value("goal", labels.get("primary_goal"))
    if not domain or not goal:
        return None
    issues = [
        normalized
        for value in labels.get("issues") or []
        if (normalized := normalize_taxonomy_value("issue", value))
    ]
    return {
        **sample,
        "labels": {
            **labels,
            "primary_domain": domain,
            "primary_goal": goal,
            "issues": list(dict.fromkeys(issues)),
        },
    }


def _candidate_from_training_sample(sample: dict, score: float) -> dict:
    labels = sample["labels"]
    context = []
    for turn in (sample.get("context") or [])[-2:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        content = " ".join(str(turn.get("content") or "").split())[:160]
        if role in {"user", "assistant"} and content:
            context.append({"role": role, "content": content})
    return {
        "kind": "example",
        "example_id": sample.get("sample_id"),
        "text": " ".join(str(sample.get("text") or "").split())[:300],
        "context": context,
        "primary_domain": labels["primary_domain"],
        "primary_goal": labels["primary_goal"],
        "issues": labels["issues"],
        "scope": labels.get("scope") or "in_scope",
        "annotation_status": (sample.get("annotation") or {}).get("status"),
        "score": round(score, 6),
    }


def _example_corpus_statistics(
    samples: list[dict],
) -> tuple[int, dict[str, float]]:
    document_frequency: Counter[str] = Counter()
    for sample in samples:
        document_frequency.update(
            _character_ngrams(str(sample.get("text") or ""))
        )
    corpus_size = max(len(samples), 1)
    return corpus_size, {
        gram: log((corpus_size + 1) / (count + 1)) + 0.2
        for gram, count in document_frequency.items()
    }


def _weighted_ngram_similarity(
    query: set[str],
    document: set[str],
    inverse_document_frequency: dict[str, float],
    corpus_size: int,
) -> float:
    if not query or not document:
        return 0.0
    unseen_weight = log(corpus_size + 1) + 0.2

    def weight(gram: str) -> float:
        return inverse_document_frequency.get(gram, unseen_weight)

    overlap = query & document
    if not overlap:
        return 0.0
    numerator = fsum(weight(gram) ** 2 for gram in overlap)
    query_norm = sqrt(fsum(weight(gram) ** 2 for gram in query))
    document_norm = sqrt(fsum(weight(gram) ** 2 for gram in document))
    return numerator / (query_norm * document_norm) if query_norm and document_norm else 0.0


def _label_evidence(examples: list[dict]) -> dict[tuple[str, str], float]:
    evidence: dict[tuple[str, str], float] = {}
    for example in examples:
        score = float(example.get("score") or 0.0)
        values = {
            "domain": [example.get("primary_domain")],
            "goal": [example.get("primary_goal")],
            "issue": example.get("issues") or [],
        }
        for kind, labels in values.items():
            for label in labels:
                if label:
                    key = (kind, str(label))
                    evidence[key] = max(evidence.get(key, 0.0), score)
    return evidence


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
    if settings.intent_labeled_example_enabled:
        await _trusted_training_samples()
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
    compact = "".join(character for character in value.lower() if character.isalnum())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index : index + 2] for index in range(len(compact) - 1)}
