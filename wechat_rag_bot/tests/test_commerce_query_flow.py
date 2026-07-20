import json

import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.reply_plan import BusinessFacts
from app.schemas.state import UserState
from app.services.youzan_order_service import YouzanOrderSummary
from app.services.youzan_order_service import YouzanCustomerIdentity
from app.services.youzan_product_service import YouzanProduct


def _message(text: str) -> NormalizedMessage:
    return NormalizedMessage(
        trace_id="commerce-trace",
        channel="wechat",
        user_id="wxid-customer",
        session_id="default",
        message=text,
        kb_id="kb_default",
        metadata={"provider": "eyun"},
    )


def _intent(primary_intent: str) -> IntentResult:
    return IntentResult(
        route="template_reply",
        primary_intent=primary_intent,
        confidence=0.95,
        need_template=True,
    )


def test_product_keyword_removes_conversation_scaffolding():
    from app.services.commerce_query_service import _product_keyword

    assert _product_keyword("有没有白色大花蝴蝶兰？发我一下链接", {}) == "白色大花蝴蝶兰"


def test_product_keyword_reuses_previous_customer_product_request():
    from app.services.commerce_query_service import _product_keyword

    assert _product_keyword(
        "发我链接",
        {},
        [{"role": "user", "content": "有没有白色大花蝴蝶兰？"}],
    ) == "白色大花蝴蝶兰"


@pytest.mark.asyncio
async def test_stage_allowlist_blocks_product_database_query():
    from app.services.commerce_query_service import build_commerce_context

    class ForbiddenProductService:
        async def search(self, keyword, *, limit):
            del keyword, limit
            raise AssertionError("product database must not be queried in this stage")

    facts = await build_commerce_context(
        _message("看看小国魂"),
        UserState(user_id="wxid-customer"),
        _intent("product_query"),
        product_service=ForbiddenProductService(),
        allowed_source_groups={"customer_context", "care_safe", "stage_script"},
    )

    assert facts == BusinessFacts()


@pytest.mark.asyncio
async def test_early_product_access_strips_value_price_stock_and_purchase_links():
    from app.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            del keyword, limit
            return [
                YouzanProduct(
                    item_id="1001",
                    title="小国魂",
                    alias="逸红双娇",
                    price_cent=2990,
                    stock=8,
                    image_url="https://example.com/product.jpg",
                    page_path="pages/goods/detail?id=1001",
                    h5_url="https://example.com/buy/1001",
                )
            ]

    facts = await build_commerce_context(
        _message("看看小国魂"),
        UserState(user_id="wxid-customer"),
        _intent("product_query"),
        product_service=FakeProductService(),
        allowed_source_groups={
            "customer_context",
            "care_safe",
            "stage_script",
            "product_catalog",
        },
    )

    product = facts.tool_state["products"][0]
    assert product["title"] == "小国魂"
    assert "price_cent" not in product
    assert "stock" not in product
    assert "page_path" not in product
    assert "h5_url" not in product
    assert "mini_program" not in facts.tool_state


@pytest.mark.asyncio
async def test_order_query_without_mobile_asks_for_mobile_and_marks_pending():
    from app.services.commerce_query_service import build_commerce_context

    state = UserState(user_id="wxid-customer")
    facts = await build_commerce_context(
        _message("帮我查一下订单"),
        state,
        _intent("order_query"),
        order_service=object(),
    )

    assert facts.tool_state == {
        "commerce_type": "order",
        "status": "missing_mobile",
    }
    assert state.metadata["commerce_pending"] == "order_mobile"


@pytest.mark.asyncio
async def test_order_query_uses_mobile_from_followup_and_returns_order_card():
    from app.services.commerce_query_service import build_commerce_context

    calls = []

    class FakeOrderService:
        async def search_by_mobile(self, mobile, *, limit):
            calls.append((mobile, limit))
            return [
                YouzanOrderSummary(
                    order_no="E001",
                    created_at="2026-07-13 10:30:00",
                    status="WAIT_BUYER_CONFIRM_GOODS",
                    status_text="已发货",
                    item_summary="白色蝴蝶兰 × 1",
                    express_company="顺丰",
                    tracking_no_masked="SF12******90",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={"commerce_pending": "order_mobile"},
    )
    facts = await build_commerce_context(
        _message("13800138000"),
        state,
        _intent("order_query"),
        order_service=FakeOrderService(),
        order_card={
            "display_name": "萧岚苑",
            "app_id": "wx123",
            "user_name": "gh_123@app",
            "icon_url": "https://cdn.example.com/icon.jpg",
            "thumb_url": "https://cdn.example.com/order.jpg",
            "page_path": "pages/order/list",
            "title": "查看我的订单",
        },
    )

    assert calls == [("13800138000", 3)]
    assert state.metadata["commerce_mobile"] == "13800138000"
    assert state.metadata.get("commerce_pending") is None
    assert facts.tool_state["orders"][0]["order_no"] == "E001"
    assert facts.tool_state["mini_program"]["page_path"] == "pages/order/list"


@pytest.mark.asyncio
async def test_order_query_reuses_mobile_from_recent_customer_chat():
    from app.services.commerce_query_service import build_commerce_context

    calls = []

    class FakeOrderService:
        async def search_by_mobile(self, mobile, *, limit):
            calls.append(mobile)
            return []

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "recent_turns": [
                {"role": "customer", "content": "我的下单手机号是13800138000"},
                {"role": "assistant", "content": "另一个手机号13900139000"},
            ]
        },
    )
    await build_commerce_context(
        _message("帮我查一下订单"),
        state,
        _intent("order_query"),
        order_service=FakeOrderService(),
    )

    assert calls == ["13800138000"]


@pytest.mark.asyncio
async def test_order_query_reuses_durable_youzan_identity_without_mobile():
    from app.services.commerce_query_service import build_commerce_context

    identity = YouzanCustomerIdentity(
        yz_uid="6190904",
        buyer_id="6190904",
        mobile_masked="138****8000",
    )

    class FakeIdentityStore:
        def get(self, **kwargs):
            assert kwargs["external_user_id"] == "wxid-customer"
            return identity

        def upsert(self, **kwargs):
            raise AssertionError("existing binding does not need to be rewritten")

    class FakeOrderService:
        async def search_by_identity(self, value, *, limit):
            assert value == identity
            assert limit == 3
            return [
                YouzanOrderSummary(
                    order_no="E001",
                    status="WAIT_SELLER_SEND_GOODS",
                    status_text="待发货",
                    item_summary="建兰皇帝 × 1",
                )
            ]

    facts = await build_commerce_context(
        _message("帮我查一下订单"),
        UserState(user_id="wxid-customer"),
        _intent("order_query"),
        order_service=FakeOrderService(),
        identity_store=FakeIdentityStore(),
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["mobile_masked"] == "138****8000"


@pytest.mark.asyncio
async def test_product_query_returns_first_product_as_mini_program_card():
    from app.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "白色大花蝴蝶兰"
            return [
                YouzanProduct(
                    item_id="123",
                    title="白色大花蝴蝶兰",
                    alias="abc",
                    price_cent=29900,
                    stock=8,
                    image_url="https://cdn.example.com/goods.jpg",
                    page_path="pages/goods/detail?alias=abc",
                )
            ]

    facts = await build_commerce_context(
        _message("白色大花蝴蝶兰"),
        UserState(user_id="wxid-customer"),
        _intent("product_query"),
        product_service=FakeProductService(),
        mini_program_base={
            "display_name": "萧岚苑",
            "app_id": "wx123",
            "user_name": "gh_123@app",
            "icon_url": "https://cdn.example.com/icon.jpg",
        },
    )

    card = facts.tool_state["mini_program"]
    assert card["title"] == "白色大花蝴蝶兰"
    assert card["thumb_url"] == "https://cdn.example.com/goods.jpg"
    assert card["page_path"] == "pages/goods/detail?alias=abc"


@pytest.mark.asyncio
async def test_product_link_request_without_product_asks_for_product():
    from app.services.commerce_query_service import build_commerce_context

    class UnexpectedProductService:
        async def search(self, keyword, *, limit):
            raise AssertionError("empty product query must not call Youzan")

    facts = await build_commerce_context(
        _message("发我链接"),
        UserState(user_id="wxid-customer"),
        _intent("product_query"),
        product_service=UnexpectedProductService(),
    )

    assert facts.tool_state == {
        "commerce_type": "product",
        "status": "missing_product",
    }


@pytest.mark.asyncio
async def test_upstream_failure_becomes_safe_unavailable_facts():
    from app.services.commerce_query_service import build_commerce_context

    class FailingProductService:
        async def search(self, keyword, *, limit):
            raise RuntimeError("provider token leaked in error")

    facts = await build_commerce_context(
        _message("有没有白色蝴蝶兰"),
        UserState(user_id="wxid-customer"),
        _intent("product_query"),
        product_service=FailingProductService(),
    )

    assert facts.tool_state == {
        "commerce_type": "product",
        "status": "unavailable",
    }


@pytest.mark.asyncio
async def test_commerce_renderer_returns_text_and_mini_program_message():
    from app.services.business_reply_renderer import render_business_reply

    facts = BusinessFacts(
        tool_state={
            "commerce_type": "product",
            "status": "found",
            "products": [
                {
                    "title": "白色大花蝴蝶兰",
                    "price_cent": 29900,
                    "stock": 8,
                    "knowledge": {
                        "highlighted_features": "花朵大、花期长，适合客厅养护",
                    },
                }
            ],
            "mini_program": {
                "display_name": "萧岚苑",
                "app_id": "wx123",
                "user_name": "gh_123@app",
                "icon_url": "https://cdn.example.com/icon.jpg",
                "thumb_url": "https://cdn.example.com/goods.jpg",
                "page_path": "pages/goods/detail?alias=abc",
                "title": "白色大花蝴蝶兰",
            },
        }
    )

    reply = await render_business_reply(_message("发我链接"), facts)

    assert "299" in reply.answer
    assert "花期长" in reply.answer
    assert reply.outbound_messages[0].type == "text"
    assert reply.outbound_messages[1].type == "mini_program"
    assert json.loads(reply.outbound_messages[1].content)["app_id"] == "wx123"


@pytest.mark.asyncio
async def test_product_renderer_uses_real_h5_link_without_mini_program_config():
    from app.services.business_reply_renderer import render_business_reply

    facts = BusinessFacts(
        tool_state={
            "commerce_type": "product",
            "status": "found",
            "products": [
                {
                    "title": "建兰皇帝",
                    "price_cent": 29900,
                    "h5_url": "https://h5.youzan.com/goods/abc",
                }
            ],
        }
    )

    reply = await render_business_reply(_message("发我链接"), facts)

    assert "https://h5.youzan.com/goods/abc" in reply.answer
    assert [item.type for item in reply.outbound_messages] == ["text"]


@pytest.mark.asyncio
async def test_phone_followup_keeps_pending_order_query_intent():
    from app.services.intent_service import classify_intent

    state = UserState(
        user_id="wxid-customer",
        metadata={"commerce_pending": "order_mobile"},
    )
    intent = await classify_intent(_message("13800138000"), state)

    assert intent.primary_intent == "order_query"
    assert intent.route == "template_reply"
