# Supervised Handoff Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an IM-style supervision workbench where AI replies by default, humans can monitor all conversations, and humans can reply only after AI triggers handoff or an authorized operator forces handoff.

**Architecture:** Add a small backend conversation state machine on top of existing chat logs, user profiles, and conversation memories. Then build a pruned Vue admin workbench with a three-column IM layout: conversation monitor list, message timeline, and supervision/AI-assist panel with reply controls gated by backend state.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite, pytest, Vue 3, Vite, TypeScript, Element Plus, Pinia, Axios.

---

## Product Rule

This is not a normal IM system.

The invariant is:

```text
AI owns the conversation by default.
Human operators can monitor.
Human operators cannot reply while status is ai_active or ai_waiting.
When AI creates a handoff, status becomes handoff_pending.
An operator must claim the conversation before replying.
Only human_active conversations accept human replies.
```

Allowed status transitions:

```text
ai_active -> handoff_pending
ai_waiting -> handoff_pending
handoff_pending -> human_active
human_active -> ai_active
human_active -> resolved
handoff_pending -> resolved
ai_active -> resolved
ai_waiting -> resolved
```

Forbidden:

```text
ai_active -> human reply
ai_waiting -> human reply
resolved -> human reply
handoff_pending -> human reply without claim
```

---

## File Structure

Backend:

- Modify: `wechat_rag_bot/app/db/models.py`
  - Add `ConversationModel` and `ConversationMessageModel`.
- Create: `wechat_rag_bot/app/schemas/conversation.py`
  - Pydantic schemas for conversation list/detail/actions.
- Create: `wechat_rag_bot/app/services/conversation_service.py`
  - State machine, list/detail, claim, reply, release, resolve, force handoff.
- Create: `wechat_rag_bot/app/routers/admin_conversations.py`
  - Admin APIs for the workbench.
- Modify: `wechat_rag_bot/app/main.py`
  - Register `admin_conversations.router`.
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
  - Upsert conversation and append messages during AI chat handling.
- Modify: `wechat_rag_bot/app/talk_script/human_handoff_service.py`
  - Keep returning current result, but let orchestrator/service persist handoff state.
- Test: `wechat_rag_bot/tests/test_admin_conversations.py`
  - State machine and API permission tests.

Frontend:

- Build after `docs/superpowers/plans/2026-07-02-admin-web-pruning.md` Tasks 1-5.
- Create: `admin-web/src/api/admin/conversations.ts`
- Create: `admin-web/src/views/workbench/index.vue`
- Create: `admin-web/src/views/workbench/components/ConversationList.vue`
- Create: `admin-web/src/views/workbench/components/MessagePanel.vue`
- Create: `admin-web/src/views/workbench/components/SupervisionPanel.vue`
- Create: `admin-web/src/views/workbench/components/ReplyComposer.vue`
- Modify: `admin-web/src/router/modules/admin.ts`
  - Put `workbench` as the first route.

---

### Task 1: Backend Conversation Tables

**Files:**
- Modify: `wechat_rag_bot/app/db/models.py`
- Test: `wechat_rag_bot/tests/test_admin_conversations.py`

- [ ] **Step 1: Write failing model creation test**

Create `wechat_rag_bot/tests/test_admin_conversations.py`:

```python
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def _reset_settings(monkeypatch, tmp_path, *, auth: bool = False):
    db_path = tmp_path / "chat_logs.db"
    monkeypatch.setenv("CHAT_LOG_ENABLED", "true")
    monkeypatch.setenv("CHAT_LOG_PROVIDER", "sqlite")
    monkeypatch.setenv("CHAT_LOG_DB_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("API_AUTH_ENABLED", "true" if auth else "false")
    monkeypatch.setenv("API_KEY", "test-key")
    get_settings.cache_clear()


def test_conversation_list_starts_empty(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

    response = client.get("/api/v1/admin/conversations")

    assert response.status_code == 200
    assert response.json()["data"] == {"items": [], "total": 0, "page": 1, "page_size": 50}
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_admin_conversations.py::test_conversation_list_starts_empty -q
```

Expected: FAIL because `/api/v1/admin/conversations` does not exist.

- [ ] **Step 3: Add database models**

Add to `wechat_rag_bot/app/db/models.py`:

```python
class ConversationModel(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    channel: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    session_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True, default="tenant_default")
    status: Mapped[str] = mapped_column(String(64), index=True, default="ai_active")
    owner_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    handoff_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    handoff_ticket_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ConversationMessageModel(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(256), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    sender_type: Mapped[str] = mapped_column(String(32), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    route: Mapped[str | None] = mapped_column(String(128), nullable=True)
    primary_intent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

- [ ] **Step 4: Commit**

```powershell
git add app/db/models.py tests/test_admin_conversations.py
git commit -m "feat: add conversation workbench models"
```

---

### Task 2: Conversation Service And Read APIs

**Files:**
- Create: `wechat_rag_bot/app/schemas/conversation.py`
- Create: `wechat_rag_bot/app/services/conversation_service.py`
- Create: `wechat_rag_bot/app/routers/admin_conversations.py`
- Modify: `wechat_rag_bot/app/main.py`
- Test: `wechat_rag_bot/tests/test_admin_conversations.py`

- [ ] **Step 1: Add schemas**

Create `wechat_rag_bot/app/schemas/conversation.py`:

```python
from pydantic import BaseModel, Field


class ConversationItem(BaseModel):
    conversation_id: str
    channel: str
    user_id: str
    session_id: str | None = None
    tenant_id: str
    status: str
    owner_id: str | None = None
    last_message: str | None = None
    last_route: str | None = None
    last_intent: str | None = None
    handoff_reason: str | None = None
    handoff_ticket_id: str | None = None
    unread_count: int = 0
    created_at: str
    updated_at: str


class ConversationMessage(BaseModel):
    id: int
    conversation_id: str
    trace_id: str | None = None
    message_id: str | None = None
    sender_type: str
    sender_id: str | None = None
    content: str
    route: str | None = None
    primary_intent: str | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: str


class ConversationListResponse(BaseModel):
    items: list[ConversationItem]
    total: int
    page: int
    page_size: int


class ConversationDetail(BaseModel):
    conversation: ConversationItem
    messages: list[ConversationMessage]


class ClaimRequest(BaseModel):
    operator_id: str


class ReplyRequest(BaseModel):
    operator_id: str
    content: str


class StatusActionRequest(BaseModel):
    operator_id: str
    reason: str | None = None
```

- [ ] **Step 2: Add service constants and read functions**

Create `wechat_rag_bot/app/services/conversation_service.py` with:

```python
import json
from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.models import Base, ConversationMessageModel, ConversationModel
from app.schemas.common import AppError, ErrorCode
from app.utils.ids import generate_id


AI_ACTIVE = "ai_active"
AI_WAITING = "ai_waiting"
HANDOFF_PENDING = "handoff_pending"
HUMAN_ACTIVE = "human_active"
RESOLVED = "resolved"

_sessionmakers: dict[str, sessionmaker] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conversation_id(channel: str, user_id: str, session_id: str | None) -> str:
    return f"{channel}:{user_id}:{session_id or 'default'}"


def _get_session() -> Session:
    settings = get_settings()
    factory = _sessionmakers.get(settings.chat_log_db_url)
    if factory is None:
        engine = create_engine(settings.chat_log_db_url)
        Base.metadata.create_all(
            engine,
            tables=[ConversationModel.__table__, ConversationMessageModel.__table__],
        )
        factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        _sessionmakers[settings.chat_log_db_url] = factory
    return factory()
```

Then add `list_conversations()` and `get_conversation_detail()`:

```python
async def list_conversations(
    *,
    page: int = 1,
    page_size: int = 50,
    status: str | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
) -> dict:
    page = max(page, 1)
    page_size = max(min(page_size, 200), 1)
    with _get_session() as session:
        filters = []
        if status:
            filters.append(ConversationModel.status == status)
        if owner_id:
            filters.append(ConversationModel.owner_id == owner_id)
        if keyword:
            filters.append(ConversationModel.last_message.like(f"%{keyword}%"))
        total = session.scalar(select(func.count()).select_from(ConversationModel).where(*filters))
        rows = session.scalars(
            select(ConversationModel)
            .where(*filters)
            .order_by(ConversationModel.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    return {"items": [_conversation_to_dict(row) for row in rows], "total": int(total or 0), "page": page, "page_size": page_size}


async def get_conversation_detail(conversation_id: str) -> dict:
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(ConversationModel.conversation_id == conversation_id)
        )
        if conversation is None:
            raise AppError(ErrorCode.REQUEST_INVALID, message="会话不存在", status_code=404)
        messages = session.scalars(
            select(ConversationMessageModel)
            .where(ConversationMessageModel.conversation_id == conversation_id)
            .order_by(ConversationMessageModel.created_at.asc(), ConversationMessageModel.id.asc())
        ).all()
    return {"conversation": _conversation_to_dict(conversation), "messages": [_message_to_dict(row) for row in messages]}
```

- [ ] **Step 3: Add router and register it**

Create `wechat_rag_bot/app/routers/admin_conversations.py`:

```python
from fastapi import APIRouter, Depends, Query

from app.schemas.chat import APIResponse
from app.services.conversation_service import get_conversation_detail, list_conversations
from app.utils.auth import require_api_key

router = APIRouter(
    prefix="/api/v1/admin/conversations",
    tags=["admin-conversations"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=APIResponse)
async def conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    status: str | None = None,
    owner_id: str | None = None,
    keyword: str | None = None,
) -> APIResponse:
    data = await list_conversations(page=page, page_size=page_size, status=status, owner_id=owner_id, keyword=keyword)
    return APIResponse(code=0, message="success", data=data)


@router.get("/{conversation_id:path}", response_model=APIResponse)
async def conversation_detail(conversation_id: str) -> APIResponse:
    data = await get_conversation_detail(conversation_id)
    return APIResponse(code=0, message="success", data=data)
```

Modify `wechat_rag_bot/app/main.py` imports and router registration:

```python
from app.routers import admin_conversations
app.include_router(admin_conversations.router)
```

- [ ] **Step 4: Add serialization helpers**

Add to `conversation_service.py`:

```python
def _conversation_to_dict(row: ConversationModel) -> dict:
    return {
        "conversation_id": row.conversation_id,
        "channel": row.channel,
        "user_id": row.user_id,
        "session_id": row.session_id,
        "tenant_id": row.tenant_id,
        "status": row.status,
        "owner_id": row.owner_id,
        "last_message": row.last_message,
        "last_route": row.last_route,
        "last_intent": row.last_intent,
        "handoff_reason": row.handoff_reason,
        "handoff_ticket_id": row.handoff_ticket_id,
        "unread_count": row.unread_count,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


def _message_to_dict(row: ConversationMessageModel) -> dict:
    try:
        metadata = json.loads(row.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "trace_id": row.trace_id,
        "message_id": row.message_id,
        "sender_type": row.sender_type,
        "sender_id": row.sender_id,
        "content": row.content,
        "route": row.route,
        "primary_intent": row.primary_intent,
        "metadata": metadata,
        "created_at": row.created_at.isoformat(),
    }
```

- [ ] **Step 5: Run read API test**

Run:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_admin_conversations.py::test_conversation_list_starts_empty -q
```

Expected: PASS.

---

### Task 3: Persist AI Conversations During Chat

**Files:**
- Modify: `wechat_rag_bot/app/services/conversation_service.py`
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Test: `wechat_rag_bot/tests/test_admin_conversations.py`

- [ ] **Step 1: Add failing integration test**

Add to `test_admin_conversations.py`:

```python
def test_chat_creates_ai_owned_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)

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

    assert chat.status_code == 200
    conversations = client.get("/api/v1/admin/conversations").json()["data"]
    assert conversations["total"] == 1
    item = conversations["items"][0]
    assert item["conversation_id"] == "api:user_001:sess_001"
    assert item["status"] in {"ai_active", "ai_waiting"}
    assert item["owner_id"] is None
```

- [ ] **Step 2: Add upsert function**

Add to `conversation_service.py`:

```python
async def record_ai_turn(*, message, result: dict) -> None:
    conversation_id = _conversation_id(message.channel, message.user_id, message.session_id)
    status = HANDOFF_PENDING if result.get("need_human") else AI_WAITING
    handoff = result.get("handoff") or {}
    now = _now()
    with _get_session() as session:
        conversation = session.scalar(
            select(ConversationModel).where(ConversationModel.conversation_id == conversation_id)
        )
        if conversation is None:
            conversation = ConversationModel(
                conversation_id=conversation_id,
                channel=message.channel,
                user_id=message.user_id,
                session_id=message.session_id,
                tenant_id=message.tenant_id,
                status=status,
                created_at=now,
                updated_at=now,
            )
            session.add(conversation)
        conversation.status = status
        conversation.owner_id = None
        conversation.last_message = message.message
        conversation.last_route = result.get("route")
        conversation.last_intent = (result.get("intent") or {}).get("primary_intent")
        conversation.handoff_reason = handoff.get("reason")
        conversation.handoff_ticket_id = handoff.get("ticket_id")
        conversation.unread_count += 1
        conversation.updated_at = now
        session.add(
            ConversationMessageModel(
                conversation_id=conversation_id,
                trace_id=message.trace_id,
                message_id=message.message_id,
                sender_type="customer",
                sender_id=message.user_id,
                content=message.message,
                route=result.get("route"),
                primary_intent=conversation.last_intent,
                metadata_json=json.dumps({"channel": message.channel}, ensure_ascii=False),
                created_at=now,
            )
        )
        answer = result.get("answer")
        if answer:
            session.add(
                ConversationMessageModel(
                    conversation_id=conversation_id,
                    trace_id=message.trace_id,
                    sender_type="ai",
                    sender_id="ai",
                    content=answer,
                    route=result.get("route"),
                    primary_intent=conversation.last_intent,
                    metadata_json=json.dumps({"sources": result.get("sources", [])}, ensure_ascii=False),
                    created_at=now,
                )
            )
        session.commit()
```

- [ ] **Step 3: Call it from chat orchestrator**

In `wechat_rag_bot/app/services/chat_orchestrator.py`, import:

```python
from app.services.conversation_service import record_ai_turn
```

After `result = _to_chat_data(...)` is built and before returning success, add:

```python
await record_ai_turn(message=message, result=result)
```

- [ ] **Step 4: Run integration test**

```powershell
cd wechat_rag_bot
python -m pytest tests/test_admin_conversations.py::test_chat_creates_ai_owned_conversation -q
```

Expected: PASS.

---

### Task 4: Enforce Claim And Reply Permissions

**Files:**
- Modify: `wechat_rag_bot/app/services/conversation_service.py`
- Modify: `wechat_rag_bot/app/routers/admin_conversations.py`
- Test: `wechat_rag_bot/tests/test_admin_conversations.py`

- [ ] **Step 1: Add permission tests**

Add:

```python
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
    assert "未接管" in response.json()["message"] or "不能人工回复" in response.json()["message"]
```

Add a handoff case:

```python
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
```

- [ ] **Step 2: Add action functions**

Add to `conversation_service.py`:

```python
async def claim_conversation(conversation_id: str, operator_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HANDOFF_PENDING:
            raise AppError(ErrorCode.REQUEST_INVALID, message="只有待人工接管的会话可以领取", status_code=409)
        conversation.status = HUMAN_ACTIVE
        conversation.owner_id = operator_id
        conversation.unread_count = 0
        conversation.updated_at = _now()
        session.commit()
        return _conversation_to_dict(conversation)


async def reply_conversation(conversation_id: str, operator_id: str, content: str) -> dict:
    content = content.strip()
    if not content:
        raise AppError(ErrorCode.REQUEST_INVALID, message="回复内容不能为空", status_code=400)
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(ErrorCode.REQUEST_INVALID, message="当前会话未接管，不能人工回复", status_code=409)
        now = _now()
        session.add(
            ConversationMessageModel(
                conversation_id=conversation_id,
                sender_type="human",
                sender_id=operator_id,
                content=content,
                metadata_json="{}",
                created_at=now,
            )
        )
        conversation.last_message = content
        conversation.updated_at = now
        session.commit()
        return _conversation_to_dict(conversation)


def _get_conversation_or_error(session: Session, conversation_id: str) -> ConversationModel:
    conversation = session.scalar(
        select(ConversationModel).where(ConversationModel.conversation_id == conversation_id)
    )
    if conversation is None:
        raise AppError(ErrorCode.REQUEST_INVALID, message="会话不存在", status_code=404)
    return conversation
```

- [ ] **Step 3: Add action routes**

Add to `admin_conversations.py`:

```python
from app.schemas.conversation import ClaimRequest, ReplyRequest
from app.services.conversation_service import claim_conversation, reply_conversation


@router.post("/{conversation_id:path}/claim", response_model=APIResponse)
async def claim(conversation_id: str, request: ClaimRequest) -> APIResponse:
    data = await claim_conversation(conversation_id, request.operator_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/reply", response_model=APIResponse)
async def reply(conversation_id: str, request: ReplyRequest) -> APIResponse:
    data = await reply_conversation(conversation_id, request.operator_id, request.content)
    return APIResponse(code=0, message="success", data=data)
```

- [ ] **Step 4: Run permission tests**

```powershell
cd wechat_rag_bot
python -m pytest tests/test_admin_conversations.py -q
```

Expected: PASS.

---

### Task 5: Add Resolve, Release To AI, And Force Handoff

**Files:**
- Modify: `wechat_rag_bot/app/services/conversation_service.py`
- Modify: `wechat_rag_bot/app/routers/admin_conversations.py`
- Test: `wechat_rag_bot/tests/test_admin_conversations.py`

- [ ] **Step 1: Add tests for state transitions**

Add tests that assert:

```python
def test_force_handoff_allows_operator_to_claim_ai_conversation(monkeypatch, tmp_path):
    _reset_settings(monkeypatch, tmp_path)
    client = TestClient(app)
    client.post("/api/v1/chat", json={"channel": "api", "user_id": "user_001", "session_id": "sess_001", "message": "你好", "kb_id": "kb_default", "metadata": {}})

    force = client.post("/api/v1/admin/conversations/api:user_001:sess_001/force-handoff", json={"operator_id": "lead_001", "reason": "AI 可能误答"})
    claim = client.post("/api/v1/admin/conversations/api:user_001:sess_001/claim", json={"operator_id": "op_001"})

    assert force.status_code == 200
    assert force.json()["data"]["status"] == "handoff_pending"
    assert claim.status_code == 200
```

- [ ] **Step 2: Implement transitions**

Add:

```python
async def force_handoff(conversation_id: str, operator_id: str, reason: str | None) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status == RESOLVED:
            raise AppError(ErrorCode.REQUEST_INVALID, message="已结束会话不能强制转人工", status_code=409)
        conversation.status = HANDOFF_PENDING
        conversation.owner_id = None
        conversation.handoff_reason = reason or "manual_force_handoff"
        conversation.handoff_ticket_id = conversation.handoff_ticket_id or generate_id("handoff")
        conversation.updated_at = _now()
        session.commit()
        return _conversation_to_dict(conversation)


async def release_to_ai(conversation_id: str, operator_id: str) -> dict:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(ErrorCode.REQUEST_INVALID, message="只有当前接管人可以交回 AI", status_code=409)
        conversation.status = AI_ACTIVE
        conversation.owner_id = None
        conversation.updated_at = _now()
        session.commit()
        return _conversation_to_dict(conversation)


async def resolve_conversation(conversation_id: str, operator_id: str, reason: str | None) -> dict:
    del operator_id
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        conversation.status = RESOLVED
        conversation.owner_id = None
        conversation.handoff_reason = reason or conversation.handoff_reason
        conversation.updated_at = _now()
        session.commit()
        return _conversation_to_dict(conversation)
```

- [ ] **Step 3: Add routes**

Add routes:

```python
@router.post("/{conversation_id:path}/force-handoff", response_model=APIResponse)
async def force_handoff_route(conversation_id: str, request: StatusActionRequest) -> APIResponse:
    data = await force_handoff(conversation_id, request.operator_id, request.reason)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/release-to-ai", response_model=APIResponse)
async def release_to_ai_route(conversation_id: str, request: StatusActionRequest) -> APIResponse:
    data = await release_to_ai(conversation_id, request.operator_id)
    return APIResponse(code=0, message="success", data=data)


@router.post("/{conversation_id:path}/resolve", response_model=APIResponse)
async def resolve_route(conversation_id: str, request: StatusActionRequest) -> APIResponse:
    data = await resolve_conversation(conversation_id, request.operator_id, request.reason)
    return APIResponse(code=0, message="success", data=data)
```

- [ ] **Step 4: Run tests**

```powershell
cd wechat_rag_bot
python -m pytest tests/test_admin_conversations.py tests/test_chat_logs.py -q
```

Expected: PASS.

---

### Task 6: Frontend Workbench API Client

**Files:**
- Create: `admin-web/src/api/admin/conversations.ts`

- [ ] **Step 1: Add typed API client**

Create:

```ts
import request from '@/config/axios'

export type ConversationStatus =
  | 'ai_active'
  | 'ai_waiting'
  | 'handoff_pending'
  | 'human_active'
  | 'resolved'

export interface ConversationItem {
  conversation_id: string
  channel: string
  user_id: string
  session_id?: string | null
  tenant_id: string
  status: ConversationStatus
  owner_id?: string | null
  last_message?: string | null
  last_route?: string | null
  last_intent?: string | null
  handoff_reason?: string | null
  handoff_ticket_id?: string | null
  unread_count: number
  created_at: string
  updated_at: string
}

export interface ConversationMessage {
  id: number
  conversation_id: string
  sender_type: 'customer' | 'ai' | 'human' | 'system'
  sender_id?: string | null
  content: string
  route?: string | null
  primary_intent?: string | null
  metadata: Record<string, unknown>
  created_at: string
}

export const getConversations = (params: {
  page: number
  page_size: number
  status?: string
  owner_id?: string
  keyword?: string
}) => request.get({ url: '/api/v1/admin/conversations', params })

export const getConversationDetail = (conversationId: string) =>
  request.get({ url: `/api/v1/admin/conversations/${conversationId}` })

export const claimConversation = (conversationId: string, operator_id: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/claim`, data: { operator_id } })

export const replyConversation = (conversationId: string, operator_id: string, content: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/reply`, data: { operator_id, content } })

export const forceHandoff = (conversationId: string, operator_id: string, reason: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/force-handoff`, data: { operator_id, reason } })

export const releaseToAi = (conversationId: string, operator_id: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/release-to-ai`, data: { operator_id } })

export const resolveConversation = (conversationId: string, operator_id: string, reason?: string) =>
  request.post({ url: `/api/v1/admin/conversations/${conversationId}/resolve`, data: { operator_id, reason } })
```

- [ ] **Step 2: Type check**

```powershell
cd admin-web
pnpm ts:check
```

Expected: PASS.

---

### Task 7: Frontend IM-Style Supervision Workbench

**Files:**
- Create: `admin-web/src/views/workbench/index.vue`
- Create: `admin-web/src/views/workbench/components/ConversationList.vue`
- Create: `admin-web/src/views/workbench/components/MessagePanel.vue`
- Create: `admin-web/src/views/workbench/components/SupervisionPanel.vue`
- Create: `admin-web/src/views/workbench/components/ReplyComposer.vue`
- Modify: `admin-web/src/router/modules/admin.ts`

- [ ] **Step 1: Add workbench route first**

In `admin-web/src/router/modules/admin.ts`, make `/workbench` the first visible route:

```ts
{
  path: '/workbench',
  component: Layout,
  name: 'WorkbenchRoot',
  meta: { title: '客服工作台', icon: 'ep:service' },
  children: [
    {
      path: '',
      component: () => import('@/views/workbench/index.vue'),
      name: 'Workbench',
      meta: { title: '客服工作台', icon: 'ep:service', noCache: true }
    }
  ]
}
```

- [ ] **Step 2: Implement layout**

Create `index.vue` with a three-column layout:

```vue
<template>
  <div class="workbench">
    <ConversationList class="panel list" @select="selectConversation" />
    <MessagePanel class="panel messages" :conversation-id="selectedId" />
    <SupervisionPanel class="panel side" :conversation-id="selectedId" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import ConversationList from './components/ConversationList.vue'
import MessagePanel from './components/MessagePanel.vue'
import SupervisionPanel from './components/SupervisionPanel.vue'

const selectedId = ref('')
const selectConversation = (id: string) => {
  selectedId.value = id
}
</script>

<style scoped>
.workbench {
  display: grid;
  grid-template-columns: 320px minmax(420px, 1fr) 360px;
  gap: 12px;
  height: calc(100vh - 96px);
  padding: 12px;
  background: #f5f7fb;
}

.panel {
  min-height: 0;
  overflow: hidden;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
}
</style>
```

- [ ] **Step 3: Reply composer must be permission-gated**

`ReplyComposer.vue` must follow this rule:

```vue
<template>
  <div class="composer">
    <ElAlert
      v-if="status === 'ai_active' || status === 'ai_waiting'"
      title="当前由 AI 自动回复，人工仅可监控"
      type="info"
      :closable="false"
    />
    <ElButton v-else-if="status === 'handoff_pending'" type="primary" @click="$emit('claim')">
      领取接管
    </ElButton>
    <template v-else-if="status === 'human_active'">
      <ElInput v-model="content" type="textarea" :rows="3" placeholder="输入人工回复" />
      <ElButton type="primary" :disabled="!content.trim()" @click="send">发送</ElButton>
    </template>
    <ElAlert v-else title="会话已结束，仅可查看" type="warning" :closable="false" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

defineProps<{ status: string }>()
const emit = defineEmits<{ claim: []; send: [content: string] }>()
const content = ref('')
const send = () => {
  const value = content.value.trim()
  if (!value) return
  emit('send', value)
  content.value = ''
}
</script>
```

- [ ] **Step 4: Verify**

Run:

```powershell
cd admin-web
pnpm ts:check
pnpm build:local
```

Expected: PASS.

Manual checks:

- AI conversations show no text input.
- Handoff pending conversations show `领取接管`.
- Human active conversations show text input.
- Resolved conversations show read-only state.

---

## Verification

Backend narrow test:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_admin_conversations.py -q
```

Backend regression:

```powershell
cd wechat_rag_bot
python -m pytest tests/test_chat_logs.py tests/test_handoff_fallbacks.py tests/test_chat_api.py -q
```

Frontend:

```powershell
cd admin-web
pnpm ts:check
pnpm build:local
```

End-to-end smoke:

1. Send normal chat message through `/api/v1/chat`.
2. Open workbench and confirm conversation status is `ai_waiting` or `ai_active`.
3. Confirm no reply input appears.
4. Send “我要转人工” through `/api/v1/chat`.
5. Confirm workbench status becomes `handoff_pending`.
6. Claim conversation.
7. Confirm status becomes `human_active`.
8. Send human reply.
9. Confirm reply appears in message timeline.
10. Release to AI or resolve.

---

## Execution Choice

Plan complete. Recommended execution order:

1. Finish the admin-web pruning plan first, through the minimal running shell.
2. Implement backend Tasks 1-5.
3. Implement frontend Tasks 6-7.
4. Only after this works, add real channel delivery for human replies to WeChat/Enterprise WeChat.
