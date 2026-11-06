import asyncio

from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.domains.sales.services import care_manual_service
from app.infrastructure.database.product_store import reset_product_store_for_tests
from app.domains.sales.services.care_manual_service import (
    list_care_manuals,
    reset_care_manual_store_for_tests,
    sync_care_manuals,
    test_match_care_manuals as match_care_manuals,
    update_care_manual,
)
from app.integrations.youzan.services.youzan_product_sync_service import (
    sync_youzan_products,
)


def _configure(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'care-manuals.db').as_posix()}"
    )
    monkeypatch.setenv("YOUZAN_ENABLED", "true")
    monkeypatch.setenv("YOUZAN_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("YOUZAN_CARE_MANUAL_PAGE_SIZE", "2")
    monkeypatch.setenv("YOUZAN_PRODUCT_DETAIL_ENABLED", "false")
    get_settings.cache_clear()
    reset_care_manual_store_for_tests()
    reset_product_store_for_tests()


def test_link_manual_to_knowledge_keeps_configuration_and_matches_shared_products(monkeypatch, tmp_path):
    from app.main import app
    from app.domains.catalog.services.product_knowledge_service import create_product_knowledge, link_product_knowledge
    from app.integrations.youzan.services.youzan_product_sync_service import _persist_sync

    _configure(monkeypatch, tmp_path)
    _persist_sync({
        "original": {"title": "墨兰玉妃", "_status": "on_sale"},
        "new": {"title": "皇后兰花", "_status": "on_sale"},
    }, {})
    knowledge = create_product_knowledge({"product_name": "墨兰玉妃", "item_id": "original", "aliases": "玉妃，皇后"})
    care_manual_service._persist_notes([
        {"note_id": "manual", "title": "【未命名品种】养护要点", "note_status": "published", "note_url": "https://example.com/manual"}
    ])
    manual = list_care_manuals()["items"][0]
    update_care_manual(manual["id"], {"enabled": True, "sort_order": 17, "card_description": "人工描述", "youzan_item_ids": ["original"]})
    import sqlite3
    with sqlite3.connect(tmp_path / "care-manuals.db") as connection:
        connection.execute("ALTER TABLE care_manual_cards DROP COLUMN knowledge_id")
    reset_care_manual_store_for_tests()
    client = TestClient(app)
    path = f"/api/v1/admin/care-manuals/{manual['id']}/knowledge"
    assert client.put(path, json={"knowledge_id": 9999}).status_code == 404
    response = client.put(path, json={"knowledge_id": knowledge["id"]})
    assert response.status_code == 200
    linked = response.json()["data"]
    assert linked["knowledge_id"] == knowledge["id"] and linked["orchid_name"] == "墨兰玉妃"
    assert linked["enabled"] and linked["sort_order"] == 17 and linked["card_description"] == "人工描述"
    assert linked["product_links"][0]["youzan_item_id"] == "original"
    link_product_knowledge("new", knowledge["id"])
    reset_care_manual_store_for_tests()
    assert client.get(f"/api/v1/admin/care-manuals/{manual['id']}").json()["data"]["knowledge_id"] == knowledge["id"]
    for item_id in ("new", "original"):
        match = match_care_manuals(youzan_item_id=item_id)
        assert match["auto_send_eligible"] and match["matches"][0]["card_id"] == manual["id"]
    assert match_care_manuals(query="皇后")["auto_send_eligible"]
    care_manual_service._persist_notes([
        {"note_id": "manual", "title": "【未命名品种】养护要点新版", "note_status": "published", "note_url": "https://example.com/manual-new"}
    ])
    assert list_care_manuals()["items"][0]["knowledge_id"] == knowledge["id"]
    assert list_care_manuals()["items"][0]["orchid_name"] == "墨兰玉妃"


def test_points_import_fuzzy_links_priority_and_fallback(monkeypatch, tmp_path):
    from app.domains.sales.services.care_manual_service import _persist_notes, auto_link_care_manual_products
    from app.integrations.youzan.services.youzan_product_sync_service import _persist_sync

    _configure(monkeypatch, tmp_path)
    _persist_sync({"101": {"title": "建兰【玉☆白丹红】3苗", "_status": "on_sale"}}, {})
    _persist_notes([
        {"note_id": 1, "title": "【建兰玉白丹红】养护注意事项", "note_status": "published", "note_url": "https://example.com/notice"},
        {"note_id": 2, "title": "【建兰玉·白丹红】单品养护要点", "note_status": "published", "note_url": "https://example.com/points"},
        {"note_id": 3, "title": "【春兰红宋梅】养护要点", "note_status": "published", "note_url": "https://example.com/other"},
    ])
    assert list_care_manuals()["total"] == 3
    assert auto_link_care_manual_products()["created_count"] == 2
    assert auto_link_care_manual_products()["created_count"] == 0
    result = match_care_manuals(youzan_item_id="101", product_name="建兰玉 白丹红")
    assert result["auto_send_eligible"] is True
    assert result["matches"][0]["title"].endswith("养护要点")
    points = result["matches"][0]["card_id"]
    update_care_manual(points, {"orchid_name": "建兰玉白丹红", "enabled": False})
    assert match_care_manuals(youzan_item_id="101")["matches"][0]["title"].endswith("养护注意事项")


class FakeProductClient:
    async def call(self, method, version, params):
        del version
        if method == "youzan.items.onsale.get":
            return {
                "count": 1,
                "items": [
                    {
                        "item_id": 1001,
                        "title": "建兰玉白丹红 3苗带盆",
                        "quantity": 8,
                    }
                ],
            }
        return {"count": 0, "items": []}


class FakeCareManualClient:
    def __init__(self):
        self.reduced = False

    async def call(self, method, version, params):
        assert method == "youzan.showcase.shopnote.list"
        assert version == "1.0.0"
        page = params["request"]["page"]
        assert params["request"]["page_size"] == 2
        if self.reduced:
            return {
                "count": 1,
                "page": 1,
                "page_size": 2,
                "data": [
                    {
                        "note_id": 11,
                        "note_alias": "yb-new",
                        "title": "【建兰玉白丹红】养护注意事项（新版）",
                        "note_status": "published",
                        "note_url": "https://h5.example.com/note/11-new",
                        "cover_photos": [{"url": "https://img.example.com/11-new.jpg"}],
                        "publish_time": 1_721_600_000,
                    }
                ],
            }
        pages = {
            1: [
                {
                    "note_id": 11,
                    "note_alias": "yb",
                    "title": "【建兰玉白丹红】养护注意事项",
                    "note_status": "published",
                    "note_url": "https://h5.example.com/note/11",
                    "cover_photos": [{"url": "https://img.example.com/11.jpg"}],
                    "publish_time": 1_721_600_000,
                },
                {
                    "note_id": 12,
                    "title": "【春兰宋梅】养护注意事项",
                    "note_status": "published",
                    "note_url": "https://h5.example.com/note/12",
                    "cover_photos": ["https://img.example.com/12.jpg"],
                    "publish_time": 1_721_600_100,
                },
            ],
            2: [
                {
                    "note_id": 13,
                    "title": "兰花上新介绍",
                    "note_status": "published",
                    "note_url": "https://h5.example.com/note/13",
                }
            ],
        }
        return {"count": 3, "page": page, "page_size": 2, "data": pages[page]}


class IgnoredPaginationClient:
    async def call(self, method, version, params):
        del method, version, params
        return {
            "count": 3,
            "page": 1,
            "page_size": 2,
            "data": [
                {
                    "note_id": 21,
                    "title": "【建兰国魂】养护注意事项",
                    "note_status": "published",
                    "note_url": "https://h5.example.com/note/21",
                },
                {
                    "note_id": 22,
                    "title": "【建兰大唐宫粉】养护注意事项",
                    "note_status": "published",
                    "note_url": "https://h5.example.com/note/22",
                },
            ],
        }


@pytest.mark.asyncio
async def test_scheduled_sync_retries_failure_and_continues_daily(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "youzan_enabled", True)
    monkeypatch.setattr(settings, "youzan_care_manual_sync_enabled", True)
    monkeypatch.setattr(settings, "youzan_care_manual_sync_startup_delay_seconds", 60)
    monkeypatch.setattr(settings, "youzan_care_manual_sync_interval_hours", 24)
    monkeypatch.setattr(care_manual_service, "youzan_credentials_available", lambda: True)
    stop_event = asyncio.Event()
    delays, triggers = [], []

    async def wait(awaitable, *, timeout):
        awaitable.close()
        delays.append(timeout)
        if len(delays) == 4:
            stop_event.set()
            return True
        raise asyncio.TimeoutError

    async def sync(*, trigger):
        triggers.append(trigger)
        if len(triggers) == 1:
            raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(asyncio, "wait_for", wait)
    monkeypatch.setattr(care_manual_service, "sync_care_manuals", sync)
    await care_manual_service.care_manual_sync_worker(stop_event)
    assert delays == [60, 300, 86400, 86400]
    assert triggers == ["scheduled"] * 3


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", ["sync", "youzan", "credentials", "stopped"])
async def test_scheduled_sync_respects_disabled_or_stopped(monkeypatch, disabled):
    settings = get_settings()
    monkeypatch.setattr(settings, "youzan_enabled", disabled != "youzan")
    monkeypatch.setattr(settings, "youzan_care_manual_sync_enabled", disabled != "sync")
    monkeypatch.setattr(care_manual_service, "youzan_credentials_available", lambda: disabled != "credentials")
    monkeypatch.setattr(care_manual_service, "sync_care_manuals", lambda **_: pytest.fail("unexpected sync"))
    stop_event = asyncio.Event()
    if disabled == "stopped":
        stop_event.set()
    await care_manual_service.care_manual_sync_worker(stop_event)


@pytest.mark.asyncio
async def test_scheduled_sync_stops_during_startup_wait(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "youzan_enabled", True)
    monkeypatch.setattr(settings, "youzan_care_manual_sync_enabled", True)
    monkeypatch.setattr(settings, "youzan_care_manual_sync_startup_delay_seconds", 60)
    monkeypatch.setattr(care_manual_service, "youzan_credentials_available", lambda: True)
    monkeypatch.setattr(care_manual_service, "sync_care_manuals", lambda **_: pytest.fail("unexpected sync"))
    stop_event = asyncio.Event()
    task = asyncio.create_task(care_manual_service.care_manual_sync_worker(stop_event))
    await asyncio.sleep(0)
    stop_event.set()
    await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_incomplete_sync_preserves_existing_manuals(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    await sync_care_manuals(client=FakeCareManualClient())
    previous = list_care_manuals()["items"]

    class IncompleteClient(FakeCareManualClient):
        async def call(self, method, version, params):
            result = await super().call(method, version, params)
            if params["request"]["page"] == 2:
                result["data"] = []
            return result

    with pytest.raises(RuntimeError, match="分页数据不完整"):
        await sync_care_manuals(client=IncompleteClient(), trigger="scheduled")
    result = list_care_manuals()
    assert result["items"] == previous
    assert result["last_sync"]["status"] == "failed"


@pytest.mark.asyncio
async def test_sync_edit_and_match_preserve_manual_configuration(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeProductClient())
    care_client = FakeCareManualClient()

    first = await sync_care_manuals(client=care_client)
    data = list_care_manuals()
    card = next(item for item in data["items"] if item["youzan_note_id"] == "11")
    update_care_manual(
        card["id"],
        {
            "orchid_name": "建兰玉白丹红",
            "aliases": ["玉白丹红", "小玉白"],
            "youzan_item_ids": ["1001"],
            "card_description": "玉白丹红养护手册",
            "sort_order": -10,
            "enabled": True,
            "match_keywords": ["丹红"],
        },
    )

    product_match = match_care_manuals(youzan_item_id="1001")
    alias_match = match_care_manuals(query="小玉白")
    care_client.reduced = True
    second = await sync_care_manuals(client=care_client)
    refreshed = list_care_manuals()
    edited = next(
        item for item in refreshed["items"] if item["youzan_note_id"] == "11"
    )
    missing = next(
        item for item in refreshed["items"] if item["youzan_note_id"] == "12"
    )

    assert first == {
        "scanned_count": 3,
        "qualified_count": 2,
        "created_count": 2,
        "updated_count": 0,
        "disabled_count": 0,
        "trigger": "manual",
        "status": "success",
    }
    assert product_match["matches"][0]["match_type"] == "exact_product"
    assert product_match["auto_send_eligible"] is True
    assert alias_match["matches"][0]["match_type"] == "exact_alias"
    assert second["disabled_count"] == 1
    assert edited["title"].endswith("（新版）")
    assert edited["note_url"].endswith("11-new")
    assert edited["aliases"] == ["玉白丹红", "小玉白"]
    assert edited["product_links"][0]["youzan_item_id"] == "1001"
    assert edited["sort_order"] == -10
    assert missing["youzan_status"] == "missing"
    assert refreshed["stats"] == {"active": 1, "disabled": 1, "unbound": 0, "product_unbound": 1}


@pytest.mark.asyncio
async def test_sync_rejects_ignored_pagination_without_partial_cards(
    monkeypatch, tmp_path
):
    _configure(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="分页参数未生效"):
        await sync_care_manuals(client=IgnoredPaginationClient())

    data = list_care_manuals()
    assert data["total"] == 0
    assert data["last_sync"]["status"] == "failed"
    assert "旧卡片数据未被覆盖" in data["last_sync"]["error_message"]


@pytest.mark.asyncio
async def test_admin_api_lists_edits_and_tests_match(monkeypatch, tmp_path):
    from app.main import app

    _configure(monkeypatch, tmp_path)
    await sync_care_manuals(client=FakeCareManualClient())
    client = TestClient(app)
    card = client.get("/api/v1/admin/care-manuals").json()["data"]["items"][0]

    updated = client.put(
        f"/api/v1/admin/care-manuals/{card['id']}",
        json={
            "orchid_name": "建兰玉白丹红",
            "aliases": ["玉白丹红"],
            "youzan_item_ids": [],
            "card_description": "固定卡片描述",
            "sort_order": 3,
            "enabled": True,
            "match_keywords": ["丹红"],
        },
    )
    matched = client.post(
        "/api/v1/admin/care-manuals/match/test",
        json={"query": "玉白丹红", "limit": 5},
    )

    assert updated.status_code == 200
    assert updated.json()["data"]["card_description"] == "固定卡片描述"
    assert matched.status_code == 200
    assert matched.json()["data"]["decision"] == "unique"
    assert matched.json()["data"]["matches"][0]["match_type"] == "exact_alias"


@pytest.mark.asyncio
async def test_batch_product_links_preview_save_and_preserve_configuration(monkeypatch, tmp_path):
    from app.main import app

    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeProductClient())
    await sync_care_manuals(client=FakeCareManualClient())
    client = TestClient(app)
    suggestions = client.get("/api/v1/admin/care-manuals/product-links/suggestions")
    assert suggestions.status_code == 200
    candidate = suggestions.json()["data"]["items"][0]
    assert candidate["matched_name"] == "玉白丹红"
    assert candidate["ambiguous"] is False
    assert list_care_manuals(match_status="product_unbound")["total"] == 2

    payload = {"links": [{"card_id": candidate["card_id"], "youzan_item_id": "1001"}]}
    invalid = client.post(
        "/api/v1/admin/care-manuals/product-links/batch",
        json={"links": [*payload["links"], {"card_id": candidate["card_id"], "youzan_item_id": "missing"}]},
    )
    assert invalid.status_code == 409
    assert list_care_manuals(match_status="product_bound")["total"] == 0
    saved = client.post("/api/v1/admin/care-manuals/product-links/batch", json=payload)
    repeated = client.post("/api/v1/admin/care-manuals/product-links/batch", json=payload)
    assert saved.status_code == repeated.status_code == 200
    assert saved.json()["data"] == {"created_count": 1, "manual_count": 1}
    assert repeated.json()["data"]["created_count"] == 0
    assert client.get("/api/v1/admin/care-manuals/product-links/suggestions").json()["data"]["items"] == []

    await sync_care_manuals(client=FakeCareManualClient())
    linked = list_care_manuals(match_status="product_bound")
    assert linked["total"] == 1
    assert linked["stats"]["product_unbound"] == 1
    assert linked["items"][0]["product_links"] == [{"youzan_item_id": "1001", "product_name": "建兰玉白丹红 3苗带盆"}]
    matched = match_care_manuals(youzan_item_id="1001")
    assert matched["auto_send_eligible"] is True
    assert matched["matches"][0]["card_id"] == candidate["card_id"]
    assert matched["matches"][0]["match_type"] == "exact_product"


@pytest.mark.asyncio
async def test_product_suggestions_flag_conflicts_and_skip_unavailable(monkeypatch, tmp_path):
    from app.infrastructure.database.models import CareManualCardModel, CareManualProductLinkModel, YouzanProductModel
    from app.domains.sales.services.care_manual_service import (
        _session, add_care_manual_product_links, suggest_care_manual_product_links,
    )

    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeProductClient())
    await sync_care_manuals(client=FakeCareManualClient())
    with _session() as session:
        cards = session.query(CareManualCardModel).order_by(CareManualCardModel.id).all()
        first_id, second_id = cards[0].id, cards[1].id
        cards[0].card_description = "保留已关联手册配置"
        cards[0].sort_order = 8
        cards[1].aliases_json = '["玉白丹红"]'
        cards[1].card_description = "保留人工配置"
        cards[1].sort_order = -10
        product = session.query(YouzanProductModel).first()
        product.title = "【玉白丹红】3苗带盆"
        product.status = "off_shelf"
        session.commit()

    suggestions = suggest_care_manual_product_links()["items"]
    assert len(suggestions) == 2
    assert all(item["ambiguous"] for item in suggestions)
    assert all(item["matched_name"] == "玉白丹红" for item in suggestions)
    add_care_manual_product_links([{"card_id": first_id, "youzan_item_id": "1001"}])
    remaining = suggest_care_manual_product_links()["items"]
    assert len(remaining) == 1 and remaining[0]["ambiguous"] is True

    with _session() as session:
        session.get(CareManualCardModel, second_id).enabled = False
        session.commit()
    assert suggest_care_manual_product_links()["items"] == []
    with pytest.raises(ValueError, match="停用"):
        add_care_manual_product_links([{"card_id": second_id, "youzan_item_id": "1001"}])
    with _session() as session:
        product = session.query(YouzanProductModel).first()
        product.status = "missing"
        session.commit()
    with pytest.raises(ValueError, match="商品已失效"):
        add_care_manual_product_links([{"card_id": first_id, "youzan_item_id": "1001"}])
    refreshed = list_care_manuals()["items"]
    first = next(item for item in refreshed if item["id"] == first_id)
    assert first["card_description"] == "保留已关联手册配置"
    assert first["sort_order"] == 8
    second = next(item for item in refreshed if item["id"] == second_id)
    assert second["card_description"] == "保留人工配置"
    assert second["sort_order"] == -10
    with _session() as session:
        first = session.get(CareManualCardModel, first_id)
        first.orchid_name = "建兰夏皇梅\\夏黄梅"
        product = session.query(YouzanProductModel).first()
        product.title = "建兰【夏黄梅】浓香好养"
        product.status = "on_sale"
        session.query(CareManualProductLinkModel).delete()
        session.commit()
    assert suggest_care_manual_product_links()["items"][0]["matched_name"] == "夏黄梅"


@pytest.mark.asyncio
async def test_product_suggestions_use_variety_name_instead_of_marketing_text(monkeypatch, tmp_path):
    from app.infrastructure.database.models import YouzanProductModel
    from app.domains.sales.services.care_manual_service import _session, suggest_care_manual_product_links

    _configure(monkeypatch, tmp_path)
    await sync_youzan_products(client=FakeProductClient())
    await sync_care_manuals(client=FakeCareManualClient())
    for title, expected in [
        ("春兰【红宋梅】浓香好养", []),
        ("建兰【宋梅】浓香好养", []),
        ("春兰【大富贵】与宋梅并称经典", []),
        ("【春兰】宋梅 经典梅瓣", ["春兰宋梅"]),
        ("【建兰玉白丹红】3苗带盆", ["建兰玉白丹红"]),
        ("【建兰】玉白丹红浓香好养", ["建兰玉白丹红"]),
    ]:
        with _session() as session:
            session.query(YouzanProductModel).first().title = title
            session.commit()
        suggestions = suggest_care_manual_product_links()["items"]
        assert [item["title"].split("】")[0].lstrip("【") for item in suggestions] == expected
