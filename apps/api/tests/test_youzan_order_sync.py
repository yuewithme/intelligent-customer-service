from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def _configure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'chat.db').as_posix()}"
    )
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite:///{(tmp_path / 'profile.db').as_posix()}"
    )
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("API_AUTH_ENABLED", "false")
    monkeypatch.setenv("YOUZAN_ENABLED", "true")
    monkeypatch.setenv("YOUZAN_ACCESS_TOKEN", "test-token")
    monkeypatch.setenv("YOUZAN_KDT_ID", "1001")
    monkeypatch.setenv("YOUZAN_ORDER_SYNC_INITIAL_LOOKBACK_DAYS", "1")
    get_settings.cache_clear()

    from app.domains.conversations.services import conversation_service
    from app.integrations.youzan.services import youzan_order_sync_service

    conversation_service._sessionmakers.clear()
    conversation_service._initialized_urls.clear()
    youzan_order_sync_service.reset_order_store_for_tests()


@pytest.mark.asyncio
async def test_order_sync_persists_incremental_orders_for_conversation_sidebar(
    monkeypatch, tmp_path
):
    from app.domains.conversations.services.conversation_service import (
        record_customer_message,
    )
    from app.domains.customers.services.user_profile_service import save_shipping_contact
    from app.integrations.youzan.services.youzan_order_sync_service import (
        sync_youzan_orders,
    )

    _configure(monkeypatch, tmp_path)
    calls = []

    class FakeClient:
        async def call(self, method, version, params):
            calls.append((method, version, params))
            return {
                "total": 1,
                "full_order_info_list": [
                    {
                        "full_order_info": {
                            "order_info": {
                                "tid": "E202607280001",
                                "status": "WAIT_SELLER_SEND_GOODS",
                                "created": "2026-07-28 09:30:00",
                                "update_time": "2026-07-28 10:00:00",
                                "payment": "168.00",
                            },
                            "buyer_info": {
                                "buyer_id": "buyer-1",
                                "mobile": "13800138000",
                            },
                            "orders": [
                                {
                                    "title": "建兰皇帝",
                                    "num": 2,
                                    "pic_path": "https://cdn.example.com/orchid.jpg",
                                }
                            ],
                        }
                    }
                ],
            }

    await record_customer_message(
        channel="wechat",
        user_id="wxid-customer",
        session_id="default",
        content="我要转人工",
    )
    await save_shipping_contact(
        "wxid-customer",
        {"mobile": "13800138000"},
        channel="wechat",
    )
    result = await sync_youzan_orders(
        client=FakeClient(),
        now=datetime(2026, 7, 28, 4, 0, tzinfo=timezone.utc),
    )

    assert result["order_count"] == 1
    assert calls[0][0:2] == ("youzan.trades.sold.get", "4.0.0")
    assert calls[0][2]["page_size"] == 100
    assert calls[0][2]["start_update"] == "2026-07-27 12:00:00"
    assert calls[0][2]["end_update"] == "2026-07-28 12:00:00"

    response = TestClient(app).get(
        "/api/v1/admin/conversations/wechat:wxid-customer:default/orders"
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "found"
    assert data["mobile_masked"] == "138****8000"
    assert data["items"][0]["order_no"] == "E202607280001"
    assert data["items"][0]["status_text"] == "待发货"
    assert data["items"][0]["payment_amount"] == "168.00"
    assert data["items"][0]["items"][0]["title"] == "建兰皇帝"
