from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select

from app.config import get_settings
from app.db.models import (
    MemoryEpisodeEventModel,
    MemoryEpisodeModel,
    MemoryEventModel,
    MemoryFactEvidenceModel,
    MemoryFactModel,
)
from app.schemas.memory import (
    MemoryContext,
    MemoryEpisodeContext,
    MemoryEvidenceItem,
    MemoryFactContext,
    MemoryQueryPlan,
)
from app.services.embedding_service import embed_text
from app.services.memory_query_planner import plan_memory_query
from app.services.memory_repository import get_memory_session
from app.services.memory_rerank_service import score_memory_episode
from app.services.memory_vector_service import search_memory_episodes


_SOURCE_RELIABILITY = {
    "verified_business_system": 1.0,
    "manual_customer_correction": 0.95,
    "customer_explicit": 0.85,
    "human_agent_annotation": 0.8,
    "customer_behavior": 0.65,
    "assistant_commitment": 0.65,
}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fact_context(
    fact: MemoryFactModel, evidence_event_ids: list[int]
) -> MemoryFactContext:
    return MemoryFactContext(
        fact_id=fact.id,
        fact_key=fact.fact_key,
        fact_value=json.loads(fact.fact_value_json),
        source_type=fact.source_type,
        confidence=fact.confidence,
        valid_from=_as_utc(fact.valid_from),
        evidence_event_ids=evidence_event_ids,
    )


def _event_context(event: MemoryEventModel) -> MemoryEvidenceItem:
    return MemoryEvidenceItem(
        event_id=event.id,
        event_uid=event.event_uid,
        event_type=event.event_type,
        actor_type=event.actor_type,
        source_type=event.source_type,
        content=json.loads(event.content_json),
        occurred_at=_as_utc(event.occurred_at),
    )


def _load_fact_contexts(
    session,
    *,
    tenant_id: str,
    subject_id: str,
    plan: MemoryQueryPlan,
    as_of: datetime,
    limit: int,
    allow_sensitive: bool,
) -> tuple[list[MemoryFactContext], list[MemoryFactContext], list[MemoryFactContext]]:
    temporal = and_(
        MemoryFactModel.valid_from <= as_of,
        or_(MemoryFactModel.valid_to.is_(None), MemoryFactModel.valid_to > as_of),
    )
    evidence_exists = (
        select(MemoryFactEvidenceModel.id)
        .join(MemoryEventModel, MemoryEventModel.id == MemoryFactEvidenceModel.event_id)
        .where(
            MemoryFactEvidenceModel.fact_id == MemoryFactModel.id,
            MemoryEventModel.tenant_id == tenant_id,
            MemoryEventModel.subject_id == subject_id,
            MemoryEventModel.deleted_at.is_(None),
        )
    )
    if not allow_sensitive:
        evidence_exists = evidence_exists.where(
            MemoryEventModel.sensitivity != "restricted"
        )
    facts = list(
        session.scalars(
            select(MemoryFactModel)
            .where(
                MemoryFactModel.tenant_id == tenant_id,
                MemoryFactModel.subject_id == subject_id,
                MemoryFactModel.fact_key.in_(plan.requested_fact_keys),
                MemoryFactModel.status.in_(("active", "disputed")),
                temporal,
                evidence_exists.exists(),
            )
            .order_by(
                MemoryFactModel.status.asc(),
                MemoryFactModel.confidence.desc(),
                MemoryFactModel.valid_from.desc(),
                MemoryFactModel.id.desc(),
            )
        )
    )
    if not facts:
        return [], [], []
    links_query = (
        select(MemoryFactEvidenceModel.fact_id, MemoryFactEvidenceModel.event_id)
        .join(MemoryEventModel, MemoryEventModel.id == MemoryFactEvidenceModel.event_id)
        .where(
            MemoryFactEvidenceModel.fact_id.in_([fact.id for fact in facts]),
            MemoryEventModel.tenant_id == tenant_id,
            MemoryEventModel.subject_id == subject_id,
            MemoryEventModel.deleted_at.is_(None),
        )
        .order_by(MemoryFactEvidenceModel.id.asc())
    )
    if not allow_sensitive:
        links_query = links_query.where(MemoryEventModel.sensitivity != "restricted")
    links = session.execute(links_query).all()
    evidence_by_fact: dict[int, list[int]] = {}
    for fact_id, event_id in links:
        evidence_by_fact.setdefault(fact_id, []).append(event_id)

    current: list[MemoryFactContext] = []
    verified: list[MemoryFactContext] = []
    conflicts: list[MemoryFactContext] = []
    for fact in facts:
        if len(current) + len(verified) + len(conflicts) >= limit:
            break
        context = _fact_context(fact, evidence_by_fact.get(fact.id, []))
        if fact.status == "disputed":
            conflicts.append(context)
        elif fact.source_type == "verified_business_system":
            verified.append(context)
        else:
            current.append(context)
    return current, verified, conflicts


def _load_episode_candidates(
    session,
    *,
    tenant_id: str,
    subject_id: str,
    vector_hits: list[dict[str, Any]],
) -> tuple[list[MemoryEpisodeModel], dict[int, float]]:
    hit_scores: dict[int, float] = {}
    for hit in vector_hits:
        try:
            episode_id = int(hit["episode_id"])
            hit_scores[episode_id] = float(hit.get("score", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
    if not hit_scores:
        return [], {}
    episodes = list(
        session.scalars(
            select(MemoryEpisodeModel).where(
                MemoryEpisodeModel.id.in_(hit_scores),
                MemoryEpisodeModel.tenant_id == tenant_id,
                MemoryEpisodeModel.subject_id == subject_id,
                MemoryEpisodeModel.status == "active",
            )
        )
    )
    return episodes, hit_scores


def _episode_evidence(
    session,
    *,
    tenant_id: str,
    subject_id: str,
    episode_ids: list[int],
) -> tuple[dict[int, list[int]], dict[int, float], set[int]]:
    if not episode_ids:
        return {}, {}, set()
    rows = session.execute(
        select(
            MemoryEpisodeEventModel.episode_id,
            MemoryEpisodeEventModel.event_id,
            MemoryEventModel.source_type,
            MemoryEventModel.sensitivity,
        )
        .join(MemoryEventModel, MemoryEventModel.id == MemoryEpisodeEventModel.event_id)
        .where(
            MemoryEpisodeEventModel.episode_id.in_(episode_ids),
            MemoryEventModel.tenant_id == tenant_id,
            MemoryEventModel.subject_id == subject_id,
            MemoryEventModel.deleted_at.is_(None),
        )
        .order_by(
            MemoryEpisodeEventModel.episode_id.asc(),
            MemoryEpisodeEventModel.position.asc(),
        )
    ).all()
    ids: dict[int, list[int]] = {}
    reliability: dict[int, float] = {}
    restricted_episode_ids: set[int] = set()
    for episode_id, event_id, source_type, sensitivity in rows:
        if sensitivity == "restricted":
            restricted_episode_ids.add(episode_id)
        ids.setdefault(episode_id, []).append(event_id)
        reliability[episode_id] = max(
            reliability.get(episode_id, 0.5),
            _SOURCE_RELIABILITY.get(source_type, 0.5),
        )
    return ids, reliability, restricted_episode_ids


def _load_evidence(
    session,
    *,
    tenant_id: str,
    subject_id: str,
    event_ids: list[int],
    allow_sensitive: bool,
) -> list[MemoryEvidenceItem]:
    if not event_ids:
        return []
    query = select(MemoryEventModel).where(
        MemoryEventModel.id.in_(event_ids),
        MemoryEventModel.tenant_id == tenant_id,
        MemoryEventModel.subject_id == subject_id,
        MemoryEventModel.deleted_at.is_(None),
    )
    if not allow_sensitive:
        query = query.where(MemoryEventModel.sensitivity != "restricted")
    rows = list(session.scalars(query))
    by_id = {row.id: row for row in rows}
    return [
        _event_context(by_id[event_id])
        for event_id in event_ids
        if event_id in by_id
    ]


async def retrieve_memory_context(
    *,
    tenant_id: str,
    subject_id: str,
    query: str,
    as_of: datetime | None = None,
    working_state: dict[str, Any] | None = None,
    plan: MemoryQueryPlan | None = None,
    allow_sensitive: bool = False,
) -> MemoryContext:
    """Retrieve scoped memory; vector hits are candidates and SQL is authoritative."""
    settings = get_settings()
    as_of = _as_utc(as_of or datetime.now(timezone.utc))
    plan = plan or plan_memory_query(query)
    with get_memory_session() as session:
        current, verified, conflicts = _load_fact_contexts(
            session,
            tenant_id=tenant_id,
            subject_id=subject_id,
            plan=plan,
            as_of=as_of,
            limit=settings.memory_v2_context_max_facts,
            allow_sensitive=allow_sensitive,
        )

        episode_contexts: list[MemoryEpisodeContext] = []
        if plan.include_episodes and settings.memory_v2_context_max_episodes:
            vector = await embed_text(query)
            vector_hits = await search_memory_episodes(
                vector,
                tenant_id=tenant_id,
                subject_id=subject_id,
                top_k=settings.memory_v2_retrieval_top_k,
            )
            episodes, hit_scores = _load_episode_candidates(
                session,
                tenant_id=tenant_id,
                subject_id=subject_id,
                vector_hits=vector_hits,
            )
            evidence_by_episode, reliability, restricted_episode_ids = (
                _episode_evidence(
                    session,
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    episode_ids=[episode.id for episode in episodes],
                )
            )
            episodes = [
                episode
                for episode in episodes
                if evidence_by_episode.get(episode.id)
                and (allow_sensitive or episode.id not in restricted_episode_ids)
            ]
            ranked = sorted(
                episodes,
                key=lambda episode: score_memory_episode(
                    episode=episode,
                    vector_score=hit_scores[episode.id],
                    plan=plan,
                    source_reliability=reliability.get(episode.id, 0.5),
                    as_of=as_of,
                ),
                reverse=True,
            )[: settings.memory_v2_context_max_episodes]
            for episode in ranked:
                score = score_memory_episode(
                    episode=episode,
                    vector_score=hit_scores[episode.id],
                    plan=plan,
                    source_reliability=reliability.get(episode.id, 0.5),
                    as_of=as_of,
                )
                episode_contexts.append(
                    MemoryEpisodeContext(
                        episode_id=episode.id,
                        episode_type=episode.episode_type,
                        title=episode.title,
                        summary=episode.summary,
                        outcome=episode.outcome,
                        importance=episode.importance,
                        started_at=_as_utc(episode.started_at),
                        ended_at=(
                            _as_utc(episode.ended_at) if episode.ended_at else None
                        ),
                        score=score,
                        evidence_event_ids=evidence_by_episode.get(episode.id, [])[
                            : settings.memory_v2_context_max_evidence_per_episode
                        ],
                    )
                )

        evidence_ids: list[int] = []
        for fact in current + verified + conflicts:
            evidence_ids.extend(fact.evidence_event_ids)
        for episode in episode_contexts:
            evidence_ids.extend(episode.evidence_event_ids)
        evidence_ids = list(dict.fromkeys(evidence_ids))
        evidence = _load_evidence(
            session,
            tenant_id=tenant_id,
            subject_id=subject_id,
            event_ids=evidence_ids,
            allow_sensitive=allow_sensitive,
        )

    unknowns: list[str] = []
    if plan.require_verified_business and not any(
        fact.fact_key == "purchase.status" for fact in verified
    ):
        unknowns.append("verified_purchase_status")
    return MemoryContext(
        subject_id=subject_id,
        as_of=as_of,
        current_facts=current,
        relevant_episodes=episode_contexts,
        working_state=working_state or {},
        verified_business_facts=verified,
        unresolved_conflicts=conflicts,
        unknowns=unknowns,
        evidence=evidence,
    )
