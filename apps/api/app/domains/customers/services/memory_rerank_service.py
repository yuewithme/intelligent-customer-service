from __future__ import annotations

from datetime import datetime, timezone

from app.infrastructure.database.models import MemoryEpisodeModel
from app.domains.customers.schemas.memory import MemoryQueryPlan


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _semantic_score(value: float) -> float:
    return max(0.0, min(1.0, (value + 1.0) / 2.0))


def score_memory_episode(
    *,
    episode: MemoryEpisodeModel,
    vector_score: float,
    plan: MemoryQueryPlan,
    source_reliability: float,
    as_of: datetime,
) -> float:
    searchable = " ".join(
        value.lower()
        for value in (episode.title, episode.summary, episode.outcome or "")
    )
    terms = [term.lower() for term in plan.query_terms if term]
    lexical = (
        sum(term in searchable for term in terms) / len(terms) if terms else 0.0
    )
    type_match = (
        1.0 if plan.episode_types and episode.episode_type in plan.episode_types else 0.0
    )
    intent = max(lexical, type_match)
    age_days = max(
        0.0,
        (_as_utc(as_of) - _as_utc(episode.started_at)).total_seconds() / 86400,
    )
    recency = 1.0 / (1.0 + age_days / 180.0)
    score = (
        0.45 * _semantic_score(vector_score)
        + 0.20 * intent
        + 0.15 * max(0.0, min(1.0, source_reliability))
        + 0.10 * recency
        + 0.10 * max(0.0, min(1.0, episode.importance))
    )
    return round(score, 6)
