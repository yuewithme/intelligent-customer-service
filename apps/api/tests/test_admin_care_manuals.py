from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.domains.sales.services.care_manual_service import (
    list_care_manuals,
    reset_care_manual_store_for_tests,
    sync_care_manuals,
    test_match_care_manuals as match_care_manuals,
    update_care_manual,
)
from app.integrations.youzan.services.youzan_product_sync_service import (
    reset_product_store_for_tests,
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
    assert refreshed["stats"] == {"active": 1, "disabled": 1, "unbound": 0}


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
