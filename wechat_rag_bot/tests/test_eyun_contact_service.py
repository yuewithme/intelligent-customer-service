from app.services.eyun_contact_service import parse_contact_snapshot


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
