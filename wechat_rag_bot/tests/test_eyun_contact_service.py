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


def test_contact_snapshot_falls_back_to_nickname_and_small_avatar():
    snapshot = parse_contact_snapshot(
        {
            "code": "1000",
            "data": [
                {
                    "remark": "",
                    "nickName": "贵杰",
                    "bigHead": "",
                    "smallHead": "https://example.com/avatar-small.jpg",
                }
            ],
        }
    )

    assert snapshot == {
        "nickname": "贵杰",
        "avatar_url": "https://example.com/avatar-small.jpg",
    }


def test_contact_snapshot_labels_openim_contact_when_provider_name_is_empty():
    snapshot = parse_contact_snapshot(
        {
            "code": "1000",
            "data": [
                {
                    "userName": "25984982682373090@openim",
                    "remark": None,
                    "nickName": "",
                    "bigHead": None,
                }
            ],
        }
    )

    assert snapshot == {"display_name": "企业微信用户（73090）"}
