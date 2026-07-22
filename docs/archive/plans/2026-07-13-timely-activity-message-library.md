# “目前活动”最小闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不重构现有客服工作台、会话状态机和 Eyun 风控队列的前提下，增加“保存活动包 → 后台可视化管理 → 人工会话选择发送 → 查询发送结果”的最小闭环，并为后续 AI 自动选择活动保留一个独立接入点。

**Architecture:** 活动作为独立业务模块，不并入知识库、话术库或用户画像。后端只新增活动表和发送记录表；活动消息项以有序 JSON 快照保存在活动表中，人工和后续 AI 都复用现有 Eyun 出站队列。第一阶段不修改 AI 回复图，验收后第二阶段只增加一个活动匹配节点，不改现有意图、RAG、话术和人工接管逻辑。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy/SQLite、pytest、Vue 3、TypeScript、Element Plus、现有 Eyun 出站队列。

---

## 一、最终产品行为

### 1. 保存活动包

1. 运营人员在会话消息区右键一条消息，进入多选模式。
2. 继续点击选择同一会话中的文本、图片或视频；保存时后端按 `created_at, id` 排序，不依赖前端传入顺序。
3. 点击“存为活动”，填写活动名称和说明。默认长期有效；开始时间和结束时间是可选项。
4. 保存后生成草稿活动；运营人员到“目前活动”页面预览并发布。
5. 图片、视频保存收到消息回调里的原始 XML 快照，用于 Eyun `/sendRecvImage`、`/sendRecvVideo` 转发；原始 XML 不返回给前端展示。

### 2. “目前活动”管理页面

管理平台“客服运营”菜单新增“目前活动”，路由为 `/operations/current-activities`。

页面采用卡片展示：

- 活动名称、说明、消息数量。
- 文本摘要、图片/视频缩略预览。
- 状态：草稿、已发布、已归档。
- 总开关：关闭后人工和 AI 都不可使用。
- AI 开关：关闭后仍可人工发送，但 AI 不会选择。
- 有效方式：长期有效或指定时间范围。
- 创建人、创建时间、更新时间、最近发送结果。

最小管理操作：创建、编辑草稿、发布、打开/关闭总开关、打开/关闭 AI、归档、查看发送记录。不做复杂版本管理、审批流、活动分类树和批量群发。

### 3. 人工会话发送

1. 会话状态变为 `human_active` 后，右侧 `SupervisionPanel` 显示“目前活动”按钮。
2. 点击跳转到 `CurrentActivities`，query 只携带 `conversation_id`；操作者从当前登录用户 store 读取，不信任 URL。
3. 页面检测到 `conversation_id` 后进入发送模式，顶部显示当前客户，列表只展示已发布、总开关打开且当前有效的活动。
4. 运营人员单选一个活动，打开完整顺序预览，确认后发送。
5. 后端重新校验会话仍为 `human_active` 且 `owner_id` 等于当前操作者，然后逐项加入现有 Eyun 队列。
6. 发送成功表示“已进入队列”，页面展示排队状态，并提供“返回原会话”。

### 4. 默认有效与开关

- `valid_from` 默认取发布时间。
- `valid_until=None` 表示长期有效。
- `enabled` 是总开关，默认打开；关闭后人工与 AI 均不可使用。
- `ai_enabled` 默认关闭，避免新活动未经确认就被 AI 自动使用。
- 草稿即使 `enabled=true` 也不可发送。

## 二、最小架构边界

### 复用的现有能力

- `ConversationModel`、`ConversationMessageModel`：来源消息和会话状态，不新增活动专用会话表。
- `conversation_service.py`：复用人工接管权限和 Eyun 目标解析。
- `EyunOutboundMessageModel`：每条活动消息仍进入现有队列、重试和按 `wId` 限速。
- `eyun_callback_service.py`：沿用现有 provider 配置和 HTTP 调用方式。
- `SupervisionPanel.vue`：在既有右侧操作区增加唯一入口。
- 现有 API 鉴权、返回结构、时间格式和后台菜单模式。

### 只新增两张表

```python
class ActivityModel(Base):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="draft")
    enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=True)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
    ai_rules_json: Mapped[str] = mapped_column(Text, default="{}")
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128))
    updated_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActivitySendLogModel(Base):
    __tablename__ = "activity_send_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(Integer, index=True)
    conversation_id: Mapped[str] = mapped_column(String(256), index=True)
    user_id: Mapped[str] = mapped_column(String(256), index=True)
    trigger_mode: Mapped[str] = mapped_column(String(16), index=True)
    operator_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outbound_message_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
```

不在发送记录表重复保存状态。查询发送记录时，根据 `outbound_message_ids_json` 关联现有 `eyun_outbound_messages`，实时聚合 `queued/sending/retrying/sent/partial`；`last_error` 非空且队列状态仍为 queued 时展示为 retrying，避免修改队列 worker 回写另一套状态。

### 活动消息 JSON

```json
[
  {
    "position": 1,
    "type": "text",
    "content": "本周兰花活动开始了",
    "source_message_id": 101
  },
  {
    "position": 2,
    "type": "received_image",
    "content": "<msg><img ... /></msg>",
    "preview_url": "/static/media/activity-preview.jpg",
    "source_message_id": 102
  }
]
```

API 返回活动详情时移除媒体项的 `content` 原始 XML，只返回 `preview_url`；发送服务在后端读取完整快照。

## 三、文件范围

### 新建

- `wechat_rag_bot/app/schemas/activity.py`：活动创建、更新、开关、列表和发送请求模型。
- `wechat_rag_bot/app/services/activity_service.py`：活动 CRUD、消息快照、有效性判断、人工发送和发送状态聚合。
- `wechat_rag_bot/app/routers/admin_activities.py`：活动管理和发送 API。
- `wechat_rag_bot/tests/test_admin_activities.py`：活动最小闭环测试。
- `admin-web/src/api/admin/activities.ts`：活动 API 和 TypeScript 类型。
- `admin-web/src/views/current-activities/index.vue`：“目前活动”管理/发送双模式页面。
- `admin-web/src/views/workbench/components/SaveActivityDialog.vue`：保存所选消息表单。

### 修改

- `wechat_rag_bot/app/db/models.py`：只新增 `ActivityModel`、`ActivitySendLogModel`。
- `wechat_rag_bot/app/main.py`：注册活动路由。
- `wechat_rag_bot/app/services/conversation_service.py`：抽出一个公开的人工发送目标校验函数。
- `wechat_rag_bot/app/services/eyun_callback_service.py`：新增收到图片/视频转发包装。
- `wechat_rag_bot/app/services/message_risk_control_service.py`：队列识别 `received_image`、`received_video` 两种类型。
- `wechat_rag_bot/tests/test_message_risk_control.py`：新增两种转发类型测试。
- `admin-web/src/views/workbench/components/MessagePanel.vue`：右键多选和保存入口。
- `admin-web/src/views/workbench/components/SupervisionPanel.vue`：人工接管后显示“目前活动”。
- `admin-web/src/views/workbench/index.vue`：保存活动协调和返回会话恢复。
- `admin-web/src/router/modules/admin.ts`：注册“目前活动”菜单。

不修改：现有会话表字段、会话状态机、`ReplyComposer.vue`、知识库、话术库、用户画像、意图识别、RAG 和 AI 回复图（第一阶段）。

## 四、API 最小集合

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/v1/admin/activities` | 活动列表；发送模式可传 `conversation_id` |
| `POST` | `/api/v1/admin/activities/from-messages` | 从同一会话的消息 ID 创建草稿 |
| `GET` | `/api/v1/admin/activities/{id}` | 活动详情和安全预览 |
| `PUT` | `/api/v1/admin/activities/{id}` | 编辑草稿或已关闭活动 |
| `POST` | `/api/v1/admin/activities/{id}/publish` | 发布活动 |
| `PATCH` | `/api/v1/admin/activities/{id}/switches` | 更新 `enabled`/`ai_enabled` |
| `POST` | `/api/v1/admin/activities/{id}/archive` | 归档活动 |
| `POST` | `/api/v1/admin/activities/{id}/send` | 向人工接管会话排队发送 |
| `GET` | `/api/v1/admin/activities/{id}/send-logs` | 查询人工/AI发送记录和队列聚合状态 |

人工发送请求：

```json
{
  "conversation_id": "wechat:wxid_customer:default",
  "operator_id": "admin"
}
```

## 五、第一阶段实施任务

### Task 1：活动模型与服务

**Files:**
- Modify: `wechat_rag_bot/app/db/models.py`
- Create: `wechat_rag_bot/app/schemas/activity.py`
- Create: `wechat_rag_bot/app/services/activity_service.py`
- Create: `wechat_rag_bot/tests/test_admin_activities.py`

- [ ] **Step 1: 写失败测试**，明确验证：来源消息必须属于同一会话；后端重排消息；只接受 text/image/video；媒体缺少 `raw_content` 时返回 422；默认长期有效；草稿、关闭、归档和显式过期活动不可发送。
- [ ] **Step 2: 运行测试确认失败**

Run: `cd wechat_rag_bot; pytest tests/test_admin_activities.py -q`

Expected: FAIL，活动模块尚不存在。

- [ ] **Step 3: 实现有效状态函数**

```python
def is_activity_available(activity: ActivityModel, now: datetime) -> bool:
    if activity.status != "published" or not activity.enabled:
        return False
    if activity.valid_from is not None and now < activity.valid_from:
        return False
    if activity.valid_until is not None and now >= activity.valid_until:
        return False
    return True
```

- [ ] **Step 4: 实现消息快照**：按 `created_at, id` 排序；文本保存正文；图片/视频从 `metadata_json.raw_content` 保存 XML，从 `metadata_json.media.url` 保存安全预览；序列化详情时隐藏媒体 XML。
- [ ] **Step 5: 使用项目现有 `Base.metadata.create_all(..., tables=[...])` 模式按数据库 URL 初始化两张表，不引入 Alembic 或新数据库。**
- [ ] **Step 6: 运行目标测试**

Run: `cd wechat_rag_bot; pytest tests/test_admin_activities.py -q`

Expected: PASS。

### Task 2：活动 API

**Files:**
- Create: `wechat_rag_bot/app/routers/admin_activities.py`
- Modify: `wechat_rag_bot/app/main.py`
- Modify: `wechat_rag_bot/tests/test_admin_activities.py`

- [ ] **Step 1: 写失败测试**，覆盖 API 鉴权、创建、列表、详情、编辑、发布、两个开关、归档以及媒体 XML 不出现在响应中。
- [ ] **Step 2: 运行测试确认路由 404。**

Run: `cd wechat_rag_bot; pytest tests/test_admin_activities.py -q`

Expected: FAIL，活动路由未注册。

- [ ] **Step 3: 实现 API 最小集合并注册 router**；发布时要求标题和至少一条消息，`valid_until` 非空时必须晚于 `valid_from`。
- [ ] **Step 4: 运行目标测试**

Run: `cd wechat_rag_bot; pytest tests/test_admin_activities.py -q`

Expected: PASS。

### Task 3：复用 Eyun 队列发送活动

**Files:**
- Modify: `wechat_rag_bot/app/services/conversation_service.py`
- Modify: `wechat_rag_bot/app/services/eyun_callback_service.py`
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`
- Modify: `wechat_rag_bot/app/services/activity_service.py`
- Modify: `wechat_rag_bot/tests/test_message_risk_control.py`
- Modify: `wechat_rag_bot/tests/test_admin_activities.py`

- [ ] **Step 1: 写失败测试**，验证未接管/owner 不匹配返回 409；文本进入 `/sendText`；收到图片进入 `/sendRecvImage`；收到视频进入 `/sendRecvVideo`；活动项按 position 排队；send log 保存 outbound IDs；查询日志聚合队列状态。
- [ ] **Step 2: 运行目标测试确认失败**

Run: `cd wechat_rag_bot; pytest tests/test_message_risk_control.py tests/test_admin_activities.py -q`

Expected: FAIL，received media 和活动发送尚未接入。

- [ ] **Step 3: 在 conversation_service 抽出只读校验函数**

```python
def get_human_activity_send_target(
    conversation_id: str, operator_id: str
) -> dict[str, str]:
    with _get_session() as session:
        conversation = _get_conversation_or_error(session, conversation_id)
        if conversation.status != HUMAN_ACTIVE or conversation.owner_id != operator_id:
            raise AppError(
                ErrorCode.REQUEST_INVALID,
                message="当前会话未由该操作员接管，不能发送活动",
                status_code=409,
            )
        target = _latest_eyun_reply_target(session, conversation)
        if target is None:
            raise AppError(
                ErrorCode.WECHAT_REPLY_FAILED,
                message="当前会话缺少 Eyun 发送目标",
                status_code=409,
            )
        return {**target, "user_id": conversation.user_id}
```

该函数复用现有 `_latest_eyun_reply_target`，不改变 `reply_conversation` 行为。

- [ ] **Step 4: 新增 Eyun 转发包装**

```python
async def send_eyun_received_media(
    *, w_id: str, wc_id: str, content: str, message_type: str
) -> None:
    endpoint = {
        "received_image": "/sendRecvImage",
        "received_video": "/sendRecvVideo",
    }[message_type]
    settings = get_settings()
    base_url = settings.eyun_base_url.rstrip("/")
    authorization = settings.eyun_authorization.strip()
    if not base_url or not authorization or not w_id:
        raise RuntimeError("Eyun configuration is incomplete")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{base_url}{endpoint}",
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json={"wId": w_id, "wcId": wc_id, "content": content},
        )
    response.raise_for_status()
    result = response.json()
    if str(result.get("code")) != "1000":
        raise RuntimeError(f"Eyun received media send failed: {result}")
```

- [ ] **Step 5: 只扩展现有队列的类型分发**；活动服务逐项调用 `enqueue_eyun_outbound`，收集返回 ID 写入一条 send log，不新增 scheduler、worker 或重试机制。
- [ ] **Step 6: 运行目标测试**

Run: `cd wechat_rag_bot; pytest tests/test_message_risk_control.py tests/test_admin_activities.py -q`

Expected: PASS。

### Task 4：工作台保存入口和右侧跳转

**Files:**
- Modify: `admin-web/src/views/workbench/components/MessagePanel.vue`
- Create: `admin-web/src/views/workbench/components/SaveActivityDialog.vue`
- Modify: `admin-web/src/views/workbench/components/SupervisionPanel.vue`
- Modify: `admin-web/src/views/workbench/index.vue`
- Create: `admin-web/src/api/admin/activities.ts`

- [ ] **Step 1: MessagePanel 右键进入选择模式**；只允许选择当前合并会话中的 customer 消息和支持的消息类型；显示选中数、取消、“存为活动”。
- [ ] **Step 2: SaveActivityDialog 收集标题、说明和可选时间**，调用 `from-messages`；成功后退出选择模式并提示“已保存为草稿”。
- [ ] **Step 3: SupervisionPanel 在 `human_active` 时显示唯一的“目前活动”按钮**

```ts
router.push({
  name: 'CurrentActivities',
  query: { conversation_id: props.conversationId }
})
```

- [ ] **Step 4: workbench 读取返回 query 的 conversation_id 并恢复原会话，不修改会话列表分组规则。**
- [ ] **Step 5: 运行前端目标检查**

Run: `cd admin-web; pnpm ts:check`

Run: `cd admin-web; pnpm exec eslint src/api/admin/activities.ts src/views/workbench/components/MessagePanel.vue src/views/workbench/components/SaveActivityDialog.vue src/views/workbench/components/SupervisionPanel.vue src/views/workbench/index.vue`

Expected: 退出码 0。

### Task 5：“目前活动”页面

**Files:**
- Create: `admin-web/src/views/current-activities/index.vue`
- Modify: `admin-web/src/router/modules/admin.ts`

- [ ] **Step 1: 注册 `CurrentActivities` 路由和“目前活动”菜单**，不调整现有菜单层级。
- [ ] **Step 2: 实现普通管理模式**：卡片列表、创建/编辑草稿、发布、两个开关、归档、发送记录。
- [ ] **Step 3: 实现会话发送模式**：query 含 `conversation_id` 时显示客户上下文，只列可发送活动，单选、预览、确认后调用 `/send`。
- [ ] **Step 4: 发送成功展示“已进入发送队列”和聚合状态，提供返回原会话按钮。**
- [ ] **Step 5: 运行前端目标检查**

Run: `cd admin-web; pnpm ts:check`

Run: `cd admin-web; pnpm exec eslint src/api/admin/activities.ts src/views/current-activities/index.vue src/router/modules/admin.ts`

Expected: 退出码 0。

### Task 6：第一阶段验收

- [ ] 文本、图片、视频三条营销消息可按原顺序保存成草稿。
- [ ] “目前活动”页面可预览、发布、关闭和重新打开活动。
- [ ] 默认活动显示“长期有效”；设置结束时间后按中国时区展示。
- [ ] 未人工接管不显示右侧入口；接管后可跳转、选择、预览并入队。
- [ ] Eyun 测试号实际按顺序收到文本、图片、视频。
- [ ] 发送记录能从现有 outbound rows 聚合出 queued/sending/retrying/sent/partial，并展示最近一次 last_error。
- [ ] 关闭、草稿、归档和显式过期活动在前后端均不能发送。

Run: `cd wechat_rag_bot; pytest tests/test_admin_activities.py tests/test_message_risk_control.py tests/test_admin_conversations.py -q`

Run: `cd admin-web; pnpm ts:check`

Run: `git diff --check`

Expected: 测试 PASS，类型检查和 diff check 退出码 0。

第一阶段通过人工验收后再开始 AI 接入；不在同一批修改中同时上线。

## 六、第二阶段：最小 AI 接入

第二阶段只增加一个确定性活动节点，不把活动全文写入全局 prompt，不改知识库索引，也不让模型自由生成营销内容。

### AI 如何获得活动

```text
客户新消息
→ activity_service 查询 published + enabled + ai_enabled 活动
→ 按客户标签、销售阶段、消息关键词和已发送记录过滤
→ 取 updated_at 最新的一项
→ reply_workflow_graph 返回数据库中的原始活动消息
→ 现有 Eyun 队列发送
```

首版规则：

```json
{
  "customer_tags_any": ["新客", "兰花意向"],
  "sales_stages": ["considering", "ready_to_buy"],
  "message_keywords_any": ["活动", "优惠", "价格", "赠品"]
}
```

非空条件组之间为 AND，数组内部为 OR。规则为空时 AI 不发送。同一客户成功发送过同一活动后不再自动发送。

### 第二阶段文件范围

- Create: `wechat_rag_bot/app/services/activity_match_service.py`
- Create: `wechat_rag_bot/tests/test_activity_ai_delivery.py`
- Modify: `wechat_rag_bot/app/services/reply_workflow_graph.py`
- Modify: `wechat_rag_bot/app/schemas/reply.py`
- Modify: `wechat_rag_bot/app/services/message_risk_control_service.py`

明确不修改：意图分类、reply planner、RAG、话术匹配、prompt builder 和用户画像表。

### 第二阶段验收

- [ ] 新发布或关闭的活动在下一轮客户消息中立即生效，无需训练或重启。
- [ ] 只命中已发布、总开关和 AI 开关均打开的活动。
- [ ] AI 发送的是活动数据库原文，不改写价格、时间、文本或媒体。
- [ ] 同一客户不会收到同一活动两次。
- [ ] 无匹配活动时，原有 AI 回复链路结果不变。

## 七、明确排除

- 主动批量群发、客户名单导入、定时营销任务。
- 审批流、复杂活动版本、活动分类树、数据看板。
- 语音、文件、链接、小程序等新消息类型。
- 修改现有会话状态机或人工接管行为。
- 把活动写入 Qdrant、知识库或长期记忆。
- AI 自行编写或改写活动内容。

## 八、自检

- 需求覆盖：右键多选、活动包、独立“目前活动”页面、人工接管后右侧入口、选择发送、长期有效、开关、时间与发送记录均在第一阶段闭环内。
- 最小改动：新增两张表、一个后端模块、一个前端页面；复用现有会话、权限、队列、重试和限速。
- 风险隔离：第一阶段不碰 AI 主链路；第二阶段在人工闭环验收后单独接入并可独立回退。
- 数据一致：后台页面、人工发送和 AI 都读取同一 ActivityModel，不维护第二份活动数据。
