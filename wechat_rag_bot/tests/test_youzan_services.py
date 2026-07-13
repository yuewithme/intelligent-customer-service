import httpx
import pytest


@pytest.mark.asyncio
async def test_youzan_client_posts_method_with_access_token():
    from app.integrations.youzan.client import YouzanClient

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"success": True, "code": 200, "data": {"items": []}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = YouzanClient(
            access_token="token-1",
            base_url="https://open.youzanyun.com",
            http_client=http_client,
        )
        result = await client.call(
            "youzan.items.onsale.get",
            "3.0.0",
            {"q": "蝴蝶兰", "page_size": 3},
        )

    assert result == {"items": []}
    assert requests[0].url == httpx.URL(
        "https://open.youzanyun.com/api/youzan.items.onsale.get/3.0.0?access_token=token-1"
    )
    assert requests[0].method == "POST"


@pytest.mark.asyncio
async def test_product_service_normalizes_item_and_builds_configured_page_path():
    from app.services.youzan_product_service import YouzanProductService

    class FakeClient:
        async def call(self, method, version, params):
            assert params["q"] == "白色蝴蝶兰"
            return {
                "items": [
                    {
                        "item_id": 123,
                        "title": "白色大花蝴蝶兰",
                        "alias": "abc123",
                        "price": 29900,
                        "pic_url": "https://cdn.example.com/goods.jpg",
                        "quantity": 8,
                    }
                ]
            }

    service = YouzanProductService(
        FakeClient(),
        page_path_template="pages/goods/detail?alias={alias}&kdt_id={kdt_id}",
        kdt_id="9001",
    )
    products = await service.search("白色蝴蝶兰", limit=3)

    assert products[0].model_dump() == {
        "item_id": "123",
        "title": "白色大花蝴蝶兰",
        "alias": "abc123",
        "price_cent": 29900,
        "stock": 8,
        "image_url": "https://cdn.example.com/goods.jpg",
        "page_path": "pages/goods/detail?alias=abc123&kdt_id=9001",
        "h5_url": None,
    }


@pytest.mark.asyncio
async def test_order_service_returns_recent_order_summary_by_mobile():
    from app.services.youzan_order_service import YouzanOrderService

    class FakeClient:
        async def call(self, method, version, params):
            if method == "youzan.scrm.customer.get":
                assert params == {"mobile": "13800138000"}
                return {"yz_uid": 6190904, "mobile": "13800138000"}
            assert method == "youzan.trades.sold.get"
            assert params["buyer_id"] == 6190904
            return {
                "full_order_info_list": [
                    {
                        "full_order_info": {
                            "order_info": {
                                "tid": "E202607130001",
                                "created": "2026-07-13 10:30:00",
                                "status": "WAIT_BUYER_CONFIRM_GOODS",
                            },
                            "orders": [{"title": "白色蝴蝶兰", "num": 1}],
                            "delivery_order": {
                                "express_name": "顺丰",
                                "express_no": "SF1234567890",
                            },
                        },
                    }
                ]
            }

    orders = await YouzanOrderService(FakeClient()).search_by_mobile(
        "13800138000", limit=3
    )

    assert orders[0].order_no == "E202607130001"
    assert orders[0].status_text == "已发货"
    assert orders[0].item_summary == "白色蝴蝶兰 × 1"
    assert orders[0].express_company == "顺丰"
    assert orders[0].tracking_no_masked == "SF12******90"
