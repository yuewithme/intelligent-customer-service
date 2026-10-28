import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.domains.conversations.services.conversation_service import _get_session, list_conversations
from app.domains.customers.services.user_profile_service import _get_session as profile_session
from app.infrastructure.database.models import (
    ConversationMessageModel,
    ConversationModel,
    EyunContactModel,
    UserProfileModel,
)


@pytest.fixture(autouse=True)
def search_db(monkeypatch, tmp_path):
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{tmp_path / 'chat.db'}")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'profiles.db'}")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("EYUN_WC_ID", "shop_a")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def conversation(user, *, tenant="shop_a", channel="wechat", display="", pinned=False, hidden=False, status="ai_waiting", session_id="default"):
    now = datetime.now(timezone.utc)
    cid = f"{channel}:{user}:{tenant}:{session_id}"
    with _get_session() as session:
        session.add(ConversationModel(
            conversation_id=cid, channel=channel, user_id=user, tenant_id=tenant,
            owner_wc_id=tenant if channel == "wechat" else None,
            session_id=session_id, user_display_name=display, last_message="最新一条无关消息",
            is_pinned=pinned, hidden_at=now if hidden else None, status=status,
            created_at=now, updated_at=now,
        ))
        session.commit()
    return cid


def message(cid, content="", *, metadata=None, delivery_status=None, days_ago=0, sender="customer"):
    with _get_session() as session:
        row = ConversationMessageModel(
            conversation_id=cid, content=content, sender_type=sender,
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
            delivery_status=delivery_status,
            created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
        session.add(row)
        session.commit()
        return row.id


def profile(user, *, tenant="shop_a", **basic):
    with profile_session() as session:
        now = datetime.now(timezone.utc)
        session.add(UserProfileModel(
            user_id=user, tenant_id=tenant, channel="wechat",
            basic_info_json=json.dumps(basic, ensure_ascii=False), created_at=now, updated_at=now,
        ))
        session.commit()


@pytest.mark.asyncio
async def test_identity_and_complete_history_have_weighted_relevance():
    exact = conversation("wxid_exact")
    profile("wxid_exact", alias_name="orchid", nickname="小兰", remark_name="张老师")
    remark = conversation("remark")
    profile("remark", remark_name="orchid")
    nickname = conversation("nickname")
    profile("nickname", nickname="orchid")
    partial = conversation("partial", display="老orchid")
    history = conversation("history", pinned=True)
    old_id = message(history, "以前问过 orchid 的养护方法", days_ago=365)
    for _ in range(65):
        message(history, "无关的新消息")

    found = await list_conversations(keyword="orchid")

    assert [row["conversation_id"] for row in found["items"]] == [exact, remark, nickname, partial, history]
    assert found["items"][-1]["search_matches"][0]["message_id"] == old_id
    assert "以前问过" in found["items"][-1]["search_matches"][0]["text"]
    for keyword, label in (("张老师", "备注"), ("小兰", "昵称"), ("wxid_exact", "微信标识")):
        result = await list_conversations(keyword=keyword)
        assert result["items"][0]["conversation_id"] == exact
        assert result["items"][0]["search_matches"][0]["label"] == label


@pytest.mark.asyncio
async def test_forward_targets_filter_before_paging_and_use_recent_order():
    pinned = conversation("older_pinned", pinned=True)
    recent = conversation("recent")
    conversation("resolved", status="resolved")
    conversation("other_account", tenant="shop_b")
    conversation("hidden", hidden=True)
    now = datetime.now(timezone.utc)
    with _get_session() as session:
        session.query(ConversationModel).filter(ConversationModel.conversation_id == pinned).update({"updated_at": now - timedelta(days=1)})
        session.query(ConversationModel).filter(ConversationModel.conversation_id == recent).update({"updated_at": now - timedelta(hours=1)})
        session.commit()

    scope = {"for_forwarding": True, "owner_wc_id": "shop_a", "page_size": 1}
    first = await list_conversations(**scope)
    second = await list_conversations(**scope, page=2)
    assert first["total"] == second["total"] == 2
    assert first["items"][0]["conversation_id"] == recent
    assert second["items"][0]["conversation_id"] == pinned
    assert (await list_conversations(**scope, allowed_owner_wc_ids=["shop_b"]))["total"] == 0
    assert (await list_conversations(owner_wc_id="shop_a"))["items"][0]["conversation_id"] == pinned

    from fastapi.testclient import TestClient
    from app.main import app

    response = TestClient(app).get("/api/v1/admin/conversations", params={
        "for_forwarding": "true", "owner_wc_id": "shop_a", "page_size": 1,
    })
    assert response.status_code == 200
    assert response.json()["data"]["total"] == 2
    assert response.json()["data"]["items"][0]["conversation_id"] == recent


@pytest.mark.asyncio
async def test_forward_targets_reuse_identity_and_full_history_search():
    identity = conversation("wxid_identity")
    profile("wxid_identity", alias_name="orchid", nickname="山间兰友", remark_name="张老师")
    history = conversation("history", pinned=True)
    old_id = message(history, "orchid 的烂根处理方法", days_ago=365)
    for _ in range(55):
        message(history, "无关的新消息")
    message(conversation("resolved", status="resolved"), "orchid 烂根")
    message(conversation("other_account", tenant="shop_b"), "orchid 烂根")

    scope = {"for_forwarding": True, "owner_wc_id": "shop_a"}
    found = await list_conversations(keyword="orchid", **scope)
    assert [row["conversation_id"] for row in found["items"]] == [identity, history]
    assert found["items"][-1]["search_matches"][0]["message_id"] == old_id
    for query, label in (("张老师", "备注"), ("山间兰友", "昵称"), ("wxid_identity", "微信标识")):
        found = await list_conversations(keyword=query, **scope)
        assert found["items"][0]["conversation_id"] == identity
        assert found["items"][0]["search_matches"][0]["label"] == label
    combined = await list_conversations(keyword="orchid 烂根", **scope)
    assert [row["conversation_id"] for row in combined["items"]] == [history, identity]


@pytest.mark.asyncio
async def test_combined_keywords_and_duplicate_messages_do_not_swamp_identity():
    combined = conversation("combined")
    profile("combined", remark_name="张老师")
    message(combined, "兰花烂根怎么办")
    identity = conversation("identity")
    profile("identity", remark_name="张老师")
    repeated = conversation("repeated")
    for _ in range(20):
        message(repeated, "张老师")

    found = await list_conversations(keyword="  张老师   烂根  ")
    assert found["items"][0]["conversation_id"] == combined
    assert {match["field"] for match in found["items"][0]["search_matches"]} == {"remark", "message"}
    single = await list_conversations(keyword="张老师")
    ids = [row["conversation_id"] for row in single["items"]]
    assert ids.index(identity) < ids.index(repeated)
    repeated_score = single["items"][-1]["search_score"]
    message(repeated, "张老师")
    assert (await list_conversations(keyword="张老师"))["items"][-1]["search_score"] == repeated_score


@pytest.mark.asyncio
async def test_identity_snapshots_and_contact_fields_remain_searchable():
    snapshot = conversation("snapshot", display="客户备注")
    message(snapshot, "你好", metadata={"remark_name": "客户备注", "nickname": "山间兰友", "alias_name": "OrchidFriend"})
    message(snapshot, "新消息没有身份信息")
    contact = conversation("contact")
    with profile_session() as session:
        EyunContactModel.__table__.create(session.get_bind(), checkfirst=True)
        now = datetime.now(timezone.utc)
        session.add(EyunContactModel(
            tenant_id="tenant_default", owner_account_id="default", wc_id="contact",
            remark_name="南宁李姐", wechat_id="nn_orchid", first_seen_at=now,
            last_seen_at=now, created_at=now, updated_at=now,
        ))
        session.commit()
    for query, cid in (("山间兰友", snapshot), ("orchidfriend", snapshot), ("南宁李姐", contact), ("nn_orchid", contact)):
        found = await list_conversations(keyword=query)
        assert [row["conversation_id"] for row in found["items"]] == [cid]


@pytest.mark.asyncio
async def test_scope_filters_apply_before_identity_and_history_search():
    allowed = conversation("shared", tenant="shop_a")
    other = conversation("shared", tenant="shop_b")
    message(allowed, "公共话题")
    message(other, "另一个账号的独有内容")
    profile("shared", tenant="shop_b", remark_name="另一个账号的备注")
    for user, kwargs in (("hidden", {"hidden": True}), ("resolved", {"status": "resolved"}), ("api", {"channel": "api"})):
        message(conversation(user, **kwargs), "公共话题")

    scope = {"allowed_owner_wc_ids": ["shop_a"], "wechat_only": True, "status": "ai_waiting"}
    assert (await list_conversations(keyword="独有内容", **scope))["total"] == 0
    assert (await list_conversations(keyword="另一个账号的备注", **scope))["total"] == 0
    assert [row["conversation_id"] for row in (await list_conversations(keyword="公共话题", **scope))["items"]] == [allowed]
    assert (await list_conversations(keyword="公共话题", allowed_owner_wc_ids=[]))["total"] == 0
    assert (await list_conversations(keyword="独有内容", tenant_id="shop_a"))["total"] == 0


@pytest.mark.asyncio
async def test_ambiguous_global_profile_and_other_account_contacts_cannot_leak():
    allowed = conversation("shared", tenant="shop_a")
    other = conversation("shared", tenant="shop_b")
    profile("shared", tenant="shop_a", remark_name="全局画像后来被其他账号更新")
    message(allowed, "你好", metadata={"nickname": "甲账号兰友"})
    message(other, "你好", metadata={"nickname": "乙账号兰友"})
    with profile_session() as session:
        EyunContactModel.__table__.create(session.get_bind(), checkfirst=True)
        now = datetime.now(timezone.utc)
        session.add(EyunContactModel(
            tenant_id="shop_b", owner_account_id="shop_b", wc_id="shared",
            remark_name="乙账号联系人备注", first_seen_at=now,
            last_seen_at=now, created_at=now, updated_at=now,
        ))
        session.commit()
    scope = {"allowed_owner_wc_ids": ["shop_a"]}
    for keyword in ("全局画像", "乙账号兰友", "乙账号联系人备注"):
        assert (await list_conversations(keyword=keyword, **scope))["total"] == 0
    assert (await list_conversations(keyword="甲账号兰友", **scope))["total"] == 1


def test_search_api_preserves_employee_scope_and_returns_match_details(monkeypatch):
    from fastapi.testclient import TestClient
    from app.domains.access import platform
    from app.domains.access.api import admin_gate
    from app.main import app

    allowed = conversation("allowed")
    other = conversation("denied", tenant="shop_b")
    old_message = message(allowed, "兰花烂根怎么办", days_ago=90)
    message(other, "兰花烂根怎么办")
    profile("allowed", remark_name="张老师", alias_name="orchid_a")
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("PLATFORM_SHARED_SECRET", "test-shared-" + "x" * 32)
    get_settings.cache_clear()
    account = {
        "id": "search-employee", "username": "search-employee", "display_name": "客服",
        "enabled": True, "role": "employee", "pages": ["/workbench"],
        "all_wechat": False, "wechat_ids": ["shop_a"],
    }

    async def remote(*args, **kwargs):
        return {"account": account}

    monkeypatch.setattr(platform, "platform_request", remote)
    monkeypatch.setattr(admin_gate, "platform_request", remote)
    client = TestClient(app, base_url="https://sales-agent.hzwohu.com")
    client.cookies.set(platform.COOKIE_NAME, "t" * 43, domain="sales-agent.hzwohu.com", path="/")
    response = client.get("/api/v1/admin/conversations", params={"keyword": "张老师 烂根"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["conversation_id"] == allowed
    assert next(match for match in data["items"][0]["search_matches"] if match["field"] == "message")["message_id"] == old_message
    assert client.get("/api/v1/admin/conversations", params={"keyword": "兰花", "tenant_id": "shop_b"}).json()["data"]["total"] == 0
    account["wechat_ids"] = []
    assert client.get("/api/v1/admin/conversations", params={"keyword": "orchid_a"}).json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_search_respects_test_and_channel_filters():
    normal = conversation("normal")
    test = conversation("demo:test")
    message(normal, "排查兰花")
    message(test, "排查兰花", metadata={"evaluation_id": "case"})
    assert [row["conversation_id"] for row in (await list_conversations(keyword="兰花", test_data=False))["items"]] == [normal]
    assert [row["conversation_id"] for row in (await list_conversations(keyword="兰花", test_data=True))["items"]] == [test]
    assert (await list_conversations(keyword="兰花", channels=("api",)))["total"] == 0


@pytest.mark.asyncio
async def test_search_uses_visible_media_text_and_excludes_revoked_and_internal_metadata():
    cid = conversation("media")
    message(cid, "撤回的秘密", delivery_status="revoked")
    message(cid, "元数据撤回", metadata={"revoked_at": "2026-01-01"})
    message(cid, "普通消息", metadata={"internal_key": "不可检索的内部词"})
    audio = message(cid, "[语音]", metadata={"media": {"type": "audio", "recognition": {"status": "succeeded", "transcript": "叶子发黄怎么办"}}})
    message(cid, "[图片]", metadata={"media": {"type": "image", "recognition": {"status": "failed", "text": "失败识别内容"}}})
    card = message(cid, "[链接]", sender="human", delivery_status="sent", metadata={"link_card": {"title": "兰花养护指南", "description": "浇水方法"}})
    for keyword in ("秘密", "元数据撤回", "不可检索的内部词", "失败识别内容"):
        assert (await list_conversations(keyword=keyword))["total"] == 0
    for keyword, expected in (("叶子发黄", audio), ("浇水方法", card)):
        result = await list_conversations(keyword=keyword)
        assert result["items"][0]["search_matches"][0]["message_id"] == expected


@pytest.mark.asyncio
async def test_literal_wildcards_and_pagination_preserve_rank():
    a = conversation("first")
    b = conversation("second")
    message(a, "literal 100% _tag")
    message(b, "literal 1000 tag")
    assert (await list_conversations(keyword="%"))["total"] == 1
    assert (await list_conversations(keyword="_"))["total"] == 1
    all_items = (await list_conversations(keyword="literal"))["items"]
    first = await list_conversations(keyword="literal", page=1, page_size=1)
    second = await list_conversations(keyword="literal", page=2, page_size=1)
    assert first["total"] == second["total"] == 2
    assert first["items"] + second["items"] == all_items
    assert (await list_conversations(keyword="literal", page=3, page_size=1))["items"] == []
    with _get_session() as session:
        session.query(ConversationModel).filter(ConversationModel.conversation_id == a).update({"is_pinned": True})
        session.commit()
    default = await list_conversations(keyword="   ")
    assert default["items"][0]["conversation_id"] == a
    assert "search_score" not in default["items"][0]
