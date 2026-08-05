from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.infrastructure.database.models import Base, EyunContactModel
from app.shared.schemas.common import AppError, ErrorCode


_OWNER_ACCOUNT_ID = "default"
_sessionmakers: dict[str, sessionmaker] = {}


async def query_eyun_friend_ids(w_id: str) -> list[str]:
    """Read the existing Eyun address-list endpoint used by this integration."""
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        raise AppError(
            ErrorCode.STATE_FAILED,
            message="Eyun 联系人同步配置不完整",
            status_code=503,
        )
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}/getAddressList",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json={"wId": w_id},
        )
    response.raise_for_status()
    body = response.json()
    if str(body.get("code")) != "1000":
        raise AppError(
            ErrorCode.STATE_FAILED,
            message=f"Eyun 获取通讯录失败：{body.get('message') or body.get('code')}",
            status_code=502,
        )
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    values = data.get("friends") if isinstance(data.get("friends"), list) else []
    return sorted({str(value).strip() for value in values if str(value).strip()})


async def sync_eyun_contacts(
    *, friend_ids: list[str] | None = None
) -> dict[str, Any]:
    settings = get_settings()
    w_id = settings.eyun_wid.strip()
    current_ids = set(
        friend_ids if friend_ids is not None else await query_eyun_friend_ids(w_id)
    )
    now = datetime.now(timezone.utc)
    new_ids: list[str] = []
    readded = 0
    removed = 0
    with _session() as session:
        rows = list(
            session.scalars(
                select(EyunContactModel).where(
                    EyunContactModel.tenant_id == "tenant_default",
                    EyunContactModel.owner_account_id == _OWNER_ACCOUNT_ID,
                )
            )
        )
        by_wc_id = {row.wc_id: row for row in rows}
        for wc_id in current_ids:
            row = by_wc_id.get(wc_id)
            if row is None:
                row = EyunContactModel(
                    tenant_id="tenant_default",
                    owner_account_id=_OWNER_ACCOUNT_ID,
                    wc_id=wc_id,
                    current_w_id=w_id or None,
                    first_seen_at=now,
                    last_seen_at=now,
                    status="active",
                    missing_count=0,
                    discovery_source="agent_contact_sync",
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                by_wc_id[wc_id] = row
                new_ids.append(wc_id)
                continue
            if row.status == "removed":
                readded += 1
            row.current_w_id = w_id or row.current_w_id
            row.status = "active"
            row.missing_count = 0
            row.last_seen_at = now
            row.updated_at = now
        threshold = max(1, settings.eyun_contact_missing_threshold)
        for row in rows:
            if row.wc_id in current_ids or row.status == "removed":
                continue
            row.missing_count += 1
            row.updated_at = now
            if row.missing_count >= threshold:
                row.status = "removed"
                row.current_w_id = None
                removed += 1
        session.commit()
    await _refresh_contact_details(sorted(current_ids), w_id)
    return {
        "total": len(current_ids),
        "new": len(new_ids),
        "readded": readded,
        "removed": removed,
        "synced_at": now.isoformat(),
    }


async def _refresh_contact_details(wc_ids: list[str], w_id: str) -> None:
    if not wc_ids or not w_id:
        return
    from app.domains.customers.services.user_profile_service import ensure_user_profile
    from app.integrations.eyun.services.eyun_contact_service import (
        get_eyun_contact_snapshots,
    )

    snapshots = await get_eyun_contact_snapshots(w_id=w_id, wc_ids=wc_ids, force=True)
    for wc_id in wc_ids:
        snapshot = snapshots.get(wc_id, {})
        if not snapshot:
            continue
        await ensure_user_profile(
            wc_id,
            tenant_id="tenant_default",
            channel="wechat",
            basic_info=snapshot,
        )
        with _session() as session:
            row = session.scalar(
                select(EyunContactModel).where(EyunContactModel.wc_id == wc_id)
            )
            if row is None:
                continue
            row.display_name = str(
                snapshot.get("remark_name")
                or snapshot.get("nickname")
                or snapshot.get("display_name")
                or row.display_name
                or ""
            ) or None
            row.remark_name = str(snapshot.get("remark_name") or row.remark_name or "") or None
            row.wechat_id = str(snapshot.get("alias_name") or row.wechat_id or "") or None
            row.avatar_url = str(snapshot.get("avatar_url") or row.avatar_url or "") or None
            row.updated_at = datetime.now(timezone.utc)
            session.commit()


def _session():
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[EyunContactModel.__table__])
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()
