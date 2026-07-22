# Eyun Risk Control Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add risk-control delivery for Eyun WeChat callbacks: debounce multi-message user input, randomize reply timing, and serialize outbound sending under rate limits.

**Architecture:** Keep `/wechat/callback` fast by writing inbound messages into a DB-backed pending queue and returning `{"code":"1000"}` immediately. A single background worker polls due inbound batches, calls the existing AI pipeline once per batch, enqueues outbound replies, then a sender worker sends replies through Eyun `sendText` with random delay and per-account spacing.

**Tech Stack:** FastAPI BackgroundTasks/lifespan, SQLAlchemy models, existing SQLite-compatible database layer, pytest/TestClient, existing `httpx` Eyun client.

---

## File Structure

- Modify: `wechat_rag_bot/app/db/models.py`
  - Add DB models for inbound debounce batches, inbound message dedupe, outbound queue, and send-rate state.
- Create: `wechat_rag_bot/app/services/message_risk_control_service.py`
  - Own all queueing, debounce, rate-limit, random delay, and worker helper logic.
- Modify: `wechat_rag_bot/app/services/eyun_callback_service.py`
  - Replace direct `handle_chat(...)` + `send_eyun_text(...)` with enqueue-only behavior for normal text messages.
  - Keep `send_eyun_text(...)` as the final outbound transport.
- Modify: `wechat_rag_bot/app/main.py`
  - Start/stop lightweight polling workers during app lifecycle.
- Modify: `wechat_rag_bot/app/config.py`
  - Add configurable debounce/rate-limit knobs with safe defaults.
- Create: `wechat_rag_bot/tests/test_message_risk_control.py`
  - TDD coverage for debounce merging, dedupe, random send timing, and account spacing.
- Modify: `wechat_rag_bot/tests/test_eyun_callback.py`
  - Update callback tests to assert enqueue behavior rather than immediate AI/sendText.

## Risk-Control Rules

- Callback fast return: `/wechat/callback` and `/eyun/callback` must return Eyun success immediately after validating/enqueuing.
- Inbound debounce: for the same conversation key, wait 60 seconds after the latest user message before AI processing.
- Multi-message merge: if more messages arrive during the 60-second window, append them into the same pending batch and push `due_at` forward another 60 seconds.
- Idempotency: if Eyun retries the same `newMsgId` or `msgId`, do not append or process it twice.
- Outbound queue: AI replies must enter a queue; never call `sendText` concurrently from multiple callback tasks.
- Single account rate: default to at most 40 messages per minute per `wId`, implemented as a conservative minimum interval of 1.6 seconds between sends from the same account.
- Random reply delay: add jitter before outbound sending so replies do not happen at a fixed offset. Recommended default: 2-12 seconds after AI reply is ready.
- Different users/groups: keep at least 1 second between any two sends, and use 2-5 seconds random spacing when rotating targets.
- Resolved/handoff state: if a conversation is in `human_active` or `resolved`, do not auto-send AI replies; keep existing supervised handoff rules authoritative.

---

### Task 1: Add Configuration Knobs

**Files:**
- Modify: `wechat_rag_bot/app/config.py`
- Test: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Write the failing config test**

```python
from app.config import get_settings


def test_risk_control_defaults(monkeypatch):
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.eyun_inbound_debounce_seconds == 60
    assert settings.eyun_send_max_per_minute == 40
    assert settings.eyun_send_min_interval_seconds == 1.6
    assert settings.eyun_reply_jitter_min_seconds == 2
    assert settings.eyun_reply_jitter_max_seconds == 12
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
cd D:/Codex产品开发/智能客服/wechat_rag_bot
python -m pytest tests/test_message_risk_control.py::test_risk_control_defaults -q
```

Expected: FAIL because the settings fields do not exist.

- [ ] **Step 3: Add settings**

Add these fields to the existing settings class in `app/config.py`:

```python
eyun_inbound_debounce_seconds: int = Field(default=60, alias="EYUN_INBOUND_DEBOUNCE_SECONDS")
eyun_send_max_per_minute: int = Field(default=40, alias="EYUN_SEND_MAX_PER_MINUTE")
eyun_send_min_interval_seconds: float = Field(default=1.6, alias="EYUN_SEND_MIN_INTERVAL_SECONDS")
eyun_reply_jitter_min_seconds: int = Field(default=2, alias="EYUN_REPLY_JITTER_MIN_SECONDS")
eyun_reply_jitter_max_seconds: int = Field(default=12, alias="EYUN_REPLY_JITTER_MAX_SECONDS")
eyun_worker_poll_seconds: float = Field(default=1.0, alias="EYUN_WORKER_POLL_SECONDS")
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_risk_control_defaults -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/config.py wechat_rag_bot/tests/test_message_risk_control.py
git commit -m "feat: add eyun risk control settings"
```

---

### Task 2: Add Queue Models

**Files:**
- Modify: `wechat_rag_bot/app/db/models.py`
- Test: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Write failing model test**

```python
from app.db.models import EyunInboundBatchModel, EyunInboundMessageModel, EyunOutboundMessageModel, EyunSendRateModel


def test_risk_control_models_have_table_names():
    assert EyunInboundBatchModel.__tablename__ == "eyun_inbound_batches"
    assert EyunInboundMessageModel.__tablename__ == "eyun_inbound_messages"
    assert EyunOutboundMessageModel.__tablename__ == "eyun_outbound_messages"
    assert EyunSendRateModel.__tablename__ == "eyun_send_rates"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_risk_control_models_have_table_names -q
```

Expected: FAIL because the models do not exist.

- [ ] **Step 3: Add models**

Add models with these responsibilities:

```python
class EyunInboundBatchModel(Base):
    __tablename__ = "eyun_inbound_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    w_id: Mapped[str | None] = mapped_column(String(256), index=True, nullable=True)
    wc_id: Mapped[str] = mapped_column(String(256), index=True)
    target_wc_id: Mapped[str] = mapped_column(String(256), index=True)
    from_user: Mapped[str | None] = mapped_column(String(256), nullable=True)
    from_group: Mapped[str | None] = mapped_column(String(256), nullable=True)
    account: Mapped[str | None] = mapped_column(String(256), nullable=True)
    message_type: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunInboundMessageModel(Base):
    __tablename__ = "eyun_inbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_message_id: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    batch_key: Mapped[str] = mapped_column(String(512), index=True)
    content: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunOutboundMessageModel(Base):
    __tablename__ = "eyun_outbound_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    w_id: Mapped[str] = mapped_column(String(256), index=True)
    wc_id: Mapped[str] = mapped_column(String(256), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_batch_key: Mapped[str | None] = mapped_column(String(512), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EyunSendRateModel(Base):
    __tablename__ = "eyun_send_rates"

    w_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

- [ ] **Step 4: Run model test**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_risk_control_models_have_table_names -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/db/models.py wechat_rag_bot/tests/test_message_risk_control.py
git commit -m "feat: add eyun risk control queue models"
```

---

### Task 3: Debounce and Merge Inbound Messages

**Files:**
- Create: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Test: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Write failing debounce tests**

```python
from datetime import datetime, timedelta, timezone

from app.services.message_risk_control_service import build_eyun_batch_key, enqueue_eyun_inbound


def test_build_eyun_batch_key_prefers_group():
    key = build_eyun_batch_key(w_id="wid", target_wc_id="group_a", from_user="user_a")
    assert key == "wid:group_a"


async def test_enqueue_eyun_inbound_merges_same_user_window(monkeypatch):
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)

    first = await enqueue_eyun_inbound({
        "account": "acct",
        "messageType": "60001",
        "wcId": "bot",
        "data": {
            "wId": "wid",
            "fromUser": "user",
            "content": "第一条",
            "newMsgId": 1,
        },
    })

    monkeypatch.setattr(
        "app.services.message_risk_control_service.utcnow",
        lambda: now + timedelta(seconds=30),
    )
    second = await enqueue_eyun_inbound({
        "account": "acct",
        "messageType": "60001",
        "wcId": "bot",
        "data": {
            "wId": "wid",
            "fromUser": "user",
            "content": "第二条",
            "newMsgId": 2,
        },
    })

    assert first["batch_key"] == second["batch_key"]
    assert second["content"] == "第一条\n第二条"
    assert second["message_count"] == 2
    assert second["due_at"] == now + timedelta(seconds=90)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_build_eyun_batch_key_prefers_group tests/test_message_risk_control.py::test_enqueue_eyun_inbound_merges_same_user_window -q
```

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement enqueue service**

Implement these functions:

```python
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_eyun_batch_key(*, w_id: str, target_wc_id: str, from_user: str) -> str:
    return f"{w_id}:{target_wc_id or from_user}"


async def enqueue_eyun_inbound(payload: dict[str, Any]) -> dict[str, Any]:
    # Parse Eyun fields.
    # Dedupe by newMsgId first, msgId second.
    # If duplicate exists, return the existing batch without appending.
    # If pending batch exists, append content and update due_at = now + debounce.
    # If no pending batch exists, create one.
```

Implementation detail: use the existing SQLAlchemy session helper pattern used by `conversation_service.py`; call `Base.metadata.create_all(...)` in the same place existing lightweight models are created if migrations are not present.

- [ ] **Step 4: Run debounce tests**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py -q
```

Expected: PASS for debounce tests.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/services/message_risk_control_service.py wechat_rag_bot/tests/test_message_risk_control.py
git commit -m "feat: debounce eyun inbound messages"
```

---

### Task 4: Change Eyun Callback to Enqueue Only

**Files:**
- Modify: `wechat_rag_bot/app/services/eyun_callback_service.py`
- Modify: `wechat_rag_bot/tests/test_eyun_callback.py`

- [ ] **Step 1: Write failing callback test**

```python
def test_wechat_callback_enqueues_eyun_text_payload(monkeypatch):
    from app.services import eyun_callback_service

    enqueued = []

    async def fake_enqueue(payload):
        enqueued.append(payload)
        return {"batch_key": "wid:user"}

    monkeypatch.setattr(eyun_callback_service, "enqueue_eyun_inbound", fake_enqueue)

    client = TestClient(app)
    response = client.post(
        "/wechat/callback",
        json={
            "account": "test_account",
            "messageType": "60001",
            "wcId": "wxid_bot",
            "data": {
                "wId": "wid_test",
                "fromUser": "wxid_customer",
                "content": "hello",
                "newMsgId": 123,
                "self": False,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["code"] == "1000"
    assert len(enqueued) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_eyun_callback.py::test_wechat_callback_enqueues_eyun_text_payload -q
```

Expected: FAIL because callback still calls AI/sendText directly.

- [ ] **Step 3: Replace direct processing with enqueue**

In `handle_eyun_callback(payload)`, keep filters for test callback, self messages, unsupported message types, and empty content. For processable text:

```python
await enqueue_eyun_inbound(payload)
return eyun_success()
```

Do not call `handle_chat(...)` or `send_eyun_text(...)` from the callback path.

- [ ] **Step 4: Run callback tests**

Run:

```powershell
python -m pytest tests/test_eyun_callback.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/services/eyun_callback_service.py wechat_rag_bot/tests/test_eyun_callback.py
git commit -m "feat: enqueue eyun callbacks for delayed processing"
```

---

### Task 5: Process Due Inbound Batches Once

**Files:**
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Test: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Write failing process test**

```python
async def test_process_due_batch_calls_ai_once_for_merged_content(monkeypatch):
    calls = []
    outbound = []

    async def fake_handle_chat(request):
        calls.append(request)
        return {"answer": "AI合并回复"}

    async def fake_enqueue_outbound(*, w_id, wc_id, content, source_batch_key):
        outbound.append({
            "w_id": w_id,
            "wc_id": wc_id,
            "content": content,
            "source_batch_key": source_batch_key,
        })

    monkeypatch.setattr("app.services.message_risk_control_service.handle_chat", fake_handle_chat)
    monkeypatch.setattr("app.services.message_risk_control_service.enqueue_eyun_outbound", fake_enqueue_outbound)

    # Create one due batch with content "第一条\n第二条".
    # Call process_due_eyun_inbound_batches(limit=10).

    assert len(calls) == 1
    assert calls[0].message == "第一条\n第二条"
    assert outbound[0]["content"] == "AI合并回复"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_process_due_batch_calls_ai_once_for_merged_content -q
```

Expected: FAIL because processor does not exist.

- [ ] **Step 3: Implement processor**

Implement:

```python
async def process_due_eyun_inbound_batches(limit: int = 10) -> int:
    # Select pending batches where due_at <= now.
    # Mark selected rows as processing before calling AI.
    # Skip auto AI when conversation status is human_active or resolved.
    # Call handle_chat once with merged content.
    # If answer exists, enqueue outbound message.
    # Mark batch processed or failed with error.
    # Return number of batches attempted.
```

- [ ] **Step 4: Run test**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_process_due_batch_calls_ai_once_for_merged_content -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/services/message_risk_control_service.py wechat_rag_bot/tests/test_message_risk_control.py
git commit -m "feat: process debounced eyun inbound batches"
```

---

### Task 6: Queue and Rate-Limit Outbound Sends

**Files:**
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Test: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Write failing outbound timing tests**

```python
async def test_enqueue_outbound_adds_random_due_at(monkeypatch):
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.services.message_risk_control_service.utcnow", lambda: now)
    monkeypatch.setattr("app.services.message_risk_control_service.random_reply_delay_seconds", lambda: 7)

    row = await enqueue_eyun_outbound(
        w_id="wid",
        wc_id="user",
        content="reply",
        source_batch_key="wid:user",
    )

    assert row["due_at"] == now + timedelta(seconds=7)
    assert row["status"] == "queued"


async def test_send_worker_respects_account_min_interval(monkeypatch):
    sent = []

    async def fake_send_eyun_text(*, w_id, wc_id, content):
        sent.append((w_id, wc_id, content))

    monkeypatch.setattr("app.services.message_risk_control_service.send_eyun_text", fake_send_eyun_text)

    # Seed two due outbound messages for same w_id.
    # Seed EyunSendRateModel.last_sent_at less than 1.6 seconds ago.
    attempted = await process_due_eyun_outbound_messages(limit=10)

    assert attempted == 0
    assert sent == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_enqueue_outbound_adds_random_due_at tests/test_message_risk_control.py::test_send_worker_respects_account_min_interval -q
```

Expected: FAIL because outbound queue does not exist.

- [ ] **Step 3: Implement outbound queue**

Implement:

```python
def random_reply_delay_seconds() -> int:
    settings = get_settings()
    return random.randint(
        settings.eyun_reply_jitter_min_seconds,
        settings.eyun_reply_jitter_max_seconds,
    )


async def enqueue_eyun_outbound(*, w_id: str, wc_id: str, content: str, source_batch_key: str | None) -> dict[str, Any]:
    # Create queued row with due_at = now + random_reply_delay_seconds().


async def process_due_eyun_outbound_messages(limit: int = 5) -> int:
    # Select due queued messages ordered by due_at/id.
    # For each row, check EyunSendRateModel for w_id.
    # If last_sent_at is too recent, push due_at forward to allowed time plus 0-1s jitter.
    # Send one message at a time using send_eyun_text.
    # Mark sent on success; increment attempts and retry later on failure.
```

- [ ] **Step 4: Run outbound tests**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/services/message_risk_control_service.py wechat_rag_bot/tests/test_message_risk_control.py
git commit -m "feat: rate limit eyun outbound replies"
```

---

### Task 7: Start Background Workers

**Files:**
- Modify: `wechat_rag_bot/app/main.py`
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Test: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Write worker loop unit test**

```python
async def test_worker_tick_processes_inbound_then_outbound(monkeypatch):
    calls = []

    async def fake_inbound(limit=10):
        calls.append("inbound")
        return 1

    async def fake_outbound(limit=5):
        calls.append("outbound")
        return 1

    monkeypatch.setattr("app.services.message_risk_control_service.process_due_eyun_inbound_batches", fake_inbound)
    monkeypatch.setattr("app.services.message_risk_control_service.process_due_eyun_outbound_messages", fake_outbound)

    await eyun_worker_tick()

    assert calls == ["inbound", "outbound"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_worker_tick_processes_inbound_then_outbound -q
```

Expected: FAIL because `eyun_worker_tick` does not exist.

- [ ] **Step 3: Implement worker tick and lifecycle**

In service:

```python
async def eyun_worker_tick() -> None:
    await process_due_eyun_inbound_batches(limit=10)
    await process_due_eyun_outbound_messages(limit=5)
```

In `app/main.py`, start one task on startup:

```python
async def eyun_risk_control_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        await eyun_worker_tick()
        await asyncio.sleep(settings.eyun_worker_poll_seconds)
```

Keep the task single-instance per process. Do not start a worker inside every request.

- [ ] **Step 4: Run worker test**

Run:

```powershell
python -m pytest tests/test_message_risk_control.py::test_worker_tick_processes_inbound_then_outbound -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add wechat_rag_bot/app/main.py wechat_rag_bot/app/services/message_risk_control_service.py wechat_rag_bot/tests/test_message_risk_control.py
git commit -m "feat: run eyun risk control worker"
```

---

### Task 8: Full Regression

**Files:**
- Test only.

- [ ] **Step 1: Run narrow new tests**

```powershell
cd D:/Codex产品开发/智能客服/wechat_rag_bot
python -m pytest tests/test_message_risk_control.py tests/test_eyun_callback.py -q
```

Expected: PASS.

- [ ] **Step 2: Run existing required regression**

```powershell
python -m pytest tests/test_admin_conversations.py tests/test_wechat.py tests/test_eyun_callback.py tests/test_chat_logs.py tests/test_handoff_fallbacks.py tests/test_chat_api.py -q
```

Expected: PASS.

- [ ] **Step 3: Deploy and verify online callback stays fast**

```powershell
$body = @{
  wcId = "wxid_test"
  account = "test"
  messageType = "00000"
  data = @{}
} | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "https://intelligent-customer-service-r63g.onrender.com/wechat/callback" -ContentType "application/json" -Body $body
```

Expected:

```json
{"code":"1000","message":"success","data":null}
```

- [ ] **Step 4: Commit final verification notes**

```powershell
git status --short
```

Expected: only intentional changes are present.

---

## Production Notes

- Render Free + local SQLite is not durable. For real online use, move `DATABASE_URL` to Render Postgres before relying on delayed queues.
- Keep Render service instance count at 1 while using SQLite or the lightweight DB worker. Multi-instance deployment needs row-level locking or Redis/Celery.
- Do not use `await asyncio.sleep(60)` inside `/wechat/callback`; it would violate the 6-second callback requirement and create unstable request concurrency.
- Add an admin view later for queued/failed outbound messages if manual recovery becomes necessary.
- If the account sends marketing bursts, reduce `EYUN_SEND_MAX_PER_MINUTE` below 40, for example 25-30/minute, to leave safety margin.

## Self-Review

- Spec coverage: frequency limit, random reply time, 1-minute multi-message debounce, fast callback return, and queue-based sending are covered.
- Placeholder scan: no implementation step relies on undefined business behavior; code snippets define required function names and status values.
- Type consistency: `batch_key`, `w_id`, `wc_id`, `due_at`, and queue statuses are consistent across models and services.
