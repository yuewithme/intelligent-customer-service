import httpx
import pytest

from app.core.config import get_settings
from app.domains.knowledge.services import unified_library
from app.domains.catalog.services import product_knowledge_service
from app.shared.schemas.common import AppError


@pytest.mark.parametrize("status,message", [(404, "活动不存在"), (409, "请先关闭活动再编辑")])
def test_activity_business_errors_keep_status_and_message(monkeypatch, status, message):
    monkeypatch.setattr(get_settings(), "unified_library_enabled", True)

    def post(url, **kwargs):
        return httpx.Response(status, json={"message": message}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    from app.domains.sales.services.activity_service import update_activity
    from app.domains.sales.schemas.activity import ActivityUpdateRequest

    with pytest.raises(AppError) as error:
        update_activity(-1, ActivityUpdateRequest(title="活动", operator_id="operator"))
    assert error.value.status_code == status
    assert error.value.message == message


def test_product_reads_unified_source_without_local_fallback(monkeypatch):
    monkeypatch.setattr(get_settings(), "unified_library_enabled", True)
    monkeypatch.setattr(product_knowledge_service, "get_product_session", lambda: pytest.fail("old source used"))
    observed = []

    def post(url, **kwargs):
        observed.append((url, kwargs["json"]))
        return httpx.Response(200, json={"data": {"item_id": "item-7", "title": "来自统一库"}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    assert product_knowledge_service.get_catalog_product("item-7")["title"] == "来自统一库"
    assert observed[0][0].endswith("/operations/products.get_catalog_product")
    assert observed[0][1]["arguments"] == {"item_id": "item-7"}
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    with pytest.raises(AppError):
        product_knowledge_service.get_catalog_product("item-7")


def test_supplier_detail_failures_do_not_become_empty_sku_updates(monkeypatch):
    monkeypatch.setattr(get_settings(), "unified_library_enabled", True)
    captured = {}

    def call(operation, **args):
        captured.update(args)
        return {"product_count": 1}

    monkeypatch.setattr(unified_library, "call", call)
    from app.integrations.youzan.services.youzan_product_sync_service import _persist_sync
    _persist_sync({"item-7": {"_status": "on_sale"}}, {"item-7": RuntimeError("upstream unavailable")})
    assert captured["detail_results"]["item-7"] is None


@pytest.mark.asyncio
async def test_care_manual_sync_list_and_send_share_unified_source(monkeypatch):
    from app.domains.sales.services import care_manual_service as manuals
    from app.domains.conversations.services import conversation_service

    monkeypatch.setattr(get_settings(), "unified_library_enabled", True)
    monkeypatch.setattr(get_settings(), "youzan_enabled", True)
    monkeypatch.setattr(manuals, "youzan_credentials_available", lambda: True)
    monkeypatch.setattr(manuals, "_session", lambda: pytest.fail("old manual source used"))
    observed = []
    manual = {"id": 209, "title": "统一库手册", "note_url": "https://example.com/current-manual", "available": True}
    note = {"note_id": 209, "title": "统一库手册养护注意事项", "note_url": manual["note_url"], "note_status": "published"}

    class Client:
        async def call(self, *args):
            return {"count": 1, "data": [note]}

    def post(url, **kwargs):
        operation = url.rsplit("/", 1)[-1]
        arguments = kwargs["json"]["arguments"]
        observed.append((operation, arguments))
        results = {
            "manuals._start_sync_run": 1,
            "manuals._persist_notes": {"created_count": 1},
            "manuals._finish_sync_run": None,
            "manuals.list_care_manuals": {"items": [manual], "total": 209},
            "manuals.get_care_manual": manual,
        }
        return httpx.Response(200, json={"data": results[operation]}, request=httpx.Request("POST", url))

    sent = {}

    async def reply(*args, **kwargs):
        sent.update(kwargs)
        return {"queued": True}

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(conversation_service, "_reply_conversation_media", reply)
    await manuals.sync_care_manuals(trigger="scheduled", client=Client())
    assert manuals.list_care_manuals(page=2, page_size=200, enabled=True)["items"] == [manual]
    await conversation_service.reply_conversation_care_manual("conversation", "operator", 209)
    assert [operation for operation, _ in observed] == [
        "manuals._start_sync_run", "manuals._persist_notes", "manuals._finish_sync_run",
        "manuals.list_care_manuals", "manuals.get_care_manual",
    ]
    assert observed[0][1] == {"trigger": "scheduled", "source_type": "manual"}
    assert observed[1][1] == {"notes": [note]}
    assert observed[3][1]["page"] == 2
    assert observed[4][1] == {"card_id": 209}
    assert sent["message_metadata"]["link_card"]["url"] == manual["note_url"]

    monkeypatch.setattr(httpx, "post", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    with pytest.raises(AppError):
        manuals.list_care_manuals()
