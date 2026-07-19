from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from sqlalchemy import create_engine, inspect, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import (
    Base,
    YouzanEventReceiptModel,
    YouzanIdentityBindingModel,
    YouzanToolCallAuditModel,
)
from app.services.youzan_order_service import YouzanCustomerIdentity


_sessionmakers: dict[str, sessionmaker] = {}


class YouzanIdentityStore:
    def get(
        self,
        *,
        tenant_id: str,
        channel: str,
        external_user_id: str,
        kdt_id: str,
    ) -> YouzanCustomerIdentity | None:
        with _get_session() as session:
            model = session.scalar(
                select(YouzanIdentityBindingModel).where(
                    YouzanIdentityBindingModel.tenant_id == tenant_id,
                    YouzanIdentityBindingModel.channel == channel,
                    YouzanIdentityBindingModel.external_user_id == external_user_id,
                    YouzanIdentityBindingModel.kdt_id == kdt_id,
                )
            )
            return _to_identity(model) if model is not None else None

    def upsert(
        self,
        *,
        tenant_id: str,
        channel: str,
        external_user_id: str,
        kdt_id: str,
        identity: YouzanCustomerIdentity | dict[str, Any],
        source: str,
    ) -> YouzanCustomerIdentity:
        if not isinstance(identity, YouzanCustomerIdentity):
            identity = YouzanCustomerIdentity.model_validate(identity)
        now = _now()
        with _get_session() as session:
            model = session.scalar(
                select(YouzanIdentityBindingModel).where(
                    YouzanIdentityBindingModel.tenant_id == tenant_id,
                    YouzanIdentityBindingModel.channel == channel,
                    YouzanIdentityBindingModel.external_user_id == external_user_id,
                    YouzanIdentityBindingModel.kdt_id == kdt_id,
                )
            )
            if model is None:
                model = YouzanIdentityBindingModel(
                    tenant_id=tenant_id,
                    channel=channel,
                    external_user_id=external_user_id,
                    kdt_id=kdt_id,
                    source=source,
                    verified_at=now,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)
            for field in YouzanCustomerIdentity.model_fields:
                value = getattr(identity, field)
                if value:
                    setattr(model, field, value)
            model.source = source
            model.verified_at = now
            model.last_seen_at = now
            model.updated_at = now
            session.commit()
            session.refresh(model)
            return _to_identity(model)

    def record_event(
        self,
        *,
        msg_id: str,
        kdt_id: str,
        event_type: str,
        event_status: str,
        payload_digest: str,
    ) -> bool:
        with _get_session() as session:
            if session.get(YouzanEventReceiptModel, msg_id) is not None:
                return False
            session.add(
                YouzanEventReceiptModel(
                    msg_id=msg_id,
                    kdt_id=kdt_id,
                    event_type=event_type,
                    event_status=event_status or None,
                    payload_digest=payload_digest,
                    processed_at=_now(),
                )
            )
            session.commit()
            return True

    def refresh_matching(
        self,
        identity: YouzanCustomerIdentity,
        *,
        kdt_id: str,
    ) -> int:
        conditions = []
        for field in (
            "yz_uid",
            "buyer_id",
            "yz_open_id",
            "fans_id",
            "weixin_openid",
            "union_id",
            "mobile",
        ):
            value = getattr(identity, field)
            if value:
                conditions.append(getattr(YouzanIdentityBindingModel, field) == value)
        if not conditions:
            return 0
        now = _now()
        with _get_session() as session:
            models = list(
                session.scalars(
                    select(YouzanIdentityBindingModel).where(
                        YouzanIdentityBindingModel.kdt_id == kdt_id,
                        or_(*conditions),
                    )
                )
            )
            for model in models:
                for field in YouzanCustomerIdentity.model_fields:
                    value = getattr(identity, field)
                    if value:
                        setattr(model, field, value)
                model.last_seen_at = now
                model.updated_at = now
            session.commit()
            return len(models)

    def record_tool_call(
        self,
        *,
        trace_id: str,
        tool_name: str,
        caller: str,
        customer_id: str | None,
        parameters: dict[str, Any],
        result_count: int,
        status: str,
        error_code: str | None,
        latency_ms: int,
    ) -> None:
        with _get_session() as session:
            session.add(
                YouzanToolCallAuditModel(
                    trace_id=trace_id,
                    tool_name=tool_name,
                    caller=caller,
                    customer_id=customer_id,
                    parameters_json=json.dumps(
                        parameters,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    result_count=result_count,
                    status=status,
                    error_code=error_code,
                    latency_ms=latency_ms,
                    created_at=_now(),
                )
            )
            session.commit()


def _get_session() -> Session:
    settings = get_settings()
    if settings.chat_log_provider != "sqlite":
        raise RuntimeError(f"unsupported chat log provider: {settings.chat_log_provider}")
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[
                YouzanIdentityBindingModel.__table__,
                YouzanEventReceiptModel.__table__,
                YouzanToolCallAuditModel.__table__,
            ],
        )
        _ensure_identity_schema(engine)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()


def _ensure_identity_schema(engine) -> None:
    columns = {
        column["name"]
        for column in inspect(engine).get_columns(
            YouzanIdentityBindingModel.__tablename__
        )
    }
    if "mobile" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE youzan_identity_bindings "
                    "ADD COLUMN mobile VARCHAR(32)"
                )
            )


def _to_identity(model: YouzanIdentityBindingModel) -> YouzanCustomerIdentity:
    return YouzanCustomerIdentity(
        **{
            field: str(getattr(model, field) or "")
            for field in YouzanCustomerIdentity.model_fields
        }
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)
