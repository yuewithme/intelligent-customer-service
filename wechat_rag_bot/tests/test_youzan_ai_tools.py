import pytest


class FakeIdentityStore:
    def __init__(self, binding=None):
        self.binding = binding
        self.upserts = []
        self.audits = []

    def get(self, **kwargs):
        return self.binding

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        self.binding = kwargs["identity"]
        return self.binding

    def record_tool_call(self, **kwargs):
        self.audits.append(kwargs)


class FakeProductService:
    async def search(self, keyword, *, limit):
        from app.services.youzan_product_service import YouzanProduct

        return [YouzanProduct(item_id="1", title=keyword, stock=8)][:limit]

    async def get(self, item_id):
        from app.services.youzan_product_service import YouzanProduct

        return YouzanProduct(item_id=item_id, title="建兰", stock=8)

    async def list_inventory(self, *, limit):
        return []


class FakeOrderService:
    def __init__(self):
        self.detail_calls = []

    async def lookup_by_mobile(self, mobile, *, limit):
        from app.services.youzan_order_service import (
            YouzanCustomerIdentity,
            YouzanOrderLookup,
            YouzanOrderSummary,
        )

        return YouzanOrderLookup(
            identity=YouzanCustomerIdentity(
                yz_uid="6190904",
                buyer_id="6190904",
                mobile=mobile,
                mobile_masked="138****8000",
            ),
            orders=[YouzanOrderSummary(order_no="E001")][:limit],
        )

    async def search_by_identity(self, identity, *, limit):
        from app.services.youzan_order_service import YouzanOrderSummary

        return [YouzanOrderSummary(order_no="E001")][:limit]

    async def get(self, order_no):
        from app.services.youzan_order_service import YouzanOrderSummary

        self.detail_calls.append(order_no)
        return YouzanOrderSummary(order_no=order_no, status="WAIT_SELLER_SEND_GOODS")


def _service(store=None, order_service=None):
    from app.services.youzan_ai_tool_service import YouzanAIToolService

    return YouzanAIToolService(
        product_service=FakeProductService(),
        order_service=order_service or FakeOrderService(),
        identity_store=store or FakeIdentityStore(),
        kdt_id="9001",
    )


@pytest.mark.asyncio
async def test_customer_resolution_persists_full_mobile_but_returns_and_audits_masked():
    store = FakeIdentityStore()
    result = await _service(store=store).resolve_customer(
        customer_id="wxid-customer",
        mobile="13800138000",
    )

    assert result["ok"] is True
    assert result["data"]["mobile_masked"] == "138****8000"
    assert "13800138000" not in str(result)
    assert store.upserts[0]["identity"].mobile == "13800138000"
    assert store.audits[0]["parameters"]["mobile_masked"] == "138****8000"
    assert "13800138000" not in str(store.audits)


@pytest.mark.asyncio
async def test_order_detail_requires_order_to_belong_to_bound_customer():
    from app.services.youzan_order_service import YouzanCustomerIdentity

    order_service = FakeOrderService()
    store = FakeIdentityStore(
        binding=YouzanCustomerIdentity(
            buyer_id="6190904",
            mobile="13800138000",
            mobile_masked="138****8000",
        )
    )
    service = _service(store=store, order_service=order_service)

    denied = await service.get_customer_order(
        customer_id="wxid-customer",
        order_no="E999",
    )
    allowed = await service.get_customer_order(
        customer_id="wxid-customer",
        order_no="E001",
    )

    assert denied["data"] == {"status": "not_found", "order": None}
    assert order_service.detail_calls == ["E001"]
    assert allowed["data"]["order"]["order_no"] == "E001"


@pytest.mark.asyncio
async def test_product_tool_returns_trace_and_read_only_marker():
    result = await _service().search_products(keyword="建兰", limit=3)

    assert result["ok"] is True
    assert result["read_only"] is True
    assert result["tool"] == "youzan_search_products"
    assert result["trace_id"]
    assert result["data"]["products"][0]["stock"] == 8


def test_identity_schema_migration_adds_full_mobile_column(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from app.services.youzan_identity_store import _ensure_identity_schema

    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE youzan_identity_bindings (id INTEGER PRIMARY KEY)")
        )

    _ensure_identity_schema(engine)

    assert "mobile" in {
        column["name"]
        for column in inspect(engine).get_columns("youzan_identity_bindings")
    }
