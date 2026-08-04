import json

import pytest

from app.domains.conversations.schemas.event import NormalizedMessage
from app.domains.decisioning.schemas.intent import IntentResult
from app.domains.decisioning.schemas.reply_plan import BusinessFacts
from app.domains.customers.schemas.state import UserState
from app.integrations.youzan.services.youzan_order_service import YouzanOrderSummary
from app.integrations.youzan.services.youzan_order_service import YouzanCustomerIdentity
from app.integrations.youzan.services.youzan_product_service import YouzanProduct


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
    from app.domains.catalog.services.commerce_query_service import _product_keyword

    assert _product_keyword("有没有白色大花蝴蝶兰？发我一下链接", {}) == "白色大花蝴蝶兰"
    assert _product_keyword("我想买忆香荷，怎么下单？", {}) == "忆香荷"


def test_product_keyword_reuses_previous_customer_product_request():
    from app.domains.catalog.services.commerce_query_service import _product_keyword

    assert _product_keyword(
        "发我链接",
        {},
        [{"role": "user", "content": "有没有白色大花蝴蝶兰？"}],
    ) == "白色大花蝴蝶兰"


def test_product_image_keyword_removes_image_request_words():
    from app.domains.catalog.services.commerce_query_service import _product_keyword

    assert _product_keyword("能给我发一下芽黄素的图片吗", {}) == "芽黄素"
    assert _product_keyword("发张芽黄素实拍图", {}) == "芽黄素"
    assert _product_keyword("我想看芽黄素的图册", {}) == "芽黄素"


def test_product_image_followup_reuses_previous_customer_product():
    from app.domains.catalog.services.commerce_query_service import _product_keyword

    assert _product_keyword(
        "刚才那款有图片吗",
        {},
        [{"role": "user", "content": "我想看看芽黄素"}],
    ) == "芽黄素"
    assert _product_keyword(
        "给我看一下这个花的图册",
        {},
        [{"role": "user", "content": "我对芽黄素感兴趣"}],
    ) == "芽黄素"


def test_purchase_followup_reuses_product_named_in_previous_ai_reply():
    from app.domains.catalog.services.commerce_query_service import _product_keyword

    assert _product_keyword(
        "那我想买这个，链接在哪里？",
        {},
        [
            {"role": "user", "content": "我喜欢建兰，你给我发图片我看看"},
            {
                "role": "assistant",
                "content": "可以的，这是建兰红君荷的商品图片，您可以看看花色和株型。",
            },
        ],
    ) == "建兰红君荷"


@pytest.mark.asyncio
async def test_purchase_followup_builds_card_for_previously_shown_product():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "建兰红君荷"
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="4792037787",
                    title="建兰红君荷",
                    alias="2x9s4jwps5ulisu",
                    price_cent=6800,
                    stock=10,
                    image_url="https://cdn.example.com/hongjunhe.jpg",
                    page_path="packages/goods/detail/index?alias=2x9s4jwps5ulisu",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "recent_turns": [
                {"role": "user", "content": "我喜欢建兰，你给我发图片我看看"},
                {
                    "role": "assistant",
                    "content": "可以的，这是建兰红君荷的商品图片，您可以看看花色和株型。",
                },
            ]
        },
    )
    facts = await build_commerce_context(
        _message("那我想买这个，链接在哪里？"),
        state,
        _intent("product_query"),
        product_service=FakeProductService(),
        mini_program_base={
            "display_name": "萧岚苑",
            "app_id": "wx123",
            "user_name": "gh_123@app",
            "icon_url": "https://cdn.example.com/icon.jpg",
        },
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["mini_program"]["title"] == "建兰红君荷"
    assert (
        facts.tool_state["mini_program"]["page_path"]
        == "packages/goods/detail/index?alias=2x9s4jwps5ulisu"
    )


@pytest.mark.asyncio
async def test_product_selection_semantics_build_structured_product_facts():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert "推荐一款好养" in keyword
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="4792037787",
                    title="建兰红君荷 新手好养",
                    alias="2x9s4jwps5ulisu",
                    price_cent=6800,
                    stock=10,
                    image_url="https://cdn.example.com/hongjunhe.jpg",
                    page_path="packages/goods/detail/index?alias=2x9s4jwps5ulisu",
                    h5_url="https://h5.youzan.com/goods/hongjunhe",
                )
            ]

    intent = IntentResult(
        route="template_reply",
        primary_intent="product_query",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection"],
        slots={"conversation_topic": "product_recommendation"},
        confidence=0.99,
        need_template=True,
    )

    facts = await build_commerce_context(
        _message("那你推荐一款好养的，最好有视频教学。"),
        UserState(user_id="wxid-customer"),
        intent,
        product_service=FakeProductService(),
        allowed_source_groups={"product_catalog"},
    )
    reply = await render_business_reply(
        _message("那你推荐一款好养的，最好有视频教学。"),
        facts,
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["products"][0]["title"] == "建兰红君荷 新手好养"
    assert facts.tool_state["products"][0]["h5_url"].endswith("hongjunhe")
    assert reply is not None
    assert "视频教学权益" in reply.answer
    assert "仍需按购买记录核实" in reply.answer


@pytest.mark.asyncio
async def test_membership_request_uses_local_product_and_exact_price_card():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "首单参与陪伴养兰客户"
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="membership-99",
                    title="首单参与陪伴养兰客户 专享特惠链接",
                    price_cent=9900,
                    h5_url="https://h5.youzan.com/goods/member-99",
                ),
                YouzanProduct(
                    item_id="membership-39",
                    title="首单参与陪伴养兰客户 专享特惠链接",
                    price_cent=3990,
                    h5_url="https://h5.youzan.com/goods/member-39",
                ),
            ]

    intent = IntentResult(
        route="template_reply",
        primary_intent="product_query",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection"],
        slots={
            "conversation_topic": "product_recommendation",
            "product_keywords": ["首单参与陪伴养兰客户"],
            "product_request_kind": "membership",
        },
        confidence=0.99,
        need_template=True,
    )
    state = UserState(user_id="wxid-customer")

    facts = await build_commerce_context(
        _message("39.9元可以加入会员吗？"),
        state,
        intent,
        product_service=FakeProductService(),
        allowed_source_groups={"product_catalog"},
    )
    reply = await render_business_reply(_message("39.9元可以加入会员吗？"), facts)

    assert facts.tool_state["products"][0]["item_id"] == "membership-39"
    assert facts.tool_state["brand"] == "萧岚苑"
    assert facts.tool_state["service_capabilities"] == [
        "系统的视频课程",
        "结合具体养护问题的一对一指导",
    ]
    assert state.metadata["commerce_last_product_id"] == "membership-39"
    assert reply is not None
    assert "萧岚苑有陪伴养兰会员" in reply.answer
    assert "39.9元" in reply.answer
    assert "购买链接" in reply.answer
    assert json.loads(reply.outbound_messages[1].content)["url"].endswith("member-39")


@pytest.mark.asyncio
async def test_budget_followup_reuses_catalog_requirements_and_sends_multiple_cards():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import CATALOG_SEARCH
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert "叶子细长" in keyword
            assert "100元以内" in keyword
            assert limit == 3
            return [
                YouzanProduct(
                    item_id=f"orchid-{index}",
                    title=f"建兰推荐款{index}",
                    price_cent=4000 + index * 500,
                    page_path=f"pages/goods/{index}",
                )
                for index in range(1, 4)
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "commerce_last_catalog_query": "推荐几款叶子细长、花香浓、新手适合的兰花"
        },
    )
    facts = await build_commerce_context(
        _message("100元以内，最好五六十左右"),
        state,
        _intent("knowledge_question"),
        product_service=FakeProductService(),
        mini_program_base={"app_id": "wx123", "display_name": "萧岚苑"},
        business_action=CATALOG_SEARCH,
        allowed_source_groups={"product_catalog", "product_value", "sku_facts"},
    )
    reply = await render_business_reply(
        _message("100元以内，最好五六十左右"),
        facts,
    )

    assert facts.tool_state["send_all_product_cards"] is True
    assert [item["price_cent"] for item in facts.tool_state["products"]] == [
        4500,
        5000,
        5500,
    ]
    assert reply is not None
    assert [message.type for message in reply.outbound_messages].count("mini_program") == 3


@pytest.mark.asyncio
async def test_selected_product_detail_queries_current_item_id_and_keeps_selection():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import (
        SELECTED_PRODUCT_DETAIL,
    )
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    class ProductDetail:
        def model_dump(self):
            return {
                "item_id": "longyansu-1",
                "title": "建兰龙岩素",
                "price_cent": 4680,
                "page_path": "pages/goods/longyansu",
                "skus": [
                    {
                        "sku_id": "sku-3",
                        "spec_name": "3苗裸根",
                        "price_cent": 4680,
                        "stock": 8,
                    }
                ],
            }

    class FakeProductService:
        async def get(self, item_id):
            assert item_id == "longyansu-1"
            return ProductDetail()

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "commerce_last_product_id": "longyansu-1",
            "commerce_last_product_keyword": "建兰龙岩素",
        },
    )
    facts = await build_commerce_context(
        _message("刚才那款龙岩素有几苗，能不能带盆发货？"),
        state,
        _intent("product_query"),
        product_service=FakeProductService(),
        mini_program_base={"app_id": "wx123", "display_name": "萧岚苑"},
        business_action=SELECTED_PRODUCT_DETAIL,
        allowed_source_groups={"product_catalog", "sku_facts"},
    )
    reply = await render_business_reply(
        _message("刚才那款龙岩素有几苗，能不能带盆发货？"),
        facts,
    )

    assert state.metadata["commerce_last_product_id"] == "longyansu-1"
    assert facts.tool_state["products"][0]["skus"][0]["spec_name"] == "3苗裸根"
    assert reply is not None
    assert "3苗裸根" in reply.answer
    assert "没写清楚是不是带盆" in reply.answer


@pytest.mark.asyncio
async def test_payment_followup_reuses_last_selected_membership_product():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "首单参与陪伴养兰客户 专享特惠链接"
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="membership-39",
                    title=keyword,
                    price_cent=3990,
                    h5_url="https://h5.youzan.com/goods/member-39",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "commerce_last_product_keyword": "首单参与陪伴养兰客户 专享特惠链接",
            "commerce_last_product_id": "membership-39",
            "commerce_last_product_kind": "membership",
        },
    )
    facts = await build_commerce_context(
        _message("现在付款吗？"),
        state,
        _intent("payment_intent"),
        product_service=FakeProductService(),
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["products"][0]["item_id"] == "membership-39"
    assert facts.tool_state["product_request_kind"] == "membership"


@pytest.mark.asyncio
async def test_supply_shortage_products_are_blocked_before_ai_context():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert limit == 1
            if keyword == "兰花专用紫砂盆":
                return [
                    YouzanProduct(
                        item_id="pot-1",
                        title="兰花专用紫砂盆",
                        price_cent=6000,
                        h5_url="https://h5.youzan.com/goods/pot",
                    )
                ]
            assert keyword == "兰花专用植料"
            return [
                YouzanProduct(
                    item_id="medium-1",
                    title="兰花专用植料混合装",
                    price_cent=1990,
                    h5_url="https://h5.youzan.com/goods/medium",
                )
            ]

    intent = IntentResult(
        route="template_reply",
        primary_intent="product_query",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection", "medium_repotting"],
        slots={
            "conversation_topic": "product_recommendation",
            "product_keywords": ["兰花专用紫砂盆", "兰花专用植料"],
            "product_request_kind": "supply_shortage",
        },
        confidence=0.99,
        need_template=True,
    )
    state = UserState(
        user_id="wxid-customer",
        metadata={
            "commerce_last_product_id": "orchid-main",
            "commerce_last_product_keyword": "建兰龙岩素",
        },
    )
    facts = await build_commerce_context(
        _message("家里盆和植料不够。"),
        state,
        intent,
        product_service=FakeProductService(),
        allowed_source_groups={"product_catalog"},
    )
    assert facts.available is False
    assert facts.tool_state == {}
    assert state.metadata["commerce_last_product_id"] == "orchid-main"


@pytest.mark.asyncio
async def test_any_order_intent_builds_card_for_previously_shown_product():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "建兰红君荷"
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="4792037787",
                    title="建兰红君荷",
                    alias="2x9s4jwps5ulisu",
                    price_cent=6800,
                    stock=10,
                    image_url="https://cdn.example.com/hongjunhe.jpg",
                    page_path="packages/goods/detail/index?alias=2x9s4jwps5ulisu",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "可以的，这是建兰红君荷的商品图片，您可以看看花色和株型。",
                }
            ]
        },
    )
    facts = await build_commerce_context(
        _message("就按这个下单吧"),
        state,
        _intent("order_intent"),
        product_service=FakeProductService(),
        mini_program_base={
            "display_name": "萧岚苑",
            "app_id": "wx123",
            "user_name": "gh_123@app",
            "icon_url": "https://cdn.example.com/icon.jpg",
        },
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["mini_program"]["title"] == "建兰红君荷"


@pytest.mark.asyncio
async def test_live_product_link_phrase_builds_card_from_recent_product():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "建兰红君荷"
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="4792037787",
                    title="建兰红君荷",
                    alias="2x9s4jwps5ulisu",
                    price_cent=6800,
                    stock=10,
                    image_url="https://cdn.example.com/hongjunhe.jpg",
                    page_path="packages/goods/detail/index?alias=2x9s4jwps5ulisu",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "recent_turns": [
                {
                    "role": "assistant",
                    "content": "可以的，这是建兰红君荷的商品图片，您可以看看花色和株型。",
                }
            ]
        },
    )
    facts = await build_commerce_context(
        _message("给我发产品链接，我要买"),
        state,
        _intent("product_query"),
        product_service=FakeProductService(),
        mini_program_base={
            "display_name": "萧岚苑",
            "app_id": "wx123",
            "user_name": "gh_123@app",
            "icon_url": "https://cdn.example.com/icon.jpg",
        },
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["mini_program"]["title"] == "建兰红君荷"


@pytest.mark.asyncio
async def test_order_intent_without_product_context_keeps_normal_order_flow():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class UnexpectedProductService:
        async def search(self, keyword, *, limit):
            raise AssertionError("order flow without a product must not query the catalog")

    facts = await build_commerce_context(
        _message("我想下单"),
        UserState(user_id="wxid-customer"),
        _intent("order_intent"),
        product_service=UnexpectedProductService(),
    )

    assert facts == BusinessFacts()


@pytest.mark.asyncio
async def test_stage_allowlist_blocks_product_database_query():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
async def test_early_product_access_strips_value_price_stock_but_keeps_purchase_navigation():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
    assert product["page_path"] == "pages/goods/detail?id=1001"
    assert product["h5_url"] == "https://example.com/buy/1001"
    assert facts.tool_state["mini_program"]["page_path"] == "pages/goods/detail?id=1001"


@pytest.mark.asyncio
async def test_order_query_without_mobile_asks_for_mobile_and_marks_pending():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
    assert state.metadata["active_task"] == {
        "domain": "order",
        "task_type": "order_query",
        "status": "awaiting_identity",
    }


@pytest.mark.asyncio
async def test_shipping_change_without_identity_collects_mobile_before_claiming_action():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_reply_renderer import render_business_reply

    state = UserState(user_id="wxid-customer")
    intent = _intent("order_query").model_copy(
        update={"slots": {"order_action": "shipping_date_change"}}
    )
    facts = await build_commerce_context(
        _message("已下单，请晚一天发货"),
        state,
        intent,
        order_service=object(),
    )
    reply = await render_business_reply(_message("已下单，请晚一天发货"), facts)

    assert facts.tool_state["requested_action"] == "shipping_date_change"
    assert facts.tool_state["requested_action_executed"] is False
    assert "下单手机号" in reply.answer
    assert "查到订单后" in reply.answer
    assert "已经改" not in reply.answer
    assert reply.metadata["allow_persona_extension"] is True
    assert reply.metadata["commerce_action"] == {
        "commerce_type": "order",
        "status": "missing_mobile",
        "requested_action": "shipping_date_change",
        "requested_action_executed": False,
        "card_sent": False,
    }


@pytest.mark.asyncio
async def test_order_query_uses_mobile_from_followup_and_returns_order_card():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
    assert state.metadata["active_task"]["status"] == "completed"
    assert state.metadata["active_task"]["last_result_status"] == "found"
    assert facts.tool_state["orders"][0]["order_no"] == "E001"
    assert facts.tool_state["mini_program"]["page_path"] == "pages/order/list"


@pytest.mark.asyncio
async def test_evaluation_order_fixture_returns_matching_mock_order_without_real_service():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import ORDER_VERIFY
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    message = _message("下单手机号是13000000000。").model_copy(
        update={
            "metadata": {
                "evaluation_id": "case08-order-fixture",
                "tool_state": {
                    "fixture_type": "order",
                    "mobile": "13000000000",
                    "orders": [
                        {
                            "order_no": "EVAL-CASE08-001",
                            "created_at": "2026-07-30 10:20:00",
                            "status": "WAIT_SELLER_SEND_GOODS",
                            "status_text": "待发货",
                            "item_summary": "春兰【松针素】× 1",
                        }
                    ],
                },
            }
        }
    )
    state = UserState(
        user_id="eval-case08",
        metadata={"commerce_pending": "order_mobile"},
    )

    facts = await build_commerce_context(
        message,
        state,
        _intent("order_query"),
        business_action=ORDER_VERIFY,
        allowed_source_groups={"order_facts"},
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["fixture_used"] is True
    assert facts.tool_state["orders"][0]["order_no"] == "EVAL-CASE08-001"
    assert state.metadata["commerce_mobile"] == "13000000000"
    assert state.metadata.get("commerce_pending") is None
    reply = await render_business_reply(message, facts)
    assert "春兰【松针素】× 1，待发货" in reply.answer
    assert reply.metadata["commerce_action"]["fixture_used"] is True


@pytest.mark.asyncio
async def test_unexecutable_order_change_routes_to_human_without_customer_copy():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import ORDER_VERIFY
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    message = _message("下单手机号是13000000000，麻烦晚一天发货。").model_copy(
        update={
            "metadata": {
                "evaluation_id": "order-change-handoff",
                "tool_state": {
                    "fixture_type": "order",
                    "mobile": "13000000000",
                    "orders": [
                        {
                            "order_no": "EVAL-HANDOFF-001",
                            "created_at": "2026-07-30 10:20:00",
                            "status": "WAIT_SELLER_SEND_GOODS",
                            "status_text": "待发货",
                            "item_summary": "陪伴养兰会员 × 1",
                        }
                    ],
                },
            }
        }
    )
    state = UserState(user_id="eval-order-change")
    intent = _intent("order_query").model_copy(
        update={"slots": {"order_action": "shipping_date_change"}}
    )

    facts = await build_commerce_context(
        message,
        state,
        intent,
        business_action=ORDER_VERIFY,
        allowed_source_groups={"order_facts"},
    )
    reply = await render_business_reply(message, facts)

    assert state.metadata["active_task"]["status"] == "verified_requires_human"
    assert reply is not None
    assert reply.need_human is True
    assert reply.route == "human"
    assert reply.answer == ""
    assert reply.metadata["handoff"]["reason"] == "order_action_requires_human"


@pytest.mark.asyncio
async def test_non_evaluation_request_cannot_use_order_fixture():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import ORDER_VERIFY

    message = _message("下单手机号是13000000000。").model_copy(
        update={
            "metadata": {
                "tool_state": {
                    "fixture_type": "order",
                    "mobile": "13000000000",
                    "orders": [{"order_no": "MUST-NOT-BE-USED"}],
                }
            }
        }
    )

    facts = await build_commerce_context(
        message,
        UserState(user_id="real-customer"),
        _intent("order_query"),
        order_service=object(),
        business_action=ORDER_VERIFY,
        allowed_source_groups={"order_facts"},
    )

    assert facts.tool_state.get("fixture_used") is not True
    assert facts.tool_state.get("orders") != [{"order_no": "MUST-NOT-BE-USED"}]


@pytest.mark.asyncio
async def test_paid_order_information_executes_order_lookup_instead_of_short_circuiting():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import ORDER_VERIFY

    calls = []

    class FakeOrderService:
        async def search_by_mobile(self, mobile, *, limit):
            calls.append((mobile, limit))
            return []

    state = UserState(
        user_id="wxid-customer",
        metadata={"commerce_mobile": "13800138000"},
    )
    intent = _intent("order_intent").model_copy(
        update={"slots": {"conversation_topic": "order_information"}}
    )
    facts = await build_commerce_context(
        _message("我已经通过微信付款了，收货地址也发给你了"),
        state,
        intent,
        order_service=FakeOrderService(),
        business_action=ORDER_VERIFY,
        allowed_source_groups={"order_facts"},
    )

    assert calls == [("13800138000", 3)]
    assert facts.tool_state["status"] == "not_found"
    assert facts.tool_state["lookup_performed"] is True


@pytest.mark.asyncio
async def test_order_query_reuses_mobile_from_recent_customer_chat():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
async def test_product_image_request_marks_image_delivery():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "芽黄素"
            return [
                YouzanProduct(
                    item_id="123",
                    title="建兰芽黄素",
                    alias="abc",
                    price_cent=6800,
                    stock=8,
                    image_url="https://cdn.example.com/yahuangsu.jpg",
                    page_path="pages/goods/detail?alias=abc",
                )
            ]

    facts = await build_commerce_context(
        _message("能给我发一下芽黄素的图片吗"),
        UserState(user_id="wxid-customer"),
        _intent("product_query"),
        product_service=FakeProductService(),
    )

    assert facts.tool_state["send_product_image"] is True


@pytest.mark.asyncio
async def test_product_link_request_without_product_asks_for_product():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

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
    from app.domains.decisioning.services.business_reply_renderer import render_business_reply

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
    from app.domains.decisioning.services.business_reply_renderer import render_business_reply

    facts = BusinessFacts(
        tool_state={
            "commerce_type": "product",
            "status": "found",
            "products": [
                {
                    "title": "建兰皇帝",
                    "price_cent": 29900,
                    "h5_url": "https://h5.youzan.com/goods/abc",
                    "image_url": "https://cdn.example.com/goods.jpg",
                    "knowledge": {"product_name": "皇帝", "category": "建兰"},
                }
            ],
        }
    )

    reply = await render_business_reply(_message("发我链接"), facts)

    assert "https://" not in reply.answer
    assert [item.type for item in reply.outbound_messages] == ["text", "link_card"]
    assert reply.outbound_messages[0].model_dump()["split"] is False
    card = json.loads(reply.outbound_messages[1].content)
    assert card == {
        "title": "建兰皇帝",
        "url": "https://h5.youzan.com/goods/abc",
        "description": "当前售价299元，点击查看详情和下单",
        "thumb_url": "https://cdn.example.com/goods.jpg",
    }


@pytest.mark.asyncio
async def test_product_renderer_returns_one_coherent_customer_message():
    from app.domains.decisioning.services.business_reply_renderer import render_business_reply

    facts = BusinessFacts(
        tool_state={
            "commerce_type": "product",
            "status": "found",
            "products": [
                {
                    "title": "建兰【芽黄素】田黄玉 建兰唯一黄素名品",
                    "price_cent": 6800,
                    "h5_url": "https://h5.youzan.com/goods/yahuangsu",
                    "image_url": "https://cdn.example.com/yahuangsu.jpg",
                    "knowledge": {
                        "product_name": "芽黄素",
                        "category": "建兰",
                        "highlighted_features": (
                            "1.芽色：新苗时期新芽呈淡黄色，随着生长逐渐转为黄绿色。\n"
                            "2.花色：花朵呈淡黄色素花，清秀雅致。\n"
                            "3.瓣型：外观清秀漂亮。"
                        ),
                    },
                }
            ],
        }
    )

    reply = await render_business_reply(_message("我喜欢绿色的素花"), facts)

    assert reply.answer == (
        "按您说的情况，我们目前库里这款建兰芽黄素比较适合您，当前售价68元，"
        "新苗时期新芽呈淡黄色，随着生长逐渐转为黄绿色；"
        "花朵呈淡黄色素花，清秀雅致。我把购买链接放下面，点开就能看详情和下单。"
    )
    assert len(reply.outbound_messages) == 2
    assert reply.outbound_messages[0].model_dump()["split"] is False
    assert "https://" not in reply.outbound_messages[0].content


@pytest.mark.asyncio
async def test_product_renderer_prefers_prebuilt_sales_copy():
    from app.domains.decisioning.services.business_reply_renderer import render_business_reply

    facts = BusinessFacts(
        tool_state={
            "commerce_type": "product",
            "status": "found",
            "products": [
                {
                    "title": "建兰芽黄素",
                    "price_cent": 6800,
                    "knowledge": {
                        "product_name": "芽黄素",
                        "category": "建兰",
                        "highlighted_features": "不应出现在回复里的旧特征",
                        "sales_copy": "新芽由明亮的淡黄色慢慢转为黄绿色，花叶相映，整体清雅耐看。",
                    },
                }
            ],
        }
    )

    reply = await render_business_reply(_message("我喜欢绿色素花"), facts)

    assert "新芽由明亮的淡黄色" in reply.answer
    assert "旧特征" not in reply.answer


@pytest.mark.asyncio
async def test_product_image_renderer_sends_text_image_and_card_without_symbols():
    from app.domains.decisioning.services.business_reply_renderer import render_business_reply

    facts = BusinessFacts(
        tool_state={
            "commerce_type": "product",
            "status": "found",
            "send_product_image": True,
            "products": [
                {
                    "title": "建兰芽黄素",
                    "price_cent": 6800,
                    "image_url": "https://cdn.example.com/yahuangsu.jpg",
                    "image_urls": [
                        "https://cdn.example.com/yahuangsu.jpg",
                        "https://cdn.example.com/yahuangsu-detail.jpg",
                    ],
                    "h5_url": "https://h5.youzan.com/goods/yahuangsu",
                    "knowledge": {
                        "product_name": "芽黄素（田黄玉）",
                        "category": "建兰",
                    },
                }
            ],
        }
    )

    reply = await render_business_reply(_message("发张图片"), facts)

    assert reply.answer == "可以的，这是建兰芽黄素的商品图片，您可以看看花色和株型。"
    assert not any(symbol in reply.answer for symbol in "“”‘’（）()—–")
    assert [item.type for item in reply.outbound_messages] == [
        "text",
        "image",
        "image",
        "link_card",
    ]
    assert reply.outbound_messages[1].content == "https://cdn.example.com/yahuangsu.jpg"
    assert (
        reply.outbound_messages[2].content
        == "https://cdn.example.com/yahuangsu-detail.jpg"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected_kind", "expects_card"),
    [
        ("会员具体有哪些服务，老师会看我这盆的情况吗？", "capability", True),
        ("会员多少钱？", "price", True),
        ("怎么开通会员？把购买链接发我。", "purchase", True),
        ("那你们的服务怎么进？多少钱？", "combined", True),
    ],
)
async def test_membership_questions_answer_current_need_before_card(
    text,
    expected_kind,
    expects_card,
):
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert keyword == "首单参与陪伴养兰客户"
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="membership-39",
                    title="首单参与陪伴养兰客户 专享特惠链接",
                    price_cent=3990,
                    h5_url="https://h5.youzan.com/goods/member-39",
                )
            ]

    intent = IntentResult(
        route="template_reply",
        primary_intent="product_query",
        primary_domain="product",
        primary_goal="seek_help",
        issues=["product_selection"],
        slots={
            "conversation_topic": "product_recommendation",
            "product_keywords": ["首单参与陪伴养兰客户"],
            "product_request_kind": "membership",
        },
        confidence=0.99,
        need_template=True,
    )
    facts = await build_commerce_context(
        _message(text),
        UserState(user_id="wxid-customer"),
        intent,
        product_service=FakeProductService(),
        allowed_source_groups={"product_catalog"},
    )
    reply = await render_business_reply(_message(text), facts)

    assert facts.tool_state["membership_question_kind"] == expected_kind
    assert facts.tool_state["send_purchase_card"] is expects_card
    if expected_kind == "capability":
        assert "一对一指导" in reply.answer
    if expected_kind == "combined":
        assert "后面还容易反复" in reply.answer
        assert "39.9元" in reply.answer
    assert (
        any(message.type in {"mini_program", "link_card"} for message in reply.outbound_messages)
        is expects_card
    )


@pytest.mark.asyncio
async def test_membership_purchase_followup_cannot_fall_back_to_orchid_card():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import CATALOG_SEARCH

    searches = []

    class FakeProductService:
        async def search(self, keyword, *, limit):
            searches.append((keyword, limit))
            if keyword == "首单参与陪伴养兰客户 专享特惠链接":
                return [
                    YouzanProduct(
                        item_id="membership-39",
                        title="首单参与陪伴养兰客户 专享特惠链接",
                        price_cent=3990,
                        h5_url="https://h5.youzan.com/goods/member-39",
                    )
                ]
            return [
                YouzanProduct(
                    item_id="orchid-1",
                    title="建兰忆香荷",
                    price_cent=2990,
                    h5_url="https://h5.youzan.com/goods/orchid-1",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "commerce_last_product_id": "membership-39",
            "commerce_last_product_keyword": "首单参与陪伴养兰客户 专享特惠链接",
            "commerce_last_product_kind": "membership",
        },
    )
    intent = IntentResult(
        route="template_reply",
        primary_intent="order_intent",
        primary_domain="product",
        primary_goal="purchase",
        issues=["product_selection"],
        slots={"conversation_topic": "product_recommendation"},
        confidence=0.99,
        need_template=True,
    )

    facts = await build_commerce_context(
        _message("可以，39.9元我能接受。把购买链接发我吧。"),
        state,
        intent,
        product_service=FakeProductService(),
        business_action=CATALOG_SEARCH,
        allowed_source_groups={"product_catalog"},
    )

    assert searches == [("首单参与陪伴养兰客户 专享特惠链接", 3)]
    assert facts.tool_state["product_request_kind"] == "membership"
    assert facts.tool_state["membership_question_kind"] == "combined"
    assert facts.tool_state["send_purchase_card"] is True
    assert [product["item_id"] for product in facts.tool_state["products"]] == [
        "membership-39"
    ]


@pytest.mark.asyncio
async def test_membership_purchase_intent_resends_an_existing_card():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import CATALOG_SEARCH

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert "陪伴养兰" in keyword
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="membership-39",
                    title="首单参与陪伴养兰客户 专享特惠链接",
                    price_cent=3990,
                    h5_url="https://h5.youzan.com/goods/member-39",
                )
            ]

    intent = IntentResult(
        route="template_reply",
        primary_intent="order_intent",
        primary_domain="product",
        primary_goal="transact",
        slots={
            "product_request_kind": "membership",
            "membership_question_kind": "combined",
        },
        confidence=0.55,
    )
    state = UserState(
        user_id="wxid-customer",
        metadata={"commerce_sent_card_ids": ["membership-39"]},
    )

    facts = await build_commerce_context(
        _message("你们有什么福利吗？买这个"),
        state,
        intent,
        product_service=FakeProductService(),
        business_action=CATALOG_SEARCH,
        allowed_source_groups={"product_catalog"},
    )

    assert facts.tool_state["send_purchase_card"] is True


@pytest.mark.asyncio
async def test_membership_price_objection_uses_facts_without_repeating_card():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import CATALOG_SEARCH
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    class FakeProductService:
        async def search(self, keyword, *, limit):
            assert "陪伴养兰" in keyword
            assert limit == 3
            return [
                YouzanProduct(
                    item_id="membership-39",
                    title="首单参与陪伴养兰客户 专享特惠链接",
                    price_cent=3990,
                    h5_url="https://h5.youzan.com/goods/member-39",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "commerce_last_product_id": "membership-39",
            "commerce_last_product_keyword": "首单参与陪伴养兰客户 专享特惠链接",
            "commerce_last_product_kind": "membership",
            "commerce_sent_card_ids": ["membership-39"],
        },
    )
    intent = IntentResult(
        route="template_reply",
        primary_intent="price_objection",
        primary_domain="commerce",
        primary_goal="express_objection",
        confidence=0.95,
    )
    message = _message("有点贵，能不能便宜点")

    facts = await build_commerce_context(
        message,
        state,
        intent,
        product_service=FakeProductService(),
        business_action=CATALOG_SEARCH,
        allowed_source_groups={"product_catalog"},
    )
    reply = await render_business_reply(message, facts)

    assert facts.tool_state["product_request_kind"] == "membership"
    assert facts.tool_state["membership_question_kind"] == "objection"
    assert facts.tool_state["price_label"] == "首单体验价"
    assert facts.tool_state["additional_discount_status"] == "unavailable"
    assert facts.tool_state["negotiation_allowed"] is False
    assert facts.tool_state["membership_objection_round"] == "initial"
    assert facts.tool_state["send_purchase_card"] is False
    assert facts.tool_state["previous_purchase_card_available"] is True
    assert reply is not None
    assert "39.9元" in reply.answer
    assert "一对一指导" in reply.answer
    assert "我理解您会关注价格" not in reply.answer
    assert "点上面的卡片开通" in reply.answer
    assert [message.type for message in reply.outbound_messages] == ["text"]


@pytest.mark.asyncio
async def test_repeated_membership_bargain_answers_directly_without_value_repetition():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import CATALOG_SEARCH
    from app.domains.decisioning.services.business_reply_renderer import (
        render_business_reply,
    )

    class FakeProductService:
        async def search(self, keyword, *, limit):
            del keyword, limit
            return [
                YouzanProduct(
                    item_id="membership-39",
                    title="首单参与陪伴养兰客户 专享特惠链接",
                    price_cent=3990,
                    h5_url="https://h5.youzan.com/goods/member-39",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={
            "commerce_last_product_id": "membership-39",
            "commerce_last_product_keyword": "首单参与陪伴养兰客户 专享特惠链接",
            "commerce_last_product_kind": "membership",
            "commerce_sent_card_ids": ["membership-39"],
            "recent_turns": [
                {"role": "user", "content": "有点贵，能不能便宜点"},
                {
                    "role": "assistant",
                    "content": "39.9元是首单体验价，后面遇到问题也有人指导。",
                },
            ],
        },
    )
    intent = IntentResult(
        route="template_reply",
        primary_intent="discount_request",
        primary_domain="commerce",
        primary_goal="express_objection",
        confidence=0.95,
    )
    message = _message("少一点可以不")

    facts = await build_commerce_context(
        message,
        state,
        intent,
        product_service=FakeProductService(),
        business_action=CATALOG_SEARCH,
        allowed_source_groups={"product_catalog"},
    )
    reply = await render_business_reply(message, facts)

    assert facts.tool_state["membership_objection_round"] == "followup"
    assert "不能再少" in reply.answer
    assert "点上面的卡片开通" in reply.answer
    assert "不着急" not in reply.answer
    assert "合适再参加" not in reply.answer
    assert "课程" not in reply.answer
    assert "一对一" not in reply.answer
    assert facts.tool_state["send_purchase_card"] is False
    assert facts.tool_state["previous_purchase_card_available"] is True


@pytest.mark.asyncio
async def test_accessory_result_is_removed_before_ai_context():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context
    from app.domains.decisioning.services.business_action_service import CATALOG_SEARCH

    class FakeProductService:
        async def search(self, keyword, *, limit):
            del keyword, limit
            return [
                YouzanProduct(
                    item_id="pot-1",
                    title="兰花专用紫砂盆",
                    price_cent=6000,
                    h5_url="https://h5.youzan.com/goods/pot",
                ),
                YouzanProduct(
                    item_id="orchid-1",
                    title="建兰红君荷",
                    price_cent=6800,
                    h5_url="https://h5.youzan.com/goods/orchid",
                ),
            ]

    facts = await build_commerce_context(
        _message("推荐一款好养的兰花"),
        UserState(user_id="wxid-customer"),
        _intent("product_query"),
        product_service=FakeProductService(),
        business_action=CATALOG_SEARCH,
        allowed_source_groups={"product_catalog"},
    )

    assert [product["item_id"] for product in facts.tool_state["products"]] == [
        "orchid-1"
    ]


@pytest.mark.asyncio
async def test_same_product_card_is_not_repeated_without_explicit_request():
    from app.domains.catalog.services.commerce_query_service import build_commerce_context

    class FakeProductService:
        async def search(self, keyword, *, limit):
            del keyword, limit
            return [
                YouzanProduct(
                    item_id="orchid-1",
                    title="建兰红君荷",
                    price_cent=6800,
                    h5_url="https://h5.youzan.com/goods/orchid",
                )
            ]

    state = UserState(
        user_id="wxid-customer",
        metadata={"commerce_sent_card_ids": ["orchid-1"]},
    )
    facts = await build_commerce_context(
        _message("这款适合新手吗？"),
        state,
        _intent("product_query"),
        product_service=FakeProductService(),
    )

    assert facts.tool_state["status"] == "found"
    assert facts.tool_state["send_purchase_card"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["13800138000", "好的，13800138000"])
async def test_phone_followup_keeps_pending_order_query_intent(text):
    from app.domains.decisioning.services.intent_service import classify_intent

    state = UserState(
        user_id="wxid-customer",
        metadata={"commerce_pending": "order_mobile"},
    )
    intent = await classify_intent(_message(text), state)

    assert intent.primary_intent == "order_query"
    assert intent.route == "template_reply"
