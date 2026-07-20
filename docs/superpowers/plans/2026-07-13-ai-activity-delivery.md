# AI Activity Selection and Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow AI to select at most one currently valid activity package for a matching private WeChat conversation and enqueue its original text/image/video messages without modifying their content or order.

**Architecture:** Activities remain a structured business-action catalog in SQLite rather than becoming ordinary RAG documents. A deterministic eligibility layer filters activities, a constrained LLM selector may choose one activity ID, and the Eyun inbound worker revalidates and dispatches the selected package through the existing outbound risk-control queue. A global feature flag and shadow mode separate decision rollout from real sending.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy/SQLite, LangGraph chat workflow, existing OpenAI-compatible LLM client, Vue 3, Element Plus, pytest.

---

## Technical decisions

### End-to-end flow

```text
Eyun private inbound batch (second and later messages)
  -> existing human/group/status guard
  -> intent + customer tags + sales stage
  -> deterministic activity candidate filter
  -> constrained LLM selector returns one activity_id or null
  -> normal customer-service reply is generated
  -> inbound worker reads activity decision
  -> server revalidates activity and cooldown
  -> normal reply is queued first
  -> original activity package is queued in stored order
  -> AI decision and activity send log are persisted
```

### AI rule contract

`activities.ai_rules_json` keeps the existing storage column but receives a typed contract:

```json
{
  "trigger_description": "用户明确询问七月会员活动或当前优惠时发送",
  "intent_ids": ["promotion_inquiry"],
  "sales_stages": ["consideration", "conversion"],
  "required_tags": ["orchid_customer"],
  "excluded_tags": ["complaint", "refund_pending"],
  "keywords": ["活动", "优惠", "会员"],
  "min_confidence": 0.85,
  "cooldown_hours": 168,
  "max_sends_per_user": 1,
  "priority": 100
}
```

Rules have these exact semantics:

- `excluded_tags`: any match rejects the candidate.
- `intent_ids` and `sales_stages`: when non-empty, the current value must be included.
- `required_tags`: when non-empty, every configured tag must exist on the customer.
- `keywords`: prefilter only; at least one keyword must occur when the list is non-empty.
- `trigger_description`: required before `ai_enabled` can be turned on and is the only free-text activity instruction sent to the selector.
- `priority`: higher values win only when selector confidence ties.
- `cooldown_hours` and `max_sends_per_user`: hard server-side delivery limits, never advisory prompt text.

### Selection contract

The selector receives no media XML, URLs, credentials, raw callback payloads, or internal database fields. It receives activity ID, title, summary, trigger description, current message, resolved intent, sales stage, and customer tag names. Its output is validated as:

```python
class ActivitySelectionDecision(BaseModel):
    activity_id: int | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=500)
```

An unknown ID, malformed JSON, timeout, confidence below the activity threshold, or empty candidate set means “send nothing.” The normal customer-service reply must continue.

### Rollout controls

```dotenv
ACTIVITY_AI_ENABLED=false
ACTIVITY_AI_SHADOW_MODE=true
ACTIVITY_AI_MAX_CANDIDATES=5
```

- Disabled: no candidate query and no selector call.
- Shadow mode: select and log, but never enqueue an activity.
- Live mode: select, revalidate, deduplicate, and enqueue.

## Scope boundaries

### Included

- Reactive private WeChat conversations only.
- Selection begins from the second inbound message because the existing first-message opening flow bypasses `handle_chat`.
- At most one activity package per inbound batch.
- Exact stored text/image/video content and exact stored order.
- Existing activity total switch, AI switch, validity window, archive state, and outbound risk-control queue.
- Intent, sales stage, customer tags, keywords, cooldown, per-user cap, confidence, and priority rules.
- Shadow decisions, live decisions, no-send reasons, and send logs.
- Fail-closed behavior: activity failures never block the normal AI reply.

### Excluded

- Proactive scheduled or bulk marketing without a customer inbound message.
- Group chats, human-controlled conversations, and resolved conversations.
- More than one activity in a single turn.
- Letting the model rewrite, summarize, reorder, merge, or invent activity messages.
- Indexing raw activity packages into Qdrant or treating them as ordinary RAG knowledge.
- Cross-channel delivery beyond Eyun/WeChat.
- Automatic learning from conversion results.
- Provider-level exactly-once delivery after an ambiguous network timeout; existing queue retry semantics remain.
- Changing the existing manual preview-and-send flow.

## File map

- Modify `wechat_rag_bot/app/config.py`: global enable, shadow, and candidate limit settings.
- Modify `wechat_rag_bot/app/schemas/activity.py`: typed AI rules and decision contracts.
- Modify `wechat_rag_bot/app/db/models.py`: AI decision audit table.
- Modify `wechat_rag_bot/app/services/activity_service.py`: eligibility, cooldown, revalidation, and AI send-log helpers.
- Create `wechat_rag_bot/app/services/activity_selector_service.py`: prompt construction, LLM selection, output validation.
- Modify `wechat_rag_bot/app/services/llm_service.py`: `activity` model-purpose resolution with intent-model fallback.
- Modify `wechat_rag_bot/app/services/chat_orchestrator.py`: invoke selector after tags and sales stage are known.
- Modify `wechat_rag_bot/app/services/message_risk_control_service.py`: dispatch selected activity through the existing queue.
- Modify `admin-web/src/api/admin/activities.ts`: typed AI rule DTO.
- Modify `admin-web/src/views/current-activities/index.vue`: AI rule editor and validation.
- Create `wechat_rag_bot/tests/test_activity_ai_selection.py`: eligibility and selector tests.
- Create `wechat_rag_bot/tests/test_activity_ai_delivery.py`: queue, order, deduplication, and fail-closed tests.
- Modify `wechat_rag_bot/tests/test_message_risk_control.py`: inbound worker integration tests.
- Modify `wechat_rag_bot/docs/project_framework.md`: architecture and operational rollout.

### Task 1: Typed AI rules and global rollout settings

**Files:**
- Modify: `wechat_rag_bot/app/config.py`
- Modify: `wechat_rag_bot/app/schemas/activity.py`
- Test: `wechat_rag_bot/tests/test_activity_ai_selection.py`

- [ ] **Step 1: Write failing schema and configuration tests**

```python
from pydantic import ValidationError

from app.config import get_settings
from app.schemas.activity import ActivityAiRules


def test_activity_ai_rules_require_trigger_description():
    try:
        ActivityAiRules(trigger_description="")
    except ValidationError:
        return
    raise AssertionError("empty trigger_description must be rejected")


def test_activity_ai_rollout_defaults_are_safe(monkeypatch):
    monkeypatch.delenv("ACTIVITY_AI_ENABLED", raising=False)
    monkeypatch.delenv("ACTIVITY_AI_SHADOW_MODE", raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.activity_ai_enabled is False
    assert settings.activity_ai_shadow_mode is True
    assert settings.activity_ai_max_candidates == 5
```

- [ ] **Step 2: Run the tests and verify the missing types/settings fail**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: collection fails because `ActivityAiRules` and activity AI settings do not exist.

- [ ] **Step 3: Add the typed rule and decision contracts**

```python
class ActivityAiRules(BaseModel):
    trigger_description: str = Field(min_length=1, max_length=1000)
    intent_ids: list[str] = Field(default_factory=list)
    sales_stages: list[str] = Field(default_factory=list)
    required_tags: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    min_confidence: float = Field(default=0.85, ge=0.5, le=1)
    cooldown_hours: int = Field(default=168, ge=0, le=24 * 365)
    max_sends_per_user: int = Field(default=1, ge=1, le=100)
    priority: int = Field(default=100, ge=0, le=1000)


class ActivitySelectionDecision(BaseModel):
    activity_id: int | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=500)
```

Change `ActivityUpdateRequest.ai_rules` from `dict` to `ActivityAiRules | None`, preserving `{}` when AI is not configured. Reject `ai_enabled=true` in `update_activity_switches` unless stored rules validate.

- [ ] **Step 4: Add safe settings defaults**

```python
activity_ai_enabled: bool = False
activity_ai_shadow_mode: bool = True
activity_ai_max_candidates: int = Field(default=5, ge=1, le=20)
activity_llm_provider: str = ""
activity_llm_model: str = ""
```

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: all Task 1 tests pass.

Commit: `feat: define activity ai rules and rollout settings`

### Task 2: Deterministic eligibility and delivery limits

**Files:**
- Modify: `wechat_rag_bot/app/services/activity_service.py`
- Test: `wechat_rag_bot/tests/test_activity_ai_selection.py`

- [ ] **Step 1: Write failing candidate-filter tests**

Cover these independent cases with explicit activities and timestamps:

```python
def test_ai_candidates_require_all_hard_gates(...):
    candidates = list_ai_activity_candidates(
        message="现在有什么会员活动",
        intent_id="promotion_inquiry",
        sales_stage="consideration",
        customer_tags={"orchid_customer"},
        user_id="wxid_1",
        now=fixed_now,
    )
    assert [item.id for item in candidates] == [eligible_activity.id]


def test_ai_candidates_exclude_archived_disabled_expired_and_ai_off(...):
    assert list_ai_activity_candidates(...) == []


def test_ai_candidates_enforce_cooldown_and_per_user_cap(...):
    assert list_ai_activity_candidates(user_id="already_sent", ...) == []
```

- [ ] **Step 2: Verify the candidate tests fail**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: failure because `list_ai_activity_candidates` does not exist.

- [ ] **Step 3: Implement the candidate DTO and filter**

```python
@dataclass(frozen=True)
class ActivityAiCandidate:
    id: int
    title: str
    summary: str
    trigger_description: str
    min_confidence: float
    priority: int


def list_ai_activity_candidates(
    *,
    message: str,
    intent_id: str,
    sales_stage: str,
    customer_tags: set[str],
    user_id: str,
    now: datetime | None = None,
) -> list[ActivityAiCandidate]:
    """Return only hard-gated candidates, ordered by priority then updated_at."""
```

The SQL query must first require `published`, `enabled`, `ai_enabled`, and valid time. Python applies typed rules and queries `ActivitySendLogModel` with `trigger_mode="ai"` for cooldown and send-cap enforcement. Return no more than `activity_ai_max_candidates`.

- [ ] **Step 4: Run focused tests and commit**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: all eligibility, cooldown, and cap tests pass.

Commit: `feat: filter ai eligible activities`

### Task 3: Constrained AI activity selector

**Files:**
- Create: `wechat_rag_bot/app/services/activity_selector_service.py`
- Modify: `wechat_rag_bot/app/services/llm_service.py`
- Test: `wechat_rag_bot/tests/test_activity_ai_selection.py`

- [ ] **Step 1: Write failing selector tests**

```python
@pytest.mark.asyncio
async def test_selector_accepts_only_a_candidate_id(monkeypatch):
    async def fake_generate_json(prompt, purpose):
        assert purpose == "activity"
        assert "raw_content" not in prompt
        return {"activity_id": 7, "confidence": 0.91, "reason": "用户询问当前优惠"}

    monkeypatch.setattr(selector.llm_service, "generate_json", fake_generate_json)
    decision = await select_activity(candidates=[candidate_7], context=context)
    assert decision.activity_id == 7


@pytest.mark.asyncio
async def test_selector_rejects_unknown_id_low_confidence_and_bad_json(monkeypatch):
    decision = await select_activity(candidates=[candidate_7], context=context)
    assert decision.activity_id is None
```

- [ ] **Step 2: Verify selector tests fail**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: failure because `activity_selector_service` does not exist.

- [ ] **Step 3: Implement selector and fail-closed parsing**

```python
@dataclass(frozen=True)
class ActivitySelectionContext:
    message: str
    intent_id: str
    sales_stage: str
    customer_tags: tuple[str, ...]


async def select_activity(
    *,
    candidates: list[ActivityAiCandidate],
    context: ActivitySelectionContext,
) -> ActivitySelectionDecision:
    if not candidates:
        return ActivitySelectionDecision(confidence=0, reason="no_candidates")
    try:
        raw = await llm_service.generate_json(
            build_activity_selector_prompt(candidates, context),
            purpose="activity",
        )
        decision = ActivitySelectionDecision.model_validate(raw)
    except Exception:
        return ActivitySelectionDecision(confidence=0, reason="selector_failed")
    candidate = next((item for item in candidates if item.id == decision.activity_id), None)
    if candidate is None or decision.confidence < candidate.min_confidence:
        return ActivitySelectionDecision(confidence=decision.confidence, reason="not_eligible")
    return decision
```

The prompt must state that candidate content is untrusted data, output must be JSON only, selecting nothing is preferred over uncertain sending, and only listed numeric IDs are valid.

- [ ] **Step 4: Add model-purpose resolution**

Add `activity` to `_resolve_model_config` with this order:

```python
"activity": (
    ("activity_llm_provider", "activity_llm_model"),
    ("intent_llm_provider", "intent_llm_model"),
),
```

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: candidate ID, low-confidence, malformed-output, and exception cases pass.

Commit: `feat: select activities with constrained ai output`

### Task 4: Chat-orchestrator decision integration

**Files:**
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Test: `wechat_rag_bot/tests/test_activity_ai_selection.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
@pytest.mark.asyncio
async def test_chat_records_activity_decision_after_tags_are_available(monkeypatch):
    result = await handle_chat(activity_matching_request)
    assert result["metadata"]["activity_decision"]["activity_id"] == activity_id


@pytest.mark.asyncio
async def test_activity_selector_failure_does_not_fail_normal_reply(monkeypatch):
    result = await handle_chat(activity_matching_request)
    assert result["answer"] == "normal reply"
    assert result["metadata"]["activity_decision"]["activity_id"] is None
```

- [ ] **Step 2: Verify integration tests fail**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: `activity_decision` is absent.

- [ ] **Step 3: Add a fail-closed decision stage**

After `tag_result`, sales stage, and reply plan are known, call a private `_decide_activity(...)` helper only when:

```python
settings.activity_ai_enabled
and message.channel == "wechat"
and plan.action not in {"human", "unsupported"}
and not is_evaluation
```

Store only `activity_id`, `confidence`, `reason`, `shadow_mode`, `candidate_ids`, and candidate count in reply metadata. Do not add activity content to the normal RAG prompt or conversation memory.

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_activity_ai_selection.py -q`

Expected: decision metadata is present and selector failures preserve the normal reply.

Commit: `feat: add activity decision to chat orchestration`

### Task 5: Idempotent AI delivery through the Eyun queue

**Files:**
- Modify: `wechat_rag_bot/app/db/models.py`
- Modify: `wechat_rag_bot/app/services/activity_service.py`
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Create: `wechat_rag_bot/tests/test_activity_ai_delivery.py`
- Modify: `wechat_rag_bot/tests/test_message_risk_control.py`

- [ ] **Step 1: Write failing live-delivery tests**

```python
@pytest.mark.asyncio
async def test_ai_activity_queues_original_items_after_normal_reply(...):
    await process_due_eyun_inbound_batches(limit=5)
    queued = read_outbound_rows()
    assert [row.message_type for row in queued] == ["text", "text", "received_image", "received_video"]
    assert queued[1].content == stored_activity_text


@pytest.mark.asyncio
async def test_same_inbound_batch_cannot_enqueue_activity_twice(...):
    await process_activity_ai_decision(...)
    await process_activity_ai_decision(...)
    assert count_activity_outbound_rows() == activity_item_count


@pytest.mark.asyncio
async def test_shadow_mode_logs_without_queueing(...):
    await process_due_eyun_inbound_batches(limit=5)
    assert count_activity_outbound_rows() == 0
    assert latest_decision.status == "shadow_selected"
```

- [ ] **Step 2: Verify delivery tests fail**

Run: `pytest tests/test_activity_ai_delivery.py tests/test_message_risk_control.py -q`

Expected: no AI activity rows or decision audit records exist.

- [ ] **Step 3: Add decision audit model**

```python
class ActivityAiDecisionModel(Base):
    __tablename__ = "activity_ai_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    processing_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    trace_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(String(256), index=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    activity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    candidate_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

Create the table through the existing scoped `Base.metadata.create_all` calls. The unique `processing_key` is the decision-level idempotency gate. Build it from the inbound batch database ID and the timestamp written when the batch enters `processing`, for example `eyun-inbound:{batch_id}:{processing_updated_at}`. Re-entering the same processing attempt therefore reuses the same key; a later customer message receives a new processing timestamp even though the conversation-level `batch_key` is reused.

- [ ] **Step 4: Implement dispatch with a second hard validation**

```python
async def process_activity_ai_decision(
    *,
    activity_id: int | None,
    candidate_ids: list[int],
    processing_key: str,
    conversation_id: str,
    user_id: str,
    w_id: str,
    wc_id: str,
    trace_id: str | None,
    confidence: float,
    reason: str,
    shadow_mode: bool,
) -> dict:
    """Persist the decision; in live mode revalidate, enqueue stored items, and write an AI send log."""
```

Reserve the decision key before any outbound insert. A null selection records `rejected`; shadow mode records `shadow_selected` and returns without queueing. In live mode, reload the activity and recheck published/enabled/AI-enabled/time/rules/cooldown/cap. Each item uses `activity:{activity_id}:{processing_key}:{position}` as its outbound source key; if a row with that exact key exists, reuse its ID instead of inserting again. Write `ActivitySendLogModel(trigger_mode="ai", operator_id=None)` only after every item has an outbound ID.

- [ ] **Step 5: Integrate the inbound worker**

Queue the existing normal AI reply first. If metadata contains a selected activity:

```python
if decision:
    await process_activity_ai_decision(
        activity_id=decision["activity_id"],
        candidate_ids=decision["candidate_ids"],
        processing_key=batch_data["processing_key"],
        conversation_id=make_conversation_id("wechat", target_user, None),
        user_id=target_user,
        w_id=batch_data["w_id"],
        wc_id=batch_data["target_wc_id"],
        trace_id=chat_result.get("trace_id"),
        confidence=decision["confidence"],
        reason=decision["reason"],
        shadow_mode=decision["shadow_mode"],
    )
```

Expose `processing_key` in `_inbound_batch_to_dict` as `eyun-inbound:{batch.id}:{batch.updated_at.isoformat()}` after the batch has entered `processing`. Activity decision exceptions must be caught separately, logged as `failed`, and must not mark an otherwise successful inbound batch as failed.

- [ ] **Step 6: Run tests and commit**

Run: `pytest tests/test_activity_ai_delivery.py tests/test_activity_delivery.py tests/test_message_risk_control.py -q`

Expected: order, shadow mode, deduplication, hard revalidation, and manual-delivery regression tests pass.

Commit: `feat: dispatch ai selected activities through risk queue`

### Task 6: AI rule editor in the activity page

**Files:**
- Modify: `admin-web/src/api/admin/activities.ts`
- Modify: `admin-web/src/views/current-activities/index.vue`

- [ ] **Step 1: Define the exact frontend DTO**

```typescript
export interface ActivityAiRules {
  trigger_description: string
  intent_ids: string[]
  sales_stages: string[]
  required_tags: string[]
  excluded_tags: string[]
  keywords: string[]
  min_confidence: number
  cooldown_hours: number
  max_sends_per_user: number
  priority: number
}
```

- [ ] **Step 2: Add form validation before enabling AI**

The activity edit dialog must expose every rule field. The AI switch must refuse to enable when `trigger_description` is blank, display `请先填写 AI 适用场景`, restore the switch to false, and avoid sending the switch API call.

- [ ] **Step 3: Add explicit operational copy**

Display these fixed explanations beside the AI switch:

```text
AI 只会选择活动，不会修改活动内容。
活动仅在客户发来消息后触发；不会主动群发。
关闭、归档、过期或达到发送上限时不会发送。
```

- [ ] **Step 4: Run frontend checks and commit**

Run: `pnpm ts:check`

Run: `pnpm exec eslint src/api/admin/activities.ts src/views/current-activities/index.vue`

Run: `pnpm build:prod`

Expected: all commands exit 0.

Commit: `feat: configure activity ai delivery rules`

### Task 7: Decision visibility and operational documentation

**Files:**
- Modify: `wechat_rag_bot/app/services/activity_service.py`
- Modify: `wechat_rag_bot/app/routers/admin_activities.py`
- Modify: `admin-web/src/api/admin/activities.ts`
- Modify: `admin-web/src/views/current-activities/index.vue`
- Modify: `wechat_rag_bot/docs/project_framework.md`
- Test: `wechat_rag_bot/tests/test_activity_ai_delivery.py`

- [ ] **Step 1: Add failing audit API tests**

```python
def test_activity_ai_decisions_include_selected_and_no_send_reasons(client):
    response = client.get(f"/api/v1/admin/activities/{activity_id}/ai-decisions")
    assert response.status_code == 200
    assert response.json()["data"]["items"][0]["status"] in {
        "shadow_selected", "enqueued", "rejected", "failed"
    }
```

- [ ] **Step 2: Add read-only decision endpoint and UI drawer**

Expose decision time, customer ID, status, confidence, reason, and candidate IDs. Never expose raw prompts, profile summaries, callback payloads, authorization values, or media XML.

- [ ] **Step 3: Document rollout and rollback**

Document:

```text
1. Deploy with ACTIVITY_AI_ENABLED=true and ACTIVITY_AI_SHADOW_MODE=true.
2. Observe at least 100 eligible conversations.
3. Review false-positive rate and no-send reasons.
4. Enable live mode for one configured activity.
5. Roll back immediately by setting ACTIVITY_AI_ENABLED=false and restarting API.
```

- [ ] **Step 4: Run tests and commit**

Run: `pytest tests/test_activity_ai_selection.py tests/test_activity_ai_delivery.py -q`

Expected: audit endpoint sanitization and statuses pass.

Commit: `feat: expose activity ai decision audit`

### Task 8: Final verification and production rollout

**Files:**
- No new source files.

- [ ] **Step 1: Run the backend activity and inbound-worker suites**

Run:

```powershell
pytest tests/test_admin_activities.py tests/test_activity_delivery.py tests/test_activity_ai_selection.py tests/test_activity_ai_delivery.py tests/test_message_risk_control.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run frontend verification**

Run:

```powershell
pnpm ts:check
pnpm exec eslint src/api/admin/activities.ts src/views/current-activities/index.vue
pnpm build:prod
```

Expected: all commands exit 0.

- [ ] **Step 3: Review and publish changes**

Run `git diff --check`, review the staged diff, scan for credentials, commit only activity AI files, fetch `origin/main`, and push only when the remote is not divergent.

Commit: `feat: add ai activity selection and delivery`

- [ ] **Step 4: Rebuild production in shadow mode**

Set production configuration to:

```dotenv
ACTIVITY_AI_ENABLED=true
ACTIVITY_AI_SHADOW_MODE=true
ACTIVITY_AI_MAX_CANDIDATES=5
```

Run:

```powershell
docker compose -p intelligent-customer-service -f docker-compose.prod.yml up -d --build
docker compose -p intelligent-customer-service -f docker-compose.prod.yml ps
Invoke-RestMethod http://127.0.0.1:21873/health
```

Expected: both containers are up and health returns `data.status=ok`.

- [ ] **Step 5: Confirm the first shadow observation window**

Acceptance thresholds before live mode:

- No activity is actually queued in shadow mode.
- Every selector call has a decision audit row.
- Unknown IDs and low-confidence decisions produce no send.
- Human-owned, group, first-message opening, archived, disabled, expired, cooldown, and capped cases produce no send.
- Reviewed false-positive rate is below 5% across at least 100 eligible conversations.

## Definition of done

- AI can see only currently eligible activity descriptors, not raw media content.
- AI can select only one listed activity ID or none.
- Backend hard gates override every model decision.
- The original package is queued in exact order without model rewriting.
- Manual activity sending remains unchanged.
- Duplicate inbound processing cannot duplicate the same activity item queue keys.
- Shadow mode and global disable provide immediate operational rollback.
- Decision and send logs distinguish `manual` and `ai` triggers.
- Production starts in shadow mode; live sending requires a separate human configuration change.
