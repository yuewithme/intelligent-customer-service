# Tag-Driven Policy Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tag-driven customer-service orchestration layer where labels decide the next action, knowledge bases, templates, prompt blocks, tone, context, and handoff behavior.

**Architecture:** Keep the current `chat_orchestrator -> intent_service -> policy_service -> template/rag/human` backbone, then add a thin tag layer and richer policy decision object. The first implementation should stay deterministic and configurable: no model training, no full multi-agent runtime, and no autonomous sub-agent loops.

**Tech Stack:** FastAPI, Pydantic, pytest, existing service modules under `wechat_rag_bot/app/services`, existing schemas under `wechat_rag_bot/app/schemas`.

---

## File Structure

- Create `wechat_rag_bot/app/schemas/tag.py`: structured tag output shared by tagger, policy, logs, and reply metadata.
- Modify `wechat_rag_bot/app/schemas/policy.py`: extend `PolicyDecision` with action, knowledge base ids, template ids, prompt block ids, context policy, and retrieval policy.
- Create `wechat_rag_bot/app/services/tagger_service.py`: combine existing `IntentResult`, `UserState`, `NormalizedMessage`, and profile/state signals into a `TagResult`.
- Create `wechat_rag_bot/app/services/policy_engine.py`: convert `TagResult` into a richer `PolicyDecision`.
- Create `wechat_rag_bot/app/services/context_selector.py`: select profile summary, session state, recent turns, and long-memory summary for the current turn.
- Create `wechat_rag_bot/app/services/prompt_builder.py`: assemble prompt blocks, templates, selected context, retrieved knowledge, and user message into one model prompt.
- Modify `wechat_rag_bot/app/services/rag_service.py`: accept optional policy/context/prompt inputs and stop hard-coding all RAG prompt behavior in one global template.
- Modify `wechat_rag_bot/app/services/chat_orchestrator.py`: call tagger and policy engine, pass policy decision through reply building, and include tag/policy metadata in the response/logs.
- Add tests:
  - `wechat_rag_bot/tests/test_tagger_service.py`
  - `wechat_rag_bot/tests/test_policy_engine.py`
  - `wechat_rag_bot/tests/test_context_selector.py`
  - `wechat_rag_bot/tests/test_prompt_builder.py`
  - Extend `wechat_rag_bot/tests/test_chat_intent_routes.py` or add `wechat_rag_bot/tests/test_tag_driven_chat_flow.py`

---

### Task 1: Add TagResult Schema

**Files:**
- Create: `wechat_rag_bot/app/schemas/tag.py`
- Test: `wechat_rag_bot/tests/test_tagger_service.py`

- [ ] **Step 1: Write the failing schema test**

```python
# wechat_rag_bot/tests/test_tagger_service.py
from app.schemas.tag import TagResult


def test_tag_result_defaults_are_stable():
    result = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="beginner",
        confidence=0.88,
    )

    assert result.intent == "orchid_care"
    assert result.route == "rag_answer"
    assert result.segment == "beginner"
    assert result.emotion == "neutral"
    assert result.stage == "unknown"
    assert result.risk_level == "normal"
    assert result.entities == {}
    assert result.tags == ["intent:orchid_care", "segment:beginner"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_tagger_service.py::test_tag_result_defaults_are_stable -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.schemas.tag'`.

- [ ] **Step 3: Create the schema**

```python
# wechat_rag_bot/app/schemas/tag.py
from pydantic import BaseModel, Field


class TagResult(BaseModel):
    intent: str
    route: str
    segment: str = "unknown"
    emotion: str = "neutral"
    stage: str = "unknown"
    risk_level: str = "normal"
    confidence: float = Field(ge=0, le=1)
    secondary_intents: list[str] = Field(default_factory=list)
    entities: dict = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    reason: str | None = None
    source: str = "intent_service"

    @property
    def tags(self) -> list[str]:
        tags = [
            f"intent:{self.intent}",
            f"segment:{self.segment}",
        ]
        if self.emotion and self.emotion != "neutral":
            tags.append(f"emotion:{self.emotion}")
        if self.stage and self.stage != "unknown":
            tags.append(f"stage:{self.stage}")
        if self.risk_level and self.risk_level != "normal":
            tags.append(f"risk:{self.risk_level}")
        tags.extend(self.labels)
        return tags
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_tagger_service.py::test_tag_result_defaults_are_stable -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/tag.py tests/test_tagger_service.py
git commit -m "feat: add tag result schema"
```

---

### Task 2: Implement Tagger Service

**Files:**
- Create: `wechat_rag_bot/app/services/tagger_service.py`
- Modify: `wechat_rag_bot/tests/test_tagger_service.py`

- [ ] **Step 1: Add failing tagger tests**

```python
# append to wechat_rag_bot/tests/test_tagger_service.py
import pytest

from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.services.tagger_service import build_tag_result


@pytest.mark.asyncio
async def test_build_tag_result_uses_intent_state_and_message_metadata():
    message = NormalizedMessage(
        trace_id="trace_1",
        channel="api",
        user_id="user_1",
        session_id="sess_1",
        message="我第一次养兰花，烂根了怎么办",
        kb_id="kb_default",
        metadata={"segment": "beginner"},
    )
    state = UserState(
        user_id="user_1",
        session_id="sess_1",
        sales_stage="first_order_nurture",
        risk_level="normal",
        customer_tags=["newbie"],
    )
    intent = IntentResult(
        route="rag_answer",
        primary_intent="orchid_care",
        secondary_intents=["root_rot"],
        sales_stage="care_support",
        customer_sentiment="anxious",
        confidence=0.91,
        need_rag=True,
    )

    result = await build_tag_result(message=message, user_state=state, intent=intent)

    assert result.intent == "orchid_care"
    assert result.route == "rag_answer"
    assert result.segment == "beginner"
    assert result.emotion == "anxious"
    assert result.stage == "care_support"
    assert result.risk_level == "normal"
    assert result.secondary_intents == ["root_rot"]
    assert "customer_tag:newbie" in result.labels
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_tagger_service.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.tagger_service'`.

- [ ] **Step 3: Implement the tagger**

```python
# wechat_rag_bot/app/services/tagger_service.py
from app.schemas.event import NormalizedMessage
from app.schemas.intent import IntentResult
from app.schemas.state import UserState
from app.schemas.tag import TagResult


async def build_tag_result(
    *,
    message: NormalizedMessage,
    user_state: UserState,
    intent: IntentResult,
) -> TagResult:
    segment = _segment_from(message, user_state)
    emotion = intent.customer_sentiment or _emotion_from_message(message.message)
    stage = intent.sales_stage if intent.sales_stage != "unknown" else user_state.sales_stage
    risk_level = _risk_from(intent, user_state)

    return TagResult(
        intent=intent.primary_intent,
        route=intent.route,
        segment=segment,
        emotion=emotion,
        stage=stage,
        risk_level=risk_level,
        confidence=intent.confidence,
        secondary_intents=intent.secondary_intents,
        entities=dict(intent.slots),
        labels=[f"customer_tag:{tag}" for tag in user_state.customer_tags],
        reason=intent.reason,
    )


def _segment_from(message: NormalizedMessage, user_state: UserState) -> str:
    metadata_segment = message.metadata.get("segment")
    if isinstance(metadata_segment, str) and metadata_segment:
        return metadata_segment
    if "newbie" in user_state.customer_tags or "新手" in user_state.customer_tags:
        return "beginner"
    if "advanced" in user_state.customer_tags or "老手" in user_state.customer_tags:
        return "advanced"
    return "unknown"


def _emotion_from_message(text: str) -> str:
    lowered = text.lower()
    if any(word in lowered for word in ["生气", "投诉", "太差", "不满"]):
        return "angry"
    if any(word in lowered for word in ["担心", "害怕", "焦虑", "怎么办"]):
        return "anxious"
    return "neutral"


def _risk_from(intent: IntentResult, user_state: UserState) -> str:
    if intent.need_human or intent.primary_intent in {"complaint", "refund_request", "human_request"}:
        return "high"
    if user_state.risk_level and user_state.risk_level != "normal":
        return user_state.risk_level
    return "normal"
```

- [ ] **Step 4: Run tests**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_tagger_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/tagger_service.py tests/test_tagger_service.py
git commit -m "feat: build tag result from intent and state"
```

---

### Task 3: Extend Policy Decision and Add Policy Engine

**Files:**
- Modify: `wechat_rag_bot/app/schemas/policy.py`
- Create: `wechat_rag_bot/app/services/policy_engine.py`
- Test: `wechat_rag_bot/tests/test_policy_engine.py`

- [ ] **Step 1: Write failing policy engine tests**

```python
# wechat_rag_bot/tests/test_policy_engine.py
import pytest

from app.schemas.tag import TagResult
from app.services.policy_engine import decide_policy


@pytest.mark.asyncio
async def test_beginner_orchid_care_uses_basic_kb_and_beginner_prompt_blocks():
    tag = TagResult(
        intent="orchid_care",
        route="rag_answer",
        segment="beginner",
        emotion="anxious",
        stage="care_support",
        confidence=0.9,
    )

    decision = await decide_policy(tag)

    assert decision.route == "rag_answer"
    assert decision.action == "rag_answer"
    assert decision.knowledge_base_ids == ["kb_orchid_basic", "kb_care_faq"]
    assert "segment.beginner" in decision.prompt_block_ids
    assert "tone.patient_step_by_step" in decision.prompt_block_ids
    assert decision.context_policy["recent_turns"] == 6
    assert decision.retrieval_policy["focus"] == ["基础原因", "处理步骤", "常见错误", "售后服务"]


@pytest.mark.asyncio
async def test_high_risk_policy_goes_to_human():
    tag = TagResult(
        intent="complaint",
        route="human",
        segment="advanced",
        risk_level="high",
        confidence=0.95,
    )

    decision = await decide_policy(tag)

    assert decision.route == "human"
    assert decision.action == "human"
    assert decision.template_ids == ["handoff_risk_high"]
    assert decision.next_action == "human_handoff"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_policy_engine.py -v
```

Expected: FAIL with missing `policy_engine`.

- [ ] **Step 3: Extend policy schema**

```python
# wechat_rag_bot/app/schemas/policy.py
from pydantic import BaseModel, Field


class PolicyDecision(BaseModel):
    route: str
    allowed: bool = True
    reason: str | None = None
    fallback_route: str | None = None
    original_route: str | None = None
    next_action: str | None = None
    action: str | None = None
    knowledge_base_ids: list[str] = Field(default_factory=list)
    template_ids: list[str] = Field(default_factory=list)
    prompt_block_ids: list[str] = Field(default_factory=list)
    context_policy: dict = Field(default_factory=dict)
    retrieval_policy: dict = Field(default_factory=dict)
```

- [ ] **Step 4: Implement policy engine**

```python
# wechat_rag_bot/app/services/policy_engine.py
from app.schemas.policy import PolicyDecision
from app.schemas.tag import TagResult


async def decide_policy(tag: TagResult) -> PolicyDecision:
    if tag.risk_level == "high" or tag.route == "human":
        return PolicyDecision(
            route="human",
            action="human",
            reason="tag_high_risk_to_human",
            original_route=tag.route,
            next_action="human_handoff",
            template_ids=["handoff_risk_high"],
        )

    if tag.intent == "orchid_care" and tag.segment == "beginner":
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="beginner_orchid_care_policy",
            original_route=tag.route,
            knowledge_base_ids=["kb_orchid_basic", "kb_care_faq"],
            template_ids=["opening_beginner_care"],
            prompt_block_ids=[
                "base.customer_service",
                "scenario.orchid_care",
                "intent.orchid_problem",
                "segment.beginner",
                "emotion.anxious" if tag.emotion == "anxious" else "emotion.neutral",
                "tone.patient_step_by_step",
                "output.customer_reply",
            ],
            context_policy={
                "recent_turns": 6,
                "include_profile_summary": True,
                "include_long_memory_summary": True,
            },
            retrieval_policy={
                "focus": ["基础原因", "处理步骤", "常见错误", "售后服务"],
                "exclude": ["高级繁殖", "复杂药剂配比"],
            },
        )

    if tag.intent == "orchid_care" and tag.segment == "advanced":
        return PolicyDecision(
            route="rag_answer",
            action="rag_answer",
            reason="advanced_orchid_care_policy",
            original_route=tag.route,
            knowledge_base_ids=["kb_orchid_advanced", "kb_best_practices"],
            template_ids=["opening_advanced_care"],
            prompt_block_ids=[
                "base.customer_service",
                "scenario.orchid_care",
                "intent.orchid_problem",
                "segment.advanced",
                "tone.concise_professional",
                "output.customer_reply",
            ],
            context_policy={
                "recent_turns": 4,
                "include_profile_summary": True,
                "include_long_memory_summary": False,
            },
            retrieval_policy={
                "focus": ["限制条件", "进阶处理", "关键参数", "最佳实践"],
                "exclude": ["基础概念", "过度解释"],
            },
        )

    return PolicyDecision(
        route=tag.route,
        action=tag.route,
        reason="default_tag_policy",
        original_route=tag.route,
        prompt_block_ids=[
            "base.customer_service",
            f"intent.{tag.intent}",
            f"segment.{tag.segment}",
            "output.customer_reply",
        ],
        context_policy={
            "recent_turns": 4,
            "include_profile_summary": True,
            "include_long_memory_summary": False,
        },
    )
```

- [ ] **Step 5: Run policy tests**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_policy_engine.py -v
```

Expected: PASS.

- [ ] **Step 6: Run existing policy-related tests**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_handoff_policy.py tests/test_chat_intent_routes.py -v
```

Expected: PASS. Existing `policy_service.decide_route` should still work because new fields have defaults.

- [ ] **Step 7: Commit**

```bash
git add app/schemas/policy.py app/services/policy_engine.py tests/test_policy_engine.py
git commit -m "feat: add tag-driven policy engine"
```

---

### Task 4: Add Context Selector

**Files:**
- Create: `wechat_rag_bot/app/schemas/context.py`
- Create: `wechat_rag_bot/app/services/context_selector.py`
- Test: `wechat_rag_bot/tests/test_context_selector.py`

- [ ] **Step 1: Write failing context selector test**

```python
# wechat_rag_bot/tests/test_context_selector.py
import pytest

from app.schemas.context import ContextSelectionInput
from app.services.context_selector import select_context


@pytest.mark.asyncio
async def test_select_context_keeps_recent_turns_and_profile_summary():
    request = ContextSelectionInput(
        profile={
            "user_id": "user_1",
            "ai_summary": "用户是新手，多次询问烂根处理。",
            "preference_summary": "喜欢一步一步说明。",
            "pain_points": ["担心养不好"],
        },
        state={
            "sales_stage": "care_support",
            "risk_level": "normal",
        },
        memories=[
            {"role": "user", "content": "第一轮"},
            {"role": "assistant", "content": "第一轮回复"},
            {"role": "user", "content": "第二轮"},
            {"role": "assistant", "content": "第二轮回复"},
        ],
        context_policy={
            "recent_turns": 2,
            "include_profile_summary": True,
            "include_long_memory_summary": True,
        },
    )

    result = await select_context(request)

    assert result.profile_summary["ai_summary"] == "用户是新手，多次询问烂根处理。"
    assert result.session_state["sales_stage"] == "care_support"
    assert [turn["content"] for turn in result.recent_turns] == ["第二轮", "第二轮回复"]
    assert "担心养不好" in result.long_memory_summary
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_context_selector.py -v
```

Expected: FAIL with missing `app.schemas.context`.

- [ ] **Step 3: Add context schemas**

```python
# wechat_rag_bot/app/schemas/context.py
from pydantic import BaseModel, Field


class ContextSelectionInput(BaseModel):
    profile: dict = Field(default_factory=dict)
    state: dict = Field(default_factory=dict)
    memories: list[dict] = Field(default_factory=list)
    context_policy: dict = Field(default_factory=dict)


class ContextPackage(BaseModel):
    profile_summary: dict = Field(default_factory=dict)
    session_state: dict = Field(default_factory=dict)
    recent_turns: list[dict] = Field(default_factory=list)
    long_memory_summary: str = ""
```

- [ ] **Step 4: Implement context selector**

```python
# wechat_rag_bot/app/services/context_selector.py
from app.schemas.context import ContextPackage, ContextSelectionInput


async def select_context(request: ContextSelectionInput) -> ContextPackage:
    recent_turns_count = int(request.context_policy.get("recent_turns", 4))
    include_profile = bool(request.context_policy.get("include_profile_summary", True))
    include_long_summary = bool(request.context_policy.get("include_long_memory_summary", False))

    profile_summary = _profile_summary(request.profile) if include_profile else {}
    session_state = {
        "sales_stage": request.state.get("sales_stage", "unknown"),
        "risk_level": request.state.get("risk_level", "normal"),
        "last_intent": request.state.get("last_intent"),
        "last_route": request.state.get("last_route"),
    }
    recent_turns = request.memories[-recent_turns_count:] if recent_turns_count > 0 else []
    long_memory_summary = _long_memory_summary(request.profile) if include_long_summary else ""

    return ContextPackage(
        profile_summary=profile_summary,
        session_state=session_state,
        recent_turns=recent_turns,
        long_memory_summary=long_memory_summary,
    )


def _profile_summary(profile: dict) -> dict:
    return {
        "ai_summary": profile.get("ai_summary"),
        "preference_summary": profile.get("preference_summary"),
        "pain_points": profile.get("pain_points", []),
        "customer_tags": profile.get("customer_tags", []),
    }


def _long_memory_summary(profile: dict) -> str:
    parts = []
    if profile.get("ai_summary"):
        parts.append(profile["ai_summary"])
    if profile.get("preference_summary"):
        parts.append(profile["preference_summary"])
    pain_points = profile.get("pain_points") or []
    if pain_points:
        parts.append("痛点：" + "、".join(pain_points))
    return "\n".join(parts)
```

- [ ] **Step 5: Run context selector tests**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_context_selector.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/context.py app/services/context_selector.py tests/test_context_selector.py
git commit -m "feat: select prompt context from profile and memory"
```

---

### Task 5: Add Prompt Builder

**Files:**
- Create: `wechat_rag_bot/app/schemas/prompt.py`
- Create: `wechat_rag_bot/app/services/prompt_builder.py`
- Test: `wechat_rag_bot/tests/test_prompt_builder.py`

- [ ] **Step 1: Write failing prompt builder test**

```python
# wechat_rag_bot/tests/test_prompt_builder.py
import pytest

from app.schemas.context import ContextPackage
from app.schemas.prompt import PromptBuildInput
from app.services.prompt_builder import build_prompt


@pytest.mark.asyncio
async def test_build_prompt_orders_blocks_context_knowledge_and_question():
    request = PromptBuildInput(
        prompt_block_ids=["base.customer_service", "segment.beginner", "tone.patient_step_by_step"],
        templates=["我来帮您一步步看。"],
        context=ContextPackage(
            profile_summary={"ai_summary": "用户是新手。"},
            session_state={"sales_stage": "care_support"},
            recent_turns=[{"role": "user", "content": "兰花烂根了"}],
            long_memory_summary="用户担心养不好。",
        ),
        knowledge_snippets=[
            {"source": "kb_basic", "text": "烂根常见原因包括植料过湿和通风不足。"}
        ],
        user_message="现在应该怎么处理？",
    )

    prompt = await build_prompt(request)

    assert prompt.index("你是智能客服助手") < prompt.index("用户是新手")
    assert "我来帮您一步步看。" in prompt
    assert "用户担心养不好。" in prompt
    assert "烂根常见原因包括植料过湿和通风不足。" in prompt
    assert prompt.strip().endswith("现在应该怎么处理？")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_prompt_builder.py -v
```

Expected: FAIL with missing `app.schemas.prompt`.

- [ ] **Step 3: Add prompt schema**

```python
# wechat_rag_bot/app/schemas/prompt.py
from pydantic import BaseModel, Field

from app.schemas.context import ContextPackage


class PromptBuildInput(BaseModel):
    prompt_block_ids: list[str] = Field(default_factory=list)
    templates: list[str] = Field(default_factory=list)
    context: ContextPackage = Field(default_factory=ContextPackage)
    knowledge_snippets: list[dict] = Field(default_factory=list)
    user_message: str
```

- [ ] **Step 4: Implement prompt builder**

```python
# wechat_rag_bot/app/services/prompt_builder.py
from app.schemas.prompt import PromptBuildInput


PROMPT_BLOCKS = {
    "base.customer_service": "你是智能客服助手。必须基于提供的信息回答，不要编造事实。",
    "scenario.orchid_care": "当前场景是兰花养护咨询。回答要围绕养护判断、处理步骤和风险边界。",
    "intent.orchid_problem": "用户正在描述兰花问题。需要先回应关切，再给出可执行建议。",
    "segment.beginner": "用户是新手。先解释基础原因，避免复杂术语，并给出清晰步骤。",
    "segment.advanced": "用户是熟练用户。直接给关键判断、限制条件和进阶建议。",
    "emotion.anxious": "用户有焦虑感。先安抚，再说明处理方向。",
    "emotion.neutral": "用户情绪中性。保持自然、清楚、简洁。",
    "tone.patient_step_by_step": "语气耐心，适合分步骤说明。",
    "tone.concise_professional": "语气简洁专业，避免重复基础概念。",
    "output.customer_reply": "只输出可以直接发给用户的客服话术，不输出内部标签、规则或分析过程。",
}


async def build_prompt(request: PromptBuildInput) -> str:
    sections: list[str] = []
    sections.extend(_render_prompt_blocks(request.prompt_block_ids))
    sections.append(_render_templates(request.templates))
    sections.append(_render_context(request.context))
    sections.append(_render_knowledge(request.knowledge_snippets))
    sections.append("用户问题：\n" + request.user_message.strip())
    return "\n\n".join(section for section in sections if section.strip())


def _render_prompt_blocks(block_ids: list[str]) -> list[str]:
    return [PROMPT_BLOCKS[block_id] for block_id in block_ids if block_id in PROMPT_BLOCKS]


def _render_templates(templates: list[str]) -> str:
    if not templates:
        return ""
    return "可使用的固定话术：\n" + "\n".join(f"- {template}" for template in templates)


def _render_context(context) -> str:
    parts = []
    if context.profile_summary:
        parts.append(f"用户画像摘要：\n{context.profile_summary}")
    if context.session_state:
        parts.append(f"会话状态：\n{context.session_state}")
    if context.recent_turns:
        turns = "\n".join(f"{turn.get('role')}: {turn.get('content')}" for turn in context.recent_turns)
        parts.append(f"近期对话：\n{turns}")
    if context.long_memory_summary:
        parts.append(f"长期记忆摘要：\n{context.long_memory_summary}")
    return "\n\n".join(parts)


def _render_knowledge(snippets: list[dict]) -> str:
    if not snippets:
        return ""
    blocks = []
    for index, snippet in enumerate(snippets, start=1):
        source = snippet.get("source") or snippet.get("file_name") or "unknown"
        text = snippet.get("text", "")
        blocks.append(f"[{index}] {source}\n{text}")
    return "知识库片段：\n" + "\n\n".join(blocks)
```

- [ ] **Step 5: Run prompt builder tests**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_prompt_builder.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/schemas/prompt.py app/services/prompt_builder.py tests/test_prompt_builder.py
git commit -m "feat: build prompts from blocks and selected context"
```

---

### Task 6: Wire Policy-Aware Prompting into RAG

**Files:**
- Modify: `wechat_rag_bot/app/services/rag_service.py`
- Test: `wechat_rag_bot/tests/test_rag_service.py`

- [ ] **Step 1: Add a failing RAG prompt integration test**

```python
# append to wechat_rag_bot/tests/test_rag_service.py
import pytest

from app.schemas.context import ContextPackage
from app.schemas.policy import PolicyDecision
from app.services.rag_service import build_rag_prompt


@pytest.mark.asyncio
async def test_build_rag_prompt_uses_policy_prompt_blocks_and_context():
    policy = PolicyDecision(
        route="rag_answer",
        action="rag_answer",
        prompt_block_ids=["base.customer_service", "segment.beginner"],
        template_ids=["opening_beginner_care"],
    )
    context = ContextPackage(
        profile_summary={"ai_summary": "用户是新手。"},
        recent_turns=[{"role": "user", "content": "兰花烂根了"}],
    )
    docs = [
        {
            "file_name": "care.md",
            "text": "烂根处理要先控水、改善通风，并观察根系状态。",
        }
    ]

    prompt = await build_rag_prompt(
        question="怎么办？",
        docs=docs,
        policy=policy,
        context=context,
        templates=["我来帮您一步步看。"],
    )

    assert "用户是新手" in prompt
    assert "我来帮您一步步看。" in prompt
    assert "烂根处理要先控水" in prompt
    assert prompt.strip().endswith("怎么办？")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_rag_service.py::test_build_rag_prompt_uses_policy_prompt_blocks_and_context -v
```

Expected: FAIL with missing `build_rag_prompt`.

- [ ] **Step 3: Add prompt helper in RAG service**

```python
# add to wechat_rag_bot/app/services/rag_service.py
from app.schemas.context import ContextPackage
from app.schemas.policy import PolicyDecision
from app.schemas.prompt import PromptBuildInput
from app.services.prompt_builder import build_prompt


async def build_rag_prompt(
    *,
    question: str,
    docs: list[dict[str, Any]],
    policy: PolicyDecision | None = None,
    context: ContextPackage | None = None,
    templates: list[str] | None = None,
) -> str:
    if policy is None:
        return PROMPT_TEMPLATE.format(context=_context(docs), question=question.strip())

    snippets = [
        {
            "source": doc.get("file_name", "unknown"),
            "text": doc.get("text", ""),
        }
        for doc in docs
    ]
    return await build_prompt(
        PromptBuildInput(
            prompt_block_ids=policy.prompt_block_ids,
            templates=templates or [],
            context=context or ContextPackage(),
            knowledge_snippets=snippets,
            user_message=question,
        )
    )
```

- [ ] **Step 4: Update `rag_chat` to use the helper without changing old callers**

Replace the existing `PROMPT_TEMPLATE.format(...)` call inside `rag_chat` with:

```python
prompt = await build_rag_prompt(question=message.strip(), docs=docs)
result = await llm_service.generate_answer(prompt)
```

- [ ] **Step 5: Run RAG tests**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_rag_service.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/rag_service.py tests/test_rag_service.py
git commit -m "feat: support policy-aware rag prompts"
```

---

### Task 7: Integrate Tagger and Policy Engine into Chat Orchestrator

**Files:**
- Modify: `wechat_rag_bot/app/services/chat_orchestrator.py`
- Test: `wechat_rag_bot/tests/test_tag_driven_chat_flow.py`

- [ ] **Step 1: Write failing orchestration metadata test**

```python
# wechat_rag_bot/tests/test_tag_driven_chat_flow.py
import pytest

from app.schemas.chat import ChatRequest
from app.services.chat_orchestrator import handle_chat


@pytest.mark.asyncio
async def test_chat_response_contains_tag_and_policy_metadata(monkeypatch):
    request = ChatRequest(
        channel="api",
        user_id="tag_user_1",
        session_id="tag_sess_1",
        message="我第一次养兰花，烂根了怎么办",
        kb_id="kb_default",
        metadata={"segment": "beginner"},
    )

    result = await handle_chat(request)

    assert "tag_result" in result["metadata"]
    assert "policy_decision" in result["metadata"]
    assert result["metadata"]["tag_result"]["segment"] == "beginner"
    assert result["metadata"]["policy_decision"]["action"] in {
        "rag_answer",
        "template_reply",
        "template_then_rag",
        "human",
        "clarify",
        "unsupported",
        "chitchat",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_tag_driven_chat_flow.py -v
```

Expected: FAIL because metadata does not include `tag_result` or `policy_decision`.

- [ ] **Step 3: Import tagger and policy engine**

Add imports to `wechat_rag_bot/app/services/chat_orchestrator.py`:

```python
from app.services.tagger_service import build_tag_result
from app.services.policy_engine import decide_policy
```

- [ ] **Step 4: Build tag and rich policy after existing route decision**

After the existing `decision = await decide_route(...)` block, add:

```python
        tag_result = await build_tag_result(
            message=message,
            user_state=user_state,
            intent=intent,
        )
        rich_decision = await decide_policy(tag_result)
        if rich_decision.reason != "default_tag_policy":
            decision = rich_decision
```

Initialize `tag_result = None` and `rich_decision = None` near the top of `handle_chat`.

- [ ] **Step 5: Attach tag and policy metadata to the final reply**

After `reply = await ...` is built and before `_to_chat_data(...)`, add:

```python
        if tag_result is not None:
            reply.metadata["tag_result"] = tag_result.model_dump()
        if decision is not None:
            reply.metadata["policy_decision"] = decision.model_dump()
```

- [ ] **Step 6: Include tag and policy in success log payload**

In `_success_log_payload`, keep the existing fields and add to the returned dict:

```python
        "tag_result": reply.metadata.get("tag_result"),
        "policy_decision": reply.metadata.get("policy_decision"),
```

If `ChatLogModel` cannot store these fields yet, keep them inside `metadata` only and do not add database columns in this task.

- [ ] **Step 7: Run orchestration tests**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_tag_driven_chat_flow.py tests/test_chat_intent_routes.py tests/test_handoff_fallbacks.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add app/services/chat_orchestrator.py tests/test_tag_driven_chat_flow.py
git commit -m "feat: attach tag-driven policy metadata to chat flow"
```

---

### Task 8: Update Architecture Docs and Run Narrow Regression

**Files:**
- Modify: `wechat_rag_bot/docs/project_framework.md`
- Test: existing narrow regression

- [ ] **Step 1: Update implementation status in docs**

In `wechat_rag_bot/docs/project_framework.md`, update the current implementation boundary from:

```markdown
- 标签驱动策略编排、Context Selector 和 Prompt Builder 是后续主链路升级方向；现有代码仍以 `Intent Service`、`Policy Service`、`Template Service`、`RAG Service` 和 `Reply Builder` 为主。
```

to:

```markdown
- 标签驱动策略编排已具备首版骨架：`Tagger` 输出结构化标签，`Policy Engine` 输出知识库、模板、提示词块和上下文策略，`Prompt Builder` 可组装模型输入；复杂工具调用和真正的多 Agent 协作仍是后续增强方向。
```

- [ ] **Step 2: Run narrow regression**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_tagger_service.py tests/test_policy_engine.py tests/test_context_selector.py tests/test_prompt_builder.py tests/test_rag_service.py tests/test_tag_driven_chat_flow.py -v
```

Expected: PASS.

- [ ] **Step 3: Run route and handoff regression**

Run:

```bash
cd wechat_rag_bot
pytest tests/test_chat_intent_routes.py tests/test_handoff_policy.py tests/test_handoff_fallbacks.py -v
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add docs/project_framework.md
git commit -m "docs: mark tag-driven orchestration implementation status"
```

---

## Success Checks

- `TagResult` exists and can represent intent, segment, emotion, stage, risk, entities, confidence, and labels.
- `PolicyDecision` can carry route/action plus knowledge base ids, template ids, prompt block ids, context policy, and retrieval policy.
- Beginner and advanced users can receive different policy decisions for the same intent.
- High-risk tags route to human handoff.
- Context selection separates recent raw turns, long summary, profile summary, and session state.
- Prompt Builder assembles prompt blocks, templates, context, knowledge snippets, and current question in a stable order.
- RAG can still run with the old default prompt and can also build a policy-aware prompt.
- Chat response metadata includes `tag_result` and `policy_decision`.
- Existing route and handoff tests keep passing.

## Self-Review

Spec coverage:

- Tags changing next action: covered by Tasks 1-3 and Task 7.
- Different knowledge bases: covered by Task 3 `knowledge_base_ids`.
- Different tone and prompt blocks: covered by Tasks 3 and 5.
- Fixed template library: existing `template_service` and `talk_script` remain, Task 3 adds `template_ids`.
- Historical context and user profile: covered by Task 4.
- Prompt Builder: covered by Task 5.
- No model training first: reflected in architecture and deterministic service tasks.

Placeholder scan:

- No `TBD`, empty implementation step, or unspecified test step remains.

Type consistency:

- `TagResult`, `PolicyDecision`, `ContextPackage`, and `PromptBuildInput` are introduced before later tasks depend on them.
