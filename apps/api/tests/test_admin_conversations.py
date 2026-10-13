import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
import json

from fastapi.testclient import TestClient
import pytest

from app.core.config import get_settings
from app.main import app
from app.domains.conversations.services.conversation_service import record_customer_message
from app.domains.conversations.services.conversation_event_service import conversation_event_broker


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "chat_logs.db"
    profile_db_path = tmp_path / "profiles.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{profile_db_path.as_posix()}")
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("EYUN_WC_ID", "")
    monkeypatch.setenv("EYUN_WID", "")
    get_settings.cache_clear()


def test_conversation_list_starts_empty(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/admin/conversations")

    assert response.status_code == 200
    assert response.json()["data"] == {"items": [], "total": 0, "page": 1, "page_size": 50}


def test_paired_file_callbacks_merge_metadata(monkeypatch, tmp_path):
    import asyncio
    from app.domains.conversations.services.conversation_service import (
        get_conversation_detail,
        merge_recent_customer_file_message,
    )

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="file_customer",
            session_id="owner",
            content="[文件]",
            message_id="first",
            status="ai_waiting",
            route="inbound_file",
            primary_intent="file",
            metadata={
                "media": {
                    "type": "file",
                    "file_name": "养兰资料.docx",
                    "file_size": "9558250",
                }
            },
        )
    )

    assert merge_recent_customer_file_message(
        channel="wechat",
        user_id="file_customer",
        session_id="owner",
        message_id="second",
        metadata={
            "media": {
                "type": "file",
                "file_name": "养兰资料.docx",
                "file_size": "9558250",
                "url": "https://media.example.com/file.docx",
            }
        },
    )
    detail = asyncio.run(get_conversation_detail("wechat:file_customer:owner"))
    assert len(detail["messages"]) == 1
    message = detail["messages"][0]
    assert message["metadata"]["media"]["url"].endswith("file.docx")
    assert message["metadata"]["provider_message_ids"] == ["first", "second"]


def test_detail_hides_legacy_sysmsg_and_collapses_historical_files(
    monkeypatch, tmp_path
):
    import asyncio
    from app.domains.conversations.services.conversation_service import (
        get_conversation_detail,
    )

    _reset_settings(monkeypatch, tmp_path)
    common = {
        "channel": "wechat",
        "user_id": "legacy_customer",
        "session_id": "owner",
        "status": "ai_active",
    }
    asyncio.run(
        record_customer_message(
            **common,
            content="[非文本消息]",
            message_id="system-1",
            route="non_text",
            metadata={
                "message_type": "60999",
                "raw_content": '<sysmsg type="functionmsg"><functionmsg /></sysmsg>',
            },
        )
    )
    for message_id, url in (
        ("file-1", ""),
        ("file-2", "https://media.example.com/guide.docx"),
    ):
        asyncio.run(
            record_customer_message(
                **common,
                content="[文件]",
                message_id=message_id,
                route="inbound_file",
                metadata={
                    "message_type": "60008",
                    "media": {
                        "type": "file",
                        "file_name": "养兰指南.docx",
                        "url": url,
                    },
                },
            )
        )
    detail = asyncio.run(get_conversation_detail("wechat:legacy_customer:owner"))
    assert len(detail["messages"]) == 1
    assert detail["messages"][0]["metadata"]["media"]["url"].endswith(
        "guide.docx"
    )


def test_failed_delivery_can_be_requeued_from_admin(monkeypatch, tmp_path):
    from app.infrastructure.database.models import (
        ConversationMessageModel,
        EyunOutboundDeliveryEventModel,
        EyunOutboundMessageModel,
    )
    from app.integrations.eyun.services import message_risk_control_service as risk

    _reset_settings(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with risk._get_session() as session:
        message = ConversationMessageModel(
            conversation_id="wechat:retry_customer:owner",
            delivery_status="failed",
            sender_type="system",
            sender_id="service_material_touch",
            content="[图片]",
            metadata_json="{}",
            created_at=now,
        )
        session.add(message)
        session.flush()
        outbound = EyunOutboundMessageModel(
            w_id="wid",
            wc_id="retry_customer",
            content="image",
            source_batch_key="test:manual-retry",
            conversation_message_id=message.id,
            status="failed",
            priority=20,
            due_at=now,
            attempts=4,
            last_error="provider failed",
            created_at=now,
            updated_at=now,
        )
        session.add(outbound)
        session.commit()
        message_id = message.id
        outbound_id = outbound.id

    response = TestClient(app).post(
        f"/api/v1/admin/conversations/messages/{message_id}/retry-delivery"
    )
    assert response.status_code == 200
    with risk._get_session() as session:
        outbound = session.get(EyunOutboundMessageModel, outbound_id)
        assert outbound.status == "queued"
        assert outbound.attempts == 0
        assert session.query(EyunOutboundDeliveryEventModel).one().event == (
            "manual_retry_scheduled"
        )


def test_message_recognition_stats_report_categories_and_results(
    monkeypatch, tmp_path
):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="reaction_customer",
            session_id="default",
            content="[表情]",
            status="ai_active",
            route="inbound_emoji",
            metadata={
                "message_type": "60006",
                "inbound_classification": {
                    "category": "emoji",
                    "subtype": "emoji",
                    "disposition": "reaction",
                },
            },
        )
    )
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="video_customer",
            session_id="default",
            content="[视频]",
            status="ai_waiting",
            route="inbound_video",
            metadata={
                "message_type": "60003",
                "inbound_classification": {
                    "category": "video",
                    "subtype": "video",
                    "disposition": "media",
                },
                "media": {
                    "type": "video",
                    "recognition": {"status": "succeeded"},
                },
            },
        )
    )

    response = TestClient(app).get(
        "/api/v1/admin/conversations/message-recognition-stats"
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total_customer_messages"] == 2
    assert data["classified_messages"] == 2
    assert data["categories"] == {"emoji": 1, "video": 1}
    assert data["dispositions"] == {"reaction": 1, "media": 1}
    assert data["recognition_statuses"] == {"succeeded": 1}


def test_admin_conversation_list_filters_by_channel(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    for channel, user_id in (("api", "test_user"), ("wechat", "real_customer")):
        asyncio.run(
            record_customer_message(
                channel=channel,
                user_id=user_id,
                session_id="default",
                content="hello",
            )
        )

    data = TestClient(app).get(
        "/api/v1/admin/conversations", params={"channel": "wechat"}
    ).json()["data"]

    assert data["total"] == 1
    assert data["items"][0]["channel"] == "wechat"
    assert data["items"][0]["user_id"] == "real_customer"


def test_admin_conversations_are_scoped_by_eyun_wc_id(monkeypatch, tmp_path):
    import asyncio
    from app.integrations.eyun.services import eyun_contact_service

    _reset_settings(monkeypatch, tmp_path)
    tenant_names = {
        "wxid_shop_a": "若兰",
        "wxid_shop_b": "小兰二号",
    }

    async def fake_contact_snapshot(*, w_id, wc_id):
        assert w_id in {"wid_shop_a", "wid_shop_b"} or wc_id not in tenant_names
        return {"nickname": tenant_names[wc_id]} if wc_id in tenant_names else {}

    monkeypatch.setattr(
        eyun_contact_service,
        "get_eyun_contact_snapshot",
        fake_contact_snapshot,
    )

    for wc_id in tenant_names:
        asyncio.run(
            record_customer_message(
                channel="wechat",
                user_id="shared_customer",
                session_id=wc_id,
                content=f"hello from {wc_id}",
                metadata={
                    "provider": "eyun",
                    "owner_wc_id": wc_id,
                    "contact_wc_id": "shared_customer",
                    "wc_id": wc_id,
                    "w_id": wc_id.replace("wxid_", "wid_"),
                },
            )
        )

    client = TestClient(app)
    tenants = client.get("/api/v1/admin/conversations/tenants").json()["data"]["items"]
    display_names = {item["wc_id"]: item["display_name"] for item in tenants}
    assert display_names["wxid_shop_a"] == "若兰"
    assert display_names["wxid_shop_b"] == "小兰二号"

    scoped = client.get(
        "/api/v1/admin/conversations",
        params={"channel": "wechat", "tenant_id": "wxid_shop_b"},
    ).json()["data"]
    assert scoped["total"] == 1
    assert scoped["items"][0]["user_id"] == "shared_customer"
    assert scoped["items"][0]["session_id"] == "wxid_shop_b"
    assert scoped["items"][0]["owner_wc_id"] == "wxid_shop_b"
    assert scoped["items"][0]["owner_display_name"] == "小兰二号"


def test_legacy_contact_tenants_are_removed_and_shared_history_is_split(
    monkeypatch, tmp_path
):
    from app.domains.conversations.services.conversation_service import (
        _backfill_conversation_tenants,
        _get_session,
    )
    from app.infrastructure.database.models import (
        ConversationMessageModel,
        ConversationModel,
    )

    _reset_settings(monkeypatch, tmp_path)
    source_id = "wechat:shared_customer:shared_customer"
    started_at = datetime(2026, 8, 21, tzinfo=timezone.utc)
    with _get_session() as session:
        session.add(
            ConversationModel(
                conversation_id=source_id,
                channel="wechat",
                user_id="shared_customer",
                session_id="shared_customer",
                tenant_id="shared_customer",
                owner_wc_id="shared_customer",
                owner_display_name="联系人残留",
                status="ai_waiting",
                unread_count=2,
                created_at=started_at,
                updated_at=started_at + timedelta(minutes=3),
            )
        )
        legacy_messages = [
            (
                "customer",
                "A 客户消息",
                {
                    "provider": "eyun",
                    "wc_id": "wxid_shop_a",
                    "w_id": "wid_shop_a",
                    "from_user": "shared_customer",
                    "owner_display_name": "若兰",
                },
            ),
            (
                "ai",
                "A 回复",
                {
                    "provider": "eyun",
                    "wc_id": "shared_customer",
                    "w_id": "wid_shop_a",
                },
            ),
            (
                "customer",
                "B 客户消息",
                {
                    "provider": "eyun",
                    "wc_id": "wxid_shop_b",
                    "w_id": "wid_shop_b",
                    "from_user": "shared_customer",
                    "owner_display_name": "小兰二号",
                },
            ),
            (
                "human",
                "B 回复",
                {
                    "provider": "eyun",
                    "wc_id": "shared_customer",
                    "w_id": "wid_shop_b",
                },
            ),
        ]
        for index, (sender_type, content, metadata) in enumerate(legacy_messages):
            session.add(
                ConversationMessageModel(
                    conversation_id=source_id,
                    sender_type=sender_type,
                    sender_id=(
                        "shared_customer"
                        if sender_type == "customer"
                        else sender_type
                    ),
                    content=content,
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                    created_at=started_at + timedelta(minutes=index),
                )
            )
        session.commit()

    with _get_session() as session:
        _backfill_conversation_tenants(session)
        session.commit()
        _backfill_conversation_tenants(session)
        session.commit()

    client = TestClient(app)
    tenants = client.get("/api/v1/admin/conversations/tenants").json()["data"][
        "items"
    ]
    assert {item["wc_id"] for item in tenants} == {
        "wxid_shop_a",
        "wxid_shop_b",
    }
    assert "shared_customer" not in {item["wc_id"] for item in tenants}

    for owner_wc_id, expected_contents in (
        ("wxid_shop_a", ["A 客户消息", "A 回复"]),
        ("wxid_shop_b", ["B 客户消息", "B 回复"]),
    ):
        detail = client.get(
            f"/api/v1/admin/conversations/wechat:shared_customer:{owner_wc_id}"
        ).json()["data"]
        assert [message["content"] for message in detail["messages"]] == (
            expected_contents
        )
        assert all(
            message["metadata"]["owner_wc_id"] == owner_wc_id
            and message["metadata"]["contact_wc_id"] == "shared_customer"
            for message in detail["messages"]
        )

    source = client.get(
        f"/api/v1/admin/conversations/{source_id}"
    ).json()["data"]
    assert source["conversation"]["owner_wc_id"] is None
    assert source["conversation"]["tenant_id"] == "tenant_default"
    assert source["messages"] == []


def test_evaluation_turn_is_recorded_as_a_labeled_workbench_conversation(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.schemas.event import NormalizedMessage
    from app.domains.conversations.services.chat_orchestrator import (
        _record_workbench_turn,
    )

    _reset_settings(monkeypatch, tmp_path)
    async def record_turn(message_text: str, answer_text: str, trace_id: str) -> None:
        await _record_workbench_turn(
            message=NormalizedMessage(
                trace_id=trace_id,
                channel="api",
                user_id="eval_classic_case_12",
                session_id="session_eval_12",
                message=message_text,
                kb_id="kb_default",
                metadata={
                    "evaluation_id": "classic-case-12",
                    "skip_customer_record": True,
                },
            ),
            result={
                "answer": answer_text,
                "answer_segments": [answer_text],
                "route": "rag_answer",
                "intent": {
                    "primary_intent": "care_question",
                    "sales_stage": (
                        "need_discovery"
                        if trace_id == "trace_eval_001"
                        else "pain_discovery"
                    ),
                },
                "sources": [],
                "template": {},
                "need_human": trace_id == "trace_eval_002",
                "handoff": (
                    {"reason": "should_not_notify"}
                    if trace_id == "trace_eval_002"
                    else None
                ),
            },
            is_evaluation=True,
        )

    asyncio.run(
        record_turn(
            "我的兰花烂根了，应该怎么办？",
            "先把烂根清理掉，再换成透气、排水好的植料。",
            "trace_eval_001",
        )
    )
    asyncio.run(
        record_turn(
            "我现在用的是水苔。",
            "那先把旧水苔清理干净，修掉发黑发软的根。",
            "trace_eval_002",
        )
    )

    client = TestClient(app)
    regular_listing = client.get(
        "/api/v1/admin/conversations", params={"channel": "wechat"}
    ).json()["data"]
    test_listing = client.get(
        "/api/v1/admin/conversations",
        params={"test_only": True},
    ).json()["data"]
    assert regular_listing["items"] == []
    assert regular_listing["total"] == 0
    assert test_listing["total"] == 1
    assert test_listing["items"][0]["user_display_name"] == "测试案例｜classic-case-12"
    assert test_listing["items"][0]["status"] == "handoff_pending"

    detail = client.get(
        "/api/v1/admin/conversations/"
        "wechat:eval_classic_case_12:session_eval_12"
    ).json()["data"]
    assert [item["sender_type"] for item in detail["messages"]] == [
        "customer",
        "ai",
        "customer",
        "ai",
    ]
    assert [item["content"] for item in detail["messages"]] == [
        "我的兰花烂根了，应该怎么办？",
        "先把烂根清理掉，再换成透气、排水好的植料。",
        "我现在用的是水苔。",
        "那先把旧水苔清理干净，修掉发黑发软的根。",
    ]
    assert all(
        item["metadata"]["evaluation_id"] == "classic-case-12"
        and item["metadata"]["is_evaluation"] is True
        for item in detail["messages"]
    )
    assert all("sales_stage" not in item["metadata"] for item in detail["messages"])


def test_evaluation_card_is_recorded_as_real_workbench_message(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.schemas.event import NormalizedMessage
    from app.domains.conversations.services.chat_orchestrator import (
        _record_workbench_turn,
    )

    _reset_settings(monkeypatch, tmp_path)
    card = {
        "title": "陪伴养兰会员",
        "url": "https://h5.youzan.com/goods/member-39",
        "description": "点击查看详情和下单",
        "thumb_url": "https://cdn.example.com/member.jpg",
    }
    asyncio.run(
        _record_workbench_turn(
            message=NormalizedMessage(
                trace_id="trace_eval_card",
                channel="api",
                user_id="eval_card_case",
                session_id="session_eval_card",
                message="把会员购买链接发我",
                kb_id="kb_default",
                metadata={"evaluation_id": "card-case"},
            ),
            result={
                "answer": "可以的，我把购买链接发您。",
                "answer_segments": ["可以的，我把购买链接发您。"],
                "outbound_messages": [
                    {"type": "text", "content": "可以的，我把购买链接发您。"},
                    {"type": "link_card", "content": json.dumps(card, ensure_ascii=False)},
                ],
                "route": "template_reply",
                "intent": {"primary_intent": "product_query"},
                "sources": [],
                "template": {},
                "need_human": False,
            },
            is_evaluation=True,
        )
    )

    detail = TestClient(app).get(
        "/api/v1/admin/conversations/"
        "wechat:eval_card_case:session_eval_card"
    ).json()["data"]
    ai_messages = [
        item for item in detail["messages"] if item["sender_type"] == "ai"
    ]
    assert [item["metadata"]["message_type"] for item in ai_messages] == [
        "text",
        "link_card",
    ]
    assert ai_messages[1]["metadata"]["link_card"] == card
    assert ai_messages[1]["metadata"]["simulated_delivery"] is True


def test_demo_card_counts_as_sent_for_material_deduplication(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.schemas.event import NormalizedMessage
    from app.domains.conversations.services.conversation_service import (
        record_ai_turn,
        was_outbound_content_sent,
    )

    _reset_settings(monkeypatch, tmp_path)
    title = "萧岚苑陪伴养兰资料"
    card = {
        "title": title,
        "url": "https://example.com/orchid-guide",
        "description": "养兰基础资料",
        "thumb_url": "",
    }
    asyncio.run(
        record_ai_turn(
            message=NormalizedMessage(
                trace_id="trace_demo_material",
                channel="wechat",
                user_id="demo_material_customer",
                session_id="default",
                message="把资料发我",
                kb_id="kb_default",
                metadata={
                    "provider": "eyun",
                    "provider_delivery_mode": "simulated",
                },
            ),
            result={
                "answer": "资料发您了。",
                "outbound_messages": [
                    {"type": "text", "content": "资料发您了。"},
                    {
                        "type": "link_card",
                        "content": json.dumps(card, ensure_ascii=False),
                    },
                ],
                "route": "agent",
                "intent": {"primary_intent": "autonomous_sales_turn"},
                "need_human": False,
            },
        )
    )

    detail = TestClient(app).get(
        "/api/v1/admin/conversations/"
        "wechat:demo_material_customer:default"
    ).json()["data"]
    ai_messages = [
        item for item in detail["messages"] if item["sender_type"] == "ai"
    ]
    assert all(item["delivery_status"] == "sent" for item in ai_messages)
    assert was_outbound_content_sent(
        channel="wechat",
        user_id="demo_material_customer",
        content_marker=title,
        within_days=30,
    )


def test_list_actions_apply_to_customer_group_and_keep_tenant_isolation(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    for session_id, tenant_id in (("a1", "shop_a"), ("a2", "shop_a"), ("b1", "shop_b")):
        asyncio.run(record_customer_message(
            channel="api", user_id="shared_customer", session_id=session_id,
            tenant_id=tenant_id, content="hello", status="ai_waiting",
        ))
    client = TestClient(app)
    base = "/api/v1/admin/conversations"
    target = f"{base}/api:shared_customer:a1"
    before = client.get(target).json()["data"]["conversation"]

    pinned = client.post(f"{target}/list-action", json={"action": "pin"})
    assert pinned.status_code == 200
    assert len(pinned.json()["data"]["items"]) == 2
    first = client.get(base, params={"page_size": 1}).json()["data"]["items"][0]
    assert first["tenant_id"] == "shop_a"
    assert first["is_pinned"] is True
    assert client.get(target).json()["data"]["conversation"]["updated_at"] == before["updated_at"]
    other = client.get(f"{base}/api:shared_customer:b1").json()["data"]["conversation"]
    assert other["is_pinned"] is False

    client.post(f"{target}/list-action", json={"action": "mute"})
    new_session = asyncio.run(record_customer_message(
        channel="api", user_id="shared_customer", session_id="a3", tenant_id="shop_a",
        content="new session", status="ai_waiting",
    ))
    assert new_session["is_pinned"] is True
    assert new_session["is_muted"] is True
    client.post(f"{target}/list-action", json={"action": "unpin"})
    assert all(not item["is_pinned"] for item in client.get(base).json()["data"]["items"])
    hidden = client.post(f"{target}/list-action", json={"action": "hide"})
    assert len(hidden.json()["data"]["items"]) == 3
    assert client.get(base).json()["data"]["total"] == 1
    assert client.get(target).json()["data"]["messages"]
    assert client.post(f"{target}/list-action", json={"action": "invalid"}).status_code == 422
    assert client.post(f"{base}/missing/list-action", json={"action": "pin"}).status_code == 404


def test_manual_unread_survives_background_read_and_new_messages(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(record_customer_message(
        channel="api", user_id="unread_customer", session_id="default",
        content="first", status="ai_waiting",
    ))
    client = TestClient(app)
    target = "/api/v1/admin/conversations/api:unread_customer:default"
    client.post(f"{target}/read")
    client.post(f"{target}/list-action", json={"action": "unread"})
    automatic = client.post(f"{target}/read", params={"automatic": True}).json()["data"]
    assert automatic["marked_unread"] is True
    assert automatic["unread_count"] == 1
    asyncio.run(record_customer_message(
        channel="api", user_id="unread_customer", session_id="default",
        content="second", status="ai_waiting",
    ))
    marked = client.post(f"{target}/list-action", json={"action": "unread"}).json()["data"]["items"][0]
    assert marked["unread_count"] == 2
    read = client.post(f"{target}/read").json()["data"]
    assert read["unread_count"] == 0
    assert read["marked_unread"] is False


def test_muted_conversation_keeps_messages_without_tenant_unread_alert(monkeypatch, tmp_path):
    import asyncio

    from app.domains.conversations.services.conversation_service import list_conversation_tenants

    _reset_settings(monkeypatch, tmp_path)
    metadata = {"provider": "eyun", "owner_wc_id": "wxid_shop", "owner_display_name": "shop"}
    asyncio.run(record_customer_message(
        channel="wechat", user_id="mute_customer", session_id="wxid_shop",
        content="first", status="ai_waiting", metadata=metadata,
    ))
    client = TestClient(app)
    target = "/api/v1/admin/conversations/wechat:mute_customer:wxid_shop"
    client.post(f"{target}/list-action", json={"action": "mute"})
    updated = asyncio.run(record_customer_message(
        channel="wechat", user_id="mute_customer", session_id="wxid_shop",
        content="second", status="ai_waiting", metadata=metadata,
    ))
    assert updated["is_muted"] is True
    assert updated["unread_count"] == 2
    assert updated["status"] == "ai_waiting"
    assert updated["last_message"] == "second"
    tenants = asyncio.run(list_conversation_tenants())["items"]
    assert tenants[0]["unread_count"] == 0
    assert tenants[0]["conversation_count"] == 1
    client.post(f"{target}/list-action", json={"action": "unmute"})
    assert asyncio.run(list_conversation_tenants())["items"][0]["unread_count"] == 2


def test_conversation_list_preferences_upgrade_existing_database(monkeypatch, tmp_path):
    import asyncio
    from sqlalchemy import create_engine, text
    from app.domains.conversations.services import conversation_service

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(record_customer_message(
        channel="api", user_id="legacy_customer", session_id="default",
        content="existing history", status="ai_waiting",
    ))
    db_url = get_settings().chat_log_db_url
    engine = create_engine(db_url)
    with engine.begin() as connection:
        for column in ("is_pinned", "is_muted", "marked_unread"):
            connection.execute(text(f"ALTER TABLE conversations DROP COLUMN {column}"))
    engine.dispose()
    conversation_service._sessionmakers.pop(db_url).kw["bind"].dispose()
    conversation_service._initialized_urls.discard(db_url)
    data = TestClient(app).get("/api/v1/admin/conversations").json()["data"]["items"][0]
    assert data["is_pinned"] is False
    assert data["is_muted"] is False
    assert data["marked_unread"] is False
    assert data["last_message"] == "existing history"
    assert data["unread_count"] == 1


def test_hidden_conversation_reappears_after_new_customer_message(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="real_customer",
            session_id="default",
            content="first message",
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:real_customer:default"

    hidden = client.post(f"/api/v1/admin/conversations/{conversation_id}/hide")

    assert hidden.status_code == 200
    assert client.get("/api/v1/admin/conversations").json()["data"]["total"] == 0
    assert client.get(
        f"/api/v1/admin/conversations/{conversation_id}"
    ).status_code == 200

    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="real_customer",
            session_id="default",
            content="new message",
        )
    )

    data = client.get("/api/v1/admin/conversations").json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["last_message"] == "new message"


def test_hidden_conversation_can_be_restored(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="real_customer",
            session_id="default",
            content="hello",
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:real_customer:default"

    client.post(f"/api/v1/admin/conversations/{conversation_id}/hide")
    restored = client.post(f"/api/v1/admin/conversations/{conversation_id}/unhide")

    assert restored.status_code == 200
    assert client.get("/api/v1/admin/conversations").json()["data"]["total"] == 1


def test_handoff_pending_suppresses_ai_and_preserves_conversation_state(
    monkeypatch, tmp_path
):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="api",
            user_id="locked_customer",
            session_id="locked_session",
            content="initial handoff message",
            route="human",
            primary_intent="human_request",
            handoff_reason="human_required",
        )
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "locked_customer",
            "session_id": "locked_session",
            "message": "客户继续发消息",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    data = response.json()["data"]
    detail = client.get(
        "/api/v1/admin/conversations/api:locked_customer:locked_session"
    ).json()["data"]
    assert data["answer"] == ""
    assert data.get("outbound_messages", []) == []
    assert data["metadata"]["handoff_locked"] is True
    assert detail["conversation"]["status"] == "handoff_pending"
    assert detail["conversation"]["last_route"] == "human"
    assert [item["sender_type"] for item in detail["messages"]] == [
        "customer",
        "customer",
    ]


def test_unclaimed_legacy_fallback_handoff_recovers_before_next_turn(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        conversation_blocks_ai,
        recover_automatic_handoff,
    )

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="api",
            user_id="legacy_fallback_customer",
            session_id="legacy_fallback_session",
            content="那你们的服务怎么进？多少钱？",
            route="human",
            primary_intent="ask_price",
            handoff_reason="template_not_found_to_handoff",
        )
    )
    assert conversation_blocks_ai(
        channel="api",
        user_id="legacy_fallback_customer",
        session_id="legacy_fallback_session",
    )

    recovered = asyncio.run(
        recover_automatic_handoff(
            channel="api",
            user_id="legacy_fallback_customer",
            session_id="legacy_fallback_session",
        )
    )

    assert recovered is True
    assert not conversation_blocks_ai(
        channel="api",
        user_id="legacy_fallback_customer",
        session_id="legacy_fallback_session",
    )


def test_unsupported_message_handoff_recovers_without_touching_manual_handoff(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        conversation_blocks_ai,
        recover_stale_unsupported_handoffs,
    )

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="unsupported_customer",
            session_id="unsupported_session",
            content="[表情]",
            route="non_text",
            handoff_reason="unsupported_message_type",
        )
    )
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="manual_customer",
            session_id="manual_session",
            content="我要找人工",
            route="human",
            handoff_reason="manual_force_handoff",
        )
    )

    recovered = asyncio.run(
        recover_stale_unsupported_handoffs(older_than_seconds=0)
    )

    assert recovered == 1
    assert not conversation_blocks_ai(
        channel="wechat",
        user_id="unsupported_customer",
        session_id="unsupported_session",
    )
    assert conversation_blocks_ai(
        channel="wechat",
        user_id="manual_customer",
        session_id="manual_session",
    )


def test_explicit_human_handoff_does_not_auto_recover(monkeypatch, tmp_path):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        conversation_blocks_ai,
        recover_automatic_handoff,
    )

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="api",
            user_id="explicit_human_customer",
            session_id="explicit_human_session",
            content="我要找人工",
            route="human",
            primary_intent="human_request",
            handoff_reason="human_required",
        )
    )

    recovered = asyncio.run(
        recover_automatic_handoff(
            channel="api",
            user_id="explicit_human_customer",
            session_id="explicit_human_session",
        )
    )

    assert recovered is False
    assert conversation_blocks_ai(
        channel="api",
        user_id="explicit_human_customer",
        session_id="explicit_human_session",
    )


def test_human_active_suppresses_ai_until_admin_releases_it(monkeypatch, tmp_path):
    import asyncio

    from app.domains.customers.services.user_profile_service import get_profile_bundle

    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    conversation_id = "api:release_customer:release_session"
    handoff = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "release_customer",
            "session_id": "release_session",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    assert handoff.json()["data"]["need_human"] is True

    claimed = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/claim",
        json={"operator_id": "op_release"},
    )
    assert claimed.json()["data"]["status"] == "human_active"
    blocked = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "release_customer",
            "session_id": "release_session",
            "message": "人工接管期间的新消息",
            "kb_id": "kb_default",
            "metadata": {},
        },
    ).json()["data"]
    assert blocked["answer"] == ""
    claimed_detail = client.get(
        f"/api/v1/admin/conversations/{conversation_id}"
    ).json()["data"]["conversation"]
    assert claimed_detail["status"] == "human_active"
    assert claimed_detail["owner_id"] == "op_release"

    released = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/release-to-ai",
        json={"operator_id": "op_release"},
    )
    assert released.json()["data"]["status"] == "ai_active"
    profile = asyncio.run(get_profile_bundle("release_customer"))["profile"]
    assert profile["is_human_handoff"] is False
    assert profile["human_handoff_status"] is None

    resumed = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "release_customer",
            "session_id": "release_session",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    ).json()["data"]
    assert resumed["answer"]
    assert resumed["route"] == "agent"


def test_admin_displays_all_wechat_groups(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("EYUN_MATERIAL_GROUP_WC_ID", "material@chatroom")
    get_settings.cache_clear()
    for group_id in ("material@chatroom", "other@chatroom"):
        asyncio.run(
            record_customer_message(
                channel="wechat",
                user_id=group_id,
                session_id="default",
                content="[图片]",
            )
        )

    data = TestClient(app).get("/api/v1/admin/conversations").json()["data"]

    assert data["total"] == 2
    assert {item["user_id"] for item in data["items"]} == {
        "material@chatroom", "other@chatroom"
    }


def test_chat_creates_ai_owned_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    event_queue = conversation_event_broker.subscribe()

    chat = client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    event = event_queue.get_nowait()
    conversation_event_broker.unsubscribe(event_queue)

    assert chat.status_code == 200
    assert event["conversation_id"] == "api:user_001:sess_001"
    assert event["reason"] == "message"
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 1
    item = conversations["items"][0]
    assert item["conversation_id"] == "api:user_001:sess_001"
    assert item["status"] in {"ai_active", "ai_waiting"}
    assert item["owner_id"] is None

    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001")
    assert detail.status_code == 200
    messages = detail.json()["data"]["messages"]
    assert [message["sender_type"] for message in messages] == ["customer", "ai"]


def test_conversation_exposes_customer_display_snapshot(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    client.post(
        "/api/v1/chat",
        json={
            "channel": "wechat",
            "user_id": "wxid_customer",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {
                "remark_name": "Alice Remark",
                "avatar_url": "https://example.com/avatar.jpg",
            },
        },
    )

    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    item = conversations["items"][0]
    assert item["user_display_name"] == "Alice Remark"
    assert item["user_avatar_url"] == "https://example.com/avatar.jpg"

    detail = client.get("/api/v1/admin/conversations/wechat:wxid_customer:sess_001")
    conversation = detail.json()["data"]["conversation"]
    assert conversation["user_display_name"] == "Alice Remark"
    assert conversation["user_avatar_url"] == "https://example.com/avatar.jpg"


def test_conversation_timestamps_are_serialized_as_utc(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    item = client.get("/api/v1/admin/conversations").json()["data"]["items"][0]
    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001").json()[
        "data"
    ]

    assert item["created_at"].endswith("+00:00")
    assert item["updated_at"].endswith("+00:00")
    assert detail["messages"][0]["created_at"].endswith("+00:00")


def test_existing_eyun_messages_recover_media_from_raw_content(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_customer",
            session_id="default",
            content="[视频]",
            metadata={
                "provider": "eyun",
                "message_type": "60003",
                "raw_content": (
                    '<msg><videomsg cdnvideourl="https://cdn.example.com/old.mp4" />'
                    "</msg>"
                ),
                "media": {
                    "type": "video",
                    "url": "https://cdn.example.com/old.mp4",
                    "fallback": False,
                },
            },
        )
    )

    client = TestClient(app)
    detail = client.get(
        "/api/v1/admin/conversations/wechat:wxid_customer:default"
    ).json()["data"]

    assert detail["messages"][0]["metadata"]["media"] == {
        "type": "video",
        "original_url": "https://cdn.example.com/old.mp4",
        "fallback": True,
        "resolve_status": "pending",
    }


def test_human_cannot_reply_until_conversation_is_claimed(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    response = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/reply",
        json={"operator_id": "op_001", "content": "人工回复"},
    )

    assert response.status_code == 409
    assert "不能人工回复" in response.json()["message"]


def test_claimed_handoff_conversation_accepts_human_reply(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    claim = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )
    reply = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/reply",
        json={"operator_id": "op_001", "content": "您好，我来处理。"},
    )

    assert claim.status_code == 200
    assert claim.json()["data"]["status"] == "human_active"
    assert reply.status_code == 200
    assert reply.json()["data"]["status"] == "human_active"

    detail = client.get("/api/v1/admin/conversations/api:user_001:sess_001")
    messages = detail.json()["data"]["messages"]
    assert messages[-1]["sender_type"] == "human"
    assert messages[-1]["content"] == "您好，我来处理。"

    memories = client.get("/api/v1/users/user_001/memories").json()["data"]["items"]
    assert memories[-1]["role"] == "human"
    assert memories[-1]["content"] == "您好，我来处理。"


def test_human_reply_to_eyun_conversation_sends_via_provider(monkeypatch, tmp_path):
    from app.integrations.eyun.services import eyun_callback_service
    from app.integrations.eyun.services import message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    sent = []

    async def fail_enqueue(payload):
        raise AssertionError("non-text callbacks should not enter the AI queue")

    async def fake_enqueue_wechat_outbound(**kwargs):
        sent.append(kwargs)
        return {"id": 1, "status": "queued"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fail_enqueue)
    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue_wechat_outbound,
    )
    client = TestClient(app)

    callback = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60003",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "toUser": "wxid_bot",
                "content": "<msg><videomsg /></msg>",
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )
    force = client.post(
        "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot/force-handoff",
        json={"operator_id": "lead_001", "reason": "manual_review"},
    )
    claim = client.post(
        "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot/claim",
        json={"operator_id": "op_001"},
    )
    reply = client.post(
        "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot/reply",
        json={"operator_id": "op_001", "content": "human reply"},
    )

    assert callback.status_code == 200
    assert force.status_code == 200
    assert claim.status_code == 200
    assert reply.status_code == 200
    assert len(sent) == 1
    assert sent[0]["w_id"] == "wid_test"
    assert sent[0]["wc_id"] == "wxid_sender"
    assert sent[0]["content"] == "human reply"
    assert sent[0]["source_batch_key"].startswith("workbench:")
    assert sent[0]["conversation_message_id"] > 0

    detail = client.get("/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot")
    messages = detail.json()["data"]["messages"]
    assert messages[-1]["sender_type"] == "human"
    assert messages[-1]["content"] == "human reply"
    assert messages[-1]["delivery_status"] == "queued"


def test_claimed_eyun_conversation_accepts_image_and_received_emoji(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        record_customer_message,
    )
    from app.integrations.eyun.services import message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    queued = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": len(queued), "status": "queued"}

    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue,
    )
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_customer",
            session_id="default",
            content="[表情]",
            message_id="emoji-provider-message",
            metadata={
                "provider": "eyun",
                "w_id": "wid-1",
                "from_user": "wxid_customer",
                "message_type": "60006",
                "raw_content": "<msg><emoji md5=\"abc123\" len=\"456\" /></msg>",
                "media": {
                    "type": "emoji",
                    "url": "https://cdn.example.com/emoji.gif",
                    "md5": "abc123",
                    "size": "456",
                    "fallback": False,
                },
            },
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:wxid_customer:default"
    assert client.post(
        f"/api/v1/admin/conversations/{conversation_id}/claim",
        json={"operator_id": "op-media"},
    ).status_code == 200

    emoji_items = client.get(
        f"/api/v1/admin/conversations/{conversation_id}/emojis"
    ).json()["data"]["items"]
    assert emoji_items[0]["md5"] == "abc123"

    image_response = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/reply-image",
        data={"operator_id": "op-media"},
        files={"file": ("reply.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
    )
    emoji_response = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/reply-emoji",
        json={
            "operator_id": "op-media",
            "source_message_id": emoji_items[0]["message_id"],
        },
    )

    assert image_response.status_code == 200
    assert emoji_response.status_code == 200
    assert queued[0]["message_type"] == "image"
    assert "/static/workbench-media/" in queued[0]["content"]
    assert queued[1]["message_type"] == "emoji"
    assert '"md5": "abc123"' in queued[1]["content"]


def test_claimed_eyun_conversation_sends_care_manual_link_card(
    monkeypatch, tmp_path
):
    import asyncio

    from app.domains.conversations.services.conversation_service import (
        record_customer_message,
    )
    from app.domains.sales.services import care_manual_service
    from app.integrations.eyun.services import message_risk_control_service

    _reset_settings(monkeypatch, tmp_path)
    queued = []

    async def fake_enqueue(**kwargs):
        queued.append(kwargs)
        return {"id": 1, "status": "queued"}

    monkeypatch.setattr(
        message_risk_control_service,
        "enqueue_wechat_outbound",
        fake_enqueue,
    )
    monkeypatch.setattr(
        care_manual_service,
        "get_care_manual",
        lambda card_id: {
            "id": card_id,
            "title": "【建兰玉白丹红】养护注意事项",
            "note_url": "https://h5.example.com/note/11",
            "cover_url": "https://img.example.com/11.jpg",
            "card_description": "玉白丹红养护手册",
            "available": True,
        },
    )
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_manual_customer",
            session_id="default",
            content="发我养护手册",
            metadata={
                "provider": "eyun",
                "w_id": "wid-1",
                "from_user": "wxid_manual_customer",
                "message_type": "60001",
            },
        )
    )
    client = TestClient(app)
    conversation_id = "wechat:wxid_manual_customer:default"
    assert client.post(
        f"/api/v1/admin/conversations/{conversation_id}/claim",
        json={"operator_id": "op-manual"},
    ).status_code == 200

    response = client.post(
        f"/api/v1/admin/conversations/{conversation_id}/reply-care-manual",
        json={"operator_id": "op-manual", "care_manual_id": 11},
    )

    assert response.status_code == 200
    assert queued[0]["message_type"] == "link_card"
    assert json.loads(queued[0]["content"]) == {
        "title": "【建兰玉白丹红】养护注意事项",
        "url": "https://h5.example.com/note/11",
        "description": "玉白丹红养护手册",
        "thumb_url": "https://img.example.com/11.jpg",
    }
    detail = client.get(
        f"/api/v1/admin/conversations/{conversation_id}"
    ).json()["data"]
    message = detail["messages"][-1]
    assert message["sender_type"] == "human"
    assert message["delivery_status"] == "queued"
    assert message["metadata"]["link_card"]["url"].endswith("/note/11")


@pytest.mark.parametrize("recognition_timeout", [False, True])
def test_queued_eyun_video_replaces_expired_media_url(
    monkeypatch, tmp_path, recognition_timeout
):
    import asyncio

    from app.integrations.eyun.services import eyun_callback_service
    from app.integrations.ai.services import video_understanding_service
    from app.integrations.ai.services.video_understanding_service import (
        VideoUnderstanding,
    )
    from app.integrations.eyun.services import message_risk_control_service
    from app.integrations.eyun.services import eyun_inbound_media_service
    from app.integrations.eyun.services.eyun_inbound_media_service import (
        process_due_eyun_media_jobs,
    )

    _reset_settings(monkeypatch, tmp_path)
    if recognition_timeout:
        monkeypatch.setattr(
            eyun_inbound_media_service, "MEDIA_RECOGNITION_TIMEOUT_SECONDS", 0.01
        )
    queued = []

    async def fake_contact_snapshot(**kwargs):
        return {}

    async def fake_download_eyun_video(**kwargs):
        assert kwargs["msg_id"] == "789"
        return "/static/media/playable.mp4"

    async def fake_understand(path):
        assert path.name == "playable.mp4"
        media = client.get(
            "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot"
        ).json()["data"]["messages"][0]["metadata"]["media"]
        assert media["url"] == "/static/media/playable.mp4"
        assert media["resolve_status"] == "succeeded"
        assert media["recognition"]["status"] == "processing"
        if recognition_timeout:
            await asyncio.Event().wait()
        return VideoUnderstanding(
            summary="视频展示兰花叶片上的黑色病斑。",
            visible_content=["兰花叶片", "黑色病斑"],
            frame_count=3,
            confidence=0.86,
        )

    async def fake_enqueue(payload):
        queued.append(payload)
        return {"batch_key": "wid_test:wxid_sender"}

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    monkeypatch.setattr(
        eyun_callback_service, "download_eyun_video", fake_download_eyun_video
    )
    monkeypatch.setattr(
        video_understanding_service, "understand_video_file", fake_understand
    )
    monkeypatch.setattr(
        message_risk_control_service, "enqueue_eyun_inbound", fake_enqueue
    )
    client = TestClient(app)
    client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60003",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "toUser": "wxid_bot",
                "content": '<msg><videomsg cdnvideourl="https://expired.example/video" /></msg>',
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )
    assert asyncio.run(process_due_eyun_media_jobs()) == 1

    detail = client.get(
        "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot"
    ).json()["data"]
    media = detail["messages"][0]["metadata"]["media"]
    assert media["url"] == "/static/media/playable.mp4"
    assert media["resolve_status"] == "succeeded"
    if recognition_timeout:
        assert media["recognition"]["status"] == "failed"
        assert media["recognition"]["error"] == "TimeoutError"
    else:
        assert media["recognition"]["status"] == "succeeded"
        assert media["recognition"]["frame_count"] == 3
    assert queued[0]["messageType"] == "60001"
    assert queued[0]["_eyun_original_message_type"] == "60003"
    if not recognition_timeout:
        assert "视频展示兰花叶片上的黑色病斑" in queued[0]["data"]["content"]
        message = detail["messages"][0]
        retry = client.post(
            f"/api/v1/admin/conversations/messages/{message['id']}/resolve-media"
        )
        assert retry.status_code == 200
        assert asyncio.run(process_due_eyun_media_jobs()) == 1
        reloaded = client.get(
            "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot"
        ).json()["data"]["messages"][0]["metadata"]["media"]
        assert reloaded["url"] == "/static/media/playable.mp4"
        assert reloaded["recognition"] == media["recognition"]
        assert len(queued) == 1


def test_stale_eyun_media_job_is_recovered_after_worker_restart(
    monkeypatch, tmp_path
):
    import asyncio

    from app.integrations.eyun.services import eyun_callback_service
    from app.infrastructure.database.models import EyunInboundMediaJobModel
    from app.integrations.eyun.services import eyun_inbound_media_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_contact_snapshot(**kwargs):
        return {}

    async def fake_download_eyun_video(**kwargs):
        return "/static/media/recovered.mp4"

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    monkeypatch.setattr(
        eyun_callback_service, "download_eyun_video", fake_download_eyun_video
    )
    client = TestClient(app)
    client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60003",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "toUser": "wxid_bot",
                "content": "<msg><videomsg /></msg>",
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )

    with eyun_inbound_media_service._get_session() as session:
        job = session.query(EyunInboundMediaJobModel).one()
        job.status = "processing"
        job.locked_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        session.commit()

    assert asyncio.run(eyun_inbound_media_service.process_due_eyun_media_jobs()) == 1
    detail = client.get(
        "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot"
    ).json()["data"]
    media = detail["messages"][0]["metadata"]["media"]
    assert media["url"] == "/static/media/recovered.mp4"
    assert media["resolve_status"] == "succeeded"
    assert media["recognition"]["status"] == "failed"
    assert detail["conversation"]["status"] == "ai_waiting"
    assert detail["conversation"]["handoff_reason"] is None


@pytest.mark.parametrize("download_timeout", [False, True])
def test_failed_eyun_video_resolution_is_persisted(monkeypatch, tmp_path, download_timeout):
    import asyncio

    from app.integrations.eyun.services import eyun_callback_service
    from app.integrations.eyun.services import eyun_inbound_media_service

    _reset_settings(monkeypatch, tmp_path)

    async def fake_contact_snapshot(**kwargs):
        return {}

    async def fail_download(**kwargs):
        if download_timeout:
            await asyncio.Event().wait()
        raise RuntimeError("provider download failed")

    monkeypatch.setattr(
        eyun_callback_service, "get_eyun_contact_snapshot", fake_contact_snapshot
    )
    monkeypatch.setattr(eyun_callback_service, "download_eyun_video", fail_download)
    monkeypatch.setattr(eyun_inbound_media_service, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(eyun_inbound_media_service, "MEDIA_DOWNLOAD_TIMEOUT_SECONDS", 0.01)
    client = TestClient(app)
    client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60003",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_sender",
                "toUser": "wxid_bot",
                "content": "<msg><videomsg /></msg>",
                "msgId": 789,
                "newMsgId": 790,
                "self": False,
            },
        },
    )
    detail_url = "/api/v1/admin/conversations/wechat:wxid_sender:wxid_bot"
    message_id = client.get(detail_url).json()["data"]["messages"][0]["id"]

    assert asyncio.run(eyun_inbound_media_service.process_due_eyun_media_jobs()) == 1
    media = client.get(detail_url).json()["data"]["messages"][0]["metadata"][
        "media"
    ]

    assert media["resolve_status"] == "failed"
    assert media["resolve_error"] == (
        "TimeoutError" if download_timeout else "provider download failed"
    )

    retry = client.post(
        f"/api/v1/admin/conversations/messages/{message_id}/resolve-media"
    )
    assert retry.status_code == 200
    assert retry.json()["data"]["metadata"]["media"]["resolve_status"] == "pending"


def test_non_resolvable_eyun_video_is_not_marked_pending(monkeypatch, tmp_path):
    import asyncio

    _reset_settings(monkeypatch, tmp_path)
    asyncio.run(
        record_customer_message(
            channel="wechat",
            user_id="wxid_customer",
            session_id="default",
            content="[video]",
            metadata={
                "provider": "eyun",
                "message_type": "other",
                "media": {
                    "type": "video",
                    "url": "https://cdn.example.com/video.mp4",
                },
            },
        )
    )

    client = TestClient(app)
    message = client.get(
        "/api/v1/admin/conversations/wechat:wxid_customer:default"
    ).json()["data"]["messages"][0]

    assert message["metadata"]["media"]["url"] == (
        "https://cdn.example.com/video.mp4"
    )
    assert "resolve_status" not in message["metadata"]["media"]


def test_message_panel_does_not_resolve_video_on_playback_error():
    panel = (
        Path(__file__).parents[2]
        / "admin"
        / "src"
        / "views"
        / "workbench"
        / "components"
        / "MessagePanel.vue"
    ).read_text(encoding="utf-8")

    assert '@error="resolveVideo(message)"' not in panel
    assert '@error="markVideoFailed(message)"' in panel
    assert "autoResolvePendingVideos" not in panel
    assert "canResolveMedia(message)" in panel
    assert "['60003', '60004']" in panel
    assert "status === 'pending' && !media?.job_key" in panel
    assert '@click="retryMedia(message)"' in panel


def test_mark_conversation_read_clears_unread_count(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "浣犲ソ",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    before = client.get("/api/v1/admin/conversations").json()["data"]["items"][0]
    response = client.post("/api/v1/admin/conversations/api:user_001:sess_001/read")
    after = client.get("/api/v1/admin/conversations").json()["data"]["items"][0]

    assert before["unread_count"] > 0
    assert response.status_code == 200
    assert response.json()["data"]["unread_count"] == 0
    assert after["unread_count"] == 0


def test_marking_an_already_read_conversation_does_not_publish_again(
    monkeypatch, tmp_path
):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "hello",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    event_queue = conversation_event_broker.subscribe()

    client.post("/api/v1/admin/conversations/api:user_001:sess_001/read")
    first_event = event_queue.get_nowait()
    client.post("/api/v1/admin/conversations/api:user_001:sess_001/read")

    assert first_event["reason"] == "read"
    assert event_queue.empty()
    conversation_event_broker.unsubscribe(event_queue)


def test_force_handoff_allows_operator_to_claim_ai_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "你好",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )

    force = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/force-handoff",
        json={"operator_id": "lead_001", "reason": "AI 可能误答"},
    )
    claim = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )

    assert force.status_code == 200
    assert force.json()["data"]["status"] == "handoff_pending"
    assert force.json()["data"]["handoff_reason"] == "AI 可能误答"
    assert claim.status_code == 200


def test_release_to_ai_and_resolve_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post(
        "/api/v1/chat",
        json={
            "channel": "api",
            "user_id": "user_001",
            "session_id": "sess_001",
            "message": "我要转人工",
            "kb_id": "kb_default",
            "metadata": {},
        },
    )
    client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/claim",
        json={"operator_id": "op_001"},
    )

    release = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/release-to-ai",
        json={"operator_id": "op_001"},
    )
    resolve = client.post(
        "/api/v1/admin/conversations/api:user_001:sess_001/resolve",
        json={"operator_id": "op_001", "reason": "已处理"},
    )

    assert release.status_code == 200
    assert release.json()["data"]["status"] == "ai_active"
    assert resolve.status_code == 200
    assert resolve.json()["data"]["status"] == "resolved"


def test_conversation_apis_require_authorization(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path, auth=True)
    client = TestClient(app)

    response = client.get("/api/v1/admin/conversations")

    assert response.status_code == 401
    assert response.json()["code"] == 40100


@pytest.mark.parametrize("cached_audio", [True, False])
@pytest.mark.parametrize("initial_failure", ["", "recognition", "download"])
def test_manual_voice_transcription_reuses_audio_and_never_replies(monkeypatch, tmp_path, cached_audio, initial_failure):
    import asyncio
    from app.domains.conversations.services import conversation_service as conversations
    from app.domains.conversations.services.message_action_service import quote_message
    from app.infrastructure.database.models import ConversationMessageModel, EyunInboundMediaJobModel
    from app.integrations.ai.services import speech_recognition_service as speech
    from app.integrations.eyun.services import eyun_callback_service as provider, eyun_inbound_media_service as jobs

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(provider, "media_storage_dir", lambda: tmp_path)
    monkeypatch.setattr(jobs, "MAX_ATTEMPTS", 1)
    audio = tmp_path / "voice.wav"
    if cached_audio:
        audio.write_bytes(b"audio")
    media = {"type": "audio", "url": "/static/media/voice.wav", "resolve_status": "succeeded",
             "recognition": {"status": "failed", "error": "previous failure"}}
    asyncio.run(record_customer_message(channel="wechat", user_id="voice_customer", session_id="owner", content="[语音]",
        metadata={"provider": "eyun", "message_type": "60004", "w_id": "wid", "from_user": "voice_customer", "media": media}))
    client = TestClient(app)
    detail_path = "/api/v1/admin/conversations/wechat:voice_customer:owner"
    message_id = client.get(detail_path).json()["data"]["messages"][0]["id"]
    action_path = f"/api/v1/admin/conversations/messages/{message_id}/transcribe"
    calls = {"download": 0, "recognize": 0, "reply": 0}
    fail = [initial_failure]

    async def download(**kwargs):
        calls["download"] += 1
        if fail[0] == "download":
            raise RuntimeError("download failed")
        audio.write_bytes(b"audio")
        return "/static/media/voice.wav"

    async def recognize(path):
        calls["recognize"] += 1
        assert path == audio and path.is_file()
        during = client.post(action_path).json()["data"]["metadata"]["media"]
        assert during["recognition"]["status"] == "processing"
        assert during["url"] == "/static/media/voice.wav"
        if fail[0] == "recognition":
            raise RuntimeError("recognition failed")
        return speech.SpeechTranscript(text="这盆兰花应该怎么浇水？")

    async def enqueue(*args):
        calls["reply"] += 1

    monkeypatch.setattr(provider, "download_eyun_voice", download)
    monkeypatch.setattr(speech, "transcribe_audio_file", recognize)
    monkeypatch.setattr(jobs, "_enqueue_media_agent_input", enqueue)
    response = client.post(action_path)
    assert response.status_code == 200
    pending = response.json()["data"]["metadata"]["media"]
    assert pending["recognition"]["status"] == "pending"
    if cached_audio:
        assert pending["url"] == media["url"] and pending["resolve_status"] == "succeeded"
    assert client.post(action_path).status_code == 200
    with jobs._get_session() as db:
        assert db.query(EyunInboundMediaJobModel).count() == 1
    assert asyncio.run(jobs.process_due_eyun_media_jobs()) == 1
    failed = initial_failure == "recognition" or (initial_failure == "download" and not cached_audio)
    if failed:
        result = client.get(detail_path).json()["data"]["messages"][0]
        assert result["metadata"]["media"]["recognition"]["status"] == "failed"
        fail[0] = ""
        assert client.post(action_path).status_code == 200
        assert asyncio.run(jobs.process_due_eyun_media_jobs()) == 1
    result = client.get(detail_path).json()["data"]["messages"][0]
    assert result["metadata"]["media"]["recognition"]["text"] == "这盆兰花应该怎么浇水？"
    assert result["metadata"]["media"]["resolve_status"] == "succeeded"
    assert result["content"] == "[语音]"
    assert client.post(action_path).status_code == 200
    assert asyncio.run(jobs.process_due_eyun_media_jobs()) == 0
    assert calls["reply"] == 0
    assert calls["recognize"] == (2 if initial_failure == "recognition" else 1)
    assert calls["download"] == (0 if cached_audio else 2 if initial_failure == "download" else 1)
    fail[0] = ""
    assert client.post(f"/api/v1/admin/conversations/messages/{message_id}/resolve-media").status_code == 200
    assert asyncio.run(jobs.process_due_eyun_media_jobs()) == 1
    repaired = client.get(detail_path).json()["data"]["messages"][0]
    assert repaired["metadata"]["media"]["recognition"] == result["metadata"]["media"]["recognition"]
    assert calls["reply"] == 0
    with conversations._get_session() as db:
        conversation = conversations._get_conversation_or_error(db, "wechat:voice_customer:owner")
        assert quote_message(db, conversation, message_id)["content"] == "这盆兰花应该怎么浇水？"
        row = db.get(ConversationMessageModel, message_id)
        metadata = json.loads(row.metadata_json)
        metadata["revoked_at"] = datetime.now(timezone.utc).isoformat()
        row.metadata_json = json.dumps(metadata)
        db.commit()
    assert client.post(action_path).status_code == 400


def test_voice_transcription_preserves_pending_automatic_job_and_rejects_video(monkeypatch, tmp_path):
    import asyncio
    from app.infrastructure.database.models import EyunInboundMediaJobModel
    from app.integrations.eyun.services import eyun_inbound_media_service as jobs

    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    for kind, message_type in (("audio", "60004"), ("audio", "80004"), ("video", "60003")):
        asyncio.run(record_customer_message(channel="wechat", user_id=message_type, session_id="owner", content="[媒体]", message_id=kind,
            metadata={"provider": "eyun", "message_type": message_type, "media": {} if message_type == "80004" else {"type": kind}}))
        cid = f"wechat:{message_type}:owner"
        job = asyncio.run(jobs.enqueue_eyun_inbound_media(conversation_id=cid, message_id=kind))
        message_id = client.get(f"/api/v1/admin/conversations/{cid}").json()["data"]["messages"][0]["id"]
        response = client.post(f"/api/v1/admin/conversations/messages/{message_id}/transcribe")
        assert response.status_code == (200 if kind == "audio" else 400)
        with jobs._get_session() as db:
            row = db.get(EyunInboundMediaJobModel, job["id"])
            assert row.status == "pending" and not json.loads(row.payload_json).get("transcribe_only")
    assert client.post("/api/v1/admin/conversations/messages/99999/transcribe").status_code == 404


def test_workbench_quote_preserves_source_and_rejects_other_customer(monkeypatch, tmp_path):
    import asyncio
    from app.integrations.eyun.services import message_risk_control_service as queue

    _reset_settings(monkeypatch, tmp_path)
    queued = []

    async def enqueue(**kwargs):
        queued.append(kwargs)

    monkeypatch.setattr(queue, "enqueue_wechat_outbound", enqueue)
    client = TestClient(app)
    ids = []
    for user in ("quote_customer", "other_customer"):
        asyncio.run(record_customer_message(
            channel="wechat", user_id=user, session_id="owner", content="需要少浇水吗？",
            metadata={"provider": "eyun", "w_id": "wid", "owner_wc_id": "owner", "from_user": user, "message_type": "60001"},
        ))
        ids.append(client.get(f"/api/v1/admin/conversations/wechat:{user}:owner").json()["data"]["messages"][0]["id"])
    path = "/api/v1/admin/conversations/wechat:quote_customer:owner"
    client.post(path + "/claim", json={"operator_id": "staff"})
    response = client.post(path + "/reply", json={"operator_id": "staff", "content": "是的", "source_message_id": ids[0]})
    assert response.status_code == 200
    assert queued[0]["content"] == "「客户：需要少浇水吗？」\n是的"
    message = client.get(path).json()["data"]["messages"][-1]
    assert message["content"] == "是的"
    assert message["metadata"]["quoted_message"]["id"] == ids[0]
    assert client.post(path + "/reply", json={"operator_id": "staff", "content": "不能引用", "source_message_id": ids[1]}).status_code == 403
    assert len(queued) == 1


@pytest.mark.parametrize("media_type, raw, expected_type", [
    ("file", '<msg><appmsg><type>6</type><title>手册.pdf</title></appmsg></msg>', "received_file"),
    ("link", '<msg><appmsg><type>5</type><title>手册</title><url>https://example.com</url></appmsg></msg>', "received_link"),
    ("mini_program", '<msg><appmsg><type>33</type><thumburl>https://example.com/cover.png</thumburl></appmsg></msg>', "received_mini_program"),
])
def test_workbench_forward_uses_queue_and_account_boundary(monkeypatch, tmp_path, media_type, raw, expected_type):
    import asyncio
    from app.integrations.eyun.services import message_risk_control_service as queue

    _reset_settings(monkeypatch, tmp_path)
    queued = []

    async def enqueue(**kwargs):
        queued.append(kwargs)

    monkeypatch.setattr(queue, "enqueue_wechat_outbound", enqueue)
    client = TestClient(app)
    for user, owner in (("source", "owner"), ("target", "owner"), ("foreign", "other_owner")):
        asyncio.run(record_customer_message(
            channel="wechat", user_id=user, session_id=owner, content="[消息]",
            metadata={"provider": "eyun", "w_id": "wid", "owner_wc_id": owner, "from_user": user,
                      "message_type": "60999", "raw_content": raw, "media": {"type": media_type}},
        ))
    source = client.get("/api/v1/admin/conversations/wechat:source:owner").json()["data"]["messages"][0]
    assert source["actions"]["forward"]
    path = "/api/v1/admin/conversations/wechat:target:owner"
    client.post(path + "/claim", json={"operator_id": "staff"})
    response = client.post(path + "/forward", json={"operator_id": "staff", "source_message_id": source["id"]})
    assert response.status_code == 200
    assert queued[0]["message_type"] == expected_type
    assert raw in queued[0]["content"] or json.loads(queued[0]["content"])["content"] == raw
    response = client.post("/api/v1/admin/conversations/wechat:foreign:other_owner/forward", json={"operator_id": "staff", "source_message_id": source["id"]})
    assert response.status_code == 409
    assert len(queued) == 1


def test_workbench_product_card_rechecks_catalog_and_limits_search_fields(monkeypatch, tmp_path):
    import asyncio
    from app.integrations.eyun.services import message_risk_control_service as queue
    from app.integrations.youzan.services import youzan_product_sync_service as catalog

    _reset_settings(monkeypatch, tmp_path)
    product = {"item_id": "123", "title": "建兰", "image_url": "https://example.com/cover.jpg",
               "h5_url": "https://example.com/product/123", "price_cent": 2980, "stock": 2, "internal_note": "内部备注"}
    items = [product]
    calls = []
    queued = []

    def list_products(**kwargs):
        calls.append(kwargs)
        return {"items": items, "total": len(items)}

    async def enqueue(**kwargs):
        queued.append(kwargs)

    monkeypatch.setattr(catalog, "list_products", list_products)
    monkeypatch.setattr(queue, "enqueue_wechat_outbound", enqueue)
    asyncio.run(record_customer_message(channel="wechat", user_id="buyer", session_id="owner", content="看看商品",
        metadata={"provider": "eyun", "w_id": "wid", "owner_wc_id": "owner", "from_user": "buyer"}))
    client = TestClient(app)
    result = client.get("/api/v1/admin/conversations/products?keyword=建兰").json()["data"]
    assert "internal_note" not in result["items"][0]
    assert calls[-1]["catalog_only"] is True
    path = "/api/v1/admin/conversations/wechat:buyer:owner"
    client.post(path + "/claim", json={"operator_id": "staff"})
    assert client.post(path + "/reply-product", json={"operator_id": "staff", "item_id": "123"}).status_code == 200
    card = json.loads(queued[0]["content"])
    assert queued[0]["message_type"] == "link_card"
    assert card["url"] == product["h5_url"]
    assert "29.80" in card["description"]
    items.clear()
    assert client.post(path + "/reply-product", json={"operator_id": "staff", "item_id": "123"}).status_code == 404
    assert len(queued) == 1


def test_workbench_revoke_retains_receipt_and_rejects_resend(monkeypatch, tmp_path):
    import asyncio
    from app.integrations.eyun.services import message_risk_control_service as queue, eyun_callback_service as provider
    from app.domains.conversations.services.conversation_service import update_outbound_message_delivery

    _reset_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(queue, "random_reply_delay_seconds", lambda: 0)
    receipt = {"msgId": 123, "newMsgId": 4792296942111367533, "createTime": int(datetime.now(timezone.utc).timestamp())}
    revoked = []

    async def send(**kwargs):
        return {"code": "1000", "data": receipt}

    async def revoke(payload):
        revoked.append(payload)
        if len(revoked) == 1:
            raise RuntimeError("too late")
        return {"code": "1000"}

    monkeypatch.setattr(provider, "send_eyun_text", send)
    monkeypatch.setattr(provider, "revoke_eyun_message", revoke)
    asyncio.run(record_customer_message(channel="wechat", user_id="revoke_customer", session_id="owner", content="你好",
        metadata={"provider": "eyun", "w_id": "wid", "owner_wc_id": "owner", "from_user": "revoke_customer"}))
    client = TestClient(app)
    path = "/api/v1/admin/conversations/wechat:revoke_customer:owner"
    client.post(path + "/claim", json={"operator_id": "staff"})
    assert client.post(path + "/reply", json={"operator_id": "staff", "content": "待撤回回复"}).status_code == 200
    assert asyncio.run(queue.process_due_eyun_outbound_messages()) == 1
    message = client.get(path).json()["data"]["messages"][-1]
    assert message["actions"]["revoke"] is True
    assert message["metadata"]["revoke_receipts"][0]["newMsgId"] == str(receipt["newMsgId"])
    revoke_path = f"/api/v1/admin/conversations/messages/{message['id']}/revoke"
    assert client.post(revoke_path, json={"operator_id": "staff"}).status_code == 502
    assert client.get(path).json()["data"]["messages"][-1]["delivery_status"] == "accepted"
    response = client.post(revoke_path, json={"operator_id": "staff"})
    assert response.status_code == 200
    assert response.json()["data"]["delivery_status"] == "revoked"
    assert revoked[-1]["wId"] == "wid"
    assert revoked[-1]["wcId"] == "revoke_customer"
    update_outbound_message_delivery(message["id"], status="confirmed")
    assert client.get(path).json()["data"]["messages"][-1]["delivery_status"] == "revoked"
    assert client.post(f"/api/v1/admin/conversations/messages/{message['id']}/retry-delivery").status_code == 409
    assert client.post(revoke_path, json={"operator_id": "staff"}).status_code == 200
    assert len(revoked) == 2
