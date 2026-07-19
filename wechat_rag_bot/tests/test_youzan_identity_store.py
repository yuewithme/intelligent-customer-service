def test_youzan_identity_store_persists_full_and_masked_mobile(monkeypatch, tmp_path):
    from app.config import get_settings
    from app.services import youzan_identity_store as service
    from app.services.youzan_order_service import YouzanCustomerIdentity

    db_path = tmp_path / "youzan-identity.db"
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    get_settings.cache_clear()
    service._sessionmakers.clear()
    try:
        store = service.YouzanIdentityStore()
        store.upsert(
            tenant_id="tenant_default",
            channel="wechat",
            external_user_id="wxid-customer",
            kdt_id="9001",
            identity=YouzanCustomerIdentity(
                yz_uid="6190904",
                buyer_id="6190904",
                mobile="13800138000",
                mobile_masked="138****8000",
            ),
            source="mobile_verified",
        )

        loaded = store.get(
            tenant_id="tenant_default",
            channel="wechat",
            external_user_id="wxid-customer",
            kdt_id="9001",
        )
        assert loaded is not None
        assert loaded.buyer_id == "6190904"
        assert loaded.mobile == "13800138000"
        assert loaded.mobile_masked == "138****8000"
        assert "13800138000" in db_path.read_bytes().decode("latin1")

        refreshed = store.refresh_matching(
            YouzanCustomerIdentity(
                mobile="13800138000",
                union_id="union-from-callback",
            ),
            kdt_id="9001",
        )
        loaded = store.get(
            tenant_id="tenant_default",
            channel="wechat",
            external_user_id="wxid-customer",
            kdt_id="9001",
        )
        assert refreshed == 1
        assert loaded is not None
        assert loaded.union_id == "union-from-callback"
    finally:
        service._sessionmakers.clear()
        get_settings.cache_clear()
