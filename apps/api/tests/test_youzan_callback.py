import hashlib
import json
from urllib.parse import quote

import pytest


class FakeIdentityStore:
    def __init__(self):
        self.events = []
        self.identities = []

    def record_event(self, **event):
        duplicate = any(item["msg_id"] == event["msg_id"] for item in self.events)
        if not duplicate:
            self.events.append(event)
        return not duplicate

    def refresh_matching(self, identity, *, kdt_id):
        assert kdt_id == "9001"
        self.identities.append(identity)
        return 1


def _payload(*, secret: str = "client-secret"):
    msg = quote(
        json.dumps(
            {
                "data": json.dumps(
                    {
                        "yz_open_id": "yz-open-1",
                        "fans_id": 123,
                        "mobile": "13800138000",
                    }
                )
            },
            ensure_ascii=False,
        )
    )
    client_id = "client-id"
    sign = hashlib.md5(  # noqa: S324 - provider protocol requires MD5
        f"{client_id}{msg}{secret}".encode()
    ).hexdigest()
    return {
        "client_id": client_id,
        "kdt_id": 9001,
        "msg_id": "message-1",
        "type": "CUSTOMER_CHANGE",
        "status": "UPDATE",
        "msg": msg,
        "sign": sign,
    }


def test_youzan_callback_verifies_decodes_and_deduplicates_message():
    from app.integrations.youzan.services.youzan_callback_service import process_youzan_callback

    store = FakeIdentityStore()
    first = process_youzan_callback(
        _payload(),
        client_id="client-id",
        client_secret="client-secret",
        expected_kdt_id="9001",
        store=store,
    )
    second = process_youzan_callback(
        _payload(),
        client_id="client-id",
        client_secret="client-secret",
        expected_kdt_id="9001",
        store=store,
    )

    assert first["data"] == {"duplicate": False, "bindings_refreshed": 1}
    assert second["data"] == {"duplicate": True, "bindings_refreshed": 0}
    assert store.identities[0].yz_open_id == "yz-open-1"
    assert store.identities[0].fans_id == "123"
    assert store.identities[0].mobile == "13800138000"
    assert store.identities[0].mobile_masked == "138****8000"
    assert len(store.events) == 1


def test_youzan_callback_rejects_invalid_signature():
    from app.integrations.youzan.services.youzan_callback_service import (
        YouzanCallbackError,
        process_youzan_callback,
    )

    with pytest.raises(YouzanCallbackError):
        process_youzan_callback(
            _payload(secret="wrong-secret"),
            client_id="client-id",
            client_secret="client-secret",
            expected_kdt_id="9001",
            store=FakeIdentityStore(),
        )
