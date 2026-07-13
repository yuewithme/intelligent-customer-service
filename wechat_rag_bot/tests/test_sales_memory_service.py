from types import SimpleNamespace

import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def sales_memory_db(monkeypatch, tmp_path):
    db_path = tmp_path / "sales_memory.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()

    from app.services import profile_identity_service, user_profile_service

    profile_identity_service._sessionmakers.clear()
    user_profile_service._sessionmakers.clear()
    try:
        from app.services import profile_refresh_service, sales_memory_service

        profile_refresh_service._sessionmakers.clear()
        sales_memory_service._sessionmakers.clear()
    except ImportError:
        pass
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_changed_fact_keeps_history_and_only_latest_value_is_current():
    from app.services.sales_memory_service import list_memory_facts, upsert_memory_fact
    from app.services.user_profile_service import ensure_user_profile

    await ensure_user_profile("user_1", tenant_id="tenant_default", channel="wechat")
    first = await upsert_memory_fact(
        profile_user_id="user_1",
        tenant_id="tenant_default",
        fact_key="customer.region",
        value="广西省",
        source_kind="deterministic_signal",
        source_trace_id="trace_1",
        confidence=0.95,
    )
    second = await upsert_memory_fact(
        profile_user_id="user_1",
        tenant_id="tenant_default",
        fact_key="customer.region",
        value="云南省",
        source_kind="customer_message",
        source_trace_id="trace_2",
        confidence=1.0,
    )

    current = await list_memory_facts("user_1", current_only=True)
    history = await list_memory_facts("user_1", current_only=False)

    assert [item["value"] for item in current] == ["云南省"]
    assert [item["value"] for item in history] == ["广西省", "云南省"]
    assert history[0]["valid_to"] is not None
    assert second["supersedes_fact_id"] == first["id"]


@pytest.mark.asyncio
async def test_deterministic_chat_signals_persist_facts_and_one_unresolved_episode():
    from app.services.sales_memory_service import (
        apply_deterministic_sales_memory,
        list_memory_facts,
        list_unresolved_sales_episodes,
    )
    from app.services.user_profile_service import ensure_user_profile

    await ensure_user_profile("user_1", tenant_id="tenant_default", channel="wechat")
    message = SimpleNamespace(
        user_id="user_1",
        tenant_id="tenant_default",
        message="收到的花盆破了，帮我处理一下",
        trace_id="trace_after_sale_1",
    )
    intent = SimpleNamespace(primary_intent="complaint", sales_stage="after_sale")
    reply = SimpleNamespace(
        metadata={"tag_result": {"labels": ["广西省", "100-200盆", "建兰"]}}
    )

    await apply_deterministic_sales_memory(message, intent, reply)
    await apply_deterministic_sales_memory(message, intent, reply)

    facts = await list_memory_facts("user_1", current_only=True)
    episodes = await list_unresolved_sales_episodes("user_1")

    assert {(item["fact_key"], item["value"]) for item in facts} == {
        ("customer.region", "广西省"),
        ("customer.plant_count", "100-200盆"),
        ("customer.preferred_variety", "建兰"),
    }
    assert len(episodes) == 1
    assert episodes[0]["episode_type"] == "complaint"
    assert "花盆破了" in episodes[0]["summary"]
    assert episodes[0]["resolved"] is False


@pytest.mark.asyncio
async def test_refresh_jobs_coalesce_and_survive_service_cache_reset(monkeypatch):
    from app.services import profile_refresh_service
    from app.services.user_profile_service import ensure_user_profile

    await ensure_user_profile("user_1", tenant_id="tenant_default", channel="wechat")
    await profile_refresh_service.schedule_profile_refresh("user_1", delay_seconds=60)
    await profile_refresh_service.schedule_profile_refresh("user_1", delay_seconds=30)

    rows = profile_refresh_service.list_profile_refresh_jobs()
    assert len(rows) == 1
    assert rows[0]["status"] == "pending"

    profile_refresh_service._sessionmakers.clear()
    rows_after_reset = profile_refresh_service.list_profile_refresh_jobs()
    assert len(rows_after_reset) == 1
    assert rows_after_reset[0]["profile_user_id"] == "user_1"


@pytest.mark.asyncio
async def test_due_refresh_job_runs_profile_enrichment_once(monkeypatch):
    from app.services import profile_refresh_service
    from app.services.user_profile_service import ensure_user_profile

    await ensure_user_profile("user_1", tenant_id="tenant_default", channel="wechat")
    calls = []

    async def fake_refresh(profile_user_id):
        calls.append(profile_user_id)

    monkeypatch.setattr(
        profile_refresh_service, "refresh_profile_from_memory", fake_refresh
    )
    await profile_refresh_service.schedule_profile_refresh("user_1", delay_seconds=0)

    processed = await profile_refresh_service.process_due_profile_refresh_jobs(limit=5)

    assert processed == 1
    assert calls == ["user_1"]
    assert profile_refresh_service.list_profile_refresh_jobs()[0]["status"] == "complete"


@pytest.mark.asyncio
async def test_message_arriving_during_refresh_keeps_job_pending(monkeypatch):
    from app.services import profile_refresh_service
    from app.services.user_profile_service import ensure_user_profile

    await ensure_user_profile("user_1", tenant_id="tenant_default", channel="wechat")
    calls = []

    async def refresh_and_receive_another_message(profile_user_id):
        calls.append(profile_user_id)
        await profile_refresh_service.schedule_profile_refresh(
            profile_user_id, delay_seconds=0
        )

    monkeypatch.setattr(
        profile_refresh_service,
        "refresh_profile_from_memory",
        refresh_and_receive_another_message,
    )
    await profile_refresh_service.schedule_profile_refresh("user_1", delay_seconds=0)

    await profile_refresh_service.process_due_profile_refresh_jobs(limit=5)

    assert calls == ["user_1"]
    assert profile_refresh_service.list_profile_refresh_jobs()[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_profile_bundle_exposes_only_current_facts_and_unresolved_episodes():
    from app.services.sales_memory_service import (
        record_sales_episode,
        upsert_memory_fact,
    )
    from app.services.user_profile_service import ensure_user_profile, get_profile_bundle

    await ensure_user_profile("user_1", tenant_id="tenant_default", channel="wechat")
    await upsert_memory_fact(
        profile_user_id="user_1",
        tenant_id="tenant_default",
        fact_key="customer.region",
        value="广西省",
        source_kind="customer_message",
        source_trace_id="trace_1",
        confidence=1.0,
    )
    await record_sales_episode(
        profile_user_id="user_1",
        tenant_id="tenant_default",
        episode_type="complaint",
        summary="客户反馈花盆破损，等待处理",
        source_trace_id="trace_2",
    )

    bundle = await get_profile_bundle("user_1")

    assert bundle["facts"][0]["value"] == "广西省"
    assert bundle["unresolved_episodes"][0]["episode_type"] == "complaint"
