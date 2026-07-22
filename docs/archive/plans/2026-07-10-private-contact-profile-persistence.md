# Private Contact Profile Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every Eyun private-chat contact immediately owns one durable database profile, with stable external identity mapping, conversation memory, refreshable contact information, and reliable asynchronous profile enrichment.

**Architecture:** Preserve `user_profiles` as the business-profile aggregate and introduce `user_identities` as the boundary between provider identifiers and the internal `user_id`. Resolve the contact before recording any private message so conversations, memories, events, and profile updates all use the same canonical internal ID. Deterministic fields update on every turn; LLM enrichment is coalesced into a database-backed refresh job so process restarts do not lose profile work.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2, SQLite initially, pytest, Vue 3 admin workbench, Eyun `/getContact` API.

---

## Product decision

The feature is approved with this identity rule:

```text
(tenant_id, provider, owner_external_id, external_user_id)
                           -> one internal profile_user_id
```

- `provider` is `eyun` for the current integration.
- `owner_external_id` is the current owned WeChat account from callback `wcId`, falling back to `data.toUser`.
- `external_user_id` is the private sender from `data.fromUser` and should match contact detail `userName`.
- `wId` is stored only as the latest connection instance because official documentation says it may change after login.
- Nickname, remark, avatar, province, city, and labels are refreshable attributes, never identity keys.
- The same person contacting two owned WeChat accounts receives two profiles initially. This prevents accidental cross-account data disclosure; a separately authorized merge feature can be designed later.
- Group messages do not create private-contact profiles.
- Self-sent messages and callback test events do not create profiles.

Official field references used by this design:

- Callback fields and private message range: `https://wkteam.cn/docs/api-wen-dang2/xiao-xi-jie-shou/shou-xiao-xi/callback.html`
- Contact request and stable fields: `https://wkteam.cn/docs/api-wen-dang2/hao-you-cao-zuo/queryUserInfo.html`
- Full contact response: `https://wkteam.cn/docs/api-wen-dang2/hao-you-cao-zuo/getContactPlus.html`

## Existing capability retained

The repository already has:

- `user_profiles` for stage, risk, handoff, tags, interests, summaries, pain points, and opportunity state;
- `conversation_memories` for user, AI, and human messages;
- `profile_events` for before/after audit records;
- profile GET/PATCH, memory, and event APIs;
- a workbench profile panel;
- chat-triggered profile extraction based only on customer-authored messages.

This plan extends those capabilities instead of replacing them.

This plan owns identity resolution, contact persistence, and the durable refresh
worker. The companion plan
`docs/superpowers/plans/2026-07-10-sales-user-memory-layer.md` owns temporal
facts, sales episodes, conversation compaction, relevance retrieval, and prompt
assembly. Execute this identity plan first; do not add semantic memory before
all inbound messages carry the canonical `profile_user_id`.

## Success checks

- The first valid Eyun private message creates exactly one `user_profiles` row and one `user_identities` row before AI processing begins.
- Repeated messages from the same identity reuse the same internal `profile_user_id`.
- The same sender under a different owned WeChat account does not reuse the profile.
- Text and non-text private messages both create a profile; group, self, and test callbacks do not.
- Conversation rows, conversation messages, chat memories, and profile events all use the same canonical internal user ID.
- `wId` changes update connection metadata without creating a new profile.
- Contact details refresh without changing identity.
- `phoneNumList`, `v1`, `v3`, signature, raw callback JSON, and authorization tokens are not copied into the profile tables.
- Profile enrichment jobs survive a process restart and coalesce multiple messages for the same profile.
- Existing `/api/v1/users/{user_id}/profile` response fields remain compatible; a new `identities` array is additive.

## Target information flow

```text
Eyun private callback
   |
   +--> validate message type / self / group / duplicate
   |
   +--> resolve_private_contact(
   |      tenant, owner wcId, fromUser, contact snapshot
   |    )
   |          |
   |          +--> user_identities
   |          +--> user_profiles
   |
   +--> record customer message with profile_user_id
   +--> enqueue inbound batch with profile_user_id
                       |
                       v
                  handle_chat
                       |
        deterministic profile signals + memories
                       |
                       v
             profile_refresh_jobs (debounced)
                       |
                       v
              LLM profile enrichment
```

## File map

- Create `wechat_rag_bot/app/schemas/profile_identity.py`: typed identity and contact snapshot contracts.
- Create `wechat_rag_bot/app/services/profile_identity_service.py`: transactional resolve/upsert logic.
- Create `wechat_rag_bot/app/services/profile_refresh_service.py`: durable coalescing and refresh worker.
- Create `wechat_rag_bot/app/scripts/backfill_private_contact_profiles.py`: dry-run and apply migration for existing Eyun conversations.
- Create `wechat_rag_bot/tests/test_profile_identity_service.py`: identity uniqueness and contact refresh tests.
- Create `wechat_rag_bot/tests/test_profile_refresh_service.py`: durable job and coalescing tests.
- Modify `wechat_rag_bot/app/db/models.py`: identity and refresh-job tables plus inbound-batch canonical user ID.
- Modify `wechat_rag_bot/app/services/eyun_contact_service.py`: parse documented contact fields while minimizing stored PII.
- Modify `wechat_rag_bot/app/services/eyun_callback_service.py`: resolve profile before recording every private message.
- Modify `wechat_rag_bot/app/services/message_risk_control_service.py`: carry canonical ID through the queue and into `ChatRequest`.
- Modify `wechat_rag_bot/app/services/user_profile_service.py`: split deterministic updates from LLM enrichment and return identities.
- Modify `wechat_rag_bot/app/services/chat_orchestrator.py`: enqueue durable profile refresh instead of an in-process background coroutine.
- Modify `wechat_rag_bot/app/main.py`: start and stop the profile refresh worker.
- Modify `wechat_rag_bot/app/routers/user_profile.py`: return additive identity data through the existing profile bundle.
- Modify `admin-web/src/api/user-profile/index.ts`: add identity types.
- Modify `admin-web/src/views/workbench/components/SupervisionPanel.vue`: show contact source and last refresh.
- Modify existing Eyun, risk-control, profile API, admin conversation, and profile tests.

### Task 1: Define provider identity and contact contracts

**Files:**
- Create: `wechat_rag_bot/app/schemas/profile_identity.py`
- Create: `wechat_rag_bot/tests/test_profile_identity_service.py`

- [ ] **Step 1: Write the contract test**

Create `tests/test_profile_identity_service.py`:

```python
from app.schemas.profile_identity import ContactSnapshot, ProviderIdentityKey


def test_provider_identity_key_excludes_transient_wid_and_display_fields():
    key = ProviderIdentityKey(
        tenant_id="tenant_default",
        provider="eyun",
        owner_external_id="wx_owner_001",
        external_user_id="wx_customer_001",
    )
    contact = ContactSnapshot(
        nickname="养兰小李",
        remark_name="浙江小李",
        avatar_url="https://example.com/avatar.jpg",
        alias_name="xiaoli",
        province="浙江",
        city="杭州",
        label_ids=["1", "8"],
    )

    assert key.model_dump() == {
        "tenant_id": "tenant_default",
        "provider": "eyun",
        "owner_external_id": "wx_owner_001",
        "external_user_id": "wx_customer_001",
    }
    assert "w_id" not in key.model_dump()
    assert contact.display_name == "浙江小李"
```

- [ ] **Step 2: Run the contract test and verify it fails**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_profile_identity_service.py::test_provider_identity_key_excludes_transient_wid_and_display_fields -q
```

Expected: import failure for `app.schemas.profile_identity`.

- [ ] **Step 3: Implement the contracts**

Create `app/schemas/profile_identity.py`:

```python
from pydantic import BaseModel, Field


class ProviderIdentityKey(BaseModel):
    tenant_id: str = "tenant_default"
    provider: str
    owner_external_id: str
    external_user_id: str


class ContactSnapshot(BaseModel):
    user_name: str | None = None
    alias_name: str | None = None
    nickname: str | None = None
    remark_name: str | None = None
    avatar_url: str | None = None
    sex: int = 0
    country: str | None = None
    province: str | None = None
    city: str | None = None
    label_ids: list[str] = Field(default_factory=list)

    @property
    def display_name(self) -> str | None:
        direct = self.remark_name or self.nickname or self.alias_name
        if direct:
            return direct
        if self.user_name and self.user_name.endswith("@openim"):
            suffix = self.user_name.removesuffix("@openim")[-5:]
            return f"企业微信用户（{suffix}）"
        return None


class ResolvedProfileIdentity(BaseModel):
    profile_user_id: str
    key: ProviderIdentityKey
    created: bool = False
```

- [ ] **Step 4: Run the contract test**

Run:

```powershell
python -m pytest tests/test_profile_identity_service.py::test_provider_identity_key_excludes_transient_wid_and_display_fields -q
```

Expected: `1 passed`.

### Task 2: Add durable identity and profile refresh models

**Files:**
- Modify: `wechat_rag_bot/app/db/models.py`
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Modify: `wechat_rag_bot/tests/test_profile_identity_service.py`

- [ ] **Step 1: Add table constraint tests**

Append to `tests/test_profile_identity_service.py`:

```python
from sqlalchemy import create_engine, inspect

from app.db.models import Base


def test_profile_identity_and_refresh_tables_have_required_columns():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    identity_columns = {item["name"] for item in inspector.get_columns("user_identities")}
    job_columns = {item["name"] for item in inspector.get_columns("profile_refresh_jobs")}

    assert {
        "profile_user_id",
        "tenant_id",
        "provider",
        "owner_external_id",
        "external_user_id",
        "latest_w_id",
        "nickname",
        "remark_name",
        "last_seen_at",
    } <= identity_columns
    assert {"profile_user_id", "status", "due_at", "attempts", "last_error"} <= job_columns
```

- [ ] **Step 2: Run the table test and verify it fails**

Run:

```powershell
python -m pytest tests/test_profile_identity_service.py::test_profile_identity_and_refresh_tables_have_required_columns -q
```

Expected: SQLAlchemy reports that `user_identities` or `profile_refresh_jobs` does not exist.

- [ ] **Step 3: Add SQLAlchemy imports**

Change the model imports to:

```python
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
```

- [ ] **Step 4: Add the two models**

Add after `UserProfileModel` in `app/db/models.py`:

```python
class UserIdentityModel(Base):
    __tablename__ = "user_identities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "provider",
            "owner_external_id",
            "external_user_id",
            name="uq_user_identity_provider_contact",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_user_id: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(64), default="wechat")
    owner_external_id: Mapped[str] = mapped_column(String(256), index=True)
    external_user_id: Mapped[str] = mapped_column(String(256), index=True)
    latest_w_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    source_account: Mapped[str | None] = mapped_column(String(256), nullable=True)
    alias_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remark_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    sex: Mapped[int] = mapped_column(Integer, default=0)
    country: Mapped[str | None] = mapped_column(String(64), nullable=True)
    province: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    contact_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProfileRefreshJobModel(Base):
    __tablename__ = "profile_refresh_jobs"

    profile_user_id: Mapped[str] = mapped_column(
        String(256),
        ForeignKey("user_profiles.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

- [ ] **Step 5: Add canonical profile ID to inbound batches**

Add this nullable column to `EyunInboundBatchModel` so old pending rows remain readable:

```python
profile_user_id: Mapped[str | None] = mapped_column(
    String(256), index=True, nullable=True
)
```

- [ ] **Step 6: Make the inbound-batch column compatible with existing databases**

Before running tests against an existing chat-log database, call this compatibility helper from `_get_session()` immediately after `create_all()`:

```python
def _ensure_profile_user_id_column(engine) -> None:
    columns = {
        column["name"]
        for column in inspect(engine).get_columns("eyun_inbound_batches")
    }
    if "profile_user_id" not in columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE eyun_inbound_batches "
                    "ADD COLUMN profile_user_id VARCHAR(256)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_eyun_inbound_batches_profile_user_id "
                    "ON eyun_inbound_batches (profile_user_id)"
                )
            )
```

Import `inspect` and `text` from SQLAlchemy in `message_risk_control_service.py`.

- [ ] **Step 7: Run the model tests**

Run:

```powershell
python -m pytest tests/test_profile_identity_service.py -q
```

Expected: all profile identity contract and table tests pass.

### Task 3: Resolve one canonical profile per private contact

**Files:**
- Create: `wechat_rag_bot/app/services/profile_identity_service.py`
- Modify: `wechat_rag_bot/tests/test_profile_identity_service.py`

- [ ] **Step 1: Add identity reuse and account-isolation tests**

Append to `tests/test_profile_identity_service.py`:

```python
import pytest

from app.config import get_settings
from app.schemas.profile_identity import ContactSnapshot, ProviderIdentityKey


@pytest.mark.asyncio
async def test_resolve_private_contact_reuses_identity_and_refreshes_wid(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}")
    get_settings.cache_clear()
    from app.services.profile_identity_service import resolve_private_contact

    key = ProviderIdentityKey(
        tenant_id="tenant_default",
        provider="eyun",
        owner_external_id="owner_1",
        external_user_id="customer_1",
    )
    first = await resolve_private_contact(
        key=key,
        channel="wechat",
        latest_w_id="wid_login_1",
        source_account="account_a",
        contact=ContactSnapshot(nickname="小李"),
    )
    second = await resolve_private_contact(
        key=key,
        channel="wechat",
        latest_w_id="wid_login_2",
        source_account="account_a",
        contact=ContactSnapshot(remark_name="浙江小李"),
    )

    assert first.created is True
    assert second.created is False
    assert second.profile_user_id == first.profile_user_id


@pytest.mark.asyncio
async def test_same_sender_on_another_owned_account_gets_another_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}")
    get_settings.cache_clear()
    from app.services.profile_identity_service import resolve_private_contact

    common = {
        "channel": "wechat",
        "latest_w_id": "wid",
        "source_account": None,
        "contact": ContactSnapshot(nickname="小李"),
    }
    one = await resolve_private_contact(
        key=ProviderIdentityKey(
            provider="eyun",
            owner_external_id="owner_1",
            external_user_id="customer_1",
        ),
        **common,
    )
    two = await resolve_private_contact(
        key=ProviderIdentityKey(
            provider="eyun",
            owner_external_id="owner_2",
            external_user_id="customer_1",
        ),
        **common,
    )
    assert one.profile_user_id != two.profile_user_id
```

- [ ] **Step 2: Run the resolver tests and verify they fail**

Run:

```powershell
python -m pytest tests/test_profile_identity_service.py -q
```

Expected: import failure for `app.services.profile_identity_service`.

- [ ] **Step 3: Implement transactional resolution**

Create `app/services/profile_identity_service.py` with these public functions:

```python
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, UserIdentityModel, UserProfileModel
from app.schemas.profile_identity import (
    ContactSnapshot,
    ProviderIdentityKey,
    ResolvedProfileIdentity,
)
from app.utils.ids import generate_id


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [UserProfileModel.__table__, UserIdentityModel.__table__]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


def _identity_query(key: ProviderIdentityKey):
    return select(UserIdentityModel).where(
        UserIdentityModel.tenant_id == key.tenant_id,
        UserIdentityModel.provider == key.provider,
        UserIdentityModel.owner_external_id == key.owner_external_id,
        UserIdentityModel.external_user_id == key.external_user_id,
    )


def _apply_contact(row: UserIdentityModel, contact: ContactSnapshot) -> None:
    row.alias_name = contact.alias_name or row.alias_name
    row.nickname = contact.nickname or row.nickname
    row.remark_name = contact.remark_name or row.remark_name
    row.avatar_url = contact.avatar_url or row.avatar_url
    row.sex = contact.sex or row.sex
    row.country = contact.country or row.country
    row.province = contact.province or row.province
    row.city = contact.city or row.city
    if contact.label_ids:
        row.label_ids_json = json.dumps(contact.label_ids, ensure_ascii=False)


async def resolve_private_contact(
    *,
    key: ProviderIdentityKey,
    channel: str,
    latest_w_id: str | None,
    source_account: str | None,
    contact: ContactSnapshot,
) -> ResolvedProfileIdentity:
    if not key.owner_external_id or not key.external_user_id:
        raise ValueError("owner_external_id and external_user_id are required")
    now = _now()
    try:
        with _get_session() as session:
            row = session.scalar(_identity_query(key))
            created = row is None
            if row is None:
                profile_user_id = generate_id("user")
                session.add(
                    UserProfileModel(
                        user_id=profile_user_id,
                        tenant_id=key.tenant_id,
                        channel=channel,
                        current_stage="unknown",
                        risk_level="normal",
                        created_at=now,
                        updated_at=now,
                    )
                )
                row = UserIdentityModel(
                    profile_user_id=profile_user_id,
                    tenant_id=key.tenant_id,
                    provider=key.provider,
                    channel=channel,
                    owner_external_id=key.owner_external_id,
                    external_user_id=key.external_user_id,
                    first_seen_at=now,
                    last_seen_at=now,
                    label_ids_json="[]",
                )
                session.add(row)
            row.latest_w_id = latest_w_id or row.latest_w_id
            row.source_account = source_account or row.source_account
            row.last_seen_at = now
            _apply_contact(row, contact)
            if any((contact.nickname, contact.remark_name, contact.avatar_url, contact.alias_name)):
                row.contact_synced_at = now
            session.commit()
            return ResolvedProfileIdentity(
                profile_user_id=row.profile_user_id,
                key=key,
                created=created,
            )
    except IntegrityError:
        with _get_session() as session:
            row = session.scalar(_identity_query(key))
            if row is None:
                raise
            return ResolvedProfileIdentity(profile_user_id=row.profile_user_id, key=key)
```

- [ ] **Step 4: Run identity resolver tests**

Run:

```powershell
python -m pytest tests/test_profile_identity_service.py -q
```

Expected: all identity contract, table, reuse, and account-isolation tests pass.

### Task 4: Parse documented Eyun contact fields without persisting unnecessary PII

**Files:**
- Modify: `wechat_rag_bot/app/services/eyun_contact_service.py`
- Modify: `wechat_rag_bot/tests/test_eyun_callback.py`

- [ ] **Step 1: Add a complete contact parsing test**

Add to `tests/test_eyun_callback.py`:

```python
def test_parse_contact_snapshot_keeps_documented_profile_fields_only():
    from app.services.eyun_contact_service import parse_contact_snapshot

    snapshot = parse_contact_snapshot(
        {
            "code": "1000",
            "data": [
                {
                    "userName": "customer_1",
                    "nickName": "小李",
                    "remark": "浙江小李",
                    "aliasName": "xiaoli",
                    "sex": 1,
                    "country": "CN",
                    "province": "浙江",
                    "city": "杭州",
                    "bigHead": "https://example.com/a.jpg",
                    "labelList": "1,8",
                    "phoneNumList": ["13800000000"],
                    "signature": "private signature",
                    "v1": "credential-v1",
                    "v3": "credential-v3",
                }
            ],
        }
    )

    assert snapshot.user_name == "customer_1"
    assert snapshot.display_name == "浙江小李"
    assert snapshot.label_ids == ["1", "8"]
    dumped = snapshot.model_dump()
    assert "phoneNumList" not in dumped
    assert "signature" not in dumped
    assert "v1" not in dumped
    assert "v3" not in dumped
```

- [ ] **Step 2: Change the parser return type**

Import `ContactSnapshot` and return an empty model for failed responses:

```python
def parse_contact_snapshot(response: dict[str, Any]) -> ContactSnapshot:
    if str(response.get("code")) != "1000":
        return ContactSnapshot()
```

For a successful contact, return:

```python
return ContactSnapshot(
    user_name=_text(contact, "userName", "username"),
    alias_name=_text(contact, "aliasName"),
    nickname=_text(contact, "nickName", "nickname"),
    remark_name=_text(contact, "remark", "remarkName", "conRemark"),
    avatar_url=_text(
        contact,
        "bigHead",
        "smallHead",
        "bigHeadImgUrl",
        "smallHeadImgUrl",
        "avatarUrl",
        "avatar",
    ),
    sex=int(contact.get("sex") or 0),
    country=_text(contact, "country"),
    province=_text(contact, "province"),
    city=_text(contact, "city"),
    label_ids=[
        item.strip()
        for item in str(contact.get("labelList") or "").split(",")
        if item.strip()
    ],
)
```

- [ ] **Step 3: Update the cached service contract**

Change `get_eyun_contact_snapshot()` to return `ContactSnapshot`. Cache `model_dump()` values and reconstruct with `ContactSnapshot.model_validate()` so callers always receive the typed contract. Replace `if not snapshot` with:

```python
if not snapshot.model_dump(exclude_none=True):
    logger.warning(
        "Eyun getContact returned no usable contact fields: code=%s message=%s",
        body.get("code"),
        body.get("message"),
    )
    return ContactSnapshot()
```

Update existing tests that compare dictionaries to compare `snapshot.model_dump(exclude_none=True)`.

- [ ] **Step 4: Run Eyun contact tests**

Run:

```powershell
python -m pytest tests/test_eyun_callback.py -q
```

Expected: all Eyun callback and contact parsing tests pass.

### Task 5: Create profiles before recording every valid private message

**Files:**
- Modify: `wechat_rag_bot/app/services/eyun_callback_service.py`
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Modify: `wechat_rag_bot/app/db/models.py`
- Modify: `wechat_rag_bot/tests/test_eyun_callback.py`
- Modify: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Add callback behavior tests**

Add these imports and the private-image case:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_private_image_creates_profile_before_recording_message(monkeypatch):
    from app.services import eyun_callback_service

    calls = []

    async def resolve_private_contact(**kwargs):
        calls.append(("resolve", kwargs["key"].external_user_id))
        return SimpleNamespace(profile_user_id="user_internal_1")

    async def record_customer_message(**kwargs):
        calls.append(("record", kwargs["user_id"]))

    monkeypatch.setattr(eyun_callback_service, "resolve_private_contact", resolve_private_contact)
    monkeypatch.setattr(eyun_callback_service, "record_customer_message", record_customer_message)
    monkeypatch.setattr(
        eyun_callback_service,
        "_eyun_workbench_metadata",
        AsyncMock(return_value={"contact_snapshot": {}}),
    )

    await eyun_callback_service.handle_eyun_callback(
        {
            "wcId": "owner_1",
            "account": "account_a",
            "messageType": "60002",
            "data": {
                "fromUser": "customer_1",
                "fromGroup": "",
                "toUser": "owner_1",
                "newMsgId": "msg_1",
                "self": False,
                "content": "",
            },
        }
    )

    assert calls == [("resolve", "customer_1"), ("record", "user_internal_1")]
```

Add this parameterized exclusion test:

```python
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {
            "wcId": "owner_1",
            "messageType": "80001",
            "data": {"fromUser": "member_1", "fromGroup": "group@chatroom", "self": False},
        },
        {
            "wcId": "owner_1",
            "messageType": "60001",
            "data": {"fromUser": "owner_1", "fromGroup": "", "self": True},
        },
        {"wcId": "owner_1", "messageType": "00000", "data": {}},
    ],
)
async def test_non_private_customer_events_do_not_create_profiles(monkeypatch, payload):
    from app.services import eyun_callback_service

    async def fail_resolve(**kwargs):
        raise AssertionError(kwargs)

    monkeypatch.setattr(eyun_callback_service, "resolve_private_contact", fail_resolve)
    result = await eyun_callback_service.handle_eyun_callback(payload)
    assert result == eyun_callback_service.eyun_success()
```

- [ ] **Step 2: Preserve display metadata and add a typed contact snapshot**

In `_eyun_workbench_metadata()`, replace the direct dictionary update from `get_eyun_contact_snapshot()` with:

```python
contact = await get_eyun_contact_snapshot(w_id=w_id, wc_id=user_id)
metadata["contact_snapshot"] = contact.model_dump(exclude_none=True)
if contact.remark_name:
    metadata["remark_name"] = contact.remark_name
if contact.nickname:
    metadata["nickname"] = contact.nickname
if contact.avatar_url:
    metadata["avatar_url"] = contact.avatar_url
```

This retains the existing conversation display-name behavior while giving the identity service a typed payload.

- [ ] **Step 3: Resolve identity immediately after callback validation**

In `handle_eyun_callback()`, after validating the event and building contact metadata, construct:

```python
owner_external_id = str(payload.get("wcId") or data.get("toUser") or "").strip()
external_user_id = str(data.get("fromUser") or "").strip()
contact = ContactSnapshot.model_validate(metadata.pop("contact_snapshot", {}))
identity = await resolve_private_contact(
    key=ProviderIdentityKey(
        tenant_id="tenant_default",
        provider="eyun",
        owner_external_id=owner_external_id,
        external_user_id=external_user_id,
    ),
    channel="wechat",
    latest_w_id=str(data.get("wId") or payload.get("wId") or "") or None,
    source_account=str(payload.get("account") or "") or None,
    contact=contact,
)
metadata.update(
    {
        "profile_user_id": identity.profile_user_id,
        "external_user_id": external_user_id,
        "owner_external_id": owner_external_id,
    }
)
```

Pass `identity.profile_user_id` to `record_customer_message()` instead of `_eyun_conversation_user_id(data)`.

- [ ] **Step 4: Carry canonical ID into the inbound queue**

Change the queue signature to:

```python
async def enqueue_eyun_inbound(
    payload: dict[str, Any], *, profile_user_id: str
) -> dict[str, Any]:
```

Set `batch.profile_user_id` on create and update. Include it in `_inbound_batch_to_dict()`.

Call it from the callback using:

```python
await enqueue_eyun_inbound(payload, profile_user_id=identity.profile_user_id)
```

- [ ] **Step 5: Use the canonical ID in the worker**

Change the worker `ChatRequest` to:

```python
ChatRequest(
    channel="wechat",
    user_id=batch_data["profile_user_id"],
    session_id=batch_data["from_group"],
    message=batch_data["content"],
    kb_id=get_settings().wechat_default_kb_id,
    metadata={
        "provider": "eyun",
        "external_user_id": batch_data["from_user"],
        "owner_external_id": batch_data["wc_id"],
        "account": batch_data["account"],
        "w_id": batch_data["w_id"],
        "batch_key": batch_data["batch_key"],
        "message_count": batch_data["message_count"],
        **customer_snapshot,
    },
)
```

Use `batch_data["profile_user_id"]` in `_conversation_blocks_ai()` so the pre-recorded customer message and AI result share one conversation ID.

- [ ] **Step 6: Run callback and queue tests**

Run:

```powershell
python -m pytest tests/test_eyun_callback.py tests/test_message_risk_control.py tests/test_admin_conversations.py -q
```

Expected: all selected tests pass; text and non-text private messages use the canonical profile ID.

### Task 6: Replace per-message in-process analysis with durable coalesced refresh

**Files:**
- Create: `wechat_rag_bot/app/services/profile_refresh_service.py`
- Create: `wechat_rag_bot/tests/test_profile_refresh_service.py`
- Modify: `wechat_rag_bot/app/services/user_profile_service.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Modify: `wechat_rag_bot/app/main.py`

- [ ] **Step 1: Write coalescing and restart tests**

Create `tests/test_profile_refresh_service.py` with the following coalescing and persistence test:

Use this core assertion:

```python
from datetime import datetime, timezone

import pytest

from app.config import get_settings
from app.db.models import UserProfileModel


@pytest.mark.asyncio
async def test_profile_refresh_jobs_coalesce_and_survive_service_cache_reset(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}")
    get_settings.cache_clear()
    from app.services import profile_refresh_service

    with profile_refresh_service._get_session() as session:
        now = datetime.now(timezone.utc)
        session.add(
            UserProfileModel(
                user_id="user_1",
                tenant_id="tenant_default",
                channel="wechat",
                current_stage="unknown",
                risk_level="normal",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()

    await profile_refresh_service.schedule_profile_refresh("user_1", delay_seconds=60)
    await profile_refresh_service.schedule_profile_refresh("user_1", delay_seconds=30)
    rows = profile_refresh_service.list_profile_refresh_jobs()
    assert len(rows) == 1
    assert rows[0]["profile_user_id"] == "user_1"
    assert rows[0]["status"] == "pending"

    profile_refresh_service._sessionmakers.clear()
    rows_after_reset = profile_refresh_service.list_profile_refresh_jobs()
    assert len(rows_after_reset) == 1
```

- [ ] **Step 2: Implement scheduling**

Create `profile_refresh_service.py` with these imports, database helpers, scheduler, and list helper:

```python
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, ProfileRefreshJobModel, UserProfileModel


_sessionmakers: dict[str, sessionmaker] = {}
_tables = [UserProfileModel.__table__, ProfileRefreshJobModel.__table__]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_session() -> Session:
    url = get_settings().database_url
    factory = _sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=_tables)
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[url] = factory
    return factory()


async def schedule_profile_refresh(
    profile_user_id: str, *, delay_seconds: int = 30
) -> None:
    now = _now()
    due_at = now + timedelta(seconds=max(delay_seconds, 0))
    with _get_session() as session:
        row = session.get(ProfileRefreshJobModel, profile_user_id)
        if row is None:
            row = ProfileRefreshJobModel(
                profile_user_id=profile_user_id,
                status="pending",
                due_at=due_at,
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
        else:
            row.status = "pending"
            row.due_at = due_at
            row.last_error = None
            row.updated_at = now
        session.commit()


def list_profile_refresh_jobs() -> list[dict]:
    with _get_session() as session:
        rows = session.scalars(
            select(ProfileRefreshJobModel).order_by(ProfileRefreshJobModel.due_at.asc())
        ).all()
        return [
            {
                "profile_user_id": row.profile_user_id,
                "status": row.status,
                "due_at": row.due_at.isoformat(),
                "attempts": row.attempts,
                "last_error": row.last_error,
            }
            for row in rows
        ]
```

- [ ] **Step 3: Split deterministic updates from LLM enrichment**

Replace `update_profile_after_chat()` with these two concrete functions, retaining the existing helper calls:

```python
async def apply_profile_signals_after_chat(message, intent, reply) -> None:
    with _get_session() as session:
        profile = _get_or_create_profile(
            session,
            message.user_id,
            tenant_id=message.tenant_id,
            channel=message.channel,
        )
        before = _profile_to_dict(profile)
        if getattr(intent, "sales_stage", None) and intent.sales_stage != "unknown":
            profile.current_stage = intent.sales_stage
        profile.last_intent = intent.primary_intent
        profile.last_route = reply.route
        profile.last_template_id = reply.template_id
        profile.last_active_at = _now()
        _apply_tag_result(profile, reply.metadata.get("tag_result"))
        _apply_sales_action(profile, intent, reply.metadata.get("sales_action"))
        profile.updated_at = _now()
        if reply.route == "human" or reply.need_human:
            handoff = reply.metadata.get("handoff", {})
            profile.is_human_handoff = True
            profile.human_ticket_id = handoff.get("ticket_id")
            profile.human_handoff_status = "pending"
            profile.human_handoff_reason = (
                handoff.get("reason")
                or getattr(intent, "reason", None)
                or reply.metadata.get("reason")
                or "human_route"
            )
            _add_event(
                session,
                profile,
                "handoff_created",
                before,
                _profile_to_dict(profile),
                profile.human_handoff_reason,
                message.trace_id,
            )
        session.commit()


async def refresh_profile_analysis(profile_user_id: str) -> None:
    with _get_session() as session:
        user_records = _list_profile_context_records(
            session,
            profile_user_id,
            current_message="",
            limit=18,
        )
    profile_analysis = await _build_profile_analysis(user_records)
    with _get_session() as session:
        profile = _get_or_create_profile(session, profile_user_id)
        before = _profile_to_dict(profile)
        _apply_profile_analysis(profile, profile_analysis)
        profile.updated_at = _now()
        _add_event(
            session,
            profile,
            "profile_analysis_refreshed",
            before,
            _profile_to_dict(profile),
            "debounced_profile_refresh",
            None,
        )
        session.commit()
```

The first function must not call an LLM. The second function must read only customer-authored content as facts, preserving the existing profile prompt tests.

- [ ] **Step 4: Implement one-job claiming and retry**

Implement `process_due_profile_refresh_jobs()` with one transaction to select IDs, one conditional claim per ID, and a fresh transaction for completion:

```python
async def process_due_profile_refresh_jobs(limit: int = 5) -> int:
    now = _now()
    with _get_session() as session:
        profile_ids = list(
            session.scalars(
                select(ProfileRefreshJobModel.profile_user_id)
                .where(
                    ProfileRefreshJobModel.status == "pending",
                    ProfileRefreshJobModel.due_at <= now,
                )
                .order_by(ProfileRefreshJobModel.due_at.asc())
                .limit(limit)
            ).all()
        )

    attempted = 0
    for profile_user_id in profile_ids:
        with _get_session() as session:
            claimed = session.execute(
                update(ProfileRefreshJobModel)
                .where(
                    ProfileRefreshJobModel.profile_user_id == profile_user_id,
                    ProfileRefreshJobModel.status == "pending",
                )
                .values(status="processing", updated_at=now)
            )
            if claimed.rowcount != 1:
                session.rollback()
                continue
            session.commit()
        attempted += 1
        try:
            from app.services.user_profile_service import refresh_profile_analysis

            await refresh_profile_analysis(profile_user_id)
        except Exception as exc:
            with _get_session() as session:
                row = session.get(ProfileRefreshJobModel, profile_user_id)
                if row is not None:
                    row.attempts = (row.attempts or 0) + 1
                    row.status = "failed" if row.attempts >= 5 else "pending"
                    row.last_error = str(exc)[:1000]
                    row.due_at = _now() + timedelta(seconds=60)
                    row.updated_at = _now()
                    session.commit()
            continue
        with _get_session() as session:
            row = session.get(ProfileRefreshJobModel, profile_user_id)
            if row is not None:
                session.delete(row)
                session.commit()
    return attempted
```

Expose the worker loop with this shutdown behavior:

```python
async def profile_refresh_worker(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await process_due_profile_refresh_jobs(limit=5)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
```

- [ ] **Step 5: Change orchestrator profile updates**

After appending conversation memories, replace the call that passes `update_profile_after_chat(message, routed_intent, reply)` into `_schedule_background_task()` with:

```python
await apply_profile_signals_after_chat(message, routed_intent, reply)
await schedule_profile_refresh(message.user_id, delay_seconds=30)
```

Remove `_background_tasks` and `_schedule_background_task()` after their remaining references reach zero.

- [ ] **Step 6: Start a stoppable refresh worker**

In `main.py` lifespan, start `profile_refresh_worker(stop_event)` alongside the existing Eyun worker and await both tasks during shutdown. The worker polls once per second using `asyncio.wait_for(stop_event.wait(), timeout=1.0)` and processes at most five due profiles per tick.

- [ ] **Step 7: Run refresh and existing profile tests**

Run:

```powershell
python -m pytest tests/test_profile_refresh_service.py tests/test_user_profile_api.py tests/test_chat_orchestrator_reply_graph.py -q
```

Expected: all selected tests pass; service-cache reset does not lose a pending refresh job.

### Task 7: Expose identity context in the existing profile bundle and workbench

**Files:**
- Modify: `wechat_rag_bot/app/services/user_profile_service.py`
- Modify: `wechat_rag_bot/app/routers/user_profile.py`
- Modify: `wechat_rag_bot/tests/test_user_profile_api.py`
- Modify: `admin-web/src/api/user-profile/index.ts`
- Modify: `admin-web/src/views/workbench/components/SupervisionPanel.vue`

- [ ] **Step 1: Add an additive API contract test**

Add this complete API test to `tests/test_user_profile_api.py`:

```python
@pytest.mark.asyncio
async def test_profile_bundle_returns_public_identity_fields(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    from app.schemas.profile_identity import ContactSnapshot, ProviderIdentityKey
    from app.services.profile_identity_service import resolve_private_contact

    resolved = await resolve_private_contact(
        key=ProviderIdentityKey(
            provider="eyun",
            owner_external_id="owner_1",
            external_user_id="customer_1",
        ),
        channel="wechat",
        latest_w_id="transient_wid",
        source_account="account_a",
        contact=ContactSnapshot(
            nickname="小李",
            remark_name="浙江小李",
            province="浙江",
            city="杭州",
        ),
    )

    response = TestClient(app).get(
        f"/api/v1/users/{resolved.profile_user_id}/profile"
    )
    body = response.json()["data"]
    assert body["profile"]["user_id"] == resolved.profile_user_id
    assert len(body["identities"]) == 1
    identity = body["identities"][0]
    assert identity["provider"] == "eyun"
    assert identity["owner_external_id"] == "owner_1"
    assert identity["external_user_id"] == "customer_1"
    assert identity["display_name"] == "浙江小李"
    assert identity["source_account"] == "account_a"
    assert "latest_w_id" not in identity
    assert "label_ids" not in identity
```

Do not expose `latest_w_id`, label IDs, internal row IDs, credentials, or authorization values.

- [ ] **Step 2: Return identities from `get_profile_bundle()`**

Import `UserIdentityModel`, add `UserIdentityModel.__table__` to `_profile_tables`, query it by `profile_user_id`, order by `last_seen_at DESC`, serialize only the fields in the test, and return:

```python
return {
    "profile": _profile_to_dict(profile),
    "identities": [_identity_to_public_dict(row) for row in identities],
    "recent_memories": _list_memories(session, user_id, 10),
    "events": _list_events(session, user_id, 20),
}
```

- [ ] **Step 3: Extend frontend types**

Add:

```typescript
export interface UserIdentity {
  provider: string
  channel: string
  owner_external_id: string
  external_user_id: string
  source_account?: string | null
  display_name?: string | null
  nickname?: string | null
  remark_name?: string | null
  avatar_url?: string | null
  province?: string | null
  city?: string | null
  first_seen_at?: string | null
  last_seen_at?: string | null
  contact_synced_at?: string | null
}
```

Add `identities: UserIdentity[]` to `UserProfileBundle` and store it alongside `profile` in the workbench state.

- [ ] **Step 4: Show identity information in the supervision panel**

Display the primary identity's `display_name`, `source_account`, region, first-seen time, last-seen time, and contact refresh time. Do not display raw `external_user_id` by default; place it behind the existing admin detail interaction if operational debugging requires it.

- [ ] **Step 5: Run backend and frontend checks**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_user_profile_api.py tests/test_admin_conversations.py -q
cd ../admin-web
pnpm ts:check
pnpm build:local
```

Expected: backend tests pass, TypeScript check exits 0, and the local frontend build exits 0.

### Task 8: Backfill existing private contacts without changing conversation ownership

**Files:**
- Create: `wechat_rag_bot/app/scripts/backfill_private_contact_profiles.py`
- Create: `wechat_rag_bot/tests/test_profile_backfill.py`

- [ ] **Step 1: Add a dry-run/apply test**

Create `tests/test_profile_backfill.py` with this cross-database fixture and dry-run/apply test:

```python
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import (
    ConversationMessageModel,
    ConversationModel,
    UserIdentityModel,
    UserProfileModel,
)


@pytest.fixture
def seed_existing_eyun_conversation(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'profiles.db').as_posix()}")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{(tmp_path / 'conversations.db').as_posix()}")
    get_settings.cache_clear()
    from app.scripts import backfill_private_contact_profiles as backfill

    backfill._profile_sessionmakers.clear()
    backfill._conversation_sessionmakers.clear()
    now = datetime.now(timezone.utc)
    user_id = "existing_external_user"
    with backfill.get_profile_session() as session:
        session.add(
            UserProfileModel(
                user_id=user_id,
                tenant_id="tenant_default",
                channel="wechat",
                current_stage="unknown",
                risk_level="normal",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    with backfill.get_conversation_session() as session:
        session.add(
            ConversationModel(
                conversation_id=f"wechat:{user_id}:default",
                channel="wechat",
                user_id=user_id,
                session_id="default",
                tenant_id="tenant_default",
                status="ai_waiting",
                created_at=now,
                updated_at=now,
            )
        )
        session.add(
            ConversationMessageModel(
                conversation_id=f"wechat:{user_id}:default",
                sender_type="customer",
                sender_id=user_id,
                content="你好",
                metadata_json=json.dumps(
                    {
                        "provider": "eyun",
                        "wc_id": "owner_1",
                        "from_user": "customer_1",
                        "from_group": "",
                    },
                    ensure_ascii=False,
                ),
                created_at=now,
            )
        )
        session.commit()
    return user_id


def test_backfill_preserves_existing_conversation_user_id(seed_existing_eyun_conversation):
    from app.scripts import backfill_private_contact_profiles as backfill

    existing_user_id = seed_existing_eyun_conversation

    dry_run = backfill.backfill_private_contact_profiles(apply=False)
    assert dry_run == {"candidates": 1, "created": 0, "skipped": 0}

    applied = backfill.backfill_private_contact_profiles(apply=True)
    assert applied == {"candidates": 1, "created": 1, "skipped": 0}
    with backfill.get_profile_session() as session:
        identity = session.scalar(select(UserIdentityModel))
    assert identity.profile_user_id == existing_user_id
```

- [ ] **Step 2: Implement metadata extraction and idempotency**

Implement the two database accessors with separate URLs:

```python
_profile_sessionmakers: dict[str, sessionmaker] = {}
_conversation_sessionmakers: dict[str, sessionmaker] = {}


def get_profile_session() -> Session:
    url = get_settings().database_url
    factory = _profile_sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(
            engine,
            tables=[UserProfileModel.__table__, UserIdentityModel.__table__],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _profile_sessionmakers[url] = factory
    return factory()


def get_conversation_session() -> Session:
    url = get_settings().chat_log_db_url
    factory = _conversation_sessionmakers.get(url)
    if factory is None:
        engine = create_engine(url)
        Base.metadata.create_all(
            engine,
            tables=[ConversationModel.__table__, ConversationMessageModel.__table__],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _conversation_sessionmakers[url] = factory
    return factory()
```

Import `create_engine`, `select`, `Session`, and `sessionmaker`. The backfill function must:

1. Read customer `ConversationMessageModel` rows ordered by ID.
2. Parse `metadata_json` and keep only `provider == "eyun"` with no `from_group`.
3. Resolve `owner_external_id` from `owner_external_id`, then `wc_id`.
4. Resolve `external_user_id` from `external_user_id`, then `from_user`.
5. Skip rows missing either identifier and count them as skipped.
6. If the composite identity already exists, skip it.
7. Reuse the conversation's current `user_id`; create a minimal `UserProfileModel` only when that profile does not exist.
8. Insert identity display fields from message metadata without copying raw callback payloads.

Use this implementation shape:

```python
def _metadata(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def backfill_private_contact_profiles(*, apply: bool) -> dict[str, int]:
    candidates: dict[tuple[str, str, str, str], dict] = {}
    skipped = 0
    with get_conversation_session() as conversation_session:
        messages = conversation_session.scalars(
            select(ConversationMessageModel)
            .where(ConversationMessageModel.sender_type == "customer")
            .order_by(ConversationMessageModel.id.asc())
        ).all()
        for message in messages:
            metadata = _metadata(message.metadata_json)
            if metadata.get("provider") != "eyun" or metadata.get("from_group"):
                continue
            owner_external_id = str(
                metadata.get("owner_external_id") or metadata.get("wc_id") or ""
            ).strip()
            external_user_id = str(
                metadata.get("external_user_id") or metadata.get("from_user") or ""
            ).strip()
            conversation = conversation_session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id == message.conversation_id
                )
            )
            if not owner_external_id or not external_user_id or conversation is None:
                skipped += 1
                continue
            tenant_id = conversation.tenant_id or "tenant_default"
            key = (tenant_id, "eyun", owner_external_id, external_user_id)
            candidates.setdefault(
                key,
                {
                    "profile_user_id": conversation.user_id,
                    "channel": conversation.channel,
                    "source_account": metadata.get("account"),
                    "nickname": metadata.get("nickname"),
                    "remark_name": metadata.get("remark_name"),
                    "avatar_url": metadata.get("avatar_url"),
                    "seen_at": message.created_at,
                },
            )

    result = {"candidates": len(candidates), "created": 0, "skipped": skipped}
    if not apply:
        return result

    now = datetime.now(timezone.utc)
    with get_profile_session() as profile_session:
        for index, (key, value) in enumerate(candidates.items(), start=1):
            tenant_id, provider, owner_external_id, external_user_id = key
            existing = profile_session.scalar(
                select(UserIdentityModel).where(
                    UserIdentityModel.tenant_id == tenant_id,
                    UserIdentityModel.provider == provider,
                    UserIdentityModel.owner_external_id == owner_external_id,
                    UserIdentityModel.external_user_id == external_user_id,
                )
            )
            if existing is not None:
                result["skipped"] += 1
                continue
            profile = profile_session.get(UserProfileModel, value["profile_user_id"])
            if profile is None:
                profile_session.add(
                    UserProfileModel(
                        user_id=value["profile_user_id"],
                        tenant_id=tenant_id,
                        channel=value["channel"],
                        current_stage="unknown",
                        risk_level="normal",
                        created_at=value["seen_at"] or now,
                        updated_at=now,
                    )
                )
            profile_session.add(
                UserIdentityModel(
                    profile_user_id=value["profile_user_id"],
                    tenant_id=tenant_id,
                    provider=provider,
                    channel=value["channel"],
                    owner_external_id=owner_external_id,
                    external_user_id=external_user_id,
                    source_account=value["source_account"],
                    nickname=value["nickname"],
                    remark_name=value["remark_name"],
                    avatar_url=value["avatar_url"],
                    label_ids_json="[]",
                    first_seen_at=value["seen_at"] or now,
                    last_seen_at=value["seen_at"] or now,
                )
            )
            result["created"] += 1
            if index % 100 == 0:
                profile_session.commit()
        profile_session.commit()
    return result
```

- [ ] **Step 3: Expose an explicit command**

Use `argparse` with only `--apply`. Running without it prints JSON dry-run counts; running with it commits in batches of 100. The command is:

```powershell
python -m app.scripts.backfill_private_contact_profiles
python -m app.scripts.backfill_private_contact_profiles --apply
```

- [ ] **Step 4: Run backfill tests twice**

Run:

```powershell
python -m pytest tests/test_profile_backfill.py -q
python -m pytest tests/test_profile_backfill.py -q
```

Expected: both runs pass, proving the backfill is idempotent.

### Task 9: Verify privacy, compatibility, and end-to-end identity consistency

**Files:**
- Modify: `wechat_rag_bot/docs/project_framework.md`
- Modify: `wechat_rag_bot/README.md`

- [ ] **Step 1: Run targeted backend verification**

Run:

```powershell
cd wechat_rag_bot
python -m pytest `
  tests/test_profile_identity_service.py `
  tests/test_profile_refresh_service.py `
  tests/test_profile_backfill.py `
  tests/test_user_profile_api.py `
  tests/test_eyun_callback.py `
  tests/test_message_risk_control.py `
  tests/test_admin_conversations.py -q
```

Expected: all targeted tests pass.

- [ ] **Step 2: Run the full backend and frontend verification**

Run:

```powershell
python -m pytest -q
python -m compileall app
cd ../admin-web
pnpm ts:check
pnpm build:local
```

Expected: every command exits with code 0.

- [ ] **Step 3: Inspect a real private-message smoke test**

For one test contact, send a text and an image. Verify in the database and admin API:

```text
user_identities rows for composite key = 1
user_profiles rows for profile_user_id = 1
conversation user_id = profile_user_id
customer message user_id = profile_user_id
conversation memory user_id = profile_user_id
pending or completed refresh job count <= 1
profile bundle identities count = 1
```

- [ ] **Step 4: Verify excluded data**

Search serialized profile and identity rows and API responses. Confirm none contain:

```text
Authorization token
raw callback JSON
phoneNumList
signature
v1
v3
full prompt text
```

- [ ] **Step 5: Document lifecycle rules**

Update project documentation with:

- composite identity definition;
- why `wId`, nickname, and avatar are not identity keys;
- private versus group behavior;
- first-seen creation and contact refresh flow;
- deterministic versus LLM profile fields;
- refresh-job retry behavior;
- backfill dry-run and apply commands;
- data-minimization list.

- [ ] **Step 6: Review scope and repository state**

Run:

```powershell
git diff --check
git diff --stat
git status --short
```

Do not stage databases, `.env` files, evaluation results, raw callback captures, contact exports, avatars, credentials, or unrelated user changes.

## Delivery order relative to the reply-decision plan

1. Complete and verify the unified reply decision core first because both projects modify `chat_orchestrator.py`.
2. Implement Tasks 1–5 of this plan to establish identity before changing profile refresh behavior.
3. Implement Task 6 so all new canonical profiles receive durable enrichment.
4. Implement Tasks 7–9 for UI, backfill, and rollout verification.
5. Begin PostgreSQL/Redis migration only after identity consistency passes the real private-message smoke test.

## Self-review record

- Spec coverage: every valid private user receives a database profile on first contact, including non-text messages; group/self/test events are excluded.
- Identity safety: the composite key uses stable provider/contact fields and explicitly excludes transient `wId` and mutable display data.
- Type consistency: all tasks use `ProviderIdentityKey`, `ContactSnapshot`, `ResolvedProfileIdentity`, `profile_user_id`, and `schedule_profile_refresh()` with the signatures defined above.
- Privacy: the stored and returned field lists explicitly exclude phone numbers, signatures, relationship credentials, tokens, and raw callbacks.
- Compatibility: `user_profiles` remains the aggregate, current profile fields and API paths remain stable, and identity data is additive.
- Migration safety: existing conversation user IDs are preserved during backfill; new contacts receive generated internal IDs before any conversation row is written.
