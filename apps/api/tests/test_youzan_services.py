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
@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (
            {
                "gw_err_resp": {
                    "err_code": 4007,
                    "err_msg": "source IP denied",
                    "trace_id": "trace-gateway",
                }
            },
            "4007",
        ),
        (
            {
                "error_response": {
                    "code": 50000,
                    "msg": "weixin follower not found",
                }
            },
            "50000",
        ),
    ],
)
async def test_youzan_client_rejects_gateway_and_legacy_errors(payload, expected_code):
    from app.integrations.youzan.client import YouzanClient, YouzanError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = YouzanClient(access_token="secret-token", http_client=http_client)
        with pytest.raises(YouzanError) as caught:
            await client.call("youzan.test", "1.0.0", {})

    assert caught.value.code == expected_code
    assert caught.value.method == "youzan.test"
    assert "secret-token" not in str(caught.value)


@pytest.mark.asyncio
async def test_youzan_client_rejects_business_failure_even_when_data_exists():
    from app.integrations.youzan.client import YouzanClient, YouzanError

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": False,
                "code": 106000004,
                "message": "订单号非法",
                "data": {"full_order_info": {}},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = YouzanClient(access_token="token", http_client=http_client)
        with pytest.raises(YouzanError) as caught:
            await client.call("youzan.trade.get", "4.0.0", {})

    assert caught.value.code == "106000004"


@pytest.mark.asyncio
async def test_product_service_normalizes_item_and_builds_configured_page_path():
    from app.integrations.youzan.services.youzan_product_service import YouzanProductService

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
async def test_product_service_enriches_first_result_with_item_detail():
    from app.integrations.youzan.services.youzan_product_service import YouzanProductService

    class FakeClient:
        async def call(self, method, version, params):
            if method == "youzan.items.onsale.get":
                return {"items": [{"item_id": 123, "title": "建兰", "quantity": 1}]}
            assert method == "youzan.item.get"
            assert params == {"item_id": "123"}
            return {
                "item": {
                    "item_id": 123,
                    "title": "建兰皇帝",
                    "quantity": 8,
                    "price": 29900,
                }
            }

    products = await YouzanProductService(
        FakeClient(), detail_enabled=True
    ).search("建兰", limit=3)

    assert products[0].title == "建兰皇帝"
    assert products[0].stock == 8


@pytest.mark.asyncio
async def test_order_service_returns_recent_order_summary_by_mobile():
    from app.integrations.youzan.services.youzan_order_service import YouzanOrderService

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


@pytest.mark.asyncio
async def test_order_service_resolves_official_openid_and_enriches_order_detail():
    from app.integrations.youzan.services.youzan_order_service import YouzanOrderService

    class FakeClient:
        async def call(self, method, version, params):
            if method == "youzan.users.weixin.follower.get":
                assert params == {"weixin_openid": "wx-open-1"}
                return {"user": {"fans_id": 12, "union_id": "union-1"}}
            if method == "youzan.scrm.customer.get":
                assert params == {"fans_id": "12", "fans_type": 1}
                return {"customer": {"yz_uid": 34, "yz_open_id": "yz-open-1"}}
            if method == "youzan.trades.sold.get":
                assert params["buyer_id"] == 34
                return {
                    "full_order_info_list": [
                        {
                            "full_order_info": {
                                "order_info": {"tid": "E001", "status": "WAIT_SELLER_SEND_GOODS"},
                                "orders": [{"title": "建兰皇帝", "num": 1}],
                            }
                        }
                    ]
                }
            assert method == "youzan.trade.get"
            assert params == {"tid": "E001"}
            return {
                "full_order_info": {
                    "order_info": {"tid": "E001", "status": "WAIT_BUYER_CONFIRM_GOODS"},
                    "orders": [{"title": "建兰皇帝", "num": 1}],
                },
                "delivery_order": [
                    {"express_name": "顺丰", "express_no": "SF1234567890"}
                ],
            }

    lookup = await YouzanOrderService(
        FakeClient(), detail_enabled=True
    ).lookup_by_weixin_openid("wx-open-1", limit=3)

    assert lookup.identity.yz_open_id == "yz-open-1"
    assert lookup.identity.weixin_openid == "wx-open-1"
    assert lookup.orders[0].status_text == "已发货"
    assert lookup.orders[0].express_company == "顺丰"
