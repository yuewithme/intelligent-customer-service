from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.schemas.memory import MemoryEventCreate, MemoryOperationCandidate
from app.services import memory_repository
from app.services.embedding_service import embed_text
from app.services.memory_consolidation_service import apply_memory_candidate
from app.services.memory_event_service import append_memory_event
from app.services.memory_identity_service import resolve_or_create_subject
from app.services.memory_retrieval_service import retrieve_memory_context
from app.services.memory_vector_service import (
    index_memory_episode,
    reset_memory_vector_cache,
    search_memory_episodes,
)


@pytest.fixture
def memory_db(monkeypatch, tmp_path):
    db_path = tmp_path / "memory-v2-retrieval.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("QDRANT_URL", "")
    monkeypatch.setenv("QDRANT_VECTOR_SIZE", "32")
    monkeypatch.setenv("MEMORY_V2_CONTEXT_MAX_EPISODES", "2")
    monkeypatch.setenv("MEMORY_V2_CONTEXT_MAX_EVIDENCE_PER_EPISODE", "2")
    get_settings.cache_clear()
    memory_repository.reset_memory_repository_cache()
    reset_memory_vector_cache()
    try:
        yield
    finally:
        reset_memory_vector_cache()
        memory_repository.reset_memory_repository_cache()
        get_settings.cache_clear()


def _subject(tenant_id="tenant_alpha", external_user_id="user_1"):
    return resolve_or_create_subject(
        tenant_id=tenant_id,
        channel="wechat",
        owner_external_id="owner_1",
        external_user_id=external_user_id,
        identity_source="eyun_contact",
        verified=True,
    )


def _event(
    subject,
    *,
    uid,
    text,
    occurred_at=None,
    event_type="customer_message",
    actor_type="customer",
    source_type="conversation_message",
    sensitivity="internal",
    content=None,
):
    return append_memory_event(
        MemoryEventCreate(
            event_uid=uid,
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            session_id="session_1",
            event_type=event_type,
            actor_type=actor_type,
            content=content or {"text": text},
            source_type=source_type,
            source_id=uid,
            occurred_at=occurred_at or datetime.now(timezone.utc),
            sensitivity=sensitivity,
        )
    ).event


def _episode(
    subject,
    events,
    *,
    title,
    summary,
    episode_type="product_consultation",
    importance=0.5,
    started_at=None,
):
    result = apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=MemoryOperationCandidate(
            operation="ADD",
            memory_kind="episode",
            evidence_event_ids=[event.id for event in events],
            source_type="customer_explicit",
            confidence=0.9,
            reason="retrieval_test_episode",
            episode_type=episode_type,
            title=title,
            summary=summary,
            importance=importance,
            started_at=started_at or events[0].occurred_at,
            ended_at=events[-1].occurred_at,
        ),
    )
    return result.record_id


def _fact(subject, event, *, key, value, source_type="customer_explicit"):
    return apply_memory_candidate(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        candidate=MemoryOperationCandidate(
            operation="ADD",
            memory_kind="semantic_fact",
            fact_key=key,
            fact_value=value,
            evidence_event_ids=[event.id],
            source_type=source_type,
            valid_from=event.occurred_at,
            confidence=0.99,
            reason="retrieval_test_fact",
        ),
    )


@pytest.mark.asyncio
async def test_memory_vector_search_is_tenant_and_subject_scoped(memory_db):
    alpha = _subject(external_user_id="alpha")
    beta = _subject(external_user_id="beta")
    other_tenant = _subject(tenant_id="tenant_beta", external_user_id="alpha")
    episodes = []
    for index, subject in enumerate((alpha, beta, other_tenant), start=1):
        event = _event(subject, uid=f"scope:{index}", text="偏好原木家具")
        episode_id = _episode(
            subject,
            [event],
            title="原木家具咨询",
            summary="客户咨询原木材质家具",
        )
        episodes.append(episode_id)
        await index_memory_episode(
            tenant_id=subject.tenant_id,
            subject_id=subject.id,
            episode_id=episode_id,
        )
    await index_memory_episode(
        tenant_id=alpha.tenant_id,
        subject_id=alpha.id,
        episode_id=episodes[0],
    )

    hits = await search_memory_episodes(
        await embed_text("原木家具"),
        tenant_id=alpha.tenant_id,
        subject_id=alpha.id,
        top_k=10,
    )

    assert [hit["episode_id"] for hit in hits] == [episodes[0]]
    assert all(hit["tenant_id"] == alpha.tenant_id for hit in hits)
    assert all(hit["subject_id"] == alpha.id for hit in hits)


@pytest.mark.asyncio
async def test_retrieval_revalidates_vector_hits_against_sql_scope(
    memory_db, monkeypatch
):
    from app.services import memory_retrieval_service

    alpha = _subject(external_user_id="alpha")
    beta = _subject(external_user_id="beta")
    alpha_event = _event(alpha, uid="sql-scope:alpha", text="咨询沙发")
    beta_event = _event(beta, uid="sql-scope:beta", text="另一个客户的隐私")
    alpha_episode = _episode(
        alpha, [alpha_event], title="沙发咨询", summary="客户咨询沙发"
    )
    beta_episode = _episode(
        beta, [beta_event], title="不应返回", summary="其他客户记录"
    )

    async def fake_search(*args, **kwargs):
        return [
            {"episode_id": beta_episode, "score": 1.0},
            {"episode_id": alpha_episode, "score": 0.5},
        ]

    monkeypatch.setattr(memory_retrieval_service, "search_memory_episodes", fake_search)
    context = await retrieve_memory_context(
        tenant_id=alpha.tenant_id,
        subject_id=alpha.id,
        query="之前咨询过什么沙发",
    )

    assert [item.episode_id for item in context.relevant_episodes] == [alpha_episode]
    assert {item.event_id for item in context.evidence} == {alpha_event.id}


@pytest.mark.asyncio
async def test_context_uses_current_temporal_fact_and_requires_verified_payment(
    memory_db,
):
    subject = _subject()
    first = _event(
        subject,
        uid="budget:old",
        text="预算三千",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    second = _event(
        subject,
        uid="budget:new",
        text="预算改为五千",
        occurred_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    _fact(
        subject,
        first,
        key="purchase.budget",
        value={"amount": 3000, "currency": "CNY", "scope": "current_purchase"},
    )
    _fact(
        subject,
        second,
        key="purchase.budget",
        value={"amount": 5000, "currency": "CNY", "scope": "current_purchase"},
    )

    budget = await retrieve_memory_context(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        query="我的预算是多少",
        as_of=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )
    payment = await retrieve_memory_context(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        query="这个订单付款了吗",
    )

    assert [fact.fact_value["amount"] for fact in budget.current_facts] == [5000]
    assert payment.verified_business_facts == []
    assert payment.unknowns == ["verified_purchase_status"]

    verified_event = _event(
        subject,
        uid="payment:verified",
        text="",
        event_type="tool_observation",
        actor_type="business_system",
        source_type="verified_business_system",
        content={"order_id": "order_1", "status": "paid"},
    )
    _fact(
        subject,
        verified_event,
        key="purchase.status",
        value={"order_id": "order_1", "status": "paid"},
        source_type="verified_business_system",
    )
    payment = await retrieve_memory_context(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        query="这个订单付款了吗",
    )
    assert payment.verified_business_facts[0].fact_value["status"] == "paid"
    assert payment.unknowns == []


@pytest.mark.asyncio
async def test_rerank_prefers_relevant_older_episode(memory_db, monkeypatch):
    from app.services import memory_retrieval_service

    subject = _subject()
    now = datetime.now(timezone.utc)
    old_event = _event(
        subject,
        uid="rerank:old",
        text="退款一直没有到账",
        occurred_at=now - timedelta(days=400),
    )
    recent_event = _event(
        subject,
        uid="rerank:recent",
        text="看看新品",
        occurred_at=now - timedelta(days=1),
    )
    old_episode = _episode(
        subject,
        [old_event],
        title="退款售后问题",
        summary="客户反馈退款未到账",
        episode_type="after_sales",
        importance=1.0,
        started_at=old_event.occurred_at,
    )
    recent_episode = _episode(
        subject,
        [recent_event],
        title="新品咨询",
        summary="客户浏览新品",
        importance=0.1,
        started_at=recent_event.occurred_at,
    )

    async def fake_search(*args, **kwargs):
        return [
            {"episode_id": recent_episode, "score": 0.99},
            {"episode_id": old_episode, "score": 0.4},
        ]

    monkeypatch.setattr(memory_retrieval_service, "search_memory_episodes", fake_search)
    context = await retrieve_memory_context(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        query="之前的退款问题是什么",
        as_of=now,
    )
    assert context.relevant_episodes[0].episode_id == old_episode


@pytest.mark.asyncio
async def test_evidence_expansion_is_bounded_and_excludes_restricted_content(
    memory_db, monkeypatch
):
    from app.services import memory_retrieval_service

    subject = _subject()
    events = [
        _event(
            subject,
            uid=f"evidence:{index}",
            text=f"evidence {index}",
        )
        for index in range(4)
    ]
    episode_id = _episode(
        subject,
        events,
        title="证据边界",
        summary="用于测试证据数量与敏感信息",
    )
    restricted_event = _event(
        subject,
        uid="evidence:restricted",
        text="restricted evidence",
        sensitivity="restricted",
    )
    restricted_episode_id = _episode(
        subject,
        [restricted_event],
        title="受限证据",
        summary="此摘要不应进入默认上下文",
    )

    async def fake_search(*args, **kwargs):
        return [
            {"episode_id": restricted_episode_id, "score": 1.0},
            {"episode_id": episode_id, "score": 0.9},
        ]

    monkeypatch.setattr(memory_retrieval_service, "search_memory_episodes", fake_search)
    context = await retrieve_memory_context(
        tenant_id=subject.tenant_id,
        subject_id=subject.id,
        query="证据边界",
    )

    assert context.relevant_episodes[0].evidence_event_ids == [
        events[0].id,
        events[1].id,
    ]
    assert [item.event_id for item in context.evidence] == [
        events[0].id,
        events[1].id,
    ]
    assert restricted_episode_id not in {
        item.episode_id for item in context.relevant_episodes
    }
    assert all(
        item.content.get("text") != "restricted evidence" for item in context.evidence
    )
