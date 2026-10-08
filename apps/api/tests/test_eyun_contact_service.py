from app.integrations.eyun.services.eyun_contact_service import (
    parse_contact_snapshot,
    parse_contact_snapshots,
)


def test_contact_snapshot_prefers_remark_and_large_avatar():
    snapshot = parse_contact_snapshot(
        {
            "code": "1000",
            "data": [
                {
                    "userName": "wxid_customer",
                    "remark": "兰友张姐",
                    "nickName": "张女士",
                    "bigHead": "https://example.com/avatar-large.jpg",
                    "smallHead": "https://example.com/avatar-small.jpg",
                }
            ],
        }
    )

    assert snapshot == {
        "remark_name": "兰友张姐",
        "nickname": "张女士",
        "avatar_url": "https://example.com/avatar-large.jpg",
    }


def test_contact_snapshot_parses_official_alias_and_label_list_fields():
    snapshot = parse_contact_snapshot(
        {
            "code": "1000",
            "data": [
                {
                    "userName": "wxid_customer",
                    "aliasName": "orchid_friend",
                    "labelList": "12,18",
                }
            ],
        }
    )

    assert snapshot == {
        "alias_name": "orchid_friend",
        "label_ids": ["12", "18"],
    }


def test_contact_snapshot_ignores_failed_provider_response():
    assert parse_contact_snapshot({"code": "1001", "data": []}) == {}


def test_contact_snapshots_are_mapped_by_official_user_name():
    snapshots = parse_contact_snapshots(
        {
            "code": "1000",
            "data": [
                {"userName": "wxid_a", "remark": "备注甲"},
                {"userName": "wxid_b", "remark": "备注乙"},
            ],
        }
    )

    assert snapshots["wxid_a"]["remark_name"] == "备注甲"
    assert snapshots["wxid_b"]["remark_name"] == "备注乙"


def test_group_member_lookup_and_mention_send_use_official_fields(monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    import httpx
    from app.integrations.eyun.services import eyun_callback_service, eyun_contact_service

    settings = SimpleNamespace(eyun_base_url="https://eyun.example", eyun_authorization="test")
    for service in (eyun_callback_service, eyun_contact_service):
        monkeypatch.setattr(service, "get_settings", lambda: settings)
    requests = []

    def respond(request):
        requests.append((request.url.path, json.loads(request.content)))
        if request.url.path == "/getChatRoomMemberInfo":
            return httpx.Response(200, json={
                "code": "1000", "data": [
                    {"userName": "other", "nickName": "其他人"},
                    {"userName": "sender", "nickName": "张姐", "remark": "私聊备注"},
                ],
            })
        return httpx.Response(200, json={"code": "1000", "data": {"newMsgId": 123}})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(respond), **kwargs
    ))
    name = asyncio.run(eyun_contact_service.get_eyun_group_member_name(
        w_id="wid", group_id="group@chatroom", member_wc_id="sender"
    ))
    assert name == "张姐"
    asyncio.run(eyun_callback_service.send_eyun_text(
        w_id="wid", wc_id="group@chatroom", content=f"@{name}\u2005你好", at="sender"
    ))
    asyncio.run(eyun_callback_service.send_eyun_text(
        w_id="wid", wc_id="friend", content="你好"
    ))
    assert requests == [
        ("/getChatRoomMemberInfo", {"wId": "wid", "chatRoomId": "group@chatroom", "userList": "sender"}),
        ("/sendText", {"wId": "wid", "wcId": "group@chatroom", "content": "@张姐\u2005你好", "at": "sender"}),
        ("/sendText", {"wId": "wid", "wcId": "friend", "content": "你好"}),
    ]
